#!/usr/bin/env python3
"""NC-UI Resilience, Snapshots, Vault & Disaster Recovery Engine (`panels/cc_engine_telemetry.py`)."""

import json
import math
import os
import re
import threading
import time
from core import probe
from core.config import THEME
from core.i18n import t
from core.privileges import get_privileged_action, request_privilege_modal
from core.resilience import (
    exec_priv_helper as _exec_priv_helper,
    expand_user_token,
    load_resilience_config,
    parse_vault_share,
    read_env_kv as _read_env_kv,
    resolve_sbin_helper,
    run_helper_async as _run_helper_async,
)
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    Box,
    FoldToggle,
    Txt,
    get_semantic_palette,
    hex_to_rgba_f,
    rounded_rect_path,
    set_feedback,
    start_inline_input,
)
from ui.markup import format_bytes_short


def _start_recovery_embedded_shell(cc_panel, title_key: str, cmd: list[str]) -> None:
    """Launch an interactive Recovery / Chroot / Golden CLI directly inside the Control Center."""
    from ui.embedded_tui import start_panel_terminal

    def _on_exit() -> bool:
        try:
            def _probe_and_refresh():
                rb = resolve_sbin_helper("nc-rescue")
                if rb and os.path.isfile(rb):
                    probe.run_cmd([rb, "status-env"], timeout=8.0)

            def _ui_refresh(_res=None):
                if cc_panel and getattr(cc_panel, "shell", None):
                    cc_panel.shell.refresh_view()

            probe.run_async(_probe_and_refresh, on_done=_ui_refresh)
        except Exception:
            pass
        return False

    start_panel_terminal(
        cc_panel,
        t("cc_system.rescue_term_title", p0=t(title_key)),
        cmd,
        on_exit=_on_exit,
    )


