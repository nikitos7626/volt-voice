import asyncio
import json
import time
import os
import random
import hmac
import hashlib
import base64
import re
import uuid
import subprocess
import html as html_lib
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from livekit import rtc
from aiohttp import web

API_KEY = os.environ.get("LIVEKIT_API_KEY", "volt_key")
API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "replace_with_a_strong_32_char_random_secret")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", LIVEKIT_URL)
BOT_PORT = int(os.environ.get("BOT_PORT", "7777"))
BOT_NAME = "[MUSIC] VOLT MUSIC"

RADIO_STATIONS = {
    "lofi": ("Lofi Girl // Chill Beats 24/7", "https://stream.zeno.fm/f3wvbbqmdg8uv"),
    "synthwave": ("Nightwave Plaza // Synthwave & Vaporwave", "https://radio.plaza.one/ogg"),
    "chill": ("SomaFM // Groove Salad Ambient Chill", "https://ice1.somafm.com/groovesalad-128-mp3"),
    "rock": ("Rock Antenne // Classic & Modern Rock", "https://stream.rockantenne.de/rockantenne/stream/mp3"),
    "metal": ("Heavy Metal Antenne // Pure Metal", "https://stream.rockantenne.de/heavy-metal/stream/mp3"),
    "ambient": ("SomaFM // Drone Zone Deep Ambient", "https://ice1.somafm.com/dronezone-128-mp3"),
    "jazz": ("SomaFM // Sonic Bliss Nu-Jazz & Beats", "https://ice1.somafm.com/sonicbliss-128-mp3")
}

EQ_PRESETS = {
    "studio": {"bass_db": 3.5, "treble_db": 2.0, "stereo_width": 1.12},
    "bass":   {"bass_db": 6.5, "treble_db": 2.0, "stereo_width": 1.15},
    "rock":   {"bass_db": 4.0, "treble_db": 3.0, "stereo_width": 1.18},
    "flat":   {"bass_db": 0.0, "treble_db": 0.0, "stereo_width": 1.00},
}

def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

def generate_bot_token(room_name="lounge"):
    header = {'alg': 'HS256', 'typ': 'JWT'}
    now = int(time.time())
    payload = {
        'name': BOT_NAME,
        'video': {
            'room': room_name,
            'roomJoin': True,
            'canPublish': True,
            'canPublishData': True,
            'canSubscribe': True
        },
        'iat': now,
        'nbf': now,
        'exp': now + 86400 * 30,
        'iss': API_KEY,
        'sub': BOT_NAME,
        'jti': f"volt_music_{now}"
    }
    h_b64 = b64url(json.dumps(header).encode())
    p_b64 = b64url(json.dumps(payload).encode())
    unsigned = f"{h_b64}.{p_b64}"
    sig = hmac.new(API_SECRET.encode('utf-8'), unsigned.encode('utf-8'), hashlib.sha256).digest()
    return f"{unsigned}.{b64url(sig)}"

