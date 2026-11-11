"""
Hi-Fi FFmpeg + libsoxr 28-bit PCM audio streaming engine with exact 20ms pacing.
"""
import asyncio
import os
import time
from livekit import rtc
from config import VOICE_MATCH_ATTENUATION_DB
from models.playback_state import PlaybackState


class AudioEngine:
    def __init__(self, state: PlaybackState, audio_source=None):
        self.state = state
        self.audio_source = audio_source
        self.music_proc = None
        self.music_task = None
        self.on_track_finished = None

    def build_af_chain(self) -> str:
        cfg = self.state.eq_config
        vol = max(0.0, min(1.5, float(cfg.get("volume", 100)) / 100.0))
        preset = cfg.get("preset", "studio")
        bass = float(cfg.get("bass_db", 3.5))
        treble = float(cfg.get("treble_db", 2.0))
        width = float(cfg.get("stereo_width", 1.12))

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

    def get_elapsed(self) -> int:
        if not self.state.is_playing or not self.state.play_started_at:
            return 0
        return int(self.state.seek_offset + (time.time() - self.state.play_started_at))

    async def play_stream(self, item: dict, stream_url: str, start_sec: float = 0.0) -> None:
        await self.stop_music(clear_file=False)
        self.state.manual_stop = False
        self.state.current_item = item
        self.state.current_stream_url = stream_url
        self.state.seek_offset = start_sec
        self.state.play_started_at = time.time()
        self.state.is_playing = True

        if self.audio_source:
            try:
                self.audio_source.clear_queue()
            except Exception:
                pass

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
            "-",
        ]

        print(f"[MUSIC] Starting ffmpeg: {item.get('title')} (EQ={self.state.eq_config['preset']}, bass={self.state.eq_config['bass_db']}dB, ss={start_sec:.1f}s)")
        try:
            self.music_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.music_task = asyncio.create_task(self._pump_music())
        except Exception as e:
            print("[MUSIC ERR] Failed to spawn ffmpeg:", e)
            self.state.is_playing = False
            self.state.current_item = None

    async def _pump_music(self) -> None:
        consecutive_read_errors = 0
        natural_eof = False
        is_radio = bool(self.state.current_item and self.state.current_item.get("is_radio"))
        max_read_errors = 160 if is_radio else 25
        next_tick = time.monotonic()

        try:
            while self.state.is_playing and self.music_proc and self.music_proc.stdout:
                try:
                    data = await self.music_proc.stdout.readexactly(3840)
                    consecutive_read_errors = 0
                except (asyncio.IncompleteReadError, Exception):
                    consecutive_read_errors += 1
                    if not self.state.is_playing:
                        break
                    if self.music_proc.returncode is not None or consecutive_read_errors > max_read_errors:
                        natural_eof = not is_radio
                        break
                    await asyncio.sleep(0.05)
                    continue

                if not self.state.is_speaking and self.audio_source:
                    frame = rtc.AudioFrame.create(48000, 2, 960)
                    frame.data.cast('B')[:] = data
                    await self.audio_source.capture_frame(frame)
                else:
                    await asyncio.sleep(0.02)

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
            print(f"[MUSIC] Pump ended (natural_eof={natural_eof}, manual_stop={self.state.manual_stop}, is_radio={is_radio}).")
            finished_item = self.state.current_item
            finished_url = self.state.current_stream_url
            self.state.is_playing = False
            self.state.current_item = None
            self.state.current_stream_url = None
            self.state.play_started_at = 0.0
            self.state.seek_offset = 0.0

            if self.audio_source:
                try:
                    self.audio_source.clear_queue()
                except Exception:
                    pass

            if finished_item and finished_item.get("is_file") and finished_item.get("local_path"):
                if (natural_eof and self.state.loop_mode != 'one') or self.state.manual_stop:
                    try:
                        if os.path.exists(finished_item["local_path"]):
                            os.remove(finished_item["local_path"])
                    except Exception:
                        pass

            if natural_eof and not self.state.manual_stop and not is_radio and self.on_track_finished:
                asyncio.create_task(self.on_track_finished(finished_item, finished_url))

    async def stop_music(self, clear_file: bool = True) -> None:
        self.state.manual_stop = True
        self.state.is_playing = False
        if self.music_task:
            self.music_task.cancel()
            try:
                await self.music_task
            except asyncio.CancelledError:
                pass
            self.music_task = None

        if self.music_proc:
            try:
                self.music_proc.kill()
            except Exception:
                pass
            self.music_proc = None

        if self.audio_source:
            try:
                self.audio_source.clear_queue()
            except Exception:
                pass

        if clear_file and self.state.current_item and self.state.current_item.get("is_file") and self.state.current_item.get("local_path"):
            try:
                if os.path.exists(self.state.current_item["local_path"]):
                    os.remove(self.state.current_item["local_path"])
            except Exception:
                pass

        self.state.current_item = None
        self.state.current_stream_url = None
        self.state.play_started_at = 0.0
        self.state.seek_offset = 0.0
