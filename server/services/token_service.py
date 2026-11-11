"""
LiveKit JWT token generator for clients and Twirp RPC administration.
"""
import time
import json
import hmac
import hashlib
import base64
import random
from config import API_KEY, API_SECRET, OWNER_HARDWARE_IDS, OWNER_NAMES

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

def generate_admin_token(room: str = "") -> str:
    header = {'alg': 'HS256', 'typ': 'JWT'}
    now = int(time.time())
    video_grant = {'roomList': True, 'roomAdmin': True}
    if room:
        video_grant['room'] = room
    payload = {
        'video': video_grant,
        'iat': now,
        'nbf': now,
        'exp': now + 120,
        'iss': API_KEY,
        'sub': 'volt_server_admin',
    }
    unsigned = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(payload).encode())}"
    sig = hmac.new(API_SECRET.encode('utf-8'), unsigned.encode('utf-8'), hashlib.sha256).digest()
    return f"{unsigned}.{b64url(sig)}"

def generate_client_token(name: str, room: str, client_id: str = "", hwid: str = "", device_name: str = "", is_restricted: bool = False) -> str:
    import re
    header = {'alg': 'HS256', 'typ': 'JWT'}
    now = int(time.time())
    clean_hw = "".join(c for c in (hwid or client_id or "") if c.isalnum() or c == '_')[:16]
    if not clean_hw:
        clean_hw = hex(random.randint(0x1000, 0xFFFF))[2:]

    # Clean any dangerous quotes, brackets, or injection characters
    clean_name = re.sub(r'[\r\n\t"\'<>{}\\\/`]', '', name).strip()[:24]
    if not clean_name:
        clean_name = "Участник"

    is_trusted_owner_hw = bool(OWNER_HARDWARE_IDS and clean_hw in OWNER_HARDWARE_IDS)
    is_protected_owner_name = bool(OWNER_NAMES and clean_name.lower() in OWNER_NAMES)

    metadata = {}
    if is_protected_owner_name:
        if is_trusted_owner_hw:
            metadata['is_admin'] = True
            metadata['role'] = 'owner'
            metadata['hardware_trusted'] = True
        else:
            clean_name = f"{clean_name} (Гость)"

    metadata['hwid'] = clean_hw
    metadata['device'] = device_name[:24] if device_name else ("Desktop_PC" if clean_hw.startswith("hw_") else "Web")

    if is_restricted:
        metadata['is_restricted'] = True
        metadata['can_manage_music'] = False

    safe_slug = re.sub(r'[^a-zA-Z0-9_\u0400-\u04FF]', '', clean_name)[:16] or "user"
    participant_id = f"{safe_slug}_{clean_hw}"
    payload = {
        'name': clean_name,
        'video': {
            'room': room,
            'roomJoin': True,
            'canPublish': True,
            'canPublishData': True,
            'canSubscribe': True,
        },
        'iat': now,
        'nbf': now,
        'exp': now + 86400,
        'iss': API_KEY,
        'sub': participant_id,
        'jti': f"{participant_id}_{now}",
        'metadata': json.dumps(metadata),
    }
    if is_restricted:
        payload['video']['canPublishSources'] = ["microphone"]
    unsigned = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(payload).encode())}"
    sig = hmac.new(API_SECRET.encode('utf-8'), unsigned.encode('utf-8'), hashlib.sha256).digest()
    return f"{unsigned}.{b64url(sig)}"
