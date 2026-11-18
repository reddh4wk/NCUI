#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Waybar Plugin & Popup — Notification Center (`panels/wp_notifications.py`)."""

import html
import re
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, FoldIconBtn, SelectPill, Txt
from ui.markup import wrap_pango_markup
from panels.base import BasePanel
from panels.cc_engine_settings import (
    clear_all_notifs,
    clear_app_notifs,
    delete_notif,
    get_active_notifs,
    get_app_click_config,
    trigger_notification_click,
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
            b.add_row(" ")
            b.add_row(f"  {Txt(t('notifications.none_available').strip(), role='inactive')}")
            b.add_row(" ")
            return [b]

        rules = _load_rules()
        group_by_app = bool(rules.get("group_by_app", False))

        cards = []
        hdr_actions = [
            SelectPill(
                t("notifications.grouped_on") if group_by_app else t("notifications.grouped_off"),
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
        mode_label = t("notifications.mode_grouped") if group_by_app else t("notifications.mode_chrono")
        cnt_lbl = f"{t('notifications.active_count', count=len(active)).strip()} ({mode_label})"
        hdr_box.add_row(" ")
        hdr_box.add_row(f"  {Txt(cnt_lbl, role='schema')}")
        hdr_box.add_row(" ")
        cards.append(hdr_box)

        wrap_w = canvas.inner_cols - 3

        if group_by_app:
            # Group notifications by app_name
            groups: dict[str, list[dict]] = {}
            for n in active:
                app = str(n.get("app_name") or n.get("app") or t("notifications.system_app")).strip()
                groups.setdefault(app, []).append(n)

            for app_name, items in groups.items():
                app_icon = _get_app_icon(app_name)
                app_slug = re.sub(r"[^a-zA-Z0-9_]", "_", app_name.lower())
                state_key = f"grp_{app_slug}"
                # Default is expanded (True)
                is_open = bool(self.get_state(state_key, True))

                toggle_uri = f"ui:state:{state_key}:{'false' if is_open else 'true'}"
                fold_btn = FoldIconBtn(is_open, toggle_uri)

                app_box = Box(
                    f"{app_name} ({len(items)})",
                    icon=app_icon,
                    actions=[
                        ActionBtn(t("notifications.clear_btn"), f"notif:clear_app:{app_name}", tone="danger_soft"),
                        fold_btn,
                    ],
                    highlighted=False,
                )
                app_box.add_row(" ")

                if not is_open:
                    if items:
                        first_s = canvas.truncate_plain(items[0].get("title", t("notifications.default_item_title")), max(16, canvas.inner_cols - 6))
                        app_box.add_row(f"  {Txt(first_s, role='inactive')}")
                else:
                    is_app_clickable = bool(get_app_click_config(app_name, rules))
                    for idx, n in enumerate(items):
                        nid = n.get("id", 0)
                        summary = canvas.truncate_plain(n.get("title", t("notifications.default_item_title")), 34)
                        time_s = n.get("time", "")
                        time_tag = f" ({time_s})" if time_s else ""
                        del_btn = ActionBtn("x", f"notif:del:{nid}", tone="danger_soft")

                        bullet = Txt("↗", role="vitality") if is_app_clickable else Txt("•", role="schema")
                        title_m = f"  {bullet} {Txt(summary + time_tag, role='focus')}"
                        if is_app_clickable:
                            title_m = f"<a href='{Canvas.escape(f'notif:click:{nid}')}'><span underline='none'>{title_m}</span></a>"
                        app_box.add_row(title_m, right_markup=del_btn)

                        body = n.get("body", "")
                        lines_added = 0
                        if body:
                            for raw_l in body.splitlines():
                                for w_l in wrap_pango_markup(Canvas.escape(raw_l), max_chars=wrap_w - 6):
                                    body_m = f"    {Txt(html.unescape(w_l), role='inactive')}"
                                    if is_app_clickable:
                                        body_m = f"<a href='{Canvas.escape(f'notif:click:{nid}')}'><span underline='none'>{body_m}</span></a>"
                                    app_box.add_row(body_m)
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
                summary = canvas.truncate_plain(n.get("title", t("notifications.default_item_title")), 24)
                app = str(n.get("app_name") or n.get("app") or "").strip()
                icon = _get_app_icon(app) if app else "\uf0f3"
                is_app_clickable = bool(app and get_app_click_config(app, rules))

                actions = [ActionBtn("x", f"notif:del:{nid}", tone="danger_soft")]
                if is_app_clickable:
                    actions.insert(0, ActionBtn(t("notifications.click_open"), f"notif:click:{nid}", tone="apply"))
                if app:
                    actions.insert(0, Txt(app[:14], role="schema"))

                n_box = Box(summary, icon=icon, actions=actions, highlighted=False)
                n_box.add_row(" ")
                body = n.get("body", "")
                lines_added = 0
                for raw_l in body.splitlines():
                    for w_l in wrap_pango_markup(Canvas.escape(raw_l), max_chars=wrap_w):
                        body_m = Txt(f"  {html.unescape(w_l)}", role="focus")
                        if is_app_clickable:
                            body_m = f"<a href='{Canvas.escape(f'notif:click:{nid}')}'><span underline='none'>{body_m}</span></a>"
                        n_box.add_row(body_m)
                        lines_added += 1
                        if lines_added >= 15:
                            break
                    if lines_added >= 15:
                        break
                n_box.add_row(" ")
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
        elif uri.startswith("notif:click:"):
            ok, _ = trigger_notification_click(uri.split(":", 2)[2])
            if ok and self.shell:
                self.shell.dispatch_uri("ui:close")
                return
        self.request_throttled_refresh()
