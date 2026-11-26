#!/usr/bin/env python3
import atexit
import json
import os
import tempfile
import threading

from core.config import get_cache_dir

STATE_DIR = get_cache_dir()
STATE_FILE = os.path.join(STATE_DIR, "state.json")


class StateManager:
    def __init__(self, path: str | None = None):
        self._fixed_path = path
        self._lock = threading.RLock()
        self._cache_data: dict | None = None
        self._cache_stat: tuple[int, int, int] | None = None
        self._dirty: bool = False
        self._flush_timer: threading.Timer | None = None
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        except OSError:
            pass
        atexit.register(self.flush)

    @property
    def path(self) -> str:
        return self._fixed_path or os.path.join(get_cache_dir(), "state.json")

    def _stat_sig(self) -> tuple[int, int, int] | None:
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            return None

    def _read_all(self) -> dict:
        with self._lock:
            if self._dirty and self._cache_data is not None:
                return self._cache_data
            sig = self._stat_sig()
            if sig is None:
                self._cache_data = {}
                self._cache_stat = None
                return self._cache_data
            if self._cache_data is not None and sig == self._cache_stat:
                return self._cache_data
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    parsed = data if isinstance(data, dict) else {}
            except Exception:
                parsed = {}
            self._cache_data = parsed
            self._cache_stat = sig
            return parsed

    def _write_all(self, data: dict) -> None:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, separators=(",", ":"))
                os.replace(tmp_path, self.path)
                self._cache_data = data
                self._cache_stat = self._stat_sig()
                self._dirty = False
            except Exception:
                pass

    def flush(self) -> None:
        with self._lock:
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            if self._dirty and self._cache_data is not None:
                self._write_all(self._cache_data)

    def get(self, namespace: str, key: str, default=None):
        with self._lock:
            data = self._read_all()
            return data.get(namespace, {}).get(key, default)

    def get_namespace(self, namespace: str) -> dict:
        with self._lock:
            data = self._read_all()
            ns = data.get(namespace, {})
            return dict(ns) if isinstance(ns, dict) else {}

    def set(self, namespace: str, key: str, value, immediate: bool = True) -> None:
        with self._lock:
            data = self._read_all()
            if namespace not in data or not isinstance(data[namespace], dict):
                data[namespace] = {}
            ns = data[namespace]
            if key in ns and type(ns[key]) is type(value) and ns[key] == value:
                return
            ns[key] = value
            self._cache_data = data
            if immediate:
                if self._flush_timer is not None:
                    self._flush_timer.cancel()
                    self._flush_timer = None
                self._write_all(data)
            else:
                self._dirty = True
                if self._flush_timer is not None:
                    self._flush_timer.cancel()
                t = threading.Timer(0.15, self.flush)
                t.daemon = True
                t.start()
                self._flush_timer = t

    def toggle(self, namespace: str, key: str, default: bool = False) -> bool:
        with self._lock:
            curr = bool(self.get(namespace, key, default))
            new_val = not curr
            self.set(namespace, key, new_val)
            return new_val


STATE = StateManager()
