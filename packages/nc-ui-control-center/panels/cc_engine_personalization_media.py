#!/usr/bin/env python3
"""NC-UI Personalization — Media, YouTube, ASCII & Staged Wallpaper Engine."""

import atexit
import base64
import colorsys
import copy
import ctypes
import fcntl
import hashlib
import json
import math
import os
import pty
import re
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import unicodedata
import urllib.request
from urllib.parse import parse_qs, urlparse
import zipfile
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib
from core import probe
try:
    from core.asciifier import (
        ASCIIFY_DEFAULTS,
        CHAR_SETS as ASCII_CHAR_SETS,
        COLOR_MODES as ASCII_COLOR_MODES,
        clear_ascii_cache,
        get_ascii_cache_stats,
        get_cached_ascii_path,
        image_cache_key,
        is_pillow_available,
        normalize_cfg as normalize_asciify_cfg,
        render_ascii_folder,
        render_ascii_image,
    )
except ImportError:
    ASCIIFY_DEFAULTS = {}
    ASCII_CHAR_SETS = {}
    ASCII_COLOR_MODES = {}
    clear_ascii_cache = lambda: None
    get_ascii_cache_stats = lambda: {"count": 0, "bytes": 0}
    get_cached_ascii_path = lambda *a, **k: ""
    image_cache_key = lambda *a, **k: ""
    is_pillow_available = lambda: False
    normalize_asciify_cfg = lambda *a, **k: {}
    render_ascii_folder = None
    render_ascii_image = None
from core import wallpaper as _wp
from core.paths import CORE_ROOT, find_bin, find_package_file
from core.config import (
    BOX_STYLES,
    DEFAULT_THEME,
    NOTIF_STYLES,
    THEME,
    _deep_merge,
    _load_toml,
    _parse_rgba_components,
    _theme_path,
    _unique_existing_paths,
    _update_toml_key_in_section,
    apply_waybar_palette,
    apply_waybar_transparency,
    derive_axis_companions,
    get_cache_dir,
    get_config_dir,
    get_layout_margins,
    get_surface_bg_hex,
    get_ui_transparency_pct,
    get_waybar_sync_color,
    get_waybar_sync_opacity,
    get_window_opacity,
    is_ncmatrix_available,
    resolve_ncmatrix_bin,
    sanitize_theme_dict,
    set_box_opacity_pct,
    set_hover_help_opacity_pct,
    set_hover_help_style,
    set_layout_margins,
    set_row_gap,
    set_surface_bg,
    set_sys_notif_opacity_pct,
    set_sys_notif_style,
    set_toast_opacity_pct,
    set_toast_style,
    set_ui_transparency_pct,
    set_waybar_sync_color,
    set_waybar_sync_opacity,
    sync_waybar_colors_from_theme,
)
from core.i18n import get_active_language, t
from core.state import STATE
from panels.cc_engine_display import get_niri_outputs_data
try:
    from panels.cc_engine_waybar import handle_cc_waybar_link, handle_cc_waybar_prompt
except ImportError:
    handle_cc_waybar_link = lambda *_a, **_k: False
    handle_cc_waybar_prompt = lambda *_a, **_k: False
from ui.canvas import Canvas
from ui.kit import (
    PALETTE,
    ActionBtn,
    Badge,
    Box,
    Divider,
    ProgressBar,
    SelectPill,
    Slider,
    Sparkline,
    StateBadge,
    Txt,
    cancel_inline_entry,
    feedback_cards,
    get_semantic_palette,
    set_feedback,
    set_inline_entry_error,
    set_slider_meta_value,
    start_inline_input,
)



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
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
_GIF_EXTS = (".gif",)
_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi")
_MEDIA_EXTS = _IMAGE_EXTS + _GIF_EXTS + _VIDEO_EXTS
_THUMB_CACHE_DIR = "/tmp/nc_wp_thumbs"
_YAZI_PICK_FILE = "/tmp/nc_yazi_pick"
_BG_FRAME_EXTRACTING: set = set()
_BG_YT_THUMBS: set = set()
_WP_USER_TOUCHED: bool = False
_YT_DL_SEQ: int = 0
_ASCII_APPLY_SEQ: int = 0
_ASCII_APPLY_STATUS: str = ""
_ASCII_PREVIEW_PENDING: set = set()
_LAST_ASCII_PREVIEW: dict[str, str] = {}
_MEDIA_DIM_CACHE: dict[tuple[str, int], tuple[int, int]] = {}
_YT_VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_PL_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_YT_PL_RAW_RE = re.compile(r"^(?:PL|UU|FL|LP|RD|OLAK5uy_)[A-Za-z0-9_-]{10,}$")
_YT_PREFIX_FILE_RE = re.compile(r"^yt_([A-Za-z0-9_-]{11})(?:-[0-9]+)?\.[A-Za-z0-9]+$")
_YT_LOCAL_FILE_RE = re.compile(r"^(?:yt_)?([A-Za-z0-9_-]{11})(?:-[0-9]+)?\.[A-Za-z0-9]+$")
_YT_LOCAL_PL_RE = re.compile(r"^yt_pl_([A-Za-z0-9_-]{10,})$")


def _is_in_yt_wall_dir(path_str: str) -> bool:
    s = str(path_str or "").strip()
    if not s:
        return False
    p = os.path.abspath(os.path.expanduser(s))
    yt_d = os.path.abspath(_base()._yt_wall_dir())
    return p == yt_d or p.startswith(yt_d + os.sep)


def _match_yt_local_vid(path_str: str) -> str:
    base = os.path.basename(str(path_str or "").strip().rstrip("/"))
    m = _YT_PREFIX_FILE_RE.match(base)
    if m:
        return m.group(1)
    if _is_in_yt_wall_dir(path_str):
        m = _YT_LOCAL_FILE_RE.match(base)
        if m:
            return m.group(1)
    return ""
_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
    "www.youtu.be",
}


def _parse_youtube_input(raw_str: str) -> dict:
    s = str(raw_str or "").strip()
    empty = {"ok": False, "video_id": "", "playlist_id": "", "is_playlist": False, "canonical_url": "", "kind": ""}
    if not s:
        return empty
    if _YT_VID_RE.match(s):
        return {
            "ok": True,
            "video_id": s,
            "playlist_id": "",
            "is_playlist": False,
            "canonical_url": f"https://www.youtube.com/watch?v={s}",
            "kind": "YT VIDEO",
        }
    if _YT_PL_RAW_RE.match(s):
        return {
            "ok": True,
            "video_id": "",
            "playlist_id": s,
            "is_playlist": True,
            "canonical_url": f"https://www.youtube.com/playlist?list={s}",
            "kind": "YT PLAYLIST",
        }
    cand = s if re.match(r"^https?://", s, re.IGNORECASE) else (f"https://{s}" if "." in s else "")
    if not cand:
        return empty
    try:
        parsed = urlparse(cand)
    except Exception:
        return empty
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        return empty
    qs = parse_qs(parsed.query)
    parts = [p for p in parsed.path.split("/") if p]
    vid = ""
    plid = (qs.get("list") or [""])[0].strip()
    if host.endswith("youtu.be"):
        if parts and _YT_VID_RE.match(parts[0]):
            vid = parts[0]
    else:
        if parsed.path.startswith("/watch") or "v" in qs:
            v_cand = (qs.get("v") or [""])[0].strip()
            if _YT_VID_RE.match(v_cand):
                vid = v_cand
        elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            if _YT_VID_RE.match(parts[1]):
                vid = parts[1]
    if plid and not _YT_PL_RE.match(plid):
        plid = ""
    is_pl = bool(plid)
    if not vid and not plid:
        return empty
    if is_pl and vid:
        canonical = f"https://www.youtube.com/watch?v={vid}&list={plid}"
    elif is_pl:
        canonical = f"https://www.youtube.com/playlist?list={plid}"
    else:
        canonical = f"https://www.youtube.com/watch?v={vid}"
    return {
        "ok": True,
        "video_id": vid,
        "playlist_id": plid,
        "is_playlist": is_pl,
        "canonical_url": canonical,
        "kind": "YT PLAYLIST" if is_pl else "YT VIDEO",
    }


def _resolve_yt_source(path_or_url: str) -> dict:
    yt = _parse_youtube_input(path_or_url)
    if yt["ok"]:
        return yt
    vid = _match_yt_local_vid(path_or_url)
    if vid:
        return _parse_youtube_input(vid)
    base = os.path.basename(str(path_or_url or "").strip().rstrip("/"))
    m_p = _YT_LOCAL_PL_RE.match(base)
    if m_p:
        return {
            "ok": True,
            "video_id": "",
            "playlist_id": m_p.group(1),
            "is_playlist": True,
            "canonical_url": f"https://www.youtube.com/playlist?list={m_p.group(1)}",
            "kind": "YT PLAYLIST",
        }
    return {"ok": False, "video_id": "", "playlist_id": "", "is_playlist": False, "canonical_url": "", "kind": ""}


def _yt_thumb_cache_path(yt: dict) -> str:
    key = yt.get("video_id") or (f"pl_{yt.get('playlist_id')}" if yt.get("playlist_id") else "")
    return os.path.join(_THUMB_CACHE_DIR, f"yt_thumb_{key}.jpg") if key else ""