class _ResilienceBlueprintMixin:
    """Pure-Cairo 3-zone Tactical Vector Blueprint matching ControllerBlueprintWidget."""

    def __init__(
        self,
        mode: str,
        prefix: str,
        status_right: str = "",
        inset_x: float = 16.0,
        fixed_height: int = 236,
    ) -> None:
        super().__init__()
        self.bp_mode = str(mode)
        self.bp_prefix = str(prefix)
        self.status_right = str(status_right or "")
        self.inset_x = max(8.0, float(inset_x))
        self.widget_width = 860
        self.widget_height = max(190, int(fixed_height))
        self.footer_h = 28.0
        self.set_size_request(360, self.widget_height)
        self.set_hexpand(True)
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("draw", self._on_draw)

    def _on_size_allocate(self, _widget, alloc) -> None:
        if alloc.width > 120 and abs(alloc.width - self.widget_width) > 1:
            self.widget_width = alloc.width
            self.queue_draw()

    @staticmethod
    def _text(cr, text: str, x: float, y: float, font_desc, color_hex: str, alpha: float = 1.0, max_w: float = 0.0, align=None) -> tuple[float, float]:
        from gi.repository import Pango, PangoCairo
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(font_desc)
        if max_w > 0.0:
            lay.set_width(int(max_w * Pango.SCALE))
            lay.set_ellipsize(Pango.EllipsizeMode.END)
        if align is not None:
            lay.set_alignment(align)
        lay.set_text(str(text), -1)
        _, ext = lay.get_pixel_extents()
        cr.set_source_rgba(*hex_to_rgba_f(color_hex, alpha))
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lay)
        return float(ext.width), float(ext.height)

    @staticmethod
    def _text_centered(cr, text: str, x: float, y: float, w: float, h: float, font_desc, color_hex: str, alpha: float = 1.0, align=None) -> tuple[float, float]:
        from gi.repository import Pango, PangoCairo
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(font_desc)
        if align is not None:
            lay.set_alignment(align)
        lay.set_text(str(text), -1)
        _, ext = lay.get_pixel_extents()
        tx = x + (w - float(ext.width)) * 0.5 - float(ext.x) if align is None else x
        if align == Pango.Alignment.LEFT:
            tx = x
        elif align == Pango.Alignment.RIGHT:
            tx = x + w - float(ext.width) - float(ext.x)
        ty = y + (h - float(ext.height)) * 0.5 - float(ext.y)
        cr.set_source_rgba(*hex_to_rgba_f(color_hex, alpha))
        cr.move_to(tx, ty)
        PangoCairo.show_layout(cr, lay)
        return float(ext.width), float(ext.height)

    def _draw_zone_hdr(self, cr, x: float, y: float, w: float, label: str, col_hex: str, f_micro, pal: dict) -> None:
        tw, th = self._text(cr, label, x, y, f_micro, col_hex, alpha=0.95)
        ly = math.floor(y + th * 0.5) + 0.5
        if tw + 10.0 < w:
            cr.save()
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.65))
            cr.set_line_width(1.0)
            cr.move_to(x + tw + 8.0, ly)
            cr.line_to(x + w, ly)
            cr.stroke()
            cr.restore()

    def _draw_node(
        self,
        cr,
        x: float,
        y: float,
        w: float,
        h: float,
        title: str,
        subtitle: str,
        border_hex: str,
        fill_hex: str,
        f_title,
        f_sub,
        pal: dict,
        fill_alpha: float = 0.58,
        badge_txt: str = "",
        badge_hex: str = "",
        double_border: bool = False,
    ) -> None:
        rounded_rect_path(cr, x + 0.5, y + 0.5, w - 1.0, h - 1.0, 5.0)
        cr.set_source_rgba(*hex_to_rgba_f(fill_hex, fill_alpha))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(border_hex, 0.88))
        cr.set_line_width(1.3)
        cr.stroke()
        if double_border:
            rounded_rect_path(cr, x + 3.5, y + 3.5, w - 7.0, h - 7.0, 3.0)
            cr.set_source_rgba(*hex_to_rgba_f(border_hex, 0.40))
            cr.set_line_width(0.9)
            cr.stroke()

        pad = 7.0 if w < 120.0 else 9.0
        bw_res = 0.0
        if badge_txt:
            from gi.repository import Pango
            bcol = badge_hex or border_hex
            bl_w = max(38.0, float(len(badge_txt)) * 7.6 + 12.0)
            bx0 = x + w - pad - bl_w + 2.0
            by0 = y + 5.0
            rounded_rect_path(cr, bx0, by0, bl_w, 18.0, 3.0)
            cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.85))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(bcol, 0.85))
            cr.set_line_width(0.9)
            cr.stroke()
            self._text(cr, badge_txt, bx0 + 2.0, by0 + 2.0, f_sub, bcol, alpha=0.96, max_w=bl_w - 4.0, align=Pango.Alignment.CENTER)
            bw_res = bl_w + 6.0

        tw_max = max(24.0, w - pad * 2.0 - bw_res)
        self._text(cr, title, x + pad, y + (6.0 if subtitle else max(4.0, (h - 16.0) * 0.5)), f_title, pal["focus"], alpha=0.96, max_w=tw_max)
        if subtitle:
            self._text(cr, subtitle, x + pad, y + 24.0, f_sub, pal["focus_val"], alpha=0.82, max_w=w - pad * 2.0)

    def _draw_pill(self, cr, cx: float, cy: float, w: float, h: float, text: str, col_hex: str, fill_hex: str, f_sub) -> None:
        from gi.repository import Pango
        h = max(h, 19.0)
        x0 = cx - w * 0.5
        y0 = cy - h * 0.5
        rounded_rect_path(cr, x0 + 0.5, y0 + 0.5, w - 1.0, h - 1.0, 3.5)
        cr.set_source_rgba(*hex_to_rgba_f(fill_hex, 0.88))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(col_hex, 0.85))
        cr.set_line_width(1.0)
        cr.stroke()
        self._text(cr, text, x0 + 2.0, y0 + max(1.0, (h - 15.0) * 0.5), f_sub, col_hex, alpha=0.96, max_w=w - 4.0, align=Pango.Alignment.CENTER)

    @staticmethod
    def _arrow(cr, x1: float, y1: float, x2: float, y2: float, col_hex: str, dashed: bool = False, bidir: bool = False) -> None:
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(col_hex, 0.92))
        cr.set_line_width(1.4)
        if dashed:
            cr.set_dash([3.0, 3.0])
        cr.move_to(x1, y1)
        cr.line_to(x2, y2)
        cr.stroke()
        cr.set_dash([])

        def _head(ax: float, ay: float, ang: float) -> None:
            hl = 6.5
            ha = 0.46
            cr.move_to(ax, ay)
            cr.line_to(ax - hl * math.cos(ang - ha), ay - hl * math.sin(ang - ha))
            cr.line_to(ax - hl * math.cos(ang + ha), ay - hl * math.sin(ang + ha))
            cr.close_path()
            cr.fill()

        ang = math.atan2(y2 - y1, x2 - x1)
        _head(x2, y2, ang)
        if bidir:
            _head(x1, y1, ang + math.pi)
        cr.restore()

    def _draw_recovery(self, cr, zxs: list[float], zw: float, f_hdr, f_title, f_sub, pal: dict, surf_hex: str) -> None:
        p = self.bp_prefix
        uw = zw - 28.0
        # Zone 1: Live RAM UKI
        x1 = zxs[0] + 14.0
        self._draw_zone_hdr(cr, x1, 10.0, uw, t(f"cc_system.{p}_bp_z1"), pal["intent"], f_hdr, pal)
        self._draw_node(
            cr, x1, 32.0, uw, 44.0,
            t(f"cc_system.{p}_bp_esp"), t(f"cc_system.{p}_bp_sq"),
            pal["vitality"], pal["vitality_bg"], f_title, f_sub, pal,
            badge_txt=t(f"cc_system.{p}_bp_sb"), badge_hex=pal["vitality"],
        )
        sc_cx = x1 + 36.0
        sc_cy = 136.0
        sc_r = 30.0
        self._arrow(cr, sc_cx, 76.0, sc_cx, sc_cy - sc_r - 3.0, pal["vitality"])
        # Polar RAM scope
        cr.save()
        cr.arc(sc_cx, sc_cy, sc_r, 0, math.tau)
        cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.88))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.85))
        cr.set_line_width(1.6)
        cr.stroke()
        cr.arc(sc_cx, sc_cy, sc_r * 0.64, 0, math.tau)
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.42))
        cr.set_line_width(1.0)
        cr.set_dash([2.5, 2.5])
        cr.stroke()
        cr.set_dash([])
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.55))
        cr.move_to(sc_cx - sc_r + 3.0, sc_cy)
        cr.line_to(sc_cx + sc_r - 3.0, sc_cy)
        cr.move_to(sc_cx, sc_cy - sc_r + 3.0)
        cr.line_to(sc_cx, sc_cy + sc_r - 3.0)
        cr.stroke()
        # Rollback arc (.old) in attention yellow
        cr.arc(sc_cx, sc_cy, sc_r + 3.5, -0.75, 0.45)
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention"], 0.92))
        cr.set_line_width(1.8)
        cr.stroke()
        # Center live RAM dot
        cr.arc(sc_cx, sc_cy, 8.0, 0, math.tau)
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.25))
        cr.fill()
        cr.arc(sc_cx, sc_cy, 4.0, 0, math.tau)
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 1.0))
        cr.fill()
        cr.restore()

        ram_bx = sc_cx + sc_r + 14.0
        self._draw_node(
            cr, ram_bx, 110.0, max(96.0, x1 + uw - ram_bx), 48.0,
            t(f"cc_system.{p}_bp_ram"), t(f"cc_system.{p}_bp_ram_sub"),
            pal["intent"], pal["intent_bg"], f_title, f_sub, pal,
            badge_txt=t(f"cc_system.{p}_bp_old"), badge_hex=pal["attention"],
        )

        # Zone 2: LUKS2 Chroot
        x2 = zxs[1] + 14.0
        self._draw_zone_hdr(cr, x2, 10.0, uw, t(f"cc_system.{p}_bp_z2"), pal["attention"], f_hdr, pal)
        cx2 = x2 + uw * 0.5
        cy2 = 91.0
        cr.save()
        # Outer volume ring
        cr.arc(cx2, cy2, 43.0, 0, math.tau)
        cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.85))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.92))
        cr.set_line_width(1.2)
        cr.stroke()
        # LUKS2 cipher ring (attention)
        cr.arc(cx2, cy2, 31.0, 0, math.tau)
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention_bg"], 0.72))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention"], 0.92))
        cr.set_line_width(1.6)
        cr.stroke()
        # Unlocked Root LV core (vitality)
        cr.arc(cx2, cy2, 17.5, 0, math.tau)
        cr.set_source_rgba(*hex_to_rgba_f(pal["vitality_bg"], 0.85))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["vitality"], 0.95))
        cr.set_line_width(1.5)
        cr.stroke()
        cr.arc(cx2, cy2, 4.0, 0, math.tau)
        cr.set_source_rgba(*hex_to_rgba_f(pal["vitality"], 1.0))
        cr.fill()
        cr.restore()

        # Bind mount corner pills connected to ring
        binds = [
            (x2 + 24.0, 56.0, "/dev", cx2 - 36.0, cy2 - 23.0),
            (x2 + uw - 24.0, 56.0, "/proc", cx2 + 36.0, cy2 - 23.0),
            (x2 + 24.0, 122.0, "/sys", cx2 - 36.0, cy2 + 23.0),
            (x2 + uw - 24.0, 122.0, "/boot", cx2 + 36.0, cy2 + 23.0),
        ]
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.65))
        cr.set_line_width(1.0)
        cr.set_dash([2.0, 2.5])
        for px, py, _lbl, rx, ry in binds:
            cr.move_to(px + (21.0 if px < cx2 else -21.0), py)
            cr.line_to(rx, ry)
        cr.stroke()
        cr.restore()
        for px, py, lbl, _rx, _ry in binds:
            self._draw_pill(cr, px, py, 54.0, 19.0, lbl, pal["schema"], pal["structure_bg"], f_sub)

        self._draw_pill(cr, cx2 - 28.0, 44.0, 54.0, 19.0, t(f"cc_system.{p}_bp_luks"), pal["attention"], pal["attention_bg"], f_sub)
        self._draw_node(
            cr, x2 + 18.0, 147.0, uw - 36.0, 29.0,
            t(f"cc_system.{p}_bp_chroot"), "",
            pal["vitality"], pal["vitality_bg"], f_sub, f_sub, pal,
        )

        # Zone 3: Golden Image
        x3 = zxs[2] + 10.0
        uw3 = zw - 20.0
        self._draw_zone_hdr(cr, x3, 10.0, uw3, t(f"cc_system.{p}_bp_z3"), pal["vitality"], f_hdr, pal)
        cw = max(88.0, (uw3 - 64.0) * 0.5)
        self._draw_node(
            cr, x3, 46.0, cw, 114.0,
            t(f"cc_system.{p}_bp_nvme"), t(f"cc_system.{p}_bp_nvme_sub"),
            pal["intent"], pal["intent_bg"], f_title, f_sub, pal,
        )
        # Partition stripes inside NVMe host node
        for idx, bcol in enumerate((pal["vitality"], pal["intent"], pal["structure"])):
            rounded_rect_path(cr, x3 + 7.0, 92.0 + idx * 18.0, cw - 14.0, 11.0, 2.5)
            cr.set_source_rgba(*hex_to_rgba_f(bcol, 0.30 if idx == 2 else 0.45))
            cr.fill_preserve()
            cr.set_source_rgba(*hex_to_rgba_f(bcol, 0.80))
            cr.set_line_width(0.9)
            cr.stroke()

        rx3 = x3 + uw3 - cw
        self._draw_node(
            cr, rx3, 46.0, cw, 114.0,
            t(f"cc_system.{p}_bp_vault"), "SHA256 ✓",
            pal["vitality"], pal["vitality_bg"], f_title, f_sub, pal,
            double_border=True,
        )
        self._draw_pill(cr, rx3 + cw * 0.5, 134.0, cw - 10.0, 20.0, t(f"cc_system.{p}_bp_rekey"), pal["attention"], pal["attention_bg"], f_sub)

        ax_l = x3 + cw + 2.0
        ax_r = rx3 - 2.0
        self._arrow(cr, ax_l, 78.0, ax_r, 78.0, pal["vitality"])
        self._draw_pill(cr, (ax_l + ax_r) * 0.5, 62.0, 60.0, 19.0, t(f"cc_system.{p}_bp_cap"), pal["vitality"], pal["structure_bg"], f_sub)
        self._arrow(cr, ax_r, 122.0, ax_l, 122.0, pal["attention"])
        self._draw_pill(cr, (ax_l + ax_r) * 0.5, 138.0, 60.0, 19.0, t(f"cc_system.{p}_bp_res"), pal["attention"], pal["structure_bg"], f_sub)

    def _draw_snapshots(self, cr, zxs: list[float], zw: float, f_hdr, f_title, f_sub, pal: dict, surf_hex: str) -> None:
        p = self.bp_prefix
        uki_prefix = str(load_resilience_config()["snapshots"].get("uki_prefix") or "linux")
        uw = zw - 28.0
        # Zone 1: Host Root & Hook
        x1 = zxs[0] + 14.0
        self._draw_zone_hdr(cr, x1, 10.0, uw, t(f"cc_system.{p}_bp_z1"), pal["intent"], f_hdr, pal)
        self._draw_node(
            cr, x1, 38.0, uw - 6.0, 54.0,
            t(f"cc_system.{p}_bp_root"), t(f"cc_system.{p}_bp_root_sub"),
            pal["intent"], pal["intent_bg"], f_title, f_sub, pal,
        )
        self._draw_node(
            cr, x1, 118.0, uw - 6.0, 54.0,
            t(f"cc_system.{p}_bp_hook"), t(f"cc_system.{p}_bp_hook_sub"),
            pal["attention"], pal["attention_bg"], f_title, f_sub, pal,
        )
        self._arrow(cr, x1 + (uw - 6.0) * 0.5, 118.0, x1 + (uw - 6.0) * 0.5, 93.0, pal["attention"], dashed=True)

        # Zone 2: Dual-Slot CoW Engine
        x2 = zxs[1] + 14.0
        self._draw_zone_hdr(cr, x2, 10.0, uw, t(f"cc_system.{p}_bp_z2"), pal["vitality"], f_hdr, pal)
        self._arrow(cr, x1 + uw - 6.0, 62.0, x2, 62.0, pal["intent"])
        self._arrow(cr, x1 + uw - 6.0, 145.0, x2, 145.0, pal["vitality"])

        self._draw_node(
            cr, x2, 38.0, uw - 6.0, 54.0,
            f"● {t(f'cc_system.{p}_bp_auto')}", f"{uki_prefix}-auto.efi",
            pal["intent"], pal["intent_bg"], f_title, f_sub, pal,
        )
        # CoW mini bar inside AUTO card
        rounded_rect_path(cr, x2 + 9.0, 77.0, uw - 24.0, 7.0, 2.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.45))
        cr.fill()
        rounded_rect_path(cr, x2 + 9.0, 77.0, (uw - 24.0) * 0.28, 7.0, 2.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.90))
        cr.fill()

        self._draw_pill(cr, x2 + (uw - 6.0) * 0.5, 105.0, 114.0, 19.0, t(f"cc_system.{p}_bp_cow"), pal["schema"], pal["structure_bg"], f_sub)
        self._draw_node(
            cr, x2, 118.0, uw - 6.0, 54.0,
            f"● {t(f'cc_system.{p}_bp_stable')}", f"{uki_prefix}-stable.efi",
            pal["vitality"], pal["vitality_bg"], f_title, f_sub, pal,
        )
        rounded_rect_path(cr, x2 + 9.0, 157.0, uw - 24.0, 7.0, 2.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.45))
        cr.fill()
        rounded_rect_path(cr, x2 + 9.0, 157.0, (uw - 24.0) * 0.44, 7.0, 2.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["vitality"], 0.90))
        cr.fill()

        # Zone 3: Limine & Promote
        x3 = zxs[2] + 14.0
        self._draw_zone_hdr(cr, x3, 10.0, uw, t(f"cc_system.{p}_bp_z3"), pal["attention"], f_hdr, pal)
        self._arrow(cr, x2 + uw - 6.0, 58.0, x3, 58.0, pal["intent"])
        self._arrow(cr, x2 + uw - 6.0, 145.0, x3, 76.0, pal["vitality"], dashed=True)

        self._draw_node(
            cr, x3, 38.0, uw - 6.0, 56.0,
            t(f"cc_system.{p}_bp_limine"), f"▸ {t(f'cc_system.{p}_bp_limine_sub')}",
            pal["focus"], surf_hex, f_title, f_sub, pal, fill_alpha=0.82,
        )
        self._arrow(cr, x3 + (uw - 6.0) * 0.5, 94.0, x3 + (uw - 6.0) * 0.5, 117.0, pal["vitality"])
        self._draw_node(
            cr, x3, 118.0, uw - 6.0, 54.0,
            t(f"cc_system.{p}_bp_prom"), t(f"cc_system.{p}_bp_prom_sub"),
            pal["attention"], pal["attention_bg"], f_title, f_sub, pal,
        )
        # Subtle rollback loop line back to Host Root along bottom & left gutter
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["attention"], 0.72))
        cr.set_line_width(1.2)
        cr.set_dash([3.0, 3.0])
        ret_x = x1 - 7.0
        cr.move_to(x3 + (uw - 6.0) * 0.35, 172.0)
        cr.line_to(x3 + (uw - 6.0) * 0.35, 180.0)
        cr.line_to(ret_x, 180.0)
        cr.line_to(ret_x, 65.0)
        cr.stroke()
        cr.restore()
        self._arrow(cr, ret_x, 65.0, x1, 65.0, pal["attention"], dashed=True)

    def _draw_backups(self, cr, zxs: list[float], zws: list[float], f_hdr, f_title, f_sub, pal: dict, surf_hex: str) -> None:
        from gi.repository import Pango
        p = self.bp_prefix
        uw1 = zws[0] - 24.0
        uw2 = zws[1] - 24.0
        uw3 = zws[2] - 24.0

        # Zone 1: Workstation Nodes
        x1 = zxs[0] + 12.0
        self._draw_zone_hdr(cr, x1, 10.0, uw1, t(f"cc_system.{p}_bp_z1"), pal["intent"], f_hdr, pal)
        self._draw_node(
            cr, x1, 36.0, uw1 - 4.0, 48.0,
            f"● {t(f'cc_system.{p}_bp_host')}", t(f"cc_system.{p}_bp_host_sub"),
            pal["intent"], pal["intent_bg"], f_title, f_sub, pal,
        )
        pw = (uw1 - 14.0) * 0.5
        self._draw_node(
            cr, x1, 124.0, pw, 44.0,
            f"○ {t(f'cc_system.{p}_bp_peer1')}", "",
            pal["vitality"], surf_hex, f_title, f_sub, pal,
        )
        self._draw_node(
            cr, x1 + pw + 10.0, 124.0, pw, 44.0,
            f"○ {t(f'cc_system.{p}_bp_peer2')}", "",
            pal["vitality"], surf_hex, f_title, f_sub, pal,
        )
        # Dashed shared links
        h_mid = x1 + (uw1 - 4.0) * 0.5
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["vitality"], 0.72))
        cr.set_line_width(1.2)
        cr.set_dash([2.5, 2.5])
        cr.move_to(h_mid, 84.0)
        cr.line_to(h_mid, 104.0)
        cr.line_to(x1 + pw * 0.5, 104.0)
        cr.line_to(x1 + pw * 0.5, 124.0)
        cr.move_to(h_mid, 104.0)
        cr.line_to(x1 + pw + 10.0 + pw * 0.5, 104.0)
        cr.line_to(x1 + pw + 10.0 + pw * 0.5, 124.0)
        cr.stroke()
        cr.restore()
        self._draw_pill(cr, h_mid, 104.0, 68.0, 19.0, t(f"cc_system.{p}_bp_shared"), pal["vitality"], pal["structure_bg"], f_sub)

        # Zone 2: Drift & Encrypted Vault
        x2 = zxs[1] + 12.0
        self._draw_zone_hdr(cr, x2, 10.0, uw2, t(f"cc_system.{p}_bp_z2"), pal["vitality"], f_hdr, pal)
        vx = x2 + 58.0
        vw = max(116.0, uw2 - 60.0)
        self._draw_node(
            cr, vx, 38.0, vw, 86.0,
            t(f"cc_system.{p}_bp_vault"), t(f"cc_system.{p}_bp_vault_sub"),
            pal["vitality"], pal["vitality_bg"], f_title, f_sub, pal,
            double_border=True,
        )
        # Mini vector vault bars inside Vault box
        for i, col in enumerate((pal["intent"], pal["vitality"], pal["attention"])):
            rounded_rect_path(cr, vx + 10.0, 88.0 + i * 9.0, vw - 20.0 - i * 16.0, 5.0, 2.0)
            cr.set_source_rgba(*hex_to_rgba_f(col, 0.78))
            cr.fill()

        # Push & Pull arrows crossing from Zone 1 into Vault
        self._arrow(cr, x1 + uw1 - 4.0, 56.0, vx - 2.0, 56.0, pal["vitality"])
        self._draw_pill(cr, x2 + 25.0, 44.0, 50.0, 19.0, f"↑ {t(f'cc_system.{p}_bp_push')}", pal["vitality"], pal["structure_bg"], f_sub)
        self._arrow(cr, vx - 2.0, 102.0, x1 + uw1 - 4.0, 102.0, pal["attention"])
        self._draw_pill(cr, x2 + 25.0, 114.0, 50.0, 19.0, f"↓ {t(f'cc_system.{p}_bp_pull')}", pal["attention"], pal["structure_bg"], f_sub)

        self._draw_node(
            cr, x2 + 6.0, 140.0, uw2 - 12.0, 30.0,
            f"⇄ {t(f'cc_system.{p}_bp_diff')}", "",
            pal["schema"], surf_hex, f_sub, f_sub, pal,
        )

        # Zone 3: Tower of Hanoi Rotation Matrix (Backup Tier x Session)
        x3 = zxs[2] + 12.0
        self._draw_zone_hdr(cr, x3, 10.0, uw3, t(f"cc_system.{p}_bp_z3"), pal["interrupt"], f_hdr, pal)
        self._arrow(cr, vx + vw, 82.0, x3 - 2.0, 82.0, pal["vitality"], dashed=True)

        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")

        def _mk_font(scale: float, bold: bool = False) -> Pango.FontDescription:
            fd = Pango.FontDescription()
            fd.set_family(font_family)
            if bold:
                fd.set_weight(Pango.Weight.BOLD)
            fd.set_size(int(round(base_pt * scale * Pango.SCALE)))
            return fd

        f_mat_lbl = _mk_font(0.74, bold=True)
        f_mat_cell = _mk_font(0.74, bold=True)
        f_mat_num = _mk_font(0.65, bold=False)
        f_mat_num_b = _mk_font(0.65, bold=True)
        f_mat_leg = _mk_font(0.74, bold=False)

        cols = 16
        rows = 4
        ty = 34.0
        hdr_h = 24.0
        row_h = 26.0
        lbl_w = max(68.0, min(80.0, uw3 * 0.235))
        cw = (uw3 - lbl_w) / float(cols)
        mat_h = hdr_h + rows * row_h

        def _sched_tier(col_num: int) -> int:
            if col_num <= 1:
                return 3
            c = col_num - 1
            tz = 0
            while (c & 1) == 0 and tz < 3:
                c >>= 1
                tz += 1
            return tz

        latest = {0: 16, 1: 15, 2: 13, 3: 9}
        tiers = [
            ("A", t(f"cc_system.{p}_bp_ring0"), pal["intent"], pal["intent_bg"]),
            ("B", t(f"cc_system.{p}_bp_ring1"), pal["vitality"], pal["vitality_bg"]),
            ("C", t(f"cc_system.{p}_bp_ring2"), pal["attention"], pal["attention_bg"]),
            ("D", t(f"cc_system.{p}_bp_ring3"), pal["interrupt"], pal["interrupt_bg"]),
        ]
        col_retained_tier = {col_num: tier_idx for tier_idx, col_num in latest.items()}

        # Outer table frame & background
        rounded_rect_path(cr, x3 + 0.5, ty + 0.5, uw3 - 1.0, mat_h - 1.0, 4.0)
        cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.62))
        cr.fill()

        cr.save()
        rounded_rect_path(cr, x3 + 0.5, ty + 0.5, uw3 - 1.0, mat_h - 1.0, 4.0)
        cr.clip()

        # Top-left diagonal corner header
        cr.rectangle(x3, ty, lbl_w, hdr_h)
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.72))
        cr.fill()
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.65))
        cr.set_line_width(0.8)
        cr.move_to(x3, ty)
        cr.line_to(x3 + lbl_w, ty + hdr_h)
        cr.stroke()
        cr.restore()
        self._text_centered(cr, t(f"cc_system.{p}_bp_axis_sess"), x3 + 4.0, ty + 1.5, lbl_w - 7.0, hdr_h * 0.50, f_mat_num, pal["schema"], alpha=0.92, align=Pango.Alignment.RIGHT)
        self._text_centered(cr, t(f"cc_system.{p}_bp_axis_lvl"), x3 + 4.0, ty + hdr_h * 0.48, lbl_w - 7.0, hdr_h * 0.50, f_mat_num, pal["schema"], alpha=0.92, align=Pango.Alignment.LEFT)

        # Session column headers 1..16
        for c_idx in range(cols):
            col_num = c_idx + 1
            cx0 = x3 + lbl_w + c_idx * cw
            ret_tier = col_retained_tier.get(col_num)
            if ret_tier is not None:
                _ltr, _lbl, tcol, tbg = tiers[ret_tier]
                cr.rectangle(cx0, ty, cw, hdr_h)
                cr.set_source_rgba(*hex_to_rgba_f(tbg, 0.92))
                cr.fill()
                cr.rectangle(cx0, ty, cw, 2.0)
                cr.set_source_rgba(*hex_to_rgba_f(tcol, 0.96))
                cr.fill()
                self._text_centered(cr, str(col_num), cx0, ty + 1.5, cw, hdr_h - 2.0, f_mat_num_b, tcol, alpha=0.98)
            else:
                cr.rectangle(cx0, ty, cw, hdr_h)
                cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.55))
                cr.fill()
                self._text_centered(cr, str(col_num), cx0, ty + 1.0, cw, hdr_h - 1.0, f_mat_num, pal["schema"], alpha=0.84)

        # Rows: tier labels & scheduled cells
        for r_idx, (ltr, rlbl, rcol, rbg) in enumerate(tiers):
            ry = ty + hdr_h + r_idx * row_h
            # Row label background + left accent bar
            cr.rectangle(x3, ry, lbl_w, row_h)
            cr.set_source_rgba(*hex_to_rgba_f(rbg, 0.40))
            cr.fill()
            cr.rectangle(x3, ry + 1.0, 2.5, row_h - 2.0)
            cr.set_source_rgba(*hex_to_rgba_f(rcol, 0.95))
            cr.fill()
            self._text_centered(cr, rlbl, x3 + 6.0, ry, lbl_w - 8.0, row_h, f_mat_lbl, rcol, alpha=0.95, align=Pango.Alignment.LEFT)

            # Cells
            for c_idx in range(cols):
                col_num = c_idx + 1
                if _sched_tier(col_num) != r_idx:
                    continue
                cx0 = x3 + lbl_w + c_idx * cw
                pad_x = 1.6
                pad_y = 2.6
                if col_num == latest[r_idx]:
                    rounded_rect_path(cr, cx0 + pad_x, ry + pad_y, cw - pad_x * 2.0, row_h - pad_y * 2.0, 2.5)
                    cr.set_source_rgba(*hex_to_rgba_f(rbg, 0.95))
                    cr.fill_preserve()
                    cr.set_source_rgba(*hex_to_rgba_f(rcol, 0.95))
                    cr.set_line_width(1.1)
                    cr.stroke()
                    self._text_centered(cr, ltr, cx0, ry, cw, row_h, f_mat_cell, rcol, alpha=1.0)
                else:
                    rounded_rect_path(cr, cx0 + pad_x, ry + pad_y, cw - pad_x * 2.0, row_h - pad_y * 2.0, 2.5)
                    cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.26))
                    cr.fill_preserve()
                    cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.55))
                    cr.set_line_width(0.8)
                    cr.stroke()
                    self._text_centered(cr, ltr, cx0, ry, cw, row_h, f_mat_cell, pal["inactive"], alpha=0.90)

        # Internal thin grid lines
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.42))
        cr.set_line_width(0.7)
        for c_idx in range(cols):
            vx_line = math.floor(x3 + lbl_w + c_idx * cw) + 0.5
            cr.move_to(vx_line, ty)
            cr.line_to(vx_line, ty + mat_h)
        for r_idx in range(rows):
            hy_line = math.floor(ty + hdr_h + r_idx * row_h) + 0.5
            cr.move_to(x3, hy_line)
            cr.line_to(x3 + uw3, hy_line)
        cr.stroke()
        cr.restore()

        cr.restore()  # end outer rounded table clip

        # Crisp outer border of table
        rounded_rect_path(cr, x3 + 0.5, ty + 0.5, uw3 - 1.0, mat_h - 1.0, 4.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.85))
        cr.set_line_width(1.0)
        cr.stroke()

        # Compact legend strip below matrix
        ly = ty + mat_h + 8.0
        bx_dim = x3 + 2.0
        rounded_rect_path(cr, bx_dim, ly + 1.0, 10.0, 10.0, 2.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.28))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.65))
        cr.set_line_width(0.8)
        cr.stroke()
        tw_old, _ = self._text(cr, t(f"cc_system.{p}_bp_mat_old"), bx_dim + 15.0, ly - 0.5, f_mat_leg, pal["schema"], alpha=0.90)

        bx_kept = bx_dim + 15.0 + tw_old + 14.0
        rounded_rect_path(cr, bx_kept, ly + 1.0, 10.0, 10.0, 2.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent_bg"], 0.95))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["intent"], 0.95))
        cr.set_line_width(1.0)
        cr.stroke()
        self._text(
            cr,
            t(f"cc_system.{p}_bp_mat_kept"),
            bx_kept + 15.0,
            ly - 0.5,
            f_mat_leg,
            pal["focus_val"],
            alpha=0.94,
            max_w=max(60.0, x3 + uw3 - (bx_kept + 15.0)),
        )

    def _on_draw(self, _widget, cr) -> bool:
        from gi.repository import Pango

        alloc = self.get_allocation()
        w = max(480.0, float(alloc.width or self.widget_width))
        h = max(190.0, float(alloc.height or self.widget_height))
        bx = float(self.inset_x)
        bw = max(360.0, w - 2.0 * bx)
        footer_h = float(self.footer_h)
        stage_h = h - footer_h

        pal = get_semantic_palette()
        surf_hex = str((THEME.get("colors") or {}).get("bg_panel") or pal["structure_bg"])
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")

        # 1. Outer recessed Tactical Blueprint frame
        rounded_rect_path(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure_bg"], 0.64))
        cr.fill_preserve()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.90))
        cr.set_line_width(1.0)
        cr.stroke()

        cr.save()
        rounded_rect_path(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        # Subtle background blueprint dots
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.25))
        gx = bx + 18.0
        while gx < bx + bw - 12.0:
            gy = 20.0
            while gy < stage_h - 10.0:
                cr.rectangle(gx, gy, 1.2, 1.2)
                gy += 18.0
            gx += 18.0
        cr.fill()
        cr.restore()

        # Fonts
        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        f_hdr = Pango.FontDescription()
        f_hdr.set_family(font_family)
        f_hdr.set_weight(Pango.Weight.BOLD)
        f_hdr.set_size(int(round(base_pt * 0.88 * Pango.SCALE)))

        f_title = Pango.FontDescription()
        f_title.set_family(font_family)
        f_title.set_weight(Pango.Weight.BOLD)
        f_title.set_size(int(round(base_pt * 0.94 * Pango.SCALE)))

        f_sub = Pango.FontDescription()
        f_sub.set_family(font_family)
        f_sub.set_size(int(round(base_pt * 0.84 * Pango.SCALE)))

        # 2. Vertical dashed dividers splitting into 3 zones
        if self.bp_mode == "backups":
            zws = [bw * 0.32, bw * 0.275, bw * 0.405]
            zxs = [bx, bx + zws[0], bx + zws[0] + zws[1]]
        else:
            zw = bw / 3.0
            zws = [zw, zw, zw]
            zxs = [bx, bx + zw, bx + zw * 2.0]
        cr.save()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.42))
        cr.set_line_width(1.0)
        cr.set_dash([2.5, 4.0])
        for dx in (zxs[1], zxs[2]):
            cr.move_to(math.floor(dx) + 0.5, 12.0)
            cr.line_to(math.floor(dx) + 0.5, stage_h - 10.0)
        cr.stroke()
        cr.restore()

        if self.bp_mode == "recovery":
            self._draw_recovery(cr, zxs, zws[0], f_hdr, f_title, f_sub, pal, surf_hex)
        elif self.bp_mode == "snapshots":
            self._draw_snapshots(cr, zxs, zws[0], f_hdr, f_title, f_sub, pal, surf_hex)
        else:
            self._draw_backups(cr, zxs, zws, f_hdr, f_title, f_sub, pal, surf_hex)

        # 3. Integrated Telemetry Footer Strip
        fy = stage_h
        cr.set_source_rgba(*hex_to_rgba_f(surf_hex, 0.80))
        cr.rectangle(bx + 1.0, fy, bw - 2.0, footer_h)
        cr.fill()
        cr.set_source_rgba(*hex_to_rgba_f(pal["structure"], 0.72))
        cr.set_line_width(1.0)
        cr.move_to(bx + 1.0, fy + 0.5)
        cr.line_to(bx + bw - 1.0, fy + 0.5)
        cr.stroke()

        fpad = 12.0
        rw = 0.0
        if self.status_right:
            rw, rh = self._text(
                cr, self.status_right, bx + bw - fpad - 210.0, fy + 6.0, f_sub,
                pal["vitality"], alpha=0.92, max_w=210.0, align=Pango.Alignment.RIGHT,
            )
        self._text(
            cr, t(f"cc_system.{self.bp_prefix}_bp_footer"), bx + fpad, fy + 6.0, f_sub,
            pal["schema"], alpha=0.90, max_w=max(80.0, bw - fpad * 2.0 - (rw + 16.0 if self.status_right else 0.0)),
        )
        cr.restore()
        return False


