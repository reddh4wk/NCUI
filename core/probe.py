#!/usr/bin/env python3
import os
import shutil
import subprocess
import threading
from gi.repository import GLib
from core.config import CONFIG
from core import log


def has_bin(name: str) -> bool:
    return shutil.which(name) is not None


def run_cmd(cmd: list, timeout: float = 3.0, input_data: bytes = None) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            cmd,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        out = res.stdout.decode("utf-8", errors="replace").strip() if res.stdout else ""
        return (res.returncode == 0), out
    except Exception as e:
        log.debug(f"run_cmd failed ({cmd}): {e}")
        return False, ""


def run_cmd_full(cmd: list, timeout: float = 4.0, input_data: bytes = None) -> tuple[bool, str, str]:
    """
    Esegue un comando restituendo (ok, stdout, stderr) per verificare l'esito reale
    delle operazioni di sistema (es. nmcli, ip, bluetoothctl).
    """
    try:
        res = subprocess.run(
            cmd,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        out = res.stdout.decode("utf-8", errors="replace").strip() if res.stdout else ""
        err = res.stderr.decode("utf-8", errors="replace").strip() if res.stderr else ""
        return (res.returncode == 0), out, err
    except Exception as e:
        log.debug(f"run_cmd_full failed ({cmd}): {e}")
        return False, "", str(e)


def run_async(fn, on_done=None) -> None:
    def _worker():
        try:
            result = fn()
            if on_done:
                GLib.idle_add(on_done, result)
        except Exception as e:
            log.error(f"run_async error: {e}")

    threading.Thread(target=_worker, daemon=True).start()


def spawn_bg(cmd: list) -> None:
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log.error(f"spawn_bg error ({cmd}): {e}")


def copy_clipboard(text: str) -> None:
    if not text:
        return
    try:
        p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
        p.communicate(input=str(text).encode("utf-8"), timeout=1.5)
    except Exception as e:
        log.error(f"copy_clipboard error: {e}")


def open_terminal(args: list) -> None:
    pref = CONFIG["general"].get("terminal", "auto")
    candidates = [pref] if pref != "auto" else ["ghostty", "kitty", "foot", "alacritty"]
    term = next((shutil.which(c) for c in candidates if shutil.which(c)), "kitty")
    spawn_bg([term, "-e"] + list(args))
