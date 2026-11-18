#!/usr/bin/env python3
import os
import shutil
import subprocess
import threading
from gi.repository import GLib
from core.config import CONFIG
from core import log


_BIN_CACHE: dict[str, bool] = {}


def has_bin(name: str) -> bool:
    cached = _BIN_CACHE.get(name)
    if cached is not None:
        return cached
    found = os.path.exists(name) if name.startswith("/") else (shutil.which(name) is not None)
    _BIN_CACHE[name] = found
    return found


_HAS_NERD_FONT: bool | None = None


def has_nerd_font() -> bool:
    global _HAS_NERD_FONT
    if _HAS_NERD_FONT is not None:
        return _HAS_NERD_FONT
    try:
        import gi
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo
        fams = PangoCairo.font_map_get_default().list_families()
        _HAS_NERD_FONT = any("nerd" in f.get_name().lower() for f in fams)
    except Exception:
        ok, out = run_cmd(["fc-list", ":", "family"], timeout=1.5)
        _HAS_NERD_FONT = bool(ok and "nerd" in out.lower())
    return _HAS_NERD_FONT


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


KNOWN_PKG_MANAGERS = ("paru", "yay", "pikaur", "trizen", "pacman", "apt", "dnf", "zypper", "apk", "xbps-query")


def list_installed_pkg_managers() -> list[str]:
    return [m for m in KNOWN_PKG_MANAGERS if has_bin(m)]


def get_preferred_pkg_manager(aur_only: bool = False) -> str:
    cfg_pm = str(CONFIG.get("general", {}).get("pkg_manager", "auto") or "auto").strip().lower()
    candidates = ("paru", "yay", "pikaur", "trizen") if aur_only else KNOWN_PKG_MANAGERS
    if cfg_pm in candidates and has_bin(cfg_pm):
        return cfg_pm
    return next((m for m in candidates if has_bin(m)), "")