_RESILIENCE_BP_CLS = None


def ResilienceBlueprintWidget(*args, **kwargs):
    global _RESILIENCE_BP_CLS
    if _RESILIENCE_BP_CLS is None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        class _ResilienceBlueprint(_ResilienceBlueprintMixin, Gtk.DrawingArea):
            pass

        _RESILIENCE_BP_CLS = _ResilienceBlueprint
    return _RESILIENCE_BP_CLS(*args, **kwargs)


def _build_guide_box(
    cc_panel,
    canvas: Canvas,
    state_key: str,
    prefix: str,
    mode: str,
    status_right: str = "",
    text_prefix: str = "",
) -> list:
    """Build a collapsible Cairo Tactical Vector Blueprint + legend card (open by default)."""
    from ui.widgets import AsciiCard

    tpfx = text_prefix or prefix
    is_open, fold_btn = FoldToggle(cc_panel, state_key, default=True, uri_prefix="ui:state")
    if not is_open:
        box = Box(
            t(f"cc_system.{prefix}_title"),
            icon="\uf059",
            actions=[fold_btn],
            highlighted=False,
        )
        box.add_row(" ")
        box.add_row(f"  {Txt(t(f'cc_system.{tpfx}_sum'), role='schema')}")
        box.add_row(" ")
        return [box]

    box_top = Box(
        t(f"cc_system.{prefix}_title"),
        icon="\uf059",
        actions=[fold_btn],
        highlighted=False,
        open_bottom=True,
    )
    box_top.add_row(" ")
    box_top.add_row(f"  {Txt(t(f'cc_system.{tpfx}_intro'), role='schema')}")
    box_top.add_row(" ")

    foot = Box("", highlighted=False, open_top=True)
    foot.add_row(" ")
    for idx in range(1, 4):
        foot.add_kv(
            t(f"cc_system.{tpfx}_k{idx}"),
            Txt(t(f"cc_system.{tpfx}_v{idx}"), role="schema"),
        )
    foot.add_row(" ")

    inset_px = max(14.0, float(canvas.char_w) * 2.0)
    bp = ResilienceBlueprintWidget(
        mode=mode,
        prefix=prefix,
        status_right=status_right,
        inset_x=inset_px,
    )
    return [AsciiCard(box_top, canvas, embedded_widget=bp, footer_box_model=foot)]


