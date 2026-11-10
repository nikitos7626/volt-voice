"""
Static asset inspector, SHA-256 caching and initial HTML hash injection.
"""
import os
import hashlib
from config import STATIC_DIR

_FILE_META_CACHE = {}

def get_file_meta(filename: str) -> dict:
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

def inject_web_hash(html_bytes: bytes, web_hash: str) -> bytes:
    snippet = f'<script>window.__VOLT_INITIAL_WEB_HASH__="{web_hash}";</script></head>'.encode('utf-8')
    if b'</head>' in html_bytes:
        return html_bytes.replace(b'</head>', snippet, 1)
    return html_bytes
