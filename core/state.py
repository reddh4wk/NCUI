#!/usr/bin/env python3
import json
import os
import tempfile

STATE_DIR = os.path.expanduser("~/.cache/nc-ui")
STATE_FILE = os.path.join(STATE_DIR, "state.json")


class StateManager:
    def __init__(self, path: str = STATE_FILE):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _read_all(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_all(self, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            pass

    def get(self, namespace: str, key: str, default=None):
        data = self._read_all()
        return data.get(namespace, {}).get(key, default)

    def get_namespace(self, namespace: str) -> dict:
        data = self._read_all()
        return data.get(namespace, {})

    def set(self, namespace: str, key: str, value) -> None:
        data = self._read_all()
        if namespace not in data or not isinstance(data[namespace], dict):
            data[namespace] = {}
        data[namespace][key] = value
        self._write_all(data)

    def toggle(self, namespace: str, key: str, default: bool = False) -> bool:
        curr = bool(self.get(namespace, key, default))
        new_val = not curr
        self.set(namespace, key, new_val)
        return new_val


STATE = StateManager()