from panels.cc_engine_telemetry_vault import (  # noqa: F401
    handle_vault_link,
    handle_vault_prompt,
    is_vault_mounted as _is_nas_mounted,
    probe_vault_status as _probe_vault_status,
    prompt_smb_user as _prompt_smb_user,
)

def _probe_nas_shares() -> dict:
    return _probe_vault_status()


def _probe_rescue_env() -> dict:
    rescue_bin = resolve_sbin_helper("nc-rescue")
    chroot_bin = resolve_sbin_helper("nc-chroot")
    golden_bin = resolve_sbin_helper("nc-golden")
    r_env = _read_env_kv("/var/lib/ncui/rescue-status.env")
    origin_env = _read_env_kv(os.environ.get("NC_RECOVERY_ORIGIN_FILE", "/etc/ncui/recovery-origin.env"))
    is_live = (
        os.path.isdir("/run/recovery-ram/upper")
        or r_env.get("LIVE_RAM_ACTIVE") == "1"
        or r_env.get("NC_RECOVERY_IMAGE") == "1"
        or origin_env.get("NC_RECOVERY_IMAGE") == "1"
    )
    return {
        "is_live": is_live,
        "origin_host": origin_env.get("NC_ORIGIN_HOSTNAME") or r_env.get("ORIGIN_HOSTNAME", ""),
        "origin_luks_uuid": origin_env.get("NC_ORIGIN_LUKS_UUID", ""),
        "has_commit": os.path.isfile(rescue_bin),
        "has_chroot": os.path.isfile(chroot_bin),
        "has_golden": os.path.isfile(golden_bin),
        "rescue_bin": rescue_bin,
        "chroot_bin": chroot_bin,
        "golden_bin": golden_bin,
    }


def _probe_golden_images(nas_base: str) -> list[dict]:
    res_cfg = load_resilience_config()
    sub_parts = [
        str(p).strip("/")
        for p in (res_cfg["vault"].get("backup_root_subdir"), res_cfg["recovery"].get("golden_subdir"))
        if str(p or "").strip("/")
    ]
    img_root = os.path.join(nas_base, *sub_parts) if sub_parts else os.path.join(nas_base, "images")
    if not os.path.isdir(img_root):
        return []
    out = []
    try:
        names = sorted(os.listdir(img_root))
    except Exception:
        return []
    for name in names:
        mdir = os.path.join(img_root, name)
        mfile = os.path.join(mdir, "manifest.env")
        if not os.path.isfile(mfile):
            continue
        kv = _read_env_kv(mfile)
        date_str = kv.get("IMAGE_CREATED", "").strip()
        if not date_str:
            try:
                with open(mfile, "r", encoding="utf-8", errors="ignore") as f:
                    for _ in range(5):
                        ln = f.readline()
                        m = re.search(r"(\d{4}-\d{2}-\d{2})", ln or "")
                        if m:
                            date_str = m.group(1)
                            break
            except Exception:
                pass
        sz_b = 0
        try:
            for idx_e, ent in enumerate(os.scandir(mdir)):
                if idx_e > 16:
                    break
                if ent.is_file(follow_symlinks=False):
                    sz_b += ent.stat(follow_symlinks=False).st_size
        except Exception:
            pass
        out.append({
            "dir_name": name,
            "title": kv.get("IMAGE_TITLE") or name,
            "date": date_str[:10] if date_str else "-",
            "vg": kv.get("VG_NAME") or "vg0",
            "desc": kv.get("IMAGE_DESCRIPTION") or "-",
            "size": format_bytes_short(sz_b) if sz_b > 0 else "-",
            "has_squashfs": os.path.isfile(os.path.join(mdir, "recovery.squashfs")),
            "has_sha256": os.path.isfile(os.path.join(mdir, "sha256sums.txt")),
        })
        if len(out) >= 12:
            break
    return out


