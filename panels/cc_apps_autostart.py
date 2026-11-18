#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import t
from core.state import STATE
from panels.wp_display import resolve_script_path
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, SelectPill, StateBadge, Txt
from panels.cc_engine_apps import (
    _feedback_cards,
    _read_niri_spawns,
    _read_systemd_services,
)


def _build_autostart_standard(cc_panel, canvas: Canvas) -> list:
    spawns = _read_niri_spawns()
    user_svcs = _read_systemd_services(user=True)
    sys_svcs = _read_systemd_services(user=False)
    cards = _feedback_cards()

    sp_box = Box(
        t("cc_apps.niri_spawn_at_startup", p0=len(spawns)),
        icon="\uf135",
        actions=[ActionBtn(t("cc_apps.add_startup"), "cc_apps:prompt_spawn", tone="apply")],
        highlighted=False,
    )
    sp_box.add_row(" ")
    if not spawns:
        sp_box.add_row(f"  {Txt(t('cc_apps.no_spawn_at_startup_rules_found'), role='inactive')}")
    else:
        st_w = max(len(t("cc_apps.active")), len(t("cc_apps.disabled"))) + 2
        btn_w = max(len(t("cc_apps.disable")), len(t("cc_apps.enable")))
        sp_rows = []
        for sp in spawns[:12]:
            sp_idx = sp["idx"]
            st = StateBadge(not sp["disabled"], t("cc_apps.active"), t("cc_apps.disabled"), min_chars=st_w)
            t_lbl = t("cc_apps.disable") if not sp["disabled"] else t("cc_apps.enable")
            t_btn = ActionBtn(t_lbl, f"cc_apps:toggle_spawn:{sp_idx}", tone="danger_soft" if not sp["disabled"] else "apply", min_chars=btn_w)
            acts = f"{t_btn} {ActionBtn('x', f'cc_apps:del_spawn:{sp_idx}', tone='danger_soft')}"
            sp_rows.append([
                st,
                Txt(canvas.truncate_plain(sp["cmd"], 42), role="focus" if not sp["disabled"] else "inactive"),
                acts,
            ])
        sp_box.add_table(
            headers=["", "", ""],
            widths=[st_w + 1, "flex", btn_w + 8],
            rows=sp_rows,
            aligns=["left", "left", "right"],
            show_headers=False,
        )
    sp_box.add_row(" ")
    cards.append(sp_box)

    sv_box = Box(t("cc_apps.user_services_systemctl_user", p0=len(user_svcs)), icon="\uf085", highlighted=False)
    sv_box.add_row(" ")
    if not user_svcs:
        sv_box.add_row(f"  {Txt(t('cc_apps.no_user_systemd_units_active_or_db'), role='inactive')}")
    else:
        rows = []
        for sv in user_svcs[:10]:
            is_act = sv["active"] == "active"
            u_n = sv["unit"]
            rows.append([
                Txt(u_n[:28], role="focus" if is_act else "inactive"),
                StateBadge(is_act, sv["sub"].upper(), sv["active"].upper()),
                f"{ActionBtn(t('cc_apps.restart'), f'cc_apps:svc_user:restart:{u_n}', tone='warn')} {ActionBtn(t('cc_apps.stop'), f'cc_apps:svc_user:stop:{u_n}', tone='danger_soft')}",
            ])
        sv_box.add_table(headers=[t("cc_apps.unit"), t("cc_apps.state"), t("cc_apps.actions")], widths=[30, 14, "flex"], rows=rows, aligns=["left", "left", "right"])

    def _build_sys_ro(b: Box):
        if not sys_svcs:
            b.add_row(f"  {Txt(t('cc_apps.no_system_services_queried'), role='inactive')}")
        else:
            s_rows = [[Txt(s["unit"][:26], role="val"), StateBadge(s["active"] == "active", s["sub"].upper(), t("cc_apps.failed"), on_tone="ok"), Txt(canvas.truncate_plain(s["desc"], 28), role="schema")] for s in sys_svcs[:12]]
            b.add_table(headers=[t("cc_apps.system_unit"), t("cc_apps.state"), t("cc_apps.description")], widths=[28, 12, "flex"], rows=s_rows)

    sv_box.add_collapsible(cc_panel, "apps_sys_svcs", t("cc_apps.system_services_inspector_read_onl", p0=len(sys_svcs)), _build_sys_ro)
    sv_box.add_row(" ")
    cards.append(sv_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_autostart_standard(cc_panel, canvas)
