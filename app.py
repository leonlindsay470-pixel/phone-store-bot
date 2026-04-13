import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "business_config.json"
DB_PATH = BASE_DIR / "chatbot.db"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "change-me")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v23.0")


# ---------- Config ----------
def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ---------- Database ----------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            sender TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            name TEXT,
            phone TEXT,
            intent TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


# ---------- Helpers ----------
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_session_id() -> str:
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())[:8]
    return session["session_id"]


def log_message(session_id: str, channel: str, sender: str, message: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (session_id, channel, sender, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, channel, sender, message, now_str()),
    )
    conn.commit()
    conn.close()


def save_lead(session_id: str, channel: str, intent: str = "", notes: str = "") -> None:
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM leads WHERE session_id = ? AND channel = ? ORDER BY id DESC LIMIT 1",
        (session_id, channel),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE leads SET intent = ?, notes = ? WHERE id = ?",
            (intent, notes, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO leads (session_id, channel, name, phone, intent, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, channel, None, None, intent, notes, now_str()),
        )
    conn.commit()
    conn.close()


PHONE_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{6,}\d)")


def maybe_capture_contact(session_id: str, channel: str, text: str) -> None:
    phone_match = PHONE_RE.search(text)
    name = None
    phone = phone_match.group(1) if phone_match else None

    lowered = text.lower()
    if "my name is" in lowered:
        after = lowered.split("my name is", 1)[1].strip()
        name = after.split("\n", 1)[0][:60].strip().title()
    elif "name:" in lowered:
        after = text.split(":", 1)[1].strip()
        name = after.split("\n", 1)[0][:60].strip().title()

    if not name and not phone:
        return

    conn = get_conn()
    existing = conn.execute(
        "SELECT id, name, phone, intent, notes FROM leads WHERE session_id = ? AND channel = ? ORDER BY id DESC LIMIT 1",
        (session_id, channel),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE leads SET name = COALESCE(?, name), phone = COALESCE(?, phone) WHERE id = ?",
            (name, phone, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO leads (session_id, channel, name, phone, intent, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, channel, name, phone, "contact", "captured from chat", now_str()),
        )
    conn.commit()
    conn.close()


def send_whatsapp_text(to_number: str, body: str) -> None:
    if not META_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        print("WhatsApp send skipped: missing META_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID")
        return

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": body},
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        print("WhatsApp send:", resp.status_code, resp.text)
    except Exception as exc:
        print("WhatsApp send failed:", exc)


# ---------- Reply Engine ----------
def build_reply(message: str, channel: str = "web") -> tuple[str, str]:
    config = load_config()
    presets = config.get("presets", {})
    inventory_keywords = config.get("inventory_keywords", {})

    text = message.strip()
    lowered = text.lower()
    intent = "general"

    for keyword, reply in inventory_keywords.items():
        if keyword.lower() in lowered:
            return reply, "inventory"

    if any(word in lowered for word in ["hello", "hi", "hey", "good morning", "good afternoon"]):
        return presets.get("greeting", "Hi! How can I help?"), "greeting"

    if any(word in lowered for word in ["hour", "open", "close", "opening time"]):
        return presets.get("hours", "We can share our opening hours."), "hours"

    if any(word in lowered for word in ["where", "location", "address", "located"]):
        return presets.get("location", "We can share our location."), "location"

    if any(word in lowered for word in ["deliver", "delivery", "pickup", "pick up"]):
        return presets.get("delivery", "We offer delivery and pickup."), "delivery"

    if any(word in lowered for word in ["price", "cost", "how much"]):
        return presets.get("prices", "Tell me the exact model and I'll guide you."), "prices"

    if any(word in lowered for word in ["trade", "trade-in", "trade in"]):
        return presets.get("tradein", "We accept trade-ins on selected devices."), "tradein"

    if any(word in lowered for word in ["accessor", "case", "charger", "screen protector", "airpods", "earbuds"]):
        return presets.get("accessories", "We carry accessories too."), "accessories"

    if any(word in lowered for word in ["human", "agent", "representative", "someone", "call me", "speak to someone"]):
        return presets.get("human", "Leave your name and number and a team member can follow up."), "human"

    if any(word in lowered for word in ["iphone", "samsung", "phone"]):
        return presets.get("prices", "Tell me the exact model, storage, and condition you want."), "product_inquiry"

    return presets.get("fallback", "I can help with stock, prices, delivery, trade-ins, accessories, store hours, or a human follow-up."), intent


# ---------- Routes ----------
@app.route("/")
def index() -> str:
    ensure_session_id()
    config = load_config()
    return render_template("index.html", config=config)


@app.route("/api/presets")
def api_presets():
    config = load_config()
    return jsonify({
        "quick_replies": config.get("quick_replies", []),
        "presets": config.get("presets", {}),
        "business": config.get("business", {}),
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"reply": "Please type a message first."}), 400

    session_id = ensure_session_id()
    log_message(session_id, "web", "user", message)
    maybe_capture_contact(session_id, "web", message)

    reply, intent = build_reply(message, "web")
    log_message(session_id, "web", "bot", reply)

    if intent in {"human", "prices", "inventory", "product_inquiry", "tradein", "delivery"}:
        save_lead(session_id, "web", intent=intent, notes=message[:500])

    return jsonify({"reply": reply, "intent": intent})


@app.route("/dashboard")
def dashboard() -> str:
    config = load_config()
    conn = get_conn()
    leads = conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 200").fetchall()
    messages = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 300").fetchall()
    conn.close()
    return render_template("dashboard.html", config=config, leads=leads, messages=messages)


@app.route("/settings", methods=["GET", "POST"])
def settings() -> str:
    config = load_config()

    if request.method == "POST":
        business = config["business"]
        for field in ["name", "tagline", "hours", "location", "phone", "email", "website", "about"]:
            business[field] = request.form.get(field, business.get(field, "")).strip()

        offers = [line.strip() for line in request.form.get("offers", "").splitlines() if line.strip()]
        quick_replies = [line.strip() for line in request.form.get("quick_replies", "").splitlines() if line.strip()]
        config["offers"] = offers
        config["quick_replies"] = quick_replies

        for key in list(config["presets"].keys()):
            config["presets"][key] = request.form.get(f"preset_{key}", config["presets"][key]).strip()

        inventory_entries = [
            line.strip()
            for line in request.form.get("inventory_keywords", "").splitlines()
            if line.strip() and "=" in line
        ]
        config["inventory_keywords"] = {
            line.split("=", 1)[0].strip().lower(): line.split("=", 1)[1].strip()
            for line in inventory_entries
        }

        save_config(config)
        return redirect(url_for("settings"))

    inventory_lines = "\n".join(
        f"{k} = {v}" for k, v in config.get("inventory_keywords", {}).items()
    )
    return render_template("settings.html", config=config, inventory_lines=inventory_lines)


@app.route("/webhooks/meta", methods=["GET"])
def verify_meta_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        return challenge or "", 200
    return "Verification failed", 403


@app.route("/webhooks/meta", methods=["POST"])
def handle_meta_webhook():
    payload = request.get_json(silent=True) or {}
    print("META WEBHOOK:", json.dumps(payload, indent=2))

    entries = payload.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})

            # WhatsApp messages
            for message in value.get("messages", []):
                from_number = message.get("from", "unknown")
                text = message.get("text", {}).get("body", "")
                if not text:
                    continue

                session_id = f"wa-{from_number}"
                log_message(session_id, "whatsapp", "user", text)
                maybe_capture_contact(session_id, "whatsapp", text)

                reply, intent = build_reply(text, "whatsapp")
                log_message(session_id, "whatsapp", "bot", reply)
                save_lead(session_id, "whatsapp", intent=intent, notes=text[:500])
                send_whatsapp_text(from_number, reply)

            # Instagram messages placeholder logging
            messaging = value.get("messaging", [])
            for event in messaging:
                sender_id = (event.get("sender") or {}).get("id", "unknown")
                message_obj = event.get("message") or {}
                text = message_obj.get("text", "")
                if not text:
                    continue

                session_id = f"ig-{sender_id}"
                log_message(session_id, "instagram", "user", text)
                maybe_capture_contact(session_id, "instagram", text)

                reply, intent = build_reply(text, "instagram")
                log_message(session_id, "instagram", "bot", reply)
                save_lead(session_id, "instagram", intent=intent, notes=text[:500])
                print("Instagram reply to send manually / wire via Graph API:", sender_id, reply)

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("REGISTERED ROUTES:")
    for rule in app.url_map.iter_rules():
        print(rule)
    import os

app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