def _collect_backup_data() -> dict:
    nas_info = _probe_nas_shares()
    nas_mounted = nas_info["mounted"]
    snaps = []
    snap_meta_dir = "/var/lib/ncui/snapshots"
    if os.path.isdir(snap_meta_dir):
        try:
            snaps = sorted(fn for fn in os.listdir(snap_meta_dir) if not fn.startswith("."))
        except Exception:
            pass
    hanoi_env = _read_env_kv("/var/lib/nc-nas-conf/status.env")
    dr_env = _read_env_kv("/var/lib/ncui/dr-status.env")
    rescue_env = _read_env_kv("/var/lib/ncui/rescue-status.env")
    res_cfg = load_resilience_config()
    snap_cfg = res_cfg["snapshots"]
    vault_cfg = res_cfg["vault"]
    ident_cfg = res_cfg["identity"]
    rec_cfg = res_cfg["recovery"]
    esp = snap_cfg.get("esp_mount", "/boot")
    uki_dir = os.path.join(esp, "EFI", "Linux")
    uki_prefix = snap_cfg.get("uki_prefix") or res_cfg["identity"].get("hostname", "linux").lower()
    rescue_uki_ok = (
        rescue_env.get("UKI_PRESENT") == "1"
        or dr_env.get("RESCUE_READY") == "1"
        or any(
            os.path.exists(p)
            for p in (
                os.path.join(uki_dir, rec_cfg.get("uki_name", "nc-rescue.efi")),
                os.path.join(uki_dir, "nc-rescue.efi"),
            )
        )
    )
    snap_slots = []
    for slot in ("auto", "stable"):
        meta = _read_env_kv(f"/var/lib/ncui/snapshots/{slot}.meta")
        uki_file = meta.get("UKI_FILE") or f"{uki_prefix}-{slot}.efi"
        uki_present = (meta.get("UKI_PRESENT") == "1") if "UKI_PRESENT" in meta else os.path.isfile(os.path.join(uki_dir, uki_file))
        max_pct_raw = str(meta.get("MAX_COW_PCT") or "0").strip()
        try:
            max_pct = float(max_pct_raw)
        except ValueError:
            max_pct = 0.0
        snap_slots.append({
            "slot": slot,
            "present": bool(meta),
            "created": meta.get("CREATED_AT", "-") if meta else "-",
            "kernel": meta.get("KERNEL", "-") if meta else "-",
            "note": meta.get("NOTE", "-") if meta else "-",
            "volumes": meta.get("VOLUMES", "") if meta else "",
            "uki_present": bool(uki_present),
            "uki_file": uki_file,
            "cow_summary": meta.get("COW_SUMMARY", "") if meta else "",
            "max_cow_pct": max_pct,
            "lv_health": meta.get("LV_HEALTH", "ACTIVE") if meta else "ABSENT",
        })

    broken_lvs = []
    for item in str(dr_env.get("BROKEN_LVS", "") or "").split(","):
        it_s = item.strip()
        if not it_s:
            continue
        if ":" in it_s:
            lv_n, lv_sz = it_s.split(":", 1)
            broken_lvs.append({"lv": lv_n.strip(), "size": lv_sz.strip()})
        else:
            broken_lvs.append({"lv": it_s, "size": "-"})

    priv = get_privileged_action("snapshot_manage")
    helper = resolve_sbin_helper("nc-snap")
    os_fam = probe.get_os_release()["family"]
    hook_kind = (
        "pacman" if (os_fam == "arch" or os.path.isdir("/etc/pacman.d"))
        else ("apt" if (os_fam == "debian" or os.path.isdir("/etc/apt/apt.conf.d")) else "none")
    )
    hook_active = (
        os.path.isfile("/etc/pacman.d/hooks/00-nc-snapshot.hook")
        or os.path.isfile("/etc/apt/apt.conf.d/00-nc-snapshot")
        or (dr_env.get("SNAP_HOOK") == "1")
        or (dr_env.get("PACMAN_HOOK") == "1")
    )

    ts_raw = hanoi_env.get("NC_STATUS_TIMESTAMP", "")
    last_check = ""
    if ts_raw.isdigit():
        try:
            last_check = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts_raw)))
        except Exception:
            last_check = ts_raw
    hanoi_summary = {
        "has_data": bool(hanoi_env),
        "sys_alias": hanoi_env.get("NC_STATUS_SYS", "-"),
        "last_check": last_check or "-",
        "push_total": int(hanoi_env.get("NC_PUSH_TOTAL", "0") or "0") if str(hanoi_env.get("NC_PUSH_TOTAL", "0")).isdigit() else 0,
        "pull_total": int(hanoi_env.get("NC_PULL_TOTAL", "0") or "0") if str(hanoi_env.get("NC_PULL_TOTAL", "0")).isdigit() else 0,
    }
    priv_r = get_privileged_action("rescue_manage")
    chroot_mnt = rescue_env.get("CHROOT_MNT") or "/run/ncui-chroot"
    return {
        "nas_mounted": nas_mounted,
        "nas_info": nas_info,
        "vault_cfg": vault_cfg,
        "ident_cfg": ident_cfg,
        "rec_cfg": rec_cfg,
        "rescue_env": rescue_env,
        "priv_rescue_ok": bool(priv_r and priv_r.check_installed()),
        "rescue_busy": str(STATE.get("system", "rescue_busy", "") or ""),
        "rescue_confirm_build": bool(STATE.get("system", "rescue_confirm_build", False)),
        "rescue_confirm_rb": bool(STATE.get("system", "rescue_confirm_rb", False)),
        "golden_verified": STATE.get("system", "golden_verified_map", {}) or {},
        "chroot_active": os.path.ismount(chroot_mnt) or rescue_env.get("CHROOT_ACTIVE") == "1",
        "chroot_cands": STATE.get("system", "chroot_candidates", None),
        "hanoi_timer_on": os.path.exists("/etc/systemd/system/timers.target.wants/nc-nas-conf.timer"),
        "hanoi_list_items": STATE.get("system", "hanoi_list_items", None),
        "hanoi_hist_items": STATE.get("system", "hanoi_hist_items", None),
        "pkglist_items": STATE.get("system", "pkglist_items", None),
        "hanoi_selected_path": str(STATE.get("system", "hanoi_selected_path", "") or ""),
        "hanoi_versions_map": STATE.get("system", "hanoi_versions_map", {}) or {},
        "hanoi_confirm_restore": str(STATE.get("system", "hanoi_confirm_restore", "") or ""),
        "hanoi_confirm_ver_restore": str(STATE.get("system", "hanoi_confirm_ver_restore", "") or ""),
        "vault_busy": str(STATE.get("system", "vault_busy", "") or ""),
        "confirm_prune": bool(STATE.get("system", "hanoi_confirm_prune", False)),
        "snaps": snaps,
        "snap_slots": snap_slots,
        "snap_cfg": snap_cfg,
        "broken_lvs": broken_lvs,
        "priv_ok": bool(priv and priv.check_installed()),
        "helper": helper,
        "hook_kind": hook_kind,
        "pacman_hook": hook_active,
        "busy_slot": str(STATE.get("system", "snap_busy_slot", "") or ""),
        "confirm_del": str(STATE.get("system", "snap_confirm_del", "") or ""),
        "confirm_promote": bool(STATE.get("system", "snap_confirm_promote", False)),
        "confirm_purge": str(STATE.get("system", "snap_confirm_purge", "") or ""),
        "note_draft": str(STATE.get("system", "snap_note_draft", "") or ""),
        "dr_env": dr_env,
        "hanoi_env": hanoi_env,
        "hanoi_summary": hanoi_summary,
        "golden_images": _probe_golden_images(nas_info["base"]) if nas_mounted else [],
        "rescue_uki_ok": rescue_uki_ok,
        "rescue_env_info": _probe_rescue_env(),
    }



def _exec_nc_snap_priv(cc_panel, args: list[str], stdin_data: str = "", timeout: float = 15.0) -> tuple[bool, bool, str]:
    opened, rc, out = _exec_priv_helper(cc_panel, "nc-snap", "snapshot_manage", args, stdin_data=stdin_data, timeout=timeout)
    return opened, (rc == 0), out


def _run_nc_snap_async(cc_panel, busy_tag: str, args: list[str], stdin_data: str = "", ok_msg: str = "") -> bool:
    def _done(opened_modal: bool, rc: int, out: str):
        if opened_modal:
            return
        if rc == 0:
            set_feedback("system", True, ok_msg or t("cc_system.snap_fb_health"))
        else:
            msg_s = (out.splitlines()[-1] if out else "error")[:140]
            set_feedback("system", False, t("cc_system.snap_fb_err", p0=msg_s))

    return _run_helper_async(
        cc_panel,
        "snap_busy_slot",
        busy_tag,
        "nc-snap",
        "snapshot_manage",
        args,
        stdin_data=stdin_data,
        timeout=90.0,
        on_done=_done,
    )


def _parse_hanoi_list_tsv(raw: str) -> tuple[dict, list[dict]]:
    summary = {}
    items = []
    for ln in (raw or "").splitlines():
        parts = ln.split("\t")
        if not parts:
            continue
        if parts[0] == "SUMMARY" and len(parts) >= 8:
            summary = {
                "alias": parts[1],
                "folder": parts[2],
                "real_count": parts[3],
                "shared_count": parts[4],
                "push_total": parts[5],
                "pull_total": parts[7],
            }
        elif parts[0] == "ITEM" and len(parts) >= 5:
            items.append({
                "state": parts[1],
                "role": parts[2],
                "shared": parts[3],
                "path": parts[4],
            })
    items.sort(key=lambda it: (1 if it["state"] == "IN_SYNC" else 0, it["path"]))
    return summary, items


def _parse_hanoi_hist_tsv(raw: str) -> list[dict]:
    out = []
    for ln in (raw or "").splitlines():
        parts = ln.split("\t")
        if len(parts) >= 6 and parts[0] == "HIST":
            out.append({
                "owner": parts[1],
                "count": parts[2],
                "latest": parts[3],
                "active": parts[4] == "1",
                "path": parts[5],
            })
    return out


def _parse_pkglist_tsv(raw: str) -> list[dict]:
    out = []
    for ln in (raw or "").splitlines():
        parts = ln.split("\t")
        if len(parts) >= 5:
            out.append({
                "num": parts[0],
                "label": parts[1],
                "count": parts[2],
                "date": parts[3],
                "file": parts[4],
            })
    return out


def _parse_hanoi_versions_tsv(raw: str) -> list[dict]:
    out = []
    for ln in (raw or "").splitlines():
        parts = ln.split("\t")
        if len(parts) >= 8 and parts[0] == "VER":
            out.append({
                "idx": parts[1],
                "ts": parts[2],
                "slot": parts[3],
                "event": parts[4],
                "author": parts[5],
                "diff": parts[6],
                "bak_path": parts[7],
            })
    return out


def _refresh_pkglists_bg(cc_panel) -> None:
    """Refresh Vault pkglists via unprivileged `nc-vault pkglist-tsv` without overwriting feedback."""
    def _worker():
        v_bin = resolve_sbin_helper("nc-vault")
        rc, out, _ = probe.run_cmd_full([v_bin, "pkglist-tsv"], timeout=6.0)
        items = _parse_pkglist_tsv(out) if rc == 0 else None

        def _finish():
            if items is not None:
                STATE.set("system", "pkglist_items", items)
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return False

        try:
            from gi.repository import GLib
            GLib.idle_add(_finish)
        except Exception:
            _finish()

    threading.Thread(target=_worker, daemon=True).start()


def _refresh_hanoi_list_silent(cc_panel, also_pkglists: bool = False) -> None:
    """Reload Vault Catalog (`nc-hanoi --list-tsv`) without clobbering the active feedback banner."""
    def _on_silent_list(opened: bool, rc: int, out: str):
        if opened:
            return
        if rc == 0:
            _sum, items = _parse_hanoi_list_tsv(out)
            STATE.set("system", "hanoi_list_items", items)
        if also_pkglists:
            v_bin = resolve_sbin_helper("nc-vault")
            rc_p, out_p, _ = probe.run_cmd_full([v_bin, "pkglist-tsv"], timeout=6.0)
            if rc_p == 0:
                STATE.set("system", "pkglist_items", _parse_pkglist_tsv(out_p))

    _run_helper_async(cc_panel, "vault_busy", "SCAN", "nc-hanoi", "hanoi_sync", ["--list-tsv"], timeout=60.0, on_done=_on_silent_list)



