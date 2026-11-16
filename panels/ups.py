#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Waybar Widget & Popup — Generic NUT Server / UPS (`panels/ups.py`)."""

from core.i18n import get_active_language, t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, ProgressBar, Txt
from panels.base import BasePanel
from panels.cc_power import _collect_ups_data


def _battery_glyph(charge: int | None) -> str:
    if charge is None:
        return "█"
    if charge <= 10:
        return "▂"
    elif charge <= 25:
        return "▃"
    elif charge <= 40:
        return "▄"
    elif charge <= 55:
        return "▅"
    elif charge <= 70:
        return "▆"
    elif charge <= 85:
        return "▇"
    else:
        return "█"


class Panel(BasePanel):
    id = "ups"
    icon = "\uf240"
    hover_highlight = True
    refresh_ms = 3000

    def __init__(self, shell=None):
        self.title = t("ups.title", "UPS Monitor")
        super().__init__(shell)

    def get_cols(self, force_expanded: bool = False) -> int:
        return 46

    def get_bar_pill(self) -> dict:
        u = _collect_ups_data()
        if not u["enabled"]:
            return {"text": "", "tooltip": "", "class": "module-nc-empty"}
        if not u["reachable"]:
            return {
                "text": "▂ ERR",
                "tooltip": t("ups.unreachable_hint", "NUT server or UPS unreachable."),
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
        is_it = (get_active_language() == "it")
        target_cols = 46
        canvas.set_cols(target_cols)

        u = _collect_ups_data()
        flds = u["popup_fields"]

        tone_map = {"online": "ok", "on-battery": "warn", "low-battery": "crit", "offline": "crit"}
        st_badge = (
            Badge("DISABLED", style="muted")
            if not u["enabled"]
            else Badge(u["status_text"].upper(), style=tone_map.get(u["css_state"], "crit"))
        )

        hdr_title = u["model"] or u["ups_name"] or t("ups.title", "UPS Monitor")
        box = Box(
            canvas.truncate_plain(hdr_title, 26),
            icon="\uf240",
            actions=[
                st_badge,
                ActionBtn("\uf021", "ups:refresh", tone="neutral"),
            ],
            highlighted=u["reachable"],
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        box.add_row(" ")

        if not u["enabled"]:
            box.add_row(f"  {Txt(t('ups.disabled_hint', 'NUT UPS monitor is disabled.'), role='inactive')}")
        elif not u["reachable"]:
            box.add_row(f"  {Txt(t('ups.unreachable_hint', 'NUT server or UPS unreachable.'), role='attention')}")
            if u["target"]:
                box.add_kv("Target", Txt(u["target"], role="schema"))
        else:
            rows_added = 0
            if flds.get("status"):
                st_role = "vitality" if u["css_state"] == "online" else ("attention" if u["css_state"] == "on-battery" else "fault")
                box.add_kv("Stato" if is_it else "Status", Txt(u["status_text"], role=st_role))
                rows_added += 1

            if flds.get("charge") and u["charge"] is not None:
                box.add_kv("Carica" if is_it else "Charge", f"{ProgressBar(u['charge'], width=12, color_by_threshold=False)} {Txt(str(u['charge']) + '%', role='vitality')}")
                rows_added += 1

            if flds.get("runtime") and u["runtime_str"]:
                box.add_kv("Autonomia" if is_it else "Runtime", Txt(u["runtime_str"], role="focus"))
                rows_added += 1

            if flds.get("load") and u["load"] is not None:
                box.add_kv("Carico" if is_it else "Load", f"{ProgressBar(u['load'], width=12, color_by_threshold=True)} {Txt(str(u['load']) + '%', role='intent')}")
                rows_added += 1

            if flds.get("power"):
                p_parts = []
                if u["realpower"]:
                    p_parts.append(f"{u['realpower']}W")
                if u["apparent_power"]:
                    p_parts.append(f"{u['apparent_power']}VA")
                if p_parts:
                    box.add_kv("Potenza" if is_it else "Power", Txt(" / ".join(p_parts), role="val"))
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
                    box.add_kv("Tensione" if is_it else "Voltage", Txt(" ".join(v_parts), role="val"))
                    rows_added += 1

            if flds.get("model") and u["model"]:
                box.add_kv("Modello" if is_it else "Model", Txt(canvas.truncate_plain(u["model"], 24), role="schema"))
                rows_added += 1

            if flds.get("mfr") and u["mfr"]:
                box.add_kv("Produttore" if is_it else "Mfr", Txt(canvas.truncate_plain(u["mfr"], 24), role="schema"))
                rows_added += 1

            if flds.get("serial") and u["serial"]:
                box.add_kv("Seriale" if is_it else "Serial", Txt(canvas.truncate_plain(u["serial"], 24), role="schema"))
                rows_added += 1

            if flds.get("temp") and u["temp"]:
                box.add_kv("Temp", Txt(f"{u['temp']} °C", role="val"))
                rows_added += 1

            if rows_added == 0:
                box.add_row(f"  {Txt('No popup fields enabled in settings.' if not is_it else 'Nessun campo abilitato nelle impostazioni.', role='inactive')}")

        box.add_row(" ")
        open_cc_btn = ActionBtn(f"\uf013 {t('ups.open_cc', 'NUT Server / UPS')}", "ui:open_settings:ups", tone="select")
        box.add_row(f"  {open_cc_btn}")
        box.add_row(" ")
        return [box]

    def on_link(self, uri: str) -> None:
        if uri == "ups:refresh":
            self.request_throttled_refresh()
