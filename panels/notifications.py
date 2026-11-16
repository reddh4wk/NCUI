#!/usr/bin/env python3
import html
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import Box, Txt, ActionBtn
from ui.markup import wrap_pango_markup
from panels.base import BasePanel
from panels.cc_notifications import get_active_notifs, clear_all_notifs, delete_notif


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

        cards = []
        hdr_box = Box(
            t("notifications.center_title"),
            icon="\uf0f3",
            actions=[ActionBtn(t("notifications.clear_all"), "notif:clear_all", tone="danger_soft")],
            highlighted=True,
        )
        hdr_box.add_row(Txt(t("notifications.active_count", count=len(active)), role="schema"))
        cards.append(hdr_box)

        wrap_w = canvas.inner_cols - 2
        for n in active:
            nid = n.get("id", 0)
            summary = canvas.truncate_plain(n.get("title", t("notifications.default_item_title")), 26)
            n_box = Box(summary, actions=[ActionBtn("x", f"notif:del:{nid}", tone="danger_soft")])
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
        elif uri.startswith("notif:del:"):
            delete_notif(uri.split(":", 2)[2])
        self.request_throttled_refresh()
