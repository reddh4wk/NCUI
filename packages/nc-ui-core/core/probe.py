#!/usr/bin/env python3
import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
import time
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
                from gi.repository import GLib
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


_LATEST_LOCK = threading.Lock()
_LATEST_COND = threading.Condition(_LATEST_LOCK)
_LATEST_JOBS: dict[str, tuple[float, object]] = {}
_LATEST_THREAD: threading.Thread | None = None


def _latest_worker() -> None:
    while True:
        due_fns = []
        with _LATEST_COND:
            while not _LATEST_JOBS:
                _LATEST_COND.wait()
            now = time.monotonic()
            next_due = min(t_due for t_due, _fn in _LATEST_JOBS.values())
            if next_due > now:
                _LATEST_COND.wait(timeout=next_due - now)
                continue
            ready_keys = [k for k, (t_due, _fn) in _LATEST_JOBS.items() if t_due <= now + 0.002]
            for k in ready_keys:
                due_fns.append(_LATEST_JOBS.pop(k)[1])
        for fn in due_fns:
            try:
                fn()
            except Exception as e:
                log.error(f"enqueue_latest error: {e}")


def enqueue_latest(key: str, fn, debounce_ms: int = 0) -> None:
    """Coalesce rapid updates by key, running only the newest callback after debounce_ms."""
    global _LATEST_THREAD
    due = time.monotonic() + (max(0, int(debounce_ms)) / 1000.0)
    with _LATEST_COND:
        _LATEST_JOBS[str(key)] = (due, fn)
        if _LATEST_THREAD is None or not _LATEST_THREAD.is_alive():
            _LATEST_THREAD = threading.Thread(target=_latest_worker, name="nc-async-latest", daemon=True)
            _LATEST_THREAD.start()
        _LATEST_COND.notify_all()


def copy_clipboard(text: str) -> None:
    if not text:
        return
    try:
        p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
        p.communicate(input=str(text).encode("utf-8"), timeout=1.5)
    except Exception as e:
        log.error(f"copy_clipboard error: {e}")


KNOWN_TERMINALS = (
    "ghostty", "kitty", "foot", "alacritty", "wezterm", "konsole",
    "gnome-terminal", "xfce4-terminal", "rio", "tilix", "xterm", "urxvt", "st",
)


def list_installed_terminals() -> list[str]:
    return [term for term in KNOWN_TERMINALS if has_bin(term)]


def open_terminal(args: list) -> bool:
    pref = str(CONFIG.get("general", {}).get("terminal", "auto") or "auto").strip()
    env_term = str(os.environ.get("TERMINAL", "") or "").strip()
    candidates = ([pref] if pref and pref != "auto" else []) + ([env_term] if env_term else []) + list_installed_terminals()
    term = next((shutil.which(c) for c in candidates if c and shutil.which(c)), "")
    if not term:
        log.warn("open_terminal: no supported terminal emulator found")
        return False
    bn = os.path.basename(term)
    prefix = [term, "--"] if bn == "gnome-terminal" else ([term, "start", "--"] if bn == "wezterm" else [term, "-e"])
    spawn_bg(prefix + list(args))
    return True


def resolve_user_editor() -> str:
    """Resolve `$VISUAL` / `$EDITOR` from environment or `/etc/profile.d/editor.sh` (never falls back to nano)."""
    for key in ("VISUAL", "EDITOR"):
        val = str(os.environ.get(key, "") or "").strip()
        if val:
            return val
    prof_path = "/etc/profile.d/editor.sh"
    if os.path.isfile(prof_path):
        try:
            raw = open(prof_path, "r", encoding="utf-8", errors="ignore").read()
            found: dict[str, str] = {}
            for m in re.finditer(r'(?m)^[ \t]*(?:export[ \t]+)?(VISUAL|EDITOR)\s*=\s*["\']?([^"\'\n#]+)["\']?', raw):
                found.setdefault(m.group(1), m.group(2).strip())
            for key in ("VISUAL", "EDITOR"):
                if found.get(key):
                    return found[key]
        except Exception:
            pass
    return ""


