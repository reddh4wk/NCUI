#!/usr/bin/env python3
import math
import os
import re
import shlex
from core.config import THEME, _parse_rgba_components
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    FoldIconBtn,
    InlineField,
    InlineFilter,
    InspectorCell,
    SelectPill,
    Txt,
    get_semantic_palette,
)
from ui.widgets import AsciiCard
from panels.cc_engine_input import (
    DEFAULT_NIRI_SHORTCUTS,
    _build_niri_action_from_draft,
    _collect_keyboard_state,
    _feedback_box,
    _get_bind_draft,
    _load_shortcuts_store,
    _norm_action_str,
)

_MOD_TOKENS = {"Mod", "Super", "Ctrl", "Control", "Alt", "Shift", "Meta"}
_PAGE_SIZE = 8


def _hex_rgba(hex_str: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    r, g, b, _ = _parse_rgba_components(hex_str)
    return (r / 255.0, g / 255.0, b / 255.0, alpha)


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    rad = max(0.0, min(float(r), float(w) * 0.5, float(h) * 0.5))
    if rad <= 0.0:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi * 0.5, 0.0)
    cr.arc(x + w - rad, y + h - rad, rad, 0.0, math.pi * 0.5)
    cr.arc(x + rad, y + h - rad, rad, math.pi * 0.5, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, math.pi * 1.5)
    cr.close_path()


def _nc_targets_spec() -> list[tuple[str, str]]:
    return [
        (t("cc_keyboard.nc_cc"), "settings system"),
        (t("cc_keyboard.nc_net"), "popup network"),
        (t("cc_keyboard.nc_audio"), "popup audio"),
        (t("cc_keyboard.nc_fw"), "settings firewall"),
        (t("cc_keyboard.nc_kbl"), "popup kb_layout"),
        (t("cc_keyboard.nc_clip"), "popup clipboard"),
        (t("cc_keyboard.nc_shot"), "popup capture"),
        (t("cc_keyboard.nc_ups"), "popup ups"),
    ]


def _humanize_niri_action(action: str, std_by_norm: dict, nc_by_tgt: dict) -> str:
    raw = str(action or "").strip()
    if not raw:
        return ""
    norm = _norm_action_str(raw)
    if norm in std_by_norm:
        return t(f"cc_keyboard.shortcut_{std_by_norm[norm]['id']}")
    m_nc = re.search(r'nc-ctl"\s+"(popup|settings)"\s+"([a-z0-9_]+)"', raw)
    if m_nc:
        tgt = f"{m_nc.group(1)} {m_nc.group(2)}"
        lbl = nc_by_tgt.get(tgt, m_nc.group(2).replace("_", " ").title())
        return t("cc_keyboard.sc_nc_prefix", p0=lbl)
    if raw.startswith("spawn "):
        try:
            toks = shlex.split(raw[6:])
        except ValueError:
            toks = raw[6:].split()
        if toks:
            exe = os.path.basename(toks[0])
            rest = " ".join(toks[1:])
            return f"{exe} {rest}".strip()
    return raw


