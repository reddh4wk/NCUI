#!/usr/bin/env python3
import os

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, Pango, PangoCairo  # noqa: E402

from core.config import THEME  # noqa: E402
from core.i18n import t  # noqa: E402
from core.privileges import build_privilege_modal_card  # noqa: E402
from ui.canvas import Canvas  # noqa: E402
from ui.kit import (  # noqa: E402
    ActionBtn,
    Badge,
    Box,
    CyclePill,
    InlineField,
    InspectorCell,
    SelectPill,
    Slider,
    StateBadge,
    SwatchTxt,
    Txt,
    feedback_cards,
    get_semantic_palette,
    hex_to_rgba_f,
    rounded_rect_path,
)
from ui.widgets import AsciiCard  # noqa: E402
from panels.cc_engine_boot import (  # noqa: E402
    _LIMINE_THEME_PRESETS,
    _load_limine_studio_data,
    detect_bootloader,
)


def _hex_to_rgb(hx: str, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    return hex_to_rgba_f(hx, 1.0, fallback=default)[:3]


def _parse_res_aspect(res_str: str) -> tuple[int, int]:
    if "x" in str(res_str or ""):
        parts = str(res_str).lower().split("x", 1)
        try:
            rw, rh = int(parts[0].strip()), int(parts[1].strip())
            if rw >= 320 and rh >= 200:
                return rw, rh
        except ValueError:
            pass
    return 2560, 1440


_PIXBUF_CACHE: dict[str, tuple[float, object]] = {}


def _load_cached_pixbuf(path: str):
    if not path or not os.path.isfile(path):
        return None
    try:
        mt = os.path.getmtime(path)
        cached = _PIXBUF_CACHE.get(path)
        if cached and cached[0] == mt:
            return cached[1]
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        _PIXBUF_CACHE.clear()
        _PIXBUF_CACHE[path] = (mt, pb)
        return pb
    except Exception:
        return None


class LimineDisplayPreviewWidget(Gtk.EventBox):
    """Faithful interactive Limine bootloader screen rendered inside a Display bezel."""

    def __init__(self, studio: dict, cc_panel, inset_x: float = 16.0) -> None:
        super().__init__()
        self._studio = studio
        self._cc_panel = cc_panel
        self._inset_x = float(inset_x)
        self._hit_rects: list[tuple[float, float, float, float, int]] = []
        self._hover_nid: int = -1

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
        self._da.set_size_request(-1, 306)
        self._da.set_hexpand(True)
        self._da.set_halign(Gtk.Align.FILL)
        self._da.connect("draw", self._on_draw)
        self.add(self._da)

    def _hit_test(self, x: float, y: float) -> int:
        for rx, ry, rw, rh, nid in self._hit_rects:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return nid
        return -1

    def _on_motion(self, _w, ev) -> bool:
        nid = self._hit_test(ev.x, ev.y)
        if nid != self._hover_nid:
            self._hover_nid = nid
            self._da.queue_draw()
        win = self.get_window()
        if win is not None:
            disp = self.get_display()
            cur = Gdk.Cursor.new_from_name(disp, "pointer" if nid >= 0 else "default")
            win.set_cursor(cur)
        return False

    def _on_leave(self, _w, _ev) -> bool:
        if self._hover_nid != -1:
            self._hover_nid = -1
            self._da.queue_draw()
        win = self.get_window()
        if win is not None:
            win.set_cursor(None)
        return False

    def _on_press(self, _w, ev) -> bool:
        if ev.button != 1:
            return False
        nid = self._hit_test(ev.x, ev.y)
        if nid >= 0 and self._cc_panel is not None:
            self._cc_panel.on_link(f"cc_boot:preview_sel:{nid}")
            return True
        return False

    def _draw_text(
        self,
        cr: cairo.Context,
        text: str,
        x: float,
        y: float,
        rgb: tuple[float, float, float],
        alpha: float = 1.0,
        size_pt: float = 9.0,
        bold: bool = False,
        max_w: float = 0.0,
        align: Pango.Alignment = Pango.Alignment.LEFT,
    ) -> float:
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription("monospace")
        fd.set_size(int(size_pt * Pango.SCALE))
        if bold:
            fd.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(fd)
        layout.set_text(str(text or ""), -1)
        if max_w > 0:
            layout.set_width(int(max_w * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_alignment(align)
        cr.save()
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)
        cr.restore()
        _w_px, h_px = layout.get_pixel_size()
        return float(h_px)

    def _visible_tree_rows(self, nodes: list[dict]) -> list[dict]:
        visible = []
        skip_depth = None
        for n in nodes:
            d = int(n.get("depth", 1))
            if skip_depth is not None:
                if d > skip_depth:
                    continue
                skip_depth = None
            visible.append(n)
            if n.get("is_dir") and not n.get("expanded"):
                skip_depth = d
        return visible

    def _paint_wallpaper(self, cr: cairo.Context, pb, sx: float, sy: float, sw: float, sh: float, style: str, res_w: int) -> None:
        if pb is None:
            return
        pw = float(pb.get_width())
        ph = float(pb.get_height())
        if pw <= 0 or ph <= 0:
            return
        cr.save()
        cr.rectangle(sx, sy, sw, sh)
        cr.clip()
        if style == "centered":
            scale = min(sw / float(max(640, res_w)), 1.0)
            dw, dh = pw * scale, ph * scale
            if dw > sw:
                fit = sw / pw
                dw, dh = pw * fit, ph * fit
            dx = sx + (sw - dw) / 2.0
            dy = sy + (sh - dh) / 2.0
            cr.translate(dx, dy)
            cr.scale(dw / pw, dh / ph)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
        elif style == "tiled":
            scale = max(0.08, sw / float(max(800, res_w)))
            cr.translate(sx, sy)
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            pat = cr.get_source()
            pat.set_extend(cairo.Extend.REPEAT)
            cr.paint()
        else:
            cr.translate(sx, sy)
            cr.scale(sw / pw, sh / ph)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
        cr.restore()

    def _on_draw(self, widget, cr: cairo.Context) -> bool:
        self._hit_rects.clear()
        alloc = widget.get_allocation()
        w = float(alloc.width)
        h = float(alloc.height)
        if w < 120 or h < 80:
            return False

        pal = get_semantic_palette()
        st = self._studio
        m = st["model"]
        is_dirty = bool(st["is_dirty"])
        res_w, res_h = _parse_res_aspect(m.get("interface_resolution", ""))

        # Outer recessed container tray matching Display & Wallpaper previews
        bx = self._inset_x
        bw = max(80.0, w - bx * 2.0)
        rounded_rect_path(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        s_bg = _hex_to_rgb(pal["structure_bg"], (0.10, 0.11, 0.14))
        s_br = _hex_to_rgb(pal["structure"], (0.26, 0.29, 0.36))
        cr.set_source_rgba(s_bg[0], s_bg[1], s_bg[2], 0.65)
        cr.fill_preserve()
        cr.set_source_rgba(s_br[0], s_br[1], s_br[2], 0.85)
        cr.set_line_width(1.0)
        cr.stroke()

        # Centered Display Bezel sized to active resolution aspect
        pad_x, pad_y = 22.0, 10.0
        avail_w = max(160.0, bw - pad_x * 2.0)
        avail_h = max(100.0, h - pad_y * 2.0)
        aspect = float(res_w) / float(res_h)
        if avail_w / aspect <= avail_h:
            mon_w = avail_w
            mon_h = avail_w / aspect
        else:
            mon_h = avail_h
            mon_w = avail_h * aspect
        mon_x = bx + (bw - mon_w) / 2.0
        mon_y = (h - mon_h) / 2.0

        accent = _hex_to_rgb(pal["attention"] if is_dirty else pal["intent"])
        focus_rgb = _hex_to_rgb(pal["focus"], (1.0, 1.0, 1.0))
        ok_rgb = _hex_to_rgb(pal["vitality"], (0.0, 1.0, 0.45))

        # Outer monitor chassis & bezel
        bezel_pad = 3.0
        rounded_rect_path(cr, mon_x, mon_y, mon_w, mon_h, 5.0)
        cr.set_source_rgba(0.03, 0.04, 0.05, 0.96)
        cr.fill_preserve()
        cr.set_source_rgba(accent[0], accent[1], accent[2], 0.95)
        cr.set_line_width(1.5)
        cr.stroke()

        sx = mon_x + bezel_pad
        sy = mon_y + bezel_pad
        sw = max(40.0, mon_w - bezel_pad * 2.0)
        sh = max(30.0, mon_h - bezel_pad * 2.0 - 22.0)

        # Clip screen surface
        cr.save()
        rounded_rect_path(cr, sx, sy, sw, sh, 3.0)
        cr.clip()

        backdrop_rgb = _hex_to_rgb(m.get("backdrop", "#000000"))
        cr.set_source_rgb(*backdrop_rgb)
        cr.rectangle(sx, sy, sw, sh)
        cr.fill()

        pb = _load_cached_pixbuf(st.get("wp_image_file", "")) if m.get("wallpaper") else None
        if pb is not None:
            self._paint_wallpaper(cr, pb, sx, sy, sw, sh, m.get("wallpaper_style", "stretched"), res_w)

        # Proportional Limine term_margin + TTRRGGBB translucent terminal overlay
        margin_px = max(4.0, min(sw * 0.22, float(int(m.get("term_margin", 0))) * (sw / float(max(800, res_w))) * 2.2))
        mx = sx + margin_px
        my = sy + margin_px
        mw = max(40.0, sw - margin_px * 2.0)
        mh = max(30.0, sh - margin_px * 2.0)

        tbg_rgb = _hex_to_rgb(m.get("term_background_rgb", "#000000"))
        alpha_f = max(0.0, min(1.0, float(int(m.get("term_background_alpha_pct", 50))) / 100.0))
        fg_rgb = _hex_to_rgb(m.get("term_foreground", "#d8dee9"), (0.85, 0.87, 0.91))
        fgb_rgb = _hex_to_rgb(m.get("term_foreground_bright", "#ffffff"), (1.0, 1.0, 1.0))
        brand_rgb = _hex_to_rgb(m.get("interface_branding_color", ""), fgb_rgb)

        rounded_rect_path(cr, mx, my, mw, mh, 3.0)
        cr.set_source_rgba(tbg_rgb[0], tbg_rgb[1], tbg_rgb[2], alpha_f)
        cr.fill_preserve()
        cr.set_source_rgba(fg_rgb[0], fg_rgb[1], fg_rgb[2], 0.22)
        cr.set_line_width(0.8)
        cr.stroke()

        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        # Terminal contents: branding (only when set) + interactive tree + comment hint
        brand_txt = str(m.get("interface_branding") or "").strip()
        pad_in = 10.0
        cur_y = my + 7.0
        if brand_txt:
            bh_px = self._draw_text(
                cr,
                brand_txt,
                mx + pad_in,
                cur_y,
                brand_rgb,
                alpha=0.98,
                size_pt=base_pt * 0.92,
                bold=True,
                max_w=max(30.0, mw - pad_in * 2.0),
                align=Pango.Alignment.CENTER,
            )
            cur_y += bh_px + 6.0
        else:
            cur_y += 8.0

        footer_h = 32.0
        list_max_y = my + mh - footer_h
        nodes = m.get("nodes") or []
        vis_rows = self._visible_tree_rows(nodes)
        sel_id = int(st.get("selected_id", -1))

        def _entry_key_num(nid_target: int) -> int:
            top_c = 0
            for nd in nodes:
                if int(nd.get("depth", 1)) == 1:
                    top_c += 1
                if int(nd.get("id", -1)) == nid_target and int(nd.get("depth", 1)) == 1:
                    return top_c
            return -1

        row_h = 18.0
        sel_node = None
        for nd in vis_rows:
            if nd["id"] == sel_id:
                sel_node = nd
            if cur_y + row_h > list_max_y:
                self._draw_text(cr, "...", mx + pad_in + 8.0, cur_y - 2.0, fg_rgb, alpha=0.6, size_pt=base_pt * 0.82)
                break

            nid = int(nd["id"])
            is_sel = nid == sel_id
            is_hov = nid == self._hover_nid
            depth = int(nd.get("depth", 1))
            ind_px = float(max(0, depth - 1)) * 12.0
            rx = mx + pad_in + ind_px
            rw = max(30.0, mw - pad_in * 2.0 - ind_px)

            prefix = ("[-] " if nd.get("expanded") else "[+] ") if nd.get("is_dir") else "    "
            top_n = _entry_key_num(nid)
            def_tag = "  *" if (top_n > 0 and str(top_n) == str(m.get("default_entry", "1"))) else ""
            row_label = f"{prefix}{nd.get('title', 'Entry')}{def_tag}"

            if is_sel:
                rounded_rect_path(cr, rx - 3.0, cur_y - 1.0, rw + 4.0, row_h - 1.0, 2.0)
                cr.set_source_rgba(fg_rgb[0], fg_rgb[1], fg_rgb[2], 0.92)
                cr.fill()
                txt_c = tbg_rgb
                bold_r = True
            elif is_hov:
                rounded_rect_path(cr, rx - 3.0, cur_y - 1.0, rw + 4.0, row_h - 1.0, 2.0)
                cr.set_source_rgba(fgb_rgb[0], fgb_rgb[1], fgb_rgb[2], 0.16)
                cr.fill()
                txt_c = fgb_rgb
                bold_r = bool(nd.get("is_dir"))
            else:
                txt_c = fgb_rgb if nd.get("is_dir") else fg_rgb
                bold_r = bool(nd.get("is_dir"))

            self._draw_text(
                cr,
                row_label,
                rx,
                cur_y,
                txt_c,
                alpha=0.98,
                size_pt=base_pt * 0.86,
                bold=bold_r,
                max_w=rw,
            )
            self._hit_rects.append((rx - 3.0, cur_y - 1.0, rw + 4.0, row_h, nid))
            cur_y += row_h

        # Comment + keybinding footer inside terminal overlay
        comment_str = ""
        if sel_node is not None:
            comment_str = str(sel_node.get("comment") or sel_node.get("path") or "").strip()
        if comment_str:
            self._draw_text(
                cr,
                comment_str,
                mx + pad_in,
                my + mh - 28.0,
                fgb_rgb,
                alpha=0.85,
                size_pt=base_pt * 0.80,
                max_w=max(30.0, mw - pad_in * 2.0),
            )
        self._draw_text(
            cr,
            t("cc_system.boot_key_hints"),
            mx + pad_in,
            my + mh - 14.0,
            fg_rgb,
            alpha=0.60,
            size_pt=base_pt * 0.78,
            max_w=max(30.0, mw - pad_in * 2.0),
        )

        # Top-right Display Bezel Status Pill (LIVE / PREVIEW *)
        badge_lbl = t("cc_system.boot_preview_badge") if is_dirty else t("cc_system.boot_live_badge")
        badge_rgb = _hex_to_rgb(pal["attention"]) if is_dirty else ok_rgb
        b_w = max(44.0, float(len(badge_lbl)) * 7.5 + 10.0)
        b_x = sx + sw - b_w - 6.0
        b_y = sy + 5.0
        rounded_rect_path(cr, b_x, b_y, b_w, 16.0, 2.5)
        cr.set_source_rgba(0.03, 0.04, 0.05, 0.80)
        cr.fill_preserve()
        cr.set_source_rgba(badge_rgb[0], badge_rgb[1], badge_rgb[2], 0.85)
        cr.set_line_width(0.8)
        cr.stroke()
        self._draw_text(cr, badge_lbl, b_x + 2.0, b_y + 1.5, badge_rgb, alpha=0.95, size_pt=base_pt * 0.78, bold=True, max_w=b_w - 4.0, align=Pango.Alignment.CENTER)

        cr.restore()  # end screen clip

        # Bottom Monitor HUD Strip inside bezel
        hud_y = sy + sh + 1.0
        cr.set_source_rgba(0.05, 0.06, 0.08, 0.92)
        cr.rectangle(sx, hud_y, sw, 20.0)
        cr.fill()
        res_lbl = m.get("interface_resolution") or t("cc_system.boot_res_auto").upper()
        to_lbl = f"{m.get('timeout', '5')}s" if m.get("timeout") != "no" else t("cc_system.boot_manual").upper()
        hud_left = t("cc_system.boot_hud_left", p0=res_lbl, p1=to_lbl, p2=m.get("term_margin", 0))
        self._draw_text(cr, hud_left, sx + 6.0, hud_y + 3.0, focus_rgb, alpha=0.88, size_pt=base_pt * 0.82, max_w=sw * 0.72)
        hud_right = t("cc_system.boot_hud_nodes", p0=len(nodes))
        self._draw_text(cr, hud_right, sx + sw * 0.72, hud_y + 3.0, accent, alpha=0.90, size_pt=base_pt * 0.82, max_w=sw * 0.28 - 6.0, align=Pango.Alignment.RIGHT)
        return False


def _match_preset(m: dict) -> str:
    for pid, pr in _LIMINE_THEME_PRESETS.items():
        if (
            m.get("term_foreground") == pr["term_foreground"]
            and m.get("term_foreground_bright") == pr["term_foreground_bright"]
            and m.get("term_background_rgb") == pr["term_background_rgb"]
        ):
            return pid
    return ""


def _build_generic_boot_cards(bl: dict, canvas: Canvas) -> list:
    kind = bl["kind"]
    esps = ", ".join(bl.get("esp_mounts") or []) or "-"
    lcfg = bl.get("loader_cfg") or {}
    entries = bl.get("entries") or []

    if kind == "none":
        box = Box(
            t("cc_system.boot_none_title"),
            icon="\uf120",
            actions=[StateBadge(False, "", t("cc_system.boot_not_detected"))],
            highlighted=False,
        )
        box.add_row(" ")
        box.add_kv(t("cc_system.boot_detected_type"), Txt(t("cc_system.boot_not_detected"), role="inactive"))
        box.add_kv(t("cc_system.boot_esp_mounts"), Txt(esps, role="val" if esps != "-" else "inactive"))
        box.add_row(f"  {Txt(t('cc_system.boot_no_entries'), role='schema')}")
        box.add_row(" ")
        return [box]

    title_key = "cc_system.boot_sdboot_title" if kind == "systemd-boot" else "cc_system.boot_grub_title"
    acts = [StateBadge(True, t("cc_system.boot_live_badge"))]
    if kind == "systemd-boot" and bl.get("has_bootctl"):
        acts.append(ActionBtn(t("cc_system.boot_status_btn"), "cc_boot:bootctl_status", tone="neutral"))
    if bl.get("conf_path"):
        acts.append(ActionBtn(t("cc_system.open_in_editor"), "cc_boot:open_editor", tone="neutral"))

    box1 = Box(t(title_key), icon="\uf120", actions=acts, highlighted=False)
    box1.add_row(" ")
    box1.add_kv(t("cc_system.boot_loader_conf"), Txt(bl.get("conf_path") or "-", role="val"))
    box1.add_kv(t("cc_system.boot_esp_mounts"), Txt(esps, role="val" if esps != "-" else "inactive"))
    if kind == "systemd-boot":
        box1.add_kv(t("cc_settings.boot_timeout"), Txt(lcfg.get("timeout", "-"), role="val"))
        box1.add_kv(t("cc_system.boot_default_target"), Txt(lcfg.get("default", "-"), role="val"))
        box1.add_kv(t("cc_system.boot_editor_flag"), Txt(lcfg.get("editor", "-"), role="val"))
    else:
        box1.add_kv(t("cc_settings.boot_timeout"), Txt(lcfg.get("GRUB_TIMEOUT", "-"), role="val"))
        box1.add_kv(t("cc_system.boot_default_target"), Txt(lcfg.get("GRUB_DEFAULT", "-"), role="val"))
        if lcfg.get("GRUB_CMDLINE_LINUX_DEFAULT"):
            box1.add_kv(t("cc_system.boot_fld_cmdline"), Txt(canvas.truncate_plain(lcfg["GRUB_CMDLINE_LINUX_DEFAULT"], 54), role="val"))
    box1.add_row(" ")

    box2 = Box(t("cc_system.boot_entries_box", p0=len(entries)), icon="\uf03a", highlighted=False)
    box2.add_row(" ")
    if entries:
        rows = [
            [
                Txt(canvas.truncate_plain(e.get("title") or e.get("id", "-"), 34), role="focus"),
                Txt(e.get("version") or "-", role="val"),
                Txt(canvas.truncate_plain(e.get("target") or "-", 36), role="val"),
            ]
            for e in entries
        ]
        box2.add_table(
            headers=[t("cc_system.boot_col_id"), t("cc_system.boot_col_ver"), t("cc_system.boot_col_target")],
            widths=[34, 14, "flex"],
            rows=rows,
            aligns=["left", "left", "left"],
        )
    else:
        box2.add_row(f"  {Txt(t('cc_system.boot_no_entries'), role='inactive')}")
    box2.add_row(" ")
    return [box1, box2]


def build_cards(cc_panel, canvas: Canvas) -> list:
    cards = feedback_cards("system", "cc_system.system_control_status") + build_privilege_modal_card(cc_panel, canvas)
    bl = detect_bootloader()
    if bl["kind"] != "limine":
        return cards + _build_generic_boot_cards(bl, canvas)

    st = _load_limine_studio_data()
    m = st["model"]
    is_dirty = st["is_dirty"]

    # ── Card 1: Preview & Global Limine Behavior ──────────────────────────────
    acts_1 = []
    if st["rescue_busy"]:
        acts_1.append(Badge(t("cc_system.rescue_busy_badge", p0=st["rescue_busy"]), style="warn"))
    if is_dirty:
        acts_1.append(Badge(t("cc_system.boot_preview_badge"), style="warn"))
    elif not st["readable"] and not st["cached"]:
        acts_1.append(Badge(t("cc_system.boot_cached_badge"), style="warn"))
    acts_1.append(ActionBtn(t("cc_system.boot_reset_btn"), "cc_boot:reset", tone="warn" if is_dirty else "muted"))
    acts_1.append(ActionBtn(t("cc_system.boot_apply_btn"), "cc_boot:apply", tone="apply" if is_dirty else "muted"))

    box1 = Box(t("cc_system.limine_bootloader"), icon="\uf120", actions=acts_1, highlighted=False, open_bottom=True)

    cur_to = str(m.get("timeout", "5"))
    to_pills = " ".join(
        SelectPill(lbl, f"cc_boot:to:{val}", selected=(cur_to == val), dirty=(is_dirty and cur_to == val))
        for val, lbl in (("0", "0s"), ("3", "3s"), ("5", "5s"), ("10", "10s"), ("no", t("cc_system.boot_manual")))
    )
    beh_pills = " ".join([
        SelectPill(t("cc_system.boot_remember_last"), "cc_boot:toggle_remember", selected=bool(m.get("remember_last_entry"))),
        SelectPill(t("cc_system.boot_verbose"), "cc_boot:toggle_verbose", selected=bool(m.get("verbose"))),
    ])
    box1.add_kv(t("cc_system.boot_timeout_flags"), f"{to_pills}  {beh_pills}")

    cur_res = str(m.get("interface_resolution") or "auto")
    res_pills = " ".join(
        SelectPill(lbl, f"cc_boot:res:{val}", selected=(cur_res == val), dirty=(is_dirty and cur_res == val))
        for val, lbl in (("auto", t("cc_system.boot_res_auto")), ("1920x1080", "1920x1080"), ("2560x1440", "2560x1440"), ("3840x2160", "3840x2160"))
    )
    box1.add_kv(t("cc_system.boot_resolution"), res_pills)

    cur_margin = int(m.get("term_margin", 0))
    margin_sl = Slider(cur_margin, "cc_boot:margin", min_val=0, max_val=160, width=20, show_pct=False, step_buttons=True, step=8, enabled=True)
    box1.add_kv(
        t("cc_system.boot_term_margin"),
        f"{margin_sl} {Txt(f'{cur_margin} px', role='val')}",
        right_markup=f"{Txt(st['conf_path'], role='schema')} {ActionBtn(t('cc_system.open_in_editor'), 'cc_set:limine_edit', tone='neutral')}",
    )
    box1.add_row(" ")

    foot1 = Box("", highlighted=False, open_top=True)
    foot1.add_row(" ")
    inset_px = max(14.0, float(canvas.char_w) * 2.0)
    cards.append(AsciiCard(box1, canvas, embedded_widget=LimineDisplayPreviewWidget(st, cc_panel, inset_x=inset_px), footer_box_model=foot1))

    # ── Card 2: Wallpaper, Opacity & Color Palette ────────────────────────────
    box2 = Box(t("cc_system.boot_palette_box"), icon="\uf53f", highlighted=False)

    active_pres = _match_preset(m)
    pres_pills = " ".join(
        SelectPill(lbl, f"cc_boot:preset:{pid}", selected=(active_pres == pid))
        for pid, lbl in (
            ("fallout", "Fallout"),
            ("cyber", t("cc_system.boot_preset_cyber")),
            ("amber", "Amber CRT"),
            ("arch", "Arch Blue"),
        )
    )
    box2.add_kv(t("cc_system.boot_presets"), pres_pills)

    cur_wp = str(m.get("wallpaper") or "")
    wp_disp = os.path.basename(cur_wp.split("#", 1)[0].replace("boot():/", "")) if cur_wp else t("cc_system.boot_wp_none")
    wp_style = str(m.get("wallpaper_style", "stretched"))
    wp_btns = [
        Txt(wp_disp, role="val" if cur_wp else "inactive"),
        ActionBtn(t("cc_system.boot_wp_import"), "cc_boot:wp_import", tone="neutral"),
        ActionBtn(t("cc_system.boot_wp_clear"), "cc_boot:wp_clear", tone="danger_soft" if cur_wp else "muted"),
        CyclePill(wp_style, "cc_boot:wp_cycle_style", active=bool(cur_wp) and wp_style != "stretched"),
    ]
    box2.add_kv(t("cc_system.boot_wallpaper"), " ".join(wp_btns))

    alpha_pct = int(m.get("term_background_alpha_pct", 50))
    op_sl = Slider(alpha_pct, "cc_boot:opacity", min_val=0, max_val=100, width=14, show_pct=True, step_buttons=True, step=10, enabled=True)
    brand_field = InlineField(
        "limine_branding",
        "cc_boot:branding_open",
        value=str(m.get("interface_branding") or ""),
        placeholder=t("cc_system.boot_ph_brand"),
        empty_label=t("cc_system.boot_ph_brand"),
        width=20,
    )
    box2.add_kv(t("cc_system.boot_overlay_brand"), f"{op_sl}   {Txt(t('cc_system.boot_brand_lbl'), role='schema')} {brand_field}")

    col_rows = []
    for ckey, lbl_key in (
        ("term_foreground", "cc_system.boot_col_fg"),
        ("term_foreground_bright", "cc_system.boot_col_fgb"),
        ("term_background_rgb", "cc_system.boot_col_bg"),
        ("interface_branding_color", "cc_system.boot_col_brand"),
        ("backdrop", "cc_system.boot_col_backdrop"),
    ):
        hx = str(m.get(ckey) or "#000000").lower()
        fld = InlineField(f"cc_lim_col_{ckey}", f"cc_boot:inline_col:{ckey}", value=hx.upper(), width=9, is_hex_color=True)
        col_rows.append([Txt(t(lbl_key), role="schema"), SwatchTxt(hx, show_hex=False), fld])
    box2.add_table(
        headers=[t("cc_system.boot_col_prop"), t("cc_system.boot_col_swatch"), t("cc_system.boot_col_hex")],
        widths=[28, 8, 18],
        rows=col_rows,
        aligns=["left", "left", "left"],
    )

    def _build_ansi_palette(target_box):
        ansi_rows = []
        pal8 = m.get("term_palette") or []
        for idx in range(min(8, len(pal8))):
            hx = str(pal8[idx]).lower()
            fld = InlineField(f"cc_lim_col_pal_{idx}", f"cc_boot:inline_col:pal_{idx}", value=hx.upper(), width=9, is_hex_color=True)
            ansi_rows.append([Txt(t(f"cc_system.boot_ansi_{idx}"), role="val"), SwatchTxt(hx, show_hex=False), fld])
        target_box.add_table(
            headers=[t("cc_system.boot_ansi_idx"), t("cc_system.boot_col_swatch"), t("cc_system.boot_col_hex")],
            widths=[18, 8, 18],
            rows=ansi_rows,
            aligns=["left", "left", "left"],
        )

    box2.add_collapsible(cc_panel, "boot_ansi_open", t("cc_system.boot_ansi_sec"), _build_ansi_palette)
    cards.append(box2)

    # ── Card 3: Hierarchical Boot Menu Entries + Selected Entry Inspector ─────
    nodes = m.get("nodes") or []
    box3 = Box(
        t("cc_system.boot_entries_box", p0=len(nodes)),
        icon="\uf03a",
        actions=[ActionBtn(t("cc_system.boot_add_btn"), "cc_boot:add_entry", tone="apply")],
        highlighted=False,
    )

    sel_id = int(st.get("selected_id", -1))
    def_idx_str = str(m.get("default_entry", "1"))
    top_counter = 0
    node_rows = []
    for nd in nodes:
        nid = int(nd["id"])
        depth = int(nd.get("depth", 1))
        if depth == 1:
            top_counter += 1
        is_sel = nid == sel_id
        prefix = "  " * max(0, depth - 1)
        icon_c = ("\uf07c " if nd.get("expanded") else "\uf07b ") if nd.get("is_dir") else ""
        cell = InspectorCell(f"{prefix}{nd.get('title', '')}", f"cc_boot:sel_node:{nid}", selected=is_sel, icon=icon_c)

        path_disp = nd.get("path") or (t("cc_system.boot_dir_expanded") if nd.get("expanded") else t("cc_system.boot_dir_collapsed"))
        path_cell = Txt(path_disp[:32], role="val" if nd.get("path") else "schema")

        acts = []
        if nd.get("is_dir"):
            dir_lbl = t("cc_system.boot_dir_close") if nd.get("expanded") else t("cc_system.boot_dir_open")
            acts.append(ActionBtn(dir_lbl, f"cc_boot:toggle_dir:{nid}", tone="neutral", min_chars=5))
        elif depth == 1:
            acts.append(SelectPill(t("cc_system.boot_default_lbl"), f"cc_boot:set_default:{top_counter}", selected=(def_idx_str == str(top_counter))))
        acts.append(ActionBtn("↑", f"cc_boot:move_up:{nid}", tone="neutral", min_chars=1))
        acts.append(ActionBtn("↓", f"cc_boot:move_dn:{nid}", tone="neutral", min_chars=1))
        acts.append(ActionBtn("x", f"cc_boot:del_node:{nid}", tone="danger_soft" if len(nodes) > 1 else "muted", min_chars=1))
        node_rows.append([cell, path_cell, " ".join(acts)])

    if node_rows:
        box3.add_table(
            headers=[t("cc_system.boot_col_entry"), t("cc_system.boot_col_target"), t("cc_system.col_actions")],
            widths=[28, 30, 24],
            rows=node_rows,
            aligns=["left", "left", "right"],
        )

    sel_node = nodes[sel_id] if 0 <= sel_id < len(nodes) else None
    if sel_node is not None:
        box3.add_section(t("cc_system.boot_entry_insp"))
        box3.add_kv(
            t("cc_system.boot_fld_title"),
            InlineField(f"lim_node_title_{sel_id}", f"cc_boot:edit_node:title:{sel_id}", value=str(sel_node.get("title") or ""), width=34),
            right_markup=Txt(sel_node.get("protocol") or ("folder" if sel_node.get("is_dir") else "efi"), role="schema"),
        )
        box3.add_kv(
            t("cc_system.boot_fld_comment"),
            InlineField(f"lim_node_comment_{sel_id}", f"cc_boot:edit_node:comment:{sel_id}", value=str(sel_node.get("comment") or ""), placeholder=t("cc_system.boot_ph_optional"), empty_label=t("cc_system.boot_ph_optional"), width=38),
        )
        if not sel_node.get("is_dir"):
            box3.add_kv(
                t("cc_system.boot_fld_path"),
                InlineField(f"lim_node_path_{sel_id}", f"cc_boot:edit_node:path:{sel_id}", value=str(sel_node.get("path") or ""), width=38),
            )
            box3.add_kv(
                t("cc_system.boot_fld_cmdline"),
                InlineField(f"lim_node_cmdline_{sel_id}", f"cc_boot:edit_node:cmdline:{sel_id}", value=str(sel_node.get("cmdline") or ""), placeholder=t("cc_system.boot_ph_optional"), empty_label=t("cc_system.boot_ph_optional"), width=38),
            )

    cards.append(box3)
    return cards
