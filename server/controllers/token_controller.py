"""
Token minting and client/web version manifest controller.
"""
from config import DOMAIN, LIVEKIT_PUBLIC_WS, OWNER_HARDWARE_IDS, EXE_VERSION
from models.state import state
from services.token_service import generate_client_token
from services.static_service import get_file_meta

def handle_token(handler, query: dict):
    name = query.get('name', ['Guest'])[0]
    room = "lounge"
    cid = query.get('client_id', [''])[0].strip()
    hwid = query.get('hwid', [''])[0].strip() or cid
    device_name = query.get('device', [''])[0].strip()
    clean_hw = "".join(c for c in hwid if c.isalnum() or c == '_')[:16]

    if clean_hw:
        state.record_hardware_session(clean_hw, name, device_name or "PC", room)

    token = generate_client_token(name, room, client_id=cid, hwid=hwid, device_name=device_name)
    host = handler.headers.get('Host', DOMAIN)
    proto = handler.headers.get('X-Forwarded-Proto', 'https')
    ws_scheme = 'wss' if proto == 'https' else 'ws'
    ws_url = LIVEKIT_PUBLIC_WS if DOMAIN != "localhost" else f"{ws_scheme}://{host}"

    handler.send_json(200, {
        'token': token,
        'wsUrl': ws_url,
        'hwid': clean_hw,
        'isHardwareTrusted': bool(OWNER_HARDWARE_IDS and clean_hw in OWNER_HARDWARE_IDS),
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
