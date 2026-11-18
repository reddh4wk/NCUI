#!/usr/bin/env python3
import os
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, StateBadge, Txt
from panels.cc_engine_telemetry import get_cpu_telemetry, get_ram_telemetry
from panels.cc_engine_sysinfo import (
    _collect_about_data,
    _feedback_cards,
    _probe_groups,
    _probe_profile,
    _probe_sessions,
)


def build_cards(cc_panel, canvas: Canvas) -> list:
    ab = _collect_about_data()
    cpu = get_cpu_telemetry({})
    ram = get_ram_telemetry()
    cards = _feedback_cards()

    ab_box = Box(t("cc_system.nightcity_workstation_about_this_p"), icon="\uf129", highlighted=False)
    ab_box.add_row(" ")
    ab_box.add_kv(t("cc_system.hostname"), Txt(ab["host"], role="val"))
    ab_box.add_kv(t("cc_system.operating_system"), Txt(ab["os_name"], role="val"))
    ab_box.add_kv(t("cc_system.active_kernel"), Txt(ab["kernel_rel"], role="val"))
    ab_box.add_kv(t("cc_system.dual_kernels"), Txt(" | ".join(ab["kernels"]) or "linux-cachyos / hardened", role="val"))
    ab_box.add_kv(t("cc_system.bootloader_wm"), Txt(f"{ab['limine']}  |  {ab['niri']}", role="schema"))
    ab_box.add_kv(t("cc_system.cpu_ram_uptime"), Txt(f"{cpu['model']}  |  {ram['total']//(1024**3)} GiB  |  {ab['uptime']}", role="val"))
    ab_box.add_row(" ")
    if ab.get("os_icon"):
        ab_box.set_side_emblem(ab["os_icon"], label=t("cc_system.emblem_os"), role="schema", max_rows=6.5)
    cards.append(ab_box)

    p = _probe_profile()
    grps = _probe_groups(p["user"])
    sess = _probe_sessions()
    cur_sid = os.environ.get("XDG_SESSION_ID", "")

    usr_box = Box(
        t("cc_sysinfo.current_user_hdr"),
        icon="\uf007",
        actions=[ActionBtn(t("cc_accounts.change_password_term"), "cc_acc:passwd", tone="warn")],
    )
    usr_box.add_row(" ")
    usr_box.add_kv(t("cc_accounts.username_full"), Txt(f"{p['user']}  ({p['gecos']})", role="val"))
    usr_box.add_kv(t("cc_accounts.uid_gid"), Txt(f"{p['uid']} / {p['gid']}", role="val"))
    usr_box.add_kv(t("cc_accounts.home_directory"), Txt(p["home"], role="val"))
    in_shell_presets = p["shell"] in p["shells"]
    sh_pills = " ".join(SelectPill(os.path.basename(sh), f"cc_acc:chsh:{sh}", selected=(p["shell"] == sh)) for sh in p["shells"])
    if sh_pills:
        usr_box.add_kv(
            t("cc_accounts.login_shell"),
            sh_pills,
            right_markup=None if in_shell_presets else Txt(p["shell"], role="val"),
            requires=["chsh"],
        )
    else:
        usr_box.add_kv(t("cc_accounts.login_shell"), Txt(p["shell"], role="val"))

    usr_box.add_section(t("cc_accounts.system_privilege_groups", p0=p["user"]))
    grp_rows = []
    for g in grps:
        gn = g["name"]
        grp_rows.append([
            Txt(gn, role="focus"),
            Txt(g["gid"], role="schema"),
            Txt(g["desc"], role="val"),
            StateBadge(g["member"], t("cc_accounts.member"), t("cc_accounts.absent") if not g["exists"] else t("cc_accounts.none")),
            ActionBtn(t("cc_accounts.remove"), f"cc_acc:gdel:{gn}", tone="danger_soft")
            if g["member"]
            else ActionBtn(t("cc_accounts.add_user"), f"cc_acc:gadd:{gn}", tone="apply" if g["exists"] else "inactive"),
        ])
    usr_box.add_table(
        headers=[t("cc_accounts.group"), t("cc_accounts.gid"), t("cc_accounts.scope"), t("cc_accounts.state"), t("cc_accounts.action")],
        widths=[12, 6, 26, 12, "flex"],
        rows=grp_rows,
        aligns=["left", "left", "left", "left", "right"],
    )

    usr_box.add_section(t("cc_accounts.active_login_sessions_loginctl"))
    if sess:
        s_rows = []
        for s in sess:
            sid = s["id"]
            is_cur = (sid == cur_sid)
            s_rows.append([
                Txt(sid + (" *" if is_cur else ""), role="vitality" if is_cur else "focus"),
                Txt(s["user"], role="val"),
                Txt(s["seat"], role="schema"),
                Txt(s["tty"], role="val"),
                ActionBtn(t("cc_accounts.terminate"), f"cc_acc:kill_sess:{sid}", tone="danger_soft" if not is_cur else "inactive"),
            ])
        usr_box.add_table(
            headers=[t("cc_accounts.session"), t("cc_accounts.user"), t("cc_accounts.seat"), t("cc_accounts.tty"), t("cc_accounts.action")],
            widths=[12, 16, 14, 14, "flex"],
            rows=s_rows,
            aligns=["left", "left", "left", "left", "right"],
        )
    else:
        usr_box.add_row(f"  {Txt(t('cc_accounts.no_logind_sessions_exposed_in_curr'), role='schema')}")
        usr_box.add_kv(t("cc_accounts.current_pid_tty"), Txt(f"PID {os.getpid()} | {os.environ.get('XDG_SESSION_TYPE', 'tty')}", role="val"))

    usr_box.add_row(" ")
    cards.append(usr_box)
    return cards
