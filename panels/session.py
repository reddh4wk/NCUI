#!/usr/bin/env python3
import getpass
import os
from core.config import get_screen_lock_cmd
from core.i18n import get_active_language
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
    title = "Session & Power"
    icon = "\uf011"
    supports_expand = False
    hover_highlight = True

    def __init__(self, shell=None):
        super().__init__(shell)
        self.pending_confirm = ""  # "suspend" | "logout" | "reboot" | "poweroff"

    def get_cols(self, force_expanded: bool = False) -> int:
        return 44

    def get_bar_pill(self) -> dict:
        return {
            "text": Txt("\uf011", role="focus"),
            "tooltip": "",
            "class": "module-nc",
        }

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        is_it = (get_active_language() == "it")

        target_cols = 44
        canvas.set_cols(target_cols)

        user = getpass.getuser()
        host = os.uname().nodename
        uptime_str = _read_system_uptime()
        lock_cmd = get_screen_lock_cmd()

        cards = []

        # --- CARD 1: ACTIVE SESSION ---
        hero_box = Box(
            "Sessione Attiva" if is_it else "Active Session",
            icon="\uf007",
            actions=[],
            highlighted=False,
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        lock_state_txt = StateBadge(
            bool(lock_cmd),
            "Lock Attivo" if is_it else "Lock Ready",
            "Non imp." if is_it else "Unset",
            on_tone="ok_soft",
        )
        hero_rows = [
            [
                f"{Txt('\uf120', role='schema')}  {Txt(f'{user}@{host}', role='focus')}",
                lock_state_txt,
            ],
            [
                f"{Txt('\uf017', role='schema')}  {Txt('Uptime:', role='schema')} {Txt(uptime_str, role='val')}",
                Txt("TTY / Wayland", role="schema"),
            ],
        ]
        hero_box.add_table(
            headers=[],
            widths=["flex", 14],
            rows=hero_rows,
            aligns=["left", "right"],
            uniform_row_height=True,
            show_headers=False,
        )
        cards.append(hero_box)

        # --- CARD 2: POWER & SESSION CONTROLS ---
        act_box = Box(
            "Controlli Alimentazione" if is_it else "Power & Session Controls",
            icon="\uf011",
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        act_box.add_row(" ")

        confirm_lbl = "Conferma" if is_it else "Confirm"

        lock_lbl = "Blocca Schermo" if is_it else "Lock Screen"
        lock_btn = (
            ActionBtn("Lock", "sess:lock", tone="apply", active=True)
            if lock_cmd
            else ActionBtn("Setup", "sess:setup_lock", tone="warn")
        )

        susp_lbl = "Sospendi in RAM" if is_it else "Suspend to RAM"
        if self.pending_confirm == "suspend":
            susp_btn = f"{ActionBtn(confirm_lbl, 'sess:do:suspend', tone='warn', active=True)} {ActionBtn('No', 'sess:cancel', tone='neutral')}"
        else:
            susp_btn = ActionBtn("Suspend", "sess:ask:suspend", tone="warn")

        def _danger_confirm_btn(kind: str, label: str) -> str:
            if self.pending_confirm == kind:
                return f"{ActionBtn(confirm_lbl, f'sess:do:{kind}', tone='danger_soft', active=True)} {ActionBtn('No', 'sess:cancel', tone='neutral')}"
            return ActionBtn(label, f"sess:ask:{kind}", tone="danger_soft")

        lo_lbl = "Termina Sessione" if is_it else "Log Out Session"
        reb_lbl = "Riavvia Sistema" if is_it else "Reboot System"
        pwr_lbl = "Spegni Macchina" if is_it else "Power Off System"

        control_rows = [
            [f"{Txt('\uf023', role='vitality')}  {Txt(lock_lbl, role='focus')}", lock_btn],
            [" ", " "],
            [f"{Txt('\uf186', role='attention')}  {Txt(susp_lbl, role='focus')}", susp_btn],
            [" ", " "],
            [f"{Txt('\uf2f5', role='interrupt_rest')}  {Txt(lo_lbl, role='focus')}", _danger_confirm_btn("logout", "Log Out")],
            [" ", " "],
            [f"{Txt('\uf021', role='interrupt_rest')}  {Txt(reb_lbl, role='focus')}", _danger_confirm_btn("reboot", "Reboot")],
            [" ", " "],
            [f"{Txt('\uf011', role='interrupt_rest')}  {Txt(pwr_lbl, role='focus')}", _danger_confirm_btn("poweroff", "Power Off")],
        ]

        act_box.add_table(
            headers=[],
            widths=["flex", 18],
            rows=control_rows,
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
