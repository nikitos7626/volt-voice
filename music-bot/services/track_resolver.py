"""
Track search and multi-platform URL resolver (Spotify, Apple Music, Yandex, YouTube, SoundCloud).
"""
import asyncio
import html as html_lib
import re
import subprocess
import urllib.request
import uuid
from config import RADIO_STATIONS


def _parse_metadata_from_html(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        body = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", errors="ignore")
        og_t = re.search(r'property=["\']og:title["\']\s+content=["\'](.*?)["\']', body)
        og_d = re.search(r'property=["\']og:description["\']\s+content=["\'](.*?)["\']', body)
        title_tag = re.search(r'<title>(.*?)</title>', body, re.DOTALL)
        title_val = html_lib.unescape(og_t.group(1)).strip() if og_t else ""
        desc_val = html_lib.unescape(og_d.group(1)).strip() if og_d else ""
        lower_url = url.lower()

        if "spotify" in lower_url:
            if title_val and "·" in desc_val:
                artist = desc_val.split("·")[0].strip()
                if artist and artist.lower() not in ("listen on spotify", "spotify"):
                    return f"{artist} - {title_val}"
            if title_tag:
                raw_t = html_lib.unescape(title_tag.group(1))
                raw_t = re.sub(r'\s*\|\s*Spotify.*$', '', raw_t, flags=re.IGNORECASE)
                raw_t = re.sub(r'\s*-\s*song(?: and lyrics)? by\s+', ' - ', raw_t, flags=re.IGNORECASE)
                if raw_t.strip() and "spotify" not in raw_t.lower():
                    return raw_t.strip()
        if title_tag:
            raw_t = html_lib.unescape(title_tag.group(1)).strip()
            raw_t = re.sub(r'\s*[-—|]\s*(Apple Music|Яндекс Музыка|Yandex Music|Deezer).*$', '', raw_t, flags=re.IGNORECASE)
            raw_t = raw_t.replace('\u200e', '').strip()
            if raw_t:
                return raw_t
        if title_val:
            return title_val
    except Exception as e:
        print("[META RESOLVE ERR]:", e)
    return None


class TrackResolver:
    async def search_tracks(self, query: str) -> list[dict]:
        q = query.strip()
        if not q:
            return []

        if q.startswith("http://") or q.startswith("https://"):
            item = await self.resolve_query_to_item(q)
            return [item] if item else []

        loop = asyncio.get_running_loop()

        def run_search() -> list[dict]:
            results = []
            try:
                cmd = [
                    "yt-dlp", "--flat-playlist", "-4", "--print",
                    "%(title)s|||%(duration_string)s|||%(webpage_url)s|||%(uploader)s",
                    f"ytsearch8:{q}",
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=14)
                for line in res.stdout.strip().splitlines():
                    parts = line.split("|||")
                    if len(parts) >= 3:
                        t, dur, url = parts[0].strip(), parts[1].strip(), parts[2].strip()
                        uploader = parts[3].strip() if len(parts) > 3 else ""
                        if "/watch?v=" in url and dur and dur != "NA":
                            results.append({
                                "id": uuid.uuid4().hex[:8],
                                "title": t,
                                "duration": dur,
                                "url": url,
                                "uploader": uploader,
                                "is_radio": False,
                                "is_file": False,
                            })
                        if len(results) >= 6:
                            break
            except Exception as e:
                print("[SEARCH ERR ytsearch]:", e)

            if not results:
                try:
                    cmd = [
                        "yt-dlp", "--flat-playlist", "-4", "--print",
                        "%(title)s|||%(duration_string)s|||%(webpage_url)s|||%(uploader)s",
                        f"scsearch5:{q}",
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
                    for line in res.stdout.strip().splitlines():
                        parts = line.split("|||")
                        if len(parts) >= 3:
                            t, dur, url = parts[0].strip(), parts[1].strip(), parts[2].strip()
                            uploader = parts[3].strip() if len(parts) > 3 else ""
                            results.append({
                                "id": uuid.uuid4().hex[:8],
                                "title": t,
                                "duration": dur if dur != "NA" else "—",
                                "url": url,
                                "uploader": uploader,
                                "is_radio": False,
                                "is_file": False,
                            })
                except Exception as e:
                    print("[SEARCH ERR scsearch]:", e)
            return results

        return await loop.run_in_executor(None, run_search)

    async def resolve_query_to_item(self, query: str) -> dict | None:
        q = query.strip()
        if not q:
            return None

        if q.lower() in RADIO_STATIONS:
            t, u = RADIO_STATIONS[q.lower()]
            return {
                "id": uuid.uuid4().hex[:8],
                "title": t,
                "url": u,
                "uploader": "Радио 24/7",
                "duration": "LIVE",
                "is_radio": True,
                "is_file": False,
            }

        if q.startswith("http://") or q.startswith("https://"):
            lower_url = q.lower()
            loop = asyncio.get_running_loop()

            if any(dom in lower_url for dom in ["open.spotify.com", "spotify.link", "music.apple.com", "music.yandex.", "deezer.com"]):
                meta_q = await loop.run_in_executor(None, _parse_metadata_from_html, q)
                if meta_q:
                    res = await self.search_tracks(meta_q)
                    if res:
                        return res[0]
                return None

            def fetch_yt_info() -> dict:
                try:
                    cmd = ["yt-dlp", "--no-playlist", "-4", "--print", "%(title)s|||%(duration_string)s|||%(uploader)s", q]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
                    line = res.stdout.strip().splitlines()[0] if res.stdout.strip() else ""
                    if "|||" in line:
                        parts = line.split("|||")
                        return {
                            "id": uuid.uuid4().hex[:8],
                            "title": parts[0].strip(),
                            "duration": parts[1].strip() if len(parts) > 1 and parts[1].strip() != "NA" else "—",
                            "uploader": parts[2].strip() if len(parts) > 2 else "",
                            "url": q,
                            "is_radio": False,
                            "is_file": False,
                        }
                except Exception:
                    pass
                return {
                    "id": uuid.uuid4().hex[:8],
                    "title": "Аудиопоток по ссылке",
                    "duration": "LIVE",
                    "uploader": "Web Stream",
                    "url": q,
                    "is_radio": True,
                    "is_file": False,
                }

            return await loop.run_in_executor(None, fetch_yt_info)

        res = await self.search_tracks(q)
        return res[0] if res else None

    async def extract_playable_stream(self, item: dict) -> str | None:
        if item.get("is_file"):
            return item.get("local_path")
        if item.get("is_radio"):
            return item.get("url")
        url = item.get("url")
        if not url:
            return None

        loop = asyncio.get_running_loop()

        def run_extract() -> str | None:
            try:
                cmd = ["yt-dlp", "--no-playlist", "-4", "-g", "-f", "bestaudio", url]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                lines = res.stdout.strip().splitlines()
                if lines and lines[0].startswith("http"):
                    return lines[0]
            except Exception as e:
                print("[EXTRACT ERR]:", e)
            return None

        stream = await loop.run_in_executor(None, run_extract)
        if not stream:
            res = await self.search_tracks(item.get("title", ""))
            for r in res:
                if r.get("url") != url:
                    item["url"] = r["url"]
                    return await self.extract_playable_stream(item)
        return stream