def open_in_editor(path: str, *, sudo: bool = False) -> tuple[bool, str]:
    """Open `path` in the configured `$VISUAL`/`$EDITOR`; return `(False, reason)` if undefined or missing."""
    ed = resolve_user_editor()
    if not ed:
        return False, "unset"
    try:
        parts = shlex.split(ed)
    except ValueError:
        parts = [ed]
    if not parts or not has_bin(parts[0]):
        return False, f"missing:{parts[0] if parts else ed}"
    prefix = "sudo " if sudo else ""
    if not open_terminal(["bash", "-c", f"{prefix}{shlex.join(parts)} {shlex.quote(path)} || read -p 'Press Enter to close...'"]):
        return False, "no_terminal"
    return True, parts[0]


_OS_RELEASE_CACHE: dict | None = None
_DISTRO_FAMILIES = {
    "arch": ("arch", "archlinux", "cachyos", "endeavouros", "manjaro", "garuda", "artix", "arcolinux", "archcraft"),
    "debian": ("debian", "ubuntu", "linuxmint", "pop", "kali", "parrot", "elementary", "zorin"),
    "fedora": ("fedora", "rhel", "centos", "almalinux", "rocky", "nobara"),
    "suse": ("opensuse", "suse", "sles", "opensuse-tumbleweed", "opensuse-leap"),
}
_FAMILY_PKG_MANAGERS = {
    "arch": ("paru", "yay", "pikaur", "trizen", "pacman"),
    "debian": ("apt", "apt-get", "nala"),
    "fedora": ("dnf", "dnf5", "yum"),
    "suse": ("zypper",),
}


def get_os_release() -> dict:
    """Read `/etc/os-release` and classify family (`arch`/`debian`/`fedora`/`suse`/`unknown`)."""
    global _OS_RELEASE_CACHE
    if _OS_RELEASE_CACHE is not None:
        return dict(_OS_RELEASE_CACHE)
    try:
        raw = platform.freedesktop_os_release()
    except OSError:
        raw = {}
    os_id = str(raw.get("ID", "") or "").strip().lower()
    id_like = str(raw.get("ID_LIKE", "") or "").strip().lower()
    pretty = str(raw.get("PRETTY_NAME") or raw.get("NAME") or "Linux").strip()
    tokens = [t for t in [os_id, *id_like.split()] if t]
    family = "unknown"
    for fam, keys in _DISTRO_FAMILIES.items():
        if any(tok in keys for tok in tokens):
            family = fam
            break
    _OS_RELEASE_CACHE = {
        "id": os_id or "linux",
        "id_like": id_like,
        "pretty_name": pretty,
        "family": family,
    }
    return dict(_OS_RELEASE_CACHE)


get_distro_info = get_os_release


def detect_esp_mounts() -> list[str]:
    """Detect mounted EFI System Partitions via findmnt or standard paths (`/boot`, `/efi`, `/boot/efi`)."""
    found: list[str] = []
    if has_bin("findmnt"):
        ok, out = run_cmd(["findmnt", "-rn", "-t", "vfat,msdos", "-o", "TARGET"], timeout=1.2)
        if ok and out:
            for ln in out.splitlines():
                mp = ln.strip()
                if mp in ("/efi", "/boot", "/boot/efi") or os.path.isdir(os.path.join(mp, "EFI")):
                    if mp not in found:
                        found.append(mp)
    for cand in ("/efi", "/boot/efi", "/boot"):
        if cand not in found and os.path.isdir(os.path.join(cand, "EFI")):
            found.append(cand)
    return found


def detect_esp() -> str:
    mounts = detect_esp_mounts()
    return mounts[0] if mounts else "/boot"


KNOWN_PKG_MANAGERS = ("paru", "yay", "pikaur", "trizen", "pacman", "apt", "dnf", "zypper", "apk", "xbps-query")


def list_installed_pkg_managers() -> list[str]:
    return [m for m in KNOWN_PKG_MANAGERS if has_bin(m)]


def get_preferred_pkg_manager(aur_only: bool = False) -> str:
    cfg_pm = str(CONFIG.get("general", {}).get("pkg_manager", "auto") or "auto").strip().lower()
    if aur_only:
        cands = ("paru", "yay", "pikaur", "trizen")
        if cfg_pm in cands and has_bin(cfg_pm):
            return cfg_pm
        return next((m for m in cands if has_bin(m)), "")
    fam_cands = _FAMILY_PKG_MANAGERS.get(get_os_release()["family"], ())
    all_cands = tuple(dict.fromkeys(fam_cands + KNOWN_PKG_MANAGERS))
    if cfg_pm in all_cands and has_bin(cfg_pm):
        return cfg_pm
    return next((m for m in all_cands if has_bin(m)), "")


detect_pkg_manager = get_preferred_pkg_manager
