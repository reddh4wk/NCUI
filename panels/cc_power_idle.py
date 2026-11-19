#!/usr/bin/env python3
from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt
from panels.cc_engine_power import (
    _collect_idle_state,
    _feedback_cards,
)


def _build_wakeup_standard(cc_panel, canvas: Canvas) -> list:
    st = _collect_idle_state()
    cards = []
    if STATE.get("privileges", "pending_action", ""):
        cards.extend(build_privilege_modal_card(cc_panel, canvas))
    cards.extend(_feedback_cards())

    idle_hdr = (
        Badge(t("cc_power.caffeine_on"), style="warn")
        if st["caffeine"]
        else StateBadge(st["idle_daemon"] != "Stopped", t("cc_power.idle_active"), t("cc_power.stopped"), on_tone="ok")
    )
    box = Box(
        t("cc_power.idle_timeout_auto_lock_caffeine_ov"),
        icon="\uf186",
        actions=[idle_hdr],
        highlighted=False,
    )
    box.add_kv(t("cc_power.idle_daemon"), StateBadge(st["idle_daemon"] != "Stopped", st["idle_daemon"].upper(), t("cc_power.stopped")))
    box.add_kv(
        t("cc_power.dim_on_idle"),
        f"{SelectPill(t('common.on'), 'cc_pwr:dim_idle:1', selected=st['dim_idle'])} {SelectPill(t('common.off'), 'cc_pwr:dim_idle:0', selected=not st['dim_idle'])}",
        requires=["brightnessctl"],
    )
    box.add_kv(
        t("cc_power.auto_lock_timeout"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_power.off"), f"cc_pwr:idle_min:{m}", selected=(st["idle_min"] == m), min_chars=0) for m in (5, 10, 15, 30, 0)),
    )
    box.add_kv(
        t("cc_power.dpms_screen_off"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_power.off"), f"cc_pwr:dpms_min:{m}", selected=(st["dpms_min"] == m), min_chars=0) for m in (10, 15, 30, 60, 0)),
    )
    box.add_kv(
        t("cc_power.awake_caffeine"),
        f"{SelectPill(t('cc_power.keep_awake'), 'cc_pwr:caffeine:on', selected=st['caffeine'])} {SelectPill(t('cc_power.allow_idle'), 'cc_pwr:caffeine:off', selected=not st['caffeine'])}",
    )
    box.add_kv(
        t("cc_power.lock_screen_command"),
        Txt(st["lock_cmd"] or t("cc_power.not_set_waybar_options"), role="val" if st["lock_cmd"] else "inactive"),
        right_markup=ActionBtn(t("cc_power.lock_now"), "cc_pwr:lock_now", tone="warn"),
    )
    cards.append(box)

    susp_box = Box(t("cc_power.suspend_and_power_button"), icon="\uf011", highlighted=False)
    susp_box.add_kv(
        t("cc_power.suspend_ac"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_power.never"), f"cc_pwr:susp_ac:{m}", selected=(st["susp_ac"] == m), min_chars=0) for m in (15, 30, 60, 120, 0)),
    )
    susp_box.add_kv(
        t("cc_power.suspend_bat"),
        " ".join(SelectPill(f"{m}m" if m else t("cc_power.never"), f"cc_pwr:susp_bat:{m}", selected=(st["susp_bat"] == m), min_chars=0) for m in (10, 15, 30, 60, 0)),
    )
    pwr_opts = (
        ("poweroff", "cc_power.pwr_btn_poweroff"),
        ("suspend", "cc_power.pwr_btn_suspend"),
        ("hibernate", "cc_power.pwr_btn_hibernate"),
        ("ignore", "cc_power.pwr_btn_ignore"),
    )
    susp_box.add_kv(
        t("cc_power.power_button"),
        " ".join(SelectPill(t(lk), f"cc_pwr:pwr_btn:{k}", selected=(st["power_key"] == k)) for k, lk in pwr_opts),
    )
    cards.append(susp_box)
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_wakeup_standard(cc_panel, canvas)