def handle_cc_system_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_sys:"):
        return False
    act = uri[len("cc_sys:"):]

    if act == "pkglist_prompt":
        cc_panel.request_prompt("cc_sys_pkglist", t("cc_system.pkglist_prompt_lbl"), "", title=t("cc_system.pkglist_prompt_title"))
        return True
    elif handle_vault_link(cc_panel, uri, on_mounted_cb=lambda: _refresh_hanoi_list_silent(cc_panel, also_pkglists=True)):
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    elif act == "snap_auth":
        request_privilege_modal(cc_panel, "snapshot_manage")
    elif act == "snap_health":
        opened, ok, out = _exec_nc_snap_priv(cc_panel, ["check-health"], timeout=10.0)
        if not opened:
            set_feedback("system", ok, t("cc_system.snap_fb_health") if ok else t("cc_system.snap_fb_err", p0=(out or "error")[:140]))
    elif act in ("snap_hook:on", "snap_hook:off"):
        cmd_arg = "hook-on" if act.endswith(":on") else "hook-off"
        is_apt = (probe.get_os_release()["family"] == "debian" or (not os.path.isdir("/etc/pacman.d") and os.path.isdir("/etc/apt/apt.conf.d")))
        if is_apt:
            msg_k = "cc_system.snap_fb_apt_hook_on" if act.endswith(":on") else "cc_system.snap_fb_apt_hook_off"
        else:
            msg_k = "cc_system.snap_fb_hook_on" if act.endswith(":on") else "cc_system.snap_fb_hook_off"
        opened, ok, out = _exec_nc_snap_priv(cc_panel, [cmd_arg], timeout=10.0)
        if not opened:
            set_feedback("system", ok, t(msg_k) if ok else t("cc_system.snap_fb_err", p0=(out or "error")[:140]))
    elif act == "snap_note_open":
        cur_note = str(STATE.get("system", "snap_note_draft", "") or "")
        start_inline_input(
            "snap_note_draft",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_note,
            max_len=56,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda s: bool(re.match(r"^[a-zA-Z0-9 _.,:/()+-]{0,56}$", s.strip())),
            on_commit=lambda s: STATE.set("system", "snap_note_draft", s.strip()),
        )
        return True
    elif act == "snap_note_clear":
        STATE.set("system", "snap_note_draft", "")
    elif act in ("snap_capture:auto", "snap_capture:stable"):
        slot = act.split(":", 1)[1]
        note_s = str(STATE.get("system", "snap_note_draft", "") or "").strip()
        STATE.set("system", "snap_confirm_del", "")
        _run_nc_snap_async(
            cc_panel,
            slot,
            ["create-stdin"],
            stdin_data=f"{slot}|{note_s}\n",
            ok_msg=t("cc_system.snap_fb_captured", p0=slot.upper()),
        )
    elif act in ("snap_extend:auto", "snap_extend:stable"):
        slot = act.split(":", 1)[1]
        cfg_s = load_resilience_config()["snapshots"]
        target_lv = cfg_s.get("root_lv") or "root"
        meta_s = _read_env_kv(f"/var/lib/ncui/snapshots/{slot}.meta")
        best_pct = -1.0
        for part in str(meta_s.get("COW_SUMMARY", "") or "").split("·"):
            m_lv = re.match(r"^\s*([a-zA-Z0-9_+.-]+)\s+([0-9]+(?:\.[0-9]+)?)%/", part)
            if m_lv:
                try:
                    pct_v = float(m_lv.group(2))
                except ValueError:
                    pct_v = 0.0
                if pct_v > best_pct:
                    best_pct = pct_v
                    target_lv = m_lv.group(1)
        _run_nc_snap_async(
            cc_panel,
            slot,
            ["extend-stdin"],
            stdin_data=f"snap_{slot}_{target_lv}|+5G\n",
            ok_msg=t("cc_system.snap_fb_extended", p0=slot),
        )
    elif act in ("snap_del_ask:auto", "snap_del_ask:stable"):
        STATE.set("system", "snap_confirm_del", act.split(":", 1)[1])
    elif act == "snap_del_cancel":
        STATE.set("system", "snap_confirm_del", "")
    elif act in ("snap_del_do:auto", "snap_del_do:stable"):
        slot = act.split(":", 1)[1]
        STATE.set("system", "snap_confirm_del", "")
        _run_nc_snap_async(
            cc_panel,
            slot,
            [f"delete-{slot}"],
            ok_msg=t("cc_system.snap_fb_deleted", p0=slot.upper()),
        )
    elif act == "snap_promote_ask":
        STATE.set("system", "snap_confirm_promote", True)
    elif act == "snap_promote_cancel":
        STATE.set("system", "snap_confirm_promote", False)
    elif act == "snap_promote_do":
        STATE.set("system", "snap_confirm_promote", False)
        slot_cur = _collect_backup_data()["dr_env"].get("BOOTED_SLOT", "snapshot")
        _run_nc_snap_async(
            cc_panel,
            "promote",
            ["promote-confirm"],
            ok_msg=t("cc_system.snap_fb_promoted", p0=slot_cur.upper()),
        )
    elif act.startswith("snap_purge_ask:"):
        STATE.set("system", "snap_confirm_purge", act.split(":", 1)[1])
    elif act == "snap_purge_cancel":
        STATE.set("system", "snap_confirm_purge", "")
    elif act.startswith("snap_purge_do:"):
        lv_n = act.split(":", 1)[1]
        STATE.set("system", "snap_confirm_purge", "")
        if re.match(r"^[a-zA-Z0-9_-]+_broken_[0-9]{8,}$", lv_n):
            _run_nc_snap_async(
                cc_panel,
                "purge",
                ["purge-broken-stdin"],
                stdin_data=f"{lv_n}\n",
                ok_msg=t("cc_system.snap_fb_purged", p0=lv_n),
            )
    elif act.startswith("snap_cfg:") and ":" in act[len("snap_cfg:"):]:
        k, v = act[len("snap_cfg:"):].split(":", 1)
        esp_allowed = set(probe.detect_esp_mounts()) | {"/boot", "/efi", "/boot/efi"}
        valid_cfg = (
            (k == "backend" and v in ("lvm_uki", "btrfs"))
            or (k == "esp_mount" and v in esp_allowed)
            or (k == "var_lv" and v in ("", "var"))
            or (k == "home_lv" and v in ("", "home"))
            or (k in ("snap_root_size", "snap_var_size") and bool(re.match(r"^[0-9]{1,4}[GM]$", v)))
            or (k == "btrfs_subvol" and bool(re.match(r"^[a-zA-Z0-9@_./-]{1,48}$", v)) and ".." not in v)
        )
        if valid_cfg:
            opened, ok, out = _exec_nc_snap_priv(cc_panel, ["config-stdin"], stdin_data=f"{k}={v}\n", timeout=8.0)
            if not opened:
                disp = f"{k}={v or 'off'}"
                set_feedback("system", ok, t("cc_system.snap_fb_cfg_saved", p0=disp) if ok else t("cc_system.snap_fb_err", p0=(out or "error")[:140]))
    elif act in ("snap_prefix_open", "snap_vg_open", "snap_subvol_open"):
        cfg_cur = load_resilience_config()["snapshots"]
        meta_map = {
            "snap_prefix_open": ("uki_prefix", cfg_cur.get("uki_prefix", ""), 32, r"^[a-zA-Z0-9_-]{1,40}$"),
            "snap_vg_open": ("vg_name", cfg_cur.get("vg_name", "vg0"), 32, r"^[a-zA-Z0-9_+.-]{1,48}$"),
            "snap_subvol_open": ("btrfs_subvol", cfg_cur.get("btrfs_subvol", "@"), 32, r"^[a-zA-Z0-9@_./-]{1,48}$"),
        }
        cfg_k, init_v, ml, pat = meta_map[act]

        def _commit_cfg_field(new_val: str, key=cfg_k, pattern=pat):
            val_s = new_val.strip()
            if not val_s or ".." in val_s or not re.match(pattern, val_s):
                set_feedback("system", False, t("cc_system.snap_fb_err", p0=f"{key}={val_s}"))
                return
            opened, ok, out = _exec_nc_snap_priv(cc_panel, ["config-stdin"], stdin_data=f"{key}={val_s}\n", timeout=8.0)
            if not opened:
                set_feedback("system", ok, t("cc_system.snap_fb_cfg_saved", p0=f"{key}={val_s}") if ok else t("cc_system.snap_fb_err", p0=(out or "error")[:140]))

        start_inline_input(
            act[:-5],
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(init_v),
            max_len=ml,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda s, p=pat: bool(s.strip()) and ".." not in s and bool(re.match(p, s.strip())),
            on_commit=_commit_cfg_field,
        )
        return True
    elif act in ("hanoi_push", "hanoi_pull", "hanoi_check", "hanoi_list"):
        if not _is_nas_mounted():
            set_feedback("system", False, t("cc_system.nas_not_mounted_mount_nas_10_10_0"))
        elif act == "hanoi_push":
            def _on_push(opened: bool, rc: int, out: str):
                if opened:
                    return
                if rc == 0:
                    line = (out.splitlines()[-1] if out else "").replace("SYNC_OK|", "")
                    msg = t("cc_system.hanoi_fb_synced", p0=line or "ok")
                    if "conflicts=" in line and not line.endswith("conflicts=0"):
                        msg = f"{msg} — {t('cc_system.hanoi_fb_conflicts_note')}"
                    STATE.set("system", "hanoi_versions_map", {})
                    STATE.set("system", "hanoi_hist_items", None)
                    set_feedback("system", True, msg)
                    _refresh_hanoi_list_silent(cc_panel)
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(cc_panel, "vault_busy", "PUSH", "nc-hanoi", "hanoi_sync", ["--sync-auto"], timeout=120.0, on_done=_on_push)
        elif act == "hanoi_pull":
            def _on_pull(opened: bool, rc: int, out: str):
                if opened:
                    return
                if rc == 0:
                    line = (out.splitlines()[-1] if out else "").replace("PULL_OK|", "")
                    msg = t("cc_system.hanoi_fb_pulled", p0=line or "ok")
                    if "conflicts=" in line and not line.endswith("conflicts=0"):
                        msg = f"{msg} — {t('cc_system.hanoi_fb_conflicts_note')}"
                    STATE.set("system", "hanoi_versions_map", {})
                    STATE.set("system", "hanoi_hist_items", None)
                    set_feedback("system", True, msg)
                    _refresh_hanoi_list_silent(cc_panel)
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(cc_panel, "vault_busy", "PULL", "nc-hanoi", "hanoi_sync", ["--pull-auto"], timeout=120.0, on_done=_on_pull)
        elif act == "hanoi_check":
            def _on_chk(opened: bool, rc: int, out: str):
                if opened:
                    return
                if rc == 0:
                    _sum, items = _parse_hanoi_list_tsv(out)
                    STATE.set("system", "hanoi_list_items", items)
                    STATE.set("system", "hanoi_hist_items", None)
                    STATE.set("system", "hanoi_versions_map", {})
                    v_bin = resolve_sbin_helper("nc-vault")
                    rc_p, out_p, _ = probe.run_cmd_full([v_bin, "pkglist-tsv"], timeout=6.0)
                    if rc_p == 0:
                        STATE.set("system", "pkglist_items", _parse_pkglist_tsv(out_p))
                    set_feedback("system", True, t("cc_system.hanoi_fb_checked", p0=_sum.get("push_total", "0"), p1=_sum.get("pull_total", "0")))
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(cc_panel, "vault_busy", "CHECK", "nc-hanoi", "hanoi_sync", ["--list-tsv"], timeout=60.0, on_done=_on_chk)
        elif act == "hanoi_list":
            def _on_list(opened: bool, rc: int, out: str):
                if opened:
                    return
                if rc == 0:
                    _sum, items = _parse_hanoi_list_tsv(out)
                    STATE.set("system", "hanoi_list_items", items)
                    set_feedback("system", True, t("cc_system.hanoi_fb_loaded", p0=len(items)))
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(cc_panel, "vault_busy", "SCAN", "nc-hanoi", "hanoi_sync", ["--list-tsv"], timeout=60.0, on_done=_on_list)
    elif act == "hanoi_load_hist":
        if not _is_nas_mounted():
            set_feedback("system", False, t("cc_system.nas_not_mounted_mount_nas_10_10_0"))
        else:
            def _on_hist(opened: bool, rc: int, out: str):
                if opened:
                    return
                if rc == 0:
                    hist = _parse_hanoi_hist_tsv(out)
                    STATE.set("system", "hanoi_hist_items", hist)
                    v_bin = resolve_sbin_helper("nc-vault")
                    rc_p, out_p, _ = probe.run_cmd_full([v_bin, "pkglist-tsv"], timeout=6.0)
                    if rc_p == 0:
                        STATE.set("system", "pkglist_items", _parse_pkglist_tsv(out_p))
                    set_feedback("system", True, t("cc_system.hanoi_fb_loaded", p0=len(hist)))
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(cc_panel, "vault_busy", "HIST", "nc-hanoi", "hanoi_sync", ["--history-tsv"], timeout=60.0, on_done=_on_hist)
    elif act == "hanoi_prune_ask":
        STATE.set("system", "hanoi_confirm_prune", True)
    elif act == "hanoi_prune_cancel":
        STATE.set("system", "hanoi_confirm_prune", False)
    elif act == "hanoi_prune_do":
        STATE.set("system", "hanoi_confirm_prune", False)
        def _on_prune(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                STATE.set("system", "hanoi_versions_map", {})
                STATE.set("system", "hanoi_hist_items", None)
                set_feedback("system", True, t("cc_system.hanoi_fb_pruned"))
                if STATE.get("system", "hanoi_view_mode", "") == "history":
                    def _on_hist_silent(op_h: bool, rc_h: int, out_h: str):
                        if not op_h and rc_h == 0:
                            STATE.set("system", "hanoi_hist_items", _parse_hanoi_hist_tsv(out_h))

                    _run_helper_async(cc_panel, "vault_busy", "HIST", "nc-hanoi", "hanoi_sync", ["--history-tsv"], timeout=60.0, on_done=_on_hist_silent)
            else:
                set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "vault_busy", "PRUNE", "nc-hanoi", "hanoi_sync", ["--prune-auto"], timeout=90.0, on_done=_on_prune)
    elif act in ("hanoi_timer:on", "hanoi_timer:off"):
        arg_t = "timer-on" if act.endswith(":on") else "timer-off"
        msg_k = "cc_system.hanoi_fb_timer_on" if act.endswith(":on") else "cc_system.hanoi_fb_timer_off"
        opened, rc, out = _exec_priv_helper(cc_panel, "nc-hanoi", "hanoi_sync", [arg_t], timeout=15.0)
        if not opened:
            set_feedback("system", rc == 0, t(msg_k) if rc == 0 else t("cc_system.hanoi_fb_err", p0=(out or "error")[:140]))
    elif act == "hanoi_add_prompt":
        if not _is_nas_mounted():
            set_feedback("system", False, t("cc_system.nas_not_mounted_mount_nas_10_10_0"))
        else:
            cc_panel.request_prompt("cc_sys_hanoi_add", t("cc_system.prompt_hanoi_add_lbl"), "", title=t("cc_system.prompt_hanoi_add_title"), completion="path_file", required=True)
            return True
    elif act == "dc_commit":
        rb = resolve_sbin_helper("nc-rescue")
        arg = ["commit"] if os.path.basename(rb) == "nc-rescue" else []
        _start_recovery_embedded_shell(cc_panel, "cc_system.btn_dc_commit", ["sudo", rb, *arg])
        return True
    elif act == "dc_commit_rebuild":
        rb = resolve_sbin_helper("nc-rescue")
        arg = "commit-rebuild" if os.path.basename(rb) == "nc-rescue" else "--rebuild"
        _start_recovery_embedded_shell(cc_panel, "cc_system.btn_dc_commit_rebuild", ["sudo", rb, arg])
        return True
    elif act == "dc_chroot":
        cb = resolve_sbin_helper("nc-chroot")
        _start_recovery_embedded_shell(cc_panel, "cc_system.rescue_chroot_term_btn", ["sudo", cb])
        return True
    elif act == "dc_chroot_ext":
        cb = resolve_sbin_helper("nc-chroot")
        if probe.open_terminal(["sudo", cb]):
            set_feedback("system", True, t("cc_system.rescue_chroot_ext_opened"))
        else:
            set_feedback("system", False, t("cc_system.rescue_chroot_ext_no_term"))
        return True
    elif act == "dc_golden_save":
        gb = resolve_sbin_helper("nc-golden")
        _start_recovery_embedded_shell(cc_panel, "cc_system.btn_dc_save_img", ["sudo", gb, "--save"])
        return True
    elif act == "dc_golden_restore":
        gb = resolve_sbin_helper("nc-golden")
        _start_recovery_embedded_shell(cc_panel, "cc_system.btn_dc_restore_img", ["sudo", gb, "--restore"])
        return True
    elif act == "rescue_refresh":
        def _on_ref(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                env_m = {}
                for ln in (out or "").splitlines():
                    if "=" in ln:
                        k_e, v_e = ln.split("=", 1)
                        env_m[k_e.strip()] = v_e.strip().strip('"')
                _op_c, rc_c, out_c = _exec_priv_helper(cc_panel, "nc-chroot", "rescue_manage", ["candidates-tsv"], timeout=8.0)
                if rc_c == 0:
                    cands = []
                    for ln in (out_c or "").splitlines():
                        parts = ln.split("\t")
                        if len(parts) >= 7 and parts[0] == "CAND":
                            cands.append({
                                "dev": parts[1],
                                "size": parts[2],
                                "model": parts[3],
                                "esp": parts[4],
                                "mapper": parts[5],
                                "scope": parts[6],
                            })
                    STATE.set("system", "chroot_candidates", cands)
                set_feedback(
                    "system",
                    True,
                    t("cc_system.rescue_fb_probed", p0=env_m.get("ESP_MOUNT", "/boot"), p1=env_m.get("ESP_FREE_HUMAN", "-")),
                )
            else:
                set_feedback("system", False, t("cc_system.rescue_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "rescue_busy", "PROBE", "nc-rescue", "rescue_manage", ["check-health"], timeout=25.0, on_done=_on_ref)
    elif act == "rescue_build_ask":
        STATE.set("system", "rescue_confirm_build", True)
    elif act == "rescue_build_cancel":
        STATE.set("system", "rescue_confirm_build", False)
    elif act == "rescue_build_do":
        STATE.set("system", "rescue_confirm_build", False)
        def _on_build(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                set_feedback("system", True, t("cc_system.rescue_fb_built"))
            else:
                set_feedback("system", False, t("cc_system.rescue_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "rescue_busy", "BUILD", "nc-rescue", "rescue_manage", ["build-uki"], timeout=240.0, on_done=_on_build)
    elif act == "rescue_rb_ask":
        STATE.set("system", "rescue_confirm_rb", True)
    elif act == "rescue_rb_cancel":
        STATE.set("system", "rescue_confirm_rb", False)
    elif act == "rescue_rb_do":
        STATE.set("system", "rescue_confirm_rb", False)
        def _on_rb(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                set_feedback("system", True, t("cc_system.rescue_fb_rolled_back"))
            else:
                set_feedback("system", False, t("cc_system.rescue_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "rescue_busy", "ROLLBACK", "nc-rescue", "rescue_manage", ["rollback-esp"], timeout=60.0, on_done=_on_rb)
    elif act == "chroot_scan":
        def _on_cands(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                cands = []
                for ln in (out or "").splitlines():
                    parts = ln.split("\t")
                    if len(parts) >= 7 and parts[0] == "CAND":
                        cands.append({
                            "dev": parts[1],
                            "size": parts[2],
                            "model": parts[3],
                            "esp": parts[4],
                            "mapper": parts[5],
                            "scope": parts[6],
                        })
                STATE.set("system", "chroot_candidates", cands)
                ext_n = sum(1 for c in cands if c["scope"] != "HOST")
                set_feedback("system", True, t("cc_system.rescue_fb_cands", p0=len(cands), p1=ext_n))
            else:
                set_feedback("system", False, t("cc_system.rescue_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "rescue_busy", "SCAN", "nc-chroot", "rescue_manage", ["candidates-tsv"], timeout=15.0, on_done=_on_cands)
    elif act == "chroot_umount":
        def _on_um(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                _exec_priv_helper(cc_panel, "nc-rescue", "rescue_manage", ["check-health"], timeout=10.0)
                set_feedback("system", True, t("cc_system.rescue_fb_chroot_umount"))
            else:
                set_feedback("system", False, t("cc_system.rescue_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "rescue_busy", "UMOUNT", "nc-chroot", "rescue_manage", ["-u"], timeout=20.0, on_done=_on_um)
    elif act.startswith("golden_verify:"):
        img_d = act.split(":", 1)[1]
        if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$", img_d) and ".." not in img_d:
            def _on_ver(opened: bool, rc: int, out: str, target_img=img_d):
                if opened:
                    return
                vmap = dict(STATE.get("system", "golden_verified_map", {}) or {})
                if rc == 0 and "VERIFY_OK|" in out:
                    parts = out.splitlines()[-1].split("|")
                    cnt = parts[2] if len(parts) > 2 else "?"
                    vmap[target_img] = True
                    STATE.set("system", "golden_verified_map", vmap)
                    set_feedback("system", True, t("cc_system.rescue_fb_verified", p0=target_img, p1=cnt))
                else:
                    vmap[target_img] = False
                    STATE.set("system", "golden_verified_map", vmap)
                    set_feedback("system", False, t("cc_system.rescue_fb_verify_err", p0=target_img))

            _run_helper_async(
                cc_panel,
                "rescue_busy",
                "SHA256",
                "nc-golden",
                "rescue_manage",
                ["verify-stdin"],
                stdin_data=f"{img_d}\n",
                timeout=600.0,
                on_done=_on_ver,
            )
    elif act.startswith("rescue_cfg:") and ":" in act[len("rescue_cfg:"):]:
        k, v = act[len("rescue_cfg:"):].split(":", 1)
        if k == "sign_sbctl" and v in ("true", "false"):
            opened, rc, out = _exec_priv_helper(cc_panel, "nc-rescue", "rescue_manage", ["config-stdin"], stdin_data=f"sign_sbctl={v}\n", timeout=10.0)
            if not opened:
                set_feedback("system", rc == 0, t("cc_system.rescue_fb_cfg_saved", p0=f"sign_sbctl={v}") if rc == 0 else t("cc_system.rescue_fb_err", p0=(out or "error")[:140]))
    elif act in ("rescue_uki_open", "rescue_gdir_open"):
        rcfg = load_resilience_config()["recovery"]
        rmap = {
            "rescue_uki_open": ("uki_name", rcfg.get("uki_name", "nc-rescue.efi"), 48, r"^[a-zA-Z0-9._-]{1,64}\.efi$"),
            "rescue_gdir_open": ("golden_subdir", rcfg.get("golden_subdir", "images"), 64, r"^[a-zA-Z0-9_./-]{1,80}$"),
        }
        cfg_k, init_v, ml, pat = rmap[act]

        def _commit_rescue_field(new_val: str, key=cfg_k, pattern=pat):
            val_s = new_val.strip()
            if not val_s or ".." in val_s or not re.match(pattern, val_s):
                set_feedback("system", False, t("cc_system.rescue_fb_err", p0=f"{key}={val_s}"))
                return
            opened, rc, out = _exec_priv_helper(cc_panel, "nc-rescue", "rescue_manage", ["config-stdin"], stdin_data=f"{key}={val_s}\n", timeout=10.0)
            if not opened:
                set_feedback("system", rc == 0, t("cc_system.rescue_fb_cfg_saved", p0=f"{key}={val_s}") if rc == 0 else t("cc_system.rescue_fb_err", p0=(out or "error")[:140]))

        start_inline_input(
            act[:-5],
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(init_v),
            max_len=ml,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda s, p=pat: bool(s.strip()) and ".." not in s and bool(re.match(p, s.strip())),
            on_commit=_commit_rescue_field,
        )
        return True
    elif act == "rescue_build_full_term":
        rb = resolve_sbin_helper("nc-rescue")
        _start_recovery_embedded_shell(cc_panel, "cc_system.rescue_build_sq_btn", ["sudo", rb, "--build-recovery"])
        return True
    elif act == "rescue_edit_term":
        rb = resolve_sbin_helper("nc-rescue")
        _start_recovery_embedded_shell(cc_panel, "cc_system.rescue_edit_sq_btn", ["sudo", rb, "edit"])
        return True
    elif act == "rescue_manage_term":
        rb = resolve_sbin_helper("nc-rescue")
        _start_recovery_embedded_shell(cc_panel, "cc_system.rescue_manage_btn", ["sudo", rb, "--manage"])
        return True
    elif act == "rescue_sync_ncui_term":
        rb = resolve_sbin_helper("nc-rescue")
        _start_recovery_embedded_shell(cc_panel, "cc_system.rescue_sync_ncui_btn", ["sudo", rb, "--sync-ncui"])
        return True
    elif act == "rescue_reg_limine":
        def _on_reg(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                set_feedback("system", True, t("cc_system.rescue_fb_reg_limine"))
            else:
                set_feedback("system", False, t("cc_system.rescue_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "rescue_busy", "LIMINE", "nc-rescue", "rescue_manage", ["register-limine"], timeout=20.0, on_done=_on_reg)
    elif act in ("snap_select:auto", "snap_select:stable"):
        STATE.set("system", "snap_selected_slot", act.split(":", 1)[1])
    elif act.startswith("hanoi_view:"):
        vw = act.split(":", 1)[1]
        if vw in ("tracked", "drift", "history", "pkglists"):
            STATE.set("system", "hanoi_view_mode", vw)
            STATE.set("system", "hanoi_confirm_restore", "")
            STATE.set("system", "hanoi_confirm_ver_restore", "")
            if vw == "history" and STATE.get("system", "hanoi_hist_items", None) is None and _is_nas_mounted():
                return handle_cc_system_link(cc_panel, "cc_sys:hanoi_load_hist")
            if vw == "pkglists" and STATE.get("system", "pkglist_items", None) is None and _is_nas_mounted():
                _refresh_pkglists_bg(cc_panel)
            if vw in ("tracked", "drift") and STATE.get("system", "hanoi_list_items", None) is None and _is_nas_mounted():
                return handle_cc_system_link(cc_panel, "cc_sys:hanoi_list")
    elif act == "hanoi_filter_open":
        cur_q = str(STATE.get("system", "hanoi_search_q", "") or "")
        start_inline_input(
            "cc_sys_hanoi_filter",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=48,
            strip_leading_hash=False,
            uppercase=False,
            on_commit=lambda s: STATE.set("system", "hanoi_search_q", s.strip()),
        )
        return True
    elif act == "hanoi_filter_clear":
        STATE.set("system", "hanoi_search_q", "")
    elif act == "hanoi_toggle_all":
        STATE.set("system", "hanoi_show_all", not bool(STATE.get("system", "hanoi_show_all", False)))
    elif act.startswith("hanoi_select:"):
        sel_p = act[len("hanoi_select:"):].strip()
        if sel_p.startswith("/") and ".." not in sel_p:
            STATE.set("system", "hanoi_selected_path", sel_p)
            STATE.set("system", "hanoi_confirm_restore", "")
            STATE.set("system", "hanoi_confirm_ver_restore", "")
            vmap = STATE.get("system", "hanoi_versions_map", {}) or {}
            if sel_p not in vmap and _is_nas_mounted():
                return handle_cc_system_link(cc_panel, "cc_sys:hanoi_load_versions")
    elif act == "hanoi_load_versions":
        sel_p = str(STATE.get("system", "hanoi_selected_path", "") or "").strip()
        if sel_p.startswith("/") and ".." not in sel_p:
            def _on_vers(opened: bool, rc: int, out: str, target_p=sel_p):
                if opened:
                    return
                if rc == 0:
                    vmap = dict(STATE.get("system", "hanoi_versions_map", {}) or {})
                    vmap[target_p] = _parse_hanoi_versions_tsv(out)
                    STATE.set("system", "hanoi_versions_map", vmap)
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(
                cc_panel,
                "vault_busy",
                "HIST",
                "nc-hanoi",
                "hanoi_sync",
                ["--versions-tsv-stdin"],
                stdin_data=f"{sel_p}\n",
                timeout=35.0,
                on_done=_on_vers,
            )
    elif act == "hanoi_push_one":
        sel_p = str(STATE.get("system", "hanoi_selected_path", "") or "").strip()
        if sel_p.startswith("/") and ".." not in sel_p:
            def _on_push1(opened: bool, rc: int, out: str, target_p=sel_p):
                if opened:
                    return
                if rc == 0:
                    vmap = dict(STATE.get("system", "hanoi_versions_map", {}) or {})
                    vmap.pop(target_p, None)
                    STATE.set("system", "hanoi_versions_map", vmap)
                    STATE.set("system", "hanoi_hist_items", None)
                    set_feedback("system", True, t("cc_system.hanoi_fb_push_one", p0=os.path.basename(target_p)))
                    _refresh_hanoi_list_silent(cc_panel)
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(
                cc_panel,
                "vault_busy",
                "PUSH",
                "nc-hanoi",
                "hanoi_sync",
                ["--push-one-stdin"],
                stdin_data=f"{sel_p}\n",
                timeout=45.0,
                on_done=_on_push1,
            )
    elif act == "hanoi_restore_ask":
        STATE.set("system", "hanoi_confirm_restore", str(STATE.get("system", "hanoi_selected_path", "") or ""))
    elif act == "hanoi_restore_cancel":
        STATE.set("system", "hanoi_confirm_restore", "")
    elif act == "hanoi_restore_do":
        sel_p = str(STATE.get("system", "hanoi_selected_path", "") or "").strip()
        STATE.set("system", "hanoi_confirm_restore", "")
        if sel_p.startswith("/") and ".." not in sel_p:
            def _on_rest(opened: bool, rc: int, out: str, target_p=sel_p):
                if opened:
                    return
                if rc == 0:
                    STATE.set("system", "hanoi_versions_map", {})
                    STATE.set("system", "hanoi_hist_items", None)
                    set_feedback("system", True, t("cc_system.hanoi_fb_restored", p0=os.path.basename(target_p)))
                    _refresh_hanoi_list_silent(cc_panel)
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(
                cc_panel,
                "vault_busy",
                "RESTORE",
                "nc-hanoi",
                "hanoi_sync",
                ["--restore-stdin"],
                stdin_data=f"{sel_p}\n",
                timeout=45.0,
                on_done=_on_rest,
            )
    elif act == "hanoi_share_open":
        sel_p = str(STATE.get("system", "hanoi_selected_path", "") or "").strip()
        if sel_p.startswith("/") and ".." not in sel_p:
            items = STATE.get("system", "hanoi_list_items", None) or []
            cur_sh = ""
            for it in items:
                if it.get("path") == sel_p:
                    cur_sh = "" if it.get("shared") == "-" else str(it.get("shared") or "")
                    break

            def _commit_sh(spec_raw: str, target_p=sel_p):
                sp = spec_raw.strip().replace(" ", "") or "local"
                if not re.match(r"^(all|local|[a-z0-9_,-]+)$", sp):
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=sp))
                    return

                def _on_sh(opened: bool, rc: int, out: str):
                    if opened:
                        return
                    if rc == 0:
                        set_feedback("system", True, t("cc_system.hanoi_fb_shared", p0=os.path.basename(target_p), p1=sp))
                        _refresh_hanoi_list_silent(cc_panel)
                    else:
                        set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

                _run_helper_async(
                    cc_panel,
                    "vault_busy",
                    "SHARE",
                    "nc-hanoi",
                    "hanoi_sync",
                    ["--share-stdin"],
                    stdin_data=f"{target_p}|{sp}\n",
                    timeout=35.0,
                    on_done=_on_sh,
                )

            start_inline_input(
                "cc_sys_hanoi_share",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=cur_sh,
                max_len=40,
                strip_leading_hash=False,
                uppercase=False,
                validator=lambda s: not s.strip() or bool(re.match(r"^(all|local|[a-z0-9_,-]+)$", s.strip().replace(" ", ""))),
                on_commit=_commit_sh,
            )
            return True
    elif act == "hanoi_diff_term":
        sel_p = str(STATE.get("system", "hanoi_selected_path", "") or "").strip()
        if sel_p.startswith("/") and ".." not in sel_p:
            from ui.embedded_tui import start_panel_terminal

            h_bin = resolve_sbin_helper("nc-hanoi")
            start_panel_terminal(
                cc_panel,
                t("common.term_title", p0=f"diff {os.path.basename(sel_p)}"),
                ["sudo", h_bin, "--diff", sel_p],
            )
        return True
    elif act.startswith("hanoi_ver_diff:"):
        v_idx = act.split(":", 1)[1].strip()
        sel_p = str(STATE.get("system", "hanoi_selected_path", "") or "").strip()
        vlist = (STATE.get("system", "hanoi_versions_map", {}) or {}).get(sel_p, [])
        match = next((v for v in vlist if str(v.get("idx")) == v_idx), None)
        if match and sel_p.startswith("/") and ".." not in sel_p:
            from ui.embedded_tui import start_panel_terminal

            h_bin = resolve_sbin_helper("nc-hanoi")
            start_panel_terminal(
                cc_panel,
                t("common.term_title", p0=f"diff {os.path.basename(sel_p)} #{v_idx}"),
                ["sudo", h_bin, "--diff-bak", match["bak_path"], sel_p],
            )
        return True
    elif act.startswith("hanoi_ver_restore_ask:"):
        STATE.set("system", "hanoi_confirm_ver_restore", act.split(":", 1)[1].strip())
    elif act == "hanoi_ver_restore_cancel":
        STATE.set("system", "hanoi_confirm_ver_restore", "")
    elif act.startswith("hanoi_ver_restore_do:"):
        v_idx = act.split(":", 1)[1].strip()
        STATE.set("system", "hanoi_confirm_ver_restore", "")
        sel_p = str(STATE.get("system", "hanoi_selected_path", "") or "").strip()
        vlist = (STATE.get("system", "hanoi_versions_map", {}) or {}).get(sel_p, [])
        match = next((v for v in vlist if str(v.get("idx")) == v_idx), None)
        if match and sel_p.startswith("/") and ".." not in sel_p:
            def _on_vrest(opened: bool, rc: int, out: str, target_p=sel_p, ts_s=match["ts"]):
                if opened:
                    return
                if rc == 0:
                    vmap = dict(STATE.get("system", "hanoi_versions_map", {}) or {})
                    vmap.pop(target_p, None)
                    STATE.set("system", "hanoi_versions_map", vmap)
                    STATE.set("system", "hanoi_hist_items", None)
                    set_feedback("system", True, t("cc_system.hanoi_fb_ver_restored", p0=os.path.basename(target_p), p1=ts_s))
                    _refresh_hanoi_list_silent(cc_panel)
                else:
                    set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

            _run_helper_async(
                cc_panel,
                "vault_busy",
                "ROLLBACK",
                "nc-hanoi",
                "hanoi_sync",
                ["--restore-bak-stdin"],
                stdin_data=f"{sel_p}|{match['bak_path']}|full\n",
                timeout=45.0,
                on_done=_on_vrest,
            )

    else:
        return False

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def ensure_backups_loaded(cc_panel) -> None:
    if os.environ.get("NC_UI_OFFSCREEN") == "1":
        return
    if STATE.get("system", "backups_autoloaded", False):
        return
    STATE.set("system", "backups_autoloaded", True)
    if not _is_nas_mounted():
        return
    priv_h = get_privileged_action("hanoi_sync")
    need_list = (STATE.get("system", "hanoi_list_items", None) is None) and bool(priv_h and priv_h.check_installed())
    need_pkgs = STATE.get("system", "pkglist_items", None) is None
    if need_list:
        _refresh_hanoi_list_silent(cc_panel, also_pkglists=need_pkgs)
    elif need_pkgs:
        _refresh_pkglists_bg(cc_panel)




def handle_cc_telemetry_link(cc_panel, uri: str) -> bool:
    return handle_cc_system_link(cc_panel, uri)

def handle_cc_telemetry_prompt(cc_panel, action_id: str, text: str) -> bool:
    if action_id == "cc_sys_pkglist":
        nm = str(text or "").strip() or "snapshot"
        def _on_pkg(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0 and "OK|" in out:
                parts = out.splitlines()[-1].split("|")
                fp = os.path.basename(parts[1]) if len(parts) > 1 else nm
                cnt = parts[2] if len(parts) > 2 else "?"
                aur = parts[3] if len(parts) > 3 else "0"
                _op_p, rc_p, out_p = _exec_priv_helper(cc_panel, "nc-vault", "vault_mount", ["pkglist-tsv"], timeout=10.0)
                if rc_p == 0:
                    STATE.set("system", "pkglist_items", _parse_pkglist_tsv(out_p))
                set_feedback("system", True, t("cc_system.vault_fb_pkg_saved", p0=fp, p1=cnt, p2=aur))
            else:
                set_feedback("system", False, t("cc_system.vault_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "vault_busy", "PKGLIST", "nc-vault", "vault_mount", ["pkglist-stdin"], stdin_data=f"{nm}\n", timeout=20.0, on_done=_on_pkg)
        return True
    if handle_vault_prompt(cc_panel, action_id, text, on_mounted_cb=lambda: _refresh_hanoi_list_silent(cc_panel, also_pkglists=True)):
        return True
    if action_id == "cc_sys_hanoi_add":
        add_p = str(text or "").strip()
        if not add_p or ".." in add_p or not add_p.startswith("/") or not os.path.exists(add_p):
            set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=add_p or "missing path"))
            return True
        def _on_add(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                STATE.set("system", "hanoi_hist_items", None)
                set_feedback("system", True, t("cc_system.hanoi_fb_added", p0=add_p))
                _refresh_hanoi_list_silent(cc_panel)
            else:
                set_feedback("system", False, t("cc_system.hanoi_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(
            cc_panel,
            "vault_busy",
            "TRACK",
            "nc-hanoi",
            "hanoi_sync",
            ["--add-stdin"],
            stdin_data=f"{add_p}||0\n",
            timeout=60.0,
            on_done=_on_add,
        )
        return True
    return False

