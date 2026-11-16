#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Notification Center Popup (`panels/notifications.py`)."""

import html
import re
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, Txt
from ui.markup import wrap_pango_markup
from panels.base import BasePanel
from panels.cc_notifications import (
    clear_all_notifs,
    clear_app_notifs,
    delete_notif,
    get_active_notifs,
    _load_rules,
    _save_rules,
)


def _get_app_icon(app_name: str) -> str:
    low = (app_name or "").lower()
    if "telegram" in low:
        return "\ue217"
    if "firefox" in low:
        return "\uf269"
    if "brave" in low or "chrome" in low or "chromium" in low:
        return "\uf268"
    if "discord" in low:
        return "\uf392"
    if "spotify" in low:
        return "\uf1bc"
    if "slack" in low:
        return "\uf198"
    if "thunderbird" in low or "mail" in low:
        return "\uf0e0"
    if "screenshot" in low or "satty" in low:
        return "\uf030"
    if "ghostty" in low or "kitty" in low or "terminal" in low:
        return "\uf120"
    return "\uf0f3"


class Panel(BasePanel):
    id = "notifications"
    icon = "\uf0f3"
    hover_highlight = True

    def __init__(self, shell=None):
        self.title = t("notifications.title")
        super().__init__(shell)

    def get_bar_pill(self) -> dict:
        active = get_active_notifs()
        count = len(active)
        icon = "\uf0f3" if count > 0 else "\uf1f6"
        css_cls = "nc-glow" if count > 0 else "module-nc"
        tip = t("notifications.tooltip_active", count=count) if count > 0 else t("notifications.tooltip_empty")
        return {
            "text": Txt(icon, role="focus" if count > 0 else "inactive"),
            "tooltip": tip,
            "class": css_cls,
        }

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        active = get_active_notifs()
        if not active:
            b = Box(t("notifications.title"), icon="\uf1f6")
            b.add_row(Txt(t("notifications.none_available"), role="inactive"))
            return [b]

        rules = _load_rules()
        group_by_app = bool(rules.get("group_by_app", False))

        cards = []
        hdr_actions = [
            SelectPill(
                "Grouped: ON" if group_by_app else "Grouped: OFF",
                "notif:toggle_group",
                selected=group_by_app,
            ),
            ActionBtn(t("notifications.clear_all"), "notif:clear_all", tone="danger_soft"),
        ]

        hdr_box = Box(
            t("notifications.center_title"),
            icon="\uf0f3",
            actions=hdr_actions,
            highlighted=True,
        )
        mode_label = "raggruppate per app" if group_by_app else "ordine cronologico"
        hdr_box.add_row(
            Txt(f"{t('notifications.active_count', count=len(active))} ({mode_label})", role="schema")
        )
        cards.append(hdr_box)

        wrap_w = canvas.inner_cols - 2

        if group_by_app:
            # Group notifications by app_name
            groups: dict[str, list[dict]] = {}
            for n in active:
                app = str(n.get("app_name") or n.get("app") or "System").strip()
                groups.setdefault(app, []).append(n)

            for app_name, items in groups.items():
                app_icon = _get_app_icon(app_name)
                app_slug = re.sub(r"[^a-zA-Z0-9_]", "_", app_name.lower())
                state_key = f"grp_{app_slug}"
                # Default is expanded (True)
                is_open = bool(self.get_state(state_key, True))

                app_box = Box(
                    f"{app_name}",
                    icon=app_icon,
                    actions=[
                        Badge(f"{len(items)}", style="accent"),
                        ActionBtn("Clear", f"notif:clear_app:{app_name}", tone="danger_soft"),
                    ],
                    highlighted=is_open,
                )

                toggle_uri = f"ui:state:{state_key}:{'false' if is_open else 'true'}"
                arrow = "▾" if is_open else "▸"
                toggle_lbl = f"{arrow} {'Riduci dettagli' if is_open else f'Espandi ({len(items)} notifiche)'}"
                app_box.add_row(f"<a href='{Canvas.escape(toggle_uri)}'><span underline='none'>{Txt(toggle_lbl, role='schema')}</span></a>")

                if is_open:
                    for idx, n in enumerate(items):
                        nid = n.get("id", 0)
                        summary = canvas.truncate_plain(n.get("title", t("notifications.default_item_title")), 34)
                        time_s = n.get("time", "")
                        time_tag = f" [{time_s}]" if time_s else ""
                        del_btn = ActionBtn("x", f"notif:del:{nid}", tone="danger_soft")

                        app_box.add_row(
                            f"  {Txt('•', role='intent')} {Txt(summary + time_tag, role='focus')}",
                            right_markup=del_btn,
                        )

                        body = n.get("body", "")
                        lines_added = 0
                        if body:
                            for raw_l in body.splitlines():
                                for w_l in wrap_pango_markup(Canvas.escape(raw_l), max_chars=wrap_w - 6):
                                    app_box.add_row(f"    {Txt(html.unescape(w_l), role='inactive')}")
                                    lines_added += 1
                                    if lines_added >= 8:
                                        break
                                if lines_added >= 8:
                                    break
                        if idx < len(items) - 1:
                            app_box.add_row(f"  {Txt('┄' * max(10, canvas.inner_cols - 8), role='structure')}")

                app_box.add_row(" ")
                cards.append(app_box)

        else:
            # Chronological flat list
            for n in active:
                nid = n.get("id", 0)
                summary = canvas.truncate_plain(n.get("title", t("notifications.default_item_title")), 26)
                app = str(n.get("app_name") or n.get("app") or "").strip()
                icon = _get_app_icon(app) if app else "\uf0f3"

                actions = [ActionBtn("x", f"notif:del:{nid}", tone="danger_soft")]
                if app:
                    actions.insert(0, Badge(app[:14], style="info"))

                n_box = Box(summary, icon=icon, actions=actions)
                body = n.get("body", "")
                lines_added = 0
                for raw_l in body.splitlines():
                    for w_l in wrap_pango_markup(Canvas.escape(raw_l), max_chars=wrap_w):
                        n_box.add_row(Txt(" " + html.unescape(w_l), role="focus"))
                        lines_added += 1
                        if lines_added >= 15:
                            break
                    if lines_added >= 15:
                        break
                cards.append(n_box)

        return cards

    def on_link(self, uri: str) -> None:
        if uri == "notif:clear_all":
            clear_all_notifs()
        elif uri == "notif:toggle_group":
            rules = _load_rules()
            rules["group_by_app"] = not rules.get("group_by_app", False)
            _save_rules(rules)
        elif uri.startswith("notif:clear_app:"):
            app_name = uri.split(":", 2)[2]
            clear_app_notifs(app_name)
        elif uri.startswith("notif:del:"):
            delete_notif(uri.split(":", 2)[2])
        self.request_throttled_refresh()