class AudioEngine:
    def __init__(self, audio_source):
        self.audio_source = audio_source
        self.current_item = None
        self.current_stream_url = None
        self.play_started_at = 0.0
        self.seek_offset = 0.0
        self.queue = []
        self.eq_config = {
            "preset": "studio",
            "bass_db": 3.5,
            "treble_db": 2.0,
            "stereo_width": 1.12,
            "volume": 100
        }
        self.music_proc = None
        self.music_task = None
        self.is_playing = False
        self.is_speaking = False
        self.manual_stop = False
        self.loop_mode = "none" # "none" | "all" | "one"
        self.history = []
        self.on_track_finished = None

    def build_af_chain(self):
        cfg = self.eq_config
        vol = max(0.0, min(1.5, float(cfg.get("volume", 100)) / 100.0))
        preset = cfg.get("preset", "studio")
        bass = float(cfg.get("bass_db", 3.5))
        treble = float(cfg.get("treble_db", 2.0))
        width = float(cfg.get("stereo_width", 1.12))

        # Standard broadcast voice-matching target (-13.0 dB):
        # Commercial music peaks at 0 dBFS (-10 to -14 LUFS), while human headset mics in WebRTC
        # output speech around -26 to -30 dBFS. This attenuation ensures music at 100% volume
        # sits at a natural, balanced conversational level alongside human speech.
        VOICE_MATCH_ATTENUATION_DB = -13.0

        if preset == "flat" and abs(bass) < 0.05 and abs(treble) < 0.05 and abs(width - 1.0) < 0.02:
            base_vol = vol * (10 ** (VOICE_MATCH_ATTENUATION_DB / 20.0))
            return f"aresample=48000:resampler=soxr:precision=28,volume={base_vol:.3f}"

        headroom = (-2.5 if bass > 2.5 else -1.0) + VOICE_MATCH_ATTENUATION_DB
        vol_db_factor = vol * (10 ** (headroom / 20.0))
        punch_db = round(bass * 0.55, 2)
        return (
            f"aresample=48000:resampler=soxr:precision=28,"
            f"volume={vol_db_factor:.3f},"
            f"equalizer=f=55:t=q:w=1.0:g={bass:.1f},"
            f"equalizer=f=110:t=q:w=1.2:g={punch_db:.1f},"
            f"equalizer=f=320:t=q:w=1.4:g=-1.2,"
            f"equalizer=f=10500:t=q:w=0.9:g={treble:.1f},"
            f"stereotools=mlev=1.0:slev={width:.2f},"
            f"alimiter=limit=0.96:attack=5:release=50"
        )

    def get_elapsed(self):
        if not self.is_playing or not self.play_started_at:
            return 0
        return int(self.seek_offset + (time.time() - self.play_started_at))

    async def play_stream(self, item, stream_url, start_sec=0.0):
        await self.stop_music(clear_file=False)
        self.manual_stop = False
        self.current_item = item
        self.current_stream_url = stream_url
        self.seek_offset = start_sec
        self.play_started_at = time.time()
        self.is_playing = True

        if self.audio_source:
            try: self.audio_source.clear_queue()
            except: pass

        af_chain = self.build_af_chain()
        is_http = stream_url.startswith("http://") or stream_url.startswith("https://")

        cmd = ["ffmpeg"]
        if is_http:
            cmd += [
                "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "10",
            ]
        if start_sec > 1.0 and not item.get("is_radio"):
            cmd += ["-ss", f"{start_sec:.1f}"]
        cmd += [
            "-i", stream_url,
            "-af", af_chain,
            "-f", "s16le",
            "-ar", "48000",
            "-ac", "2",
            "-vn",
            "-loglevel", "error",
            "-"
        ]

        print(f"[MUSIC] Starting ffmpeg: {item.get('title')} (EQ={self.eq_config['preset']}, bass={self.eq_config['bass_db']}dB, ss={start_sec:.1f}s)")
        try:
            self.music_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            self.music_task = asyncio.create_task(self._pump_music())
        except Exception as e:
            print("[MUSIC ERR] Failed to spawn ffmpeg:", e)
            self.is_playing = False
            self.current_item = None

    async def _pump_music(self):
        consecutive_read_errors = 0
        natural_eof = False
        is_radio = bool(self.current_item and self.current_item.get("is_radio"))
        max_read_errors = 160 if is_radio else 25 # Radio gets 8s buffer tolerance, files get 1.25s
        next_tick = time.monotonic()

        try:
            while self.is_playing and self.music_proc and self.music_proc.stdout:
                try:
                    data = await self.music_proc.stdout.readexactly(3840)
                    consecutive_read_errors = 0
                except (asyncio.IncompleteReadError, Exception):
                    consecutive_read_errors += 1
                    if not self.is_playing:
                        break
                    if self.music_proc.returncode is not None or consecutive_read_errors > max_read_errors:
                        natural_eof = not is_radio
                        break
                    await asyncio.sleep(0.05)
                    continue

                if not self.is_speaking and self.audio_source:
                    frame = rtc.AudioFrame.create(48000, 2, 960)
                    frame.data.cast('B')[:] = data
                    await self.audio_source.capture_frame(frame)
                else:
                    await asyncio.sleep(0.02)

                # Realtime frame pacing (20ms per frame) to prevent burst-buffer WebRTC audio dropping
                next_tick += 0.020
                now = time.monotonic()
                delay = next_tick - now
                if delay > 0.002:
                    await asyncio.sleep(delay)
                elif delay < -0.100:
                    next_tick = now
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print("[MUSIC ERR] Pump loop error:", e)
        finally:
            print(f"[MUSIC] Pump ended (natural_eof={natural_eof}, manual_stop={self.manual_stop}, is_radio={is_radio}).")
            finished_item = self.current_item
            finished_url = self.current_stream_url
            self.is_playing = False
            self.current_item = None
            self.current_stream_url = None
            self.play_started_at = 0.0
            self.seek_offset = 0.0

            if self.audio_source:
                try: self.audio_source.clear_queue()
                except: pass

            if finished_item and finished_item.get("is_file") and finished_item.get("local_path"):
                if (natural_eof and self.loop_mode != 'one') or self.manual_stop:
                    try:
                        if os.path.exists(finished_item["local_path"]):
                            os.remove(finished_item["local_path"])
                    except Exception:
                        pass

            if natural_eof and not self.manual_stop and not is_radio and self.on_track_finished:
                asyncio.create_task(self.on_track_finished(finished_item, finished_url))

    async def stop_music(self, clear_file=True):
        self.manual_stop = True
        self.is_playing = False
        if self.music_task:
            self.music_task.cancel()
            try: await self.music_task
            except asyncio.CancelledError: pass
            self.music_task = None

        if self.music_proc:
            try: self.music_proc.kill()
            except: pass
            self.music_proc = None

        if self.audio_source:
            try: self.audio_source.clear_queue()
            except: pass

        if clear_file and self.current_item and self.current_item.get("is_file") and self.current_item.get("local_path"):
            try:
                if os.path.exists(self.current_item["local_path"]):
                    os.remove(self.current_item["local_path"])
            except Exception:
                pass

        self.current_item = None
        self.current_stream_url = None
        self.play_started_at = 0.0
        self.seek_offset = 0.0

