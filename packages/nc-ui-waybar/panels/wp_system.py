#!/usr/bin/env python3
import glob
import os
from core.config import THEME
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, FoldIconBtn, ProgressBar, Sparkline, StateBadge, Txt
from ui.markup import format_bytes_short
from panels.base import BasePanel
from panels.cc_engine_resources import (
    read_proc_stat,
    get_cpu_bar_pct,
    get_cpu_telemetry,
    get_ram_telemetry,
    get_gpu_bar_pct,
    get_gpu_telemetry,
)


def _pct_role(pct: float) -> str:
    if pct < 50.0:
        return "vitality"
    if pct < 75.0:
        return "attention"
    if pct < 90.0:
        return "interrupt"
    return "fault"


def _sys_temp_role(temp: float) -> str:
    if temp < 65.0:
        return "vitality"
    if temp < 80.0:
        return "attention"
    if temp < 85.0:
        return "interrupt"
    return "fault"


def _temp_bar_pct(temp: float) -> float:
    """Map thermal bands (<65 / <80 / <85 / >=85 °C) onto ProgressBar thresholds (<50 / <75 / <90 / >=90%)."""
    if temp < 65.0:
        return max(0.0, (temp / 65.0) * 49.0)
    if temp < 80.0:
        return 50.0 + ((temp - 65.0) / 15.0) * 24.0
    if temp < 85.0:
        return 75.0 + ((temp - 80.0) / 5.0) * 14.0
    return min(100.0, 90.0 + ((temp - 85.0) / 10.0) * 10.0)


