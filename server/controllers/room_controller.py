"""
Room presence, heartbeat, and leave lifecycle controller.
"""
import json
from config import DEFAULT_ROOM_ID, DEFAULT_ROOM_NAME
from models.state import state
from services.livekit_client import list_participants, remove_participant

def get_active_users(room_id: str) -> list[str]:
    live = list_participants(room_id)
    if live is not None:
        return live
    return state.get_fallback_active_users(room_id)

def handle_get_rooms(handler, query: dict):
    u_list = get_active_users(DEFAULT_ROOM_ID)
    handler.send_json(200, {
        "ok": True,
        "rooms": [{
            "id": DEFAULT_ROOM_ID,
            "name": DEFAULT_ROOM_NAME,
            "icon": "⚡",
            "is_protected": False,
            "is_temp": False,
            "count": len(u_list),
            "users": u_list,
        }],
    })

def handle_heartbeat_get(handler, query: dict):
    room_id = query.get('room', [''])[0]
    user = query.get('user', [''])[0].strip()
    is_leave = query.get('leave', [''])[0] == '1'
    if room_id and user:
        if is_leave:
            state.remove_user(room_id, user)
        else:
            state.touch_user(room_id, user)
    handler.send_json(200, {"ok": True})

def handle_heartbeat_post(handler, query: dict, body: bytes):
    handle_heartbeat_get(handler, query)

def handle_leave(handler, query: dict):
    user = query.get('user', [''])[0].strip()
    hwid = query.get('hwid', [''])[0].strip()
    clean_hw = "".join(c for c in hwid if c.isalnum() or c == '_')[:16]
    if clean_hw and user:
        remove_participant(DEFAULT_ROOM_ID, f"{user}_{clean_hw}")
    if user:
        state.remove_user(DEFAULT_ROOM_ID, user)
    handler.send_json(200, {"ok": True})
