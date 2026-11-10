"""
Twirp RPC client interfacing with LiveKit Server.
"""
import json
import urllib.request
from config import LIVEKIT_URL
from services.token_service import generate_admin_token

def remove_participant(room_id: str, identity: str) -> bool:
    if not identity or not room_id:
        return False
    try:
        tok = generate_admin_token(room_id)
        req = urllib.request.Request(
            f"{LIVEKIT_URL}/twirp/livekit.RoomService/RemoveParticipant",
            data=json.dumps({"room": room_id, "identity": identity}).encode('utf-8'),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1.0):
            return True
    except Exception:
        return False

def list_participants(room_id: str) -> list[str] | None:
    try:
        tok = generate_admin_token(room_id)
        req = urllib.request.Request(
            f"{LIVEKIT_URL}/twirp/livekit.RoomService/ListParticipants",
            data=json.dumps({"room": room_id}).encode('utf-8'),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            res = json.loads(resp.read().decode('utf-8'))
            names = []
            for p in res.get("participants", []):
                n = (p.get("name") or p.get("identity") or "").replace("[MUSIC]", "").replace("[BOT]", "").strip()
                if n and n not in names:
                    names.append(n)
            return names
    except Exception:
        return None
