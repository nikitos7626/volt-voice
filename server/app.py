import http.server
import socketserver
import json
import time
import hmac
import hashlib
import base64
import os
import uuid
import urllib.parse
import urllib.request

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
EXE_VERSION = "1.2.0"
_FILE_META_CACHE = {}

DEFAULT_ROOMS = [
    {"id": "lounge", "name": "Основной канал", "icon": "⚡", "is_protected": False, "is_temp": False},
]
ROOM_USERS = {}
HARDWARE_SESSIONS = {}


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')


def get_file_meta(filename):
    fpath = os.path.join(STATIC_DIR, filename)
    if not os.path.exists(fpath):
        return {"exists": False, "sha256": "", "short": "", "size": 0, "mtime": 0}
    try:
        st = os.stat(fpath)
        key = (st.st_mtime, st.st_size)
        cached = _FILE_META_CACHE.get(filename)
        if cached and cached.get("_key") == key:
            return cached
        h = hashlib.sha256()
        with open(fpath, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        digest = h.hexdigest()
        meta = {
            "_key": key,
            "exists": True,
            "sha256": digest,
            "short": digest[:12],
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        }
        _FILE_META_CACHE[filename] = meta
        return meta
    except Exception:
        return {"exists": False, "sha256": "", "short": "", "size": 0, "mtime": 0}


def inject_web_hash(html_bytes, web_hash):
    snippet = f'<script>window.__VOLT_INITIAL_WEB_HASH__="{web_hash}";</script></head>'.encode('utf-8')
    if b'</head>' in html_bytes:
        return html_bytes.replace(b'</head>', snippet, 1)
    return html_bytes


def generate_livekit_admin_token(room=""):
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


def livekit_remove_participant(room_id, identity):
    if not identity:
        return
    try:
        tok = generate_livekit_admin_token(room_id)
        req = urllib.request.Request(
            f"{LIVEKIT_URL}/twirp/livekit.RoomService/RemoveParticipant",
            data=json.dumps({"room": room_id, "identity": identity}).encode('utf-8'),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1.0):
            pass
    except Exception:
        pass


def livekit_list_participants(room_id):
    try:
        tok = generate_livekit_admin_token(room_id)
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


def get_room_active_users(room_id):
    live_users = livekit_list_participants(room_id)
    if live_users is not None:
        return live_users
    now = time.time()
    user_map = ROOM_USERS.get(room_id, {})
    return [u for u, ts in list(user_map.items()) if now - ts < 18]


def generate_livekit_token(name, room, client_id="", hwid="", device_name=""):
    import random
    header = {'alg': 'HS256', 'typ': 'JWT'}
    now = int(time.time())
    clean_hw = "".join(c for c in (hwid or client_id or "") if c.isalnum() or c == '_')[:16]
    if not clean_hw:
        clean_hw = hex(random.randint(0x1000, 0xFFFF))[2:]

    clean_name = (name or "Guest").strip()[:32]
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

    participant_id = f"{clean_name}_{clean_hw}"
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
    unsigned = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(payload).encode())}"
    sig = hmac.new(API_SECRET.encode('utf-8'), unsigned.encode('utf-8'), hashlib.sha256).digest()
    return f"{unsigned}.{b64url(sig)}"


def handle_heartbeat(query):
    room_id = query.get('room', [''])[0]
    user = query.get('user', [''])[0].strip()
    is_leave = query.get('leave', [''])[0] == '1'
    now = time.time()
    if room_id and user:
        if is_leave:
            ROOM_USERS.get(room_id, {}).pop(user, None)
        else:
            ROOM_USERS.setdefault(room_id, {})[user] = now


