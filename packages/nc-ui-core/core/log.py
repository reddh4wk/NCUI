#!/usr/bin/env python3
import re
import sys
import threading
import time
import traceback
from core.config import CONFIG

LOG_FILE = CONFIG["general"].get("log_file", "/tmp/nc-ui.log")
LOG_LEVEL = int(CONFIG["general"].get("log_level", 3))

_TS_PREFIX_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
_WRITE_LOCK = threading.Lock()


def _write(level_num: int, tag: str, msg: str) -> None:
    if LOG_LEVEL < level_num:
        return
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        lines = str(msg or "").splitlines() or [""]
        block = "".join(
            f"{ln}\n" if _TS_PREFIX_RE.match(ln) else f"[{ts}] [{tag}] {ln}\n"
            for ln in lines
        )
        with _WRITE_LOCK:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(block)
    except Exception:
        pass


class _TimestampedLogStream:
    def __init__(self, tag: str = "STDERR"):
        self.tag = tag
        self._buf = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buf += str(data)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                _write(1, self.tag, line)
        return len(data)

    def flush(self) -> None:
        if self._buf:
            rem = self._buf.rstrip("\r\n")
            self._buf = ""
            if rem:
                _write(1, self.tag, rem)

    def isatty(self) -> bool:
        return False


def install_stderr_logger() -> None:
    """Redirect sys.stderr and uncaught exceptions into timestamped nc-ui.log lines."""
    if not isinstance(sys.stderr, _TimestampedLogStream):
        sys.stderr = _TimestampedLogStream("STDERR")

    def _excepthook(exc_type, exc_val, exc_tb):
        tb_txt = "".join(traceback.format_exception(exc_type, exc_val, exc_tb)).rstrip()
        error(f"Unhandled exception:\n{tb_txt}")

    def _thread_excepthook(args):
        tname = getattr(args.thread, "name", "Thread")
        tb_txt = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip()
        error(f"Unhandled thread exception ({tname}):\n{tb_txt}")

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def error(msg: str) -> None:
    _write(1, "ERROR", msg)


def warn(msg: str) -> None:
    _write(2, "WARN", msg)


def info(msg: str) -> None:
    _write(3, "INFO", msg)


def debug(msg: str) -> None:
    _write(6, "DEBUG", msg)
