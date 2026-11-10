"""
Music bot status, state, proxying, and file upload controller.
"""
import json
import urllib.parse
from services.music_proxy import (
    query_bot_status,
    query_music_state,
    forward_music_action,
    save_and_forward_upload,
)

def handle_bot_status(handler, query: dict):
    code, raw = query_bot_status()
    handler.send_raw(code, 'application/json', raw)

def handle_music_state(handler, query: dict):
    code, raw = query_music_state()
    handler.send_raw(code, 'application/json', raw)

def handle_music_action_post(handler, path: str, query: dict, body: bytes):
    action = path[len('/api/music/'):].strip('/')
    code, raw = forward_music_action(action, body or b'{}')
    handler.send_raw(code, 'application/json', raw)

def handle_music_upload_post(handler, query: dict, body: bytes):
    try:
        length = len(body)
        if length <= 0 or length > 60 * 1024 * 1024:
            raise ValueError("Размер файла должен быть от 1 байта до 60 МБ")
        orig_name = urllib.parse.unquote(handler.headers.get('X-Filename', 'track.mp3'))
        room = urllib.parse.unquote(handler.headers.get('X-Room', 'lounge')) or 'lounge'
        mode = handler.headers.get('X-Mode', 'play')
        code, raw = save_and_forward_upload(body, orig_name, room, mode)
    except Exception as e:
        code, raw = 200, json.dumps({"ok": False, "error": str(e)}).encode('utf-8')
    handler.send_raw(code, 'application/json', raw)
