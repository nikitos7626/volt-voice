"""
Chat File Upload & Secure Download Controller.
Streams 64KB chunks directly to/from disk with strict XSS & size isolation.
"""
import io
import urllib.parse
from config import MAX_FILE_SIZE_BYTES
from services.file_service import save_uploaded_file, get_file_info
from controllers.token_controller import is_client_restricted


def handle_chat_upload_post(handler, query: dict, body: bytes):
    if is_client_restricted(handler, query):
        handler.send_json(403, {"ok": False, "error": "Загрузка файлов ограничена администратором"})
        return
    try:
        length = int(handler.headers.get('Content-Length', len(body) if body else 0))
        if length <= 0:
            handler.send_json(400, {"ok": False, "error": "Файл не выбран или пустой (0 байт)"})
            return
        if length > MAX_FILE_SIZE_BYTES:
            max_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
            handler.send_json(413, {"ok": False, "error": f"Файл слишком большой. Максимум {max_mb} МБ"})
            return

        orig_name = urllib.parse.unquote(handler.headers.get('X-Filename', 'file.bin'))
        stream = io.BytesIO(body) if (body and len(body) == length) else handler.rfile
        meta = save_uploaded_file(stream, length, orig_name)
        handler.send_json(200, {"ok": True, "file": meta})
    except ValueError as ve:
        handler.send_json(400, {"ok": False, "error": str(ve)})
    except Exception as e:
        handler.send_json(500, {"ok": False, "error": str(e)})


def handle_serve_upload_get(handler, path: str, query: dict):
    # Expected path: /uploads/<fid>/<filename>
    rel = path[len('/uploads/'):].strip('/')
    parts = rel.split('/', 1)
    if len(parts) != 2:
        handler.send_error(404, "Not Found")
        return

    fid = urllib.parse.unquote(parts[0])
    filename = urllib.parse.unquote(parts[1])
    info = get_file_info(fid, filename)
    if not info:
        handler.send_error(404, "File Not Found or Expired")
        return

    force_download = query.get('download', ['0'])[0] == '1'
    disp_type = "inline" if (info["is_previewable"] and not force_download) else "attachment"
    encoded_name = urllib.parse.quote(info["name"])

    handler.send_response(200)
    handler.send_header('Content-Type', info["mime"])
    handler.send_header('Content-Length', str(info["size"]))
    handler.send_header('Content-Disposition', f"{disp_type}; filename*=UTF-8''{encoded_name}")
    # Strict security isolation against XSS / MIME sniffing from uploaded HTML/SVG/etc.
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Security-Policy', "default-src 'none'; media-src 'self'; img-src 'self'; sandbox")
    handler.end_headers()

    # Stream file in 64 KB chunks
    with open(info["path"], 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            handler.wfile.write(chunk)
