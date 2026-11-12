# Setting up FloodGuard alerts

FloodGuard sends an alert by itself when the alert level rises (Normal → Watch → Warning → Danger),
when it falls again after two updates in a row ("All clear"), and when river readings are late for
more than 6 hours. Demo replays send alerts marked **[DEMO]**.

With no setup at all you already get:
* a pop-up on the computer running FloodGuard, and
* a pop-up and a sound in the dashboard (press **Show pop-ups in this browser** once, in the
  *Alerts and data* tab).

For alerts on a phone, copy `config/alerts.example.json` to **`config/alerts.json`** and fill in one or
more channels below. Set `"enabled": true` for each one you use, restart FloodGuard, then press
**Send a test alert** in the *Alerts and data* tab. Keep `alerts.json` private: it holds your keys.

## Telegram (free, recommended, about 2 minutes)

The easy way, from the dashboard (FloodGuard must be running):

1. In Telegram, open **@BotFather**, send `/newbot`, choose a name (for example "FloodGuard Mahanadi")
   and a username ending in `bot`. Copy the token it gives you (it looks like `123456789:AA...`).
2. In the dashboard, open **Alerts and data**, paste the token under *Get alerts on your phone* and
   press **Connect**.
3. Click the link to your bot that appears, press **Start** in Telegram, then press **Connect** again.
   A welcome message arrives on your phone. Anyone else who presses Start in the bot (team members,
   examiners, a Telegram group with the bot added) is added the next time you press the button.

FloodGuard saves the token and chat ids in `config/alerts.json` by itself. Press **Send a test alert**
to check.

By hand instead: run `python scripts/live/telegram_chat_id.py <token>` after pressing Start, and put
`"telegram": {"enabled": true, "send_demo_alerts": true, "bot_token": "<token>", "chat_ids": ["<chat id>"]}`
in `config/alerts.json`.

## E-mail (Gmail)

1. Turn on 2-step verification for the Google account, then create an **App password**
   (Google Account → Security → App passwords).
2. In `config/alerts.json`:
   ```json
   "email": {"enabled": true, "smtp_host": "smtp.gmail.com", "smtp_port": 587,
             "username": "you@gmail.com", "app_password": "<16-letter app password>", "to": ["you@gmail.com"]}
   ```

## SMS

SMS needs an account with an SMS provider, which usually costs money.

* **Fast2SMS** (India): create an account, copy the API key, then
  `"sms": {"enabled": true, "provider": "fast2sms", "api_key": "<key>", "numbers": ["9XXXXXXXXX"]}`.
* **Twilio:** `"provider": "twilio"`, plus `account_sid`, `auth_token`, `from_number`, and numbers
  with `+91`.

`send_demo_alerts` is `false` for SMS by default so a demo does not use paid messages; set it to `true`
if you want the examiners' phones to receive the demo SMS.

## Check

The *Alerts and data* tab shows which channels are on, every alert sent, and for each channel whether
it was delivered ("sent") or the error message. Everything is also logged in
`08_results/live/alerts_log.json`.
