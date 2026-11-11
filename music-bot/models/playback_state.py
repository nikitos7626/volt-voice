"""
Domain model storing playback queue, history, loop state, and equalizer settings.
"""
from dataclasses import dataclass, field


@dataclass
class PlaybackState:
    current_item: dict | None = None
    current_stream_url: str | None = None
    play_started_at: float = 0.0
    seek_offset: float = 0.0
    is_playing: bool = False
    is_speaking: bool = False
    manual_stop: bool = False
    loop_mode: str = "none"  # "none" | "all" | "one"
    queue: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    eq_config: dict = field(default_factory=lambda: {
        "preset": "studio",
        "bass_db": 3.5,
        "treble_db": 2.0,
        "stereo_width": 1.12,
        "volume": 100,
    })

    def remove_from_queue(self, track_id: str) -> None:
        self.queue = [q for q in self.queue if q.get("id") != track_id]

    def cycle_loop_mode(self, requested: str | None = None) -> str:
        valid = ("none", "all", "one")
        if requested in valid:
            self.loop_mode = requested
        else:
            idx = valid.index(self.loop_mode) if self.loop_mode in valid else 0
            self.loop_mode = valid[(idx + 1) % len(valid)]
        return self.loop_mode
