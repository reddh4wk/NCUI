#!/usr/bin/env python3
"""NC-UI Personalization — Cursor Recoloring, Installer & Windows ANI/CUR Converter."""

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



_WIN_ROLE_TO_XCUR = {
    "pointer": "left_ptr",
    "arrow": "left_ptr",
    "normal": "left_ptr",
    "default": "left_ptr",
    "help": "question_arrow",
    "work": "left_ptr_watch",
    "working": "left_ptr_watch",
    "appstarting": "left_ptr_watch",
    "progress": "left_ptr_watch",
    "busy": "watch",
    "wait": "watch",
    "cross": "crosshair",
    "precision": "crosshair",
    "text": "xterm",
    "beam": "xterm",
    "ibeam": "xterm",
    "hand": "pencil",
    "pen": "pencil",
    "handwriting": "pencil",
    "unavailiable": "not-allowed",
    "unavailable": "not-allowed",
    "no": "not-allowed",
    "forbid": "not-allowed",
    "vert": "sb_v_double_arrow",
    "ns": "sb_v_double_arrow",
    "sizens": "sb_v_double_arrow",
    "horz": "sb_h_double_arrow",
    "ew": "sb_h_double_arrow",
    "sizewe": "sb_h_double_arrow",
    "dgn1": "bd_double_arrow",
    "nwse": "bd_double_arrow",
    "sizenwse": "bd_double_arrow",
    "dgn2": "fd_double_arrow",
    "nesw": "fd_double_arrow",
    "sizenesw": "fd_double_arrow",
    "move": "fleur",
    "sizeall": "fleur",
    "alternate": "center_ptr",
    "up": "center_ptr",
    "link": "pointer",
    "hand2": "pointer",
    "pin": "pointer",
    "person": "pointer",
}

_XCUR_SYMLINKS = {
    "left_ptr": ("default", "arrow", "top_left_arrow"),
    "pointer": ("hand", "hand1", "hand2", "pointing_hand"),
    "xterm": ("text", "ibeam"),
    "watch": ("wait",),
    "left_ptr_watch": ("progress", "half-busy"),
    "question_arrow": ("help", "whats_this", "left_ptr_help"),
    "crosshair": ("cross", "tcross"),
    "not-allowed": ("crossed_circle", "forbidden", "no-drop", "pirate"),
    "fleur": ("move", "all-scroll", "size_all", "grab", "grabbing"),
    "sb_v_double_arrow": ("ns-resize", "n-resize", "s-resize", "v_double_arrow", "row-resize", "size_ver"),
    "sb_h_double_arrow": ("ew-resize", "e-resize", "w-resize", "h_double_arrow", "col-resize", "size_hor"),
    "bd_double_arrow": ("nwse-resize", "nw-resize", "se-resize", "size_fdiag"),
    "fd_double_arrow": ("nesw-resize", "ne-resize", "sw-resize", "size_bdiag"),
    "center_ptr": ("up_arrow",),
    "pencil": ("draft",),
}


NCUI_CURSOR_NAME = "NCUI Cursor"
NCUI_CURSOR_PREFIX = "NCUI-Cursor"
NCUI_CURSOR_ASSETS_DIR = find_package_file("assets", "cursors", "ncui-cursor") or os.path.join(CORE_ROOT, "assets", "cursors", "ncui-cursor")


def is_ncui_cursor_name(name: str) -> bool:
    s = str(name or "").strip().lower()
    return s in ("ncui cursor", "ncui-cursor") or s.startswith("ncui-cursor-")