class _ShortcutChordMixin:
    """Interactive pure-Cairo keycap chord visualizer & 1-click capture stage."""

    def __init__(
        self,
        combo: str,
        target_label: str,
        is_cap: bool,
        cap_prev: str,
        conflict_act: str,
        is_editing: bool,
        on_stage_click=None,
        widget_width: int = 760,
        fixed_height: int = 94,
        inset_x: int = 14,
        font_px: float = 11.0,
    ):
        super().__init__()
        self.combo = str(combo or "").strip()
        self.target_label = str(target_label or "").strip()
        self.is_cap = bool(is_cap)
        self.cap_prev = str(cap_prev or "").strip()
        self.conflict_act = str(conflict_act or "").strip()
        self.is_editing = bool(is_editing)
        self.on_stage_click = on_stage_click
        self.widget_width = max(260, int(widget_width))
        self.widget_height = max(92, int(fixed_height))
        self.inset_x = max(4, int(inset_x))
        self.font_px = max(8.5, float(font_px))
        self.footer_h = 26
        self._hovered = False
        self._init_widget()

    def _init_widget(self) -> None:
        from gi.repository import Gdk

        self.set_size_request(self.widget_width, self.widget_height)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("size-allocate", self._on_size_allocate)
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

    def _on_size_allocate(self, _widget, alloc) -> None:
        if alloc.width > (2 * self.inset_x + 40) and abs(alloc.width - self.widget_width) > 1:
            self.widget_width = alloc.width
            self.queue_draw()

    def _set_cursor(self, name: str | None) -> None:
        try:
            win = self.get_window()
            if win is None:
                return
            if not name:
                win.set_cursor(None)
                return
            from gi.repository import Gdk

            win.set_cursor(Gdk.Cursor.new_from_name(win.get_display(), name))
        except Exception:
            pass

    def _on_enter(self, _widget, _event) -> bool:
        self._hovered = True
        self._set_cursor("pointer")
        self.queue_draw()
        return False

    def _on_leave(self, _widget, _event) -> bool:
        self._hovered = False
        self._set_cursor(None)
        self.queue_draw()
        return False

    def _on_press(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        if callable(self.on_stage_click):
            self.on_stage_click()
        return True

    def _draw_keycap(
        self,
        cr,
        x: float,
        y: float,
        h: float,
        label: str,
        font_desc,
        fill_col: str,
        border_col: str,
        text_col: str,
        skirt_col: str,
        fill_alpha: float = 0.88,
        dashed: bool = False,
        line_w: float = 1.25,
    ) -> float:
        from gi.repository import PangoCairo

        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(font_desc)
        lay.set_text(label, -1)
        _, ext = lay.get_pixel_extents()
        pad_x = 12.0
        kw = max(36.0, float(ext.width) + pad_x * 2.0)

        cr.save()
        _rounded_rect(cr, x + 0.5, y + 0.5, kw - 1.0, h - 1.0, 5.5)
        cr.set_source_rgba(*_hex_rgba(fill_col, fill_alpha))
        cr.fill_preserve()
        if dashed:
            cr.set_dash([4.0, 3.0])
        cr.set_source_rgba(*_hex_rgba(border_col, 0.95))
        cr.set_line_width(line_w)
        cr.stroke()

        # Subtle tactile 3D bottom keycap skirt bevel inside rounded border
        if not dashed:
            _rounded_rect(cr, x + 1.0, y + 1.0, kw - 2.0, h - 2.0, 5.0)
            cr.clip()
            cr.set_source_rgba(*_hex_rgba(skirt_col, 0.55))
            cr.rectangle(x + 1.0, y + h - 4.0, kw - 2.0, 3.0)
            cr.fill()

        cr.set_source_rgba(*_hex_rgba(text_col, 1.0))
        tx = x + (kw - ext.width) * 0.5
        ty = y + max(2.0, (h - 3.0 - ext.height) * 0.5)
        cr.move_to(tx, ty)
        PangoCairo.show_layout(cr, lay)
        cr.restore()
        return kw

    def _on_draw(self, _widget, cr) -> bool:
        from gi.repository import Pango, PangoCairo

        alloc = self.get_allocation()
        w = max(260.0, float(alloc.width or self.widget_width))
        h = max(84.0, float(alloc.height or self.widget_height))
        bx = float(self.inset_x)
        bw = max(120.0, w - 2.0 * bx)
        footer_h = float(self.footer_h)
        stage_h = h - footer_h

        pal = get_semantic_palette()
        surf_hex = str((THEME.get("colors") or {}).get("bg_panel") or pal["structure_bg"])
        font_family = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")

        # 1. Outer recessed Tactical Blueprint container
        _rounded_rect(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 6.0)
        cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.62))
        cr.fill_preserve()
        if self.is_cap:
            brd_col, brd_w = pal["attention"], 1.4
        elif self._hovered:
            brd_col, brd_w = pal["focus"], 1.15
        else:
            brd_col, brd_w = pal["structure"], 1.0
        cr.set_source_rgba(*_hex_rgba(brd_col, 0.92))
        cr.set_line_width(brd_w)
        cr.stroke()

        cr.save()
        _rounded_rect(cr, bx + 1.0, 1.0, bw - 2.0, h - 2.0, 5.5)
        cr.clip()

        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
        key_sz = int(round(base_pt * 1.00 * Pango.SCALE))
        act_sz = int(round(base_pt * 0.94 * Pango.SCALE))
        micro_sz = int(round(base_pt * 0.85 * Pango.SCALE))

        f_key = Pango.FontDescription()
        f_key.set_family(font_family)
        f_key.set_weight(Pango.Weight.BOLD)
        f_key.set_size(key_sz)

        f_act = Pango.FontDescription()
        f_act.set_family(font_family)
        f_act.set_size(act_sz)

        f_micro = Pango.FontDescription()
        f_micro.set_family(font_family)
        f_micro.set_size(micro_sz)

        f_badge = Pango.FontDescription()
        f_badge.set_family(font_family)
        f_badge.set_weight(Pango.Weight.BOLD)
        f_badge.set_size(micro_sz)

        # 2. Top-Right Semantic Status Pill (measure first so action label never collides)
        badge_txt, badge_fg, badge_bg = "", "", ""
        if self.is_cap:
            badge_txt, badge_fg, badge_bg = t("cc_keyboard.sc_badge_capturing"), pal["attention"], pal["attention_bg"]
        elif self.conflict_act:
            badge_txt, badge_fg, badge_bg = t("cc_keyboard.sc_badge_overwrite"), pal["attention"], pal["attention_bg"]
        elif self.combo and self.is_editing:
            badge_txt, badge_fg, badge_bg = t("cc_keyboard.sc_badge_editing"), pal["intent"], pal["intent_bg"]
        elif self.combo:
            badge_txt, badge_fg, badge_bg = t("cc_keyboard.free_in_niri"), pal["vitality"], pal["vitality_bg"]

        right_limit = bx + bw - 14.0
        if badge_txt:
            bl = PangoCairo.create_layout(cr)
            bl.set_font_description(f_badge)
            bl.set_text(f"\u25cf {badge_txt}", -1)
            _, be = bl.get_pixel_extents()
            pw = be.width + 16.0
            ph = max(22.0, be.height + 8.0)
            px = right_limit - pw
            py = (stage_h - ph) * 0.5
            _rounded_rect(cr, px, py, pw, ph, 4.0)
            cr.set_source_rgba(*_hex_rgba(badge_bg, 0.94))
            cr.fill_preserve()
            cr.set_source_rgba(*_hex_rgba(badge_fg, 0.55))
            cr.set_line_width(1.0)
            cr.stroke()
            cr.set_source_rgba(*_hex_rgba(badge_fg, 1.0))
            cr.move_to(px + 8.0, py + (ph - be.height) * 0.5)
            PangoCairo.show_layout(cr, bl)
            right_limit = px - 14.0

        # 3. Tactile Keycap Chord + Arrow + Action Target
        cap_h = 34.0
        cap_y = (stage_h - cap_h) * 0.5
        cur_x = bx + 16.0

        def _draw_plus(cx: float) -> float:
            pl = PangoCairo.create_layout(cr)
            pl.set_font_description(f_key)
            pl.set_text("+", -1)
            _, pe = pl.get_pixel_extents()
            cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.80))
            cr.move_to(cx + 5.0, cap_y + (cap_h - pe.height) * 0.5)
            PangoCairo.show_layout(cr, pl)
            return cx + pe.width + 10.0

        if self.is_cap:
            live_mods = [tok for tok in self.cap_prev.replace("...", "").split("+") if tok.strip()]
            for tok in live_mods:
                kw = self._draw_keycap(
                    cr, cur_x, cap_y, cap_h, tok.strip(), f_key,
                    fill_col=pal["attention_bg"], border_col=pal["attention"],
                    text_col=pal["attention"], skirt_col=pal["attention"],
                )
                cur_x = _draw_plus(cur_x + kw)
            kw = self._draw_keycap(
                cr, cur_x, cap_y, cap_h, t("cc_keyboard.press_trigger_hint"), f_key,
                fill_col=pal["attention_bg"], border_col=pal["attention"],
                text_col=pal["focus"], skirt_col=pal["attention"],
                fill_alpha=0.65, dashed=True, line_w=1.4,
            )
            cur_x += kw
        elif self.combo:
            parts = [p.strip() for p in self.combo.split("+") if p.strip()]
            for pi, tok in enumerate(parts):
                if pi > 0:
                    cur_x = _draw_plus(cur_x)
                is_last = (pi == len(parts) - 1)
                is_mod = tok in _MOD_TOKENS and not is_last
                if is_mod:
                    kw = self._draw_keycap(
                        cr, cur_x, cap_y, cap_h, tok, f_key,
                        fill_col=surf_hex, border_col=pal["attention"],
                        text_col=pal["focus_val"], skirt_col=pal["attention"],
                        fill_alpha=0.92, line_w=1.15,
                    )
                else:
                    kw = self._draw_keycap(
                        cr, cur_x, cap_y, cap_h, tok, f_key,
                        fill_col=pal["intent_bg"], border_col=pal["intent"],
                        text_col=pal["focus"], skirt_col=pal["intent"],
                        fill_alpha=0.92, line_w=1.5,
                    )
                cur_x += kw
        else:
            kw1 = self._draw_keycap(
                cr, cur_x, cap_y, cap_h, "Mod", f_key,
                fill_col=surf_hex, border_col=pal["structure"],
                text_col=pal["inactive"], skirt_col=pal["structure"],
                fill_alpha=0.55, dashed=True, line_w=1.0,
            )
            cur_x = _draw_plus(cur_x + kw1)
            kw2 = self._draw_keycap(
                cr, cur_x, cap_y, cap_h, "Key", f_key,
                fill_col=surf_hex, border_col=pal["structure"],
                text_col=pal["inactive"], skirt_col=pal["structure"],
                fill_alpha=0.55, dashed=True, line_w=1.0,
            )
            cur_x += kw2

        # Arrow → Action Target
        if self.target_label and (right_limit - cur_x) >= 60.0:
            al = PangoCairo.create_layout(cr)
            al.set_font_description(f_key)
            al.set_text("\u2192", -1)
            _, ae = al.get_pixel_extents()
            cur_x += 12.0
            cr.set_source_rgba(*_hex_rgba(pal["intent"] if self.combo else pal["schema"], 0.85))
            cr.move_to(cur_x, cap_y + (cap_h - ae.height) * 0.5)
            PangoCairo.show_layout(cr, al)
            cur_x += ae.width + 12.0

            avail_act = max(24.0, right_limit - cur_x)
            tl = PangoCairo.create_layout(cr)
            tl.set_font_description(f_act)
            tl.set_ellipsize(Pango.EllipsizeMode.END)
            tl.set_width(int(avail_act * Pango.SCALE))
            tl.set_text(self.target_label, -1)
            _, te = tl.get_pixel_extents()
            cr.set_source_rgba(*_hex_rgba(pal["focus_val"] if self.combo else pal["schema"], 0.95))
            cr.move_to(cur_x, (stage_h - te.height) * 0.5)
            PangoCairo.show_layout(cr, tl)

        # 4. Integrated Telemetry Footer Strip
        fy = stage_h
        cr.set_source_rgba(*_hex_rgba(surf_hex, 0.78))
        cr.rectangle(bx + 1.0, fy, bw - 2.0, footer_h)
        cr.fill()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.72))
        cr.set_line_width(1.0)
        cr.move_to(bx + 1.0, fy + 0.5)
        cr.line_to(bx + bw - 1.0, fy + 0.5)
        cr.stroke()

        if self.is_cap:
            foot_l, foot_col = t("cc_keyboard.sc_stage_cap_hint"), pal["attention"]
        elif self.conflict_act:
            foot_l, foot_col = t("cc_keyboard.sc_stage_overwrites_hint", p0=self.conflict_act), pal["attention"]
        elif self.combo and self.is_editing:
            foot_l, foot_col = t("cc_keyboard.sc_stage_editing_hint"), pal["focus_val"]
        elif self.combo:
            foot_l, foot_col = t("cc_keyboard.sc_stage_ready_hint"), pal["vitality"]
        else:
            foot_l, foot_col = t("cc_keyboard.sc_stage_click_hint"), pal["schema"]

        foot_r = "~/.config/niri/config.kdl"
        fr_lay = PangoCairo.create_layout(cr)
        fr_lay.set_font_description(f_micro)
        fr_lay.set_text(foot_r, -1)
        _, fre = fr_lay.get_pixel_extents()

        fpad = 12.0
        fl_lay = PangoCairo.create_layout(cr)
        fl_lay.set_font_description(f_micro)
        fl_lay.set_ellipsize(Pango.EllipsizeMode.END)
        fl_lay.set_width(int(max(40.0, bw - fpad * 3.0 - fre.width) * Pango.SCALE))
        fl_lay.set_text(foot_l, -1)
        _, fle = fl_lay.get_pixel_extents()

        fty = fy + max(2.0, (footer_h - max(fle.height, fre.height)) * 0.5)
        cr.set_source_rgba(*_hex_rgba(foot_col, 0.92))
        cr.move_to(bx + fpad, fty)
        PangoCairo.show_layout(cr, fl_lay)

        cr.set_source_rgba(*_hex_rgba(pal["schema"], 0.78))
        cr.move_to(bx + bw - fpad - fre.width, fty)
        PangoCairo.show_layout(cr, fr_lay)

        cr.restore()
        return False


