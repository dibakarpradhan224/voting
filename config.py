import os
from dotenv import load_dotenv

load_dotenv()

# ── Bot ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Your bot's @username WITHOUT the @ — used to build deep-join links
# e.g. https://t.me/<BOT_USERNAME>?start=<code>
BOT_USERNAME = os.getenv("BOT_USERNAME", "DEAD_X_VOTING_BOT")

# ── MongoDB ──────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "giveaway_bot")

# ── Owner (bot-level, sees /bot on/off, /stats, /broadcast) ─────────
# Numeric Telegram user id of @RoyalEren
OWNER_ID = int(os.getenv("OWNER_ID", "7987799736"))
OWNER_USERNAME = "Iyuxz"

# ── Misc ─────────────────────────────────────────────────────────────
LEADERBOARD_SIZE = 10
MAX_PARTICIPANTS_SHOWN_IN_MANAGE = 25  # for add/remove vote participant picker
BROADCAST_DELAY = 0.05  # seconds between broadcast messages, avoid flood limits