def _ensure_youtube_thumbnail(yt: dict, cc_panel=None) -> str:
    if not yt or not yt.get("ok"):
        return ""
    thumb = _yt_thumb_cache_path(yt)
    if not thumb:
        return ""
    try:
        if os.path.isfile(thumb) and os.path.getsize(thumb) > 512:
            return thumb
    except OSError:
        pass
    if thumb in _BG_YT_THUMBS:
        return ""
    _BG_YT_THUMBS.add(thumb)

    def _fetch():
        try:
            os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
            img_url = ""
            if yt.get("video_id"):
                img_url = f"https://i.ytimg.com/vi/{yt['video_id']}/hqdefault.jpg"
            elif yt.get("canonical_url") and probe.has_bin("yt-dlp"):
                ok_u, out_u = probe.run_cmd(
                    ["yt-dlp", "--flat-playlist", "--playlist-items", "1", "--get-thumbnail", yt["canonical_url"]],
                    timeout=8.0,
                )
                if ok_u and out_u:
                    img_url = out_u.splitlines()[0].strip()
            if not img_url:
                return ""
            req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = resp.read()
            if data and len(data) > 512:
                tmp_t = f"{thumb}.tmp"
                with open(tmp_t, "wb") as f:
                    f.write(data)
                os.replace(tmp_t, thumb)
                return thumb
        except Exception:
            pass
        finally:
            _BG_YT_THUMBS.discard(thumb)
        return ""

    def _done(path: str):
        if path and cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_fetch, on_done=_done)
    return ""


SOLID_CLASSIC_PRESETS = (
    ("#000000", "wp_solid_preset_black"),
    ("#FFFFFF", "wp_solid_preset_white"),
    ("#2E3440", "wp_solid_preset_slate"),
    ("#008080", "wp_solid_preset_win98"),
)
SOLID_DESIGNER_PRESETS = (
    ("#002B36", "wp_solid_preset_solarized"),
    ("#1A1B26", "wp_solid_preset_tokyo"),
    ("#1D2021", "wp_solid_preset_carbon"),
)


def _resolve_solid_hex(raw: str) -> str:
    s = str(raw or "").strip().lower()
    if s in _SOLID_NAMED_COLORS:
        return _SOLID_NAMED_COLORS[s].upper()
    hx = s.lstrip("#")
    if re.match(r"^[0-9a-f]{6}$", hx):
        return f"#{hx.upper()}"
    return "#18181A"


def detect_media_kind(path: str) -> str:
    s = str(path or "").strip()
    if not s:
        return ""
    yt = _parse_youtube_input(s)
    if yt["ok"]:
        return yt["kind"]
    base = os.path.basename(s.rstrip("/"))
    if _YT_LOCAL_PL_RE.match(base):
        return "YT PLAYLIST"
    if _match_yt_local_vid(s):
        return "YT VIDEO"
    ext = os.path.splitext(s.lower())[1]
    if ext in _GIF_EXTS:
        return "GIF"
    if ext in _VIDEO_EXTS:
        return "VIDEO"
    if ext in _IMAGE_EXTS:
        return "IMAGE"
    return ""


def count_folder_media(folder_path: str) -> int:
    d = os.path.expanduser(str(folder_path or "").strip())
    if not os.path.isdir(d):
        return 0
    cnt = 0
    try:
        with os.scandir(d) as it:
            for ent in it:
                if ent.is_file() and ent.name.lower().endswith(_MEDIA_EXTS):
                    cnt += 1
    except OSError:
        pass
    return cnt


