"""
VOLT Web Application Entrypoint & Modular HTTP Server.
Orchestrates routing, controllers, services, and TCP lifecycle.
"""
import http.server
import socketserver
import json
import urllib.parse
from config import PORT
from router import Router
from controllers import (
    room_controller,
    token_controller,
    music_controller,
    static_controller,
)

# -------------------------------------------------------------
# Declarative Route Table
# -------------------------------------------------------------
router = Router()

# Rooms & Presence
router.get('/api/rooms')(room_controller.handle_get_rooms)
router.get('/api/heartbeat')(room_controller.handle_heartbeat_get)
router.post('/api/heartbeat')(room_controller.handle_heartbeat_post)
router.get('/api/leave')(room_controller.handle_leave)
router.get('/api/lobby_enter')(room_controller.handle_leave)

# Tokens & Updates
router.get('/api/token')(token_controller.handle_token)
router.get('/api/version')(token_controller.handle_version)

# Music Engine & Bot
router.get('/api/bot/status')(music_controller.handle_bot_status)
router.get('/api/music/state')(music_controller.handle_music_state)
router.post('/api/music/upload')(music_controller.handle_music_upload_post)
router.prefix_post_route('/api/music/')(music_controller.handle_music_action_post)

# Static Files & UI
router.get('/music')(static_controller.handle_music_deck)
router.get('/VOLT.exe')(static_controller.handle_volt_exe)
router.get('/volt.exe')(static_controller.handle_volt_exe)
router.get('/download')(static_controller.handle_volt_exe)
router.get('/anime.min.js')(static_controller.handle_anime_js)
router.get('/livekit-client.umd.min.js')(static_controller.handle_livekit_js)
router.set_default_get(static_controller.handle_index)


class VoltRequestHandler(http.server.BaseHTTPRequestHandler):
    """Clean HTTP request handler delegating dispatching to Router."""

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def send_raw(self, code: int, content_type: str, data: bytes, extra_headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_raw(code, 'application/json; charset=utf-8', data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        router.dispatch_get(self, parsed.path, query)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else b''
        router.dispatch_post(self, parsed.path, query, body)

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, format, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', PORT), VoltRequestHandler) as httpd:
        print(f"VOLT Observatory server listening on 0.0.0.0:{PORT}...")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down VOLT server...")
            httpd.shutdown()


if __name__ == '__main__':
    main()