class MusicController:
    def __init__(self):
        self.room = None
        self.audio_source = None
        self.audio_track = None
        self.audio_engine = AudioEngine(None)
        self.audio_engine.on_track_finished = self._on_track_finished
        self.current_room = None
        self.is_connected = False
        self.idle_leave_task = None
        self.lock = asyncio.Lock()

    def _cancel_idle_timer(self):
        if self.idle_leave_task:
            try:
                cur = asyncio.current_task()
            except RuntimeError:
                cur = None
            if self.idle_leave_task != cur:
                self.idle_leave_task.cancel()
            self.idle_leave_task = None

    def _start_idle_timer(self):
        self._cancel_idle_timer()
        print("[MUSIC] Все участники вышли — запускаю 5-минутный таймер автовыхода (300 сек).")
        async def _idle_wait():
            try:
                await asyncio.sleep(300)
                if self.room:
                    humans_now = [p for p in self.room.remote_participants.values() if not ('[BOT]' in (p.name or '') or '[MUSIC]' in (p.name or '') or p.identity.startswith('['))]
                    if len(humans_now) == 0:
                        print("[MUSIC] 5 минут без людей истекло — отключаюсь от канала.")
                        await self.dismiss()
            except asyncio.CancelledError:
                pass
        self.idle_leave_task = asyncio.create_task(_idle_wait())

    async def summon(self, room_name="lounge"):
        async with self.lock:
            if (
                self.is_connected
                and self.current_room == room_name
                and self.room is not None
                and hasattr(self.room, 'isconnected')
                and self.room.isconnected()
            ):
                return True
            await self._disconnect_internal(keep_queue=True)

            print(f"[MUSIC] Connecting to channel: {room_name}")
            self.current_room = room_name
            token = generate_bot_token(room_name)
            self.room = rtc.Room()

            self.audio_source = rtc.AudioSource(48000, 2)
            self.audio_track = rtc.LocalAudioTrack.create_audio_track("volt_music_hifi", self.audio_source)
            self.audio_engine.audio_source = self.audio_source

            self._setup_events(self.room)

            await self.room.connect(LIVEKIT_URL, token)
            publish_opts = rtc.TrackPublishOptions(
                source=rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO,
                dtx=False,
                red=False
            )
            publish_opts.audio_encoding.max_bitrate = 510000
            await self.room.local_participant.publish_track(self.audio_track, publish_opts)
            self.is_connected = True

            humans = [p for p in self.room.remote_participants.values() if not ('[BOT]' in (p.name or '') or '[MUSIC]' in (p.name or '') or p.identity.startswith('['))]
            if len(humans) > 0:
                self._cancel_idle_timer()
            else:
                self._start_idle_timer()
            return True

    async def dismiss(self):
        async with self.lock:
            await self._disconnect_internal(keep_queue=False)

    async def _disconnect_internal(self, keep_queue=False):
        self._cancel_idle_timer()
        await self.audio_engine.stop_music(clear_file=True)
        if not keep_queue:
            for q in self.audio_engine.queue:
                if q.get("is_file") and q.get("local_path"):
                    try: os.remove(q["local_path"])
                    except Exception: pass
            self.audio_engine.queue.clear()
        if self.room:
            try: await self.room.disconnect()
            except: pass
            self.room = None
        self.is_connected = False
        self.current_room = None
        self.audio_source = None
        self.audio_track = None
        self.audio_engine.audio_source = None

    def _setup_events(self, room):
        @room.on("disconnected")
        def on_room_disconnected(*args, **kwargs):
            print(f"[MUSIC] Room '{self.current_room}' disconnected from server.")
            self.is_connected = False
            self.room = None
            self.audio_source = None
            self.audio_track = None
            self.audio_engine.audio_source = None

        @room.on("participant_connected")
        def on_participant_connected(participant: rtc.Participant):
            name = participant.name or participant.identity
            if not ('[BOT]' in name or '[MUSIC]' in name or participant.identity.startswith('[')):
                self._cancel_idle_timer()

        @room.on("participant_disconnected")
        def on_participant_disconnected(participant: rtc.Participant):
            if not self.room: return
            async def _check_after_grace():
                await asyncio.sleep(4.0)
                if not self.room: return
                humans = [p for p in self.room.remote_participants.values() if not ('[BOT]' in (p.name or '') or '[MUSIC]' in (p.name or '') or p.identity.startswith('['))]
                if len(humans) == 0:
                    self._start_idle_timer()
                else:
                    self._cancel_idle_timer()
            asyncio.create_task(_check_after_grace())

        @room.on("data_received")
        def on_data_received(data: rtc.DataPacket):
            try:
                raw = data.data.decode('utf-8')
                parsed = json.loads(raw)
                if parsed.get('type') != 'chat': return
                sender = parsed.get('sender', '')
                if '[BOT]' in sender or '[MUSIC]' in sender: return
                text = (parsed.get('text') or '').strip()
                lower = text.lower()
                if lower.startswith('/play ') or lower.startswith('/radio ') or lower == '/stop' or lower == '/next':
                    asyncio.create_task(self.handle_chat_shortcut(text))
            except Exception:
                pass

    async def handle_chat_shortcut(self, text):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ['/stop']:
            await self.audio_engine.stop_music()
        elif cmd in ['/next']:
            await self.play_next_in_queue()
        elif cmd in ['/radio'] and arg.lower() in RADIO_STATIONS:
            t, u = RADIO_STATIONS[arg.lower()]
            await self.play_item({"id": uuid.uuid4().hex[:8], "title": t, "url": u, "is_radio": True, "duration": "LIVE"})
        elif cmd in ['/play'] and arg:
            item = await self.resolve_query_to_item(arg)
            if item:
                await self.play_item(item)

    async def _on_track_finished(self, finished_item=None, finished_url=None):
        if not finished_item:
            if self.audio_engine.queue:
                await self.play_next_in_queue()
            return

        # 1. Loop Single Track Mode
        if self.audio_engine.loop_mode == 'one':
            print(f"[LOOP] Repeating single track: {finished_item.get('title')}")
            await self.play_item(finished_item)
            return

        # 2. Collect to history for playlist loop
        self.audio_engine.history.append(finished_item)

        # 3. Next track in queue
        if self.audio_engine.queue:
            next_item = self.audio_engine.queue.pop(0)
            await self.play_item(next_item)
            return

        # 4. Loop All Playlist Mode
        if self.audio_engine.loop_mode == 'all':
            all_tracks = list(self.audio_engine.history)
            self.audio_engine.history.clear()
            if all_tracks:
                first = all_tracks.pop(0)
                self.audio_engine.queue = all_tracks
                print(f"[LOOP] Looping playlist from start ({len(all_tracks)+1} tracks): {first.get('title')}")
                await self.play_item(first)
                return

        # 5. Normal end
        await self.audio_engine.stop_music()

    async def play_next_in_queue(self):
        cur = self.audio_engine.current_item
        if cur and self.audio_engine.loop_mode == 'all':
            self.audio_engine.history.append(cur)

        if not self.audio_engine.queue:
            if self.audio_engine.loop_mode == 'all' and self.audio_engine.history:
                all_tracks = list(self.audio_engine.history)
                self.audio_engine.history.clear()
                first = all_tracks.pop(0)
                self.audio_engine.queue = all_tracks
                return await self.play_item(first)
            await self.audio_engine.stop_music()
            return False
        next_item = self.audio_engine.queue.pop(0)
        return await self.play_item(next_item)

    async def search_tracks(self, query):
        q = query.strip()
        if not q:
            return []

        if q.startswith("http://") or q.startswith("https://"):
            item = await self.resolve_query_to_item(q)
            return [item] if item else []

        loop = asyncio.get_running_loop()
        def run_search():
            results = []
            try:
                cmd = ["yt-dlp", "--flat-playlist", "-4", "--print", "%(title)s|||%(duration_string)s|||%(webpage_url)s|||%(uploader)s", f"ytsearch8:{q}"]
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
                                "is_file": False
                            })
                        if len(results) >= 6:
                            break
            except Exception as e:
                print("[SEARCH ERR ytsearch]:", e)

            if not results:
                try:
                    cmd = ["yt-dlp", "--flat-playlist", "-4", "--print", "%(title)s|||%(duration_string)s|||%(webpage_url)s|||%(uploader)s", f"scsearch5:{q}"]
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
                                "is_file": False
                            })
                except Exception as e:
                    print("[SEARCH ERR scsearch]:", e)
            return results

        return await loop.run_in_executor(None, run_search)

    async def resolve_query_to_item(self, q):
        q = q.strip()
        if not q:
            return None

        if q.lower() in RADIO_STATIONS:
            t, u = RADIO_STATIONS[q.lower()]
            return {"id": uuid.uuid4().hex[:8], "title": t, "url": u, "uploader": "Радио 24/7", "duration": "LIVE", "is_radio": True, "is_file": False}

        if q.startswith("http://") or q.startswith("https://"):
            lower_url = q.lower()
            loop = asyncio.get_running_loop()

            if any(dom in lower_url for dom in ["open.spotify.com", "spotify.link", "music.apple.com", "music.yandex.", "deezer.com"]):
                def fetch_meta():
                    try:
                        req = urllib.request.Request(q, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                        body = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", errors="ignore")
                        og_t = re.search(r'property=["\']og:title["\']\s+content=["\'](.*?)["\']', body)
                        og_d = re.search(r'property=["\']og:description["\']\s+content=["\'](.*?)["\']', body)
                        title_tag = re.search(r'<title>(.*?)</title>', body, re.DOTALL)
                        title_val = html_lib.unescape(og_t.group(1)).strip() if og_t else ""
                        desc_val = html_lib.unescape(og_d.group(1)).strip() if og_d else ""

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
                            if raw_t: return raw_t
                        if title_val: return title_val
                    except Exception as e:
                        print("[META RESOLVE ERR]:", e)
                    return None
                meta_q = await loop.run_in_executor(None, fetch_meta)
                if meta_q:
                    res = await self.search_tracks(meta_q)
                    if res: return res[0]
                return None

            def fetch_yt_info():
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
                            "is_file": False
                        }
                except Exception:
                    pass
                return {"id": uuid.uuid4().hex[:8], "title": "Аудиопоток по ссылке", "duration": "LIVE", "uploader": "Web Stream", "url": q, "is_radio": True, "is_file": False}

            return await loop.run_in_executor(None, fetch_yt_info)

        res = await self.search_tracks(q)
        return res[0] if res else None

    async def extract_playable_stream(self, item):
        if item.get("is_file"):
            return item.get("local_path")
        if item.get("is_radio"):
            return item.get("url")
        url = item.get("url")
        if not url:
            return None
        loop = asyncio.get_running_loop()
        def run_extract():
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

    async def play_item(self, item):
        if not item:
            return False
        if not self.is_connected:
            await self.summon(self.current_room or "lounge")
        stream_url = await self.extract_playable_stream(item)
        if not stream_url:
            return False
        await self.audio_engine.play_stream(item, stream_url)
        return True

    def get_state(self):
        is_really_conn = bool(self.is_connected and self.room and hasattr(self.room, 'isconnected') and self.room.isconnected())
        return {
            "ok": True,
            "connected": is_really_conn,
            "room": self.current_room if is_really_conn else None,
            "is_playing": self.audio_engine.is_playing,
            "current": self.audio_engine.current_item,
            "elapsed_sec": self.audio_engine.get_elapsed(),
            "loop_mode": self.audio_engine.loop_mode,
            "queue": self.audio_engine.queue,
            "eq": self.audio_engine.eq_config,
            "radios": [{"id": k, "title": v[0]} for k, v in RADIO_STATIONS.items()]
        }

