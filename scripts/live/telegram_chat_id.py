#!/usr/bin/env python3
"""Find your Telegram chat id for FloodGuard alerts.
1. In Telegram, talk to @BotFather, send /newbot, copy the token.
2. Open your new bot in Telegram and send it any message (for example "hi").
3. Run:  python scripts/live/telegram_chat_id.py <token>
It prints the chat id to paste into config/alerts.json."""
import json, sys
from urllib.request import urlopen

if len(sys.argv) < 2:
    sys.exit(__doc__)
j = json.loads(urlopen(f"https://api.telegram.org/bot{sys.argv[1]}/getUpdates", timeout=30).read().decode())
chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name") or u["message"]["chat"].get("title")
         for u in j.get("result", []) if "message" in u}
if not chats:
    print("No messages yet. Send any message to your bot in Telegram, then run this again.")
for cid, name in chats.items():
    print(f"chat id {cid}  ({name})")