_SHORTCUT_CHORD_CLS = None


def ShortcutChordWidget(*args, **kwargs):
    global _SHORTCUT_CHORD_CLS
    if _SHORTCUT_CHORD_CLS is None:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        class _ShortcutChord(_ShortcutChordMixin, Gtk.DrawingArea):
            pass

        _SHORTCUT_CHORD_CLS = _ShortcutChord
    return _SHORTCUT_CHORD_CLS(*args, **kwargs)


def _build_shortcuts_standard(cc_panel, canvas: Canvas, st: dict) -> list:
    cards = _feedback_box()
    binds = st["binds"]
    cap_mode = str(STATE.get("keyboard", "capture_mode", "") or "")
    cap_prev = str(STATE.get("keyboard", "capture_preview", "") or "")
    wiz = STATE.get("keyboard", "conflict_wizard", {}) or {}

    std_by_norm = {_norm_action_str(d["action"]): d for d in DEFAULT_NIRI_SHORTCUTS}
    nc_specs = _nc_targets_spec()
    nc_by_tgt = {tgt: lbl for lbl, tgt in nc_specs}

    # =====================================================================
    # CONDITIONAL — Conflict Resolution Wizard Card
    # =====================================================================
    if isinstance(wiz, dict) and wiz.get("conflicts"):
        confs = wiz["conflicts"]
        w_mode = wiz.get("mode", "choice")
        w_step = int(wiz.get("step", 0))
        cw_box = Box(
            t("cc_keyboard.default_restore_conflicts", p0=len(confs)),
            icon="\uf071",
            actions=[
                ActionBtn(t("cc_keyboard.overwrite_save_orphans"), "cc_kbd:conflict_overwrite", tone="warn"),
                ActionBtn(t("cc_keyboard.guided_rebind"), "cc_kbd:conflict_guided", tone="apply")
                if w_mode != "guided"
                else Txt(t("cc_keyboard.step", p0=w_step + 1, p1=len(confs)), role="schema"),
                ActionBtn(t("cc_keyboard.cancel"), "cc_kbd:conflict_cancel", tone="neutral"),
            ],
            highlighted=False,
        )
        if w_mode == "guided" and 0 <= w_step < len(confs):
            cur_c = confs[w_step]
            cw_box.add_row(f"  {Badge(t('cc_keyboard.guided_step_press_new_shortcut_key', p0=w_step + 1, p1=len(confs)), style='warn')}  {Txt(cap_prev, role='focus')}")
            cw_box.add_kv(t("cc_keyboard.default_wants_combo"), f"{Txt(cur_c['combo'], role='focus')} \u2192 {Txt(cur_c['default_label'], role='focus')}")
            cw_box.add_kv(
                t("cc_keyboard.displaced_user_action"),
                Txt(cur_c["current_action"], role="attention"),
                right_markup=f"{ActionBtn(t('cc_keyboard.move_to_orphans'), 'cc_kbd:conflict_step_orphan', tone='neutral')} {ActionBtn(t('cc_keyboard.skip_default'), 'cc_kbd:conflict_step_skip', tone='danger_soft')}",
            )
        else:
            cw_box.add_table(
                headers=[t("cc_keyboard.combo"), t("cc_keyboard.current_user_action"), t("cc_keyboard.default_action_to_restore")],
                widths=[18, 28, "flex"],
                rows=[
                    [
                        Txt(c["combo"], role="focus"),
                        Txt(canvas.truncate_plain(c["current_action"], 26), role="attention"),
                        Txt(canvas.truncate_plain(c["default_label"], 28), role="vitality"),
                    ]
                    for c in confs[:8]
                ],
            )
        cards.append(cw_box)

    bd = _get_bind_draft()
    flt_cat = str(STATE.get("keyboard", "bind_filter", "ALL") or "ALL")
    shortcut_q = str(STATE.get("keyboard", "shortcut_query", "") or "").strip()
    sql = shortcut_q.lower()
    is_cap = (cap_mode == "shortcut_combo")
    by_combo = {b["combo"]: b["action"] for b in binds}
    by_norm_act = {_norm_action_str(b["action"]) for b in binds}
    combo_disp = bd["combo"]
    is_editing = bool(bd["edit_orig"])
    conflict_act = by_combo.get(combo_disp, "") if combo_disp and combo_disp != bd["edit_orig"] else ""
    kdl_act = _build_niri_action_from_draft(bd)
    active_edit_combo = bd["edit_orig"] or (combo_disp if combo_disp in by_combo else "")

    # Orphans & Unbound Standard Actions computation
    store = _load_shortcuts_store()
    orphans = [o for o in store["orphans"] if _norm_action_str(o.get("action")) not in by_norm_act]
    unbound_std = [d for d in DEFAULT_NIRI_SHORTCUTS if _norm_action_str(d["action"]) not in by_norm_act]
    tot_orphans = len(orphans) + len(unbound_std)

    if sql:
        orphans = [o for o in orphans if sql in f"{o.get('label', '')} {o.get('action', '')}".lower()]
        unbound_std = [d for d in unbound_std if sql in f"{t('cc_keyboard.shortcut_' + d['id'])} {d['combo']} {d['action']}".lower()]

    # =====================================================================
    # BOX 1 — Active Keybindings Library (Master-Detail + Clean Pagination)
    # =====================================================================
    restore_scope = flt_cat if flt_cat in ("window", "system", "apps") else "window"
    b_box = Box(
        t("cc_keyboard.active_keybindings", p0=len(binds)),
        icon="\uf0c9",
        actions=[
            ActionBtn(t("cc_keyboard.sc_new_btn"), "cc_kbd:bind_new", tone="apply"),
            ActionBtn(t("cc_keyboard.sc_restore_defaults_btn"), f"cc_kbd:restore_defaults:{restore_scope}", tone="warn"),
            ActionBtn(t("cc_keyboard.reload_niri"), "cc_kbd:reload_niri", tone="neutral"),
        ],
        highlighted=False,
    )
    flt_pills = " ".join(
        SelectPill(
            t("cc_keyboard.flt_orphans", count=tot_orphans) if cid == "orphans" else t(f"cc_keyboard.flt_{cid}"),
            f"cc_kbd:bind_flt:{cid}",
            selected=(flt_cat == cid),
            style="secondary",
        )
        for cid in ("ALL", "window", "system", "apps", "custom", "orphans")
    )
    b_box.add_kv(t("cc_keyboard.sc_category_lbl"), flt_pills)

    # Prepare filtered list + pagination controls
    if flt_cat == "orphans":
        combined_orphans = [("orphan", idx, o) for idx, o in enumerate(orphans)] + [("default", 0, d) for d in unbound_std]
        total_items = len(combined_orphans)
    else:
        shown = [b for b in binds if flt_cat == "ALL" or b["cat"] == flt_cat]
        if sql:
            shown = [
                b for b in shown
                if sql in f"{b['combo']} {b['cat']} {b['action']} {_humanize_niri_action(b['action'], std_by_norm, nc_by_tgt)}".lower()
            ]
        total_items = len(shown)

    total_pages = max(1, (total_items + _PAGE_SIZE - 1) // _PAGE_SIZE)
    cur_page = max(0, min(total_pages - 1, int(STATE.get("keyboard", "bind_page", 0) or 0)))
    start_i = cur_page * _PAGE_SIZE
    end_i = min(total_items, start_i + _PAGE_SIZE)

    page_right = ""
    if total_items > 0:
        range_lbl = Txt(t("cc_keyboard.sc_page_range", p0=start_i + 1, p1=end_i, p2=total_items), role="schema")
        if total_pages > 1:
            prev_b = ActionBtn("\u2039", f"cc_kbd:bind_page:{cur_page - 1}", tone="neutral") if cur_page > 0 else "   "
            next_b = ActionBtn("\u203a", f"cc_kbd:bind_page:{cur_page + 1}", tone="neutral") if cur_page < total_pages - 1 else "   "
            page_right = f"{prev_b}  {range_lbl}  {next_b}"
        else:
            page_right = range_lbl

    b_box.add_kv(
        t("cc_keyboard.filter_shortcuts_lbl"),
        InlineFilter(
            "cc_bind_flt",
            shortcut_q,
            t("cc_keyboard.filter_shortcuts_ph"),
            "cc_kbd:bind_flt_open",
            clear_uri="cc_kbd:bind_flt_clear" if shortcut_q else "",
            width=28,
            active=bool(shortcut_q),
        ),
        right_markup=page_right,
    )
    b_box.add_row(" ")

    if flt_cat == "orphans":
        if not combined_orphans:
            b_box.add_row(f"  {Txt(t('cc_keyboard.no_orphaned_or_missing_standard_ac'), role='vitality')}")
        else:
            o_rows = []
            for kind, idx, item in combined_orphans[start_i:end_i]:
                if kind == "orphan":
                    o_rows.append([
                        Txt(t("cc_keyboard.orphan"), role="attention"),
                        Txt(canvas.truncate_plain(_humanize_niri_action(item.get("action", ""), std_by_norm, nc_by_tgt) or item.get("label", ""), 42), role="focus"),
                        f"{ActionBtn(t('cc_keyboard.bind_key'), f'cc_kbd:bind_orphan:{idx}', tone='apply')} {ActionBtn('x', f'cc_kbd:del_orphan:{idx}', tone='danger_soft')}",
                    ])
                else:
                    lbl = t(f"cc_keyboard.shortcut_{item['id']}")
                    d_id = item["id"]
                    o_rows.append([
                        Txt(t("cc_keyboard.default_2"), role="schema"),
                        Txt(canvas.truncate_plain(f"{lbl} ({item['combo']})", 42), role="val"),
                        f"{ActionBtn('\u21ba ' + item['combo'], f'cc_kbd:bind_std_default:{d_id}', tone='apply')} {ActionBtn(t('cc_keyboard.custom'), f'cc_kbd:bind_std_pick:{d_id}', tone='neutral')}",
                    ])
            b_box.add_table(
                headers=[t("cc_keyboard.kind"), t("cc_keyboard.unbound_action_command"), t("cc_keyboard.rebind_actions")],
                widths=[10, "flex", 28],
                rows=o_rows,
                aligns=["left", "left", "right"],
            )
    else:
        if not shown:
            b_box.add_row(f"  {Txt(t('cc_keyboard.no_matching_bindings_in_this_categ'), role='inactive')}")
        else:
            rows = []
            for b in shown[start_i:end_i]:
                idx = binds.index(b)
                human_act = _humanize_niri_action(b["action"], std_by_norm, nc_by_tgt)
                b_cmb = b["combo"]
                rows.append([
                    InspectorCell(
                        canvas.truncate_plain(b_cmb, 18),
                        f"cc_kbd:bind_edit:{idx}",
                        selected=(b_cmb == active_edit_combo),
                    ),
                    Txt(b["cat"].upper()[:6], role="schema"),
                    Txt(canvas.truncate_plain(human_act, 40), role="val"),
                    f"{ActionBtn(t('cc_keyboard.unbind'), f'cc_kbd:unbind:{idx}', tone='warn')} {ActionBtn('x', f'cc_kbd:del_bind:{b_cmb}', tone='danger_soft')}",
                ])
            b_box.add_table(
                headers=[t("cc_keyboard.shortcut"), t("cc_keyboard.cat"), t("cc_keyboard.niri_action_command"), t("cc_keyboard.actions")],
                widths=[20, 9, "flex", 16],
                rows=rows,
                aligns=["left", "left", "left", "right"],
            )
    cards.append(b_box)

    # =====================================================================
    # BOX 2 — Shortcut Composer & Interactive Pure-Cairo Keycap Stage
    # =====================================================================
    cap_btn = (
        ActionBtn(t("cc_keyboard.cancel_esc"), "cc_kbd:cap_stop", tone="warn", modal_active=True)
        if is_cap
        else ActionBtn(t("cc_keyboard.capture_keys_2"), "cc_kbd:cap_start:shortcut_combo", tone="apply")
    )
    manual_fld = InlineField(
        "cc_bind_combo",
        "cc_kbd:bind_inline_combo",
        value="",
        empty_label=t("cc_keyboard.manual"),
        placeholder="Mod+Shift+G",
        width=16,
        icon="\u270e",
        tone="neutral",
    )
    has_draft_ctx = bool(combo_disp or is_editing or bd["cmd"])
    clear_btn = ActionBtn(t("cc_keyboard.clear"), "cc_kbd:bind_clear", tone="warn" if has_draft_ctx else "neutral")
    comp_title = (
        t("cc_keyboard.sc_editing_title", p0=bd["edit_orig"])
        if is_editing
        else t("cc_keyboard.sc_composer_title")
    )
    sb_box = Box(
        comp_title,
        icon="\uf084",
        actions=[cap_btn, manual_fld, clear_btn],
        highlighted=False,
    )

    mode_pills = " ".join([
        SelectPill(t("cc_keyboard.standard_action"), "cc_kbd:bind_mode:standard", selected=(bd["mode"] == "standard")),
        SelectPill(t("cc_keyboard.custom_command"), "cc_kbd:bind_mode:spawn", selected=(bd["mode"] == "spawn")),
        SelectPill(t("cc_keyboard.nc_ui_panel"), "cc_kbd:bind_mode:ncui", selected=(bd["mode"] == "ncui")),
    ])
    sb_box.add_kv(t("cc_keyboard.sc_action_source_lbl"), mode_pills)

    if bd["mode"] == "standard":
        cat_counts = {
            cid: sum(1 for d in DEFAULT_NIRI_SHORTCUTS if d["cat"] == cid)
            for cid in ("window", "system", "apps")
        }
        cat_pills = " ".join(
            SelectPill(
                t("cc_keyboard.sc_cat_count", p0=t(f"cc_keyboard.cat_{cid}"), p1=cat_counts[cid]),
                f"cc_kbd:bind_std_cat:{cid}",
                selected=(bd["std_cat"] == cid),
                style="secondary",
            )
            for cid in ("window", "system", "apps")
        )
        sb_box.add_kv(t("cc_keyboard.sc_category_lbl"), cat_pills)
        sb_box.add_row(" ")

        std_items = [d for d in DEFAULT_NIRI_SHORTCUTS if d["cat"] == bd["std_cat"]]
        n_cols = 3
        col_w = max(22, (canvas.inner_cols - 6) // n_cols)
        act_pills = [
            SelectPill(
                canvas.truncate_plain(t(f"cc_keyboard.shortcut_{d['id']}"), col_w - 5),
                f"cc_kbd:bind_std_act:{d['id']}",
                selected=(bd["std_act"] == d["action"]),
            )
            for d in std_items
        ]
        grid_rows = []
        for i in range(0, len(act_pills), n_cols):
            chunk = act_pills[i : i + n_cols]
            while len(chunk) < n_cols:
                chunk.append("")
            grid_rows.append(chunk)
        sb_box.add_table(
            headers=["", "", ""],
            widths=[col_w, col_w, "flex"],
            rows=grid_rows,
            aligns=["left", "left", "left"],
            show_headers=False,
        )
    elif bd["mode"] == "spawn":
        cmd_disp = (
            Txt(canvas.truncate_plain(bd["cmd"], 46), role="focus")
            if bd["cmd"]
            else Txt(t("cc_keyboard.click_set_command_to_choose_comman"), role="inactive")
        )
        sb_box.add_kv(
            t("cc_keyboard.sc_command_lbl"),
            cmd_disp,
            right_markup=ActionBtn(t("cc_keyboard.set_command"), "cc_kbd:bind_prompt_cmd", tone="neutral"),
        )
    else:
        sb_box.add_row(" ")
        n_cols = 4
        col_w = max(17, (canvas.inner_cols - 8) // n_cols)
        nc_pills = [
            SelectPill(canvas.truncate_plain(lbl, col_w - 5), f"cc_kbd:bind_nc:{tgt}", selected=(bd["nc_target"] == tgt))
            for lbl, tgt in nc_specs
        ]
        grid_rows = []
        for i in range(0, len(nc_pills), n_cols):
            chunk = nc_pills[i : i + n_cols]
            while len(chunk) < n_cols:
                chunk.append("")
            grid_rows.append(chunk)
        sb_box.add_table(
            headers=["", "", "", ""],
            widths=[col_w, col_w, col_w, "flex"],
            rows=grid_rows,
            aligns=["left", "left", "left", "left"],
            show_headers=False,
        )

    kdl_line = f"{combo_disp or '<keys>'} {{ {kdl_act or '<command>'}; }}"
    can_save = bool(combo_disp and kdl_act)
    save_btn = ActionBtn(t("cc_keyboard.save_reload_niri"), "cc_kbd:bind_save", tone="apply" if can_save else "inactive")
    sb_box.add_section(t("cc_keyboard.sc_preview_sec"), actions=[save_btn])
    sb_box.add_kv(
        t("cc_keyboard.sc_kdl_rule_lbl"),
        Txt(canvas.truncate_plain(kdl_line, 62), role="val" if can_save else "inactive"),
    )
    sb_box.add_row(" ")
    sb_box.open_bottom = True

    stage_target = _humanize_niri_action(kdl_act, std_by_norm, nc_by_tgt) if kdl_act else ""
    conflict_human = _humanize_niri_action(conflict_act, std_by_norm, nc_by_tgt) if conflict_act else ""
    chord_widget = ShortcutChordWidget(
        combo=combo_disp,
        target_label=stage_target,
        is_cap=is_cap,
        cap_prev=cap_prev,
        conflict_act=conflict_human,
        is_editing=is_editing,
        on_stage_click=(
            lambda: cc_panel.on_link("cc_kbd:cap_stop" if is_cap else "cc_kbd:cap_start:shortcut_combo")
            if cc_panel
            else None
        ),
        widget_width=int(round(canvas.total_px)),
        fixed_height=max(92, int(round(canvas.char_h * 5.0))),
        inset_x=max(14, int(round(canvas.char_w * 2.0))),
        font_px=max(10.0, float(canvas.char_w) * 1.40),
    )
    sb_foot = Box("", highlighted=False)
    sb_foot.open_top = True
    sb_foot.add_row(" ")
    cards.append(AsciiCard(sb_box, canvas, embedded_widget=chord_widget, footer_box_model=sb_foot))

    # =====================================================================
    # BOX 3 — NC-UI Internal 2D Navigation Reference (Collapsible Footer Box)
    # =====================================================================
    nav_open = bool(cc_panel.get_state("kbd_nc_nav", False)) if (cc_panel and hasattr(cc_panel, "get_state")) else False
    nav_uri = f"ui:state:kbd_nc_nav:{'false' if nav_open else 'true'}"
    nav_box = Box(
        t("cc_keyboard.nc_ui_internal_2d_navigation_keys"),
        icon="\uf11c",
        actions=[FoldIconBtn(nav_open, nav_uri)],
        highlighted=False,
    )
    if nav_open:
        nav_box.add_table(
            headers=[t("cc_keyboard.keys"), t("cc_keyboard.nc_ui_navigation_action")],
            widths=[22, "flex"],
            rows=[
                [Txt(t("cc_keyboard.arrows_tab"), role="focus"), Txt(t("cc_keyboard.2d_spatial_focus_across_buttons_sl"), role="val")],
                [Txt(t("cc_keyboard.shift_left_right"), role="focus"), Txt(t("cc_keyboard.cycle_subtabs_waybar_bar_surfing"), role="val")],
                [Txt(t("cc_keyboard.shift_up_down"), role="focus"), Txt(t("cc_keyboard.switch_control_center_section_expa"), role="val")],
                [Txt(t("cc_keyboard.enter_space"), role="focus"), Txt(t("cc_keyboard.trigger_focused_control_or_prompt"), role="val")],
            ],
        )
    else:
        nav_box.add_row(f"  {Txt(t('cc_keyboard.sc_nav_summary'), role='schema')}")
    cards.append(nav_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    st = _collect_keyboard_state()
    return _build_shortcuts_standard(cc_panel, canvas, st)