def _hex_to_hsv(hex_color: str, fallback: str) -> tuple[float, float, float]:
    h = str(hex_color or "").strip().lstrip("#")
    if len(h) != 6 or not re.match(r"^[0-9a-fA-F]{6}$", h):
        h = fallback.lstrip("#")
    return colorsys.rgb_to_hsv(int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


def _parse_xcursor_frames(path: str) -> list[tuple[int, int, int, int, int, int, bytes]]:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 16:
        return []
    magic, hdr_sz, _ver, ntoc = struct.unpack_from("<4sIII", data, 0)
    if magic != b"Xcur":
        return []
    frames = []
    for i in range(ntoc):
        off = hdr_sz + i * 12
        if off + 12 > len(data):
            break
        ctype, subtype, pos = struct.unpack_from("<III", data, off)
        if ctype != 0xFFFD0002 or pos + 36 > len(data):
            continue
        chdr, _ct2, csub, _cv, w, h, xhot, yhot, delay = struct.unpack_from("<IIIIIIIII", data, pos)
        pix_start = pos + chdr
        pix_len = w * h * 4
        if pix_start + pix_len > len(data):
            continue
        frames.append((int(csub or subtype or max(w, h)), w, h, xhot, yhot, delay, data[pix_start : pix_start + pix_len]))
    return frames


def _recolor_xcursor_bgra(
    raw: bytes,
    intent_hsv: tuple[float, float, float],
    fault_hsv: tuple[float, float, float],
) -> bytes:
    ih, isat, ival = intent_hsv
    fh, fsat, fval = fault_hsv
    out = bytearray(raw)
    n = len(out)
    i = 0
    while i < n:
        a = out[i + 3]
        if a > 0:
            b, g, r = out[i], out[i + 1], out[i + 2]
            inv_a = 1.0 / float(a)
            ur = min(1.0, r * inv_a)
            ug = min(1.0, g * inv_a)
            ub = min(1.0, b * inv_a)
            hh, ss, vv = colorsys.rgb_to_hsv(ur, ug, ub)
            deg = hh * 360.0
            if ss >= 0.15 and 130.0 <= deg <= 240.0:
                nr, ng, nb = colorsys.hsv_to_rgb(ih, min(1.0, isat * (ss / 0.63)), min(1.0, ival * vv))
                out[i] = min(a, int(round(nb * a)))
                out[i + 1] = min(a, int(round(ng * a)))
                out[i + 2] = min(a, int(round(nr * a)))
            elif ss >= 0.15 and (deg <= 40.0 or deg >= 320.0):
                nr, ng, nb = colorsys.hsv_to_rgb(fh, min(1.0, fsat * (ss / 0.65)), min(1.0, fval * vv))
                out[i] = min(a, int(round(nb * a)))
                out[i + 1] = min(a, int(round(ng * a)))
                out[i + 2] = min(a, int(round(nr * a)))
        i += 4
    return bytes(out)


def ensure_ncui_cursor_installed(intent_hex: str = "", fault_hex: str = "") -> str:
    if not os.path.isdir(NCUI_CURSOR_ASSETS_DIR):
        return "Adwaita"
    colors = THEME.get("colors", {})
    raw_i = str(intent_hex or colors.get("intent", "#00f0ff")).strip().lstrip("#").lower()
    raw_f = str(fault_hex or colors.get("fault", "#ff2442")).strip().lstrip("#").lower()
    if not re.match(r"^[0-9a-f]{6}$", raw_i):
        raw_i = "00f0ff"
    if not re.match(r"^[0-9a-f]{6}$", raw_f):
        raw_f = "ff2442"

    disk_name = f"{NCUI_CURSOR_PREFIX}-{raw_i}-{raw_f}"
    u_icons = _base()._user_icons_dir()
    os.makedirs(u_icons, exist_ok=True)
    dest_dir = os.path.join(u_icons, disk_name)
    if not os.path.isfile(os.path.join(dest_dir, "cursors", "left_ptr")):
        intent_hsv = _hex_to_hsv(f"#{raw_i}", "#00f0ff")
        fault_hsv = _hex_to_hsv(f"#{raw_f}", "#ff2442")
        tmp_dir = os.path.join(u_icons, f".{disk_name}.tmp-{os.getpid()}")
        cur_dir = os.path.join(tmp_dir, "cursors")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        os.makedirs(cur_dir, exist_ok=True)
        try:
            for fn in sorted(os.listdir(NCUI_CURSOR_ASSETS_DIR)):
                sp = os.path.join(NCUI_CURSOR_ASSETS_DIR, fn)
                if not os.path.isfile(sp):
                    continue
                frames = _parse_xcursor_frames(sp)
                if not frames:
                    continue
                recolored = [
                    (nom, w, h, xhot, yhot, delay, _recolor_xcursor_bgra(raw, intent_hsv, fault_hsv))
                    for (nom, w, h, xhot, yhot, delay, raw) in frames
                ]
                _write_xcursor_file(os.path.join(cur_dir, fn), recolored)
            for target_role, aliases in _XCUR_SYMLINKS.items():
                if not os.path.exists(os.path.join(cur_dir, target_role)):
                    continue
                for alias in aliases:
                    ap = os.path.join(cur_dir, alias)
                    if not os.path.exists(ap):
                        try:
                            os.symlink(target_role, ap)
                        except OSError:
                            pass
            with open(os.path.join(tmp_dir, "index.theme"), "w", encoding="utf-8") as f:
                f.write(
                    f"[Icon Theme]\nName={NCUI_CURSOR_NAME}\n"
                    f"Comment=NC-UI built-in theme-synced cursor (intent=#{raw_i}, fault=#{raw_f})\n"
                    f"Inherits=Adwaita\n"
                )
            shutil.rmtree(dest_dir, ignore_errors=True)
            os.replace(tmp_dir, dest_dir)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    # Prune stale color-tagged variants so ~/.local/share/icons stays tidy
    try:
        for entry in os.listdir(u_icons):
            if entry.startswith(f"{NCUI_CURSOR_PREFIX}-") and entry != disk_name:
                shutil.rmtree(os.path.join(u_icons, entry), ignore_errors=True)
    except OSError:
        pass

    # Maintain stable fallback alias directory inheriting the active color-tagged build
    alias_dir = os.path.join(u_icons, NCUI_CURSOR_PREFIX)
    try:
        os.makedirs(alias_dir, exist_ok=True)
        with open(os.path.join(alias_dir, "index.theme"), "w", encoding="utf-8") as f:
            f.write(f"[Icon Theme]\nName={NCUI_CURSOR_NAME}\nInherits={disk_name}\n")
    except OSError:
        pass
    return disk_name


def sync_ncui_cursor_if_active() -> bool:
    if os.environ.get("NC_UI_OFFSCREEN") == "1":
        return False
    cur_t, cur_sz = _read_cursor_cfg()
    if not is_ncui_cursor_name(cur_t):
        return False
    ok, _msg = _apply_cursor_cfg(NCUI_CURSOR_NAME, cur_sz)
    return ok


def _list_cursor_themes() -> list[dict]:
    found = {}
    if os.path.isdir(NCUI_CURSOR_ASSETS_DIR):
        try:
            n_master = sum(1 for fn in os.listdir(NCUI_CURSOR_ASSETS_DIR) if os.path.isfile(os.path.join(NCUI_CURSOR_ASSETS_DIR, fn)))
        except OSError:
            n_master = 15
        n_links = sum(len(v) for k, v in _XCUR_SYMLINKS.items() if os.path.isfile(os.path.join(NCUI_CURSOR_ASSETS_DIR, k)))
        found[NCUI_CURSOR_NAME] = {
            "name": NCUI_CURSOR_NAME,
            "path": NCUI_CURSOR_ASSETS_DIR,
            "is_builtin": True,
            "is_user": False,
            "count": n_master + n_links,
            "has_archive": False,
        }
    u_icons = _base()._user_icons_dir()
    c_store = _base()._cursors_store_dir()
    for root in _base()._sys_icons_dirs():
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            if name in ("default", "hicolor", NCUI_CURSOR_PREFIX) or name.startswith(f"{NCUI_CURSOR_PREFIX}-") or name in found:
                continue
            cur_dir = os.path.join(root, name, "cursors")
            if not os.path.isdir(cur_dir):
                continue
            try:
                cnt = len(os.listdir(cur_dir))
            except OSError:
                cnt = 0
            if cnt <= 0:
                continue
            found[name] = {
                "name": name,
                "path": os.path.join(root, name),
                "is_builtin": False,
                "is_user": os.path.abspath(root) == os.path.abspath(u_icons),
                "count": cnt,
                "has_archive": os.path.isfile(os.path.join(c_store, f"{name}.zip")),
            }
    if "Adwaita" not in found:
        found["Adwaita"] = {"name": "Adwaita", "path": "/usr/share/icons/Adwaita", "is_builtin": False, "is_user": False, "count": 0, "has_archive": False}
    return sorted(found.values(), key=lambda x: (0 if x.get("is_builtin") else (1 if x["is_user"] else 2), x["name"].lower()))


def _read_cursor_cfg() -> tuple[str, int]:
    typo = THEME.get("typography", {})
    theme_n = str(typo.get("cursor_theme") or STATE.get("personalization", "cursor_theme", "") or "")
    size_v = int(typo.get("cursor_size") or STATE.get("personalization", "cursor_size", 24) or 24)
    niri_cfg = _base()._niri_config_file()
    if os.path.exists(niri_cfg):
        try:
            txt = open(niri_cfg, "r", encoding="utf-8").read()
            m_t = re.search(r'xcursor-theme\s+"([^"]+)"', txt)
            m_s = re.search(r"xcursor-size\s+(\d+)", txt)
            if m_t:
                theme_n = m_t.group(1)
            if m_s:
                size_v = int(m_s.group(1))
        except Exception:
            pass
    if is_ncui_cursor_name(theme_n):
        theme_n = NCUI_CURSOR_NAME
    return theme_n or "Adwaita", max(16, min(64, int(size_v or 24)))


def _update_gtk_ini_cursor(ini_path: str, theme_name: str, size_val: int) -> None:
    os.makedirs(os.path.dirname(ini_path), exist_ok=True)
    txt = ""
    if os.path.isfile(ini_path):
        try:
            txt = open(ini_path, "r", encoding="utf-8").read()
        except OSError:
            txt = ""
    lines = txt.splitlines()
    if not any(ln.strip().lower() == "[settings]" for ln in lines):
        lines = ["[Settings]"] + lines
    out = []
    in_sec = False
    wrote_t = wrote_s = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_sec:
                if not wrote_t:
                    out.append(f"gtk-cursor-theme-name={theme_name}")
                    wrote_t = True
                if not wrote_s:
                    out.append(f"gtk-cursor-theme-size={int(size_val)}")
                    wrote_s = True
            in_sec = (s.lower() == "[settings]")
            out.append(ln)
            continue
        if in_sec and s.startswith("gtk-cursor-theme-name="):
            out.append(f"gtk-cursor-theme-name={theme_name}")
            wrote_t = True
        elif in_sec and s.startswith("gtk-cursor-theme-size="):
            out.append(f"gtk-cursor-theme-size={int(size_val)}")
            wrote_s = True
        else:
            out.append(ln)
    if in_sec:
        if not wrote_t:
            out.append(f"gtk-cursor-theme-name={theme_name}")
        if not wrote_s:
            out.append(f"gtk-cursor-theme-size={int(size_val)}")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")


def _apply_cursor_cfg(theme_name: str, size_val: int) -> tuple[bool, str]:
    raw_t = str(theme_name or "Adwaita").strip() or "Adwaita"
    is_ncui = is_ncui_cursor_name(raw_t)
    clean_t = NCUI_CURSOR_NAME if is_ncui else raw_t
    disk_t = ensure_ncui_cursor_installed() if is_ncui else clean_t
    clean_sz = max(16, min(64, int(size_val or 24)))
    STATE.set("personalization", "cursor_theme", clean_t)
    STATE.set("personalization", "cursor_size", clean_sz)
    c_store = _base()._cursors_store_dir()
    u_icons = _base()._user_icons_dir()
    xdg_cfg = _base()._xdg_cfg_dir()
    niri_cfg = _base()._niri_config_file()
    arch_name = "" if is_ncui else (f"{clean_t}.zip" if os.path.isfile(os.path.join(c_store, f"{clean_t}.zip")) else "")
    typo = THEME.setdefault("typography", {})
    typo["cursor_theme"] = clean_t
    typo["cursor_size"] = clean_sz
    typo["cursor_archive"] = arch_name
    _base()._save_theme_file(_theme_path(), THEME)

    for ini_p in (
        os.path.join(xdg_cfg, "gtk-3.0", "settings.ini"),
        os.path.join(xdg_cfg, "gtk-4.0", "settings.ini"),
    ):
        try:
            _update_gtk_ini_cursor(ini_p, disk_t, clean_sz)
        except OSError:
            pass

    for def_p in (
        os.path.expanduser("~/.icons/default/index.theme"),
        os.path.join(u_icons, "default", "index.theme"),
    ):
        try:
            os.makedirs(os.path.dirname(def_p), exist_ok=True)
            with open(def_p, "w", encoding="utf-8") as f:
                f.write(f"[Icon Theme]\nName=Default\nInherits={disk_t}\n")
        except OSError:
            pass

    os.environ["XCURSOR_THEME"] = disk_t
    os.environ["XCURSOR_SIZE"] = str(clean_sz)
    try:
        from gi.repository import Gtk
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-cursor-theme-name", disk_t)
            settings.set_property("gtk-cursor-theme-size", clean_sz)
    except Exception:
        pass

    if os.path.exists(niri_cfg):
        try:
            old_txt = open(niri_cfg, "r", encoding="utf-8").read()
            txt = old_txt
            c_blk = f'cursor {{\n    xcursor-theme "{disk_t}"\n    xcursor-size {clean_sz}\n}}'
            m_blk = re.search(r"(?ms)^[ \t]*cursor\s*\{[^\}]*\}", txt)
            if m_blk:
                blk = m_blk.group(0)
                if re.search(r"xcursor-theme\s+\"[^\"]*\"", blk):
                    blk = re.sub(r'xcursor-theme\s+"[^"]*"', f'xcursor-theme "{disk_t}"', blk)
                else:
                    blk = blk.rstrip("}") + f'    xcursor-theme "{disk_t}"\n}}'
                if re.search(r"xcursor-size\s+\d+", blk):
                    blk = re.sub(r"xcursor-size\s+\d+", f"xcursor-size {clean_sz}", blk)
                else:
                    blk = blk.rstrip("}") + f"    xcursor-size {clean_sz}\n}}"
                txt = txt[:m_blk.start()] + blk + txt[m_blk.end():]
            else:
                txt = txt.rstrip() + f"\n\n{c_blk}\n"
            with open(niri_cfg, "w", encoding="utf-8") as f:
                f.write(txt)
            if probe.has_bin("niri"):
                ok_v, _o, err_v = probe.run_cmd_full(["niri", "validate", "-c", niri_cfg], timeout=2.0)
                if not ok_v:
                    with open(niri_cfg, "w", encoding="utf-8") as f:
                        f.write(old_txt)
                    return False, err_v or t("cc_personalization.niri_validate_err")
                probe.run_cmd(["niri", "msg", "action", "load-config-file"], timeout=1.5)
        except Exception as e:
            return False, str(e)
    return True, t("cc_personalization.cur_applied_msg", p0=clean_t, p1=clean_sz)


def _pixbuf_to_xcur_bgra(pb) -> tuple[int, int, bytes]:
    w, h = int(pb.get_width()), int(pb.get_height())
    nc = int(pb.get_n_channels())
    stride = int(pb.get_rowstride())
    px = pb.get_pixels()
    out = bytearray(w * h * 4)
    dst = 0
    for y in range(h):
        row_off = y * stride
        for x in range(w):
            off = row_off + x * nc
            r, g, b = px[off], px[off + 1], px[off + 2]
            a = px[off + 3] if nc >= 4 else 255
            if a == 0:
                out[dst : dst + 4] = b"\x00\x00\x00\x00"
            elif a < 255:
                out[dst] = (b * a + 127) // 255
                out[dst + 1] = (g * a + 127) // 255
                out[dst + 2] = (r * a + 127) // 255
                out[dst + 3] = a
            else:
                out[dst] = b
                out[dst + 1] = g
                out[dst + 2] = r
                out[dst + 3] = 255
            dst += 4
    return w, h, bytes(out)


def _decode_win_img_payload(payload: bytes, w_hint: int, h_hint: int) -> tuple[int, int, bytes] | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        ldr = GdkPixbuf.PixbufLoader.new_with_type("png")
        ldr.write(payload)
        ldr.close()
        pb = ldr.get_pixbuf()
        return _pixbuf_to_xcur_bgra(pb) if pb else None
    wb = 0 if w_hint >= 256 else w_hint
    hb = 0 if h_hint >= 256 else h_hint
    ico_blob = struct.pack("<HHHBBBBHHII", 0, 1, 1, wb, hb, 0, 0, 1, 32, len(payload), 22) + payload
    try:
        ldr = GdkPixbuf.PixbufLoader.new_with_type("ico")
        ldr.write(ico_blob)
        ldr.close()
        pb = ldr.get_pixbuf()
        if pb:
            return _pixbuf_to_xcur_bgra(pb)
    except Exception:
        pass
    return None


def _parse_cur_frames(raw: bytes, default_delay_ms: int = 50) -> list[tuple[int, int, int, int, int, int, bytes]]:
    if len(raw) < 6:
        return []
    _res, ico_type, count = struct.unpack_from("<HHH", raw, 0)
    if ico_type not in (1, 2) or count <= 0 or len(raw) < 6 + count * 16:
        return []
    frames = []
    for i in range(count):
        wb, hb, _cols, _r, p_xh, b_yh, img_sz, img_off = struct.unpack_from("<BBBBHHII", raw, 6 + i * 16)
        if img_off + img_sz > len(raw) or img_sz <= 0:
            continue
        w_hint, h_hint = (wb or 256), (hb or 256)
        decoded = _decode_win_img_payload(raw[img_off : img_off + img_sz], w_hint, h_hint)
        if not decoded:
            continue
        w, h, bgra = decoded
        xhot = min(max(0, p_xh if ico_type == 2 else 0), max(0, w - 1))
        yhot = min(max(0, b_yh if ico_type == 2 else 0), max(0, h - 1))
        frames.append((max(w, h), w, h, xhot, yhot, max(16, int(default_delay_ms)), bgra))
    return frames


def _parse_ani_frames(raw: bytes) -> list[tuple[int, int, int, int, int, int, bytes]]:
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"ACON":
        return []
    pos = 12
    default_jif = 10
    rates = []
    seq = []
    icons = []
    while pos + 8 <= len(raw):
        ck_id = raw[pos : pos + 4]
        ck_sz = struct.unpack_from("<I", raw, pos + 4)[0]
        ck_data = raw[pos + 8 : pos + 8 + ck_sz]
        pos += 8 + ck_sz + (ck_sz & 1)
        if ck_id == b"anih" and len(ck_data) >= 36:
            vals = struct.unpack_from("<9I", ck_data, 0)
            default_jif = vals[7] or 10
        elif ck_id == b"rate":
            cnt = len(ck_data) // 4
            rates = list(struct.unpack_from(f"<{cnt}I", ck_data, 0))
        elif ck_id == b"seq ":
            cnt = len(ck_data) // 4
            seq = list(struct.unpack_from(f"<{cnt}I", ck_data, 0))
        elif ck_id == b"LIST" and len(ck_data) >= 4 and ck_data[:4] == b"fram":
            sub_p = 4
            while sub_p + 8 <= len(ck_data):
                sid = ck_data[sub_p : sub_p + 4]
                ssz = struct.unpack_from("<I", ck_data, sub_p + 4)[0]
                if sid == b"icon":
                    icons.append(ck_data[sub_p + 8 : sub_p + 8 + ssz])
                sub_p += 8 + ssz + (ssz & 1)
    if not icons:
        return []
    steps = seq if seq else list(range(len(icons)))
    out = []
    for step_i, frame_idx in enumerate(steps):
        if not (0 <= frame_idx < len(icons)):
            continue
        jif = rates[step_i] if step_i < len(rates) else default_jif
        delay_ms = max(16, int(round((jif or default_jif) * 1000.0 / 60.0)))
        out.extend(_parse_cur_frames(icons[frame_idx], default_delay_ms=delay_ms))
    return out


def _write_xcursor_file(out_path: str, frames: list[tuple[int, int, int, int, int, int, bytes]]) -> None:
    ntoc = len(frames)
    hdr_end = 16 + ntoc * 12
    toc_buf = bytearray()
    chunks_buf = bytearray()
    cur_pos = hdr_end
    for nom, w, h, xhot, yhot, delay_ms, bgra in frames:
        toc_buf.extend(struct.pack("<III", 0xFFFD0002, nom, cur_pos))
        ch = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, nom, 1, w, h, xhot, yhot, delay_ms)
        chunks_buf.extend(ch)
        chunks_buf.extend(bgra)
        cur_pos += len(ch) + len(bgra)
    with open(out_path, "wb") as f:
        f.write(struct.pack("<4sIII", b"Xcur", 16, 0x10000, ntoc))
        f.write(toc_buf)
        f.write(chunks_buf)


