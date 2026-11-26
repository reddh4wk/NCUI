#!/usr/bin/env python3
import hashlib
import math
import os
import random
import re
import time
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo
from core import probe
try:
    from core.asciifier import (
        CHAR_SETS,
        COLOR_MODES,
        get_ascii_cache_stats,
        is_pillow_available,
    )
except ImportError:
    CHAR_SETS = {}
    COLOR_MODES = {}
    get_ascii_cache_stats = lambda: {"count": 0, "bytes": 0}
    is_pillow_available = lambda: False
from core.config import THEME, is_ncmatrix_available
from core.wallpaper import has_katakana_font
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, InlineField, SelectPill, Slider, StateBadge, SwatchTxt, Txt, get_inline_entry_error, get_semantic_palette, hex_to_rgba_f, rounded_rect_path
from ui.widgets import AsciiCard
from panels.cc_engine_personalization import (
    SOLID_CLASSIC_PRESETS,
    SOLID_DESIGNER_PRESETS,
    WALL_DIR,
    _feedback_cards,
    _find_media_file,
    _fmt_cm_glow,
    _fmt_cm_parallax,
    _fmt_cm_var,
    _get_niri_outputs,
    _norm_cm_decay,
    _norm_cm_density,
    _norm_cm_mirror,
    _norm_cm_overlap,
    _norm_cm_shimmer,
    _norm_cm_tail,
    _resolve_solid_hex,
    _resolve_yt_source,
    get_ncmatrix_live_cpu_usage,
    count_folder_media,
    detect_media_kind,
    get_active_yazi_session,
    get_ascii_apply_status,
    get_desktop_preview_state,
    get_editor_asciify_cfg,
    get_selected_monitors,
    is_live_15fps_enabled,
    should_show_scene_alignment,
    sync_editor_from_live_monitors,
)

_MATRIX_RGB = {
    "green": (0.0, 1.0, 0.45),
    "cyan": (0.0, 0.94, 1.0),
    "blue": (0.30, 0.56, 1.0),
    "red": (1.0, 0.16, 0.25),
    "magenta": (1.0, 0.16, 0.62),
    "yellow": (1.0, 0.92, 0.10),
    "white": (0.94, 0.96, 1.0),
}
_CHARSETS = {
    "katakana": "".join(chr(c) for c in range(0xFF66, 0xFF9D)) + "0123456789",
    "ascii": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ*+",
    "binary": "01",
    "hex": "0123456789ABCDEF",
}
_PIXBUF_CACHE = {}
_NCM_GLYPH_CACHE = {}
_NCM_SIM_CACHE = {}
_NCM_EXP_DECAY = [0.08 + 0.88 * math.exp(-3.15 * (i / 255.0)) for i in range(256)]
_NCM_LIN_DECAY = [max(0.14, 1.0 - (i / 255.0)) * 0.90 for i in range(256)]
_NCM_SPEED_MS = (520.0, 420.0, 330.0, 260.0, 200.0, 150.0, 110.0, 75.0, 45.0, 20.0)


def _ncm_speed_to_ms(spd: int) -> float:
    idx = max(1, min(10, int(spd))) - 1
    return _NCM_SPEED_MS[idx]


def _vivid_rainbow_rgb(seed_b: bytes) -> tuple:
    r, g, b = seed_b[3], seed_b[4], seed_b[5]
    if r < 0xCC and g < 0xCC and b < 0xCC:
        ch = seed_b[6] % 3
        boost = 0xCC + (seed_b[7] % (256 - 0xCC))
        if ch == 0:
            r = boost
        elif ch == 1:
            g = boost
        else:
            b = boost
    return (r / 255.0, g / 255.0, b / 255.0)


def _get_ncm_glyph_atlas(charset: str, fsize: int) -> dict:
    """Pre-render compact A8 normal, horizontally mirrored, and CRT halo surfaces per glyph."""
    cell_h = max(4, min(10, int(round(2.4 + fsize * 0.28))))
    cell_w = max(3, int(round(cell_h * 0.72)))
    key = (charset, cell_w, cell_h)
    cached = _NCM_GLYPH_CACHE.get(key)
    if cached is not None:
        return cached

    glyphs = _CHARSETS.get(charset, _CHARSETS["katakana"])
    font_px = max(3.6, cell_h * 0.92)
    fdesc = Pango.FontDescription.from_string(f"monospace {font_px:.1f}px")
    fopts = cairo.FontOptions()
    fopts.set_antialias(cairo.ANTIALIAS_GRAY)
    fopts.set_hint_style(cairo.HINT_STYLE_NONE)

    normals = []
    mirroreds = []
    halos = []
    halo_offsets = (
        (0, 0, 0.30),
        (-1, 0, 0.24),
        (1, 0, 0.24),
        (0, -1, 0.24),
        (0, 1, 0.24),
        (-1, -1, 0.15),
        (1, -1, 0.15),
        (-1, 1, 0.15),
        (1, 1, 0.15),
        (-2, 0, 0.08),
        (2, 0, 0.08),
        (0, -2, 0.08),
        (0, 2, 0.08),
    )

    for ch in glyphs:
        s_norm = cairo.ImageSurface(cairo.FORMAT_A8, cell_w, cell_h)
        cr = cairo.Context(s_norm)
        cr.set_font_options(fopts)
        cr.set_source_rgba(0.0, 0.0, 0.0, 1.0)
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(fdesc)
        lay.set_text(ch, -1)
        _ink, log = lay.get_pixel_extents()
        ox = (cell_w - log.width) * 0.5
        oy = (cell_h - log.height) * 0.5
        cr.move_to(ox, oy)
        PangoCairo.show_layout(cr, lay)

        s_mir = cairo.ImageSurface(cairo.FORMAT_A8, cell_w, cell_h)
        mcr = cairo.Context(s_mir)
        mcr.translate(cell_w, 0.0)
        mcr.scale(-1.0, 1.0)
        mcr.set_source_surface(s_norm, 0.0, 0.0)
        mcr.paint()

        s_halo = cairo.ImageSurface(cairo.FORMAT_A8, cell_w + 4, cell_h + 4)
        hcr = cairo.Context(s_halo)
        for dx, dy, alpha in halo_offsets:
            hcr.set_source_rgba(0.0, 0.0, 0.0, alpha)
            hcr.mask_surface(s_norm, 2 + dx, 2 + dy)

        normals.append(s_norm)
        mirroreds.append(s_mir)
        halos.append(s_halo)

    res = {
        "cell_w": cell_w,
        "cell_h": cell_h,
        "normal": normals,
        "mirrored": mirroreds,
        "halo": halos,
        "n": len(glyphs),
        "glyphs": glyphs,
    }
    if len(_NCM_GLYPH_CACHE) >= 24:
        _NCM_GLYPH_CACHE.clear()
    _NCM_GLYPH_CACHE[key] = res
    return res


def _ncm_max_space(rows: int, rate: float) -> int:
    rows = max(1, int(rows))
    if rate <= 0.001 or abs(rate - 1.0) < 1e-4:
        return rows
    if rate < 1.0:
        mean_s = rows * ((1.0 / rate) - 0.5)
        return max(1, min(rows * 40, int(round(2.0 * mean_s - 1.0))))
    eff_t = max(5.0, (rows / rate) - 2.0)
    frac_len = 0.50 + 0.14 * min(1.0, (rate - 1.0) * 0.25)
    mean_s = eff_t * (1.0 - frac_len)
    return max(1, int(round(2.0 * mean_s - 1.0)))


def _ncm_pick_length(rows: int, rate: float, tail_mode: str, rng: random.Random) -> int:
    if rows <= 3:
        base_l = 3
    elif rate <= 1.0001:
        base_l = rng.randrange(rows - 3) + 3
    else:
        eff_t = max(5.0, (rows / rate) - 2.0)
        frac_len = 0.50 + 0.14 * min(1.0, (rate - 1.0) * 0.25)
        mean_l = eff_t * frac_len
        span = max(1, min(rows - 3, int(round(2.0 * (mean_l - 3.0) + 1.0))))
        base_l = rng.randrange(span) + 3
    if tail_mode == "short":
        base_l = (base_l * 55 + 50) // 100
    elif tail_mode == "long":
        base_l = (base_l * 175 + 50) // 100
    return max(3, min(rows * 2, base_l))


