#!/usr/bin/env python3
"""Unified Wayland wallpaper backend for NC-UI (awww, swaybg, slideshow, video, nc-matrix)."""

import glob
import json
import os
import random
import re
import signal
import socket
import subprocess
import sys
import threading
import time

from core import probe
from core.config import THEME, resolve_ncmatrix_bin
from core.paths import find_bin

NC_DESKTOP_BASE = os.environ.get("NC_DESKTOP_BASE") or os.path.expanduser("~/.local/nc-desktop")
WALL_DIR = os.path.join(NC_DESKTOP_BASE, "wallpapers")
STATE_DIR = os.path.join(NC_DESKTOP_BASE, "state")
PID_DIR = os.path.join(NC_DESKTOP_BASE, "pids")
LOG_DIR = os.path.join(NC_DESKTOP_BASE, "logs")

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
_GIF_EXTS = (".gif",)
_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v")
_SLIDE_EXTS = _IMAGE_EXTS + _GIF_EXTS

_SOLID_NAMED_COLORS = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#00ff00",
    "lime": "#00ff00", "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff",
    "aqua": "#00ffff", "magenta": "#ff00ff", "fuchsia": "#ff00ff", "gray": "#808080",
    "grey": "#808080", "darkgray": "#2a2a2e", "darkgrey": "#2a2a2e",
    "lightgray": "#c0c0c0", "lightgrey": "#c0c0c0", "silver": "#c0c0c0",
    "orange": "#ff8800", "purple": "#800080", "violet": "#800080", "pink": "#ffc0cb",
    "brown": "#8b4513", "navy": "#000080", "teal": "#008080", "olive": "#808000",
    "maroon": "#800000", "indigo": "#4b0082", "coral": "#ff7f50", "gold": "#ffd700",
    "cyber": "#0d0e15", "anthracite": "#18181a", "dark": "#18181a",
    "mocha": "#1e1e2e", "catppuccin": "#1e1e2e", "mantle": "#181825",
    "crust": "#11111b", "lavender": "#b4befe", "mauve": "#cba6f7",
    "peach": "#fab387", "sky": "#89dceb", "sapphire": "#74c7ec",
    "rosewater": "#f5e0dc", "flamingo": "#f2cdcd",
}
_FIT_MAP = {
    "fill": "crop", "crop": "crop",
    "fit": "fit", "contain": "fit",
    "stretch": "stretch", "scale": "stretch",
    "center": "no", "no": "no", "none": "no",
}
_TRANS_POOL = ("wipe", "wave", "grow", "outer", "fade", "simple", "left", "right")
_VALID_TRANS = set(_TRANS_POOL) | {"top", "bottom", "center", "any", "random", "none"}


def ensure_wayland_env() -> None:
    """Populate XDG_RUNTIME_DIR, WAYLAND_DISPLAY, and NIRI_SOCKET if unset."""
    rt = os.environ.get("XDG_RUNTIME_DIR", "").strip() or f"/run/user/{os.getuid()}"
    os.environ["XDG_RUNTIME_DIR"] = rt
    if not os.environ.get("WAYLAND_DISPLAY"):
        socks = sorted(s for s in glob.glob(os.path.join(rt, "wayland-*")) if not s.endswith(".lock"))
        if socks:
            os.environ["WAYLAND_DISPLAY"] = os.path.basename(socks[0])
    if not os.environ.get("NIRI_SOCKET"):
        nsocks = sorted(glob.glob(os.path.join(rt, "niri*.sock")))
        if nsocks:
            os.environ["NIRI_SOCKET"] = nsocks[0]


def get_active_outputs(wait_sec: float = 0.0) -> dict:
    """Return active Niri outputs keyed by name, optionally polling briefly at boot."""
    deadline = time.monotonic() + max(0.0, float(wait_sec or 0.0))
    while True:
        ok, out = probe.run_cmd(["niri", "msg", "-j", "outputs"], timeout=1.5)
        if ok and out:
            try:
                data = json.loads(out)
                if isinstance(data, dict):
                    active = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("logical")}
                    if active:
                        return active
            except Exception:
                pass
        if time.monotonic() >= deadline:
            return {}
        time.sleep(0.1)


def get_output_resolution(mon: str, outputs_data: dict | None = None) -> tuple[int, int]:
    """Return pixel (width, height) for a Niri output."""
    data = outputs_data if isinstance(outputs_data, dict) else get_active_outputs()
    out = data.get(mon) if isinstance(data, dict) else None
    if isinstance(out, dict):
        modes = out.get("modes") or []
        cur_idx = out.get("current_mode")
        if isinstance(cur_idx, int) and 0 <= cur_idx < len(modes):
            m = modes[cur_idx] or {}
            mw, mh = int(m.get("width") or 0), int(m.get("height") or 0)
            if mw >= 320 and mh >= 240:
                return mw, mh
        logical = out.get("logical") or {}
        lw, lh = int(logical.get("width") or 0), int(logical.get("height") or 0)
        if lw >= 320 and lh >= 240:
            sc = float(logical.get("scale") or 1.0)
            return int(round(lw * sc)), int(round(lh * sc))
    return 1920, 1080


def target_yt_height(w: int, h: int) -> int:
    short_side = min(max(320, int(w or 1920)), max(240, int(h or 1080)))
    for tier in (720, 1080, 1440, 2160):
        if short_side <= tier + 120:
            return tier
    return 2160


def resolve_solid_hex(raw: str) -> str:
    s = str(raw or "").strip().lower()
    if s in _SOLID_NAMED_COLORS:
        return _SOLID_NAMED_COLORS[s].upper()
    hx = s.lstrip("#")
    if hx.startswith("0x"):
        hx = hx[2:]
    if len(hx) == 3 and re.fullmatch(r"[0-9a-f]{3}", hx):
        hx = "".join(c * 2 for c in hx)
    if re.fullmatch(r"[0-9a-f]{6}", hx):
        return f"#{hx.upper()}"
    return "#18181A"


