# Phone Store Omnichannel Chatbot

This version is customized for a phone store and is structured so the same reply engine can power:
- website chat
- WhatsApp Cloud API
- Instagram DM automation

It also includes a settings page where you can customize the preset texts and quick replies without editing Python.

## What changed
- niche-specific flow for phone stores
- editable presets in `business_config.json` and `/settings`
- one dashboard for web, WhatsApp, and Instagram messages
- webhook endpoint for Meta at `/webhooks/meta`
- helper functions for WhatsApp and Instagram outbound replies

## Local run

### Windows PowerShell
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open:
- `http://127.0.0.1:5000/`
- `http://127.0.0.1:5000/dashboard`
- `http://127.0.0.1:5000/settings`

## Meta setup you still need
This project gives you the app-side code, but you still need your own Meta developer setup and credentials.

Add these to a `.env` file or your hosting environment:
```env
FLASK_SECRET_KEY=change-me
META_VERIFY_TOKEN=change-me
META_ACCESS_TOKEN=your-meta-access-token
WHATSAPP_PHONE_NUMBER_ID=your-whatsapp-phone-number-id
META_PAGE_ID=your-facebook-page-id-for-instagram-messaging
```

## Webhook verification
Meta will call:
- `GET /webhooks/meta` for verification
- `POST /webhooks/meta` for incoming WhatsApp or Instagram messages

The code expects:
- WhatsApp Cloud API style `messages` changes for WhatsApp
- Messenger-platform style `messaging` events for Instagram DM

## Customizable preset texts
Go to `/settings` and change:
- greeting
- hours
- location
- delivery
- prices
- trade-in
- accessories
- human handoff
- fallback
- quick replies
- keyword based stock replies

## How to sell it
Sell the outcome, not the code:
- instant replies on WhatsApp and Instagram
- fewer missed DMs
- faster price checks
- cleaner lead capture for phone stores

## Notes
- Instagram and WhatsApp both require business-side Meta setup before this can work live.
- You should host this on Render, Railway, or another public server before connecting real webhooks.
- Test with your own Meta business assets first.