def _read_sysfs_str(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _read_laptop_power() -> dict | None:
    bat_dirs = sorted(glob.glob("/sys/class/power_supply/BAT*"))
    if not bat_dirs:
        return None
    batteries = []
    for bd in bat_dirs:
        cap_s = _read_sysfs_str(os.path.join(bd, "capacity"))
        if not cap_s:
            continue
        try:
            cap = max(0, min(100, int(cap_s)))
        except ValueError:
            continue
        status = _read_sysfs_str(os.path.join(bd, "status")) or "Unknown"
        power_w = 0.0
        p_raw = _read_sysfs_str(os.path.join(bd, "power_now"))
        if p_raw.isdigit():
            power_w = int(p_raw) / 1_000_000.0
        else:
            c_raw = _read_sysfs_str(os.path.join(bd, "current_now"))
            v_raw = _read_sysfs_str(os.path.join(bd, "voltage_now"))
            if c_raw.isdigit() and v_raw.isdigit():
                power_w = (int(c_raw) * int(v_raw)) / 1_000_000_000_000.0
        health_pct = None
        for full_k, des_k in (("energy_full", "energy_full_design"), ("charge_full", "charge_full_design")):
            f_s = _read_sysfs_str(os.path.join(bd, full_k))
            d_s = _read_sysfs_str(os.path.join(bd, des_k))
            if f_s.isdigit() and d_s.isdigit() and int(d_s) > 0:
                health_pct = min(100, int(round((int(f_s) / int(d_s)) * 100.0)))
                break
        batteries.append({
            "name": os.path.basename(bd),
            "capacity": cap,
            "status": status,
            "power_w": power_w,
            "health_pct": health_pct,
        })
    if not batteries:
        return None
    ac_online = None
    for acd in sorted(glob.glob("/sys/class/power_supply/AC*") + glob.glob("/sys/class/power_supply/ADP*")):
        val = _read_sysfs_str(os.path.join(acd, "online"))
        if val in ("0", "1"):
            ac_online = (val == "1")
            break
    return {"ac_online": ac_online, "batteries": batteries}


class SystemPanel(BasePanel):
    supports_expand = False
    hover_highlight = True
    refresh_ms = 1000

    def __init__(self, panel_id: str, shell=None):
        self.id = panel_id
        self.is_cpu = (panel_id == "cpu")
        self.is_gpu = (panel_id == "gpu")
        if shell is not None:
            self.title = t("system.cpu_title") if self.is_cpu else (t("system.gpu_title") if self.is_gpu else t("system.ram_title"))
        else:
            self.title = self.id
        self.icon = "\uf2db" if self.is_cpu else ("\U000f08ae" if self.is_gpu else "\uf538")
        super().__init__(shell)
        self.prev_stat = read_proc_stat() if self.is_cpu else {}

    def get_cols(self, force_expanded: bool = False) -> int:
        return 50

    def get_refresh_ms(self) -> int:
        val = super().get_refresh_ms()
        return min(val, 1000) if val > 0 else 1000

    def get_bar_pill(self) -> dict:
        font = THEME["typography"]["font_family"]
        glow_on = bool(STATE.get("waybar", "sys_glow_enabled", True))
        thresh = max(50, min(95, int(STATE.get("waybar", "sys_glow_threshold", 80) or 80)))
        if self.is_cpu:
            pct = float(get_cpu_bar_pct())
            text = f"{Txt('\uf2db', role='focus')}  <span font_family='{font}'>{Txt(f'{pct:2.0f}%', role='focus')}</span>"
        elif self.is_gpu:
            avail, util_pct, vram_pct = get_gpu_bar_pct()
            if not avail:
                return {"text": "", "tooltip": "", "class": "module-nc-empty"}
            pct = max(util_pct, vram_pct)
            text = f"{Txt('\U000f08ae', role='focus')}  <span font_family='{font}'>{Txt(f'{util_pct:2.0f}%', role='focus')}</span>"
        else:
            rm = get_ram_telemetry()
            pct = float(rm["pct"])
            pct_s = f"{pct:2.0f}%"
            text = f"{Txt('\uf538', role='focus')}  <span font_family='{font}'>{Txt(pct_s, role='focus')}</span>"
        css_cls = ["module-nc", "nc-glow-crit"] if (glow_on and pct >= thresh) else "module-nc"
        return {"text": text, "tooltip": "", "class": css_cls}

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        target_cols = 50
        canvas.set_cols(target_cols)

        if self.is_cpu:
            tele = get_cpu_telemetry(self.prev_stat)
            self.prev_stat = tele["stat"]
            pct = tele["total_pct"]
            l1, l5, l15 = tele["loads"]
            temp = tele["temp_c"]
            temp_role = _sys_temp_role(temp)
            gov = tele.get("governor", "")
            hdr_acts = [StateBadge(True, gov.upper(), on_tone="ok")] if gov else []
            hero = Box(
                canvas.truncate_plain(tele["model"], 22),
                icon="\uf2db",
                actions=hdr_acts,
                highlighted=False,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            hero.add_row(" ")
            hero.add_kv(
                t("system.cpu_load_kv"),
                f"{ProgressBar(pct, width=16)} {Txt(f'{pct:5.1f}%', role=_pct_role(pct))}",
            )
            max_ghz = tele["max_ghz"]
            avg_ghz = tele["avg_ghz"]
            hero.add_kv(
                t("system.clock_gov_kv"),
                f"{Txt(f'{max_ghz:4.2f} GHz', role='val')} {Txt(t('system.peak_tag'), role='schema')} "
                f"{Txt('·', role='structure')} {Txt(f'{avg_ghz:4.2f} GHz', role='val')}",
            )
            if temp > 0.0:
                hero.add_kv(
                    t("system.thermal_kv"),
                    f"{ProgressBar(_temp_bar_pct(temp), width=16)} "
                    f"{Txt(f'{temp:4.1f} °C', role=temp_role)}",
                )
            else:
                hero.add_kv(t("system.thermal_kv"), Txt(t("common.nd"), role="inactive"))
            hero.add_kv(
                t("system.load_avg_kv"),
                f"{Txt(f'{l1:.2f}', role='val')} {Txt('1m', role='schema')} "
                f"{Txt('·', role='structure')} {Txt(f'{l5:.2f}', role='val')} {Txt('5m', role='schema')} "
                f"{Txt('·', role='structure')} {Txt(f'{l15:.2f}', role='val')} {Txt('15m', role='schema')}",
            )

            show_cores = bool(self.get_state("show_cores", False))
            toggle_c_btn = FoldIconBtn(
                show_cores,
                f"ui:state:show_cores:{'false' if show_cores else 'true'}",
            )
            hero.add_section(t("system.core_monitor"), actions=[toggle_c_btn])
            cores = tele["cores_pct"]
            if not show_cores:
                hero.add_row(f"  {Sparkline(cores, max_items=32)}  {Txt(t('system.threads_tag', n=len(cores)), role='schema')}")
            else:
                spark_chars = THEME["ascii"]["sparkline_chars"]
                core_rows = []
                for r_idx in range(0, len(cores), 4):
                    chunk = cores[r_idx : r_idx + 4]
                    cells = []
                    for c_off, cp in enumerate(chunk):
                        cid = r_idx + c_off
                        s_idx = min(len(spark_chars) - 1, int((cp / 100.0) * len(spark_chars)))
                        cells.append(f"{Txt(f'{cid:02d}', role='schema')} {Txt(f'{spark_chars[s_idx]}{int(round(cp)):3d}%', role=_pct_role(cp))}")
                    while len(cells) < 4:
                        cells.append("")
                    core_rows.append(cells)
                if core_rows:
                    hero.add_table(
                        headers=["C0", "C1", "C2", "C3"],
                        widths=[10, 10, 10, 10],
                        rows=core_rows,
                        aligns=["left", "left", "left", "left"],
                        show_headers=False,
                    )
        elif self.is_gpu:
            show_procs = bool(self.get_state("show_gpu_procs", False)) or force_expanded
            tele = get_gpu_telemetry(include_procs=show_procs)
            if not tele["available"]:
                hero = Box(
                    t("system.gpu_title"),
                    icon="\U000f08ae",
                    highlighted=False,
                    min_cols=target_cols,
                    max_cols=target_cols,
                )
                hero.add_row(" ")
                hero.add_row(f"  {Txt(t('system.gpu_unavailable'), role='inactive')}")
                hero.add_row(" ")
            else:
                pstate = tele.get("pstate", "")
                hdr_acts = [StateBadge(True, pstate.upper(), on_tone="ok")] if pstate else []
                hero = Box(
                    canvas.truncate_plain(tele["model"], 24),
                    icon="\U000f08ae",
                    actions=hdr_acts,
                    highlighted=False,
                    min_cols=target_cols,
                    max_cols=target_cols,
                )
                hero.add_row(" ")
                util_pct = tele["util_pct"]
                hero.add_kv(
                    t("system.gpu_load_kv"),
                    f"{ProgressBar(util_pct, width=14)} {Txt(f'{util_pct:5.1f}%', role=_pct_role(util_pct))}",
                )
                if tele.get("vram_total", 0) > 0:
                    vram_u_g = tele["vram_used"] / (1024.0 ** 3)
                    vram_t_g = tele["vram_total"] / (1024.0 ** 3)
                    vram_pct = tele["vram_pct"]
                    hero.add_kv(
                        t("system.gpu_vram_kv"),
                        f"{ProgressBar(vram_pct, width=12)} {Txt(f'{vram_u_g:4.1f}/{vram_t_g:.0f}G', role='val')} "
                        f"{Txt(f'{vram_pct:4.0f}%', role=_pct_role(vram_pct))}",
                    )
                else:
                    hero.add_kv(t("system.gpu_vram_kv"), Txt(t("system.gpu_shared_uma"), role="schema"))
                temp = tele["temp_c"]
                if temp > 0.0:
                    temp_role = _sys_temp_role(temp)
                    hero.add_kv(
                        t("system.thermal_kv"),
                        f"{ProgressBar(_temp_bar_pct(temp), width=14)} "
                        f"{Txt(f'{temp:4.1f} °C', role=temp_role)}",
                    )
                else:
                    hero.add_kv(t("system.thermal_kv"), Txt(t("common.nd"), role="inactive"))
                if tele["power_w"] > 0.0 or tele["power_limit_w"] > 0.0:
                    pwr_str = (
                        f"{tele['power_w']:.1f} / {tele['power_limit_w']:.0f} W"
                        if tele["power_limit_w"] > 0
                        else f"{tele['power_w']:.1f} W"
                    )
                    pwr_val = Txt(pwr_str, role="val")
                elif tele["fan_pct"] is not None:
                    pwr_val = ""
                else:
                    pwr_val = Txt(t("common.nd"), role="inactive")
                if tele["fan_pct"] is not None:
                    fan_p = tele["fan_pct"]
                    fan_part = (
                        f"{Txt(t('system.gpu_fan_lbl'), role='schema')} "
                        f"{Txt(f'{fan_p}%', role='val')}"
                    )
                    pwr_val = f"{pwr_val} {Txt('·', role='structure')} {fan_part}" if pwr_val else fan_part
                hero.add_kv(t("system.gpu_power_kv"), pwr_val)
                gfx_mhz = tele["gfx_mhz"]
                mem_mhz = tele["mem_mhz"]
                clk_parts = []
                if gfx_mhz > 0:
                    clk_parts.append(f"{Txt(f'{gfx_mhz} MHz', role='val')} {Txt(t('system.gpu_gfx_tag'), role='schema')}")
                if mem_mhz > 0:
                    clk_parts.append(f"{Txt(f'{mem_mhz} MHz', role='val')} {Txt(t('system.gpu_mem_tag'), role='schema')}")
                if clk_parts:
                    hero.add_kv(t("system.gpu_clocks_kv"), f" {Txt('·', role='structure')} ".join(clk_parts))
                else:
                    hero.add_kv(t("system.gpu_clocks_kv"), Txt(t("common.nd"), role="inactive"))

                toggle_p_btn = FoldIconBtn(
                    show_procs,
                    f"ui:state:show_gpu_procs:{'false' if show_procs else 'true'}",
                )
                procs = tele.get("procs", [])
                sec_title = f"{t('system.gpu_processes_sec')} ({len(procs)})" if show_procs else t("system.gpu_processes_sec")
                hero.add_section(sec_title, actions=[toggle_p_btn])
                if show_procs:
                    if procs:
                        p_rows = [
                            [Txt(canvas.truncate_plain(p["name"], 24), role="focus"), Txt(f"{p['vram_mib']} MiB", role="val")]
                            for p in procs[:8]
                        ]
                        hero.add_table(
                            headers=[t("system.gpu_proc_col"), t("system.gpu_vram_col")],
                            widths=["flex", 10],
                            rows=p_rows,
                            aligns=["left", "right"],
                        )
                    else:
                        hero.add_row(f"  {Txt(t('system.gpu_no_procs'), role='inactive')}")
        else:
            rm = get_ram_telemetry()
            used_g = rm["used"] / (1024.0 ** 3)
            tot_g = rm["total"] / (1024.0 ** 3)
            sw_u_g = rm["sw_used"] / (1024.0 ** 3)
            sw_t_g = rm["sw_total"] / (1024.0 ** 3)
            hero = Box(
                t("system.memory_box", tot=f"{tot_g:.0f}"),
                icon="\uf538",
                actions=[ActionBtn(t("system.sync_disks"), "ui:cmd:sync", tone="apply")],
                highlighted=False,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            hero.add_row(" ")
            ram_pct = rm["pct"]
            sw_pct = rm["sw_pct"]
            avail_role = _pct_role(ram_pct)
            hero.add_kv(
                t("system.ram_used_kv"),
                f"{ProgressBar(ram_pct, width=12)} {Txt(f'{used_g:4.1f}/{tot_g:.0f}G', role='val')} "
                f"{Txt(f'{ram_pct:4.0f}%', role=_pct_role(ram_pct))}",
            )
            hero.add_kv(
                t("system.ram_avail_kv"),
                f"{Txt(format_bytes_short(rm['avail']), role=avail_role)} "
                f"{Txt('·', role='structure')} {Txt(t('system.cache_label'), role='schema')} {Txt(format_bytes_short(rm['cache']), role='val')}",
            )
            if rm["sw_total"] > 0:
                hero.add_kv(
                    t("system.swap_zram_kv"),
                    f"{ProgressBar(sw_pct, width=12)} {Txt(f'{sw_u_g:4.1f}/{sw_t_g:.0f}G', role='val')} "
                    f"{Txt(f'{sw_pct:4.0f}%', role=_pct_role(sw_pct))}",
                )
            else:
                hero.add_kv(t("system.swap_zram_kv"), Txt(t("common.none"), role="inactive"))
            hero.add_kv(
                t("system.dirty_buf_kv"),
                f"{Txt(t('system.dirty_label'), role='schema')} "
                f"{Txt(format_bytes_short(rm['dirty']), role='attention' if rm['dirty'] > 16 * 1024 * 1024 else 'val')} "
                f"{Txt('·', role='structure')} {Txt(t('system.shared_label'), role='schema')} {Txt(format_bytes_short(rm['shared']), role='val')}",
            )

        hero.set_bottom_bar(
            t("system.process_monitor_sec"),
            actions=[ActionBtn("\uf013", f"ui:open_settings:power:{'gpu' if self.is_gpu else 'cpu_ram'}", tone="neutral")],
        )
        cards = [hero]
        if self.is_cpu:
            lp = _read_laptop_power()
            if lp is not None:
                ac_acts = []
                if lp["ac_online"] is not None:
                    ac_acts.append(
                        StateBadge(True, t("system.ac_online"), on_tone="ok")
                        if lp["ac_online"]
                        else Badge(t("system.on_battery"), style="warn")
                    )
                bat_box = Box(
                    t("system.battery_box"),
                    icon="\uf240",
                    actions=ac_acts,
                    highlighted=False,
                    min_cols=target_cols,
                    max_cols=target_cols,
                )
                bat_box.add_row(" ")
                for bat in lp["batteries"]:
                    cap = bat["capacity"]
                    cap_role = "vitality" if cap >= 50 else ("attention" if cap >= 20 else "fault")
                    st_l = bat["status"].strip().lower()
                    if st_l == "charging":
                        st_lbl = t("system.bat_charging")
                    elif st_l == "discharging":
                        st_lbl = t("system.bat_discharging")
                    elif st_l == "not charging":
                        st_lbl = t("system.bat_not_charging")
                    elif st_l == "full":
                        st_lbl = t("system.bat_full")
                    else:
                        st_lbl = t("common.nd")
                    pwr_part = f" · {bat['power_w']:.1f} W" if bat["power_w"] > 0 else ""
                    bat_box.add_kv(
                        bat["name"],
                        f"{ProgressBar(cap, width=12, invert_threshold=True)} {Txt(f'{cap}%', role=cap_role)} "
                        f"{Txt(st_lbl + pwr_part, role='schema')}",
                    )
                    if bat["health_pct"] is not None:
                        bat_box.add_kv(t("system.bat_health_kv"), Txt(f"{bat['health_pct']}%", role="val"))
                bat_box.add_row(" ")
                cards.append(bat_box)
        return cards


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return SystemPanel(panel_id, shell)
