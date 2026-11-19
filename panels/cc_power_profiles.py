#!/usr/bin/env python3
from core.i18n import t
from core.privileges import build_privilege_modal_card
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, ProgressBar, SelectPill, StateBadge, Txt
from panels.cc_engine_power import (
    _collect_batteries_and_devices,
    _collect_cpu_power,
    _feedback_cards,
)

_PPD_LABEL_KEYS = {
    "performance": "cc_power.mode_performance",
    "balanced": "cc_power.mode_balanced",
    "power-saver": "cc_power.mode_power_saver",
}


def _build_adv_cpu_rows(sub: Box, st: dict) -> None:
    gov_btns = " ".join(SelectPill(g, f"cc_pwr:gov:{g}", selected=(st["gov"] == g)) for g in st["avail_gov"][:5])
    gov_act = None if st["cpu_priv_ok"] else ActionBtn(t("cc_power.authorize_priv"), "cc_pwr:auth_cpu", tone="apply")
    sub.add_kv(t("cc_power.cpu_governor"), gov_btns, right_markup=gov_act, requires=["cpupower"])

    if st["avail_epp"]:
        epp_btns = " ".join(SelectPill(e.replace("_", " "), f"cc_pwr:epp:{e}", selected=(st["epp"] == e)) for e in st["avail_epp"][:4])
        sub.add_kv(t("cc_power.energy_perf_epp"), epp_btns)
    else:
        sub.add_kv(t("cc_power.energy_perf_epp"), Txt(st["epp"] or t("common.nd"), role="val" if st["epp"] else "inactive"))

    if st["boost"] in ("0", "1"):
        b_on = st["boost"] == "1"
        sub.add_kv(
            t("cc_power.core_boost_cpb"),
            f"{SelectPill(t('cc_power.boost_on'), 'cc_pwr:boost:1', selected=b_on)} {SelectPill(t('cc_power.eco_off'), 'cc_pwr:boost:0', selected=not b_on)}",
        )

    if st["gpu_info"]:
        sub.add_kv(t("cc_power.gpu_power"), Txt(st["gpu_info"], role="val"))


def _build_power_profiles_box(cc_panel, st: dict) -> Box:
    hdr_lbl = (st["ppd_active"] or st["gov"]).upper()
    box = Box(
        t("cc_power.power_profiles_title"),
        icon="\uf0e7",
        actions=[Txt(hdr_lbl, role="schema")],
        highlighted=False,
    )
    if st["ppd_avail"]:
        ppd_pills = " ".join(
            SelectPill(t(_PPD_LABEL_KEYS[p]) if p in _PPD_LABEL_KEYS else p, f"cc_pwr:ppd:{p}", selected=(st["ppd_active"] == p))
            for p in st["ppd_avail"]
        )
        box.add_kv(t("cc_power.power_mode"), ppd_pills, requires=["powerprofilesctl"])
        if st["gpu_info"]:
            box.add_kv(t("cc_power.gpu_power"), Txt(st["gpu_info"], role="val"))
        box.add_collapsible(
            cc_panel,
            "pwr_adv_cpu",
            t("cc_power.advanced_cpu_tuning"),
            lambda sub: _build_adv_cpu_rows(sub, st),
        )
    else:
        box.add_kv(t("cc_power.power_mode"), Txt(t("cc_power.ppd_not_installed"), role="inactive"))
        _build_adv_cpu_rows(box, st)
    return box


def _battery_status_badge(status: str) -> str:
    s = status.lower()
    if s == "charging":
        return StateBadge(True, t("cc_power.charging"), on_tone="ok")
    if s == "full":
        return StateBadge(True, t("cc_power.full"), on_tone="ok")
    if s == "not charging":
        return StateBadge(True, t("cc_power.not_charging"), on_tone="warn")
    return StateBadge(False, t("cc_power.discharging"), t("cc_power.discharging"))


def _build_batteries_box(bd: dict) -> Box:
    bats = bd["batteries"]
    periphs = bd["peripherals"]
    total = len(bats) + len(periphs)
    title = t("cc_power.batteries_title_n", p0=total) if total else t("cc_power.batteries_title")
    box = Box(title, icon="\uf240", highlighted=False)

    if not bats and not periphs:
        box.add_kv(t("cc_power.power_source"), Txt(t("cc_power.ac_desktop_no_battery"), role="inactive"))
        return box

    for b in bats:
        cap = b["capacity"]
        bat_name = b["name"]
        watts_val = b["watts"]
        ch_role = "vitality" if cap >= 50 else ("attention" if cap >= 20 else "fault")
        w_txt = f"  {Txt(f'({watts_val}W)', role='schema')}" if watts_val else ""
        bar = ProgressBar(cap, width=18, color_by_threshold=True, invert_threshold=True)
        box.add_kv(
            t("cc_power.battery_label", p0=bat_name),
            f"{bar} {Txt(f'{cap}%', role=ch_role)}{w_txt}",
            right_markup=_battery_status_badge(b["status"]),
        )
        if b["has_threshold"]:
            thr = b["end_threshold"]
            thr_pills = (
                f"{SelectPill(t('cc_power.charge_max'), f'cc_pwr:bat_thr:{bat_name}:100', selected=(thr == 100))} "
                f"{SelectPill(t('cc_power.charge_preserve'), f'cc_pwr:bat_thr:{bat_name}:80', selected=(thr == 80))}"
            )
            box.add_kv(t("cc_power.charge_health", p0=bat_name), thr_pills)

    if periphs:
        rows = [[Txt(p["name"], role="focus"), Txt(p["type"], role="schema"), Txt(p["charge"], role="val")] for p in periphs]
        box.add_table(
            headers=[t("cc_power.device"), t("cc_power.type"), t("cc_power.charge")],
            widths=[24, 14, "flex"],
            rows=rows,
            aligns=["left", "left", "left"],
        )
    return box


def _build_general_standard(cc_panel, canvas: Canvas) -> list:
    cards = []
    if STATE.get("privileges", "pending_action", ""):
        cards.extend(build_privilege_modal_card(cc_panel, canvas))
    cards.extend(_feedback_cards())
    st = _collect_cpu_power(async_refresh=True, cc_panel=cc_panel)
    bd = _collect_batteries_and_devices(cc_panel=cc_panel)
    cards.append(_build_power_profiles_box(cc_panel, st))
    cards.append(_build_batteries_box(bd))
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_general_standard(cc_panel, canvas)