ctrl = MusicController()
routes = web.RouteTableDef()

@routes.get('/status')
async def handle_status(request):
    return web.json_response({"connected": ctrl.is_connected, "room": ctrl.current_room})

@routes.get('/state')
async def handle_state(request):
    return web.json_response(ctrl.get_state())

@routes.post('/summon')
async def handle_summon(request):
    try: data = await request.json()
    except: data = {}
    room = data.get('room', 'lounge') or 'lounge'
    await ctrl.summon(room)
    return web.json_response(ctrl.get_state())

@routes.post('/dismiss')
async def handle_dismiss(request):
    await ctrl.dismiss()
    return web.json_response(ctrl.get_state())

@routes.post('/search')
async def handle_search(request):
    data = await request.json()
    q = data.get('query', '').strip()
    results = await ctrl.search_tracks(q)
    return web.json_response({"ok": True, "results": results})

@routes.post('/play')
async def handle_play(request):
    data = await request.json()
    room = data.get('room') or ctrl.current_room or 'lounge'
    is_live_in_room = bool(ctrl.is_connected and ctrl.room and hasattr(ctrl.room, 'isconnected') and ctrl.room.isconnected() and ctrl.current_room == room)
    if not is_live_in_room:
        await ctrl.summon(room)
    item = data.get('item')
    if not item and data.get('query'):
        item = await ctrl.resolve_query_to_item(data['query'])
    if not item:
        return web.json_response({"ok": False, "error": "Трек не найден"})
    if 'id' not in item:
        item['id'] = uuid.uuid4().hex[:8]
    ok = await ctrl.play_item(item)
    return web.json_response({"ok": ok, **ctrl.get_state()})

