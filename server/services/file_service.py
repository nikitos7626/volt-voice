"""
File Storage Service: Streamed 64KB I/O, secure ID generation, MIME detection,
XSS isolation, and rolling 48h TTL / 5GB quota auto-cleanup.
"""
import os
import re
import time
import uuid
import mimetypes
import threading
from typing import BinaryIO
from config import (
    UPLOAD_DIR,
    MAX_FILE_SIZE_BYTES,
    FILE_TTL_SECONDS,
    MAX_STORAGE_CAP_BYTES,
)

_cleanup_lock = threading.Lock()

# Whitelist safe previewable MIME types for inline display
INLINE_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
INLINE_VIDEO_MIMES = {
    "video/mp4",
    "video/webm",
}
INLINE_AUDIO_MIMES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/wav",
    "audio/flac",
    "audio/aac",
    "audio/m4a",
}


def sanitize_filename(filename: str) -> str:
    """Strips path traversal, control chars, and limits length to 100 chars."""
    base = os.path.basename(filename.strip().replace("\\", "/"))
    # Remove control characters and quotes
    clean = re.sub(r'[\r\n\t"\';]', "_", base).strip(". ")
    if not clean:
        clean = "attachment.bin"
    return clean[:100]


def is_safe_preview_mime(mime: str) -> bool:
    """Determines whether a file can be served with inline disposition."""
    return (
        mime in INLINE_IMAGE_MIMES
        or mime in INLINE_VIDEO_MIMES
        or mime in INLINE_AUDIO_MIMES
    )


def save_uploaded_file(input_stream: BinaryIO, length: int, original_filename: str) -> dict:
    """
    Streams file bytes chunk-by-chunk (64 KB) directly to disk.
    Keeps memory footprint < 1 MB even for 25 MB files, protecting 2GB RAM VPS.
    """
    if length <= 0:
        raise ValueError("Файл пуст (0 байт)")
    if length > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"Размер файла превышает лимит {MAX_FILE_SIZE_BYTES // (1024 * 1024)} МБ")

    safe_name = sanitize_filename(original_filename)
    fid = f"f_{uuid.uuid4().hex[:12]}"
    folder = os.path.join(UPLOAD_DIR, fid)
    os.makedirs(folder, exist_ok=True)
    target_path = os.path.join(folder, safe_name)

    bytes_read = 0
    chunk_size = 65536
    with open(target_path, "wb") as out_f:
        while bytes_read < length:
            to_read = min(chunk_size, length - bytes_read)
            chunk = input_stream.read(to_read)
            if not chunk:
                break
            out_f.write(chunk)
            bytes_read += len(chunk)

    if bytes_read != length:
        # Partial / aborted upload -> remove broken file
        try:
            if os.path.exists(target_path):
                os.remove(target_path)
            if os.path.exists(folder):
                os.rmdir(folder)
        except Exception:
            pass
        raise IOError(f"Загрузка прервана (получено {bytes_read} из {length} байт)")

    # MIME type detection
    mime, _ = mimetypes.guess_type(safe_name)
    mime = mime or "application/octet-stream"

    return {
        "id": fid,
        "name": safe_name,
        "size": bytes_read,
        "mime": mime,
        "is_image": mime in INLINE_IMAGE_MIMES,
        "is_video": mime in INLINE_VIDEO_MIMES,
        "is_audio": mime in INLINE_AUDIO_MIMES,
        "url": f"/uploads/{fid}/{safe_name}",
        "uploaded_at": int(time.time()),
    }


def get_file_info(fid: str, filename: str) -> dict | None:
    """Validates path and returns file info for download/preview."""
    clean_fid = "".join(c for c in fid if c.isalnum() or c == "_")
    safe_name = sanitize_filename(filename)
    if not clean_fid or not safe_name:
        return None

    target_path = os.path.join(UPLOAD_DIR, clean_fid, safe_name)
    if not os.path.isfile(target_path):
        return None

    # Anti path-traversal check
    real_path = os.path.realpath(target_path)
    real_upload_dir = os.path.realpath(UPLOAD_DIR)
    if not real_path.startswith(real_upload_dir):
        return None

    size = os.path.getsize(target_path)
    mime, _ = mimetypes.guess_type(safe_name)
    mime = mime or "application/octet-stream"

    return {
        "path": target_path,
        "name": safe_name,
        "size": size,
        "mime": mime,
        "is_previewable": is_safe_preview_mime(mime),
    }


def cleanup_expired_files() -> int:
    """
    Sweeps uploads directory:
    1. Removes any folder older than FILE_TTL_SECONDS (48 hours).
    2. Enforces MAX_STORAGE_CAP_BYTES (5 GB) via LRU eviction.
    """
    if not os.path.isdir(UPLOAD_DIR):
        return 0

    with _cleanup_lock:
        now = time.time()
        purged = 0
        folders = []

        try:
            for entry in os.listdir(UPLOAD_DIR):
                fpath = os.path.join(UPLOAD_DIR, entry)
                if os.path.isdir(fpath) and entry.startswith("f_"):
                    try:
                        st = os.stat(fpath)
                        folders.append((fpath, st.st_mtime, st.st_size))
                        # 1. TTL 48h check
                        if now - st.st_mtime > FILE_TTL_SECONDS:
                            import shutil
                            shutil.rmtree(fpath, ignore_errors=True)
                            purged += 1
                    except Exception:
                        pass

            # 2. Hard storage quota check (5 GB)
            total_size = sum(sz for _, _, sz in folders)
            if total_size > MAX_STORAGE_CAP_BYTES:
                # Sort oldest first
                folders.sort(key=lambda x: x[1])
                for fpath, _, sz in folders:
                    if os.path.exists(fpath):
                        import shutil
                        shutil.rmtree(fpath, ignore_errors=True)
                        purged += 1
                        total_size -= sz
                        if total_size <= MAX_STORAGE_CAP_BYTES * 0.8:
                            break
        except Exception:
            pass

        return purged


def start_background_file_cleaner(interval_seconds: int = 1800):
    """Starts a low-priority background daemon sweeping expired files every 30 minutes."""
    def _worker():
        while True:
            time.sleep(interval_seconds)
            try:
                cleanup_expired_files()
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True, name="VoltFileCleaner")
    t.start()
    return t