def extract_media_preview_frame(path: str, cc_panel=None) -> str:
    """Return image/GIF path directly, cached YouTube thumb, or ffmpeg video frame."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    yt = _parse_youtube_input(raw)
    if yt["ok"]:
        cached_yt = _yt_thumb_cache_path(yt)
        if cached_yt and os.path.isfile(cached_yt):
            return cached_yt
        _ensure_youtube_thumbnail(yt, cc_panel)
        return ""
    p = os.path.expanduser(raw)
    if not p or not os.path.isfile(p):
        return ""
    kind = detect_media_kind(p)
    if kind in ("IMAGE", "GIF"):
        return p
    if kind in ("VIDEO", "YT VIDEO") and probe.has_bin("ffmpeg"):
        try:
            os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
            mtime = os.path.getmtime(p)
            key = hashlib.md5(f"{p}:{mtime}".encode("utf-8")).hexdigest()[:16]
            thumb = os.path.join(_THUMB_CACHE_DIR, f"{key}.png")
            if os.path.isfile(thumb):
                return thumb
            for ts_seek in ("00:00:01", "00:00:00"):
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", ts_seek, "-i", p, "-frames:v", "1", "-vf", "scale=640:-1", thumb],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=6.0,
                )
                if os.path.isfile(thumb):
                    return thumb
        except Exception:
            pass
    return ""


def _find_media_file(target: str, current_image: str = "") -> str:
    t_str = os.path.expanduser(str(target or "").strip())
    if current_image:
        if os.path.isabs(current_image) and os.path.exists(current_image):
            return current_image
        for d in (t_str, _base().WALL_DIR, os.path.join(_base().WALL_DIR, t_str)):
            cand = os.path.join(d, current_image)
            if os.path.exists(cand):
                return cand
    if t_str:
        if os.path.isfile(t_str):
            return t_str
        w_cand = os.path.join(_base().WALL_DIR, t_str)
        if os.path.isfile(w_cand):
            return w_cand
        for d in (t_str, w_cand):
            if os.path.isdir(d):
                try:
                    for fn in sorted(os.listdir(d)):
                        if fn.lower().endswith(_MEDIA_EXTS):
                            return os.path.join(d, fn)
                except OSError:
                    pass
    return ""


def _resolve_image_candidate(target: str, current_image: str = "") -> str:
    media = _find_media_file(target, current_image)
    return extract_media_preview_frame(media) if media else ""


def extract_media_preview_frames(path: str, count: int = 6, cc_panel=None) -> list[str]:
    """Return cached multi-frame thumbnails for GIF/Video/YouTube."""
    raw = str(path or "").strip()
    if not raw:
        return []
    yt = _parse_youtube_input(raw)
    if yt["ok"]:
        t_img = extract_media_preview_frame(raw, cc_panel=cc_panel)
        return [t_img] if t_img else []
    p = os.path.expanduser(raw)
    if not p or not os.path.isfile(p):
        return []
    kind = detect_media_kind(p)
    if kind == "IMAGE":
        return [p]
    if kind not in ("GIF", "VIDEO", "YT VIDEO"):
        return []
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        mtime = 0.0
    key = hashlib.md5(f"{p}:{mtime}".encode("utf-8")).hexdigest()[:16]
    os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
    cached = [
        os.path.join(_THUMB_CACHE_DIR, f"{key}_f{i}.png")
        for i in range(count)
        if os.path.isfile(os.path.join(_THUMB_CACHE_DIR, f"{key}_f{i}.png"))
    ]
    if len(cached) >= 2:
        return cached
    if probe.has_bin("ffmpeg") and key not in _BG_FRAME_EXTRACTING:
        _BG_FRAME_EXTRACTING.add(key)
        pattern = os.path.join(_THUMB_CACHE_DIR, f"{key}_f%d.png")
        vf = "fps=6,scale=320:-1" if kind == "GIF" else "fps=2,scale=320:-1"

        def _extract():
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", p, "-vf", vf, "-frames:v", str(count), "-start_number", "0", pattern],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10.0,
                )
            except Exception:
                pass
            finally:
                _BG_FRAME_EXTRACTING.discard(key)

        probe.run_async(_extract)
    single = extract_media_preview_frame(p)
    return [single] if single else []


def is_live_15fps_enabled() -> bool:
    default_fps = (os.cpu_count() or 4) >= 6
    return bool(STATE.get("desktop", "preview_15fps", default_fps))


def _get_niri_outputs() -> dict:
    data = get_niri_outputs_data()
    if not isinstance(data, dict):
        return {}
    active = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("logical")}
    return active or data


def _target_yt_height(mon_w: int, mon_h: int) -> int:
    short_side = min(max(320, int(mon_w or 1920)), max(240, int(mon_h or 1080)))
    for tier in (720, 1080, 1440, 2160):
        if short_side <= tier + 120:
            return tier
    return 2160


def _get_monitor_resolution(mon: str, outputs_data: dict = None) -> tuple[int, int]:
    data = outputs_data if isinstance(outputs_data, dict) else _get_niri_outputs()
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


def _target_yt_height_for_monitors(mons: list[str], outputs_data: dict = None) -> int:
    return max((_target_yt_height(*_get_monitor_resolution(m, outputs_data)) for m in mons), default=1080)


def get_selected_monitors(out_names: list[str] = None) -> list[str]:
    if out_names is None:
        out_names = sorted(_get_niri_outputs().keys())
    raw = STATE.get("desktop", "selected_monitors", None)
    if raw is None or not isinstance(raw, list):
        return list(out_names)
    return [o for o in out_names if o in raw]


def get_ascii_apply_status() -> str:
    return _ASCII_APPLY_STATUS


def _norm_align(raw) -> str:
    v = str(raw or "center").strip().lower()
    return v if v in ("top", "center", "bottom") else "center"


def get_media_dimensions(path: str) -> tuple[int, int]:
    """Return cached (width, height) of a local image/GIF/video header, or (0, 0)."""
    raw = str(path or "").strip()
    if not raw or raw.startswith("http"):
        return 0, 0
    ap = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isfile(ap):
        return 0, 0
    try:
        mtime_ns = int(os.stat(ap).st_mtime_ns)
    except OSError:
        mtime_ns = 0
    ck = (ap, mtime_ns)
    if ck in _MEDIA_DIM_CACHE:
        return _MEDIA_DIM_CACHE[ck]

    w, h = 0, 0
    kind = detect_media_kind(ap)
    if kind in ("IMAGE", "GIF"):
        try:
            from PIL import Image
            with Image.open(ap) as im:
                w, h = int(im.size[0]), int(im.size[1])
        except Exception:
            try:
                _fmt, pw, ph = GdkPixbuf.Pixbuf.get_file_info(ap)
                w, h = int(pw or 0), int(ph or 0)
            except Exception:
                w, h = 0, 0
    elif kind in ("VIDEO", "YT VIDEO") and probe.has_bin("ffprobe"):
        ok_p, out_p = probe.run_cmd(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", ap],
            timeout=1.5,
        )
        if ok_p and "x" in out_p:
            try:
                ps = out_p.splitlines()[0].strip().split("x", 1)
                w, h = int(ps[0]), int(ps[1])
            except (ValueError, IndexError):
                w, h = 0, 0

    if len(_MEDIA_DIM_CACHE) > 128:
        _MEDIA_DIM_CACHE.clear()
    _MEDIA_DIM_CACHE[ck] = (w, h)
    return w, h


def should_show_scene_alignment(scene_path: str, fit: str, sel_mons: list[str], outputs_data: dict) -> bool:
    """Return True when fit is 'fill' and the local scene is proportionally taller than >=1 selected monitor."""
    if str(fit or "fill") != "fill":
        return False
    mw, mh = get_media_dimensions(scene_path)
    if mw <= 0 or mh <= 0:
        return False
    media_ar = mw / float(mh)
    mons = list(sel_mons or sorted((outputs_data or {}).keys()))
    for m in mons:
        ow, oh = _get_monitor_resolution(m, outputs_data)
        if ow > 0 and oh > 0 and media_ar < (ow / float(oh)) - 0.02:
            return True
    return False


def _cancel_pending_ascii_apply() -> None:
    global _ASCII_APPLY_SEQ, _ASCII_APPLY_STATUS
    _ASCII_APPLY_SEQ += 1
    _ASCII_APPLY_STATUS = ""


def get_editor_asciify_cfg() -> dict:
    return normalize_asciify_cfg({
        "enabled": STATE.get("desktop", "ascii_enabled", False),
        "mode": STATE.get("desktop", "ascii_mode", "color"),
        "chars": STATE.get("desktop", "ascii_chars", "standard"),
        "density": STATE.get("desktop", "ascii_density", 1.0),
        "tint": STATE.get("desktop", "ascii_tint", 0.22),
        "scale": STATE.get("desktop", "ascii_scale", 1.0),
        "contrast": STATE.get("desktop", "ascii_contrast", 1.0),
        "brightness": STATE.get("desktop", "ascii_brightness", 1.0),
        "invert": STATE.get("desktop", "ascii_invert", False),
    })


def _set_editor_asciify_cfg(cfg: dict) -> dict:
    c = normalize_asciify_cfg(cfg)
    for k in ("enabled", "mode", "chars", "density", "tint", "scale", "contrast", "brightness", "invert"):
        STATE.set("desktop", f"ascii_{k}", c[k])
    return c


def _find_static_image_file(target: str, current_image: str = "") -> str:
    t_str = os.path.expanduser(str(target or "").strip())
    if current_image:
        for cand in (
            current_image if os.path.isabs(current_image) else "",
            os.path.join(t_str, current_image) if t_str else "",
            os.path.join(_base().WALL_DIR, current_image),
        ):
            if cand and os.path.isfile(cand) and cand.lower().endswith(_IMAGE_EXTS):
                return cand
    if t_str:
        for cand in (t_str, os.path.join(_base().WALL_DIR, t_str)):
            if os.path.isfile(cand) and cand.lower().endswith(_IMAGE_EXTS):
                return cand
        for d in (t_str, os.path.join(_base().WALL_DIR, t_str)):
            if os.path.isdir(d):
                found = []
                for base, dirs, files in os.walk(d, followlinks=True):
                    rel = os.path.relpath(base, d)
                    if (0 if rel == "." else len(rel.split(os.sep))) >= 1:
                        dirs[:] = []
                    for fn in files:
                        if fn.lower().endswith(_IMAGE_EXTS):
                            found.append(os.path.join(base, fn))
                if found:
                    found.sort()
                    return found[0]
    return ""


def _ensure_ascii_preview(src_img: str, asc_cfg: dict, cc_panel=None) -> str:
    ap = os.path.abspath(os.path.expanduser(str(src_img or "")))
    cached = get_cached_ascii_path(ap, asc_cfg, preview=True) or get_cached_ascii_path(ap, asc_cfg, preview=False)
    if cached:
        _LAST_ASCII_PREVIEW[ap] = cached
        return cached
    if os.environ.get("NC_UI_OFFSCREEN") == "1":
        try:
            out_p = render_ascii_image(ap, asc_cfg, preview=True)
            if out_p:
                _LAST_ASCII_PREVIEW[ap] = out_p
            return out_p
        except Exception:
            return ""
    ck = image_cache_key(ap, asc_cfg, preview=True)
    if ck not in _ASCII_PREVIEW_PENDING:
        _ASCII_PREVIEW_PENDING.add(ck)

        def _work():
            try:
                return render_ascii_image(ap, asc_cfg, preview=True)
            except Exception:
                return ""
            finally:
                _ASCII_PREVIEW_PENDING.discard(ck)

        def _done(path: str):
            if path and os.path.isfile(path):
                _LAST_ASCII_PREVIEW[ap] = path
                if cc_panel and cc_panel.shell:
                    cc_panel.shell.refresh_view()

        probe.run_async(_work, on_done=_done)
    prev = _LAST_ASCII_PREVIEW.get(ap, "")
    return prev if (prev and os.path.isfile(prev)) else ""


def _norm_asc_diff(src: dict) -> tuple:
    a = normalize_asciify_cfg(src.get("asciify"))
    if not a["enabled"]:
        return (False,)
    return (
        True,
        a["mode"],
        a["chars"],
        a["density"],
        a["tint"],
        a["scale"],
        a["contrast"],
        a["brightness"],
        a["invert"],
    )


def _get_editor_draft_spec() -> dict:
    wp_mode = str(STATE.get("desktop", "wp_mode", "scene"))
    if wp_mode == "scene":
        path = str(STATE.get("desktop", "scene_path", "") or "")
        fit = str(STATE.get("desktop", "scene_fit", "fill") or "fill")
        kind = detect_media_kind(path)
        yt_deliv = str(STATE.get("desktop", "yt_delivery", "stream") or "stream")
        img_p = extract_media_preview_frame(path)
        frms = extract_media_preview_frames(path)
        if not img_p and os.path.isdir(path):
            first_m = _find_media_file(path)
            if first_m:
                img_p = extract_media_preview_frame(first_m)
                frms = extract_media_preview_frames(first_m)
        if not img_p and kind in ("YT VIDEO", "YT PLAYLIST"):
            yt_s = _resolve_yt_source(path)
            if yt_s.get("ok"):
                img_p = extract_media_preview_frame(yt_s["canonical_url"])
                if img_p:
                    frms = [img_p]
        return {
            "mode": "scene" if path else "none",
            "target": path,
            "fit": fit,
            "align": _norm_align(STATE.get("desktop", "scene_align", "center")),
            "image_path": img_p,
            "frames": frms,
            "media_kind": kind,
            "yt_delivery": yt_deliv,
            "video_loop": str(STATE.get("desktop", "video_loop", "loop") or "loop"),
            "playlist_order": str(STATE.get("desktop", "playlist_order", "sequential") or "sequential"),
            "video_mute": bool(STATE.get("desktop", "video_mute", True)),
            "video_volume": int(STATE.get("desktop", "video_volume", 60) or 60),
            "asciify": get_editor_asciify_cfg() if (kind == "IMAGE" or not path) else dict(ASCIIFY_DEFAULTS),
        }
    if wp_mode == "slideshow":
        folder = str(STATE.get("desktop", "slide_folder", _base().WALL_DIR) or _base().WALL_DIR)
        first_media = _find_media_file(folder)
        return {
            "mode": "slideshow",
            "target": folder,
            "fit": str(STATE.get("desktop", "scene_fit", "fill") or "fill"),
            "interval": str(STATE.get("desktop", "slide_interval", "5m") or "5m"),
            "order": str(STATE.get("desktop", "slide_order", "shuffle") or "shuffle"),
            "trans": str(STATE.get("desktop", "slide_trans", "fade") or "fade"),
            "slide_speed": str(STATE.get("desktop", "slide_speed", "1.5s") or "1.5s"),
            "image_path": extract_media_preview_frame(first_media) if first_media else "",
            "frames": extract_media_preview_frames(first_media) if first_media else [],
            "asciify": get_editor_asciify_cfg(),
        }
    if wp_mode == "solid":
        hex_col = _resolve_solid_hex(STATE.get("desktop", "solid_hex", "#000000") or "#000000")
        return {
            "mode": "solid",
            "target": hex_col,
            "hex": hex_col,
            "bgcolor": hex_col.lstrip("#").lower(),
        }
    return {
        "mode": "ncmatrix",
        "charset": str(STATE.get("desktop", "cm_charset", "katakana") or "katakana"),
        "color": str(STATE.get("desktop", "cm_color", "theme") or "theme"),
        "speed": str(STATE.get("desktop", "cm_speed", "3") or "3"),
        "size": str(STATE.get("desktop", "cm_size", "6") or "6"),
        "spawn_rate": _base()._fmt_cm_spawn(STATE.get("desktop", "cm_spawn_rate", "0.7")),
        "head_color": _base()._norm_cm_head(STATE.get("desktop", "cm_head_color", "blend")),
        "speed_variance": _base()._fmt_cm_var(STATE.get("desktop", "cm_speed_variance", "6.0")),
        "overlap_mode": _base()._norm_cm_overlap(STATE.get("desktop", "cm_overlap", "hybrid")),
        "depth_parallax": _base()._fmt_cm_parallax(STATE.get("desktop", "cm_parallax", "0.40")),
        "glow": _base()._fmt_cm_glow(STATE.get("desktop", "cm_glow", "0.80")),
        "decay_mode": _base()._norm_cm_decay(STATE.get("desktop", "cm_decay", "exponential")),
        "shimmer": _base()._norm_cm_shimmer(STATE.get("desktop", "cm_shimmer", "insane")),
        "mirror": _base()._norm_cm_mirror(STATE.get("desktop", "cm_mirror", "random")),
        "tail_length": _base()._norm_cm_tail(STATE.get("desktop", "cm_tail_len", "normal")),
        "col_density": _base()._norm_cm_density(STATE.get("desktop", "cm_col_density", "dense")),
        "cm_trans": str(STATE.get("desktop", "cm_trans", "flow") or "flow"),
    }


def _norm_spec_for_diff(src: dict) -> tuple:
    raw_mode = str(src.get("mode", "none") or "none").lower()
    mode = "ncmatrix" if raw_mode == "cmatrix" else ("scene" if raw_mode == "image" else raw_mode)
    target = str(src.get("target", "") or "").strip()
    if mode == "scene" and not target:
        mode = "none"
    if mode == "none":
        return ("none",)
    if mode == "scene":
        mk = str(src.get("media_kind") or detect_media_kind(target))
        fit_val = str(src.get("fit") or "fill")
        base = [
            "scene",
            target,
            fit_val,
        ]
        if fit_val == "fill":
            base.append(_norm_align(src.get("align", "center")))
        if mk in ("VIDEO", "YT VIDEO", "YT PLAYLIST") or target.startswith("http"):
            mute = bool(src.get("video_mute", True))
            base.extend([
                str(src.get("video_loop") or "loop"),
                mute,
                0 if mute else int(src.get("video_volume", 60) or 60),
            ])
        if mk in ("YT VIDEO", "YT PLAYLIST") or _resolve_yt_source(target).get("ok"):
            base.append(str(src.get("yt_delivery") or ("stream" if target.startswith("http") else "download")))
        if mk == "YT PLAYLIST":
            base.append(str(src.get("playlist_order") or "sequential"))
        if mk == "IMAGE":
            base.append(_norm_asc_diff(src))
        return tuple(base)
    if mode == "slideshow":
        shuf_val = str(src.get("shuffle", "true")).lower()
        order = str(src.get("order") or ("sequential" if shuf_val == "false" else "shuffle"))
        return (
            "slideshow",
            target,
            str(src.get("fit") or "fill"),
            str(src.get("interval") or "5m"),
            order,
            str(src.get("trans") or src.get("transition") or "fade"),
            str(src.get("slide_speed") or "1.5s"),
            _norm_asc_diff(src),
        )
    if mode == "ncmatrix":
        charset = str(src.get("charset") or ("katakana" if str(src.get("japanese", True)).lower() == "true" else "ascii"))
        return (
            "ncmatrix",
            charset,
            str(src.get("color") or "theme"),
            str(src.get("speed") or "3"),
            str(src.get("size") or "6"),
            _base()._fmt_cm_spawn(src.get("spawn_rate", "0.7")),
            _base()._norm_cm_head(src.get("head_color", "blend")),
            _base()._fmt_cm_var(src.get("speed_variance", "6.0")),
            _base()._norm_cm_overlap(src.get("overlap_mode", "hybrid")),
            _base()._fmt_cm_parallax(src.get("depth_parallax", "0.40")),
            _base()._fmt_cm_glow(src.get("glow", "0.80")),
            _base()._norm_cm_decay(src.get("decay_mode", "exponential")),
            _base()._norm_cm_shimmer(src.get("shimmer", "insane")),
            _base()._norm_cm_mirror(src.get("mirror", "random")),
            _base()._norm_cm_tail(src.get("tail_length", "normal")),
            _base()._norm_cm_density(src.get("col_density", "dense")),
            str(src.get("cm_trans") or src.get("transition") or "flow"),
        )
    if mode == "solid":
        return ("solid", _resolve_solid_hex(src.get("hex") or target))
    return (mode, target)


def _specs_differ(spec_a: dict, spec_b: dict) -> bool:
    return _norm_spec_for_diff(spec_a or {}) != _norm_spec_for_diff(spec_b or {})


def _read_live_src(out_name: str, live_cache: dict = None) -> dict:
    if live_cache is None:
        live_cache = STATE.get("desktop", "out_live", {})
        live_cache = live_cache if isinstance(live_cache, dict) else {}
    disk_file = os.path.join(_base().NC_DESKTOP_BASE, "state", f"{out_name}.json")
    disk_mtime = 0.0
    disk_st = {}
    if os.path.exists(disk_file):
        try:
            disk_mtime = os.path.getmtime(disk_file)
            with open(disk_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                disk_st = loaded
        except Exception:
            disk_st = {}
    ov = live_cache.get(out_name) if isinstance(live_cache.get(out_name), dict) else {}
    ov_ts = float(ov.get("ts", 0.0) or 0.0) if ov else 0.0
    if disk_st.get("source_target"):
        disk_st = dict(disk_st)
        disk_st["rendered_target"] = str(disk_st.get("rendered_target") or disk_st.get("target") or "")
        disk_st["target"] = str(disk_st["source_target"])
        disk_st["asciify"] = normalize_asciify_cfg(disk_st.get("asciify"))
    elif (not disk_st.get("source_target")) and ov.get("rendered_target") and disk_st.get("target") == ov.get("rendered_target"):
        disk_st = dict(disk_st)
        disk_st["source_target"] = str(ov.get("source_target") or ov.get("target") or "")
        disk_st["rendered_target"] = str(ov.get("rendered_target") or "")
        disk_st["target"] = str(disk_st["source_target"])
        disk_st["asciify"] = normalize_asciify_cfg(ov.get("asciify"))
    return ov if (ov and ov_ts >= disk_mtime) else (disk_st or ov)


def _stage_current_to_selected(fast_live: bool = False) -> None:
    outs_data = _get_niri_outputs()
    outs = sorted(outs_data.keys())
    sel = get_selected_monitors(outs)
    staged = {
        k: v
        for k, v in (STATE.get("desktop", "staged_preview", {}) or {}).items()
        if k in sel
    }
    if not sel:
        STATE.set("desktop", "staged_preview", staged, immediate=(not fast_live))
        return
    live_cache = STATE.get("desktop", "out_live", {})
    live_cache = dict(live_cache) if isinstance(live_cache, dict) else {}
    spec = _get_editor_draft_spec()
    now_ts = time.time()
    live_mutated = False
    for out_n in sel:
        live_src = _read_live_src(out_n, live_cache)
        live_m = str(live_src.get("mode", "none") or "none").lower()
        if live_m == "cmatrix":
            live_m = "ncmatrix"
        if spec.get("mode") == "ncmatrix" and live_m == "ncmatrix":
            if _specs_differ(spec, live_src):
                _apply_single_non_ascii_monitor(out_n, spec, outs_data, live_cache, defer_json=fast_live)
                ent = dict(spec)
                ent["ts"] = now_ts
                live_cache[out_n] = ent
                live_mutated = True
            staged.pop(out_n, None)
        elif _specs_differ(spec, live_src):
            ent = dict(spec)
            ent["ts"] = now_ts
            staged[out_n] = ent
        else:
            staged.pop(out_n, None)
    if live_mutated:
        STATE.set("desktop", "out_live", live_cache, immediate=(not fast_live))
    STATE.set("desktop", "staged_preview", staged, immediate=(not fast_live))


def get_desktop_preview_state(outputs_data: dict, cc_panel=None) -> dict:
    """Resolve current [LIVE] or staged [PREVIEW *] desktop state per monitor."""
    staged_map = STATE.get("desktop", "staged_preview", {})
    staged_map = staged_map if isinstance(staged_map, dict) else {}
    live_cache = STATE.get("desktop", "out_live", {})
    live_cache = live_cache if isinstance(live_cache, dict) else {}

    result = {}
    for out_name in outputs_data:
        live_src = _read_live_src(out_name, live_cache)
        raw_staged = staged_map.get(out_name) if isinstance(staged_map.get(out_name), dict) else None
        is_staged = bool(raw_staged and _specs_differ(raw_staged, live_src))
        src = raw_staged if is_staged else live_src

        raw_mode = str(src.get("mode", "none") or "none").lower()
        mode = "ncmatrix" if raw_mode == "cmatrix" else ("scene" if raw_mode == "image" else raw_mode)
        target = str(src.get("target", "") or "").strip()
        cur_img = str(src.get("current_image", "") or "")
        if mode == "scene" and not target and not cur_img and not str(src.get("image_path", "") or ""):
            mode = "none"

        img_path, frames = "", []
        media_kind = str(detect_media_kind(target) or src.get("media_kind") or "")
        asc = normalize_asciify_cfg(src.get("asciify"))
        if mode in ("scene", "slideshow"):
            yt_src = _resolve_yt_source(target) if mode == "scene" else {"ok": False}
            if yt_src["ok"] and (target.startswith("http") or not os.path.exists(os.path.expanduser(target))):
                cand_img = str(src.get("image_path", "") or "")
                img_path = cand_img if (cand_img and os.path.isfile(cand_img)) else extract_media_preview_frame(yt_src["canonical_url"])
                frames = [img_p for img_p in (img_path,) if img_p]
            else:
                media_src = _find_media_file(target, cur_img)
                cand_img = str(src.get("image_path", "") or "")
                img_path = cand_img if (cand_img and os.path.isfile(cand_img)) else (extract_media_preview_frame(media_src) if media_src else "")
                if media_src:
                    frames = extract_media_preview_frames(media_src)
                if not frames and img_path:
                    frames = [img_path]

            if asc["enabled"] and is_pillow_available():
                cur_rendered = ""
                if not is_staged and mode == "slideshow" and src.get("rendered_target") and cur_img:
                    cand_r = os.path.join(str(src["rendered_target"]), cur_img)
                    if os.path.isfile(cand_r):
                        cur_rendered = cand_r
                if cur_rendered:
                    img_path = cur_rendered
                    frames = [cur_rendered]
                else:
                    prev_src = _find_static_image_file(target, cur_img) if (mode == "slideshow" or media_kind == "IMAGE") else ""
                    if prev_src:
                        asc_prev = _ensure_ascii_preview(prev_src, asc, cc_panel=cc_panel)
                        img_path = asc_prev
                        frames = [asc_prev] if asc_prev else []

        hex_col = _resolve_solid_hex(src.get("hex") or target) if mode == "solid" else ""
        charset = str(src.get("charset") or ("katakana" if str(src.get("japanese", True)).lower() == "true" else "ascii"))
        shuf_val = str(src.get("shuffle", "true")).lower()
        order = str(src.get("order") or ("sequential" if shuf_val == "false" else "shuffle"))
        yt_deliv = str(src.get("yt_delivery") or ("stream" if target.startswith("http") else "download"))
        result[out_name] = {
            "mode": mode,
            "staged": is_staged,
            "target": target,
            "fit": str(src.get("fit") or "fill"),
            "align": _norm_align(src.get("align", "center")),
            "image_path": img_path,
            "frames": frames,
            "media_kind": media_kind,
            "yt_delivery": yt_deliv,
            "video_loop": str(src.get("video_loop") or "loop"),
            "playlist_order": str(src.get("playlist_order") or "sequential"),
            "hex": hex_col,
            "color": str(src.get("color") or "theme"),
            "speed": str(src.get("speed") or "3"),
            "size": str(src.get("size") or "6"),
            "spawn_rate": _base()._fmt_cm_spawn(src.get("spawn_rate", "0.7")),
            "head_color": _base()._norm_cm_head(src.get("head_color", "blend")),
            "speed_variance": _base()._fmt_cm_var(src.get("speed_variance", "6.0")),
            "overlap_mode": _base()._norm_cm_overlap(src.get("overlap_mode", "hybrid")),
            "depth_parallax": _base()._fmt_cm_parallax(src.get("depth_parallax", "0.40")),
            "glow": _base()._fmt_cm_glow(src.get("glow", "0.80")),
            "decay_mode": _base()._norm_cm_decay(src.get("decay_mode", "exponential")),
            "shimmer": _base()._norm_cm_shimmer(src.get("shimmer", "insane")),
            "mirror": _base()._norm_cm_mirror(src.get("mirror", "random")),
            "tail_length": _base()._norm_cm_tail(src.get("tail_length", "normal")),
            "col_density": _base()._norm_cm_density(src.get("col_density", "dense")),
            "cm_trans": str(src.get("cm_trans") or src.get("transition") or "flow"),
            "charset": charset,
            "interval": str(src.get("interval") or "5m"),
            "order": order,
            "trans": str(src.get("trans") or src.get("transition") or "fade"),
            "slide_speed": str(src.get("slide_speed") or "1.5s"),
            "video_mute": bool(src.get("video_mute", True)),
            "video_volume": int(src.get("video_volume", 60) or 60),
            "asciify": asc,
        }
    return result


def sync_editor_from_live_monitors(outputs_data: dict = None) -> None:
    """Sync Card 2 operating mode & options to selected monitor's preview/live state."""
    if _WP_USER_TOUCHED:
        return
    if outputs_data is None:
        outputs_data = _get_niri_outputs()
    outs = sorted(outputs_data.keys())
    if not outs:
        return
    live_map = get_desktop_preview_state(outputs_data)
    sel = get_selected_monitors(outs)
    cands = [m for m in sel if m in live_map and live_map[m].get("mode") in ("scene", "slideshow", "ncmatrix", "cmatrix", "solid")]
    if not cands:
        cands = [m for m in outs if m in live_map and live_map[m].get("mode") in ("scene", "slideshow", "ncmatrix", "cmatrix", "solid")]
    if not cands:
        return
    st = live_map[cands[0]]
    m = "ncmatrix" if st["mode"] == "cmatrix" else st["mode"]
    updates = {"wp_mode": m}
    if m == "scene":
        if st.get("target"):
            updates["scene_path"] = st["target"]
        updates["scene_fit"] = st.get("fit") or "fill"
        updates["scene_align"] = _norm_align(st.get("align", "center"))
        updates["yt_delivery"] = st.get("yt_delivery") or ("stream" if str(st.get("target", "")).startswith("http") else "download")
        updates["video_loop"] = st.get("video_loop") or "loop"
        updates["playlist_order"] = st.get("playlist_order") or "sequential"
        updates["video_mute"] = bool(st.get("video_mute", True))
        updates["video_volume"] = int(st.get("video_volume", 60) or 60)
        asc = normalize_asciify_cfg(st.get("asciify"))
        for ak in ("enabled", "mode", "chars", "density", "tint", "scale", "contrast", "brightness", "invert"):
            updates[f"ascii_{ak}"] = asc[ak]
    elif m == "slideshow":
        if st.get("target"):
            updates["slide_folder"] = st["target"]
        updates["slide_interval"] = st.get("interval") or "5m"
        updates["slide_order"] = st.get("order") or "shuffle"
        updates["slide_trans"] = st.get("trans") or "fade"
        updates["slide_speed"] = st.get("slide_speed") or "1.5s"
        asc = normalize_asciify_cfg(st.get("asciify"))
        for ak in ("enabled", "mode", "chars", "density", "tint", "scale", "contrast", "brightness", "invert"):
            updates[f"ascii_{ak}"] = asc[ak]
    elif m == "solid":
        updates["solid_hex"] = _resolve_solid_hex(st.get("hex") or st.get("target") or "#000000")
    elif m == "ncmatrix":
        updates["cm_charset"] = st.get("charset") or "katakana"
        updates["cm_color"] = st.get("color") or "theme"
        updates["cm_speed"] = str(st.get("speed") or "3")
        updates["cm_size"] = str(st.get("size") or "6")
        updates["cm_spawn_rate"] = _base()._fmt_cm_spawn(st.get("spawn_rate", "0.7"))
        updates["cm_head_color"] = _base()._norm_cm_head(st.get("head_color", "blend"))
        updates["cm_speed_variance"] = _base()._fmt_cm_var(st.get("speed_variance", "6.0"))
        updates["cm_overlap"] = _base()._norm_cm_overlap(st.get("overlap_mode", "hybrid"))
        updates["cm_parallax"] = _base()._fmt_cm_parallax(st.get("depth_parallax", "0.40"))
        updates["cm_glow"] = _base()._fmt_cm_glow(st.get("glow", "0.80"))
        updates["cm_decay"] = _base()._norm_cm_decay(st.get("decay_mode", "exponential"))
        updates["cm_shimmer"] = _base()._norm_cm_shimmer(st.get("shimmer", "insane"))
        updates["cm_mirror"] = _base()._norm_cm_mirror(st.get("mirror", "random"))
        updates["cm_tail_len"] = _base()._norm_cm_tail(st.get("tail_length", "normal"))
        updates["cm_col_density"] = _base()._norm_cm_density(st.get("col_density", "dense"))
        updates["cm_trans"] = str(st.get("cm_trans") or "flow")

    ns = STATE.get_namespace("desktop")
    for k, v in updates.items():
        if ns.get(k) != v:
            STATE.set("desktop", k, v)


