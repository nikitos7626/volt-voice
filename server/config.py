"""
Configuration settings for VOLT server.
Loads environment variables with safe defaults.
"""
import os

PORT = int(os.environ.get("VOLT_PORT", "3000"))
DOMAIN = os.environ.get("DOMAIN", "localhost")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "http://127.0.0.1:7880").rstrip("/")
LIVEKIT_PUBLIC_WS = os.environ.get("LIVEKIT_PUBLIC_WS", f"wss://{DOMAIN}")
API_KEY = os.environ.get("LIVEKIT_API_KEY", "volt_key")
API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "replace_with_a_strong_32_char_random_secret")
MUSIC_BOT_URL = os.environ.get("MUSIC_BOT_URL", "http://127.0.0.1:7777").rstrip("/")

OWNER_HARDWARE_IDS = {x.strip() for x in os.environ.get("OWNER_HARDWARE_IDS", "").split(",") if x.strip()}
OWNER_NAMES = {x.strip().lower() for x in os.environ.get("OWNER_NAMES", "").split(",") if x.strip()}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
EXE_VERSION = "1.3.0"
DEFAULT_ROOM_ID = "lounge"
DEFAULT_ROOM_NAME = "Основной канал"
