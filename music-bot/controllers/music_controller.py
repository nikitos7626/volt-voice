"""
Music Controller: LiveKit room connection lifecycle, idle auto-leave, queue & loop orchestration.
"""
import asyncio
import json
import os
import uuid
from livekit import rtc
from config import EQ_PRESETS, LIVEKIT_URL, RADIO_STATIONS
from models.playback_state import PlaybackState
from services.audio_engine import AudioEngine
from services.token_service import generate_bot_token
from services.track_resolver import TrackResolver


class MusicController:
    def __init__(self):
        self.room = None
        self.audio_source = None
        self.audio_track = None
        self.state = PlaybackState()
        self.audio_engine = AudioEngine(self.state, None)
        self.audio_engine.on_track_finished = self._on_track_finished
        self.resolver = TrackResolver()
        self.current_room = None
        self.is_connected = False
        self.idle_leave_task = None
        self.lock = asyncio.Lock()

    def is_live_connected(self, room: str | None = None) -> bool:
        live = bool(
            self.is_connected
            and self.room is not None
            and hasattr(self.room, "isconnected")
            and self.room.isconnected()
        )
        if room is not None:
            return live and self.current_room == room
        return live

    def _cancel_idle_timer(self) -> None:
        if self.idle_leave_task:
            try:
                cur = asyncio.current_task()
            except RuntimeError:
                cur = None
            if self.idle_leave_task != cur:
                self.idle_leave_task.cancel()
            self.idle_leave_task = None

    def _start_idle_timer(self) -> None:
        self._cancel_idle_timer()
        print("[MUSIC] Все участники вышли — запускаю 5-минутный таймер автовыхода (300 сек).")

        async def _idle_wait():
            try:
                await asyncio.sleep(300)
                if self.room:
                    humans_now = [
                        p for p in self.room.remote_participants.values()
                        if not ('[BOT]' in (p.name or '') or '[MUSIC]' in (p.name or '') or p.identity.startswith('['))
                    ]
                    if len(humans_now) == 0:
                        print("[MUSIC] 5 минут без людей истекло — отключаюсь от канала.")
                        await self.dismiss()
            except asyncio.CancelledError:
                pass

        self.idle_leave_task = asyncio.create_task(_idle_wait())

    async def summon(self, room_name: str = "lounge") -> bool:
        async with self.lock:
            if self.is_live_connected(room_name):
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
                red=False,
            )
            publish_opts.audio_encoding.max_bitrate = 510000
            await self.room.local_participant.publish_track(self.audio_track, publish_opts)
            self.is_connected = True

            humans = [
                p for p in self.room.remote_participants.values()
                if not ('[BOT]' in (p.name or '') or '[MUSIC]' in (p.name or '') or p.identity.startswith('['))
            ]
            if len(humans) > 0:
                self._cancel_idle_timer()
            else:
                self._start_idle_timer()
            return True

    async def dismiss(self) -> None:
        async with self.lock:
            await self._disconnect_internal(keep_queue=False)

    async def _disconnect_internal(self, keep_queue: bool = False) -> None:
        self._cancel_idle_timer()
        await self.audio_engine.stop_music(clear_file=True)
        if not keep_queue:
            for q in self.state.queue:
                if q.get("is_file") and q.get("local_path"):
                    try:
                        os.remove(q["local_path"])
                    except Exception:
                        pass
            self.state.queue.clear()
        if self.room:
            try:
                await self.room.disconnect()
            except Exception:
                pass
            self.room = None
        self.is_connected = False
        self.current_room = None
        self.audio_source = None
        self.audio_track = None
        self.audio_engine.audio_source = None

    def _setup_events(self, room: rtc.Room) -> None:
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
            if not self.room:
                return

            async def _check_after_grace():
                await asyncio.sleep(4.0)
                if not self.room:
                    return
                humans = [
                    p for p in self.room.remote_participants.values()
                    if not ('[BOT]' in (p.name or '') or '[MUSIC]' in (p.name or '') or p.identity.startswith('['))
                ]
                if len(humans) == 0:
                    self._start_idle_timer()
                else:
                    self._cancel_idle_timer()

            asyncio.create_task(_check_after_grace())

        @room.on("data_received")
        def on_data_received(data: rtc.DataPacket):
            try:
                raw = data.data.decode("utf-8")
                parsed = json.loads(raw)
                if parsed.get("type") != "chat":
                    return
                sender = parsed.get("sender", "")
                if "[BOT]" in sender or "[MUSIC]" in sender:
                    return

                # Security: drop music shortcuts from restricted clients
                if data.participant:
                    part = data.participant
                    ident = getattr(part, 'identity', '') or ''
                    if any(bad in ident for bad in ('web_17081bd', 'web_572a09f', 'alert')):
                        print(f"[SECURITY] Dropped music command from restricted participant: {ident}")
                        return
                    try:
                        m = json.loads(getattr(part, 'metadata', '') or '{}')
                        if m.get('can_manage_music') is False or m.get('is_restricted'):
                            print(f"[SECURITY] Dropped music command from restricted participant: {ident}")
                            return
                    except Exception:
                        pass

                text = (parsed.get("text") or "").strip()
                lower = text.lower()
                if lower.startswith("/play ") or lower.startswith("/radio ") or lower in ("/stop", "/next"):
                    asyncio.create_task(self.handle_chat_shortcut(text))
            except Exception:
                pass

    async def handle_chat_shortcut(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "/stop":
            await self.audio_engine.stop_music()
        elif cmd == "/next":
            await self.play_next_in_queue()
        elif cmd == "/radio" and arg.lower() in RADIO_STATIONS:
            t, u = RADIO_STATIONS[arg.lower()]
            await self.play_item({
                "id": uuid.uuid4().hex[:8],
                "title": t,
                "url": u,
                "is_radio": True,
                "duration": "LIVE",
            })
        elif cmd == "/play" and arg:
            item = await self.resolver.resolve_query_to_item(arg)
            if item:
                await self.play_item(item)

    async def _on_track_finished(self, finished_item=None, finished_url=None) -> None:
        if not finished_item:
            if self.state.queue:
                await self.play_next_in_queue()
            return

        if self.state.loop_mode == "one":
            print(f"[LOOP] Repeating single track: {finished_item.get('title')}")
            await self.play_item(finished_item)
            return

        self.state.history.append(finished_item)

        if self.state.queue:
            next_item = self.state.queue.pop(0)
            await self.play_item(next_item)
            return

        if self.state.loop_mode == "all":
            all_tracks = list(self.state.history)
            self.state.history.clear()
            if all_tracks:
                first = all_tracks.pop(0)
                self.state.queue = all_tracks
                print(f"[LOOP] Looping playlist from start ({len(all_tracks) + 1} tracks): {first.get('title')}")
                await self.play_item(first)
                return

        await self.audio_engine.stop_music()

    async def play_next_in_queue(self) -> bool:
        cur = self.state.current_item
        if cur and self.state.loop_mode == "all":
            self.state.history.append(cur)

        if not self.state.queue:
            if self.state.loop_mode == "all" and self.state.history:
                all_tracks = list(self.state.history)
                self.state.history.clear()
                first = all_tracks.pop(0)
                self.state.queue = all_tracks
                return await self.play_item(first)
            await self.audio_engine.stop_music()
            return False

        next_item = self.state.queue.pop(0)
        return await self.play_item(next_item)

    async def play_item(self, item: dict) -> bool:
        if not item:
            return False
        if not self.is_connected:
            await self.summon(self.current_room or "lounge")
        stream_url = await self.resolver.extract_playable_stream(item)
        if not stream_url:
            return False
        await self.audio_engine.play_stream(item, stream_url)
        return True

    async def update_eq(self, data: dict) -> None:
        preset = data.get("preset")
        if preset and preset in EQ_PRESETS and "bass_db" not in data:
            self.state.eq_config["preset"] = preset
            self.state.eq_config.update(EQ_PRESETS[preset])
        else:
            if preset:
                self.state.eq_config["preset"] = preset
            for k in ("bass_db", "treble_db", "stereo_width", "volume"):
                if k in data:
                    self.state.eq_config[k] = float(data[k])

        if self.state.is_playing and self.state.current_item and self.state.current_stream_url:
            elapsed = self.audio_engine.get_elapsed()
            await self.audio_engine.play_stream(
                self.state.current_item,
                self.state.current_stream_url,
                start_sec=float(elapsed),
            )

    def get_state(self) -> dict:
        is_really_conn = self.is_live_connected()
        return {
            "ok": True,
            "connected": is_really_conn,
            "room": self.current_room if is_really_conn else None,
            "is_playing": self.state.is_playing,
            "current": self.state.current_item,
            "elapsed_sec": self.audio_engine.get_elapsed(),
            "loop_mode": self.state.loop_mode,
            "queue": self.state.queue,
            "eq": self.state.eq_config,
            "radios": [{"id": k, "title": v[0]} for k, v in RADIO_STATIONS.items()],
        }