class VoltHandler(http.server.BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def send_json(self, code, payload):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_file(self, filename, content_type, download_name=None, inject_hash=False):
        filepath = os.path.join(STATIC_DIR, filename)
        if not os.path.exists(filepath):
            self.send_error(404)
            return
        with open(filepath, 'rb') as f:
            content = f.read()
        if inject_hash:
            h = get_file_meta(filename)["short"]
            content = inject_web_hash(content, h)
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        if download_name:
            self.send_header('Content-Disposition', f'attachment; filename="{download_name}"')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        if path == '/api/bot/status':
            try:
                with urllib.request.urlopen(f"{MUSIC_BOT_URL}/status", timeout=2) as b_resp:
                    raw = b_resp.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
            except Exception:
                self.send_json(200, {"connected": False, "room": None})
            return

        if path in ('/api/lobby_enter', '/api/leave'):
            user = query.get('user', [''])[0].strip()
            hwid = query.get('hwid', [''])[0].strip()
            clean_hw = "".join(c for c in hwid if c.isalnum() or c == '_')[:16]
            if path == '/api/leave' and clean_hw and user:
                livekit_remove_participant("lounge", f"{user}_{clean_hw}")
            if user:
                for r_map in list(ROOM_USERS.values()):
                    r_map.pop(user, None)
            self.send_json(200, {"ok": True})
            return

        if path == '/api/rooms':
            u_list = get_room_active_users("lounge")
            self.send_json(200, {
                "ok": True,
                "rooms": [{
                    "id": "lounge",
                    "name": "Основной канал",
                    "icon": "⚡",
                    "is_protected": False,
                    "is_temp": False,
                    "count": len(u_list),
                    "users": u_list,
                }],
            })
            return

        if path == '/api/heartbeat':
            handle_heartbeat(query)
            self.send_json(200, {"ok": True})
            return

        if path == '/api/token':
            name = query.get('name', ['Guest'])[0]
            room = "lounge"
            cid = query.get('client_id', [''])[0].strip()
            hwid = query.get('hwid', [''])[0].strip() or cid
            device_name = query.get('device', [''])[0].strip()
            clean_hw = "".join(c for c in hwid if c.isalnum() or c == '_')[:16]

            if clean_hw:
                HARDWARE_SESSIONS[clean_hw] = {
                    "name": name,
                    "device": device_name or "PC",
                    "room": room,
                    "last_seen": time.time(),
                }

            token = generate_livekit_token(name, room, client_id=cid, hwid=hwid, device_name=device_name)
            host = self.headers.get('Host', DOMAIN)
            proto = self.headers.get('X-Forwarded-Proto', 'https')
            ws_scheme = 'wss' if proto == 'https' else 'ws'
            ws_url = LIVEKIT_PUBLIC_WS if DOMAIN != "localhost" else f"{ws_scheme}://{host}"

            self.send_json(200, {
                'token': token,
                'wsUrl': ws_url,
                'hwid': clean_hw,
                'isHardwareTrusted': bool(OWNER_HARDWARE_IDS and clean_hw in OWNER_HARDWARE_IDS),
            })
            return

        if path == '/api/version':
            idx_meta = get_file_meta('index.html')
            mus_meta = get_file_meta('music.html')
            exe_meta = get_file_meta('VOLT.exe')
            host = self.headers.get('Host', DOMAIN)
            proto = self.headers.get('X-Forwarded-Proto', 'https')
            base_url = f"{proto}://{host}"
            self.send_json(200, {
                "ok": True,
                "web_hash": idx_meta["short"],
                "music_web_hash": mus_meta["short"],
                "exe_version": EXE_VERSION,
                "exe_sha256": exe_meta["sha256"],
                "exe_size": exe_meta["size"],
                "exe_url": f"{base_url}/VOLT.exe",
            })
            return

        if path == '/api/music/state':
            try:
                with urllib.request.urlopen(f"{MUSIC_BOT_URL}/state", timeout=4) as b_resp:
                    raw = b_resp.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
            except Exception as e:
                self.send_json(200, {"ok": False, "connected": False, "error": str(e)})
            return

        if path == '/music':
            self.serve_file('music.html', 'text/html; charset=utf-8', inject_hash=True)
            return

        if path in ('/VOLT.exe', '/download', '/volt.exe'):
            self.serve_file('VOLT.exe', 'application/octet-stream', download_name='VOLT.exe')
            return

        if path == '/anime.min.js':
            self.serve_file('anime.min.js', 'application/javascript')
            return

        if path == '/livekit-client.umd.min.js':
            self.serve_file('livekit-client.umd.min.js', 'application/javascript')
            return

        self.serve_file('index.html', 'text/html; charset=utf-8', inject_hash=True)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/heartbeat':
            handle_heartbeat(urllib.parse.parse_qs(parsed.query))
            self.send_json(200, {"ok": True})
            return

        if parsed.path == '/api/music/upload':
            try:
                length = int(self.headers.get('Content-Length', 0))
                if length <= 0 or length > 60 * 1024 * 1024:
                    raise ValueError("Размер файла должен быть от 1 байта до 60 МБ")
                raw_bytes = self.rfile.read(length)
                orig_name = urllib.parse.unquote(self.headers.get('X-Filename', 'track.mp3'))
                room = urllib.parse.unquote(self.headers.get('X-Room', 'lounge')) or 'lounge'
                mode = self.headers.get('X-Mode', 'play')
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
                    "room": room,
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
                b_req = urllib.request.Request(f"{MUSIC_BOT_URL}/{endpoint}", data=payload, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(b_req, timeout=10) as b_resp:
                    raw = b_resp.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception as e:
                self.send_json(200, {"ok": False, "error": str(e)})
            return

        if parsed.path.startswith('/api/music/'):
            action = parsed.path[len('/api/music/'):].strip('/')
            allowed = {'search', 'play', 'queue_add', 'queue_remove', 'queue_clear', 'next', 'stop', 'eq', 'loop', 'summon', 'dismiss'}
            if action in allowed:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length) if length > 0 else b'{}'
                try:
                    b_req = urllib.request.Request(f"{MUSIC_BOT_URL}/{action}", data=body, headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(b_req, timeout=25) as b_resp:
                        raw = b_resp.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except Exception as e:
                    self.send_json(200, {"ok": False, "error": str(e)})
                return

        self.send_error(404)

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, format, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == '__main__':
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', PORT), VoltHandler) as httpd:
        print(f"VOLT server listening on 0.0.0.0:{PORT}...")
        httpd.serve_forever()
