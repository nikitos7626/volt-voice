"""
Thread-safe in-memory state store for rooms, active users, and hardware sessions.
"""
import time
import threading

class StateStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._room_users = {}       # room_id -> {user_name: last_seen_timestamp}
        self._hardware_sessions = {} # clean_hwid -> session_dict

    def touch_user(self, room_id: str, user: str) -> None:
        if not room_id or not user:
            return
        now = time.time()
        with self._lock:
            self._room_users.setdefault(room_id, {})[user] = now

    def remove_user(self, room_id: str, user: str) -> None:
        with self._lock:
            if room_id and room_id in self._room_users:
                self._room_users[room_id].pop(user, None)
            else:
                for r_map in self._room_users.values():
                    r_map.pop(user, None)

    def get_fallback_active_users(self, room_id: str, max_age: float = 18.0) -> list[str]:
        now = time.time()
        with self._lock:
            user_map = self._room_users.get(room_id, {})
            return [u for u, ts in list(user_map.items()) if now - ts < max_age]

    def record_hardware_session(self, hwid: str, name: str, device: str, room: str) -> None:
        if not hwid:
            return
        with self._lock:
            self._hardware_sessions[hwid] = {
                "name": name,
                "device": device or "PC",
                "room": room,
                "last_seen": time.time(),
            }

    def get_hardware_session(self, hwid: str) -> dict | None:
        with self._lock:
            return self._hardware_sessions.get(hwid)

# Singleton state instance
state = StateStore()
