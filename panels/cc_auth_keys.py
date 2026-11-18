#!/usr/bin/env python3
import glob
import os
import shlex
from core import probe
from core.i18n import t
from core.privileges import (
    build_privilege_modal_card,
    get_privileged_action,
    list_privileged_actions,
)
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, StateBadge, Txt
from panels.cc_engine_auth import (
    _feedback_cards,
    _probe_keys,
)


def _build_keys_standard(cc_panel, canvas: Canvas) -> list:
    d = _probe_keys()
    cards = _feedback_cards()

    ssh_box = Box(
        t("cc_auth.ssh_public_keys_ssh_agent_files", p0=len(d['pubs'])),
        icon="\uf084",
        actions=[ActionBtn(t("cc_auth.ssh_add_term"), "cc_auth:ssh_add", tone="apply")],
        highlighted=False,
    )
    ssh_box.add_row(" ")
    ssh_box.add_kv(
        t("cc_auth.ssh_agent"),
        Txt(t("cc_auth.loaded_identities", p0=len(d['agent'])) if d["agent"] else t("cc_auth.socket_idle"), role="val"),
    )
    if d["pubs"]:
        pub_rows = [
            [
                Txt(pk["type"][:11], role="schema"),
                Txt(pk["name"], role="focus"),
                Txt(pk["comment"][:28], role="schema"),
                ActionBtn(t("cc_auth.copy_pub"), f"cc_auth:copy_pub:{pk['name']}", tone="neutral"),
            ]
            for pk in d["pubs"][:8]
        ]
        ssh_box.add_table(
            headers=["", "", "", ""],
            widths=[12, 16, "flex", 13],
            rows=pub_rows,
            aligns=["left", "left", "left", "right"],
            show_headers=False,
        )
    else:
        ssh_box.add_row(f"  {Txt(t('cc_auth.no_ssh_pub_files_found'), role='inactive')}")
    if d["agent"]:
        def _fill_agent(bx: Box):
            for ln in d["agent"]:
                bx.add_row(f"  {Txt(ln, role='schema')}")
        ssh_box.add_collapsible(cc_panel, "auth_ssh_agent", t("cc_auth.active_ssh_agent_keys", p0=len(d['agent'])), _fill_agent)
    ssh_box.add_row(" ")
    cards.append(ssh_box)

    gpg_box = Box(t("cc_auth.gnupg_secret_keyring"), icon="\uf023", highlighted=False)
    gpg_box.add_row(" ")
    if d["gpg"]:
        for ln in d["gpg"][:6]:
            gpg_box.add_row(f"  {Txt(ln, role='val')}", requires=["gpg"])
    else:
        gpg_box.add_row(f"  {Txt(t('cc_auth.no_gpg_secret_keys_in_gnupg'), role='schema')}", requires=["gpg"])
    gpg_box.add_row(" ")
    cards.append(gpg_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_keys_standard(cc_panel, canvas)
