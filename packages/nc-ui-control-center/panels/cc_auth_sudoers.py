#!/usr/bin/env python3
import glob
import os
import shlex
from core import probe
from core.i18n import t
from core.privileges import (
    build_privilege_modal_card,
    build_sudo_padlock_actions,
    get_privileged_action,
    list_privileged_actions,
)
from core.resilience import probe_bundled_helpers
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, InspectorCell, StateBadge, Txt
from panels.cc_engine_auth import (
    _feedback_cards,
)


def _build_sudoers_standard(cc_panel, canvas: Canvas) -> list:
    actions = list_privileged_actions()
    cards = _feedback_cards() + build_privilege_modal_card(cc_panel, canvas)
    insp_id = str(STATE.get("privileges", "inspect_id", "") or "")
    if insp_id not in [a.id for a in actions] and actions:
        insp_id = actions[0].id

    auth_cnt = sum(1 for a in actions if a.check_installed())
    act_w = max(len(t("cc_auth.revoke")), len(t("cc_auth.authorize")))
    reg_box = Box(
        t("cc_auth.centralized_privileged_actions_reg_n", p0=auth_cnt, p1=len(actions)),
        icon="\uf023",
        actions=build_sudo_padlock_actions(),
        highlighted=False,
    )
    reg_box.add_row(" ")
    rows = []
    for a in actions:
        installed = a.check_installed()
        act_btn = (
            ActionBtn(t("cc_auth.revoke"), f"priv:revoke:{a.id}", tone="danger_soft", min_chars=act_w)
            if installed
            else ActionBtn(t("cc_auth.authorize"), f"priv:request:{a.id}", tone="apply", min_chars=act_w)
        )
        rows.append([
            InspectorCell(a.id, f"cc_auth:priv_inspect:{a.id}", selected=(insp_id == a.id)),
            Txt(canvas.truncate_plain(a.title(), 40), role="schema"),
            StateBadge(installed, t("cc_auth.active"), t("cc_auth.locked"), on_tone="ok"),
            act_btn,
        ])
    reg_box.add_table(
        headers=[t("cc_auth.action_id"), t("cc_auth.description"), t("cc_auth.state"), t("cc_auth.actions")],
        widths=[19, "flex", 12, max(12, act_w + 4)],
        rows=rows,
        aligns=["left", "left", "left", "right"],
    )
    reg_box.add_row(" ")
    cards.append(reg_box)

    sel = get_privileged_action(insp_id)
    if sel:
        is_inst = sel.check_installed()
        insp_box = Box(
            t("cc_auth.rule_inspector_ncui", p0=sel.id),
            icon="\uf15c",
            actions=[
                ActionBtn(t("cc_auth.run"), f"priv:exec:{sel.id}", tone="apply"),
                ActionBtn(t("cc_auth.revoke"), f"priv:revoke:{sel.id}", tone="danger_soft") if is_inst else ActionBtn(t("cc_auth.authorize"), f"priv:request:{sel.id}", tone="apply"),
            ],
            highlighted=False,
        )
        insp_box.add_row(" ")
        insp_box.add_kv(t("cc_auth.security_policy"), Txt(t("cc_auth.least_privilege_no_wildcards_0440"), role="val"))
        insp_box.add_kv(t("cc_auth.target_file"), Txt(sel.filename, role="val"))
        insp_box.add_kv(t("cc_auth.purpose"), Txt(sel.description(), role="val"))
        insp_box.add_section(t("cc_auth.exact_sudoers_content_preview"))
        for ln in sel.sudoers_content().splitlines():
            insp_box.add_row(f"  {Txt(canvas.truncate_plain(ln, canvas.inner_cols - 6), role='val' if not ln.startswith('#') else 'schema')}")
        insp_box.add_row(" ")
        cards.append(insp_box)

    helpers = probe_bundled_helpers()
    all_synced = all(h["synced"] for h in helpers)
    sync_btn = ActionBtn(
        t("cc_auth.btn_sync_helpers"),
        "priv:sync_helpers",
        tone="muted" if all_synced else "apply",
    )
    h_box = Box(
        t("cc_auth.bundled_helpers_title"),
        icon="\uf085",
        actions=[sync_btn],
        highlighted=False,
    )
    h_box.add_row(" ")
    h_rows = []
    for h in helpers:
        sha_disp = h["src_short"] if h["synced"] else f"{h['dst_short']}→{h['src_short']}"
        st_b = StateBadge(
            h["synced"],
            t("cc_auth.helper_synced"),
            t("cc_auth.helper_outdated"),
            on_tone="ok",
        )
        h_rows.append([
            Txt(h["name"], role="focus"),
            Txt(h["dst"], role="val"),
            Txt(sha_disp, role="schema"),
            st_b,
        ])
    h_box.add_table(
        headers=[
            t("cc_auth.helper_col"),
            t("cc_auth.target_col"),
            t("cc_auth.sha_col"),
            t("cc_auth.state"),
        ],
        widths=[18, "flex", 15, 13],
        rows=h_rows,
        aligns=["left", "left", "left", "right"],
    )
    h_box.add_row(" ")
    cards.append(h_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_sudoers_standard(cc_panel, canvas)
