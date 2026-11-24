#!/usr/bin/env python3
import getpass
import os
from core.config import get_screen_lock_cmd
from core.i18n import t
from core import probe
from ui.canvas import Canvas
from ui.kit import Box, Txt, ActionBtn, StateBadge
from panels.base import BasePanel


def _read_system_uptime() -> str:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            total_sec = int(float(f.read().split()[0]))
        days, rem = divmod(total_sec, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        if days > 0:
            return f"{days}d {hours:02d}h {mins:02d}m"
        return f"{hours:02d}h {mins:02d}m"
    except Exception:
        return "N/A"


class Panel(BasePanel):
    id = "session"
    icon = "\uf011"
    supports_expand = False
    hover_highlight = True

    def __init__(self, shell=None):
        self.title = t("session.title") if shell is not None else self.id
        super().__init__(shell)
        self.pending_confirm = ""  # "suspend" | "logout" | "reboot" | "poweroff"

    def get_cols(self, force_expanded: bool = False) -> int:
        return 52

    def get_bar_pill(self) -> dict:
        return {
            "text": Txt("\uf011", role="focus"),
            "tooltip": "",
            "class": "module-nc",
        }

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        target_cols = 52
        canvas.set_cols(target_cols)

        user = getpass.getuser()
        host = os.uname().nodename
        uptime_str = _read_system_uptime()
        lock_cmd = get_screen_lock_cmd()

        cards = []

        # --- CARD 1: ACTIVE SESSION (Arch x Nightcity Cyberdeck HUD) ---
        hero_box = Box(
            t("session.active_session"),
            icon="\uf007",
            actions=[],
            highlighted=False,
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        lock_state_txt = StateBadge(
            bool(lock_cmd),
            t("session.lock_ready"),
            t("session.unset"),
            on_tone="ok_soft",
        )
        arch_art = [
            f"{Txt('      /\\       ', role='focus')}",
            f"{Txt('     /  \\      ', role='focus')}",
            f"{Txt('    /\\   \\     ', role='focus')}",
            f"{Txt('   /  ', role='focus')}{Txt('..', role='vitality')}{Txt('  \\    ', role='focus')}",
            f"{Txt('  /   ', role='focus')}{Txt('||', role='schema')}{Txt('   \\   ', role='focus')}",
            f"{Txt(' /   ', role='schema')}{Txt('_||_', role='schema')}{Txt('  -\\  ', role='schema')}",
            f"{Txt('/_-\'\'    \'\'-_\\ ', role='schema')}",
        ]
        hud_right = [
            f"{Txt('╭── ', role='structure')}{Txt(t('session.hud_header'), role='focus')}{Txt(' ──╮', role='structure')}",
            f"  {Txt('\uf007', role='vitality')}  {Txt(f'{user}@{host}', role='focus')}",
            f"  {Txt('\uf017', role='schema')}  {Txt(t('session.uptime'), role='schema')} {Txt(uptime_str, role='val')}",
            f"  {Txt('\uf120', role='schema')}  {Txt(t('session.tty_wayland'), role='val')}",
            f"  {Txt('\uf023', role='schema')}  {lock_state_txt}",
            f"{Txt('╰── ', role='structure')}{Txt(t('session.hud_footer'), role='schema')}{Txt(' ──╯', role='structure')}",
            f" {Txt(':: ', role='structure')}{Txt('SYS.ONLINE', role='vitality')}{Txt(' // ', role='structure')}{Txt('01', role='schema')}{Txt(' ::', role='structure')}",
        ]
        hero_rows = [[arch_art[i], hud_right[i]] for i in range(len(arch_art))]
        hero_box.add_row(" ")
        hero_box.add_table(
            headers=[],
            widths=[15, "flex"],
            rows=hero_rows,
            aligns=["left", "left"],
            uniform_row_height=True,
            show_headers=False,
        )
        hero_box.add_row(" ")
        cards.append(hero_box)

        # --- CARD 2: POWER & SESSION CONTROLS ---
        act_box = Box(
            t("session.power_session_controls"),
            icon="\uf011",
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        act_box.add_row(" ")

        confirm_lbl = t("session.confirm")

        lock_lbl = t("session.lock_screen")
        lock_btn = (
            ActionBtn(t("session.lock"), "sess:lock", tone="neutral")
            if lock_cmd
            else ActionBtn(t("session.setup"), "sess:setup_lock", tone="warn")
        )

        susp_lbl = t("session.suspend_to_ram")
        if self.pending_confirm == "suspend":
            susp_btn = f"{ActionBtn(confirm_lbl, 'sess:do:suspend', tone='warn')} {ActionBtn(t('session.no'), 'sess:cancel', tone='neutral')}"
        else:
            susp_btn = ActionBtn(t("session.suspend"), "sess:ask:suspend", tone="warn")

        def _danger_confirm_btn(kind: str, label: str) -> str:
            if self.pending_confirm == kind:
                return f"{ActionBtn(confirm_lbl, f'sess:do:{kind}', tone='danger_soft')} {ActionBtn(t('session.no'), 'sess:cancel', tone='neutral')}"
            return ActionBtn(label, f"sess:ask:{kind}", tone="danger_soft")

        lo_lbl = t("session.log_out_session")
        reb_lbl = t("session.reboot_system")
        pwr_lbl = t("session.power_off_system")

        session_rows = [
            [f"{Txt('\uf023', role='vitality')}  {Txt(lock_lbl, role='focus')}", lock_btn],
            [" ", " "],
            [f"{Txt('\uf186', role='attention')}  {Txt(susp_lbl, role='focus')}", susp_btn],
        ]
        act_box.add_table(
            headers=[],
            widths=["flex", 20],
            rows=session_rows,
            aligns=["left", "right"],
            uniform_row_height=True,
            show_headers=False,
        )
        act_box.add_row(" ")
        act_box.add_section(t("session.power_sequence"))
        act_box.add_row(" ")
        power_rows = [
            [f"{Txt('\uf2f5', role='interrupt_rest')}  {Txt(lo_lbl, role='focus')}", _danger_confirm_btn("logout", t("session.logout_btn"))],
            [" ", " "],
            [f"{Txt('\uf021', role='interrupt_rest')}  {Txt(reb_lbl, role='focus')}", _danger_confirm_btn("reboot", t("session.reboot_btn"))],
            [" ", " "],
            [f"{Txt('\uf011', role='interrupt_rest')}  {Txt(pwr_lbl, role='focus')}", _danger_confirm_btn("poweroff", t("session.poweroff_btn"))],
        ]
        act_box.add_table(
            headers=[],
            widths=["flex", 20],
            rows=power_rows,
            aligns=["left", "right"],
            uniform_row_height=True,
            show_headers=False,
        )
        act_box.add_row(" ")
        cards.append(act_box)

        return cards

    def on_link(self, uri: str) -> None:
        if not uri.startswith("sess:"):
            return
        act = uri[len("sess:"):]
        if act == "setup_lock":
            from core.state import STATE
            STATE.set("control_center", "subtab_waybar", "options")
            if self.shell:
                self.shell.dispatch_uri("ui:open_settings:waybar")
            return
        if act == "lock":
            lock_cmd = get_screen_lock_cmd()
            if not lock_cmd:
                from core.state import STATE
                STATE.set("control_center", "subtab_waybar", "options")
                if self.shell:
                    self.shell.dispatch_uri("ui:open_settings:waybar")
                return
            probe.spawn_bg(["bash", "-c", f"sleep 0.12; {lock_cmd}"])
            if self.shell:
                self.shell.dispatch_uri("ui:close")
            return
        if act == "cancel":
            self.pending_confirm = ""
            if self.shell:
                self.shell.refresh_view()
            return
        if act.startswith("ask:"):
            self.pending_confirm = act.split(":", 1)[1]
            if self.shell:
                self.shell.refresh_view()
            return
        if act.startswith("do:"):
            kind = act.split(":", 1)[1]
            if kind == "suspend":
                lock_cmd = get_screen_lock_cmd()
                if lock_cmd:
                    probe.spawn_bg(["bash", "-c", f"{lock_cmd} & sleep 0.3; systemctl suspend"])
                else:
                    probe.spawn_bg(["systemctl", "suspend"])
            elif kind == "poweroff":
                probe.spawn_bg(["systemctl", "poweroff"])
            elif kind == "reboot":
                probe.spawn_bg(["systemctl", "reboot"])
            elif kind == "logout":
                if probe.has_bin("niri"):
                    probe.spawn_bg(["niri", "msg", "action", "quit", "--skip-confirmation"])
                else:
                    probe.spawn_bg(["loginctl", "terminate-user", getpass.getuser()])
            if self.shell:
                self.shell.dispatch_uri("ui:close")


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return Panel(shell)
