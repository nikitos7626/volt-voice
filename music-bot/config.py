"""
Configuration, radio stations directory, and studio EQ presets for VOLT MUSIC engine.
"""
import os

API_KEY = os.environ.get("LIVEKIT_API_KEY", "volt_key")
API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "replace_with_a_strong_32_char_random_secret")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
BOT_PORT = int(os.environ.get("BOT_PORT", "7777"))
BOT_NAME = "[MUSIC] VOLT MUSIC"

# Broadcast voice-matching attenuation: commercial music peaks at 0 dBFS (-10..-14 LUFS)
# while WebRTC headset speech sits around -26..-30 dBFS.
VOICE_MATCH_ATTENUATION_DB = -18.5

RADIO_STATIONS = {
    "lofi": ("Lofi Girl // Chill Beats 24/7", "https://stream.zeno.fm/f3wvbbqmdg8uv"),
    "synthwave": ("Nightwave Plaza // Synthwave & Vaporwave", "https://radio.plaza.one/ogg"),
    "chill": ("SomaFM // Groove Salad Ambient Chill", "https://ice1.somafm.com/groovesalad-128-mp3"),
    "rock": ("Rock Antenne // Classic & Modern Rock", "https://stream.rockantenne.de/rockantenne/stream/mp3"),
    "metal": ("Heavy Metal Antenne // Pure Metal", "https://stream.rockantenne.de/heavy-metal/stream/mp3"),
    "ambient": ("SomaFM // Drone Zone Deep Ambient", "https://ice1.somafm.com/dronezone-128-mp3"),
    "jazz": ("SomaFM // Sonic Bliss Nu-Jazz & Beats", "https://ice1.somafm.com/sonicbliss-128-mp3"),
}

EQ_PRESETS = {
    "studio": {"bass_db": 3.5, "treble_db": 2.0, "stereo_width": 1.12},
    "bass":   {"bass_db": 6.5, "treble_db": 2.0, "stereo_width": 1.15},
    "rock":   {"bass_db": 4.0, "treble_db": 3.0, "stereo_width": 1.18},
    "flat":   {"bass_db": 0.0, "treble_db": 0.0, "stereo_width": 1.00},
}