def norm_align(raw) -> str:
    v = str(raw or "center").strip().lower()
    return v if v in ("top", "center", "bottom") else "center"


def parse_interval_seconds(raw: str) -> int:
    s = str(raw or "5m").strip()
    m = re.fullmatch(r"([0-9]+)([sSmMhH]?)", s)
    if not m:
        raise ValueError(f"Invalid interval: {raw}")
    val = int(m.group(1))
    unit = (m.group(2) or "s").lower()
    mult = 3600 if unit == "h" else (60 if unit == "m" else 1)
    return val * mult


def wallpaper_pid_files(mon: str) -> tuple[str, ...]:
    return (
        f"slideshow_{mon}.pid",
        f"video_{mon}.pid",
        f"nc-matrix_{mon}.pid",
        f"ncmatrix_{mon}.pid",
        f"cmatrix_{mon}.pid",
        f"solid_{mon}.pid",
    )


def send_mpv_ipc(mon: str, cmd_args: list) -> bool:
    sock_path = f"/tmp/nc_mpvpaper_{mon}.sock"
    if not os.path.exists(sock_path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(sock_path)
            s.sendall((json.dumps({"command": cmd_args}) + "\n").encode("utf-8"))
        return True
    except Exception:
        return False


def collect_output_pids(mon: str) -> list[int]:
    pids: set[int] = set()
    for pf_name in wallpaper_pid_files(mon):
        pid_f = os.path.join(PID_DIR, pf_name)
        if not os.path.isfile(pid_f):
            continue
        try:
            pid = int(open(pid_f, "r", encoding="utf-8").read().strip())
            if pid > 0:
                os.kill(pid, 0)
                pids.add(pid)
        except Exception:
            pass
    if probe.has_bin("pgrep"):
        for pat in (
            f"^([^ ]*/)?nc-matrix .*--output[= ]{mon}( |$)",
            f"^([^ ]*/)?ncmatrix .*--output[= ]{mon}( |$)",
            f"^([^ ]*/)?swaybg .*-o[= ]{mon}( |$)",
            f"^([^ ]*/)?(python[^ ]* |env python[^ ]* )?([^ ]*/)?nc-video-wallpaper.*--output[= ]{mon}( |$)",
            f"^([^ ]*/)?ffplay .*-window_title nc-vidwp-audio-{mon}( |$)",
            f"^([^ ]*/)?pacat .*nc-vidwp-audio-{mon}( |$)",
            f"^([^ ]*/)?mpvpaper .*( |^){mon}( |$)",
            f"nc-ctl.*wallpaper slideshow-worker {mon}( |$)",
        ):
            ok_p, out_p = probe.run_cmd(["pgrep", "-f", pat], timeout=1.0)
            if ok_p and out_p:
                for ln in out_p.splitlines():
                    try:
                        p = int(ln.strip())
                        if p > 0 and p != os.getpid():
                            pids.add(p)
                    except ValueError:
                        pass
    return sorted(pids)


def get_running_ncmatrix_pid(mon: str) -> int:
    pid_f = os.path.join(PID_DIR, f"nc-matrix_{mon}.pid")
    if not os.path.isfile(pid_f):
        return 0
    try:
        pid = int(open(pid_f, "r", encoding="utf-8").read().strip())
        if pid <= 0:
            return 0
        os.kill(pid, 0)
        with open(f"/proc/{pid}/cmdline", "rb") as cf:
            cmd = cf.read().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        if "nc-matrix" in cmd and f"--output {mon}" in cmd:
            exe_p = os.readlink(f"/proc/{pid}/exe")
            bin_p = resolve_ncmatrix_bin()
            if not bin_p or exe_p.endswith(" (deleted)") or not os.path.samefile(exe_p, bin_p):
                return 0
            return pid
    except Exception:
        pass
    return 0


def spawn_bg_pid(cmd: list, log_file: str = "") -> int:
    out_f = None
    try:
        if log_file:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            out_f = open(log_file, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=out_f if out_f is not None else subprocess.DEVNULL,
            stderr=out_f if out_f is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
        return int(proc.pid or 0)
    except Exception:
        return 0
    finally:
        if out_f is not None:
            try:
                out_f.close()
            except OSError:
                pass


def kill_pids_after_overlap(
    old_pids: list[int],
    new_pid: int = 0,
    delay: float = 0.30,
    remove_pf: tuple[str, ...] = (),
    sync: bool = False,
) -> None:
    targets = [int(p) for p in set(old_pids or ()) if int(p) > 0 and int(p) != int(new_pid or 0) and int(p) != os.getpid()]
    if not targets and not remove_pf:
        return

    def _worker() -> None:
        if delay > 0:
            time.sleep(delay)
        if new_pid > 0:
            try:
                os.kill(new_pid, 0)
            except OSError:
                return
        for pf in remove_pf:
            try:
                os.remove(pf)
            except OSError:
                pass
        alive = []
        for pid in targets:
            try:
                if probe.has_bin("pkill"):
                    subprocess.run(["pkill", "-TERM", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.kill(pid, signal.SIGTERM)
                alive.append(pid)
            except OSError:
                pass
        if alive:
            time.sleep(0.15)
            for pid in alive:
                try:
                    os.kill(pid, 0)
                    if probe.has_bin("pkill"):
                        subprocess.run(["pkill", "-KILL", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    if sync:
        _worker()
    else:
        threading.Thread(target=_worker, daemon=True).start()


def stop_output(mon: str) -> None:
    """Stop every managed wallpaper daemon and clear pid/socket files for `mon`."""
    send_mpv_ipc(mon, ["quit"])
    for pf_name in wallpaper_pid_files(mon):
        pid_f = os.path.join(PID_DIR, pf_name)
        if os.path.isfile(pid_f):
            try:
                pid = int(open(pid_f, "r", encoding="utf-8").read().strip())
                if pid > 0 and pid != os.getpid():
                    if probe.has_bin("pkill"):
                        subprocess.run(["pkill", "-TERM", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(10):
                        time.sleep(0.03)
                        os.kill(pid, 0)
                    if probe.has_bin("pkill"):
                        subprocess.run(["pkill", "-KILL", "-P", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
            try:
                os.remove(pid_f)
            except OSError:
                pass
    for path in (os.path.join(PID_DIR, f"pause_{mon}.flag"), f"/tmp/nc_mpvpaper_{mon}.sock"):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    if probe.has_bin("pkill"):
        for pat in (
            f"^([^ ]*/)?nc-matrix .*--output[= ]{mon}( |$)",
            f"^([^ ]*/)?ncmatrix .*--output[= ]{mon}( |$)",
            f"^([^ ]*/)?swaybg .*-o[= ]{mon}( |$)",
            f"^([^ ]*/)?(python[^ ]* |env python[^ ]* )?([^ ]*/)?nc-video-wallpaper.*--output[= ]{mon}( |$)",
            "^([^ ]*/)?(python[^ ]* |env python[^ ]* )?([^ ]*/)?nc-video-wallpaper.*--mode[= ]cmatrix",
            f"^([^ ]*/)?ffplay .*-window_title nc-vidwp-audio-{mon}( |$)",
            f"^([^ ]*/)?pacat .*nc-vidwp-audio-{mon}( |$)",
            f"^([^ ]*/)?mpvpaper .*( |^){mon}( |$)",
            "^([^ ]*/)?ffplay -nodisp -vn -loglevel quiet -autoexit ",
            f"nc-ctl.*wallpaper slideshow-worker {mon}( |$)",
        ):
            subprocess.run(["pkill", "-9", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_output_state(mon: str) -> dict:
    sf = os.path.join(STATE_DIR, f"{mon}.json")
    if not os.path.isfile(sf):
        return {}
    try:
        data = json.load(open(sf, "r", encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_output_state(mon: str, payload: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        sf = os.path.join(STATE_DIR, f"{mon}.json")
        tmp_sf = f"{sf}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp_sf, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_sf, sf)
    except Exception:
        pass


def merge_output_state(mon: str, extra: dict) -> None:
    data = read_output_state(mon)
    data.update(extra)
    data.setdefault("output", mon)
    save_output_state(mon, data)


def ensure_awww_daemon() -> bool:
    """Ensure `awww-daemon` is running and responding to queries."""
    ok, _ = probe.run_cmd(["awww", "query"], timeout=1.0)
    if ok:
        return True
    if not probe.has_bin("awww") or not probe.has_bin("awww-daemon"):
        return False
    running = False
    if probe.has_bin("pgrep"):
        running, _ = probe.run_cmd(["pgrep", "-u", str(os.getuid()), "-x", "awww-daemon"], timeout=1.0)
    if not running:
        spawn_bg_pid(["awww-daemon"])
    for _ in range(30):
        ok, _ = probe.run_cmd(["awww", "query"], timeout=1.0)
        if ok:
            return True
        time.sleep(0.1)
    return False


def _resolve_local_media(raw: str, is_dir: bool = False) -> str:
    p = os.path.expanduser(str(raw or "").strip())
    if not p:
        return ""
    chk = os.path.isdir if is_dir else os.path.isfile
    if chk(p):
        return os.path.realpath(p)
    cand = os.path.join(WALL_DIR, p)
    return os.path.realpath(cand) if chk(cand) else ""


def _list_slideshow_files(folder: str, shuffle: bool) -> list[str]:
    root = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(root):
        return []
    files: list[str] = []
    for base, dirs, names in os.walk(root, followlinks=True):
        rel = os.path.relpath(base, root)
        if (0 if rel == "." else len(rel.split(os.sep))) >= 1:
            dirs[:] = []
        for fn in names:
            if fn.lower().endswith(_SLIDE_EXTS):
                files.append(os.path.join(base, fn))
    if shuffle:
        random.shuffle(files)
    else:
        files.sort()
    return files


def _run_awww_img(
    mon: str,
    img_path: str,
    fit: str = "fill",
    transition: str = "wipe",
    bgcolor: str = "18181a",
    align: str = "center",
) -> tuple[bool, str]:
    resize = _FIT_MAP.get(str(fit or "fill").lower())
    if not resize:
        return False, f"Invalid fit mode: {fit}"
    trans = str(transition or "wipe").lower()
    if trans not in _VALID_TRANS:
        trans = "wipe"
    actual_trans = random.choice(_TRANS_POOL) if trans == "random" else trans
    cmd = [
        "awww", "img", img_path,
        "--outputs", mon,
        "--resize", resize,
        "--fill-color", str(bgcolor or "18181a").lstrip("#"),
    ]
    if resize == "crop":
        cmd.extend(["--crop-gravity", norm_align(align)])
    if actual_trans == "none":
        cmd.extend(["--transition-type", "simple", "--transition-step", "255", "--transition-duration", "0"])
    elif actual_trans in ("wipe", "wave"):
        cmd.extend(["--transition-type", actual_trans, "--transition-angle", "30", "--transition-step", "90", "--transition-duration", "1.4", "--transition-fps", "120"])
    elif actual_trans in ("grow", "outer"):
        cmd.extend(["--transition-type", actual_trans, "--transition-pos", "0.5,0.5", "--transition-step", "90", "--transition-duration", "1.4", "--transition-fps", "120"])
    else:
        cmd.extend(["--transition-type", actual_trans, "--transition-step", "90", "--transition-duration", "1.2", "--transition-fps", "120"])
    ok, _out, err = probe.run_cmd_full(cmd, timeout=8.0)
    return ok, (err or "").strip()


def apply_image(
    mon: str,
    img_path: str,
    fit: str = "fill",
    transition: str = "wipe",
    bgcolor: str = "18181a",
    align: str = "center",
    save: bool = True,
    sync_cleanup: bool = False,
) -> tuple[bool, str]:
    resolved = _resolve_local_media(img_path, is_dir=False)
    if not resolved:
        return False, f"Image not found: {img_path}"
    if not ensure_awww_daemon():
        return False, "awww-daemon unavailable"
    old_pids = collect_output_pids(mon)
    ok, err = _run_awww_img(mon, resolved, fit=fit, transition=transition, bgcolor=bgcolor, align=align)
    if not ok:
        return False, err or f"awww img failed for {mon}"
    if save:
        save_output_state(mon, {
            "output": mon,
            "mode": "image",
            "target": resolved,
            "fit": fit,
            "align": norm_align(align),
            "transition": transition,
            "bgcolor": str(bgcolor or "18181a").lstrip("#").lower(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    if old_pids:
        stale_pfs = tuple(os.path.join(PID_DIR, pf) for pf in wallpaper_pid_files(mon))
        kill_pids_after_overlap(old_pids, new_pid=0, delay=0.25, remove_pf=stale_pfs, sync=sync_cleanup)
    return True, f"{mon}: image applied"


def apply_solid(
    mon: str,
    color_spec: str,
    save: bool = True,
    sync_cleanup: bool = False,
) -> tuple[bool, str]:
    hex_val = resolve_solid_hex(color_spec)
    hx_bare = hex_val.lstrip("#").lower()
    old_pids = collect_output_pids(mon)
    if probe.has_bin("swaybg"):
        new_pid = spawn_bg_pid(["swaybg", "-o", mon, "-c", hex_val, "-m", "solid_color"])
        if new_pid <= 0:
            return False, f"swaybg failed to start on {mon}"
        os.makedirs(PID_DIR, exist_ok=True)
        try:
            with open(os.path.join(PID_DIR, f"solid_{mon}.pid"), "w", encoding="utf-8") as pf:
                pf.write(f"{new_pid}\n")
        except OSError:
            pass
        stale_pfs = tuple(os.path.join(PID_DIR, pf) for pf in wallpaper_pid_files(mon) if pf != f"solid_{mon}.pid")
        kill_pids_after_overlap(old_pids, new_pid=new_pid, delay=0.30, remove_pf=stale_pfs, sync=sync_cleanup)
    else:
        if not ensure_awww_daemon():
            return False, "awww-daemon unavailable"
        ok, _out, err = probe.run_cmd_full(["awww", "clear", "--outputs", mon, hx_bare], timeout=5.0)
        if not ok:
            return False, (err or f"awww clear failed for {mon}").strip()
        if old_pids:
            stale_pfs = tuple(os.path.join(PID_DIR, pf) for pf in wallpaper_pid_files(mon))
            kill_pids_after_overlap(old_pids, new_pid=0, delay=0.25, remove_pf=stale_pfs, sync=sync_cleanup)
    if save:
        save_output_state(mon, {
            "output": mon,
            "mode": "solid",
            "target": hex_val,
            "hex": hex_val,
            "bgcolor": hx_bare,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return True, f"{mon}: solid {hex_val}"


def apply_slideshow(
    mon: str,
    dir_path: str,
    fit: str = "fill",
    transition: str = "fade",
    interval: str = "5m",
    bgcolor: str = "18181a",
    shuffle: bool = True,
    save: bool = True,
    sync_cleanup: bool = False,
) -> tuple[bool, str]:
    try:
        interval_sec = parse_interval_seconds(interval)
    except ValueError as e:
        return False, str(e)
    if interval_sec < 2:
        return False, "Minimum slideshow interval is 2s"
    resolved_dir = _resolve_local_media(dir_path, is_dir=True)
    if not resolved_dir or not _list_slideshow_files(resolved_dir, False):
        return False, f"No images found in {dir_path}"
    if not ensure_awww_daemon():
        return False, "awww-daemon unavailable"
    nc_ctl_bin = find_bin("nc-ctl")
    if not nc_ctl_bin:
        return False, "nc-ctl not found"

    old_pids = collect_output_pids(mon)
    if save:
        save_output_state(mon, {
            "output": mon,
            "mode": "slideshow",
            "target": resolved_dir,
            "fit": fit,
            "transition": transition,
            "interval": str(interval),
            "bgcolor": str(bgcolor or "18181a").lstrip("#").lower(),
            "shuffle": "true" if shuffle else "false",
            "order": "shuffle" if shuffle else "sequential",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    new_pid = spawn_bg_pid(
        [
            sys.executable, nc_ctl_bin, "wallpaper", "slideshow-worker",
            mon, resolved_dir, fit, transition, str(interval_sec),
            str(bgcolor or "18181a").lstrip("#").lower(), "1" if shuffle else "0",
        ],
        log_file=os.path.join(LOG_DIR, f"slideshow_{mon}.log"),
    )
    if new_pid <= 0:
        return False, f"Failed to spawn slideshow worker for {mon}"
    os.makedirs(PID_DIR, exist_ok=True)
    try:
        with open(os.path.join(PID_DIR, f"slideshow_{mon}.pid"), "w", encoding="utf-8") as pf:
            pf.write(f"{new_pid}\n")
    except OSError:
        pass
    stale_pfs = tuple(os.path.join(PID_DIR, pf) for pf in wallpaper_pid_files(mon) if pf != f"slideshow_{mon}.pid")
    kill_pids_after_overlap(old_pids, new_pid=new_pid, delay=0.30, remove_pf=stale_pfs, sync=sync_cleanup)
    return True, f"{mon}: slideshow started"


def run_slideshow_worker(
    mon: str,
    dir_path: str,
    fit: str = "fill",
    transition: str = "fade",
    interval_sec: int = 300,
    bgcolor: str = "18181a",
    shuffle: bool = True,
) -> None:
    """Background loop rotating images on `mon` via `awww`."""
    ensure_wayland_env()
    if not ensure_awww_daemon():
        return
    wake = threading.Event()
    stop_flag = {"stop": False}

    def _on_next(_sig, _frm):
        wake.set()

    def _on_stop(_sig, _frm):
        stop_flag["stop"] = True
        wake.set()

    signal.signal(signal.SIGUSR1, _on_next)
    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)

    pause_flag = os.path.join(PID_DIR, f"pause_{mon}.flag")
    delay = max(2, int(interval_sec))
    while not stop_flag["stop"]:
        files = _list_slideshow_files(dir_path, bool(shuffle))
        if not files:
            wake.wait(10.0)
            wake.clear()
            continue
        for img in files:
            while not stop_flag["stop"] and os.path.exists(pause_flag):
                wake.wait(1.0)
                if wake.is_set():
                    wake.clear()
                    break
            if stop_flag["stop"]:
                break
            _run_awww_img(mon, img, fit=fit, transition=transition, bgcolor=bgcolor)
            merge_output_state(mon, {"current_image": os.path.basename(img)})
            wake.wait(float(delay))
            wake.clear()


def control_slideshow(mon: str, action: str) -> tuple[bool, str]:
    pid_f = os.path.join(PID_DIR, f"slideshow_{mon}.pid")
    pause_flag = os.path.join(PID_DIR, f"pause_{mon}.flag")
    if action == "stop":
        stop_output(mon)
        return True, f"{mon}: stopped"
    if action == "resume":
        try:
            if os.path.exists(pause_flag):
                os.remove(pause_flag)
                return True, f"{mon}: resumed"
        except OSError:
            pass
        return False, f"{mon}: not paused"

    pid = 0
    try:
        pid = int(open(pid_f, "r", encoding="utf-8").read().strip())
        os.kill(pid, 0)
    except Exception:
        pid = 0
    if pid <= 0:
        return False, f"{mon}: no active slideshow"
    if action == "pause":
        os.makedirs(PID_DIR, exist_ok=True)
        open(pause_flag, "a", encoding="utf-8").close()
        return True, f"{mon}: paused"
    if action == "next":
        try:
            os.kill(pid, signal.SIGUSR1)
            return True, f"{mon}: skipped to next"
        except OSError as e:
            return False, str(e)
    return False, f"Unknown slideshow action: {action}"


def apply_video(
    mon: str,
    target: str,
    outputs_data: dict | None = None,
    fit: str = "fill",
    align: str = "center",
    media_kind: str = "",
    yt_delivery: str = "",
    video_loop: str = "loop",
    playlist_order: str = "sequential",
    video_mute: bool = True,
    video_volume: int = 60,
    save: bool = True,
    sync_cleanup: bool = False,
) -> tuple[bool, str]:
    target_p = str(target or "").strip()
    if not target_p:
        return False, "Missing video target"
    video_bin = find_bin("nc-video-wallpaper")
    use_mpv = probe.has_bin("mpvpaper")
    if not use_mpv and not (video_bin and os.path.isfile(video_bin)):
        return False, "mpvpaper / nc-video-wallpaper missing"

    old_pids = collect_output_pids(mon)
    is_mute = bool(video_mute)
    vol = max(0, min(100, int(video_volume or 60)))
    yt_deliv = str(yt_delivery or ("stream" if target_p.startswith("http") else "download"))
    vid_loop = str(video_loop or "loop")
    pl_order = str(playlist_order or "sequential")
    fit_mode = str(fit or "fill")
    al_v = norm_align(align)
    ipc_sock = f"/tmp/nc_mpvpaper_{mon}.sock"
    try:
        if os.path.exists(ipc_sock):
            os.remove(ipc_sock)
    except OSError:
        pass

    mon_w, mon_h = get_output_resolution(mon, outputs_data)
    target_h = target_yt_height(mon_w, mon_h)

    if use_mpv:
        opts = [f"input-ipc-server={ipc_sock}"]
        if target_p.startswith("http") or yt_deliv == "stream":
            opts.extend(["ytdl=yes", f"ytdl-format=bv*[height<={target_h}]+ba/b[height<={target_h}]/b"])
        if vid_loop == "once":
            opts.append("keep-open=yes")
        elif media_kind == "YT PLAYLIST" or os.path.isdir(target_p):
            opts.append("loop-playlist=inf")
        else:
            opts.append("loop=inf")
        if media_kind == "YT PLAYLIST" and pl_order == "shuffle":
            opts.append("shuffle")
        if fit_mode == "fit":
            opts.append("panscan=0.0")
        elif fit_mode == "stretch":
            opts.append("keepaspect=no")
        elif fit_mode == "center":
            opts.append("video-unscaled=yes")
        else:
            opts.append("panscan=1.0")
            opts.append(f"video-align-y={-1 if al_v == 'top' else (1 if al_v == 'bottom' else 0)}")
        opts.append("no-audio" if is_mute else f"volume={vol}")
        new_pid = spawn_bg_pid(["mpvpaper", "-o", " ".join(opts), mon, target_p])
    else:
        new_pid = spawn_bg_pid([
            sys.executable, video_bin,
            "--output", mon,
            "--target", target_p,
            "--fit", fit_mode,
            "--align", al_v,
            "--loop", vid_loop,
            "--order", pl_order,
            "--mute", "1" if is_mute else "0",
            "--volume", str(vol),
            "--width", str(mon_w),
            "--height", str(mon_h),
            "--max-height", str(target_h),
            "--ipc-socket", ipc_sock,
        ])

    if new_pid > 0:
        os.makedirs(PID_DIR, exist_ok=True)
        try:
            with open(os.path.join(PID_DIR, f"video_{mon}.pid"), "w", encoding="utf-8") as pf:
                pf.write(f"{new_pid}\n")
        except OSError:
            pass
    stale_pfs = tuple(os.path.join(PID_DIR, pf) for pf in wallpaper_pid_files(mon) if pf != f"video_{mon}.pid")
    kill_pids_after_overlap(old_pids, new_pid=new_pid, delay=0.35, remove_pf=stale_pfs, sync=sync_cleanup)
    if save:
        save_output_state(mon, {
            "output": mon,
            "mode": "scene",
            "target": target_p,
            "fit": fit_mode,
            "align": al_v,
            "media_kind": media_kind,
            "yt_delivery": yt_deliv,
            "video_loop": vid_loop,
            "playlist_order": pl_order,
            "video_mute": is_mute,
            "video_volume": vol,
        })
    return True, f"{mon}: video applied"


def _norm_cm_float(raw, lo: float, hi: float, default: float, step: float = 0.1) -> str:
    s = str(default if (raw is None or str(raw).strip() == "") else raw).strip().lower().replace(",", ".")
    if s in ("on", "true", "yes"):
        v = default
    elif s in ("off", "false", "no"):
        v = 0.0
    else:
        try:
            v = float(s)
        except (TypeError, ValueError):
            v = default
    v = max(lo, min(hi, round(v / step) * step))
    fmt = "{:.2f}" if step < 0.09 else "{:.1f}"
    return fmt.format(0.0 if abs(v) < 1e-9 else v)


def resolve_ncmatrix_color(col: str) -> str:
    """Resolve 'theme' matrix color to active NC-UI intent hex; return other values verbatim."""
    s = str(col or "green").strip()
    if s.lower() == "theme":
        hx = str(THEME.get("colors", {}).get("intent", "#00f0ff") or "#00f0ff").strip()
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", hx):
            return hx.lower()
        return "#00f0ff"
    return s or "green"


def apply_ncmatrix(
    mon: str,
    spec: dict,
    outputs_data: dict | None = None,
    prev_is_ncm: bool = False,
    prev_size: str = "6",
    defer_json: bool = False,
    sync_cleanup: bool = False,
    save: bool = True,
) -> tuple[bool, str]:
    ncmatrix_bin = resolve_ncmatrix_bin()
    if not ncmatrix_bin:
        return False, "nc-matrix missing"

    jp_raw = spec.get("japanese")
    cs = str(spec.get("charset") or ("ascii" if jp_raw in (False, "false", "0") else "katakana"))
    col = str(spec.get("color") or "theme")
    resolved_col = resolve_ncmatrix_color(col)
    spd = str(spec.get("speed") or "3")
    sz = str(spec.get("size") or "6")
    sp_rate = _norm_cm_float(spec.get("spawn_rate", "0.7"), 0.0, 3.0, 0.7, 0.1)
    hc_raw = str(spec.get("head_color") or "blend").strip().lower()
    head_col = "white" if hc_raw in ("white", "1", "on", "true") else ("match" if hc_raw in ("match", "color", "trail", "shader", "0", "false", "off") else "blend")
    spd_var = _norm_cm_float(spec.get("speed_variance", "6.0"), 0.0, 10.0, 6.0, 0.1)
    ov_raw = str(spec.get("overlap_mode") or "hybrid").strip().lower()
    overlap_m = "parallax" if ov_raw in ("parallax", "overwrite", "cover", "1") else ("additive" if ov_raw in ("additive", "screen", "boost", "2") else "hybrid")
    parallax_m = _norm_cm_float(spec.get("depth_parallax", "0.40"), 0.0, 1.0, 0.40, 0.05)
    glow_v = _norm_cm_float(spec.get("glow", "0.80"), 0.0, 1.0, 0.80, 0.05)
    dec_raw = str(spec.get("decay_mode") or "exponential").strip().lower()
    decay_m = "linear" if dec_raw in ("linear", "classic", "1") else "exponential"
    shm_raw = str(spec.get("shimmer") or "insane").strip().lower()
    shimmer_m = {"off": "off", "none": "off", "0": "off", "false": "off", "low": "low", "1": "low", "med": "med", "medium": "med", "2": "med", "high": "high", "3": "high", "on": "high", "true": "high"}.get(shm_raw, "insane")
    mir_raw = str(spec.get("mirror") or "random").strip().lower()
    mirror_m = "off" if mir_raw in ("off", "normal", "0", "false") else ("on" if mir_raw in ("on", "all", "mirror", "1", "true") else "random")
    tl_raw = str(spec.get("tail_length") or "normal").strip().lower()
    tail_m = "short" if tl_raw in ("short", "0") else ("long" if tl_raw in ("long", "2") else "normal")
    den_raw = str(spec.get("col_density") or "dense").strip().lower()
    density_m = "classic" if den_raw in ("classic", "0", "off", "false", "no") else "dense"
    cm_trans = str(spec.get("cm_trans") or spec.get("transition") or "flow")

    try:
        with open(f"/tmp/nc-matrix_{mon}.conf", "w", encoding="utf-8") as cf:
            cf.write(
                f"charset={cs}\ncolor={resolved_col}\nspeed={spd}\nspawn_rate={sp_rate}\n"
                f"head_color={head_col}\nspeed_variance={spd_var}\n"
                f"overlap_mode={overlap_m}\ndepth_parallax={parallax_m}\n"
                f"glow={glow_v}\ndecay_mode={decay_m}\nshimmer={shimmer_m}\n"
                f"mirror={mirror_m}\ntail_length={tail_m}\ncol_density={density_m}\n"
                f"font_size={sz}\n"
                f"transition={cm_trans if cm_trans in ('drain', 'instant') else 'flow'}\nbold=2\nasync=1\nfade=1\n"
            )
    except OSError:
        pass

    running_pid = get_running_ncmatrix_pid(mon)
    pid_file = os.path.join(PID_DIR, f"nc-matrix_{mon}.pid")

    if running_pid > 0:
        if not prev_is_ncm:
            other_pids = [p for p in collect_output_pids(mon) if p != running_pid]
            if other_pids:
                stale_pfs = tuple(os.path.join(PID_DIR, pf) for pf in wallpaper_pid_files(mon) if pf != f"nc-matrix_{mon}.pid")
                kill_pids_after_overlap(other_pids, new_pid=running_pid, delay=0.05, remove_pf=stale_pfs, sync=sync_cleanup)
        sig = signal.SIGUSR2 if (cm_trans == "drain" or (cm_trans != "instant" and str(prev_size) != sz)) else signal.SIGUSR1
        try:
            os.kill(running_pid, sig)
        except OSError:
            running_pid = 0

    if running_pid <= 0:
        old_pids = collect_output_pids(mon)
        outs = outputs_data if isinstance(outputs_data, dict) else get_active_outputs()
        mon_w, mon_h = get_output_resolution(mon, outs)
        logical = (outs.get(mon) or {}).get("logical") or {}
        lx, ly = logical.get("x"), logical.get("y")
        ipc_sock = f"/tmp/nc_mpvpaper_{mon}.sock"
        try:
            if os.path.exists(ipc_sock):
                os.remove(ipc_sock)
        except OSError:
            pass
        cmd = [
            ncmatrix_bin, "--wallpaper", "--output", mon,
            "--charset", cs, "--color", resolved_col, "--speed", spd,
            "--spawn-rate", sp_rate, "--head-color", head_col,
            "--speed-variance", spd_var, "--overlap", overlap_m,
            "--parallax", parallax_m, "--glow", glow_v,
            "--decay", decay_m, "--shimmer", shimmer_m,
            "--mirror", mirror_m, "--tail-length", tail_m,
            "--dense", density_m, "--font-size", sz,
            "--width", str(mon_w), "--height", str(mon_h),
            "--pid-file", pid_file, "--ipc-socket", ipc_sock, "-a", "-b",
        ]
        if lx is not None and ly is not None:
            cmd.extend(["--x", str(int(lx)), "--y", str(int(ly))])
        new_pid = spawn_bg_pid(cmd, log_file=os.path.join(LOG_DIR, f"nc-matrix_{mon}.log"))
        if new_pid <= 0:
            return False, f"Failed to spawn nc-matrix on {mon}"
        os.makedirs(PID_DIR, exist_ok=True)
        try:
            with open(pid_file, "w", encoding="utf-8") as pf:
                pf.write(f"{new_pid}\n")
        except OSError:
            pass
        stale_pfs = tuple(os.path.join(PID_DIR, pf) for pf in wallpaper_pid_files(mon) if pf != f"nc-matrix_{mon}.pid")
        kill_pids_after_overlap(old_pids, new_pid=new_pid, delay=0.35, remove_pf=stale_pfs, sync=sync_cleanup)

    ncm_payload = {
        "output": mon,
        "mode": "ncmatrix",
        "target": "ncmatrix",
        "charset": cs,
        "color": col,
        "speed": spd,
        "size": sz,
        "spawn_rate": sp_rate,
        "head_color": head_col,
        "speed_variance": spd_var,
        "overlap_mode": overlap_m,
        "depth_parallax": parallax_m,
        "glow": glow_v,
        "decay_mode": decay_m,
        "shimmer": shimmer_m,
        "mirror": mirror_m,
        "tail_length": tail_m,
        "col_density": density_m,
        "cm_trans": cm_trans,
        "japanese": (cs == "katakana"),
    }
    if save:
        if defer_json and running_pid > 0:
            probe.enqueue_latest(f"wp_json_save_{mon}", lambda m=mon, p=ncm_payload: save_output_state(m, p), debounce_ms=150)
        else:
            save_output_state(mon, ncm_payload)
    return True, f"{mon}: nc-matrix applied"


_KATAKANA_FONT_CACHE: bool | None = None


def sync_ncmatrix_theme_wallpapers() -> int:
    """Re-apply live theme color to all running nc-matrix monitors configured with color='theme'."""
    if not os.path.isdir(STATE_DIR):
        return 0
    synced = 0
    for sf in glob.glob(os.path.join(STATE_DIR, "*.json")):
        mon = os.path.basename(sf)[:-5]
        st = read_output_state(mon)
        if (
            isinstance(st, dict)
            and str(st.get("mode") or "").lower() in ("ncmatrix", "cmatrix")
            and str(st.get("color") or "").strip().lower() == "theme"
            and get_running_ncmatrix_pid(mon) > 0
        ):
            ok, _ = apply_ncmatrix(mon, st, prev_is_ncm=True, prev_size=str(st.get("size") or "6"), save=False)
            if ok:
                synced += 1
    return synced


def has_katakana_font() -> bool:
    """Return True if a font covering halfwidth Katakana (U+FF71) is installed."""
    global _KATAKANA_FONT_CACHE
    if _KATAKANA_FONT_CACHE is not None:
        return _KATAKANA_FONT_CACHE
    found = False
    if probe.has_bin("fc-list"):
        ok, out = probe.run_cmd(["fc-list", ":charset=ff71", "file"], timeout=1.0)
        found = bool(ok and out.strip())
    else:
        for base in ("/usr/share/fonts/noto-cjk", "/usr/share/fonts/OTF", "/usr/share/fonts/TTF"):
            if os.path.isdir(base) and any(
                any(k in fn.lower() for k in ("cjk", "ipa", "ipafont", "han", "katakana", "gothic", "mincho"))
                for fn in os.listdir(base)
            ):
                found = True
                break
    _KATAKANA_FONT_CACHE = found
    return found


def default_ncmatrix_state() -> dict:
    """Default nc-matrix state used on first startup when no wallpaper is configured."""
    return {
        "mode": "ncmatrix",
        "charset": "katakana" if has_katakana_font() else "ascii",
        "color": "theme",
        "speed": "3",
        "size": "6",
        "spawn_rate": "0.7",
        "head_color": "blend",
        "speed_variance": "6.0",
        "overlap_mode": "hybrid",
        "depth_parallax": "0.40",
        "glow": "0.80",
        "decay_mode": "exponential",
        "shimmer": "insane",
        "mirror": "random",
        "tail_length": "normal",
        "col_density": "dense",
        "cm_trans": "flow",
    }


def _restore_ascii_meta(mon: str, st: dict) -> None:
    src_t = st.get("source_target")
    if not src_t:
        return
    extra = {"source_target": src_t, "rendered_target": st.get("rendered_target", "")}
    if isinstance(st.get("asciify"), dict):
        extra["asciify"] = st["asciify"]
    merge_output_state(mon, extra)


def restore_all(target_mon: str = "") -> tuple[bool, str]:
    """Restore saved wallpaper state for `target_mon` or all active Niri outputs."""
    ensure_wayland_env()
    outputs = get_active_outputs(wait_sec=3.0)
    if not outputs:
        return False, "No active Niri outputs detected"

    mons = [target_mon] if (target_mon and target_mon != "all") else sorted(outputs.keys())
    restored: list[str] = []
    errors: list[str] = []

    for mon in mons:
        if mon not in outputs:
            continue
        st = read_output_state(mon)
        if not st:
            if resolve_ncmatrix_bin():
                ok, msg = apply_ncmatrix(mon, default_ncmatrix_state(), outputs_data=outputs, sync_cleanup=True, save=True)
                if ok:
                    restored.append(mon)
                else:
                    errors.append(f"{mon}: {msg}")
            continue
        mode = str(st.get("mode") or "").lower()
        target = str(st.get("target") or "").strip()
        fit = str(st.get("fit") or "fill")
        align = norm_align(st.get("align", "center"))
        trans = str(st.get("trans") or st.get("transition") or "wipe")
        bgcolor = str(st.get("bgcolor") or st.get("hex") or "18181a")
        rendered = str(st.get("rendered_target") or "").strip()
        media_kind = str(st.get("media_kind") or "")

        if mode in ("image", "scene"):
            is_video = (
                media_kind in ("VIDEO", "YT VIDEO", "YT PLAYLIST")
                or target.startswith(("http://", "https://"))
                or target.lower().endswith(_VIDEO_EXTS)
                or (bool(target) and os.path.isdir(os.path.expanduser(target)))
            )
            if is_video:
                ok, msg = apply_video(
                    mon, target, outputs_data=outputs, fit=fit, align=align,
                    media_kind=media_kind,
                    yt_delivery=str(st.get("yt_delivery") or ""),
                    video_loop=str(st.get("video_loop") or "loop"),
                    playlist_order=str(st.get("playlist_order") or "sequential"),
                    video_mute=bool(st.get("video_mute", True)),
                    video_volume=int(st.get("video_volume", 60) or 60),
                    save=False, sync_cleanup=True,
                )
            else:
                eff = rendered if (rendered and os.path.isfile(rendered)) else target
                if not eff or not os.path.isfile(os.path.expanduser(eff)):
                    if resolve_ncmatrix_bin():
                        ok, msg = apply_ncmatrix(mon, default_ncmatrix_state(), outputs_data=outputs, sync_cleanup=True, save=False)
                        if ok:
                            restored.append(mon)
                        else:
                            errors.append(f"{mon}: {msg}")
                    continue
                ok, msg = apply_image(mon, eff, fit=fit, transition=trans, bgcolor=bgcolor, align=align, save=(mode == "image"), sync_cleanup=True)
                if ok and mode == "image":
                    _restore_ascii_meta(mon, st)
        elif mode == "video":
            ok, msg = apply_video(
                mon, target, outputs_data=outputs, fit=fit, align=align,
                media_kind=media_kind,
                yt_delivery=str(st.get("yt_delivery") or ""),
                video_loop=str(st.get("video_loop") or "loop"),
                playlist_order=str(st.get("playlist_order") or "sequential"),
                video_mute=bool(st.get("video_mute", True)),
                video_volume=int(st.get("video_volume", 60) or 60),
                save=False, sync_cleanup=True,
            )
        elif mode == "solid":
            ok, msg = apply_solid(mon, st.get("hex") or target or bgcolor, save=True, sync_cleanup=True)
        elif mode == "slideshow":
            eff = rendered if (rendered and os.path.isdir(rendered)) else target
            if not eff or not os.path.isdir(os.path.expanduser(eff)):
                if resolve_ncmatrix_bin():
                    ok, msg = apply_ncmatrix(mon, default_ncmatrix_state(), outputs_data=outputs, sync_cleanup=True, save=False)
                    if ok:
                        restored.append(mon)
                    else:
                        errors.append(f"{mon}: {msg}")
                continue
            shuf_raw = st.get("shuffle", "true")
            order = str(st.get("order") or ("sequential" if shuf_raw in (False, "false") else "shuffle"))
            ok, msg = apply_slideshow(
                mon, eff, fit=fit,
                transition=str(st.get("trans") or st.get("transition") or "fade"),
                interval=str(st.get("interval") or "5m"),
                bgcolor=bgcolor,
                shuffle=(order != "sequential"),
                save=True, sync_cleanup=True,
            )
            if ok:
                _restore_ascii_meta(mon, st)
        elif mode in ("ncmatrix", "cmatrix"):
            eff_st = dict(st)
            if (eff_st.get("charset") or "katakana") == "katakana" and not has_katakana_font():
                eff_st["charset"] = "ascii"
            ok, msg = apply_ncmatrix(mon, eff_st, outputs_data=outputs, sync_cleanup=True, save=False)
        elif mode == "none":
            stop_output(mon)
            ok, msg = True, f"{mon}: stopped"
        else:
            continue

        if ok:
            restored.append(mon)
        else:
            errors.append(f"{mon}: {msg}")

    if errors:
        return False, "; ".join(errors)
    return True, f"Restored: {', '.join(restored) or 'none'}"
