"""
Token minting and client/web version manifest controller.
"""
from config import DOMAIN, LIVEKIT_PUBLIC_WS, OWNER_HARDWARE_IDS, EXE_VERSION
from models.state import state
from services.token_service import generate_client_token
from services.static_service import get_file_meta

RESTRICTED_HWIDS = {"web_17081bd", "web_572a09f"}
RESTRICTED_IP_PREFIXES = ("188.233.", "104.28.254.")

def is_client_restricted(handler, query=None):
    if query:
        hwid = query.get('hwid', [''])[0].strip() or query.get('client_id', [''])[0].strip()
        if hwid and any(h in hwid for h in RESTRICTED_HWIDS):
            return True
        user = query.get('name', [''])[0].strip() or query.get('user', [''])[0].strip()
        if any(bad in user.lower() for bad in ['alert', '<script', 'hello!']):
            return True

    cookie = handler.headers.get('Cookie', '')
    if any(h in cookie for h in RESTRICTED_HWIDS):
        return True

    xff = handler.headers.get('X-Forwarded-For', handler.headers.get('X-Real-IP', ''))
    ip = xff.split(',')[0].strip() if xff else ''
    if ip and any(ip.startswith(prefix) for prefix in RESTRICTED_IP_PREFIXES):
        return True
    return False

def handle_token(handler, query: dict):
    name = query.get('name', ['Guest'])[0]
    room = "lounge"
    cid = query.get('client_id', [''])[0].strip()
    hwid = query.get('hwid', [''])[0].strip() or cid
    device_name = query.get('device', [''])[0].strip()
    clean_hw = "".join(c for c in hwid if c.isalnum() or c == '_')[:16]

    restricted = is_client_restricted(handler, query)

    if clean_hw:
        state.record_hardware_session(clean_hw, name, device_name or "PC", room)

    token = generate_client_token(name, room, client_id=cid, hwid=hwid, device_name=device_name, is_restricted=restricted)
    host = handler.headers.get('Host', DOMAIN)
    proto = handler.headers.get('X-Forwarded-Proto', 'https')
    ws_scheme = 'wss' if proto == 'https' else 'ws'
    ws_url = LIVEKIT_PUBLIC_WS if DOMAIN != "localhost" else f"{ws_scheme}://{host}"

    handler.send_json(200, {
        'token': token,
        'wsUrl': ws_url,
        'hwid': clean_hw,
        'isHardwareTrusted': bool(OWNER_HARDWARE_IDS and clean_hw in OWNER_HARDWARE_IDS),
        'isRestricted': restricted,
        'canManageMusic': not restricted,
    })

def handle_version(handler, query: dict):
    idx_meta = get_file_meta('index.html')
    mus_meta = get_file_meta('music.html')
    exe_meta = get_file_meta('VOLT.exe')
    host = handler.headers.get('Host', DOMAIN)
    proto = handler.headers.get('X-Forwarded-Proto', 'https')
    base_url = f"{proto}://{host}"
    handler.send_json(200, {
        "ok": True,
        "web_hash": idx_meta["short"],
        "music_web_hash": mus_meta["short"],
        "exe_version": EXE_VERSION,
        "exe_sha256": exe_meta["sha256"],
        "exe_size": exe_meta["size"],
        "exe_url": f"{base_url}/VOLT.exe",
    })
