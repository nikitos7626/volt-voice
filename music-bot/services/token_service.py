"""
LiveKit JWT token generator for the VOLT MUSIC worker participant.
"""
import time
import json
import hmac
import hashlib
import base64
from config import API_KEY, API_SECRET, BOT_NAME


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')


def generate_bot_token(room_name: str = "lounge") -> str:
    header = {'alg': 'HS256', 'typ': 'JWT'}
    now = int(time.time())
    payload = {
        'name': BOT_NAME,
        'video': {
            'room': room_name,
            'roomJoin': True,
            'canPublish': True,
            'canPublishData': True,
            'canSubscribe': True,
        },
        'iat': now,
        'nbf': now,
        'exp': now + 86400 * 30,
        'iss': API_KEY,
        'sub': BOT_NAME,
        'jti': f"volt_music_{now}",
    }
    h_b64 = b64url(json.dumps(header).encode())
    p_b64 = b64url(json.dumps(payload).encode())
    unsigned = f"{h_b64}.{p_b64}"
    sig = hmac.new(API_SECRET.encode('utf-8'), unsigned.encode('utf-8'), hashlib.sha256).digest()
    return f"{unsigned}.{b64url(sig)}"