def _match_cursor_role(stem: str) -> str:
    s = stem.lower().strip()
    for token, xcur_name in _WIN_ROLE_TO_XCUR.items():
        if s == token or s.endswith(f"_{token}") or s.endswith(f"-{token}"):
            return xcur_name
    for token, xcur_name in _WIN_ROLE_TO_XCUR.items():
        if token in s:
            return xcur_name
    return ""


def _parse_inf_roles(inf_text: str) -> dict[str, str]:
    roles = {}
    in_strings = False
    for raw_ln in inf_text.splitlines():
        ln = raw_ln.strip()
        if not ln or ln.startswith(";"):
            continue
        if ln.startswith("[") and ln.endswith("]"):
            in_strings = (ln[1:-1].strip().lower() == "strings")
            continue
        if in_strings and "=" in ln:
            k, v = ln.split("=", 1)
            k_low = k.strip().lower()
            v_clean = os.path.basename(v.strip().strip('"').strip("'").replace("\\", "/")).lower()
            if k_low in _WIN_ROLE_TO_XCUR and v_clean:
                roles[v_clean] = _WIN_ROLE_TO_XCUR[k_low]
    return roles


def convert_windows_cursor_zip(zip_path: str, custom_name: str = "") -> tuple[bool, str, str]:
    zp = os.path.abspath(os.path.expanduser(str(zip_path or "").strip().strip('"').strip("'")))
    if zp.startswith("file://"):
        zp = zp[7:]
    if not os.path.isfile(zp) or not zipfile.is_zipfile(zp):
        return False, t("cc_personalization.cur_zip_invalid", p0=zip_path), ""

    raw_base = custom_name or os.path.splitext(os.path.basename(zp))[0]
    theme_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw_base).strip("-._") or "win-cursor"
    dest_dir = os.path.join(_base()._user_icons_dir(), theme_name)
    cur_dir = os.path.join(dest_dir, "cursors")
    os.makedirs(cur_dir, exist_ok=True)

    inf_map = {}
    win_entries = []
    precompiled_xcur = []
    with zipfile.ZipFile(zp, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            bn = os.path.basename(info.filename)
            if not bn:
                continue
            low = bn.lower()
            data = zf.read(info.filename)
            if low.endswith(".inf"):
                inf_map.update(_parse_inf_roles(data.decode("utf-8", errors="ignore")))
            elif low.endswith((".cur", ".ani")):
                win_entries.append((bn, data))
            elif data[:4] == b"Xcur" and "/cursors/" in info.filename.replace("\\", "/"):
                precompiled_xcur.append((bn, data))

    written_roles = set()
    if win_entries:
        fallback_frames = None
        for bn, data in win_entries:
            frames = _parse_ani_frames(data) if bn.lower().endswith(".ani") else _parse_cur_frames(data)
            if not frames:
                continue
            if fallback_frames is None:
                fallback_frames = frames
            role = inf_map.get(bn.lower()) or _match_cursor_role(os.path.splitext(bn)[0])
            if not role or role in written_roles:
                continue
            _write_xcursor_file(os.path.join(cur_dir, role), frames)
            written_roles.add(role)
        if "left_ptr" not in written_roles and fallback_frames:
            _write_xcursor_file(os.path.join(cur_dir, "left_ptr"), fallback_frames)
            written_roles.add("left_ptr")
    elif precompiled_xcur:
        for bn, data in precompiled_xcur:
            with open(os.path.join(cur_dir, bn), "wb") as f:
                f.write(data)
            written_roles.add(bn)

    if not written_roles:
        shutil.rmtree(dest_dir, ignore_errors=True)
        return False, t("cc_personalization.cur_zip_no_cursors"), ""

    for target_role, aliases in _XCUR_SYMLINKS.items():
        if not os.path.exists(os.path.join(cur_dir, target_role)):
            continue
        for alias in aliases:
            ap = os.path.join(cur_dir, alias)
            if not os.path.exists(ap):
                try:
                    os.symlink(target_role, ap)
                except OSError:
                    pass

    with open(os.path.join(dest_dir, "index.theme"), "w", encoding="utf-8") as f:
        f.write(f"[Icon Theme]\nName={theme_name}\nComment=Converted from Windows cursor archive by NC-UI\nInherits=Adwaita\n")

    c_store = _base()._cursors_store_dir()
    os.makedirs(c_store, exist_ok=True)
    stored_zip = os.path.join(c_store, f"{theme_name}.zip")
    if os.path.abspath(zp) != os.path.abspath(stored_zip):
        shutil.copy2(zp, stored_zip)

    _cur_t, cur_sz = _read_cursor_cfg()
    ok_app, msg_app = _apply_cursor_cfg(theme_name, cur_sz)
    if not ok_app:
        return False, msg_app, theme_name
    return True, t("cc_personalization.cur_zip_installed", p0=theme_name, p1=len(written_roles)), theme_name


def _delete_cursor_theme(name: str) -> tuple[bool, str]:
    if is_ncui_cursor_name(name):
        return False, t("cc_personalization.cur_del_builtin_err")
    clean = os.path.basename(str(name or "").strip())
    if not clean:
        return False, t("cc_personalization.cur_invalid_err")
    target = os.path.join(_base()._user_icons_dir(), clean)
    if not os.path.isdir(target):
        return False, t("cc_personalization.cur_del_system_err")
    shutil.rmtree(target, ignore_errors=True)
    arch = os.path.join(_base()._cursors_store_dir(), f"{clean}.zip")
    if os.path.isfile(arch):
        try:
            os.remove(arch)
        except OSError:
            pass
    cur_t, cur_sz = _read_cursor_cfg()
    if cur_t == clean:
        _apply_cursor_cfg("Adwaita", cur_sz)
    return True, t("cc_personalization.cur_deleted_msg", p0=clean)

def _base():
    import panels.cc_engine_personalization as m
    return m