class _MiniMatrixSim:
    """Stateful miniature physics simulation matching ncmatrix.c timing and trail optics."""

    MAX_TRAILS_PER_COL = 4

    def __init__(
        self,
        out_name: str,
        n_active: int,
        rows: int,
        is_dense: bool,
        n_glyphs: int,
        real_rows: float,
        cfg: tuple,
    ):
        self.out_name = out_name
        self.n_active = n_active
        self.rows = rows
        self.is_dense = is_dense
        self.n_glyphs = max(1, n_glyphs)
        self.real_rows = max(14.0, real_rows)
        self.cfg = cfg  # (spd, spd_var, parallax_str, spawn_rate, tail_mode, charset)
        seed_val = int.from_bytes(hashlib.md5(f"sim:{out_name}:{n_active}:{rows}".encode("utf-8")).digest()[:8], "little")
        self.rng = random.Random(seed_val)
        self.cols = [[] for _ in range(n_active)]
        spd, _spd_var, _par, rate, _tail, _cs = cfg
        base_ms = _ncm_speed_to_ms(spd)
        max_sp = max(1, _ncm_max_space(rows, rate))
        row_scale = self.real_rows / float(max(4, rows))
        self.spawn_cd = [float(self.rng.randrange(max_sp) + 1) * base_ms * row_scale for _ in range(n_active)]
        self.last_ts = time.monotonic()
        self.warmup(cfg)

    def _calc_lum(self, tr: list, spd_var: float, parallax_str: float) -> None:
        rel = tr[4]
        real_step_ms = tr[2]
        abs_rel = 1.0 - max(0.0, min(1.0, (real_step_ms - 20.0) / 380.0))
        eff_rel = (0.78 * rel + 0.22 * abs_rel) if spd_var > 0.01 else abs_rel
        s = max(0.0, min(1.0, parallax_str)) * 2.0
        if s <= 1.0:
            atten = s * (1.0 - eff_rel)
            min_br = 28.0 / 255.0
        else:
            atten = 1.0 - (max(0.0, eff_rel) ** s)
            min_br = (28.0 - (s - 1.0) * 16.0) / 255.0
        tr[6] = max(min_br, 1.0 - atten * (1.0 - min_br))

    def sync_config(self, cfg: tuple, n_glyphs: int, real_rows: float) -> None:
        spd, spd_var, parallax_str, rate, _tail, charset = cfg
        old_spd, old_var, old_par, old_rate, _old_tail, old_cs = self.cfg
        self.real_rows = max(14.0, real_rows)
        self.n_glyphs = max(1, n_glyphs)
        base_ms = _ncm_speed_to_ms(spd)

        if abs(spd - old_spd) > 1e-4 or abs(spd_var - old_var) > 1e-4 or abs(parallax_str - old_par) > 1e-4:
            spread = 2.0 ** (spd_var * 0.50)
            min_ms = 10.0 if spd_var > 5.0 else 12.0
            max_ms = base_ms * (2.1 if spd_var <= 5.0 else (2.1 + (spd_var - 5.0) * 0.48))
            for col_tr in self.cols:
                for tr in col_tr:
                    rel = tr[4]
                    if spd_var <= 0.01:
                        tr[2] = base_ms
                    else:
                        tr[2] = max(min_ms, min(max_ms, base_ms * (spread ** -(rel * 2.0 - 1.0))))
                    self._calc_lum(tr, spd_var, parallax_str)

        if charset != old_cs and self.n_glyphs > 0:
            ng = self.n_glyphs
            for col_tr in self.cols:
                for tr in col_tr:
                    tr[8] = [g % ng for g in tr[8]]

        if rate > 0.001 and abs(rate - old_rate) > 1e-4:
            row_scale = self.real_rows / float(max(4, self.rows))
            max_cd = max(base_ms * row_scale, _ncm_max_space(self.rows, rate) * base_ms * row_scale)
            for k in range(self.n_active):
                if self.spawn_cd[k] > max_cd:
                    self.spawn_cd[k] = (self.rng.random() * 0.95 + 0.05) * max_cd

        self.cfg = cfg

    def _spawn(self, k: int, spd: int, spd_var: float, parallax_str: float, rate: float, tail_mode: str) -> int:
        rng = self.rng
        base_ms = _ncm_speed_to_ms(spd)
        if spd_var <= 0.01:
            rel = 0.5
            real_step_ms = base_ms
        else:
            rel = rng.random()
            spread = 2.0 ** (spd_var * 0.50)
            min_ms = 10.0 if spd_var > 5.0 else 12.0
            max_ms = base_ms * (2.1 if spd_var <= 5.0 else (2.1 + (spd_var - 5.0) * 0.48))
            real_step_ms = max(min_ms, min(max_ms, base_ms * (spread ** -(rel * 2.0 - 1.0))))
        length = _ncm_pick_length(self.rows, rate, tail_mode, rng)
        head_mix = rng.randrange(256)
        color_seed = rng.randrange(251)
        glyphs = [0] * self.rows
        glyphs[0] = rng.randrange(self.n_glyphs)
        tr = [0, length, real_step_ms, 0.0, rel, head_mix, 1.0, color_seed, glyphs]
        self._calc_lum(tr, spd_var, parallax_str)
        self.cols[k].append(tr)
        return length

    def step_dt(self, dt_ms: float, shimmer_m: str) -> None:
        if dt_ms <= 0.0:
            return
        spd, spd_var, parallax_str, rate, tail_mode, _cs = self.cfg
        row_scale = self.real_rows / float(max(4, self.rows))
        base_eff_ms = _ncm_speed_to_ms(spd) * row_scale
        rows = self.rows
        rng = self.rng
        ng = self.n_glyphs
        shim_k = 4 if shimmer_m == "insane" else (2 if shimmer_m == "high" else (1 if shimmer_m == "med" else (-1 if shimmer_m == "low" else 0)))

        for k in range(self.n_active):
            col_tr = self.cols[k]
            min_head = 32767
            min_tail_top = 32767
            alive = []
            for tr in col_tr:
                eff_ms = tr[2] * row_scale
                acc = tr[3] + dt_ms
                dead = False
                guard = 0
                tg = tr[8]
                while acc >= eff_ms and guard < 4:
                    acc -= eff_ms
                    guard += 1
                    tr[0] += 1
                    hr = tr[0]
                    if 0 <= hr < rows:
                        tg[hr] = rng.randrange(ng)
                    if shim_k != 0 and hr > 1:
                        n_mut = (1 if rng.random() < 0.5 else 0) if shim_k < 0 else shim_k
                        if n_mut > 0:
                            r_lo = max(0, hr - tr[1])
                            r_hi = min(rows - 1, hr - 1)
                            if r_hi >= r_lo:
                                for _ in range(n_mut):
                                    mr = rng.randint(r_lo, r_hi)
                                    tg[mr] = rng.randrange(ng)
                    if hr - tr[1] >= rows:
                        dead = True
                        break
                if not dead:
                    tr[3] = min(acc, eff_ms)
                    alive.append(tr)
                    if tr[0] < min_head:
                        min_head = tr[0]
                    t_top = tr[0] - tr[1]
                    if t_top < min_tail_top:
                        min_tail_top = t_top
            col_tr[:] = alive

            if rate > 0.001:
                if spd_var > 0.01 or min_tail_top >= 1:
                    self.spawn_cd[k] -= dt_ms
                if self.spawn_cd[k] <= 0.0 and len(col_tr) < self.MAX_TRAILS_PER_COL and min_head >= 3:
                    new_len = self._spawn(k, spd, spd_var, parallax_str, rate, tail_mode)
                    max_sp = max(1, _ncm_max_space(rows, rate))
                    gap = rng.randrange(max_sp) + 1
                    wait_rows = max(1, gap + (new_len // 2 if spd_var > 0.01 else 0))
                    self.spawn_cd[k] = wait_rows * base_eff_ms

    def warmup(self, cfg: tuple) -> None:
        spd = cfg[0]
        base_ms = _ncm_speed_to_ms(spd)
        total_ms = max(2800.0, base_ms * self.real_rows * 1.35)
        steps = 72
        dt = total_ms / float(steps)
        for _ in range(steps):
            self.step_dt(dt, "off")

    def step_live(self, shimmer_m: str) -> None:
        now_ts = time.monotonic()
        dt_ms = min(140.0, max(0.0, (now_ts - self.last_ts) * 1000.0))
        self.last_ts = now_ts
        if dt_ms > 0.0:
            self.step_dt(dt_ms, shimmer_m)


def _get_fitted_pixbuf(path: str, target_w: int, target_h: int, fit: str = "fill", align: str = "center"):
    """Scale preview pixbuf according to fit mode ('fill', 'fit', 'center', 'stretch') and vertical crop align."""
    if not path or not os.path.isfile(path) or target_w < 8 or target_h < 8:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    norm_align = align if align in ("top", "center", "bottom") else "center"
    key = (path, mtime, int(target_w), int(target_h), fit, norm_align)
    if key in _PIXBUF_CACHE:
        return _PIXBUF_CACHE[key]
    try:
        src = GdkPixbuf.Pixbuf.new_from_file(path)
        ow, oh = src.get_width(), src.get_height()
        if ow <= 0 or oh <= 0:
            return None
        if fit == "stretch":
            pb = src.scale_simple(target_w, target_h, GdkPixbuf.InterpType.BILINEAR)
            ox, oy = 0, 0
        elif fit == "fit":
            sc = min(target_w / float(ow), target_h / float(oh))
            sw, sh = max(4, int(round(ow * sc))), max(4, int(round(oh * sc)))
            pb = src.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
            ox, oy = (target_w - sw) // 2, (target_h - sh) // 2
        elif fit == "center":
            sc = min(1.0, min(target_w / float(ow), target_h / float(oh)) * 1.1)
            sw, sh = max(4, min(target_w, int(round(ow * sc)))), max(4, min(target_h, int(round(oh * sc))))
            pb = src.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
            ox, oy = (target_w - sw) // 2, (target_h - sh) // 2
        else:
            sc = max(target_w / float(ow), target_h / float(oh))
            sw, sh = max(target_w, int(round(ow * sc))), max(target_h, int(round(oh * sc)))
            scaled = src.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
            crop_x = max(0, (sw - target_w) // 2)
            if norm_align == "top":
                crop_y = 0
            elif norm_align == "bottom":
                crop_y = max(0, sh - target_h)
            else:
                crop_y = max(0, (sh - target_h) // 2)
            pb = scaled.new_subpixbuf(crop_x, crop_y, target_w, target_h)
            ox, oy = 0, 0
        res = (pb, ox, oy)
        if len(_PIXBUF_CACHE) > 48:
            _PIXBUF_CACHE.clear()
        _PIXBUF_CACHE[key] = res
        return res
    except Exception:
        return None


class NiriDesktopPreviewMap(Gtk.EventBox):
    def __init__(
        self,
        outputs_data: dict,
        preview_states: dict,
        selected_monitors: list,
        on_toggle_monitor,
        map_width: int = 840,
        char_w: float = 8.0,
        live_15fps: bool = True,
        max_map_height: int = 230,
        padding: int = 12,
        footer_text: str = "",
        inset_x: int | None = None,
        font_px: float | None = None,
    ):
        super().__init__()
        self.set_visible_window(False)
        self.set_above_child(True)
        self.outputs = outputs_data
        self.preview_states = preview_states
        self.selected_monitors = list(selected_monitors)
        self.on_toggle_monitor = on_toggle_monitor
        self.map_width = max(380, int(map_width))
        self.char_w = max(5.0, float(char_w))
        self.live_15fps = bool(live_15fps)
        self.max_map_height = int(max_map_height)
        self.padding = int(padding)
        self.footer_text = str(footer_text or "").strip()
        self.inset_x = max(14, int(round(self.char_w * 2.0))) if inset_x is None else max(0, int(inset_x))
        self.font_px = max(10.0, float(self.char_w) * 1.45) if font_px is None else max(8.0, float(font_px))
        self.footer_h = max(22, int(round(self.font_px * 2.15))) if self.footer_text else 0
        self.body_h = 126
        self.rects = []
        self._anim_tick = 0
        self._timer_id = None
        self._has_animated = any(
            st.get("mode") in ("ncmatrix", "cmatrix") or len(st.get("frames") or []) > 1
            for st in preview_states.values()
        )

        self.da = Gtk.DrawingArea()
        self.add(self.da)
        self._compute_layout()

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
        )
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", lambda _w, ev: ev.button == 1)
        self.da.connect("draw", self._on_draw)
        self.connect("map", self._on_map)
        self.connect("unmap", self._stop_timer)
        self.connect("destroy", self._stop_timer)

    def _on_map(self, *_args) -> None:
        self._stop_timer()
        if self.live_15fps and self._has_animated and os.environ.get("NC_UI_OFFSCREEN") != "1":
            self._timer_id = GLib.timeout_add(66, self._on_anim_tick)

    def _stop_timer(self, *_args) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _on_anim_tick(self) -> bool:
        if not self.get_mapped() or not self.live_15fps:
            self._timer_id = None
            return False
        self._anim_tick += 1
        self.da.queue_draw()
        return True

    def sync_from(self, other: "NiriDesktopPreviewMap") -> None:
        """Update map state in-place across refreshes so sibling cards/sliders are not destroyed."""
        layout_changed = (
            self.outputs != other.outputs
            or self.map_width != other.map_width
            or self.footer_text != other.footer_text
            or self.max_map_height != other.max_map_height
            or self.padding != other.padding
            or self.inset_x != other.inset_x
        )
        self.outputs = other.outputs
        self.preview_states = other.preview_states
        self.selected_monitors = list(other.selected_monitors)
        self.on_toggle_monitor = other.on_toggle_monitor
        self.map_width = other.map_width
        self.char_w = other.char_w
        self.live_15fps = other.live_15fps
        self.max_map_height = other.max_map_height
        self.padding = other.padding
        self.footer_text = other.footer_text
        self.inset_x = other.inset_x
        self.font_px = other.font_px
        self.footer_h = other.footer_h
        self._has_animated = other._has_animated

        if layout_changed:
            self._compute_layout(resize_widgets=True)

        want_timer = (
            self.live_15fps
            and self._has_animated
            and os.environ.get("NC_UI_OFFSCREEN") != "1"
            and self.get_mapped()
        )
        if want_timer and self._timer_id is None:
            self._timer_id = GLib.timeout_add(66, self._on_anim_tick)
        elif not want_timer:
            self._stop_timer()
        self.da.queue_draw()

    def _compute_layout(self, resize_widgets: bool = True) -> None:
        valid = {k: v for k, v in self.outputs.items() if v.get("logical")}
        inner_w = max(120, self.map_width - (2 * self.inset_x))
        usable_w = max(80, inner_w - (2 * self.padding))
        if not valid:
            self.body_h = 126
            if resize_widgets:
                self.set_size_request(self.map_width, 126 + self.footer_h)
                self.da.set_size_request(self.map_width, 126 + self.footer_h)
            return

        min_x = min(v["logical"]["x"] for v in valid.values())
        min_y = min(v["logical"]["y"] for v in valid.values())
        max_x = max(v["logical"]["x"] + v["logical"]["width"] for v in valid.values())
        max_y = max(v["logical"]["y"] + v["logical"]["height"] for v in valid.values())
        log_w = max(1, max_x - min_x)
        log_h = max(1, max_y - min_y)

        scale_w = usable_w / float(max(log_w, 2560))
        scale_h = (self.max_map_height - (2 * self.padding)) / float(log_h)
        scale = min(scale_w, scale_h)

        rel_rects = []
        for out_name in sorted(valid.keys()):
            ld = valid[out_name]["logical"]
            rw = max(84, int(round(ld["width"] * scale)))
            rh = max(56, int(round(ld["height"] * scale)))
            rx = int(round((ld["x"] - min_x) * scale))
            ry = int(round((ld["y"] - min_y) * scale))
            rel_rects.append((out_name, rx, ry, rw, rh, ld))

        min_rx = min(rx for _n, rx, _ry, _rw, _rh, _ld in rel_rects)
        min_ry = min(ry for _n, _rx, ry, _rw, _rh, _ld in rel_rects)
        used_w = max(rx + rw for _n, rx, _ry, rw, _rh, _ld in rel_rects) - min_rx
        used_h = max(ry + rh for _n, _rx, ry, _rw, rh, _ld in rel_rects) - min_ry
        body_h = max(126, int(round(used_h)) + 2 * self.padding)
        self.body_h = body_h
        map_h = body_h + self.footer_h

        x_off = self.inset_x + max(float(self.padding), (inner_w - used_w) / 2.0) - min_rx
        y_off = max(float(self.padding), (body_h - used_h) / 2.0) - min_ry

        if resize_widgets:
            self.set_size_request(self.map_width, map_h)
            self.da.set_size_request(self.map_width, map_h)

        self.rects = [
            (out_name, int(round(x_off + rx)), int(round(y_off + ry)), rw, rh, ld)
            for out_name, rx, ry, rw, rh, ld in rel_rects
        ]

    def _emit_toggle(self, out_name: str) -> bool:
        if self.on_toggle_monitor:
            self.on_toggle_monitor(out_name)
        return False

    def _on_press(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        for out_name, rx, ry, rw, rh, _ld in self.rects:
            if rx <= event.x <= (rx + rw) and ry <= event.y <= (ry + rh):
                if self.on_toggle_monitor:
                    GLib.idle_add(self._emit_toggle, out_name)
                return True
        return False

    def _draw_ncmatrix_bg(self, cr, out_name: str, rx: int, ry: int, rw: int, rh: int, ld: dict, st: dict) -> None:
        cr.set_source_rgb(0x05 / 255.0, 0x06 / 255.0, 0x09 / 255.0)
        cr.paint()

        col_raw = str(st.get("color", "green") or "green").strip().lower()
        is_rainbow = (col_raw == "rainbow")
        if is_rainbow:
            rgb = _MATRIX_RGB["green"]
        elif col_raw.startswith("#") and len(col_raw) == 7:
            rgb = hex_to_rgba_f(col_raw, fallback=_MATRIX_RGB["green"])[:3]
        else:
            rgb = _MATRIX_RGB.get(col_raw, _MATRIX_RGB["green"])
        charset = str(st.get("charset", "katakana"))
        try:
            spd = max(1, min(10, int(st.get("speed", "5"))))
        except ValueError:
            spd = 5
        raw_sr = st.get("spawn_rate")
        try:
            spawn_rate = max(0.0, min(3.0, float("1.5" if raw_sr is None else raw_sr)))
        except (TypeError, ValueError):
            spawn_rate = 1.5
        if spawn_rate <= 0.001:
            return
        head_mode = str(st.get("head_color", "blend") or "blend").strip().lower()
        if head_mode not in ("white", "match", "blend"):
            head_mode = "blend"
        try:
            spd_var = max(0.0, min(10.0, float(st.get("speed_variance", "5.0") or 5.0)))
        except (TypeError, ValueError):
            spd_var = 5.0
        overlap_m = str(st.get("overlap_mode", "hybrid") or "hybrid").strip().lower()
        try:
            parallax_str = max(0.0, min(1.0, float(_fmt_cm_parallax(st.get("depth_parallax", "0.50")))))
        except (TypeError, ValueError):
            parallax_str = 0.50
        try:
            glow_v = max(0.0, min(1.0, float(st.get("glow", "0.60") or 0.60)))
        except (TypeError, ValueError):
            glow_v = 0.60
        decay_m = str(st.get("decay_mode", "exponential") or "exponential").strip().lower()
        shimmer_m = str(st.get("shimmer", "insane") or "insane").strip().lower()
        mirror_m = str(st.get("mirror", "random") or "random").strip().lower()
        tail_m = str(st.get("tail_length", "long") or "long").strip().lower()
        is_dense = str(st.get("col_density", "dense") or "dense").strip().lower() == "dense"

        try:
            fsize = max(5, min(32, int(st.get("size", "9") or 9)))
        except ValueError:
            fsize = 9

        atlas = _get_ncm_glyph_atlas(charset, fsize)
        cw = atlas["cell_w"]
        ch = atlas["cell_h"]
        step_x = cw if is_dense else (cw * 2)
        n_active = max(4, (rw - 4) // step_x)
        rows = max(6, (rh - 4) // ch)
        phys_h = max(360, int(ld.get("height", 1440) or 1440))
        real_cell_h = max(7.0, fsize * 1.95)
        real_rows = max(14.0, phys_h / real_cell_h)
        cfg_t = (spd, spd_var, parallax_str, spawn_rate, tail_m, charset)

        sim = _NCM_SIM_CACHE.get(out_name)
        if sim is None or sim.n_active != n_active or sim.rows != rows or sim.is_dense != is_dense:
            sim = _MiniMatrixSim(out_name, n_active, rows, is_dense, atlas["n"], real_rows, cfg_t)
            if len(_NCM_SIM_CACHE) >= 16:
                _NCM_SIM_CACHE.clear()
            _NCM_SIM_CACHE[out_name] = sim
        else:
            sim.sync_config(cfg_t, atlas["n"], real_rows)
            if self.live_15fps and os.environ.get("NC_UI_OFFSCREEN") != "1":
                sim.step_live(shimmer_m)
            else:
                sim.last_ts = time.monotonic()

        decay_lut = _NCM_EXP_DECAY if decay_m == "exponential" else _NCM_LIN_DECAY
        surf_norm = atlas["normal"]
        surf_mir = atlas["mirrored"]
        surf_halo = atlas["halo"]
        ng = atlas["n"]
        ox0 = rx + 2 + max(0, ((rw - 4) - n_active * step_x) // 2)
        oy0 = ry + 2
        cr_r0, cr_g0, cr_b0 = rgb

        for k in range(n_active):
            col_tr = sim.cols[k]
            if not col_tr:
                continue
            gx = ox0 + k * step_x

            row_gi = [0] * rows
            row_fade = [0.0] * rows
            row_wh = [0.0] * rows
            row_prio = [-1.0] * rows
            row_cid = [0] * rows
            row_is_hd = [False] * rows

            for tr in col_tr:
                hr = tr[0]
                tlen = tr[1]
                r_end = hr if hr < rows else (rows - 1)
                r_start = hr - tlen
                if r_start < 0:
                    r_start = 0
                if r_start > r_end:
                    continue
                inv_spd = max(1.0, 1000.0 - tr[2])
                bright = tr[6]
                head_mix = tr[5]
                c_seed_tr = tr[7]
                tg = tr[8]
                denom = tlen + 1

                for r in range(r_start, r_end + 1):
                    dist = hr - r
                    is_hd = (dist == 0)
                    if is_hd:
                        d_a = bright
                    else:
                        li = (dist * 255) // denom
                        if li > 255:
                            li = 255
                        d_a = decay_lut[li] * bright

                    wh_mix = 0.0
                    if is_hd:
                        if head_mode == "white":
                            wh_mix = 1.0
                        elif head_mode == "blend":
                            s_par = parallax_str * 2.0
                            min_br = (28.0 / 255.0) if s_par <= 1.0 else ((28.0 - (s_par - 1.0) * 16.0) / 255.0)
                            lum_n = max(0.0, min(1.0, (bright - min_br) / (1.0 - min_br)))
                            range_f = (lum_n * lum_n) if s_par <= 1.0 else (lum_n ** (1.0 + s_par))
                            wh_mix = (head_mix / 255.0) * range_f

                    gi = tg[r] % ng
                    cur_f = row_fade[r]
                    if row_prio[r] < 0.0:
                        row_gi[r] = gi
                        row_fade[r] = d_a
                        row_wh[r] = wh_mix
                        row_cid[r] = c_seed_tr
                        row_is_hd[r] = is_hd
                        row_prio[r] = (inv_spd * 256.0 + d_a) if overlap_m == "parallax" else ((1048576.0 if is_hd else 0.0) + inv_spd * 256.0 + d_a)
                    elif overlap_m == "parallax":
                        prio = inv_spd * 256.0 + d_a
                        if prio >= row_prio[r]:
                            row_prio[r] = prio
                            row_gi[r] = gi
                            row_fade[r] = d_a
                            row_wh[r] = wh_mix
                            row_cid[r] = c_seed_tr
                            row_is_hd[r] = is_hd
                    elif overlap_m == "additive":
                        scr = cur_f + d_a - cur_f * d_a
                        comb = min(1.0, scr + 0.25 * cur_f * d_a)
                        c_wh = (cur_f * d_a) * 0.25
                        if d_a >= cur_f or is_hd:
                            row_gi[r] = gi
                            row_cid[r] = c_seed_tr
                            row_is_hd[r] = is_hd
                            row_wh[r] = min(1.0, wh_mix + c_wh)
                        else:
                            row_wh[r] = min(1.0, row_wh[r] + c_wh)
                        row_fade[r] = comb
                    else:
                        scr = min(1.0, cur_f + d_a - cur_f * d_a)
                        prio = (1048576.0 if is_hd else 0.0) + inv_spd * 256.0 + d_a
                        if prio >= row_prio[r]:
                            row_prio[r] = prio
                            row_gi[r] = gi
                            row_wh[r] = wh_mix
                            row_cid[r] = c_seed_tr
                            row_is_hd[r] = is_hd
                        row_fade[r] = scr

            for r in range(rows):
                fade = row_fade[r]
                if fade <= 0.015:
                    continue
                gi = row_gi[r]
                gy = oy0 + r * ch

                if is_rainbow:
                    hv = (r * 131 + row_cid[r] * 37 + k * 17) & 0xFFFF
                    c_seed = hashlib.md5(f"rb:{hv}".encode("utf-8")).digest()
                    cr_r, cr_g, cr_b = _vivid_rainbow_rgb(c_seed)
                else:
                    cr_r, cr_g, cr_b = cr_r0, cr_g0, cr_b0

                wh_mix = row_wh[r]
                if glow_v > 0.05 and fade > 0.14:
                    halo_a = min(1.0, fade * glow_v * (0.60 + 0.50 * fade))
                    if row_is_hd[r] and head_mode == "white":
                        hr_r, hr_g, hr_b = 0.95, 1.0, 0.97
                    elif wh_mix > 0.0:
                        bm = min(1.0, wh_mix + 0.18 * halo_a)
                        hr_r = cr_r + (0.95 - cr_r) * bm
                        hr_g = cr_g + (1.00 - cr_g) * bm
                        hr_b = cr_b + (0.97 - cr_b) * bm
                    else:
                        hr_r, hr_g, hr_b = cr_r, cr_g, cr_b
                    cr.set_source_rgba(hr_r, hr_g, hr_b, halo_a)
                    cr.mask_surface(surf_halo[gi], gx - 2, gy - 2)

                if row_is_hd[r] and head_mode == "white":
                    fg_r, fg_g, fg_b = 0.95, 1.0, 0.97
                elif wh_mix > 0.0:
                    fg_r = cr_r + (0.95 - cr_r) * wh_mix
                    fg_g = cr_g + (1.00 - cr_g) * wh_mix
                    fg_b = cr_b + (0.97 - cr_b) * wh_mix
                else:
                    fg_r, fg_g, fg_b = cr_r, cr_g, cr_b

                do_mir = (mirror_m == "on") or (mirror_m == "random" and (((gi * 2654435761 ^ (r * 131 + k * 17)) & 1) == 1))
                cr.set_source_rgba(fg_r, fg_g, fg_b, min(1.0, fade))
                cr.mask_surface(surf_mir[gi] if do_mir else surf_norm[gi], gx, gy)

    def _draw_monitor_hud(self, cr, out_name: str, rx: int, ry: int, rw: int, rh: int, ld: dict, st: dict, is_selected: bool = True) -> None:
        is_staged = bool(st.get("staged"))

        badge_txt = t("cc_personalization.wp_badge_preview") if is_staged else t("cc_personalization.wp_badge_live")
        b_rgb = (1.0, 0.92, 0.10) if is_staged else (0.0, 1.0, 0.45)
        b_bg = (0.18, 0.15, 0.02, 0.90) if is_staged else (0.02, 0.14, 0.08, 0.88)

        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        cr.select_font_face("monospace", 0, 1)
        cr.set_font_size(base_pt * 1.00)
        b_ext = cr.text_extents(badge_txt)
        bw_px = b_ext.width + 12
        bh_px = 18
        bx = rx + rw - bw_px - 6
        by = ry + 6
        rounded_rect_path(cr, bx, by, bw_px, bh_px, 3.0)
        cr.set_source_rgba(*b_bg)
        cr.fill_preserve()
        cr.set_source_rgba(b_rgb[0], b_rgb[1], b_rgb[2], 0.88)
        cr.set_line_width(1.0)
        cr.stroke()
        cr.set_source_rgb(*b_rgb)
        cr.move_to(bx + 6, by + 13.0)
        cr.show_text(badge_txt)

        mode = st.get("mode", "none")
        asc_on = bool((st.get("asciify") or {}).get("enabled"))
        raw_target = str(st.get("target") or st.get("image_path") or "").strip()
        if raw_target == "?":
            raw_target = ""
        if mode == "scene" and raw_target:
            kind = st.get("media_kind") or detect_media_kind(raw_target) or "IMG"
            yt_s = _resolve_yt_source(raw_target)
            if yt_s.get("ok"):
                base = yt_s["playlist_id"] if yt_s["is_playlist"] else yt_s["video_id"]
            else:
                base = os.path.basename(raw_target.rstrip("/")) or raw_target
            tag = f"IMG·ASCII: {base}" if (asc_on and kind == "IMAGE") else f"{kind}: {base}"
            tag_rgb = (1.0, 0.92, 0.10) if is_staged else (0.0, 1.0, 0.45)
        elif mode == "slideshow" and raw_target:
            slide_base = os.path.basename(raw_target.rstrip("/")) or "dir"
            tag = t("cc_personalization.desktop_mode_slide_ascii" if asc_on else "cc_personalization.desktop_mode_slide", p0=slide_base)
            tag_rgb = (0.0, 0.94, 1.0)
        elif mode in ("ncmatrix", "cmatrix"):
            cs = st.get("charset", "katakana").upper()[:4]
            col_s = str(st.get("color", "green") or "green").strip()
            col_low = col_s.lower()
            raw_hud_sr = st.get("spawn_rate")
            try:
                hud_sr = max(0.0, min(5.0, float("1.0" if raw_hud_sr is None else raw_hud_sr)))
            except (TypeError, ValueError):
                hud_sr = 1.0
            sr_suf = f"·{hud_sr:.1f}X" if abs(hud_sr - 1.0) > 0.05 else ""
            tag = t("cc_personalization.desktop_mode_matrix", p0=f"{cs}·{col_s.upper()}·{st.get('size', '9')}PT{sr_suf}")
            if col_low == "rainbow":
                tag_rgb = (0.0, 0.94, 1.0)
            elif col_low.startswith("#"):
                tr, tg, tb = hex_to_rgba_f(col_low, fallback=_MATRIX_RGB["green"])[:3]
                tag_rgb = (0.88, 0.92, 0.99) if max(tr, tg, tb) < 0.25 else (tr, tg, tb)
            else:
                tag_rgb = _MATRIX_RGB.get(col_low, _MATRIX_RGB["green"])
        elif mode == "solid":
            tag = t("cc_personalization.desktop_mode_solid", p0=st.get("hex", "#18181a"))
            tag_rgb = (0.85, 0.90, 1.0)
        else:
            tag = t("cc_personalization.desktop_mode_none")
            tag_rgb = hex_to_rgba_f(THEME["colors"].get("schema", "#8c9bba"), fallback=(0.55, 0.61, 0.73))[:3]

        hud_h = max(25, min(30, int(rh * 0.26)))
        cr.set_source_rgba(0.03, 0.04, 0.07, 0.86)
        cr.rectangle(rx, ry + rh - hud_h, rw, hud_h)
        cr.fill()

        cr.set_font_size(base_pt * 1.12)
        if is_selected:
            cr.set_source_rgb(1.0, 1.0, 1.0)
        else:
            c_unsel = hex_to_rgba_f(THEME["colors"].get("schema", "#8c9bba"), fallback=(0.55, 0.61, 0.73))[:3]
            cr.set_source_rgb(*c_unsel)
        mon_txt = f"{'●' if is_selected else '○'} {out_name} {ld.get('width', 0)}x{ld.get('height', 0)}"
        left_ext = cr.text_extents(mon_txt)
        cr.move_to(rx + 8, ry + rh - (hud_h / 2.0) + 4.0)
        cr.show_text(mon_txt)

        avail_right = rw - left_ext.width - 22
        if avail_right >= 48:
            cr.set_font_size(base_pt * 1.02)
            ext = cr.text_extents(tag)
            while ext.width > avail_right and len(tag) > 4:
                tag = tag[:-2] + "…"
                ext = cr.text_extents(tag)
            cr.set_source_rgb(*tag_rgb)
            cr.move_to(rx + rw - ext.width - 8, ry + rh - (hud_h / 2.0) + 3.2)
            cr.show_text(tag)

    def _on_draw(self, da, cr) -> bool:
        alloc = da.get_allocation()
        ref_lbl = getattr(self, "_ref_label", None)
        ref_lay = ref_lbl.get_layout() if ref_lbl is not None else None
        box_w = ref_lay.get_pixel_size()[0] if ref_lay is not None else 0
        alloc_w = box_w if box_w > 0 else max(self.map_width, alloc.width)
        alloc_h = max(120, alloc.height)
        if abs(int(round(alloc_w)) - self.map_width) > 2:
            self.map_width = int(round(alloc_w))
            self._compute_layout(resize_widgets=False)

        pal = get_semantic_palette()
        c_sbg = hex_to_rgba_f(pal["structure_bg"], fallback=(0.10, 0.11, 0.14))[:3]
        c_struct_rgb = hex_to_rgba_f(pal["structure"], fallback=(0.26, 0.29, 0.36))[:3]
        c_sch_rgb = hex_to_rgba_f(pal["schema"], fallback=(0.55, 0.61, 0.73))[:3]
        c_vit = hex_to_rgba_f(THEME["colors"].get("vitality", "#00ff73"), fallback=(0.0, 1.0, 0.45))[:3]
        c_attn = hex_to_rgba_f(THEME["colors"].get("attention", "#ffeb19"), fallback=(1.0, 0.92, 0.10))[:3]

        bx = float(self.inset_x)
        bw = max(60.0, float(alloc_w - (2 * self.inset_x)))
        bh = float(alloc_h)
        canvas_h = max(72.0, bh - float(self.footer_h))

        # Recessed nested container matching Display Topology (zero dot-grid)
        rounded_rect_path(cr, bx + 0.5, 0.5, bw - 1.0, bh - 1.0, 6.0)
        cr.set_source_rgba(c_sbg[0], c_sbg[1], c_sbg[2], 0.65)
        cr.fill_preserve()
        cr.set_source_rgba(c_struct_rgb[0], c_struct_rgb[1], c_struct_rgb[2], 0.85)
        cr.set_line_width(1.0)
        cr.stroke()

        cr.save()
        rounded_rect_path(cr, bx + 1.0, 1.0, bw - 2.0, bh - 2.0, 5.5)
        cr.clip()

        for out_name, rx, ry, rw, rh, ld in self.rects:
            st = self.preview_states.get(out_name, {"mode": "none"})
            mode = st.get("mode", "none")
            is_selected = out_name in self.selected_monitors
            is_staged = bool(st.get("staged"))

            cr.save()
            rounded_rect_path(cr, rx, ry, rw, rh, 8.0)
            cr.clip()

            if mode in ("scene", "slideshow"):
                frames = st.get("frames") or []
                frame_path = frames[(self._anim_tick // 4) % len(frames)] if (self.live_15fps and frames) else st.get("image_path")
                cr.set_source_rgb(0.05, 0.06, 0.09)
                cr.paint()
                fitted = _get_fitted_pixbuf(frame_path, rw, rh, st.get("fit", "fill"), st.get("align", "center"))
                if fitted is not None:
                    pb, ox, oy = fitted
                    Gdk.cairo_set_source_pixbuf(cr, pb, rx + ox, ry + oy)
                    cr.paint()
            elif mode == "solid":
                sr, sg, sb = hex_to_rgba_f(st.get("hex", "#18181a"), fallback=(0.09, 0.09, 0.10))[:3]
                cr.set_source_rgb(sr, sg, sb)
                cr.paint()
            elif mode in ("ncmatrix", "cmatrix"):
                self._draw_ncmatrix_bg(cr, out_name, rx, ry, rw, rh, ld, st)
            else:
                cr.set_source_rgb(0.04, 0.05, 0.08)
                cr.paint()

            if not is_selected:
                cr.set_source_rgba(0.02, 0.03, 0.05, 0.42)
                cr.paint()

            self._draw_monitor_hud(cr, out_name, rx, ry, rw, rh, ld, st, is_selected=is_selected)
            cr.restore()

            rounded_rect_path(cr, rx + 1.0, ry + 1.0, rw - 2.0, rh - 2.0, 8.0)
            if is_staged:
                cr.set_source_rgba(c_attn[0], c_attn[1], c_attn[2], 0.98)
                cr.set_line_width(2.4)
            elif is_selected:
                cr.set_source_rgba(c_vit[0], c_vit[1], c_vit[2], 0.95)
                cr.set_line_width(2.4)
            else:
                cr.set_source_rgba(c_struct_rgb[0], c_struct_rgb[1], c_struct_rgb[2], 0.90)
                cr.set_line_width(1.5)
            cr.stroke()

        if self.footer_text and self.footer_h > 0:
            fh = bh - canvas_h
            cr.set_source_rgba(12 / 255.0, 15 / 255.0, 22 / 255.0, 0.55)
            cr.rectangle(bx + 1.0, canvas_h, bw - 2.0, fh)
            cr.fill()
            cr.set_source_rgba(c_struct_rgb[0], c_struct_rgb[1], c_struct_rgb[2], 0.72)
            cr.set_line_width(1.0)
            cr.move_to(bx + 1.0, canvas_h + 0.5)
            cr.line_to(bx + bw - 1.0, canvas_h + 0.5)
            cr.stroke()

            font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")
            base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
            sub_sz = int(round(base_pt * 0.86 * Pango.SCALE))
            fdesc = Pango.FontDescription()
            fdesc.set_family(font_family)
            fdesc.set_size(sub_sz)
            fpad = max(10.0, round(self.font_px * 0.9))
            fl = PangoCairo.create_layout(cr)
            fl.set_font_description(fdesc)
            fl.set_ellipsize(Pango.EllipsizeMode.END)
            fl.set_width(int(max(40.0, bw - 2.0 * fpad) * Pango.SCALE))
            fl.set_text(self.footer_text, -1)
            _, fl_ext = fl.get_pixel_extents()
            fty = canvas_h + max(2.0, (fh - fl_ext.height) / 2.0)
            cr.set_source_rgba(c_sch_rgb[0], c_sch_rgb[1], c_sch_rgb[2], 0.88)
            cr.move_to(bx + fpad, fty)
            PangoCairo.show_layout(cr, fl)

        cr.restore()
        return False


class EmbeddedYaziWidget(Gtk.EventBox):
    def __init__(self, session, map_width: int, char_w: float, cell_h: float, border_hex: str):
        super().__init__()
        self.set_visible_window(False)
        self.set_above_child(True)
        self.session = session
        self.map_width = max(380, int(map_width))
        self.char_w = max(5.0, float(char_w))
        self.cell_h = max(14.0, float(cell_h))
        self.border_hex = border_hex
        self._last_geom = (self.char_w * 2.5, 4.0, self.char_w, self.cell_h)
        self._pix_cache = (None, 0, 0, None)

        total_h = int(round(4.0 + self.session.rows * self.cell_h + 4.0))
        self.da = Gtk.DrawingArea()
        self.da.set_size_request(self.map_width, total_h)
        self.set_size_request(self.map_width, total_h)
        self.add(self.da)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.connect("button-press-event", self._on_button)
        self.connect("button-release-event", self._on_button)
        self.connect("scroll-event", self._on_scroll)
        self.da.connect("draw", self._on_draw)
        self._redraw_cb = lambda: self.da.queue_draw()
        self.session.on_redraw = self._redraw_cb
        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_args) -> None:
        if getattr(self.session, "on_redraw", None) is self._redraw_cb:
            self.session.on_redraw = None

    def set_border_color(self, border_hex: str) -> None:
        if border_hex and border_hex != self.border_hex:
            self.border_hex = border_hex
            self.da.queue_draw()

    def _xy_to_cell(self, ex: float, ey: float):
        x0, y0, cw, ch = self._last_geom
        cols, rows = self.session.cols, self.session.rows
        if x0 <= ex <= (x0 + cols * cw) and y0 <= ey <= (y0 + rows * ch):
            c = max(1, min(cols, int((ex - x0) / cw) + 1))
            r = max(1, min(rows, int((ey - y0) / ch) + 1))
            return c, r
        return None

    def _on_button(self, _w, ev) -> bool:
        cell = self._xy_to_cell(ev.x, ev.y)
        if cell is None or ev.button not in (1, 2, 3):
            return False
        btn = {1: 0, 2: 1, 3: 2}[ev.button]
        kind = "M" if ev.type == Gdk.EventType.BUTTON_PRESS else "m"
        self.session.send_bytes(f"\x1b[<{btn};{cell[0]};{cell[1]}{kind}".encode("ascii"))
        return True

    def _on_scroll(self, _w, ev) -> bool:
        cell = self._xy_to_cell(ev.x, ev.y)
        if cell is None:
            return False
        btn = None
        if ev.direction == Gdk.ScrollDirection.UP:
            btn = 64
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            btn = 65
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            ok_d, _dx, dy = ev.get_scroll_deltas()
            if ok_d and abs(dy) >= 0.05:
                btn = 64 if dy < 0 else 65
        if btn is not None:
            self.session.send_bytes(f"\x1b[<{btn};{cell[0]};{cell[1]}M".encode("ascii"))
            return True
        return False

    def _on_draw(self, da, cr) -> bool:
        alloc = da.get_allocation()
        ref_lbl = getattr(self, "_ref_label", None)
        ref_lay = ref_lbl.get_layout() if ref_lbl is not None else None
        box_w = ref_lay.get_pixel_size()[0] if ref_lay is not None else 0
        alloc_w = box_w if box_w > 0 else max(self.map_width, alloc.width)
        if abs(int(round(alloc_w)) - self.map_width) > 2:
            self.map_width = int(round(alloc_w))

        x0 = self.char_w * 2.5
        y0 = 4.0
        term_w = max(200.0, (alloc_w - self.char_w * 2.5) - x0)
        ch = self.cell_h

        with self.session.lock:
            rows = self.session.rows
            cols = self.session.cols
            cw = term_w / float(max(1, cols))
            self._last_geom = (x0, y0, cw, ch)
            grid_snap = [[tuple(cell) for cell in row] for row in self.session.grid]
            pix = self.session.preview_pixbuf

        p = get_semantic_palette()
        def_fg = hex_to_rgba_f(p["focus_val"], fallback=(0.89, 0.92, 0.99))[:3]
        def_bg = hex_to_rgba_f(p["structure_bg"], fallback=(0.10, 0.11, 0.14))[:3]
        typo = THEME["typography"]
        desc_reg = Pango.FontDescription.from_string(
            f"{typo['font_family']}, Font Awesome 6 Free, Font Awesome 6 Brands, Symbols Nerd Font, monospace {typo['font_weight']} {typo['font_size']}"
        )
        desc_bold = Pango.FontDescription.from_string(
            f"{typo['font_family']}, Font Awesome 6 Free, Font Awesome 6 Brands, Symbols Nerd Font, monospace bold {typo['font_size']}"
        )
        layout = PangoCairo.create_layout(cr)

        img_bounds = [rows, -1, cols, -1]
        for r, row in enumerate(grid_snap):
            ry = y0 + r * ch
            c = 0
            while c < cols:
                txt, fg, bg, bold, rev, is_img = row[c]
                if is_img:
                    img_bounds[0] = min(img_bounds[0], r)
                    img_bounds[1] = max(img_bounds[1], r)
                    img_bounds[2] = min(img_bounds[2], c)
                    img_bounds[3] = max(img_bounds[3], c)
                eff_bg = (fg or def_fg) if rev else bg
                if eff_bg is not None and not is_img:
                    c2 = c + 1
                    while c2 < cols:
                        t2 = row[c2]
                        eb2 = (t2[1] or def_fg) if t2[4] else t2[2]
                        if eb2 != eff_bg or t2[5]:
                            break
                        c2 += 1
                    rounded_rect_path(cr, x0 + c * cw, ry + 1.0, (c2 - c) * cw, max(2.0, ch - 2.0), 2.5)
                    cr.set_source_rgba(eff_bg[0], eff_bg[1], eff_bg[2], 0.92)
                    cr.fill()
                    c = c2
                else:
                    c += 1

        for r, row in enumerate(grid_snap):
            ry = y0 + r * ch
            c = 0
            while c < cols:
                txt, fg, bg, bold, rev, is_img = row[c]
                if is_img or not txt or txt == " ":
                    c += 1
                    continue
                eff_fg = (bg or def_bg) if rev else (fg or def_fg)
                layout.set_font_description(desc_bold if bold else desc_reg)
                cr.set_source_rgb(*eff_fg)
                if 32 < ord(txt) < 127:
                    run = [txt]
                    c2 = c + 1
                    while c2 < cols:
                        t2 = row[c2]
                        ef2 = (t2[2] or def_bg) if t2[4] else (t2[1] or def_fg)
                        if t2[5] or not t2[0] or not (32 <= ord(t2[0]) < 127) or ef2 != eff_fg or t2[3] != bold:
                            break
                        run.append(t2[0])
                        c2 += 1
                    layout.set_text("".join(run), -1)
                    cr.move_to(x0 + c * cw, ry)
                    PangoCairo.show_layout(cr, layout)
                    c = c2
                else:
                    layout.set_text(txt, -1)
                    cr.move_to(x0 + c * cw, ry)
                    PangoCairo.show_layout(cr, layout)
                    c += 1

        if pix is not None and img_bounds[1] >= img_bounds[0] and img_bounds[3] >= img_bounds[2]:
            ix = x0 + img_bounds[2] * cw
            iy = y0 + img_bounds[0] * ch
            iw = max(8.0, (img_bounds[3] - img_bounds[2] + 1) * cw)
            ih = max(8.0, (img_bounds[1] - img_bounds[0] + 1) * ch)
            pw, ph = max(1, pix.get_width()), max(1, pix.get_height())
            sc = min(iw / float(pw), ih / float(ph))
            dw, dh = max(4, int(round(pw * sc))), max(4, int(round(ph * sc)))
            cached_src, cw_c, ch_c, cached_pb = self._pix_cache
            if cached_src is not pix or cw_c != dw or ch_c != dh or cached_pb is None:
                cached_pb = pix.scale_simple(dw, dh, GdkPixbuf.InterpType.BILINEAR)
                self._pix_cache = (pix, dw, dh, cached_pb)
            if cached_pb is not None:
                ox = ix + (iw - dw) / 2.0
                oy = iy + (ih - dh) / 2.0
                Gdk.cairo_set_source_pixbuf(cr, cached_pb, ox, oy)
                cr.paint()

        return False


def _add_asciify_collapsible(cfg_box: Box, cc_panel) -> None:
    if not is_pillow_available():
        cfg_box.add_section(t("cc_personalization.wp_sec_asciify"))
        cfg_box.add_row(" ")
        cfg_box.add_kv(
            t("cc_personalization.wp_style_row"),
            Badge(t("cc_personalization.wp_ascii_missing_badge"), style="warn"),
        )
        cfg_box.add_row(" ")
        return

    asc = get_editor_asciify_cfg()
    mode_lbl = t(f"cc_personalization.wp_ascii_mode_{asc['mode']}")
    chars_lbl = t(f"cc_personalization.wp_ascii_chars_{asc['chars']}") if asc["chars"] in CHAR_SETS else t("cc_personalization.wp_color_custom")
    dens_txt = f"{asc['density']:.2f}x"
    tint_txt = f"{int(round(asc['tint'] * 100))}%"
    scale_txt = f"{asc['scale']:.2f}x"
    contrast_txt = f"{asc['contrast']:.1f}x"
    bright_txt = f"{asc['brightness']:.1f}x"

    summary = t(
        "cc_personalization.wp_ascii_summary",
        p0=mode_lbl,
        p1=chars_lbl,
        p2=dens_txt,
        p3=tint_txt,
        p4=scale_txt,
    )

    def _build_all(b: Box) -> None:
        b.add_row(" ")
        mode_pills = " ".join(
            SelectPill(
                t(f"cc_personalization.wp_ascii_mode_{cm}"),
                f"cc_desk:ascii_mode:{cm}",
                selected=(asc["mode"] == cm),
                style="secondary",
            )
            for cm in COLOR_MODES
        )
        b.add_kv(t("cc_personalization.wp_ascii_mode_row"), mode_pills)

        is_custom_chars = asc["chars"] not in CHAR_SETS
        disp_custom = ""
        if is_custom_chars:
            raw_c = str(asc["chars"])
            disp_custom = raw_c if len(raw_c) <= 24 else (raw_c[:22] + "..")
        char_pills = " ".join(
            SelectPill(
                t(f"cc_personalization.wp_ascii_chars_{cs}"),
                f"cc_desk:ascii_chars:{cs}",
                selected=(asc["chars"] == cs),
                style="secondary",
            )
            for cs in ("standard", "binary", "katakana", "kanjis")
        )
        b.add_kv(t("cc_personalization.wp_ascii_chars_row"), char_pills)

        custom_field = InlineField(
            "cc_desk_ascii_chars",
            "cc_desk:inline_ascii_chars",
            value=disp_custom,
            empty_label=t("cc_personalization.wp_ascii_custom_chars"),
            placeholder=" .:-=+*#%@",
            width=22,
            icon="\u270e",
            tone="neutral",
            selected=is_custom_chars,
        )
        b.add_kv(t("cc_personalization.wp_ascii_custom_row"), custom_field)

        dens_slider = Slider(asc["density"], "cc_desk:ascii_density", min_val=0.25, max_val=2.5, width=20, show_pct=False, step=0.25)
        b.add_kv(t("cc_personalization.wp_ascii_density_row"), f"{dens_slider} {Txt(dens_txt, role='val')}")

        tint_slider = Slider(asc["tint"], "cc_desk:ascii_tint", min_val=0.0, max_val=1.0, width=20, show_pct=False, step=0.05)
        b.add_kv(t("cc_personalization.wp_ascii_tint_row"), f"{tint_slider} {Txt(tint_txt, role='val')}")

        sc_slider = Slider(asc["scale"], "cc_desk:ascii_scale", min_val=0.25, max_val=1.5, width=20, show_pct=False, step=0.25)
        b.add_kv(t("cc_personalization.wp_ascii_scale_row"), f"{sc_slider} {Txt(scale_txt, role='val')}")

        ct_slider = Slider(asc["contrast"], "cc_desk:ascii_contrast", min_val=0.5, max_val=2.0, width=20, show_pct=False, step=0.1)
        b.add_kv(t("cc_personalization.wp_ascii_contrast_row"), f"{ct_slider} {Txt(contrast_txt, role='val')}")

        br_slider = Slider(asc["brightness"], "cc_desk:ascii_brightness", min_val=0.5, max_val=2.0, width=20, show_pct=False, step=0.1)
        b.add_kv(t("cc_personalization.wp_ascii_brightness_row"), f"{br_slider} {Txt(bright_txt, role='val')}")

        inv_pills = " ".join([
            SelectPill(t("cc_personalization.wp_ascii_normal"), "cc_desk:ascii_invert:0", selected=(not asc["invert"]), style="secondary"),
            SelectPill(t("cc_personalization.wp_ascii_inverted"), "cc_desk:ascii_invert:1", selected=asc["invert"], style="secondary"),
        ])
        b.add_kv(t("cc_personalization.wp_ascii_invert_row"), inv_pills)

        n_files, cache_bytes = get_ascii_cache_stats()
        size_str = f"{cache_bytes / (1024 * 1024):.1f} MiB" if cache_bytes >= 1024 * 1024 else f"{max(0, cache_bytes // 1024)} KiB"
        clear_cache_btn = ActionBtn(t("cc_personalization.wp_ascii_clear_cache"), "cc_desk:ascii_clear_cache", tone="danger_soft") if n_files > 0 else ""
        b.add_kv(
            t("cc_personalization.wp_ascii_cache_row"),
            Txt(t("cc_personalization.wp_ascii_cache_info", p0=n_files, p1=size_str), role="schema"),
            right_markup=clear_cache_btn,
        )

    cfg_box.add_collapsible(
        cc_panel,
        "wp_ascii_cfg",
        t("cc_personalization.wp_sec_asciify"),
        _build_all,
        actions=[ActionBtn(t("cc_personalization.wp_ascii_defaults_btn"), "cc_desk:ascii_defaults", tone="warn")],
        summary=summary,
    )
    cfg_box.add_row(" ")


def _build_wallpaper_standard(cc_panel, canvas: Canvas) -> list:
    outputs_data = _get_niri_outputs()
    out_names = sorted(outputs_data.keys())
    sync_editor_from_live_monitors(outputs_data)

    sel_mons = get_selected_monitors(out_names)
    all_selected = (len(out_names) > 0 and len(sel_mons) == len(out_names))
    live_15fps = is_live_15fps_enabled()

    cards = _feedback_cards()
    preview_states = get_desktop_preview_state(outputs_data, cc_panel=cc_panel)
    has_staged = any(bool(st.get("staged")) for st in preview_states.values())
    ascii_status = get_ascii_apply_status()

    # CARD 1: Unified Framed Monitor Map & Live 15 FPS Mini-Engine
    fps_lbl = t("cc_personalization.wp_live_15fps" if live_15fps else "cc_personalization.wp_static_preview")
    hdr_acts = [
        SelectPill(fps_lbl, "cc_desk:toggle_fps", selected=live_15fps, style="secondary"),
        Badge(t("cc_personalization.wp_badge_preview"), style="warn") if has_staged else StateBadge(True, t("cc_personalization.wp_badge_live")),
    ]
    if not ascii_status:
        if has_staged:
            hdr_acts.append(ActionBtn(t("cc_personalization.wp_reset"), "cc_desk:reset_staged", tone="warn"))
        hdr_acts.append(ActionBtn(t("cc_personalization.wp_apply"), "cc_desk:apply_staged", tone="apply" if has_staged else "inactive"))

    prev_box = Box(t("cc_personalization.desktop_preview_title"), icon="\uf108", actions=hdr_acts, highlighted=False)
    prev_box.add_row(" ")
    prev_box.add_row(f"  {Txt(t('cc_personalization.desktop_preview_hint'), role='schema')}")
    prev_box.add_row(" ")

    valid_outs = [o for o in out_names if outputs_data[o].get("logical")]
    if valid_outs:
        prev_box.open_bottom = True
        summary_str = "  |  ".join(
            f"{o}: {outputs_data[o]['logical'].get('width', 0)}x{outputs_data[o]['logical'].get('height', 0)}"
            for o in valid_outs
        )

        def _on_toggle_mon(out_name: str) -> None:
            if cc_panel:
                cc_panel.on_link(f"cc_desk:sel_mon:{out_name}")

        map_w = NiriDesktopPreviewMap(
            outputs_data,
            preview_states,
            sel_mons,
            on_toggle_monitor=_on_toggle_mon,
            map_width=int(round(canvas.total_px)),
            char_w=canvas.char_w,
            live_15fps=live_15fps,
            max_map_height=max(230, int(round(canvas.char_h * 13.5))),
            padding=max(12, int(round(canvas.char_w * 1.5))),
            footer_text=summary_str,
            inset_x=max(14, int(round(canvas.char_w * 2))),
            font_px=max(10.0, float(canvas.char_w) * 1.45),
        )
        foot = Box("", highlighted=False)
        foot.open_top = True
        foot.add_row(" ")
        cards.append(AsciiCard(prev_box, canvas, embedded_widget=map_w, footer_box_model=foot))
    else:
        cards.append(prev_box)

    # CARD 2: Wallpaper Configuration & Modes
    ncm_avail = is_ncmatrix_available()
    raw_wp_mode = str(STATE.get("desktop", "wp_mode", "scene"))
    wp_mode = "ncmatrix" if raw_wp_mode == "cmatrix" else raw_wp_mode
    if wp_mode == "ncmatrix" and not ncm_avail:
        wp_mode = "scene"
    cfg_box = Box(t("cc_personalization.wp_config_title"), icon="\uf03e", highlighted=False)

    # Gruppo A — Target & Operating Mode
    cfg_box.add_row(" ")
    mon_pills = [SelectPill(t("cc_personalization.wp_all_monitors"), "cc_desk:sel_all", selected=all_selected, style="secondary")] + [
        SelectPill(o, f"cc_desk:sel_mon:{o}", selected=(o in sel_mons), style="secondary") for o in out_names
    ]
    cfg_box.add_kv(t("cc_personalization.wp_monitors_row"), " ".join(mon_pills))

    mode_list = [
        SelectPill(t("cc_personalization.wp_mode_scene"), "cc_desk:mode:scene", selected=(wp_mode == "scene"), style="secondary"),
        SelectPill(t("cc_personalization.wp_mode_slideshow"), "cc_desk:mode:slideshow", selected=(wp_mode == "slideshow"), style="secondary"),
        SelectPill(t("cc_personalization.wp_mode_ncmatrix"), "cc_desk:mode:ncmatrix", selected=(wp_mode == "ncmatrix" and ncm_avail), style="secondary", disabled=not ncm_avail),
        SelectPill(t("cc_personalization.wp_mode_solid"), "cc_desk:mode:solid", selected=(wp_mode == "solid"), style="secondary"),
    ]
    mode_pills = " ".join(mode_list)
    mode_warn = Badge(t("ui.missing", p0="nc-matrix"), style="warn") if not ncm_avail else ""
    cfg_box.add_kv(t("cc_personalization.wp_mode_row"), mode_pills, right_markup=mode_warn)
    cfg_box.add_row(" ")

    has_staged_sel = any(bool(preview_states.get(m, {}).get("staged")) for m in sel_mons)

    has_animated_bg = any(
        st.get("mode") == "scene"
        and (
            (st.get("media_kind") or detect_media_kind(st.get("target", "")))
            in ("GIF", "VIDEO", "YT VIDEO", "YT PLAYLIST")
            or str(st.get("target", "")).startswith("http")
        )
        for st in preview_states.values()
    )

    if wp_mode == "scene":
        scene_path = str(STATE.get("desktop", "scene_path", "") or "")
        scene_fit = str(STATE.get("desktop", "scene_fit", "fill") or "fill")
        kind = detect_media_kind(scene_path)
        yt_src = _resolve_yt_source(scene_path)
        is_yt = kind in ("YT VIDEO", "YT PLAYLIST") or yt_src.get("ok")
        is_video_or_yt = kind in ("VIDEO", "YT VIDEO", "YT PLAYLIST") or scene_path.startswith("http")
        is_playlist = kind == "YT PLAYLIST"
        if kind in ("GIF", "VIDEO", "YT VIDEO", "YT PLAYLIST") or scene_path.startswith("http"):
            has_animated_bg = True

        # Gruppo B — Scene Source & Geometry
        cfg_box.add_section(t("cc_personalization.wp_sec_source"))
        cfg_box.add_row(" ")

        if scene_path:
            disp_scene = scene_path if scene_path.startswith("http") else os.path.basename(scene_path.rstrip("/"))
        else:
            disp_scene = t("cc_personalization.wp_no_scene_selected")
        kind_tag = f" {Txt(f'({kind})', role='schema')}" if kind else ""
        clear_btn = ActionBtn(t("cc_personalization.wp_clear_scene"), "cc_desk:clear_scene", tone="danger_soft") if scene_path else ""
        cfg_box.add_kv(
            t("cc_personalization.wp_selected_scene"),
            f"{Txt(canvas.truncate_plain(disp_scene, 34), role='focus' if scene_path else 'inactive')}{kind_tag}",
            right_markup=clear_btn,
        )

        source_btns = " ".join([
            ActionBtn(t("cc_personalization.wp_set_scene"), "cc_desk:pick_scene", tone="neutral"),
            ActionBtn(t("cc_personalization.wp_type_path"), "cc_desk:type_scene", tone="neutral"),
            ActionBtn(t("cc_personalization.wp_youtube_btn"), "cc_desk:prompt_yt", tone="neutral"),
            ActionBtn(t("cc_personalization.wp_paste_yt_btn"), "cc_desk:paste_yt", tone="neutral"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_scene_source"), source_btns)

        yt_deliv = str(STATE.get("desktop", "yt_delivery", "stream") or "stream")
        deliv_pills = " ".join([
            SelectPill(t("cc_personalization.wp_yt_stream"), "cc_desk:yt_deliv:stream", selected=(yt_deliv == "stream"), style="secondary"),
            SelectPill(t("cc_personalization.wp_yt_dl_local"), "cc_desk:yt_deliv:download", selected=(yt_deliv == "download"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_yt_delivery"), deliv_pills)

        if STATE.get("desktop", "yt_status") == "downloading":
            dl_key = "cc_personalization.wp_yt_downloading_pl" if is_playlist else "cc_personalization.wp_yt_downloading"
            cfg_box.add_kv(t("cc_personalization.wp_yt_status"), Txt(t(dl_key), role="attention"))

        fit_pills = " ".join([
            SelectPill(t("cc_personalization.wp_fit_fill"), "cc_desk:fit:fill", selected=(scene_fit == "fill"), style="secondary"),
            SelectPill(t("cc_personalization.wp_fit_fit"), "cc_desk:fit:fit", selected=(scene_fit == "fit"), style="secondary"),
            SelectPill(t("cc_personalization.wp_fit_center"), "cc_desk:fit:center", selected=(scene_fit == "center"), style="secondary"),
            SelectPill(t("cc_personalization.wp_fit_stretch"), "cc_desk:fit:stretch", selected=(scene_fit == "stretch"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_scaling_fit"), fit_pills)
        if should_show_scene_alignment(scene_path, scene_fit, sel_mons, outputs_data):
            scene_align = str(STATE.get("desktop", "scene_align", "center") or "center")
            align_pills = " ".join([
                SelectPill(t("cc_personalization.wp_align_top"), "cc_desk:align:top", selected=(scene_align == "top"), style="secondary"),
                SelectPill(t("cc_personalization.wp_align_center"), "cc_desk:align:center", selected=(scene_align == "center"), style="secondary"),
                SelectPill(t("cc_personalization.wp_align_bottom"), "cc_desk:align:bottom", selected=(scene_align == "bottom"), style="secondary"),
            ])
            cfg_box.add_kv(t("cc_personalization.wp_align_row"), align_pills)
        if not is_video_or_yt and kind != "GIF":
            asc_on = bool(get_editor_asciify_cfg().get("enabled"))
            style_pills = " ".join([
                SelectPill(t("cc_personalization.wp_style_normal"), "cc_desk:ascii_toggle:0", selected=(not asc_on), style="secondary"),
                SelectPill(t("cc_personalization.wp_style_asciified"), "cc_desk:ascii_toggle:1", selected=asc_on, style="secondary"),
            ])
            cfg_box.add_kv(t("cc_personalization.wp_style_row"), style_pills)
        cfg_box.add_kv(t("cc_personalization.wp_supported_formats"), Txt(t("cc_personalization.wp_supported_val"), role="inactive"))
        cfg_box.add_row(" ")

        if not is_video_or_yt and kind != "GIF" and get_editor_asciify_cfg().get("enabled"):
            _add_asciify_collapsible(cfg_box, cc_panel)

        # Gruppo C — Video & YouTube Playback Engine
        if is_video_or_yt:
            cfg_box.add_section(t("cc_personalization.wp_sec_playback"))
            cfg_box.add_row(" ")

            vid_loop = str(STATE.get("desktop", "video_loop", "loop") or "loop")
            loop_pills = " ".join([
                SelectPill(t("cc_personalization.wp_play_loop"), "cc_desk:vid_loop:loop", selected=(vid_loop == "loop"), style="secondary"),
                SelectPill(t("cc_personalization.wp_play_once"), "cc_desk:vid_loop:once", selected=(vid_loop == "once"), style="secondary"),
            ])
            cfg_box.add_kv(t("cc_personalization.wp_playback_mode"), loop_pills)

            if is_playlist:
                pl_order = str(STATE.get("desktop", "playlist_order", "sequential") or "sequential")
                pl_pills = " ".join([
                    SelectPill(t("cc_personalization.wp_pl_seq"), "cc_desk:pl_ord:sequential", selected=(pl_order == "sequential"), style="secondary"),
                    SelectPill(t("cc_personalization.wp_pl_shuffle"), "cc_desk:pl_ord:shuffle", selected=(pl_order == "shuffle"), style="secondary"),
                ])
                pl_tracks = " ".join([
                    ActionBtn(t("cc_personalization.wp_pl_prev"), "cc_desk:pl_prev", tone="neutral"),
                    ActionBtn(t("cc_personalization.wp_pl_next"), "cc_desk:pl_next", tone="neutral"),
                ])
                cfg_box.add_kv(t("cc_personalization.wp_playlist_ctrl"), pl_pills, right_markup=pl_tracks)

            vid_mute = bool(STATE.get("desktop", "video_mute", True))
            vid_vol = int(STATE.get("desktop", "video_volume", 60) or 60)
            audio_pills = " ".join([
                SelectPill(t("cc_personalization.wp_audio_mute"), "cc_desk:vid_mute:1", selected=vid_mute, style="secondary"),
                SelectPill(t("cc_personalization.wp_audio_on"), "cc_desk:vid_mute:0", selected=(not vid_mute), style="secondary"),
            ])
            cfg_box.add_kv(t("cc_personalization.wp_video_audio"), audio_pills)
            if not vid_mute:
                vol_pills = " ".join(
                    SelectPill(f"{v}%", f"cc_desk:vid_vol:{v}", selected=(vid_vol == v), style="secondary")
                    for v in (20, 40, 60, 80, 100)
                )
                cfg_box.add_kv(t("cc_personalization.wp_audio_volume"), vol_pills)
            cfg_box.add_row(" ")

    elif wp_mode == "slideshow":
        folder = str(STATE.get("desktop", "slide_folder", WALL_DIR) or WALL_DIR)
        interval = str(STATE.get("desktop", "slide_interval", "5m") or "5m")
        order = str(STATE.get("desktop", "slide_order", "shuffle") or "shuffle")
        trans = str(STATE.get("desktop", "slide_trans", "fade") or "fade")
        spd = str(STATE.get("desktop", "slide_speed", "1.5s") or "1.5s")

        sample_media = _find_media_file(folder)
        if sample_media and detect_media_kind(sample_media) in ("GIF", "VIDEO", "YT VIDEO"):
            has_animated_bg = True

        cfg_box.add_section(t("cc_personalization.wp_sec_source"))
        cfg_box.add_row(" ")

        f_count = count_folder_media(folder)
        f_disp = canvas.truncate_plain(folder.replace(os.path.expanduser("~"), "~"), 28)
        folder_btns = f"{ActionBtn(t('cc_personalization.wp_set_scene_folder'), 'cc_desk:pick_folder', tone='neutral')} {ActionBtn(t('cc_personalization.wp_type_path'), 'cc_desk:type_folder', tone='neutral')}"
        cfg_box.add_kv(
            t("cc_personalization.wp_scene_folder"),
            f"{Txt(f_disp, role='val')} {Txt(t('cc_personalization.wp_files_count', p0=f_count), role='schema')}",
            right_markup=folder_btns,
        )
        cfg_box.add_kv(t("cc_personalization.wp_supported_formats"), Txt(t("cc_personalization.wp_supported_val"), role="inactive"))

        int_pills = " ".join([
            SelectPill(t(f"cc_personalization.wp_interval_{iv}"), f"cc_desk:slide_int:{iv}", selected=(interval == iv), style="secondary")
            for iv in ("1m", "5m", "15m", "30m", "1h")
        ] + [
            InlineField(
                "cc_desk_interval",
                "cc_desk:inline_interval",
                value=interval if interval not in ("1m", "5m", "15m", "30m", "1h") else "",
                empty_label=t("cc_personalization.wp_custom_dots"),
                placeholder="5m",
                width=8,
                icon="",
                tone="neutral",
                selected=(interval not in ("1m", "5m", "15m", "30m", "1h")),
            )
        ])
        cfg_box.add_kv(t("cc_personalization.wp_switch_interval"), int_pills)

        ord_pills = " ".join([
            SelectPill(t("cc_personalization.wp_order_shuffle"), "cc_desk:slide_ord:shuffle", selected=(order == "shuffle"), style="secondary"),
            SelectPill(t("cc_personalization.wp_order_seq"), "cc_desk:slide_ord:sequential", selected=(order == "sequential"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_playback_order"), ord_pills)

        tr_pills = " ".join([
            SelectPill(t(f"cc_personalization.wp_trans_{tr}"), f"cc_desk:slide_tr:{tr}", selected=(trans == tr), style="secondary")
            for tr in ("fade", "wipe", "grow", "wave", "none")
        ])
        cfg_box.add_kv(t("cc_personalization.wp_trans_effect"), tr_pills)

        spd_pills = " ".join([
            SelectPill(t("cc_personalization.wp_speed_fast"), "cc_desk:slide_spd:0.5s", selected=(spd == "0.5s"), style="secondary"),
            SelectPill(t("cc_personalization.wp_speed_normal"), "cc_desk:slide_spd:1.5s", selected=(spd == "1.5s"), style="secondary"),
            SelectPill(t("cc_personalization.wp_speed_slow"), "cc_desk:slide_spd:3.0s", selected=(spd == "3.0s"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_trans_speed"), spd_pills)

        ctrl_btns = " ".join([
            ActionBtn(t("cc_personalization.wp_prev"), "cc_desk:prev", tone="neutral"),
            ActionBtn(t("cc_personalization.wp_pause"), "cc_desk:pause", tone="warn"),
            ActionBtn(t("cc_personalization.wp_resume"), "cc_desk:resume", tone="apply"),
            ActionBtn(t("cc_personalization.wp_next"), "cc_desk:next", tone="neutral"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_playback_ctrl"), ctrl_btns)
        asc_on = bool(get_editor_asciify_cfg().get("enabled"))
        style_pills = " ".join([
            SelectPill(t("cc_personalization.wp_style_normal"), "cc_desk:ascii_toggle:0", selected=(not asc_on), style="secondary"),
            SelectPill(t("cc_personalization.wp_style_asciified"), "cc_desk:ascii_toggle:1", selected=asc_on, style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_style_row"), style_pills)
        cfg_box.add_row(" ")

        if asc_on:
            _add_asciify_collapsible(cfg_box, cc_panel)

    elif wp_mode == "solid":
        solid_hex = _resolve_solid_hex(STATE.get("desktop", "solid_hex", "#000000") or "#000000")
        preset_map = dict(SOLID_CLASSIC_PRESETS + SOLID_DESIGNER_PRESETS)
        p_key = preset_map.get(solid_hex)
        p_lbl = t(f"cc_personalization.{p_key}") if p_key else t("cc_personalization.wp_solid_custom_label")

        cfg_box.add_section(t("cc_personalization.wp_solid_section"))
        cfg_box.add_row(" ")
        custom_btn = InlineField(
            "cc_desk_solid_hex",
            "cc_desk:inline_solid_hex",
            value=solid_hex if not p_key else "",
            empty_label=t("cc_personalization.wp_solid_custom_btn"),
            placeholder="#RRGGBB",
            width=9,
            icon="\u270e",
            tone="neutral",
            is_hex_color=True,
            selected=(not p_key),
        )
        cfg_box.add_kv(
            t("cc_personalization.wp_solid_active_color"),
            f"{SwatchTxt(solid_hex)}  {Txt(f'({p_lbl})', role='schema')}  {custom_btn}",
        )
        classic_pills = " ".join(
            SelectPill(
                t(f"cc_personalization.{lkey}"),
                f"cc_desk:solid_hex:{hx}",
                selected=(solid_hex == hx),
                dirty=(has_staged_sel and solid_hex == hx),
                style="secondary",
            )
            for hx, lkey in SOLID_CLASSIC_PRESETS
        )
        cfg_box.add_kv(t("cc_personalization.wp_solid_classic_presets"), classic_pills)
        designer_pills = " ".join(
            SelectPill(
                t(f"cc_personalization.{lkey}"),
                f"cc_desk:solid_hex:{hx}",
                selected=(solid_hex == hx),
                dirty=(has_staged_sel and solid_hex == hx),
                style="secondary",
            )
            for hx, lkey in SOLID_DESIGNER_PRESETS
        )
        cfg_box.add_kv(t("cc_personalization.wp_solid_designer_presets"), designer_pills)
        cfg_box.add_row(" ")

    elif ncm_avail:
        cm_cs = str(STATE.get("desktop", "cm_charset", "katakana") or "katakana")
        cm_spd = str(STATE.get("desktop", "cm_speed", "5") or "5")
        cm_sz = str(STATE.get("desktop", "cm_size", "9") or "9")
        cm_tr = str(STATE.get("desktop", "cm_trans", "flow") or "flow")

        cfg_box.add_section(t("cc_personalization.ncmatrix_native_wayland_shader_asci"))
        cfg_box.add_row(" ")

        kata_ok = has_katakana_font()
        if cm_cs == "katakana" and not kata_ok:
            cm_cs = "ascii"
        cs_pills = " ".join([
            SelectPill(
                t(f"cc_personalization.wp_charset_{cs}"),
                f"cc_desk:cm_charset:{cs}",
                selected=(cm_cs == cs),
                style="secondary",
                disabled=(cs == "katakana" and not kata_ok),
            )
            for cs in ("katakana", "ascii", "binary", "hex")
        ])
        cs_warn = Badge(t("ui.missing", p0="otf-ipafont"), style="warn") if not kata_ok else ""
        cfg_box.add_kv(t("cc_personalization.charset_mode"), cs_pills, right_markup=cs_warn)

        cm_col = str(STATE.get("desktop", "cm_color", "green") or "green").strip()
        cm_col_low = cm_col.lower()
        is_custom_hex = bool(re.match(r"^#[0-9A-Fa-f]{6}$", cm_col))
        col_pills = " ".join(
            SelectPill(
                t(f"cc_personalization.wp_color_{c}"),
                f"cc_desk:cm_col:{c}",
                selected=(cm_col_low == c),
                dirty=(has_staged_sel and cm_col_low == c),
                style="secondary",
            )
            for c in ("green", "cyan", "yellow", "white", "rainbow")
        ) + " " + InlineField(
            "cm_col_custom",
            "cc_desk:cm_col_custom",
            value=cm_col.upper() if is_custom_hex else "",
            empty_label=t("cc_personalization.wp_color_custom"),
            placeholder=t("cc_personalization.wp_custom_placeholder"),
            width=9,
            icon="",
            tone="neutral",
            is_hex_color=True,
            selected=is_custom_hex,
            dirty=(has_staged_sel and is_custom_hex),
        )
        cfg_box.add_kv(t("cc_personalization.shader_color"), col_pills)
        hex_err = get_inline_entry_error("cm_col_custom")
        if hex_err:
            cfg_box.add_row(f"  {Txt(hex_err, role='crit')}")

        cm_head = str(STATE.get("desktop", "cm_head_color", "blend") or "blend").strip().lower()
        if cm_head not in ("white", "match", "blend"):
            cm_head = "blend"
        head_pills = " ".join([
            SelectPill(t("cc_personalization.wp_cm_head_white"), "cc_desk:cm_head:white", selected=(cm_head == "white"), style="secondary"),
            SelectPill(t("cc_personalization.wp_cm_head_match"), "cc_desk:cm_head:match", selected=(cm_head == "match"), style="secondary"),
            SelectPill(t("cc_personalization.wp_cm_head_blend"), "cc_desk:cm_head:blend", selected=(cm_head == "blend"), style="secondary"),
        ])
        cfg_box.add_kv(t("cc_personalization.wp_cm_head_color"), head_pills)

        spd_pills = " ".join(
            SelectPill(str(s), f"cc_desk:cm_spd:{s}", selected=(cm_spd == str(s)), style="secondary")
            for s in range(1, 11)
        )
        cfg_box.add_kv(t("cc_personalization.speed_level"), spd_pills)

        raw_cm_spawn = STATE.get("desktop", "cm_spawn_rate", "1.5")
        try:
            cm_spawn = max(0.0, min(3.0, round(float("1.5" if raw_cm_spawn is None or str(raw_cm_spawn).strip() == "" else raw_cm_spawn), 1)))
        except (TypeError, ValueError):
            cm_spawn = 1.5
        spawn_slider = Slider(
            cm_spawn,
            "cc_desk:cm_spawn",
            min_val=0.0,
            max_val=3.0,
            width=30,
            show_pct=False,
            step=0.1,
        )
        cfg_box.add_kv(
            t("cc_personalization.wp_cm_spawn_rate"),
            f"{spawn_slider} {Txt(f'{cm_spawn:.1f}x', role='val')}",
        )

        sz_pills = " ".join(
            SelectPill(t("cc_personalization.wp_cm_size_pt", p0=sz), f"cc_desk:cm_size:{sz}", selected=(cm_sz == str(sz)), style="secondary")
            for sz in ("6", "8", "9", "10", "12", "14", "16", "20")
        )
        cfg_box.add_kv(t("cc_personalization.wp_cm_font_size"), sz_pills)

        cm_var_s = _fmt_cm_var(STATE.get("desktop", "cm_speed_variance", "5.0"))
        cm_var = float(cm_var_s)
        cm_ov = _norm_cm_overlap(STATE.get("desktop", "cm_overlap", "hybrid"))
        cm_par_s = _fmt_cm_parallax(STATE.get("desktop", "cm_parallax", "0.50"))
        cm_par = float(cm_par_s)
        cm_glow_s = _fmt_cm_glow(STATE.get("desktop", "cm_glow", "0.60"))
        cm_glow = float(cm_glow_s)
        cm_dec = _norm_cm_decay(STATE.get("desktop", "cm_decay", "exponential"))
        cm_shim = _norm_cm_shimmer(STATE.get("desktop", "cm_shimmer", "insane"))
        cm_mir = _norm_cm_mirror(STATE.get("desktop", "cm_mirror", "random"))
        cm_tail = _norm_cm_tail(STATE.get("desktop", "cm_tail_len", "long"))
        cm_dens = _norm_cm_density(STATE.get("desktop", "cm_col_density", "dense"))

        def _build_cm_adv(b: Box) -> None:
            b.add_row(" ")
            var_slider = Slider(
                cm_var,
                "cc_desk:cm_var",
                min_val=0.0,
                max_val=10.0,
                width=30,
                show_pct=False,
                step=0.5,
            )
            b.add_kv(t("cc_personalization.wp_cm_speed_var"), f"{var_slider} {Txt(f'±{cm_var:.1f}', role='val')}")

            ov_pills = " ".join(
                SelectPill(t(f"cc_personalization.wp_cm_overlap_{ov}"), f"cc_desk:cm_overlap:{ov}", selected=(cm_ov == ov), style="secondary")
                for ov in ("hybrid", "parallax", "additive")
            )
            b.add_kv(t("cc_personalization.wp_cm_overlap"), ov_pills)

            par_slider = Slider(
                cm_par,
                "cc_desk:cm_parallax",
                min_val=0.0,
                max_val=1.0,
                width=30,
                show_pct=False,
                step=0.05,
            )
            b.add_kv(t("cc_personalization.wp_cm_parallax"), f"{par_slider} {Txt(f'{int(round(cm_par * 100))}%', role='val')}")

            glow_slider = Slider(
                cm_glow,
                "cc_desk:cm_glow",
                min_val=0.0,
                max_val=1.0,
                width=30,
                show_pct=False,
                step=0.05,
            )
            b.add_kv(t("cc_personalization.wp_cm_glow"), f"{glow_slider} {Txt(f'{int(round(cm_glow * 100))}%', role='val')}")

            dec_pills = " ".join(
                SelectPill(t(f"cc_personalization.wp_cm_decay_{dm}"), f"cc_desk:cm_decay:{dm}", selected=(cm_dec == dm), style="secondary")
                for dm in ("exponential", "linear")
            )
            b.add_kv(t("cc_personalization.wp_cm_decay"), dec_pills)

            shim_pills = " ".join(
                SelectPill(t(f"cc_personalization.wp_cm_shimmer_{sh}"), f"cc_desk:cm_shimmer:{sh}", selected=(cm_shim == sh), style="secondary")
                for sh in ("off", "low", "med", "high", "insane")
            )
            b.add_kv(t("cc_personalization.wp_cm_shimmer"), shim_pills)

            mir_pills = " ".join(
                SelectPill(t(f"cc_personalization.wp_cm_mirror_{mm}"), f"cc_desk:cm_mirror:{mm}", selected=(cm_mir == mm), style="secondary")
                for mm in ("off", "on", "random")
            )
            b.add_kv(t("cc_personalization.wp_cm_mirror"), mir_pills)

            tail_pills = " ".join(
                SelectPill(t(f"cc_personalization.wp_cm_tail_{tl}"), f"cc_desk:cm_tail:{tl}", selected=(cm_tail == tl), style="secondary")
                for tl in ("short", "normal", "long")
            )
            b.add_kv(t("cc_personalization.wp_cm_tail_len"), tail_pills)

            dens_pills = " ".join(
                SelectPill(t(f"cc_personalization.wp_cm_density_{dn}"), f"cc_desk:cm_density:{dn}", selected=(cm_dens == dn), style="secondary")
                for dn in ("classic", "dense")
            )
            b.add_kv(t("cc_personalization.wp_cm_col_density"), dens_pills)

            tr_pills = " ".join(
                SelectPill(t(f"cc_personalization.wp_ncmatrix_trans_{tr}"), f"cc_desk:cm_trans:{tr}", selected=(cm_tr == tr), style="secondary")
                for tr in ("flow", "drain", "instant")
            )
            b.add_kv(t("cc_personalization.wp_ncmatrix_transition"), tr_pills)

        adv_summary = t(
            "cc_personalization.wp_cm_adv_summary",
            p0=f"{cm_var:.1f}",
            p1=t(f"cc_personalization.wp_cm_overlap_{cm_ov}"),
            p2=str(int(round(cm_glow * 100))),
            p3=t(f"cc_personalization.wp_cm_tail_{cm_tail}"),
        )
        cfg_box.add_row(" ")
        cfg_box.add_collapsible(cc_panel, "wp_cm_adv_open", t("cc_personalization.wp_cm_adv_section"), _build_cm_adv, summary=adv_summary)

        cfg_box.add_row(" ")
        cfg_box.add_row(f"  {Txt('ℹ', role='schema')} {Txt(t('cc_personalization.wp_ncmatrix_efficiency_note'), role='inactive')}")
        cpu_info = get_ncmatrix_live_cpu_usage(sel_mons)
        if cpu_info["running"]:
            core_pct = cpu_info["core_pct"]
            cpu_role = "ok" if core_pct < 50.0 else ("warn" if core_pct < 75.0 else ("interrupt" if core_pct < 90.0 else "crit"))
            mons_str = ", ".join(cpu_info["monitors"])
            detail_txt = (
                t("cc_personalization.wp_ncmatrix_cpu_live_detail", p0=f"{cpu_info['sys_pct']:.2f}%")
                if cpu_info["count"] == 1
                else t("cc_personalization.wp_ncmatrix_cpu_live_detail_multi", p0=str(cpu_info["count"]), p1=f"{cpu_info['sys_pct']:.2f}%")
            )
            cfg_box.add_row(
                f"  {Txt('\uf2db', role='schema')} {Txt(t('cc_personalization.wp_ncmatrix_cpu_live_lbl', p0=mons_str), role='schema')} "
                f"{Txt(f'{core_pct:.1f}%', role=cpu_role)} {Txt(detail_txt, role='inactive')}"
            )
        else:
            cfg_box.add_row(
                f"  {Txt('\uf2db', role='schema')} {Txt(t('cc_personalization.wp_ncmatrix_cpu_idle_lbl'), role='schema')} "
                f"{Txt(t('cc_personalization.wp_ncmatrix_cpu_idle_val'), role='inactive')}"
            )
        cfg_box.add_row(" ")

    # Gruppo D — Barra Commit in Fondo al Box
    cfg_box.add_section(t("cc_personalization.wp_sec_commit"))
    cfg_box.add_row(" ")
    if ascii_status:
        st_txt = Badge(canvas.truncate_plain(ascii_status, 52), style="warn")
        st_btns = ActionBtn(t("cc_personalization.wp_ascii_cancel_btn"), "cc_desk:ascii_cancel", tone="danger_soft")
    elif has_staged:
        st_txt = Txt(t("cc_personalization.wp_staged_footer_warn"), role="warn")
        st_btns = f"{ActionBtn(t('cc_personalization.wp_reset'), 'cc_desk:reset_staged', tone='warn')} {ActionBtn(t('cc_personalization.wp_apply_wallpaper'), 'cc_desk:apply_staged', tone='apply')}"
    else:
        st_txt = Txt(t("cc_personalization.wp_live_footer_ok"), role="ok")
        st_btns = ActionBtn(t("cc_personalization.wp_apply_wallpaper"), "cc_desk:apply_staged", tone="inactive")
    cfg_box.add_row(f"  {st_txt}", right_markup=st_btns)
    if has_animated_bg:
        cfg_box.add_row(f"  {Txt(t('cc_personalization.wp_gif_video_warn'), role='warn')}")
    cfg_box.add_row(" ")

    cards.append(cfg_box)

    yazi_sess = get_active_yazi_session()
    if STATE.get("desktop", "yazi_open") and yazi_sess is not None and getattr(yazi_sess, "target_mode", "wallpaper") == "wallpaper":
        yazi_title = t("cc_personalization.wp_yazi_box_folder_title" if yazi_sess.is_folder else "cc_personalization.wp_yazi_box_title")
        yazi_box = Box(
            yazi_title,
            icon="\uf07c",
            actions=[ActionBtn("\uf00d", "cc_desk:close_yazi", tone="danger_soft")],
            highlighted=False,
        )
        yazi_box.add_row(f"  {Txt(t('cc_personalization.wp_yazi_box_hint'), role='schema')}")
        yazi_box.open_bottom = True
        yazi_w = EmbeddedYaziWidget(
            yazi_sess,
            map_width=int(round(canvas.total_px)),
            char_w=canvas.char_w,
            cell_h=max(14.0, float(canvas.char_h)),
            border_hex=yazi_box._resolve_palette()[0],
        )
        yazi_foot = Box("", highlighted=False)
        yazi_foot.open_top = True
        yazi_foot.add_row(" ")
        cards.append(AsciiCard(yazi_box, canvas, embedded_widget=yazi_w, footer_box_model=yazi_foot))

    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_wallpaper_standard(cc_panel, canvas)
