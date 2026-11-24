#!/usr/bin/env python3
import math
import os
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, Pango, PangoCairo
from core.config import THEME
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, FoldIconBtn, ProgressBar, SelectPill, Sparkline, Txt, get_semantic_palette
from ui.widgets import AsciiCard
from panels.cc_engine_telemetry import (
    _ensure_btop_config,
    _ensure_nvtop_bin,
    _feedback_cards,
    close_embedded_tui,
    get_cpu_telemetry,
    get_or_start_tui_session,
    get_ram_telemetry,
    set_active_tui,
)


def _pct_role(pct: float) -> str:
    if pct < 50.0:
        return "vitality"
    if pct < 75.0:
        return "attention"
    if pct < 90.0:
        return "interrupt"
    return "fault"


def _hex_to_rgb_f(hex_str: str, fallback: tuple = (0.0, 0.94, 1.0)) -> tuple:
    h = str(hex_str or "").strip().lstrip("#")
    if len(h) == 6:
        try:
            return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
        except ValueError:
            pass
    return fallback


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    rad = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
    cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
    cr.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _compute_tui_grid_metrics(target_w: int, char_w: float):
    typo = THEME["typography"]
    ff = str(typo.get("font_family", "Courier") or "Courier")
    no_liga = Pango.AttrList()
    no_liga.insert(Pango.AttrFontFeatures.new("liga=0, clig=0, dlig=0, hlig=0"))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    cr = cairo.Context(surf)
    lay = PangoCairo.create_layout(cr)
    lay.set_attributes(no_liga)
    desc_reg = Pango.FontDescription.from_string(
        f"{ff}, Font Awesome 6 Free, Symbols Nerd Font, monospace 9.0"
    )
    desc_bold = Pango.FontDescription.from_string(
        f"{ff}, Font Awesome 6 Free, Symbols Nerd Font, monospace bold 9.0"
    )
    lay.set_font_description(desc_reg)
    lay.set_text("0" * 40, -1)
    _, log_r = lay.get_pixel_extents()
    cw = max(5.0, float(log_r.width) / 40.0)
    ch = max(11.0, float(log_r.height))
    avail_w = max(320.0, float(target_w) - char_w * 1.5)
    cols = max(84, int(avail_w // cw))
    return cols, cw, ch, desc_reg, desc_bold


class EmbeddedTuiWidget(Gtk.EventBox):
    def __init__(self, tui_id: str, session, map_width: int, char_w: float, target_height_px: int = 0):
        super().__init__()
        self.set_visible_window(False)
        self.set_above_child(True)
        self.tui_id = str(tui_id)
        self.session = session
        self.map_width = max(380, int(map_width))
        self.char_w = max(5.0, float(char_w))
        self._no_liga_attrs = Pango.AttrList()
        self._no_liga_attrs.insert(Pango.AttrFontFeatures.new("liga=0, clig=0, dlig=0, hlig=0"))
        self._metrics_sig = None
        self._cw = 7.0
        self._ch = 13.0
        self._desc_reg = None
        self._desc_bold = None
        self._update_metrics(self.map_width)
        self._ch_draw = (
            max(float(self._ch), (float(target_height_px) - 4.0) / float(max(1, self.session.rows)))
            if target_height_px > 0
            else float(self._ch)
        )
        self._last_geom = (self.char_w * 0.75, 2.0, self._cw, self._ch_draw)

        total_h = max(int(round(2.0 + self.session.rows * self._ch + 2.0)), int(target_height_px))
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

    def _update_metrics(self, target_w: int) -> None:
        typo = THEME["typography"]
        ff = str(typo.get("font_family", "Courier") or "Courier")
        sig = (ff, int(target_w))
        if self._metrics_sig == sig and self._desc_reg is not None:
            return
        self._metrics_sig = sig
        cols, cw, ch, desc_reg, desc_bold = _compute_tui_grid_metrics(target_w, self.char_w)
        self._cw = cw
        self._ch = ch
        self._desc_reg = desc_reg
        self._desc_bold = desc_bold
        if self.session.cols != cols:
            self.session.resize(self.session.rows, cols)

    def _on_destroy(self, *_args) -> None:
        if getattr(self.session, "on_redraw", None) is self._redraw_cb:
            self.session.on_redraw = None

    def _xy_to_cell(self, ex: float, ey: float):
        x0, y0, cw, ch = self._last_geom
        cols, rows = self.session.cols, self.session.rows
        if x0 <= ex <= (x0 + cols * cw) and y0 <= ey <= (y0 + rows * ch):
            c = max(1, min(cols, int((ex - x0) / cw) + 1))
            r = max(1, min(rows, int((ey - y0) / ch) + 1))
            return c, r
        return None

    def _on_button(self, _w, ev) -> bool:
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 1:
            set_active_tui(self.tui_id)
            self.da.queue_draw()
        cell = self._xy_to_cell(ev.x, ev.y)
        if cell is None or ev.button not in (1, 2, 3):
            return False
        btn = {1: 0, 2: 1, 3: 2}[ev.button]
        kind = "M" if ev.type == Gdk.EventType.BUTTON_PRESS else "m"
        self.session.send_bytes(f"\x1b[<{btn};{cell[0]};{cell[1]}{kind}".encode("ascii"))
        return True

    def _on_scroll(self, _w, ev) -> bool:
        if STATE.get("system", "active_tui", "") != self.tui_id:
            return False
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
        alloc_w = float(box_w if box_w > 0 else max(self.map_width, alloc.width))
        if abs(int(round(alloc_w)) - self.map_width) > 2:
            self.map_width = int(round(alloc_w))
            self._update_metrics(self.map_width)

        with self.session.lock:
            rows = self.session.rows
            cols = self.session.cols
            grid_snap = [list(row) for row in self.session.grid]

        cw = self._cw
        ch = self._ch_draw
        term_w = cols * cw
        term_h = rows * ch
        x0 = max(self.char_w * 0.75, (alloc_w - term_w) * 0.5)
        y0 = 2.0
        self._last_geom = (x0, y0, cw, ch)

        p = get_semantic_palette()
        is_active = STATE.get("system", "active_tui", "") == self.tui_id
        if is_active:
            br, bg_c, bb = _hex_to_rgb_f(p["intent"], (0.0, 0.94, 1.0))
            _rounded_rect(cr, x0 - 2.0, y0 - 1.0, term_w + 4.0, term_h + 2.0, 3.0)
            cr.set_source_rgba(br, bg_c, bb, 0.90)
            cr.set_line_width(1.0)
            cr.stroke()

        def_fg = _hex_to_rgb_f(p["focus_val"], (0.89, 0.92, 0.99))
        def_bg = _hex_to_rgb_f(p["structure_bg"], (0.10, 0.11, 0.14))
        layout = PangoCairo.create_layout(cr)
        layout.set_attributes(self._no_liga_attrs)

        for r, row in enumerate(grid_snap):
            ry = y0 + r * ch
            c = 0
            while c < cols:
                txt, fg, bg, bold, rev = row[c]
                eff_bg = (fg or def_fg) if rev else bg
                if eff_bg is not None:
                    c2 = c + 1
                    while c2 < cols:
                        t2 = row[c2]
                        eb2 = (t2[1] or def_fg) if t2[4] else t2[2]
                        if eb2 != eff_bg:
                            break
                        c2 += 1
                    cr.set_source_rgb(*eff_bg)
                    cr.rectangle(x0 + c * cw, ry, (c2 - c) * cw + 0.5, ch + 0.5)
                    cr.fill()
                    c = c2
                else:
                    c += 1

        for r, row in enumerate(grid_snap):
            ry = y0 + r * ch
            c = 0
            while c < cols:
                txt, fg, bg, bold, rev = row[c]
                if not txt or txt == " ":
                    c += 1
                    continue
                eff_fg = (bg or def_bg) if rev else (fg or def_fg)
                layout.set_font_description(self._desc_bold if bold else self._desc_reg)
                cr.set_source_rgb(*eff_fg)
                if len(txt) == 1 and 32 < ord(txt) < 127:
                    run = [txt]
                    c2 = c + 1
                    while c2 < cols:
                        t2 = row[c2]
                        ef2 = (t2[2] or def_bg) if t2[4] else (t2[1] or def_fg)
                        if not t2[0] or len(t2[0]) != 1 or not (32 <= ord(t2[0]) < 127) or ef2 != eff_fg or t2[3] != bold:
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
        return False


def _build_tui_ncbox(
    cc_panel,
    canvas: Canvas,
    tui_id: str,
    cmd: list[str],
    rows: int,
    cols: int,
    target_height_px: int,
    title: str,
    icon: str,
    open_key: str,
    desc_key: str,
):
    is_open = bool(cc_panel.get_state(open_key, True))
    acts = [
        ActionBtn(t("cc_system.restart_tui"), f"cc_sys:tui_restart:{tui_id}", tone="neutral"),
        ActionBtn(t("cc_system.open_window"), f"cc_sys:{tui_id}", tone="neutral"),
        FoldIconBtn(is_open, f"ui:state:{open_key}:{'false' if is_open else 'true'}"),
    ]
    box = Box(title, icon=icon, actions=acts, highlighted=False)
    if not is_open:
        close_embedded_tui(tui_id)
        box.add_row(" ")
        box.add_row(f"  {Txt(t(desc_key), role='inactive')}")
        box.add_row(" ")
        return box

    sess = get_or_start_tui_session(tui_id, cmd, rows=rows, cols=cols, cc_panel=cc_panel)
    w = EmbeddedTuiWidget(
        tui_id,
        sess,
        map_width=int(round(canvas.total_px)),
        char_w=canvas.char_w,
        target_height_px=target_height_px,
    )
    box.open_bottom = True
    foot = Box("", highlighted=False)
    foot.open_top = True
    return AsciiCard(box, canvas, embedded_widget=w, footer_box_model=foot)


def _build_resources_standard(cc_panel, canvas: Canvas) -> list:
    cpu = get_cpu_telemetry({})
    ram = get_ram_telemetry()
    cards = _feedback_cards()

    top = Box(
        t("cc_system.cpu_ram_swap_telemetry", p0=cpu['model']),
        icon="\uf2db",
        actions=[ActionBtn(t("cc_system.sync_disks"), "cc_sys:sync_disks", tone="apply")],
        highlighted=False,
    )
    top.add_row(" ")
    c_pct, c_tmp, c_ghz = cpu["total_pct"], cpu["temp_c"], cpu["max_ghz"]
    tmp_role = "vitality" if c_tmp < 65.0 else ("attention" if c_tmp < 80.0 else "fault")
    r_used_g, r_tot_g = ram["used"] / (1024 ** 3), ram["total"] / (1024 ** 3)
    s_used_g, s_tot_g = ram["sw_used"] / (1024 ** 3), ram["sw_total"] / (1024 ** 3)
    top.add_kv(
        t("cc_system.cpu_load"),
        f"{ProgressBar(c_pct, width=22)} {Txt(f'{c_pct:4.1f}%', role=_pct_role(c_pct))}  "
        f"{Txt(f'{c_tmp:.1f}C', role=tmp_role)}  {Txt(f'{c_ghz:.2f}GHz', role='val')}",
    )
    top.add_kv(t("cc_system.core_sparkline"), Sparkline(cpu["cores_pct"], max_items=32))
    top.add_kv(t("cc_system.ram_usage"), f"{ProgressBar(ram['pct'], width=22)} {Txt(f'{r_used_g:.1f}/{r_tot_g:.1f} GiB', role='val')}")
    top.add_kv(t("cc_system.swap_zram"), f"{ProgressBar(ram['sw_pct'], width=22)} {Txt(f'{s_used_g:.1f}/{s_tot_g:.1f} GiB', role='val')}")

    def _build_sysctl(bx: Box):
        swp = open("/proc/sys/vm/swappiness").read().strip() if os.path.exists("/proc/sys/vm/swappiness") else "60"
        drt = open("/proc/sys/vm/dirty_ratio").read().strip() if os.path.exists("/proc/sys/vm/dirty_ratio") else "20"
        swp_pills = " ".join(SelectPill(v, f"cc_sys:sysctl:vm.swappiness:{v}", selected=(swp == v)) for v in ("10", "60", "100"))
        drt_pills = " ".join(SelectPill(v, f"cc_sys:sysctl:vm.dirty_ratio:{v}", selected=(drt == v)) for v in ("5", "10", "20"))
        bx.add_kv("vm.swappiness", swp_pills if swp in ("10", "60", "100") else f"{Txt(swp, role='val')}  {swp_pills}")
        bx.add_kv("vm.dirty_ratio", drt_pills if drt in ("5", "10", "20") else f"{Txt(drt, role='val')}  {drt_pills}")

    top.add_collapsible(cc_panel, "sys_sysctl", t("cc_system.kernel_sysctl_vm_tuning"), _build_sysctl)
    top.add_row(" ")
    cards.append(top)

    cols, _cw, ch, _dr, _db = _compute_tui_grid_metrics(int(round(canvas.total_px)), canvas.char_w)
    btop_open = bool(cc_panel.get_state("tui_btop_open", True))
    nvtop_open = bool(cc_panel.get_state("tui_nvtop_open", True))
    n_open = int(btop_open) + int(nvtop_open)
    n_collapsed = int(not btop_open) + int(not nvtop_open)

    shell = getattr(cc_panel, "shell", None)
    if n_open >= 1 and shell is not None and hasattr(shell, "_get_cc_target_height_px"):
        _ml, _mr, m_top, m_bot, _gap = shell._get_inner_box_margins()
        avail_total_h = shell._get_cc_target_height_px() - m_top - m_bot
        spacing = int(shell.geom.get("card_spacing", 4))
        hdr_box = getattr(cc_panel, "_current_hdr_box", None)
        top_items = ([hdr_box] if hdr_box is not None else []) + list(cards)
        used_h = sum(shell._measure_cc_box_px(it) + spacing for it in top_items)
        if n_collapsed > 0:
            c_sample = Box(
                t("cc_system.btop_box_title"),
                icon="\uf0ae",
                actions=[
                    ActionBtn(t("cc_system.restart_tui"), "x", tone="neutral"),
                    ActionBtn(t("cc_system.open_window"), "y", tone="neutral"),
                    FoldIconBtn(False, "z"),
                ],
                highlighted=False,
            )
            c_sample.add_row(" ")
            c_sample.add_row("  .")
            c_sample.add_row(" ")
            used_h += n_collapsed * (shell._measure_cc_box_px(c_sample) + spacing)
        per_card_px = (avail_total_h - used_h - (n_open - 1) * spacing) // n_open
        hdr_probe = Box(
            "x",
            icon="\uf0ae",
            actions=[
                ActionBtn(t("cc_system.restart_tui"), "x", tone="neutral"),
                FoldIconBtn(True, "z"),
            ],
            highlighted=False,
            open_bottom=True,
        )
        foot_probe = Box("", highlighted=False, open_top=True)
        chrome_px = shell._measure_cc_box_px(hdr_probe) + shell._measure_cc_box_px(foot_probe)
        min_rows = 24 if avail_total_h < 900 else 20
        target_widget_h = max(int(round(min_rows * ch + 4)), int(per_card_px - chrome_px))
        shared_rows = max(min_rows, int((target_widget_h - 4) // ch))
    else:
        shared_rows = 26
        target_widget_h = int(round(shared_rows * ch + 4))

    cards.append(
        _build_tui_ncbox(
            cc_panel,
            canvas,
            tui_id="btop",
            cmd=["btop", "--force-utf", "--config", _ensure_btop_config()],
            rows=shared_rows,
            cols=cols,
            target_height_px=target_widget_h,
            title=t("cc_system.btop_box_title"),
            icon="\uf0ae",
            open_key="tui_btop_open",
            desc_key="cc_system.btop_collapsed_desc",
        )
    )
    cards.append(
        _build_tui_ncbox(
            cc_panel,
            canvas,
            tui_id="nvtop",
            cmd=[_ensure_nvtop_bin()],
            rows=shared_rows,
            cols=cols,
            target_height_px=target_widget_h,
            title=t("cc_system.nvtop_box_title"),
            icon="\uf2db",
            open_key="tui_nvtop_open",
            desc_key="cc_system.nvtop_collapsed_desc",
        )
    )
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_resources_standard(cc_panel, canvas)