@routes.post('/queue_add')
async def handle_queue_add(request):
    data = await request.json()
    room = data.get('room') or ctrl.current_room or 'lounge'
    is_live_in_room = bool(ctrl.is_connected and ctrl.room and hasattr(ctrl.room, 'isconnected') and ctrl.room.isconnected() and ctrl.current_room == room)
    if not is_live_in_room:
        await ctrl.summon(room)
    item = data.get('item')
    if not item and data.get('query'):
        item = await ctrl.resolve_query_to_item(data['query'])
    if not item:
        return web.json_response({"ok": False, "error": "Трек не найден"})
    if 'id' not in item:
        item['id'] = uuid.uuid4().hex[:8]
    if not ctrl.audio_engine.is_playing:
        await ctrl.play_item(item)
    else:
        ctrl.audio_engine.queue.append(item)
    return web.json_response({"ok": True, **ctrl.get_state()})

@routes.post('/queue_remove')
async def handle_queue_remove(request):
    data = await request.json()
    tid = data.get('id')
    ctrl.audio_engine.queue = [q for q in ctrl.audio_engine.queue if q.get('id') != tid]
    return web.json_response({"ok": True, **ctrl.get_state()})

@routes.post('/queue_clear')
async def handle_queue_clear(request):
    ctrl.audio_engine.queue.clear()
    return web.json_response({"ok": True, **ctrl.get_state()})

