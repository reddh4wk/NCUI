#!/usr/bin/env python3
import math
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo  # noqa: E402

from core.config import THEME  # noqa: E402
from core.i18n import t  # noqa: E402
from core.privileges import build_privilege_modal_card  # noqa: E402
from core.state import STATE  # noqa: E402
from ui.canvas import Canvas  # noqa: E402
from ui.kit import (  # noqa: E402
    ActionBtn,
    Badge,
    Box,
    InspectorCell,
    ProgressBar,
    SelectPill,
    StateBadge,
    Txt,
    get_semantic_palette,
)
from ui.markup import format_bytes_short  # noqa: E402
from ui.widgets import AsciiCard  # noqa: E402
from panels.cc_engine_disks import (  # noqa: E402
    _collect_disks_data,
    _feedback_cards,
    ensure_disks_probed,
)


def _use_role(pct: float | None) -> str:
    if pct is None:
        return "inactive"
    if pct >= 90.0:
        return "fault"
    if pct >= 75.0:
        return "interrupt"
    if pct >= 50.0:
        return "attention"
    return "val"


def _temp_role(temp_c: int | None) -> str:
    if temp_c is None:
        return "inactive"
    if temp_c >= 70:
        return "fault"
    if temp_c >= 60:
        return "attention"
    return "vitality"


def _hex_rgba(hx: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    s = str(hx or "#ffffff").strip().lstrip("#")
    if len(s) == 8:
        s = s[2:]
    if len(s) == 6:
        try:
            return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0, float(alpha))
        except ValueError:
            pass
    return (1.0, 1.0, 1.0, float(alpha))


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(float(r), max(0.0, w * 0.5), max(0.0, h * 0.5))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi * 0.5, 0.0)
    cr.arc(x + w - r, y + h - r, r, 0.0, math.pi * 0.5)
    cr.arc(x + r, y + h - r, r, math.pi * 0.5, math.pi)
    cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
    cr.close_path()


def _usage_hex(pct: float | None, pal: dict) -> str:
    if pct is None:
        return pal["structure"]
    if pct >= 90.0:
        return pal["fault"]
    if pct >= 75.0:
        return pal["interrupt"]
    if pct >= 50.0:
        return pal["attention"]
    return pal["vitality"]


def _part_palette(seg: dict, pal: dict, surf_hex: str) -> tuple[str, str, bool]:
    """Return (border_hex, fill_hex, is_dashed) for a physical partition or LV segment."""
    if seg.get("is_unalloc") or seg.get("is_vg_free"):
        return pal["structure"], surf_hex, True
    fst = str(seg.get("fstype") or "").lower()
    fl = str(seg.get("flags") or "").upper()
    mp = str(seg.get("mountpoint") or "")
    if seg.get("is_snap"):
        return pal["attention"], pal["attention_bg"], False
    if "ESP" in fl or fst == "vfat" or mp in ("/boot", "/efi"):
        return pal["vitality"], pal["vitality_bg"], False
    if "LUKS" in fl or "CRYPT" in fl or fst == "crypto_luks" or fst == "lvm2_member":
        return pal["attention"], pal["attention_bg"], False
    if "SWAP" in fl or fst == "swap" or mp == "SWAP":
        return pal["interrupt"], pal["interrupt_bg"], False
    if mp == "/":
        return pal["vitality"], pal["vitality_bg"], False
    if seg.get("mounted"):
        return pal["intent"], pal["intent_bg"], False
    return pal["schema"], pal["structure_bg"], False


def _short_seg_label(seg: dict) -> str:
    if seg.get("is_vg_free"):
        return "FREE"
    if seg.get("is_unalloc"):
        return t("cc_system.disks_map_unalloc")
    mp = str(seg.get("mountpoint") or "-")
    if mp in ("/", "/home", "/var", "/tmp", "/boot", "/efi"):
        return mp
    short = str(seg.get("lv_short") or seg.get("name") or "-")
    if short.startswith("snap_auto_"):
        return "snap_" + short[len("snap_auto_"):][:1]
    if short == "pacmancache":
        return "pacman"
    if mp != "-":
        tail = mp.rstrip("/").rsplit("/", 1)[-1]
        return short if (tail == "tmp" or len(tail) > 9) else tail
    return short[:9]


def _calc_segment_widths(segs: list[dict], total_w: float, gap: float, min_w: float) -> list[float]:
    """Damped proportional widths guaranteeing legible cards even for small ESP/boot slices."""
    n = len(segs)
    if n == 0:
        return []
    avail_w = max(40.0, total_w - gap * max(0, n - 1))
    eff_min = min(min_w, avail_w / float(n))
    weights = [max(0.08, float(max(1, s.get("size_b") or 1)) ** 0.38) for s in segs]
    tot_wt = sum(weights) or 1.0
    widths = [max(eff_min, (wt / tot_wt) * avail_w) for wt in weights]
    scale = avail_w / (sum(widths) or 1.0)
    return [w * scale for w in widths]


