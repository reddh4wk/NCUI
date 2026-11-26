#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Waybar Plugin & Popup — Generic NUT Server / UPS (`panels/wp_ups.py`)."""

from core import probe
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, ProgressBar, StateBadge, Txt
from panels.base import BasePanel
from panels.cc_engine_ups import _collect_ups_data, invalidate_ups_cache


def _battery_glyph(charge: int | None) -> str:
    if charge is None:
        return "█"
    return next((g for lim, g in ((10, "▂"), (25, "▃"), (40, "▄"), (55, "▅"), (70, "▆"), (85, "▇")) if charge <= lim), "█")


class Panel(BasePanel):
    id = "ups"
    icon = "\uf240"
    hover_highlight = True
    refresh_ms = 3000

    def __init__(self, shell=None):
        self.title = t("ups.title") if shell is not None else self.id
        super().__init__(shell)

    def get_cols(self, force_expanded: bool = False) -> int:
        return 46

    def get_bar_pill(self) -> dict:
        u = _collect_ups_data()
        if not u["enabled"] or not u["target"]:
            return {"text": "", "tooltip": "", "class": "module-nc-empty"}
        if not probe.has_bin("upsc"):
            return {
                "text": "▂ ERR",
                "tooltip": t("ups.upsc_missing_hint"),
                "class": "module-nc offline",
            }
        if not u["reachable"]:
            return {
                "text": "▂ ERR",
                "tooltip": t("ups.unreachable_hint"),
                "class": "module-nc offline",
            }

        charge = u["charge"]
        glyph = _battery_glyph(charge)
        if charge is not None:
            pill_txt = f"{glyph} {charge}%"
        else:
            pill_txt = f"{glyph} {u['status_raw'] or 'OL'}"

        tip_parts = [u["model"] or u["ups_name"] or "UPS", u["status_text"]]
        if u["runtime_str"]:
            tip_parts.append(u["runtime_str"])
        return {
            "text": pill_txt,
            "tooltip": " | ".join(tip_parts),
            "class": f"module-nc {u['css_state']}",
        }

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        target_cols = 46
        canvas.set_cols(target_cols)

        u = _collect_ups_data(async_refresh=True, cc_panel=self)
        flds = u["popup_fields"]

        has_upsc = probe.has_bin("upsc")
        tone_map = {"online": "ok", "on-battery": "warn", "low-battery": "crit", "offline": "crit"}
        if not u["enabled"]:
            st_badge = StateBadge(False, "", t("ups.disabled"))
        elif not has_upsc:
            st_badge = Badge(t("ui.missing", p0="upsc"), style="warn")
        elif not u["target"]:
            st_badge = Badge(t("cc_power.not_set_badge"), style="warn")
        elif u.get("polling"):
            st_badge = Badge(t("cc_power.polling").upper(), style="warn")
        elif u["css_state"] == "online":
            st_badge = StateBadge(True, u["status_text"].upper(), on_tone="ok")
        else:
            st_badge = Badge(u["status_text"].upper(), style=tone_map.get(u["css_state"], "crit"))

        hdr_title = u["model"] or u["ups_name"] or t("ups.title")
        box = Box(
            canvas.truncate_plain(hdr_title, 18),
            icon="\uf240",
            actions=[
                st_badge,
                ActionBtn("\uf021", "ups:refresh", tone="neutral"),
            ],
            highlighted=False,
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        box.add_row(" ")

        if not u["enabled"]:
            box.add_row(f"  {Txt(t('ups.disabled_hint'), role='inactive')}")
        elif not has_upsc:
            box.add_row(f"  {Txt(t('ups.upsc_missing_hint'), role='attention')}")
        elif not u["target"]:
            box.add_row(f"  {Txt(t('cc_power.not_set'), role='inactive')}")
        elif u.get("polling"):
            box.add_row(f"  {Txt(t('cc_power.polling'), role='inactive')}")
        elif not u["reachable"]:
            box.add_row(f"  {Txt(t('ups.unreachable_hint'), role='attention')}")
            if u["target"]:
                box.add_kv(t("ups.target"), Txt(u["target"], role="val"))
        else:
            rows_added = 0
            if flds.get("status"):
                st_role = "vitality" if u["css_state"] == "online" else ("attention" if u["css_state"] == "on-battery" else "fault")
                box.add_kv(t("ups.status"), Txt(u["status_text"], role=st_role))
                rows_added += 1

            if flds.get("charge") and u["charge"] is not None:
                c_val = int(u["charge"])
                c_role = "vitality" if c_val >= 50 else ("attention" if c_val >= 20 else "fault")
                box.add_kv(t("ups.charge"), f"{ProgressBar(c_val, width=12, color_by_threshold=True, invert_threshold=True)} {Txt(f'{c_val}%', role=c_role)}")
                rows_added += 1

            if flds.get("runtime") and u["runtime_str"]:
                box.add_kv(t("ups.runtime"), Txt(u["runtime_str"], role="val"))
                rows_added += 1

            if flds.get("load") and u["load"] is not None:
                l_val = int(u["load"])
                l_role = "vitality" if l_val < 50 else ("attention" if l_val < 75 else ("interrupt" if l_val < 90 else "fault"))
                box.add_kv(t("ups.load"), f"{ProgressBar(l_val, width=12, color_by_threshold=True)} {Txt(f'{l_val}%', role=l_role)}")
                rows_added += 1

            if flds.get("power"):
                p_parts = []
                if u["realpower"]:
                    p_parts.append(f"{u['realpower']}W")
                if u["apparent_power"]:
                    p_parts.append(f"{u['apparent_power']}VA")
                if p_parts:
                    box.add_kv(t("ups.power"), Txt(" / ".join(p_parts), role="val"))
                    rows_added += 1

            if flds.get("voltage"):
                v_parts = []
                if u["vin"]:
                    v_parts.append(f"IN:{u['vin']}V")
                if u["vout"]:
                    v_parts.append(f"OUT:{u['vout']}V")
                if u["vbat"]:
                    v_parts.append(f"BAT:{u['vbat']}V")
                if v_parts:
                    box.add_kv(t("ups.voltage"), Txt(" ".join(v_parts), role="val"))
                    rows_added += 1

            if flds.get("model") and u["model"]:
                box.add_kv(t("ups.model"), Txt(canvas.truncate_plain(u["model"], 24), role="val"))
                rows_added += 1

            if flds.get("mfr") and u["mfr"]:
                box.add_kv(t("ups.mfr"), Txt(canvas.truncate_plain(u["mfr"], 24), role="val"))
                rows_added += 1

            if flds.get("serial") and u["serial"]:
                box.add_kv(t("ups.serial"), Txt(canvas.truncate_plain(u["serial"], 24), role="val"))
                rows_added += 1

            if flds.get("temp") and u["temp"]:
                box.add_kv(t("ups.temp"), Txt(f"{u['temp']} °C", role="val"))
                rows_added += 1

            if rows_added == 0:
                box.add_row(f"  {Txt(t('ups.no_popup_fields_enabled_in_setting'), role='inactive')}")

        box.add_row(" ")
        open_cc_btn = ActionBtn(f"\uf013 {t('ups.open_cc')}", "ui:open_settings:power:ups", tone="neutral")
        box.add_row(f"  {open_cc_btn}")
        box.add_row(" ")
        return [box]

    def on_link(self, uri: str) -> None:
        if uri == "ups:refresh":
            invalidate_ups_cache()
            self.request_throttled_refresh()