def _cancel_pending_yt_dl() -> None:
    global _YT_DL_SEQ
    _YT_DL_SEQ += 1
    if STATE.get("desktop", "yt_status"):
        STATE.set("desktop", "yt_status", "")


def _wallpaper_pid_files(mon: str) -> tuple[str, ...]:
    return _wp.wallpaper_pid_files(mon)


def _collect_output_wallpaper_pids(mon: str) -> list[int]:
    return _wp.collect_output_pids(mon)


def _get_running_ncmatrix_pid(mon: str) -> int:
    return _wp.get_running_ncmatrix_pid(mon)


_NCMATRIX_CPU_SAMPLES: dict[int, tuple[int, float]] = {}
_NCMATRIX_CPU_PCT: dict[int, float] = {}


def reset_ncmatrix_cpu_telemetry() -> None:
    """Clear cached /proc CPU baselines when leaving the Wallpaper view."""
    _NCMATRIX_CPU_SAMPLES.clear()
    _NCMATRIX_CPU_PCT.clear()


def _read_proc_stat_ticks(pid: int) -> tuple[int, int] | None:
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as sf:
            raw = sf.read()
        parts = raw[raw.rfind(")") + 2:].split()
        if len(parts) <= 19:
            return None
        return int(parts[11]) + int(parts[12]), int(parts[19])
    except Exception:
        return None