class DiskPartitionMapWidget(Gtk.EventBox):
    """Interactive GParted-style 2-track Cairo vector layout & SMART telemetry widget."""

    def __init__(
        self,
        disk: dict,
        disk_vols: list[dict],
        vgs: list[dict],
        selected_vol: str,
        cc_panel,
        inset_x: float = 16.0,
    ) -> None:
        super().__init__()
        self._disk = disk
        self._vols = list(disk_vols or [])
        self._vgs = list(vgs or [])
        self._sel_vol = str(selected_vol or "")
        self._cc_panel = cc_panel
        self._inset_x = max(8.0, float(inset_x))
        self._widget_w = 860
        self._widget_h = 236
        self._footer_h = 28.0
        self._hits: list[tuple[float, float, float, float, dict]] = []
        self._hover_seg: dict | None = None

        self.set_visible_window(False)
        self.set_above_child(True)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("button-press-event", self._on_press)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("leave-notify-event", self._on_leave)

        self._da = Gtk.DrawingArea()
        self._da.set_size_request(360, self._widget_h)
        self._da.set_hexpand(True)
        self._da.connect("size-allocate", self._on_size_allocate)
        self._da.connect("draw", self._on_draw)
        self.add(self._da)

    def _on_size_allocate(self, _w, alloc) -> None:
        if alloc.width > 120 and abs(alloc.width - self._widget_w) > 1:
            self._widget_w = alloc.width
            self._da.queue_draw()

    def _hit_test(self, x: float, y: float) -> dict | None:
        for rx, ry, rw, rh, seg in self._hits:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return seg
        return None

    def _on_motion(self, _w, ev) -> bool:
        seg = self._hit_test(ev.x, ev.y)
        cur_name = seg.get("name") if seg else None
        prev_name = self._hover_seg.get("name") if self._hover_seg else None
        if cur_name != prev_name:
            self._hover_seg = seg
            self._da.queue_draw()
        win = self.get_window()
        if win is not None:
            clickable = bool(seg and seg.get("clickable"))
            cur = Gdk.Cursor.new_from_name(self.get_display(), "pointer" if clickable else "default")
            win.set_cursor(cur)
        return False

    def _on_leave(self, _w, _ev) -> bool:
        if self._hover_seg is not None:
            self._hover_seg = None
            self._da.queue_draw()
        win = self.get_window()
        if win is not None:
            win.set_cursor(None)
        return False

    def _on_press(self, _w, ev) -> bool:
        if ev.button != 1:
            return False
        seg = self._hit_test(ev.x, ev.y)
        if seg and seg.get("clickable") and self._cc_panel is not None:
            from panels.cc_engine_disks import handle_cc_disks_link
            return handle_cc_disks_link(self._cc_panel, f"cc_sys:vol_sel:{seg['name']}")
        return False

    @staticmethod
    def _draw_text(
        cr,
        text: str,
        x: float,
        y: float,
        font_desc,
        color_hex: str,
        alpha: float = 1.0,
        max_w: float = 0.0,
        align=None,
    ) -> tuple[float, float]:
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(font_desc)
        if max_w > 0.0:
            lay.set_width(int(max_w * Pango.SCALE))
            lay.set_ellipsize(Pango.EllipsizeMode.END)
        if align is not None:
            lay.set_alignment(align)
        lay.set_text(str(text), -1)
        _, ext = lay.get_pixel_extents()
        cr.set_source_rgba(*_hex_rgba(color_hex, alpha))
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lay)
        return float(ext.width), float(ext.height)

    def _draw_cassette(
        self,
        cr,
        x: float,
        y: float,
        w: float,
        h: float,
        seg: dict,
        pal: dict,
        surf_hex: str,
        f_title,
        f_sub,
        compact: bool = False,
    ) -> None:
        bcol, fcol, is_dash = _part_palette(seg, pal, surf_hex)
        name = str(seg.get("name") or "")
        is_sel = bool(name and name == self._sel_vol)
        is_hov = bool(self._hover_seg and name and name == self._hover_seg.get("name"))
        draw_border = pal["focus"] if (is_hov or is_sel) else bcol

        cr.save()
        _rounded_rect(cr, x + 0.5, y + 0.5, w - 1.0, h - 1.0, 4.5)
        cr.set_source_rgba(*_hex_rgba(fcol, 0.72 if is_sel else 0.58))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(draw_border, 0.98 if (is_hov or is_sel) else 0.85))
        cr.set_line_width(1.7 if (is_hov or is_sel) else 1.2)
        if is_dash:
            cr.set_dash([3.0, 3.0])
        cr.stroke()
        cr.restore()

        pad = 6.0 if compact else 8.0
        if compact:
            top_lbl = _short_seg_label(seg)
            self._draw_text(cr, top_lbl, x + pad, y + 5.0, f_title, pal["focus"] if not seg.get("is_vg_free") else pal["schema"], alpha=0.95, max_w=w - pad * 2.0)
            self._draw_text(cr, seg.get("size_h", "-"), x + pad, y + 22.0, f_sub, pal["focus_val"], alpha=0.88, max_w=w - pad * 2.0)
            pct = seg.get("use_pct") if seg.get("use_pct") is not None else seg.get("cow_pct")
            bar_y = y + h - 12.0
            bar_w = max(12.0, w - pad * 2.0)
            if pct is not None:
                p_txt = f"C{pct:.0f}%" if seg.get("is_snap") else f"{pct:.0f}%"
                self._draw_text(cr, p_txt, x + pad, y + 39.0, f_sub, pal["schema"], alpha=0.92, max_w=bar_w)
                _rounded_rect(cr, x + pad, bar_y, bar_w, 5.0, 1.5)
                cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.45))
                cr.fill()
                fill_w = max(3.0, bar_w * max(0.0, min(100.0, float(pct))) / 100.0)
                _rounded_rect(cr, x + pad, bar_y, fill_w, 5.0, 1.5)
                cr.set_source_rgba(*_hex_rgba(_usage_hex(pct, pal), 0.94))
                cr.fill()
            elif seg.get("is_vg_free"):
                self._draw_text(cr, t("cc_system.disks_map_vg_free"), x + pad, y + 39.0, f_sub, pal["schema"], alpha=0.80, max_w=bar_w)
            else:
                self._draw_text(cr, str(seg.get("fstype") or "-"), x + pad, y + 39.0, f_sub, pal["schema"], alpha=0.82, max_w=bar_w)
        else:
            sz_txt = str(seg.get("size_h") or "-")
            sz_w = min(w * 0.45, max(44.0, float(len(sz_txt)) * 8.2 + 6.0))
            title_txt = t("cc_system.disks_map_unalloc") if seg.get("is_unalloc") else name
            self._draw_text(cr, title_txt, x + pad, y + 6.0, f_title, pal["focus"], alpha=0.96, max_w=max(20.0, w - pad * 2.0 - sz_w - 4.0))
            self._draw_text(cr, sz_txt, x + w - pad - sz_w, y + 6.0, f_sub, pal["focus_val"], alpha=0.92, max_w=sz_w, align=Pango.Alignment.RIGHT)

            sub_parts = []
            if not seg.get("is_unalloc"):
                sub_parts.append(str(seg.get("fstype") or "-"))
                if seg.get("mountpoint") and seg["mountpoint"] != "-":
                    sub_parts.append(str(seg["mountpoint"]))
                elif seg.get("flags") and seg["flags"] != "-":
                    sub_parts.append(str(seg["flags"]).split(",")[0].strip())
            self._draw_text(cr, " · ".join(sub_parts) or "-", x + pad, y + 24.0, f_sub, pal["schema"], alpha=0.92, max_w=w - pad * 2.0)

            bar_y = y + h - 12.0
            bar_w = max(16.0, w - pad * 2.0)
            pct = seg.get("use_pct")
            _rounded_rect(cr, x + pad, bar_y, bar_w, 5.5, 2.0)
            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.42))
            cr.fill()
            if pct is not None:
                fill_w = max(4.0, bar_w * max(0.0, min(100.0, float(pct))) / 100.0)
                _rounded_rect(cr, x + pad, bar_y, fill_w, 5.5, 2.0)
                cr.set_source_rgba(*_hex_rgba(_usage_hex(pct, pal), 0.94))
                cr.fill()
            elif not seg.get("is_unalloc"):
                # Container stripe across bar
                _rounded_rect(cr, x + pad, bar_y, bar_w, 5.5, 2.0)
                cr.set_source_rgba(*_hex_rgba(bcol, 0.35))
                cr.fill()

        if seg.get("clickable"):
            self._hits.append((x, y, w, h, seg))

    def _on_draw(self, _widget, cr) -> bool:
        alloc = self._da.get_allocation()
        w = max(480.0, float(alloc.width or self._widget_w))
        h = max(190.0, float(alloc.height or self._widget_h))
        bx = float(self._inset_x)
        bw = max(360.0, w - 2.0 * bx)
        footer_h = float(self._footer_h)
        stage_h = h - footer_h
        self._hits = []

        pal = get_semantic_palette()
        surf_hex = str((THEME.get("colors") or {}).get("bg_panel") or pal["structure_bg"])
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")

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

        # 1. Outer recessed Tactical Blueprint frame
        _rounded_rect(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.64))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.90))
        cr.set_line_width(1.0)
        cr.stroke()

        cr.save()
        _rounded_rect(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        # Subtle background blueprint grid dots
        cr.save()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.22))
        gx = bx + 18.0
        while gx < bx + bw - 12.0:
            gy = 20.0
            while gy < stage_h - 8.0:
                cr.rectangle(gx, gy, 1.1, 1.1)
                gy += 18.0
            gx += 18.0
        cr.fill()
        cr.restore()

        pad_x = 14.0
        ux = bx + pad_x
        uw = bw - 2.0 * pad_x

        # 2. Track 1 Header + GParted Physical Partition Bar
        d = self._disk
        sm = d.get("smart") or {}
        t1_lbl = f"{t('cc_system.disks_map_t1')} — {d['path']} ({d['size_h']} · {d['pttype']})"
        self._draw_text(cr, t1_lbl, ux, 8.0, f_hdr, pal["intent"], alpha=0.95, max_w=uw * 0.68)

        rt_parts = []
        if sm.get("temp_c") is not None:
            rt_parts.append(f"{sm['temp_c']}°C")
        if sm.get("wear_pct") is not None:
            rt_parts.append(t("cc_system.disks_wear_lbl", p0=sm["wear_pct"]))
        if sm.get("bytes_written"):
            rt_parts.append(t("cc_system.disks_written_lbl", p0=format_bytes_short(sm["bytes_written"])))
        rt_txt = " · ".join(rt_parts)
        if rt_txt:
            self._draw_text(cr, rt_txt, ux + uw * 0.68, 8.0, f_sub, pal["focus_val"], alpha=0.90, max_w=uw * 0.32, align=Pango.Alignment.RIGHT)

        top_segs = []
        for v in self._vols:
            if v["depth"] == 0:
                s_copy = dict(v)
                s_copy["clickable"] = True
                top_segs.append(s_copy)
        unalloc_b = max(0, int(d.get("size_b", 0)) - int(d.get("alloc_b", 0)))
        if unalloc_b > 1024 ** 3:
            top_segs.append({
                "name": "unallocated",
                "size_b": unalloc_b,
                "size_h": format_bytes_short(unalloc_b),
                "fstype": "free",
                "mountpoint": "-",
                "flags": "-",
                "is_unalloc": True,
                "clickable": False,
            })

        t1_y = 28.0
        t1_h = 62.0
        gap1 = 6.0
        t1_widths = _calc_segment_widths(top_segs, uw, gap1, 108.0)
        cx = ux
        container_rect = None
        for seg, sw in zip(top_segs, t1_widths):
            self._draw_cassette(cr, cx, t1_y, sw, t1_h, seg, pal, surf_hex, f_title, f_sub, compact=False)
            fl = str(seg.get("flags") or "").upper()
            if ("LUKS" in fl or "LVM" in fl) and container_rect is None:
                container_rect = (cx, t1_y + t1_h, sw)
            cx += sw + gap1

        # 3. Track 2: Either LVM/LUKS Expansion OR SMART Gauges
        nested_vols = [dict(v, clickable=True) for v in self._vols if v["depth"] >= 1 and not (v["type"] == "crypt" and any(x["depth"] > v["depth"] for x in self._vols))]
        lvm_vols = [v for v in nested_vols if v["type"] == "lvm"]
        show_nested = nested_vols if lvm_vols else nested_vols

        if show_nested:
            # GParted trapezoidal projection lines from container partition down to Track 2
            t2_bar_y = 126.0
            t2_bar_h = 74.0
            if container_rect is not None:
                px0, py0, pw0 = container_rect
                cr.save()
                cr.set_source_rgba(*_hex_rgba(pal["attention"], 0.60))
                cr.set_line_width(1.1)
                cr.set_dash([3.0, 3.0])
                cr.move_to(px0 + 4.0, py0 + 1.0)
                cr.line_to(ux + 2.0, t2_bar_y - 1.0)
                cr.move_to(px0 + pw0 - 4.0, py0 + 1.0)
                cr.line_to(ux + uw - 2.0, t2_bar_y - 1.0)
                cr.stroke()
                cr.restore()

            vg_names = sorted({v["vg_name"] for v in show_nested if v.get("vg_name")})
            vg_objs = [vg for vg in self._vgs if vg["vg_name"] in vg_names]
            vg_sum = " · ".join(f"{vg['vg_name']} ({vg['free_h']} {t('cc_system.disks_map_vg_free').lower()})" for vg in vg_objs)
            t2_hdr = t("cc_system.disks_map_t2_lvm")
            if vg_names:
                t2_hdr = f"{t2_hdr} — {', '.join(vg_names)}"
            self._draw_text(cr, t2_hdr, ux, 106.0, f_hdr, pal["attention"], alpha=0.95, max_w=uw * 0.62)
            if vg_sum:
                self._draw_text(cr, vg_sum, ux + uw * 0.62, 106.0, f_sub, pal["schema"], alpha=0.92, max_w=uw * 0.38, align=Pango.Alignment.RIGHT)

            t2_segs = list(show_nested)
            for vg in vg_objs:
                if vg["free_b"] > 1024 ** 3:
                    t2_segs.append({
                        "name": f"{vg['vg_name']}-free",
                        "lv_short": "FREE",
                        "size_b": vg["free_b"],
                        "size_h": vg["free_h"],
                        "fstype": "free",
                        "mountpoint": "-",
                        "flags": "-",
                        "is_vg_free": True,
                        "clickable": False,
                    })
            gap2 = 4.0
            t2_widths = _calc_segment_widths(t2_segs, uw, gap2, 58.0)
            cx2 = ux
            for seg, sw in zip(t2_segs, t2_widths):
                self._draw_cassette(cr, cx2, t2_bar_y, sw, t2_bar_h, seg, pal, surf_hex, f_title, f_sub, compact=True)
                cx2 += sw + gap2
        else:
            # Track 2: 3 Horizontal SMART Endurance & Integrity Gauges
            self._draw_text(cr, t("cc_system.disks_map_t2_smart"), ux, 106.0, f_hdr, pal["vitality"], alpha=0.95, max_w=uw)
            gw = (uw - 24.0) / 3.0
            gy = 126.0
            gh = 70.0
            temp_c = sm.get("temp_c")
            wear_p = sm.get("wear_pct")
            spare_p = sm.get("spare_pct")
            bad_s = sm.get("bad_sectors")

            gauges = [
                (
                    t("cc_system.disks_map_thermal"),
                    f"{temp_c}°C / 70°C" if temp_c is not None else t("cc_system.disks_smart_na"),
                    min(100.0, max(0.0, (float(temp_c or 0) / 70.0) * 100.0)) if temp_c is not None else 0.0,
                    _temp_role(temp_c),
                ),
                (
                    t("cc_system.disks_map_life"),
                    f"{max(0, 100 - int(wear_p))}%" if wear_p is not None else t("cc_system.disks_smart_na"),
                    float(max(0, 100 - int(wear_p))) if wear_p is not None else 0.0,
                    "vitality" if (wear_p or 0) < 75 else "attention",
                ),
                (
                    t("cc_system.disks_map_spare"),
                    f"{spare_p}% · {bad_s or 0} bad" if spare_p is not None else t("cc_system.disks_smart_na"),
                    float(spare_p) if spare_p is not None else (100.0 if bad_s == 0 else 0.0),
                    "vitality" if (bad_s or 0) == 0 else "fault",
                ),
            ]
            for idx, (g_lbl, g_val, g_pct, g_role) in enumerate(gauges):
                gx0 = ux + idx * (gw + 12.0)
                g_col = pal.get(g_role, pal["schema"])
                g_bg = pal.get(f"{g_role}_bg", pal["structure_bg"])
                _rounded_rect(cr, gx0 + 0.5, gy + 0.5, gw - 1.0, gh - 1.0, 4.5)
                cr.set_source_rgba(*_hex_rgba(g_bg, 0.55))
                cr.fill_preserve()
                cr.set_source_rgba(*_hex_rgba(g_col, 0.78))
                cr.set_line_width(1.1)
                cr.stroke()
                self._draw_text(cr, g_lbl, gx0 + 10.0, gy + 8.0, f_sub, pal["schema"], alpha=0.95, max_w=gw - 20.0)
                self._draw_text(cr, g_val, gx0 + 10.0, gy + 26.0, f_title, pal["focus"], alpha=0.96, max_w=gw - 20.0)
                bw_in = max(24.0, gw - 20.0)
                _rounded_rect(cr, gx0 + 10.0, gy + gh - 15.0, bw_in, 6.0, 2.0)
                cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.45))
                cr.fill()
                _rounded_rect(cr, gx0 + 10.0, gy + gh - 15.0, max(4.0, bw_in * max(0.0, min(100.0, g_pct)) / 100.0), 6.0, 2.0)
                cr.set_source_rgba(*_hex_rgba(g_col, 0.94))
                cr.fill()

        # 4. Bottom Live Inspection HUD Strip
        fy = stage_h
        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.82))
        cr.rectangle(bx + 1.0, fy, bw - 2.0, footer_h)
        cr.fill()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
        cr.set_line_width(1.0)
        cr.move_to(bx + 1.0, fy + 0.5)
        cr.line_to(bx + bw - 1.0, fy + 0.5)
        cr.stroke()

        active_seg = self._hover_seg or next((v for v in self._vols if v["name"] == self._sel_vol), None)
        if active_seg:
            seg_pct = active_seg.get("use_pct") if active_seg.get("use_pct") is not None else active_seg.get("cow_pct")
            p_str = f" · {'CoW ' if active_seg.get('is_snap') else ''}{seg_pct:.1f}%" if seg_pct is not None else ""
            hud_l = f"{active_seg.get('path', active_seg['name'])}  ·  {active_seg.get('size_h', '-')}  ·  {active_seg.get('fstype', '-')}{p_str}"
            if active_seg.get("mountpoint") and active_seg["mountpoint"] != "-":
                hud_l += f"  ·  {active_seg['mountpoint']}"
            hud_r = str(active_seg.get("flags") or "-")
            self._draw_text(cr, hud_l, bx + 12.0, fy + 6.0, f_sub, pal["focus_val"], alpha=0.95, max_w=bw * 0.72)
            self._draw_text(cr, hud_r, bx + bw * 0.72, fy + 6.0, f_sub, pal["attention"], alpha=0.92, max_w=bw * 0.28 - 12.0, align=Pango.Alignment.RIGHT)
        else:
            self._draw_text(cr, t("cc_system.disks_map_footer_hint"), bx + 12.0, fy + 6.0, f_sub, pal["schema"], alpha=0.90, max_w=bw - 24.0)

        cr.restore()
        return False


