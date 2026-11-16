#!/usr/bin/env python3
import os
from core.config import THEME
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import Box, ProgressBar, Sparkline, Txt, SelectPill, ActionBtn
from ui.markup import format_bytes_short
from panels.base import BasePanel
from panels.cc_system import (
    read_proc_stat,
    get_cpu_bar_pct,
    get_cpu_telemetry,
    get_ram_telemetry,
    get_process_list,
    signal_processes,
)


def _pct_role(pct: float) -> str:
    if pct < 50.0:
        return "vitality"
    if pct < 75.0:
        return "attention"
    if pct < 90.0:
        return "interrupt"
    return "fault"


class SystemPanel(BasePanel):
    supports_expand = True

    def __init__(self, panel_id: str, shell=None):
        self.id = panel_id
        self.is_cpu = (panel_id == "cpu")
        self.title = t("system.cpu_title") if self.is_cpu else t("system.ram_title")
        self.icon = "\uf2db" if self.is_cpu else "\uf538"
        super().__init__(shell)
        self.prev_stat = read_proc_stat() if self.is_cpu else {}
        self.proc_limit = 8

    def get_bar_pill(self) -> dict:
        font = THEME["typography"]["font_family"]
        if self.is_cpu:
            pct = get_cpu_bar_pct()
            text = f"{Txt('\uf2db', role='focus')}  <span font_family='{font}'>{Txt(f'{pct:2.0f}%', role='focus')}</span>"
        else:
            rm = get_ram_telemetry()
            pct_s = f"{rm['pct']:2.0f}%"
            text = f"{Txt('\uf538', role='focus')}  <span font_family='{font}'>{Txt(pct_s, role='focus')}</span>"
        return {"text": text, "tooltip": "", "class": "module-nc"}

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        expanded = force_expanded or self.is_expanded
        group_app = bool(self.get_state("group_by_app", not self.is_cpu))
        show_cores = bool(self.get_state("show_cores", False))
        app_btn = SelectPill(t("system.app_group"), "ui:state:group_by_app:true", selected=group_app)
        pid_btn = SelectPill(t("system.pid_group"), "ui:state:group_by_app:false", selected=not group_app)
        cards = []

        if self.is_cpu:
            tele = get_cpu_telemetry(self.prev_stat)
            self.prev_stat = tele["stat"]
            pct = tele["total_pct"]
            l1, _l5, _l15 = tele["loads"]
            temp = tele["temp_c"]
            temp_role = _pct_role(max(0.0, (temp - 35.0) * 1.8))
            hero = Box(tele["model"][:18], icon="\uf2db", actions=[app_btn, pid_btn], highlighted=True)
            hero.add_row(
                f"  {Txt(t('system.load_label'), role='schema')} {ProgressBar(pct, width=14)} "
                f"{Txt(f'{pct:4.1f}%', role=_pct_role(pct))} {Txt(f'({l1:.2f})', role='val')}"
            )
            max_ghz_s = f"{tele['max_ghz']:4.2f} GHz"
            hero.add_row(
                f"  {Txt(t('system.temp_label'), role='schema')} {Txt(f'{temp:4.1f} C', role=temp_role)}     "
                f"{Txt(t('system.peak_label'), role='schema')} {Txt(max_ghz_s, role='intent')}"
            )
            toggle_c_btn = ActionBtn(
                t("system.hide_details") if show_cores else t("system.show_details"),
                f"ui:state:show_cores:{'false' if show_cores else 'true'}",
                tone="select" if show_cores else "neutral",
                active=show_cores,
            )
            hero.add_section(t("system.core_monitor"), actions=[toggle_c_btn])
            cores = tele["cores_pct"]
            if not show_cores:
                hero.add_row(f"  {Sparkline(cores, max_items=32)}")
            else:
                spark_chars = THEME["ascii"]["sparkline_chars"]
                for r_idx in range(0, len(cores), 4):
                    chunk = cores[r_idx : r_idx + 4]
                    cells = []
                    for c_off, cp in enumerate(chunk):
                        cid = r_idx + c_off
                        s_idx = min(len(spark_chars) - 1, int((cp / 100.0) * len(spark_chars)))
                        cells.append(f"{Txt(f'{cid:02d}', role='schema')}{Txt(f'{spark_chars[s_idx]}{int(round(cp)):3d}%', role=_pct_role(cp))}")
                    hero.add_row(" " + " ".join(cells))
            cards.append(hero)
        else:
            rm = get_ram_telemetry()
            used_g = rm["used"] / (1024.0 ** 3)
            tot_g = rm["total"] / (1024.0 ** 3)
            sw_u_g = rm["sw_used"] / (1024.0 ** 3)
            sw_t_g = rm["sw_total"] / (1024.0 ** 3)
            hero = Box(t("system.memory_box", tot=f"{tot_g:.0f}"), icon="\uf538", actions=[app_btn, pid_btn], highlighted=True)
            hero.add_row(
                f"  {Txt('RAM:', role='schema')}  {ProgressBar(rm['pct'], width=13)} "
                f"{Txt(f'{used_g:4.1f}G', role=_pct_role(rm['pct']))}{Txt(f'/{tot_g:.0f}G', role='val')}"
            )
            hero.add_row(
                f"  {Txt(t('system.free_label'), role='schema')} {Txt(format_bytes_short(rm['avail']), role='vitality')}    "
                f"{Txt(t('system.cache_label'), role='schema')} {Txt(format_bytes_short(rm['cache']), role='val')}    "
                f"{Txt(format_bytes_short(rm['dirty']), role='attention' if rm['dirty'] else 'val')}"
            )
            hero.add_row(
                f"  {Txt(t('system.swap_label'), role='schema')} {ProgressBar(rm['sw_pct'], width=13)} "
                f"{Txt(f'{sw_u_g:4.1f}G/{sw_t_g:.0f}G', role='val')}"
            )
            hero.add_section(t("system.quick_actions"))
            hero.add_row(f"  {ActionBtn(t('system.sync_disks'), 'ui:cmd:sync', tone='apply')}")
            cards.append(hero)

        all_procs = get_process_list(sort_by="cpu" if self.is_cpu else "ram", group_by_app=group_app)
        shown = all_procs[: self.proc_limit]
        n_threads = os.cpu_count() or 1
        max_cmd_w = 84 if expanded else 36
        for p in shown:
            id_tag = f"{p['count']}x" if (group_app and p["count"] > 1) else str(p["pid"])
            target_pids = ",".join(str(x) for x in p["pids"]) if group_app else str(p["pid"])
            p_box = Box(
                f"{p['comm'][:16]} ({id_tag})",
                actions=[
                    ActionBtn("T", f"sys:term:{target_pids}", tone="warn"),
                    ActionBtn("K", f"sys:kill:{target_pids}", tone="danger_soft"),
                ],
            )
            if self.is_cpu:
                norm_cpu = min(100.0, p["cpu"] / float(n_threads) if p["cpu"] > 100.0 else p["cpu"])
                p_cpu_s = f"{p['cpu']:5.1f}%"
                p_rss_s = f"{format_bytes_short(p['rss']):>5}    {p['nlwp']}t"
                p_box.add_row(
                    f"  {ProgressBar(norm_cpu, width=10)} {Txt(p_cpu_s, role=_pct_role(norm_cpu))}    "
                    f"{Txt(p_rss_s, role='val')}"
                )
            else:
                mem_pct = p["mem"] * 2.0
                p_rss_s = f"{format_bytes_short(p['rss']):>6}"
                p_mem_s = f"({p['mem']:4.1f}%)   {p['cpu']:4.1f}%"
                p_box.add_row(
                    f"  {ProgressBar(min(100.0, p['mem'] * 2.5), width=10)} "
                    f"{Txt(p_rss_s, role=_pct_role(mem_pct))} "
                    f"{Txt(p_mem_s, role='val')}"
                )
            cmd_clean = canvas.truncate_plain(p["args"].replace("\n", " ").strip(), max_cmd_w)
            p_box.add_row(Txt(f" {cmd_clean}", role="schema"))
            cards.append(p_box)

        rem = len(all_procs) - len(shown)
        if rem > 0:
            step = min(8, rem)
            more_box = Box(t("system.processes_box"))
            more_box.add_row(f"         {ActionBtn(t('system.load_more', step=step, rem=rem), 'sys:load_more', tone='select', brackets=False)}")
            cards.append(more_box)
        return cards

    def on_link(self, uri: str) -> None:
        if uri == "sys:load_more":
            self.proc_limit += 8
            if self.shell:
                self.shell.refresh_view()
        elif uri.startswith("sys:term:") or uri.startswith("sys:kill:"):
            parts = uri.split(":", 2)
            signal_processes(parts[2], force_kill=(parts[1] == "kill"))
            if self.shell:
                self.shell.refresh_view()


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return SystemPanel(panel_id, shell)