def get_ncmatrix_live_cpu_usage(monitors: list[str]) -> dict:
    """Measure live per-core and total-system CPU% of active `nc-matrix` instances via /proc."""
    active_pids: dict[int, str] = {}
    active_mons: list[str] = []
    for mon in monitors or []:
        pid = _get_running_ncmatrix_pid(mon)
        if pid > 0 and pid not in active_pids:
            active_pids[pid] = mon
            active_mons.append(mon)

    for stale in [p for p in _NCMATRIX_CPU_SAMPLES if p not in active_pids]:
        _NCMATRIX_CPU_SAMPLES.pop(stale, None)
        _NCMATRIX_CPU_PCT.pop(stale, None)

    if not active_pids:
        return {"running": False, "monitors": [], "pids": [], "core_pct": 0.0, "sys_pct": 0.0, "count": 0}

    clk = os.sysconf("SC_CLK_TCK") or 100
    ncpu = os.cpu_count() or 1
    now = time.monotonic()
    uptime: float | None = None
    total_core_pct = 0.0
    alive_pids: list[int] = []
    alive_mons: list[str] = []

    for pid, mon in active_pids.items():
        stat_vals = _read_proc_stat_ticks(pid)
        if stat_vals is None:
            _NCMATRIX_CPU_SAMPLES.pop(pid, None)
            _NCMATRIX_CPU_PCT.pop(pid, None)
            continue
        tot, starttime = stat_vals
        prev = _NCMATRIX_CPU_SAMPLES.get(pid)
        if prev is None:
            _NCMATRIX_CPU_SAMPLES[pid] = (tot, now)
            if uptime is None:
                try:
                    with open("/proc/uptime", "r", encoding="utf-8") as uf:
                        uptime = float(uf.read().split()[0])
                except Exception:
                    uptime = 0.0
            elapsed = max(0.1, uptime - (starttime / clk)) if uptime > 0 else 1.0
            pct = max(0.0, ((tot / clk) / elapsed) * 100.0)
            _NCMATRIX_CPU_PCT[pid] = pct
        else:
            prev_tot, prev_mono = prev
            dt = now - prev_mono
            if dt >= 1.5:
                pct = max(0.0, ((tot - prev_tot) / clk) / dt * 100.0)
                _NCMATRIX_CPU_SAMPLES[pid] = (tot, now)
                _NCMATRIX_CPU_PCT[pid] = pct
            else:
                pct = _NCMATRIX_CPU_PCT.get(pid, 0.0)
        total_core_pct += pct
        alive_pids.append(pid)
        alive_mons.append(mon)

    if not alive_pids:
        return {"running": False, "monitors": [], "pids": [], "core_pct": 0.0, "sys_pct": 0.0, "count": 0}

    return {
        "running": True,
        "monitors": alive_mons,
        "pids": alive_pids,
        "core_pct": total_core_pct,
        "sys_pct": total_core_pct / ncpu,
        "count": len(alive_pids),
    }


