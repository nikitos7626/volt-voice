"""
Static file delivery controller (index, music deck, JS libraries, desktop binaries).
"""
import os
from config import STATIC_DIR
from services.static_service import get_file_meta, inject_web_hash

def serve_static(handler, filename: str, content_type: str, download_name: str | None = None, inject_hash: bool = False):
    filepath = os.path.join(STATIC_DIR, filename)
    if not os.path.exists(filepath):
        handler.send_error(404, "File Not Found")
        return
    with open(filepath, 'rb') as f:
        content = f.read()
    if inject_hash:
        h = get_file_meta(filename)["short"]
        content = inject_web_hash(content, h)
    extra_headers = []
    if download_name:
        extra_headers.append(('Content-Disposition', f'attachment; filename="{download_name}"'))
    handler.send_raw(200, content_type, content, extra_headers=extra_headers)

def handle_index(handler, path: str, query: dict):
    serve_static(handler, 'index.html', 'text/html; charset=utf-8', inject_hash=True)

def handle_music_deck(handler, query: dict):
    serve_static(handler, 'music.html', 'text/html; charset=utf-8', inject_hash=True)

def handle_volt_exe(handler, query: dict):
    serve_static(handler, 'VOLT.exe', 'application/octet-stream', download_name='VOLT.exe')

def handle_anime_js(handler, query: dict):
    serve_static(handler, 'anime.min.js', 'application/javascript')

def handle_livekit_js(handler, query: dict):
    serve_static(handler, 'livekit-client.umd.min.js', 'application/javascript')