@routes.post('/next')
async def handle_next(request):
    await ctrl.play_next_in_queue()
    return web.json_response({"ok": True, **ctrl.get_state()})

@routes.post('/loop')
async def handle_loop(request):
    try: data = await request.json()
    except: data = {}
    requested_mode = data.get('mode')
    valid = ['none', 'all', 'one']
    if requested_mode in valid:
        ctrl.audio_engine.loop_mode = requested_mode
    else:
        cur = ctrl.audio_engine.loop_mode
        idx = valid.index(cur) if cur in valid else 0
        ctrl.audio_engine.loop_mode = valid[(idx + 1) % len(valid)]
    print(f"[MUSIC] Loop mode switched to: {ctrl.audio_engine.loop_mode}")
    return web.json_response({"ok": True, **ctrl.get_state()})

@routes.post('/stop')
async def handle_stop(request):
    await ctrl.audio_engine.stop_music()
    return web.json_response({"ok": True, **ctrl.get_state()})

@routes.post('/eq')
async def handle_eq(request):
    data = await request.json()
    preset = data.get('preset')
    if preset and preset in EQ_PRESETS and 'bass_db' not in data:
        ctrl.audio_engine.eq_config['preset'] = preset
        ctrl.audio_engine.eq_config.update(EQ_PRESETS[preset])
    else:
        if preset: ctrl.audio_engine.eq_config['preset'] = preset
        for k in ('bass_db', 'treble_db', 'stereo_width', 'volume'):
            if k in data:
                ctrl.audio_engine.eq_config[k] = float(data[k])

    if ctrl.audio_engine.is_playing and ctrl.audio_engine.current_item and ctrl.audio_engine.current_stream_url:
        cur_item = ctrl.audio_engine.current_item
        cur_url = ctrl.audio_engine.current_stream_url
        elapsed = ctrl.audio_engine.get_elapsed()
        await ctrl.audio_engine.play_stream(cur_item, cur_url, start_sec=float(elapsed))

    return web.json_response({"ok": True, **ctrl.get_state()})

async def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting VOLT MUSIC Hi-Fi Audio Engine...")
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', BOT_PORT)
    await site.start()
    print(f"VOLT MUSIC Engine controller running on http://0.0.0.0:{BOT_PORT}...")
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