def _spawn_bg_pid(cmd: list) -> int:
    return _wp.spawn_bg_pid(cmd)


def _kill_pids_after_overlap(old_pids: list[int], new_pid: int = 0, delay: float = 0.30, remove_pf: tuple[str, ...] = ()) -> None:
    _wp.kill_pids_after_overlap(old_pids, new_pid=new_pid, delay=delay, remove_pf=remove_pf)


def _stop_mpvpaper(mon: str) -> None:
    _wp.stop_output(mon)


def _update_live_video_audio_if_running(mons: list[str], mute: bool = None, volume: int = None) -> bool:
    """Update live video audio via IPC immediately when video wallpaper is running on target monitors."""
    if not mons:
        return False
    live = dict(STATE.get("desktop", "out_live", {}) or {})
    staged = dict(STATE.get("desktop", "staged_preview", {}) or {})
    cur_target = str(STATE.get("desktop", "scene_path", "") or "").strip()
    cur_mode = str(STATE.get("desktop", "wp_mode", "scene") or "scene")
    use_mpv = probe.has_bin("mpvpaper")
    updated_any = False

    for mon in mons:
        sock_path = f"/tmp/nc_mpvpaper_{mon}.sock"
        if not os.path.exists(sock_path):
            continue
        live_src = _read_live_src(mon, live)
        is_same_target = (
            cur_mode == "scene"
            and live_src.get("mode") in ("scene", "image")
            and str(live_src.get("target", "") or "").strip() == cur_target
            and not staged.get(mon)
        )
        sent_mute = False
        if mute is True:
            sent_mute = _send_mpv_ipc(mon, ["set_property", "mute", "yes"] if use_mpv else ["set_mute", True])
            if probe.has_bin("pkill"):
                if not sent_mute:
                    for pat in (
                        f"^([^ ]*/)?ffplay .*-window_title nc-vidwp-audio-{mon}( |$)",
                        f"^([^ ]*/)?pacat .*nc-vidwp-audio-{mon}( |$)",
                    ):
                        subprocess.run(["pkill", "-9", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-9", "-f", "^([^ ]*/)?ffplay -nodisp -vn -loglevel quiet -autoexit "], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not is_same_target:
            continue

        sent = False
        if mute is not None:
            sent = sent_mute if mute is True else _send_mpv_ipc(mon, ["set_property", "mute", "no"] if use_mpv else ["set_mute", False])
        elif volume is not None:
            v_clamped = max(0, min(100, int(volume)))
            sent = _send_mpv_ipc(mon, ["set_property", "volume", v_clamped] if use_mpv else ["set_volume", v_clamped])

        if sent:
            ent = dict(live_src)
            if mute is not None:
                ent["video_mute"] = bool(mute)
            if volume is not None:
                ent["video_volume"] = max(0, min(100, int(volume)))
            _save_desktop_json_state(mon, {
                "output": mon,
                "mode": "scene",
                "target": ent.get("target", cur_target),
                "fit": ent.get("fit", "fill"),
                "media_kind": ent.get("media_kind") or detect_media_kind(ent.get("target", cur_target)),
                "yt_delivery": ent.get("yt_delivery", "stream"),
                "video_loop": ent.get("video_loop", "loop"),
                "playlist_order": ent.get("playlist_order", "sequential"),
                "video_mute": bool(ent.get("video_mute", True)),
                "video_volume": int(ent.get("video_volume", 60) or 60),
            })
            ent["ts"] = time.time()
            live[mon] = ent
            updated_any = True

    if updated_any:
        STATE.set("desktop", "out_live", live)
    return updated_any


def _send_mpv_ipc(mon: str, cmd_args: list) -> bool:
    return _wp.send_mpv_ipc(mon, cmd_args)


def _read_clipboard_text() -> str:
    if probe.has_bin("wl-paste"):
        ok, out = probe.run_cmd(["wl-paste", "-n"], timeout=1.5)
        if ok and out:
            return out.strip()
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk
        txt = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()
        if txt:
            return txt.strip()
    except Exception:
        pass
    return ""


def _find_cached_yt_media(yt: dict) -> str:
    if not yt or not yt.get("ok"):
        return ""
    yt_w = _base()._yt_wall_dir()
    if yt.get("is_playlist") and yt.get("playlist_id"):
        pl_dir = os.path.join(yt_w, f"yt_pl_{yt['playlist_id']}")
        if os.path.isdir(pl_dir) and count_folder_media(pl_dir) > 0:
            return pl_dir
        return ""
    vid = yt.get("video_id") or ""
    if vid and os.path.isdir(yt_w):
        for ext in _VIDEO_EXTS:
            cand = os.path.join(yt_w, f"yt_{vid}{ext}")
            if os.path.isfile(cand):
                return cand
        try:
            for fn in sorted(os.listdir(yt_w)):
                fp = os.path.join(yt_w, fn)
                if os.path.isfile(fp) and fn.lower().endswith(_VIDEO_EXTS) and _match_yt_local_vid(fp) == vid:
                    return fp
        except OSError:
            pass
    return ""


def _save_desktop_json_state(mon: str, payload: dict) -> None:
    _wp.save_output_state(mon, payload)


def _start_youtube_download(cc_panel, url: str, yt_info: dict = None) -> None:
    yt = yt_info if (yt_info and yt_info.get("ok")) else _parse_youtube_input(url)
    target_url = yt["canonical_url"] if yt["ok"] else str(url or "").strip()
    if not re.match(r"^https?://\S+$", target_url, re.IGNORECASE):
        _base()._set_pers_fb(False, t("cc_personalization.wp_yt_invalid"))
        return
    _cancel_pending_yt_dl()
    dl_seq = _YT_DL_SEQ
    yt_w = _base()._yt_wall_dir()
    os.makedirs(yt_w, exist_ok=True)
    is_pl = bool(yt.get("is_playlist"))
    outs_data = _get_niri_outputs()
    sel_mons = get_selected_monitors(sorted(outs_data.keys()))
    target_h = _target_yt_height_for_monitors(sel_mons or list(outs_data.keys()), outs_data)
    STATE.set("desktop", "yt_status", "downloading")
    STATE.set("desktop", "yt_url", target_url)
    start_ts = time.time()

    def _download():
        if is_pl and yt.get("playlist_id"):
            pl_dir = os.path.join(yt_w, f"yt_pl_{yt['playlist_id']}")
            os.makedirs(pl_dir, exist_ok=True)
            cmd = [
                "yt-dlp",
                "--yes-playlist",
                "--ignore-errors",
                "-S",
                f"res:{target_h},fps",
                "-f",
                f"bv*[height<={target_h}]+ba/b[height<={target_h}]/b",
                "--merge-output-format",
                "mp4",
                "-o",
                os.path.join(pl_dir, "%(playlist_index)03d_%(id)s.%(ext)s"),
                target_url,
            ]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
                cnt = count_folder_media(pl_dir)
                err_msg = (res.stderr or "").strip().splitlines()[-1] if res.stderr else "exit != 0"
                return (cnt > 0, pl_dir if cnt > 0 else "", err_msg)
            except Exception as e:
                return (False, "", str(e))

        cmd = [
            "yt-dlp",
            "--no-playlist",
            "-S",
            f"res:{target_h},fps",
            "-f",
            f"bv*[height<={target_h}]+ba/b[height<={target_h}]/b",
            "--merge-output-format",
            "mp4",
            "--print",
            "after_move:filepath",
            "-o",
            os.path.join(yt_w, "yt_%(id)s.%(ext)s"),
            target_url,
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
            lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
            out_path = next((ln for ln in reversed(lines) if os.path.isfile(ln)), "")
            if not out_path and os.path.isdir(yt_w):
                cands = [
                    os.path.join(yt_w, fn)
                    for fn in os.listdir(yt_w)
                    if fn.lower().endswith(_VIDEO_EXTS)
                ]
                recent = [c for c in cands if os.path.getmtime(c) >= (start_ts - 2.0)]
                if recent:
                    out_path = max(recent, key=os.path.getmtime)
            return (res.returncode == 0 and bool(out_path), out_path, (res.stderr or "").strip().splitlines()[-1] if res.stderr else "exit != 0")
        except Exception as e:
            return (False, "", str(e))

    def _done(res):
        global _WP_USER_TOUCHED
        if dl_seq != _YT_DL_SEQ:
            return
        ok, path, err = res
        STATE.set("desktop", "yt_status", "")
        if ok and path and os.path.exists(path):
            STATE.set("desktop", "wp_mode", "scene")
            STATE.set("desktop", "scene_path", path)
            STATE.set("desktop", "yt_delivery", "download")
            _WP_USER_TOUCHED = True
            _stage_current_to_selected()
            _base()._set_pers_fb(True, t("cc_personalization.wp_yt_done", p0=os.path.basename(path.rstrip("/")) or path))
        else:
            _base()._set_pers_fb(False, t("cc_personalization.wp_yt_failed", p0=err[:60]))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_download, on_done=_done)


def _handle_youtube_input(cc_panel, raw_input: str, from_clipboard: bool = False) -> bool:
    global _WP_USER_TOUCHED
    _cancel_pending_yt_dl()
    yt = _parse_youtube_input(raw_input)
    if not yt["ok"]:
        if from_clipboard:
            _base()._set_pers_fb(False, t("cc_personalization.wp_yt_clip_invalid"))
            return False
        if not re.match(r"^https?://\S+$", str(raw_input or "").strip(), re.IGNORECASE):
            _base()._set_pers_fb(False, t("cc_personalization.wp_yt_invalid"))
            return False
        canon = str(raw_input or "").strip()
        STATE.set("desktop", "wp_mode", "scene")
        STATE.set("desktop", "scene_path", canon)
        STATE.set("desktop", "yt_url", canon)
        STATE.set("desktop", "yt_delivery", "stream")
        _WP_USER_TOUCHED = True
        _stage_current_to_selected()
        _base()._set_pers_fb(True, t("cc_personalization.wp_yt_stream_staged", p0=canon))
        return True

    canon = yt["canonical_url"]
    STATE.set("desktop", "yt_url", canon)
    _ensure_youtube_thumbnail(yt, cc_panel)
    deliv = str(STATE.get("desktop", "yt_delivery", "stream") or "stream")
    if deliv == "stream" or not probe.has_bin("yt-dlp"):
        STATE.set("desktop", "wp_mode", "scene")
        STATE.set("desktop", "scene_path", canon)
        STATE.set("desktop", "yt_delivery", "stream")
        _WP_USER_TOUCHED = True
        _stage_current_to_selected()
        _base()._set_pers_fb(True, t("cc_personalization.wp_yt_stream_staged", p0=canon))
        return True

    cached = _find_cached_yt_media(yt)
    if cached:
        STATE.set("desktop", "wp_mode", "scene")
        STATE.set("desktop", "scene_path", cached)
        _WP_USER_TOUCHED = True
        _stage_current_to_selected()
        _base()._set_pers_fb(True, t("cc_personalization.wp_yt_done", p0=os.path.basename(cached.rstrip("/")) or cached))
        return True

    _start_youtube_download(cc_panel, canon, yt_info=yt)
    return True


def _merge_desktop_json_meta(mon: str, extra: dict) -> None:
    _wp.merge_output_state(mon, extra)


def _spec_needs_asciify(spec: dict) -> bool:
    if not isinstance(spec, dict):
        return False
    m = spec.get("mode", "")
    target = str(spec.get("target", "") or "")
    if not target:
        return False
    asc = normalize_asciify_cfg(spec.get("asciify"))
    if not asc["enabled"]:
        return False
    if m == "slideshow":
        return True
    if m in ("scene", "image"):
        return (spec.get("media_kind") or detect_media_kind(target)) == "IMAGE"
    return False


def _cleanup_orphan_monitors(outs: list[str], live: dict) -> None:
    st_dir = os.path.join(_base().NC_DESKTOP_BASE, "state")
    pid_dir = os.path.join(_base().NC_DESKTOP_BASE, "pids")
    orphan_outs = set()
    for d, ext in ((st_dir, ".json"), (pid_dir, ".pid")):
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.endswith(ext):
                    base = fn[:-len(ext)]
                    mon_name = base.split("_", 1)[1] if "_" in base else base
                    if mon_name and mon_name not in outs:
                        orphan_outs.add(mon_name)
    for orph in orphan_outs:
        _stop_mpvpaper(orph)
        try:
            os.remove(os.path.join(st_dir, f"{orph}.json"))
        except OSError:
            pass
        live.pop(orph, None)


def _apply_single_non_ascii_monitor(mon: str, spec: dict, outs_data: dict, live: dict, defer_json: bool = False) -> tuple[bool, float, str]:
    """Apply a single non-ASCIIfied monitor wallpaper spec; returns (ok, async_grace, err_msg)."""
    m_kind = spec.get("mode", "scene")
    if m_kind == "scene" and spec.get("target"):
        target_p = spec["target"]
        media_kind = spec.get("media_kind") or detect_media_kind(target_p)
        is_video_or_yt = media_kind in ("VIDEO", "YT VIDEO", "YT PLAYLIST") or target_p.startswith("http")
        video_bin = find_bin("nc-video-wallpaper")
        if is_video_or_yt and (probe.has_bin("mpvpaper") or os.path.isfile(video_bin)):
            ok, err = _wp.apply_video(
                mon,
                target_p,
                outputs_data=outs_data,
                fit=str(spec.get("fit") or "fill"),
                align=_norm_align(spec.get("align", "center")),
                media_kind=media_kind,
                yt_delivery=str(spec.get("yt_delivery") or ("stream" if target_p.startswith("http") else "download")),
                video_loop=str(spec.get("video_loop") or "loop"),
                playlist_order=str(spec.get("playlist_order") or "sequential"),
                video_mute=bool(spec.get("video_mute", True)),
                video_volume=int(spec.get("video_volume", 60) or 60),
            )
            return ok, 0.0, ("" if ok else err)
        ok, err = _wp.apply_image(mon, target_p, fit=spec.get("fit", "fill"), align=_norm_align(spec.get("align", "center")))
        return ok, 0.0, ("" if ok else err)
    if m_kind == "none" or (m_kind == "scene" and not spec.get("target")):
        _wp.stop_output(mon)
        _wp.save_output_state(mon, {"output": mon, "mode": "none"})
        return True, 0.0, ""
    if m_kind == "slideshow" and spec.get("target"):
        ok, err = _wp.apply_slideshow(
            mon,
            spec["target"],
            fit=spec.get("fit", "fill"),
            transition=spec.get("trans", "fade"),
            interval=spec.get("interval", "5m"),
            shuffle=(spec.get("order") != "sequential"),
        )
        return ok, 0.0, ("" if ok else err)
    if m_kind == "solid":
        hex_val = _resolve_solid_hex(spec.get("hex") or spec.get("target") or "#000000")
        ok, err = _wp.apply_solid(mon, hex_val)
        return ok, 0.0, ("" if ok else err)
    if m_kind in ("ncmatrix", "cmatrix"):
        prev_live = _read_live_src(mon, live)
        prev_is_ncm = str(prev_live.get("mode") or "").lower() in ("ncmatrix", "cmatrix")
        prev_size = str(prev_live.get("size") or "9")
        ok, err = _wp.apply_ncmatrix(
            mon,
            spec,
            outputs_data=outs_data,
            prev_is_ncm=prev_is_ncm,
            prev_size=prev_size,
            defer_json=defer_json,
        )
        if not ok and err == "nc-matrix missing":
            return False, 0.0, t("cc_personalization.wall_tool_missing", p0="nc-matrix")
        return ok, 0.0, ("" if ok else err)
    return True, 0.0, ""


def _apply_staged_wallpapers() -> tuple[bool, str]:
    global _WP_USER_TOUCHED
    outs_data = _get_niri_outputs()
    outs = sorted(outs_data.keys())
    staged = dict(STATE.get("desktop", "staged_preview", {}) or {})
    if not staged:
        sel = get_selected_monitors(outs)
        if not sel:
            return False, t("cc_personalization.wp_no_selection")
        spec = _get_editor_draft_spec()
        staged = {m: spec for m in sel}

    live = dict(STATE.get("desktop", "out_live", {}) or {})
    _cleanup_orphan_monitors(outs, live)

    applied_mons = []
    modes_used = set()
    for mon, spec in staged.items():
        if not isinstance(spec, dict):
            continue
        modes_used.add(spec.get("mode", "scene"))
        ok, async_grace, err = _apply_single_non_ascii_monitor(mon, spec, outs_data, live)
        if not ok:
            return False, err
        ent = dict(spec)
        ent["ts"] = time.time() + async_grace
        live[mon] = ent
        applied_mons.append(mon)

    STATE.set("desktop", "out_live", live)
    STATE.set("desktop", "staged_preview", {})
    _WP_USER_TOUCHED = False
    if not applied_mons:
        return False, t("cc_personalization.wp_no_selection")
    return True, t("cc_personalization.wp_applied_msg", p0=", ".join(sorted(modes_used)), p1=", ".join(applied_mons))


def _start_apply_staged_wallpapers(cc_panel, staged_input: dict = None) -> None:
    global _ASCII_APPLY_SEQ, _ASCII_APPLY_STATUS
    if not is_pillow_available():
        _base()._set_pers_fb(False, t("cc_personalization.wp_ascii_pillow_missing"))
        return

    outs_data = _get_niri_outputs()
    outs = sorted(outs_data.keys())
    staged = dict(staged_input if staged_input is not None else (STATE.get("desktop", "staged_preview", {}) or {}))
    if not staged:
        sel = get_selected_monitors(outs)
        if not sel:
            _base()._set_pers_fb(False, t("cc_personalization.wp_no_selection"))
            return
        spec = _get_editor_draft_spec()
        staged = {m: spec for m in sel}

    live_snap = dict(STATE.get("desktop", "out_live", {}) or {})
    _ASCII_APPLY_SEQ += 1
    my_seq = _ASCII_APPLY_SEQ
    _ASCII_APPLY_STATUS = t("cc_personalization.wp_ascii_rendering_init")
    last_ui_ts = [0.0]

    def _set_status(msg: str, force: bool = False) -> None:
        global _ASCII_APPLY_STATUS
        if my_seq != _ASCII_APPLY_SEQ:
            return
        _ASCII_APPLY_STATUS = msg
        now = time.monotonic()
        if force or (now - last_ui_ts[0]) >= 0.12:
            last_ui_ts[0] = now
            if cc_panel and cc_panel.shell:
                GLib.idle_add(lambda: (cc_panel.shell.refresh_view() if cc_panel and cc_panel.shell else None) and False)

    def _work():
        live = dict(live_snap)
        applied_mons = []
        modes_used = set()
        try:
            _cleanup_orphan_monitors(outs, live)
            for mon, spec in staged.items():
                if my_seq != _ASCII_APPLY_SEQ:
                    return ("cancelled", "", live, [])
                if not isinstance(spec, dict):
                    continue
                m_kind = spec.get("mode", "scene")
                modes_used.add(m_kind)
                if _spec_needs_asciify(spec):
                    asc = normalize_asciify_cfg(spec.get("asciify"))
                    target_p = str(spec["target"])
                    if m_kind in ("scene", "image"):
                        align_mode = _norm_align(spec.get("align", "center"))
                        _set_status(t("cc_personalization.wp_ascii_rendering_single", p0=os.path.basename(target_p)), force=True)
                        rendered = render_ascii_image(target_p, asc, preview=False)
                        if my_seq != _ASCII_APPLY_SEQ:
                            return ("cancelled", "", live, [])
                        ok_cmd, err_cmd = _wp.apply_image(mon, rendered, fit=spec.get("fit", "fill"), align=align_mode, sync_cleanup=True)
                        if not ok_cmd:
                            return ("err", (err_cmd or "apply failed").strip(), live, applied_mons)
                        _merge_desktop_json_meta(mon, {"source_target": target_p, "rendered_target": rendered, "align": align_mode, "asciify": asc})
                        ent = dict(spec)
                        ent["rendered_target"] = rendered
                        ent["ts"] = time.time() + 1.0
                        live[mon] = ent
                    else:
                        def _on_prog(idx: int, tot: int, fname: str) -> None:
                            _set_status(t("cc_personalization.wp_ascii_rendering_slide", p0=idx, p1=tot, p2=fname))

                        out_dir, _n_ok, _n_tot = render_ascii_folder(
                            target_p,
                            asc,
                            progress_cb=_on_prog,
                            cancel_check=lambda: my_seq != _ASCII_APPLY_SEQ,
                        )
                        if my_seq != _ASCII_APPLY_SEQ:
                            return ("cancelled", "", live, [])
                        if not out_dir:
                            return ("err", t("cc_personalization.wp_ascii_no_images"), live, applied_mons)
                        ok_cmd, err_cmd = _wp.apply_slideshow(
                            mon,
                            out_dir,
                            fit=spec.get("fit", "fill"),
                            transition=spec.get("trans", "fade"),
                            interval=spec.get("interval", "5m"),
                            shuffle=(spec.get("order") != "sequential"),
                            sync_cleanup=True,
                        )
                        if not ok_cmd:
                            return ("err", (err_cmd or "apply failed").strip(), live, applied_mons)
                        _merge_desktop_json_meta(mon, {"source_target": target_p, "rendered_target": out_dir, "asciify": asc})
                        ent = dict(spec)
                        ent["rendered_target"] = out_dir
                        ent["ts"] = time.time() + 1.0
                        live[mon] = ent
                else:
                    ok, async_grace, err = _apply_single_non_ascii_monitor(mon, spec, outs_data, live)
                    if not ok:
                        return ("err", err, live, applied_mons)
                    ent = dict(spec)
                    ent["ts"] = time.time() + async_grace
                    live[mon] = ent
                applied_mons.append(mon)
            if not applied_mons:
                return ("err", t("cc_personalization.wp_no_selection"), live, [])
            return ("ok", t("cc_personalization.wp_applied_msg", p0=", ".join(sorted(modes_used)), p1=", ".join(applied_mons)), live, applied_mons)
        except Exception as e:
            return ("err", str(e), live, applied_mons)

    def _done(res):
        global _WP_USER_TOUCHED, _ASCII_APPLY_STATUS
        if my_seq != _ASCII_APPLY_SEQ:
            return
        _ASCII_APPLY_STATUS = ""
        kind, msg, live_out, _mons = res
        if kind == "ok":
            STATE.set("desktop", "out_live", live_out)
            STATE.set("desktop", "staged_preview", {})
            _WP_USER_TOUCHED = False
            _base()._set_pers_fb(True, msg)
        elif kind == "err":
            _base()._set_pers_fb(False, msg)
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_work, on_done=_done)


def is_wp_user_touched() -> bool:
    return _WP_USER_TOUCHED

def set_wp_user_touched(val: bool = True) -> None:
    global _WP_USER_TOUCHED
    _WP_USER_TOUCHED = bool(val)

def _base():
    import panels.cc_engine_personalization as m
    return m