def build_cards(cc_panel, canvas: Canvas) -> list:
    ensure_disks_probed(cc_panel)
    dm = _collect_disks_data()
    disks = dm["physical_disks"]
    cards = _feedback_cards() + build_privilege_modal_card(cc_panel, canvas)

    any_failed = any(d["smart"].get("passed") is False or (d["smart"].get("bad_sectors") or 0) > 0 for d in disks)
    any_probed = any(d["smart"].get("supported") for d in disks)

    hdr_acts = []
    if dm["disks_busy"]:
        hdr_acts.append(Badge(dm["disks_busy"], style="warn"))
    elif any_failed:
        hdr_acts.append(Badge(t("cc_system.disks_badge_warn"), style="crit"))
    elif any_probed:
        hdr_acts.append(StateBadge(True, t("cc_system.disks_badge_ok")))
    if dm["smart_updated_at"]:
        hdr_acts.append(Txt(dm["smart_updated_at"][5:16], role="schema"))
    if not dm["priv_disks_ok"]:
        hdr_acts.append(ActionBtn(t("cc_system.btn_authorize"), "priv:request:disks_inspect", tone="apply"))
    hdr_acts.append(ActionBtn(t("cc_system.disks_probe_btn"), "cc_sys:disks_probe", tone="neutral"))

    # ── Card 1: Physical Disks & SMART Summary ────────────────────────────────
    d_box = Box(
        t("cc_system.disks_box_title", p0=len(disks)),
        icon="\uf0a0",
        actions=hdr_acts,
        highlighted=False,
    )
    d_box.add_row(" ")

    sel_disk = dm["selected_disk"]
    sel_obj = None
    d_rows = []
    for d in disks:
        is_sel = d["name"] == sel_disk
        if is_sel:
            sel_obj = d
        sm = d["smart"]
        temp_c = sm.get("temp_c")
        wear_p = sm.get("wear_pct")
        bad_s = sm.get("bad_sectors")
        passed = sm.get("passed")

        tw_parts = []
        if temp_c is not None:
            tw_parts.append(Txt(f"{temp_c}°C", role=_temp_role(temp_c)))
        if wear_p is not None:
            tw_parts.append(Txt(f"{wear_p}%", role="attention" if wear_p >= 80 else "val"))
        tw_cell = " · ".join(tw_parts) if tw_parts else Txt("-", role="inactive")

        if bad_s is None:
            bad_cell = Txt("-", role="inactive")
        elif bad_s == 0:
            bad_cell = Txt("0", role="vitality")
        else:
            bad_cell = Txt(str(bad_s), role="fault")

        if passed is True:
            sm_cell = StateBadge(True, t("cc_system.disks_smart_ok"))
        elif passed is False:
            sm_cell = Badge(t("cc_system.disks_smart_fail"), style="crit")
        else:
            sm_cell = Txt(t("cc_system.disks_smart_na"), role="inactive")

        d_rows.append([
            InspectorCell(d["name"], f"cc_sys:disk_sel:{d['name']}", selected=is_sel),
            Txt(canvas.truncate_plain(f"{d['model']} ({d['media']})", 28), role="val"),
            Txt(f"{d['size_h']} · {d['pttype']}", role="val"),
            tw_cell,
            bad_cell,
            sm_cell,
        ])

    if d_rows:
        d_box.add_table(
            headers=[
                t("cc_system.disks_col_disk"),
                t("cc_system.disks_col_model"),
                t("cc_system.disks_col_cap"),
                t("cc_system.disks_col_temp_wear"),
                t("cc_system.disks_col_bad"),
                t("cc_system.disks_col_smart"),
            ],
            widths=[12, "flex", 14, 12, 8, 12],
            rows=d_rows,
            aligns=["left", "left", "left", "left", "right", "right"],
        )
    else:
        d_box.add_row(f"  {Txt(t('cc_system.disks_none'), role='inactive')}")

    if dm["empty_rm"]:
        def _build_rm(bx: Box):
            rm_rows = [
                [Txt(r["path"], role="schema"), Txt(r["tran"], role="schema"), Txt(r["model"], role="val")]
                for r in dm["empty_rm"]
            ]
            bx.add_table(
                headers=[t("cc_system.device"), t("cc_system.disks_col_bus"), t("cc_system.disks_col_model")],
                widths=[14, 8, "flex"],
                rows=rm_rows,
            )

        rm_sum = ", ".join(r["path"] for r in dm["empty_rm"])
        d_box.add_collapsible(
            cc_panel,
            "disks_empty_rm",
            t("cc_system.disks_empty_rm_sec", p0=len(dm["empty_rm"])),
            _build_rm,
            summary=rm_sum,
        )

    d_box.add_row(" ")
    cards.append(d_box)

    # ── Card 2: GParted Vector Disk Map + SMART Hardware Inspector ────────────
    if sel_obj is not None:
        sm = sel_obj["smart"]
        bd = sm.get("bad_detail") if isinstance(sm.get("bad_detail"), dict) else {}
        disk_vols = [v for v in dm["volumes"] if v["disk"] == sel_obj["name"]]
        i_hdr = Box(
            t("cc_system.disks_insp_title", p0=sel_obj["path"], p1=canvas.truncate_plain(sel_obj["model"], 30)),
            icon="\uf1c0",
            actions=[ActionBtn(t("cc_system.disks_smart_term"), f"cc_sys:disk_smart_term:{sel_obj['path']}", tone="neutral")],
            highlighted=False,
            open_bottom=True,
        )
        i_hdr.add_row(" ")

        i_foot = Box("", highlighted=False, open_top=True)
        i_foot.add_row(" ")
        fw = sm.get("firmware") or "-"
        bus_info = sel_obj["media"] if sel_obj["tran"].lower() == sel_obj["media"].lower() else f"{sel_obj['tran']} ({sel_obj['media']})"
        i_foot.add_kv(
            t("cc_system.disks_insp_hw"),
            Txt(f"{sel_obj['model']} · FW {fw} · {bus_info}", role="val"),
            right_markup=Txt(sel_obj["serial"], role="schema"),
        )
        passed = sm.get("passed")
        bad_n = sm.get("bad_sectors")
        if bad_n is not None:
            bad_s = t(
                "cc_system.disks_bad_breakdown",
                p0=bad_n,
                p1=bd.get("realloc", 0),
                p2=bd.get("pending", 0),
                p3=bd.get("media", 0) or bd.get("uncorr", 0),
            )
            bad_r = "vitality" if bad_n == 0 else "fault"
        else:
            bad_s = t("cc_system.disks_smart_hint")
            bad_r = "schema"
        spare = sm.get("spare_pct")
        spare_r = Txt(t("cc_system.disks_spare_lbl", p0=spare), role="vitality" if (spare or 0) > 20 else "attention") if spare is not None else ""
        sm_badge = StateBadge(True, t("cc_system.disks_smart_ok")) if passed is True else (Badge(t("cc_system.disks_smart_fail"), style="crit") if passed is False else Txt(t("cc_system.disks_smart_na"), role="inactive"))
        i_foot.add_kv(
            t("cc_system.disks_insp_smart"),
            f"{sm_badge}  {Txt(bad_s, role=bad_r)}",
            right_markup=spare_r,
        )
        poh = sm.get("power_on_hours")
        pcy = sm.get("power_cycles")
        tbw = sm.get("bytes_written")
        end_parts = []
        if sm.get("temp_c") is not None:
            end_parts.append(f"{sm['temp_c']}°C")
        if sm.get("wear_pct") is not None:
            end_parts.append(t("cc_system.disks_wear_lbl", p0=sm["wear_pct"]))
        if poh is not None:
            end_parts.append(f"{poh}h")
        if pcy is not None:
            end_parts.append(f"{pcy} cycles")
        end_right = Txt(t("cc_system.disks_written_lbl", p0=format_bytes_short(tbw)), role="schema") if tbw else ""
        i_foot.add_kv(
            t("cc_system.disks_insp_endurance"),
            Txt(" · ".join(end_parts) if end_parts else "-", role="val" if end_parts else "inactive"),
            right_markup=end_right,
        )
        i_foot.add_row(" ")

        inset_px = max(14.0, float(canvas.char_w) * 2.0)
        map_w = DiskPartitionMapWidget(sel_obj, disk_vols, dm["vgs"], dm["selected_volume"], cc_panel, inset_x=inset_px)
        cards.append(AsciiCard(i_hdr, canvas, embedded_widget=map_w, footer_box_model=i_foot))

    # ── Card 3: Deduplicated Partitions, Flags & LVM Details ──────────────────
    scope = dm["scope"]
    vols = [v for v in dm["volumes"] if scope == "all" or v["disk"] == sel_disk]
    sel_vol = dm["selected_volume"]
    scope_pills = [
        SelectPill(t("cc_system.disks_scope_sel", p0=sel_disk or "-"), "cc_sys:disks_scope:disk", selected=(scope == "disk")),
        SelectPill(t("cc_system.disks_scope_all"), "cc_sys:disks_scope:all", selected=(scope == "all")),
    ]
    v_box = Box(
        t("cc_system.disks_vols_title", p0=len(vols)),
        icon="\uf0ce",
        actions=scope_pills,
        highlighted=False,
    )
    v_box.add_row(" ")
    v_rows = []
    for v in vols[:28]:
        indent = "  " * min(2, v["depth"])
        dev_lbl = f"{indent}{v['name']}"
        mp = canvas.truncate_plain(v["mountpoint"], 20)
        up = v["use_pct"]
        cp = v.get("cow_pct")
        if up is not None:
            use_cell = f"{ProgressBar(up, width=6)} {Txt(f'{up:3.0f}%', role=_use_role(up))}"
        elif cp is not None:
            use_cell = f"{ProgressBar(cp, width=6)} {Txt(f'C{cp:2.0f}%', role='attention' if cp >= 60 else 'val')}"
        else:
            use_cell = Txt("-", role="inactive")
        fl_s = v["flags"]
        fl_role = "attention" if ("LUKS2" in fl_s or "ESP" in fl_s or "SNAP-CoW" in fl_s) else ("schema" if fl_s != "-" else "inactive")
        v_is_sel = bool(v["name"] == sel_vol)
        v_rows.append([
            InspectorCell(canvas.truncate_plain(dev_lbl, 20), f"cc_sys:vol_sel:{v['name']}", selected=v_is_sel),
            Txt(v["size_h"], role="val"),
            Txt(v["fstype"], role="schema"),
            Txt(canvas.truncate_plain(fl_s, 18), role=fl_role),
            Txt(mp, role="val" if v["mounted"] else "inactive"),
            use_cell,
        ])
    if v_rows:
        v_box.add_table(
            headers=[
                t("cc_system.device"),
                t("cc_system.size"),
                t("cc_system.fs"),
                t("cc_system.disks_col_flags"),
                t("cc_system.mountpoint"),
                t("cc_system.use"),
            ],
            widths=[20, 8, 12, 16, "flex", 14],
            rows=v_rows,
            aligns=["left", "left", "left", "left", "left", "right"],
        )

    if dm["lvs"]:
        def _build_lvm(bx: Box):
            lv_rows = [
                [
                    Txt(r["vg_name"], role="schema"),
                    Txt(r["lv_name"], role="focus"),
                    Txt(r["size_h"], role="val"),
                    Txt(r["lv_attr"], role="schema"),
                    Txt(r["data_pct_s"], role="inactive" if r["data_pct_s"] == "-" else "val"),
                ]
                for r in dm["lvs"][:16]
            ]
            bx.add_table(
                headers=["VG", t("cc_system.lv"), t("cc_system.size"), "ATTR", t("cc_system.data")],
                widths=[10, "flex", 10, 12, 10],
                rows=lv_rows,
            )

        vg_sum = " · ".join(f"{vg['vg_name']} {vg['size_h']} ({vg['free_h']} {t('cc_system.disks_map_vg_free').lower()})" for vg in dm["vgs"])
        v_box.add_collapsible(
            cc_panel,
            "sys_lvm",
            t("cc_system.lvm_logical_volumes", p0=len(dm["lvs"])),
            _build_lvm,
            summary=vg_sum,
        )

    v_box.add_row(" ")
    cards.append(v_box)
    return cards
