#!/usr/bin/env python3
import time
from core.config import CONFIG

LOG_FILE = CONFIG["general"].get("log_file", "/tmp/nc-ui.log")
LOG_LEVEL = int(CONFIG["general"].get("log_level", 3))


def _write(level_num: int, tag: str, msg: str) -> None:
    if LOG_LEVEL < level_num:
        return
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{tag}] {msg}\n")
    except Exception:
        pass


def error(msg: str) -> None:
    _write(1, "ERROR", msg)


def warn(msg: str) -> None:
    _write(2, "WARN", msg)


def info(msg: str) -> None:
    _write(3, "INFO", msg)


def debug(msg: str) -> None:
    _write(6, "DEBUG", msg)
