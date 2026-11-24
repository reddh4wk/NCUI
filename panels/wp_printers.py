#!/usr/bin/env python3
"""NC-UI Waybar Plugin & Popup — Auto-Hiding Print Spooler (`panels/wp_printers.py`)."""

import time
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, StateBadge, Txt
from panels.base import BasePanel
from panels.cc_engine_printers import (
    get_printers_bar_summary,
    handle_cc_printers_link,
    handle_cc_printers_prompt,
    invalidate_printers_cache,
)

POPUP_COLS = 52


class Panel(BasePanel):
    id = "printers"
    icon = "\uf02f"
    hover_highlight = True
    refresh_ms = 2000

    def __init__(self, shell=None):
        self.title = t("printers_popup.title") if shell is not None else self.id
        super().__init__(shell)

    def get_cols(self, force_expanded: bool = False) -> int:
        return POPUP_COLS

    def get_bar_pill(self) -> dict:
        s = get_printers_bar_summary(include_jobs=False)
        if not s["visible"]:
            return {"text": "", "tooltip": "", "class": "module-nc-empty"}
        cnt = s["count_txt"]
        pill_txt = f"{self.icon}  {cnt}" if cnt else self.icon
        css = f"module-nc {s['css']}".strip() if s["css"] else "module-nc"
        return {"text": pill_txt, "tooltip": "", "class": css}

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        canvas.set_cols(POPUP_COLS)
        s = get_printers_bar_summary(include_jobs=True)
        daemon = s["daemon"]
        snap = s["snap"]
        jobs = s["jobs"]
        printers = snap["printers"]

        confirm_purge = bool(self.get_state("confirm_purge", False))
        if not jobs and confirm_purge:
            self.set_state("confirm_purge", False)
            confirm_purge = False

        if not daemon["scheduler_up"]:
            st_badge = StateBadge(False, "", t("cc_printers.stopped"))
        elif s["css"] == "crit":
            st_badge = Badge(t("printers_popup.alert_badge"), style="crit")
        elif s["css"] == "warn":
            st_badge = Badge(t("printers_popup.warn_badge"), style="warn")
        elif jobs:
            st_badge = StateBadge(True, t("cc_printers.printing"))
        else:
            st_badge = StateBadge(True, t("cc_printers.ready"))

        acts = [
            st_badge,
            ActionBtn("\uf021", "prn_pop:refresh", tone="neutral"),
        ]
        if daemon["scheduler_up"] and jobs:
            if confirm_purge:
                acts.append(ActionBtn(t("printers_popup.confirm_short"), "cc_prn:purge_queue:ALL", tone="danger_soft"))
                acts.append(ActionBtn("x", "prn_pop:cancel_purge", tone="neutral"))
            else:
                acts.append(ActionBtn(t("printers_popup.purge_short"), "prn_pop:ask_purge", tone="danger_soft"))

        hdr_title = (
            t("printers_popup.box_title_n", p0=len(jobs))
            if daemon["scheduler_up"]
            else t("printers_popup.title")
        )
        box = Box(
            canvas.truncate_plain(hdr_title, 26),
            icon=self.icon,
            actions=acts,
            highlighted=False,
            fixed_width=True,
            min_cols=POPUP_COLS,
            max_cols=POPUP_COLS,
        )
        box.add_row(" ")

        fb = STATE.get("printers", "fb", {})
        if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 10.0:
            box.add_row(f"  {Txt(canvas.truncate_plain(str(fb['msg']), POPUP_COLS - 6), role='vitality' if fb.get('ok') else 'fault')}")

        if not daemon["scheduler_up"]:
            box.add_kv(
                t("cc_printers.cups_package"),
                StateBadge(daemon["cups_installed"], t("cc_printers.installed"), t("cc_printers.missing_bin")),
                key_width=17,
            )
            box.add_kv(
                t("cc_printers.boot_autostart"),
                StateBadge(daemon["service_enabled"], t("common.enabled"), t("common.disabled")),
                key_width=17,
            )
            box.add_kv(
                t("cc_printers.kv_cups_scheduler"),
                f"{StateBadge(False, '', t('cc_printers.stopped'))}  {ActionBtn(t('cc_printers.start_cups'), 'cc_prn:svc:start:cups.service', tone='apply')}",
                key_width=17,
            )
        else:
            def_p = next((p for p in printers if p["is_default"]), printers[0] if printers else None)
            if def_p:
                p_name = def_p["name"]
                p_btn = (
                    ActionBtn(t("cc_printers.pause"), f"cc_prn:pause_prn:{p_name}", tone="danger_soft")
                    if def_p["enabled"]
                    else ActionBtn(t("cc_printers.resume"), f"cc_prn:enable_prn:{p_name}", tone="apply")
                )
                box.add_kv(
                    t("printers_popup.kv_default_prn"),
                    Txt(canvas.truncate_plain(p_name, 16), role="val"),
                    key_width=17,
                    right_markup=p_btn,
                )
            else:
                box.add_kv(
                    t("printers_popup.kv_default_prn"),
                    Txt(t("printers_popup.no_printers_short"), role="inactive"),
                    key_width=17,
                )

            if s["alerts"]:
                if len(s["alerts"]) == 1 and def_p and s["alerts"][0][0] == def_p["name"]:
                    al_str = s["alerts"][0][1]
                else:
                    al_str = ", ".join(f"{pn}: {msg}" for pn, msg in s["alerts"][:2])
                box.add_kv(
                    t("cc_printers.kv_alerts"),
                    Txt(canvas.truncate_plain(al_str, 24), role="attention"),
                    key_width=17,
                )

            box.add_section(t("cc_printers.sec_spooler_jobs"))
            if not jobs:
                box.add_row(f"  {Txt(t('printers_popup.queue_idle_hint'), role='inactive')}")
            else:
                hold_w = max(len(t("printers_popup.hold_short")), len(t("printers_popup.rel_short")))
                rows = []
                for j in jobs[:8]:
                    jid = j["id"]
                    if j["state"] == "printing":
                        st_m = StateBadge(True, t("cc_printers.printing"))
                    elif j["state"] == "held":
                        st_m = Badge(t("cc_printers.held_badge"), style="warn")
                    elif j["state"] in ("aborted", "stopped"):
                        st_m = Badge(t("cc_printers.aborted_badge"), style="crit")
                    else:
                        st_m = Txt(t("cc_printers.pending_lbl"), role="val")

                    h_btn = (
                        ActionBtn(t("printers_popup.rel_short"), f"cc_prn:job_hold:{jid}:0", tone="apply", min_chars=hold_w)
                        if j["state"] == "held"
                        else ActionBtn(t("printers_popup.hold_short"), f"cc_prn:job_hold:{jid}:1", tone="warn", min_chars=hold_w)
                    )
                    acts_cell = f"{h_btn} {ActionBtn('x', f'cc_prn:job_cancel:{jid}', tone='danger_soft')}"
                    rows.append([
                        Txt(f"#{j['num']}", role="focus"),
                        Txt(canvas.truncate_plain(j["title"], 18), role="focus_val"),
                        st_m,
                        acts_cell,
                    ])

                box.add_table(
                    headers=[
                        t("cc_printers.col_job_id"),
                        t("cc_printers.col_document"),
                        t("cc_printers.col_state"),
                        t("cc_printers.col_actions"),
                    ],
                    widths=[6, "flex", 10, 12],
                    rows=rows,
                    aligns=["left", "left", "left", "right"],
                )

        box.add_row(" ")
        target_sub = "queue" if jobs else "devices"
        box.set_bottom_bar(
            t("printers_popup.bottom_manage_lbl"),
            actions=[ActionBtn("\uf013", f"ui:open_settings:printers:{target_sub}", tone="neutral")],
        )
        return [box]

    def on_link(self, uri: str) -> None:
        if uri == "prn_pop:refresh":
            self.set_state("confirm_purge", False)
            invalidate_printers_cache(keep_drivers=False)
            if self.shell:
                self.shell.refresh_view()
            return
        if uri == "prn_pop:ask_purge":
            self.set_state("confirm_purge", True)
            if self.shell:
                self.shell.refresh_view()
            return
        if uri == "prn_pop:cancel_purge":
            self.set_state("confirm_purge", False)
            if self.shell:
                self.shell.refresh_view()
            return
        if uri.startswith("cc_prn:"):
            self.set_state("confirm_purge", False)
            handle_cc_printers_link(self, uri)

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        handle_cc_printers_prompt(self, action_id, text)


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return Panel(shell)
