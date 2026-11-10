"""
Proxy service forwarding calls and local audio uploads to VOLT MUSIC engine.
"""
import os
import json
import uuid
import urllib.request
from config import MUSIC_BOT_URL

ALLOWED_MUSIC_ACTIONS = {
    'search', 'play', 'queue_add', 'queue_remove',
    'queue_clear', 'next', 'stop', 'eq', 'loop', 'summon', 'dismiss'
}

def query_bot_status() -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"{MUSIC_BOT_URL}/status", timeout=2) as b_resp:
            return 200, b_resp.read()
    except Exception:
        return 200, json.dumps({"connected": False, "room": None}).encode('utf-8')

def query_music_state() -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"{MUSIC_BOT_URL}/state", timeout=4) as b_resp:
            return 200, b_resp.read()
    except Exception as e:
        return 200, json.dumps({"ok": False, "connected": False, "error": str(e)}).encode('utf-8')

def forward_music_action(action: str, body_bytes: bytes) -> tuple[int, bytes]:
    if action not in ALLOWED_MUSIC_ACTIONS:
        return 404, json.dumps({"ok": False, "error": "Unknown action"}).encode('utf-8')
    try:
        b_req = urllib.request.Request(
            f"{MUSIC_BOT_URL}/{action}",
            data=body_bytes,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(b_req, timeout=25) as b_resp:
            return 200, b_resp.read()
    except Exception as e:
        return 200, json.dumps({"ok": False, "error": str(e)}).encode('utf-8')

def save_and_forward_upload(raw_bytes: bytes, orig_name: str, room: str, mode: str) -> tuple[int, bytes]:
    ext = os.path.splitext(orig_name)[1].lower()
    if ext not in ['.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.opus']:
        ext = '.mp3'
    os.makedirs('/tmp/volt_music_uploads', exist_ok=True)
    fid = uuid.uuid4().hex[:8]
    fpath = f"/tmp/volt_music_uploads/{fid}{ext}"
    with open(fpath, 'wb') as out_f:
        out_f.write(raw_bytes)

    endpoint = "queue_add" if mode == "queue" else "play"
    payload = json.dumps({
        "room": room or "lounge",
        "item": {
            "id": fid,
            "title": os.path.splitext(orig_name)[0][:60] or "Загруженный трек",
            "uploader": f"Локальный файл ({ext.upper().lstrip('.')})",
            "duration": "FILE",
            "local_path": fpath,
            "is_file": True,
            "is_radio": False,
        },
    }).encode('utf-8')
    try:
        b_req = urllib.request.Request(
            f"{MUSIC_BOT_URL}/{endpoint}",
            data=payload,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(b_req, timeout=10) as b_resp:
            return 200, b_resp.read()
    except Exception as e:
        return 200, json.dumps({"ok": False, "error": str(e)}).encode('utf-8')
