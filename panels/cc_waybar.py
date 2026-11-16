#!/usr/bin/env python3
import json
import os
import re
import subprocess
from core.config import (
    CONFIG,
    ROOT_DIR,
    get_network_show_bar_speed,
    set_network_show_bar_speed,
    set_waybar_width_pct,
    estimate_waybar_free_chars_budget,
    get_screen_lock_cmd,
    set_screen_lock_cmd,
    apply_waybar_transparency,
    _update_toml_key_in_section,
    _unique_existing_paths,
)
from core.state import STATE
from core.i18n import get_active_language
from ui.canvas import Canvas
from ui.kit import Box, Badge, Slider, ProgressBar, Divider, Txt, SelectPill, ActionBtn, StateBadge
from panels.cc_network import get_interfaces_fast

USER_WB_CONF = os.path.expanduser("~/.config/waybar/config.jsonc")
REPO_WB_CONF = os.path.join(ROOT_DIR, "config", "waybar", "config.jsonc")

KNOWN_WAYBAR_WIDGETS = {
    "niri/workspaces":      {"label": "Niri Workspaces",       "icon": "\uf111", "desc": "Active workspace indicator dots"},
    "niri/window":          {"label": "Active Window Title",   "icon": "\uf2d0", "desc": "Focused window title (invisible background)"},
    "custom/media":         {"label": "Now Playing Music",     "icon": "\uf001", "desc": "MPRIS music metadata pipe (Spotify / YouTube Music)"},
    "custom/settings":      {"label": "Control Center Gear",   "icon": "\uf013", "desc": "Opens the NC-UI 960px side Control Center"},
    "custom/calendar":      {"label": "Clock & Calendar",      "icon": "\uf073", "desc": "Live time, date & SQLite/Google calendar popup"},
    "custom/desktop":       {"label": "Display & Wallpapers",  "icon": "\uf108", "desc": "Monitor map, wallpapers & CMatrix shader"},
    "custom/cliphist":      {"label": "Clipboard Manager",     "icon": "\uf0ea", "desc": "Clipboard history, pinned items & image toggle"},
    "custom/screenshot":    {"label": "Capture Studio",        "icon": "\uf030", "desc": "Area/Window/Screen capture with Satty"},
    "custom/audio":         {"label": "Audio & Mixer",         "icon": "\uf028", "desc": "Dynamic volume icon, sinks, sources & app mixer"},
    "custom/netcycle":      {"label": "Network & Bluetooth",   "icon": "\uf6ff", "desc": "LAN, Wi-Fi, Bluetooth & VPN status"},
    "custom/cpu":           {"label": "CPU Telemetry",         "icon": "\uf2db", "desc": "CPU load percentage, temperature & top processes"},
    "custom/ram":           {"label": "RAM Telemetry",         "icon": "\uf538", "desc": "Memory allocation, cache, swap & top processes"},
    "custom/notifications": {"label": "Notification Center",   "icon": "\uf0f3", "desc": "Notification history & glowing alert bell"},
    "custom/session":       {"label": "Power & Screen Lock",   "icon": "\uf011", "desc": "Lock screen, suspend, logout, reboot & poweroff menu"},
}


def _strip_jsonc_comments(text: str) -> str:
    pattern = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*[\s\S]*?\*/')
    return pattern.sub(lambda m: m.group(1) if m.group(1) else "", text)


def _load_wb_jsonc(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cleaned = _strip_jsonc_comments(f.read())
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _backfill_wb_modules(data: dict) -> dict:
    base = _load_wb_jsonc(REPO_WB_CONF)
    for k, v in base.items():
        if isinstance(v, dict) and (k.startswith("custom/") or k.startswith("niri/")):
            if not isinstance(data.get(k), dict):
                data[k] = dict(v)
            else:
                for sub_k, sub_v in v.items():
                    if sub_k != "max-length":
                        data[k][sub_k] = sub_v
    return data


def _read_wb_config() -> dict:
    data = _load_wb_jsonc(USER_WB_CONF) or _load_wb_jsonc(REPO_WB_CONF)
    if not data:
        data = {"modules-left": [], "modules-center": [], "modules-right": []}
    return _backfill_wb_modules(data)


def _write_wb_config(data: dict, reload_bar: bool = False) -> None:
    _backfill_wb_modules(data)
    budget = estimate_waybar_free_chars_budget()
    data.setdefault("niri/window", {})["max-length"] = int(budget["window_chars"])
    for k, v in data.items():
        if isinstance(v, dict) and (k.startswith("custom/") or k.startswith("niri/")):
            v["tooltip"] = False

    paths = _unique_existing_paths(
        REPO_WB_CONF,
        os.path.expanduser("~/Projects/ncui/config/waybar/config.jsonc"),
    )
    user_rp = os.path.realpath(USER_WB_CONF) if os.path.exists(USER_WB_CONF) else USER_WB_CONF
    if user_rp not in paths:
        paths.insert(0, user_rp)

    for rp in paths:
        try:
            os.makedirs(os.path.dirname(rp), exist_ok=True)
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass

    if reload_bar:
        apply_waybar_transparency()


def _build_waybar_options_tab(cc_panel, canvas: Canvas) -> list:
    is_it = (get_active_language() == "it")

    cards = []

    # ── CARD 1: SCREEN LOCK & SESSION WIDGET OPTIONS (custom/session) ──
    lock_cmd = get_screen_lock_cmd()
    lock_badge = StateBadge(True, "CONFIGURED", on_tone="ok") if lock_cmd else StateBadge(True, "NOT SET", on_tone="warn")
    lock_box = Box(
        "Opzioni Blocco Schermo & Sessione (custom/session)" if is_it else "Screen Lock & Session Widget (custom/session)",
        icon="\uf023",
        actions=[
            lock_badge,
            ActionBtn(
                "\uf044 Imposta Comando.." if is_it else "\uf044 Set Lock Command..",
                "cc_wb:prompt_lock_cmd",
                tone="select",
                active=True,
            ),
        ],
        elected=bool(lock_cmd),
    )
    lock_box.add_row(" ")
    lock_disp = (
        Txt(lock_cmd, role="focus")
        if lock_cmd
        else Txt("Non configurato (scegli un preset o inserisci un comando)" if is_it else "Not configured (select a preset or enter a custom command)", role="attention")
    )
    lock_box.add_kv(
        "Comando Lock" if is_it else "Lock Command",
        lock_disp,
        right_markup=ActionBtn("Clear", "cc_wb:set_lock:", tone="danger_soft") if lock_cmd else "",
    )
    presets = [
        ("swaylock", "swaylock -f -c 000000"),
        ("hyprlock", "hyprlock"),
        ("gtklock", "gtklock"),
        ("waylock", "waylock"),
        ("loginctl", "loginctl lock-session"),
    ]
    preset_btns = "  ".join(
        SelectPill(lbl, f"cc_wb:set_lock:{cmd_v}", selected=(lock_cmd == cmd_v))
        for lbl, cmd_v in presets
    )
    lock_box.add_kv("Preset Rapidi" if is_it else "Quick Presets", preset_btns)
    lock_box.add_row(" ")
    cards.append(lock_box)

    # ── CARD 2: NETWORK WIDGET OPTIONS (custom/netcycle) ──
    show_spd = get_network_show_bar_speed()
    unit_mode = str(STATE.get("network", "speed_unit", CONFIG.get("network", {}).get("default_speed_unit", "bytes")))
    mon_iface = str(STATE.get("network", "monitored_iface", CONFIG.get("network", {}).get("monitored_interfaces", "auto")))
    ifaces = [i["name"] for i in get_interfaces_fast()]

    net_badge = StateBadge(show_spd, "SPEED: ON", "ICONS ONLY", on_tone="ok")
    net_box = Box(
        "Opzioni Widget Rete sulla Barra (custom/netcycle)" if is_it else "Network Pill Widget Options (custom/netcycle)",
        icon="\uf6ff",
        actions=[net_badge],
        elected=show_spd,
    )
    net_box.add_row(" ")
    spd_on_btn = SelectPill("ON (Show Speed)", "cc_wb:net_spd:on", selected=show_spd)
    spd_off_btn = SelectPill("OFF (Icons Only)", "cc_wb:net_spd:off", selected=not show_spd)
    net_box.add_kv(
        "Traffic Speed" if not is_it else "Velocit\u00e0 Traffico",
        f"{spd_on_btn}   {spd_off_btn}",
    )

    u_bytes_btn = SelectPill("Bytes (kB/s, MB/s)", "cc_wb:net_unit:bytes", selected=(unit_mode == "bytes"))
    u_bits_btn = SelectPill("Bits (kb/s, Mb/s)", "cc_wb:net_unit:bits", selected=(unit_mode == "bits"))
    net_box.add_kv(
        "Speed Unit" if not is_it else "Unit\u00e0 di Misura",
        f"{u_bytes_btn}   {u_bits_btn}",
    )

    if_candidates = ["auto", "ALL"] + ifaces[:6]
    if_btns = "  ".join(
        SelectPill(ic_name, f"cc_wb:net_if:{ic_name}", selected=(mon_iface == ic_name))
        for ic_name in if_candidates
    )
    net_box.add_kv("Monitored Dev" if not is_it else "Scheda Monitorata", if_btns)
    net_box.add_row(" ")
    cards.append(net_box)

    # ── CARD 3: LUNGHEZZA PERCENTUALE BARRA DEL TITOLO E SPOTIFY/MUSICA ──
    budget = estimate_waybar_free_chars_budget()
    win_pct = budget["window_pct"]
    med_pct = budget["media_pct"]
    win_chars = budget["window_chars"]
    med_chars = budget["media_chars"]
    tot_chars = budget["total_free_chars"]
    mon_w = budget["mon_w"]
    has_win = budget["has_window"]
    has_med = budget["has_media"]

    apply_btn = ActionBtn(
        "\uf00c Apply & Reload" if not is_it else "\uf00c Applica & Ricarica",
        "cc_wb:apply",
        tone="apply",
        active=True,
    )
    len_box = Box(
        "Lunghezza Barre Testuali (% Spazio Libero)" if is_it else "Elastic Text Bars Width (% Free Bar Space)",
        icon="\uf07e",
        actions=[Badge(f"{mon_w}px (~{tot_chars} chars)", style="info"), apply_btn],
        elected=True,
    )

    win_status = "ON BAR" if has_win else "DISABLED"
    len_box.add_section(
        f"\uf2d0 Barra del Titolo (niri/window) [{win_status}]" if is_it else f"\uf2d0 Window Title Bar (niri/window) [{win_status}]",
        actions=[Badge(f"{win_pct}% \u2248 {win_chars} chars", style="accent" if has_win else "secondary")],
    )
    win_slider = Slider(
        win_pct,
        "cc_wb:win_pct",
        min_val=5,
        max_val=100,
        width=28,
        show_pct=True,
        step_buttons=True,
        step=5,
    )
    len_box.add_kv("Title Width %" if not is_it else "Larghezza Titolo %", win_slider)
    win_presets = "  ".join(
        SelectPill(f"{p}%", f"cc_wb:win_pct:{p}", selected=(abs(win_pct - p) <= 2))
        for p in (15, 25, 35, 45, 60, 75, 90)
    )
    len_box.add_kv("Quick Presets" if not is_it else "Preset Rapidi", win_presets)

    med_status = "ON BAR" if has_med else "DISABLED"
    len_box.add_section(
        f"\uf001 Barra Musica / Spotify (custom/media) [{med_status}]" if is_it else f"\uf001 Spotify / Music Bar (custom/media) [{med_status}]",
        actions=[Badge(f"{med_pct}% \u2248 {med_chars} chars", style="accent" if has_med else "secondary")],
    )
    med_slider = Slider(
        med_pct,
        "cc_wb:med_pct",
        min_val=5,
        max_val=100,
        width=28,
        show_pct=True,
        step_buttons=True,
        step=5,
    )
    len_box.add_kv("Music Width %" if not is_it else "Larghezza Musica %", med_slider)
    med_presets = "  ".join(
        SelectPill(f"{p}%", f"cc_wb:med_pct:{p}", selected=(abs(med_pct - p) <= 2))
        for p in (15, 25, 35, 45, 60, 75, 90)
    )
    len_box.add_kv("Quick Presets" if not is_it else "Preset Rapidi", med_presets)

    used_pct = min(100, (win_pct if has_win else 0) + (med_pct if has_med else 0))
    free_margin_pct = max(0, 100 - used_pct)
    len_box.add_section("Allocazione Spazio Disponibile" if is_it else "Available Space Allocation")
    len_box.add_kv(
        "Total Allocated" if not is_it else "Totale Allocato",
        f"{ProgressBar(used_pct, width=24, color_by_threshold=False)}   "
        f"{Txt(f'{used_pct}% used', role='intent')} {Txt(f'({free_margin_pct}% margin)', role='schema')}",
    )
    hint = (
        "  La barra Musica si aggiorna istantaneamente. Premi [Applica & Ricarica] per aggiornare la barra del Titolo."
        if is_it
        else "  Music Bar updates live immediately. Click [Apply & Reload] to update Window Title max-length."
    )
    len_box.add_row(Txt(hint, role="schema"))
    cards.append(len_box)

    return cards


def build_cc_waybar_cards(cc_panel, canvas: Canvas, active_sub: str = "layout") -> list:
    if active_sub == "options":
        return _build_waybar_options_tab(cc_panel, canvas)

    is_it = (get_active_language() == "it")

    data = _read_wb_config()
    left_mods = list(data.get("modules-left", []) or [])
    center_mods = list(data.get("modules-center", []) or [])
    right_mods = list(data.get("modules-right", []) or [])
    active_all = set(left_mods + center_mods + right_mods)

    cards = []

    hdr_box = Box(
        "Gestione Layout & Widget Waybar" if is_it else "Waybar Layout & Widgets Manager",
        icon="\uf0db",
        actions=[
            ActionBtn(
                "\uf00c Applica & Ricarica" if is_it else "\uf00c Apply & Reload",
                "cc_wb:apply",
                tone="apply",
                active=True,
            )
        ],
        elected=True,
    )
    hdr_box.add_row(
        f"  {Txt('Ordina e sposta i widget tra Sinistra, Centro e Destra, poi clicca [Applica & Ricarica].' if is_it else 'Order and move modules across Left, Center and Right sections, then click [Apply & Reload].', role='schema')}"
    )
    cards.append(hdr_box)

    sections_spec = [
        ("left",   "Sezione Sinistra (modules-left)" if is_it else "Left Section (modules-left)",   left_mods),
        ("center", "Sezione Centrale (modules-center)" if is_it else "Center Section (modules-center)", center_mods),
        ("right",  "Sezione Destra (modules-right)" if is_it else "Right Section (modules-right)",  right_mods),
    ]

    for sec_key, sec_title, mod_list in sections_spec:
        sec_box = Box(
            sec_title,
            icon="\uf0c9",
            actions=[Badge(f"{len(mod_list)} widgets", style="info")],
        )
        if not mod_list:
            sec_box.add_row(f"  {Txt('(Sezione vuota)' if is_it else '(Empty section)', role='inactive')}")
        else:
            for idx, m_id in enumerate(mod_list):
                meta = KNOWN_WAYBAR_WIDGETS.get(m_id, {"label": m_id, "icon": "\uf12e", "desc": ""})
                up_b = ActionBtn("\u2191", f"cc_wb:up:{sec_key}:{idx}", tone="neutral") if idx > 0 else Txt("[\u2191]", role="structure")
                dn_b = ActionBtn("\u2193", f"cc_wb:down:{sec_key}:{idx}", tone="neutral") if idx < len(mod_list) - 1 else Txt("[\u2193]", role="structure")
                to_l = SelectPill("L", f"cc_wb:move:{m_id}:left", selected=(sec_key == "left"))
                to_c = SelectPill("C", f"cc_wb:move:{m_id}:center", selected=(sec_key == "center"))
                to_r = SelectPill("R", f"cc_wb:move:{m_id}:right", selected=(sec_key == "right"))
                rm_b = ActionBtn("x", f"cc_wb:remove:{m_id}", tone="danger_soft")

                m_title = f"{meta['icon']}  {meta['label']}"
                left_str = (
                    f"  {Txt(f'{idx + 1:02d}.', role='intent')}  "
                    f"{Txt(m_title, role='focus')}  "
                    f"{Txt(f'({m_id})', role='schema')}"
                )
                right_str = f"{up_b} {dn_b}   {to_l} {to_c} {to_r}   {rm_b}"
                sec_box.add_row(left_str, right_markup=right_str)
        cards.append(sec_box)

    all_known_keys = list(KNOWN_WAYBAR_WIDGETS.keys())
    for k, v in data.items():
        if isinstance(v, dict) and (k.startswith("custom/") or k.startswith("niri/")) and k not in all_known_keys:
            all_known_keys.append(k)

    disabled_mods = [m for m in all_known_keys if m not in active_all]
    cards.append(Divider("Widget Disponibili" if is_it else "Available Widgets Pool", icon="\uf067"))
    pool_box = Box(
        "Widget Disponibili (Non sulla barra)" if is_it else "Available / Disabled Widgets Pool",
        icon="\uf067",
        actions=[Badge(f"{len(disabled_mods)} available", style="accent")],
    )
    if not disabled_mods:
        pool_box.add_row(f"  {Txt('Tutti i widget disponibili sono posizionati sulla barra.' if is_it else 'All available widgets are currently placed on the bar.', role='inactive')}")
    else:
        for m_id in disabled_mods:
            meta = KNOWN_WAYBAR_WIDGETS.get(m_id, {"label": m_id, "icon": "\uf12e", "desc": ""})
            add_l = ActionBtn("+ Left", f"cc_wb:add:{m_id}:left", tone="apply")
            add_c = ActionBtn("+ Center", f"cc_wb:add:{m_id}:center", tone="apply")
            add_r = ActionBtn("+ Right", f"cc_wb:add:{m_id}:right", tone="apply")
            m_title = f"{meta['icon']}  {meta['label']}"
            pool_box.add_row(
                f"  {Txt(m_title, role='focus')}  "
                f"{Txt(meta.get('desc', m_id), role='schema')}",
                right_markup=f"{add_l}  {add_c}  {add_r}",
            )
    cards.append(pool_box)

    return cards


def handle_cc_waybar_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_wb:"):
        return False
    act = uri[len("cc_wb:"):]
    is_it = (get_active_language() == "it")
    data = _read_wb_config()
    sec_map = {
        "left": list(data.get("modules-left", []) or []),
        "center": list(data.get("modules-center", []) or []),
        "right": list(data.get("modules-right", []) or []),
    }

    if act == "apply":
        if cc_panel.shell:
            cc_panel.shell.bump_leave_guard(1.5)
        _write_wb_config(data, reload_bar=True)
        return True

    if act == "prompt_lock_cmd":
        cur = get_screen_lock_cmd()
        ph = "Comando blocco schermo (es. swaylock -f -c 000000 o hyprlock):" if is_it else "Screen lock command (e.g. swaylock -f -c 000000 or hyprlock):"
        cc_panel.request_prompt("cc_wb_lock_cmd", ph, cur, title="Screen Lock Command")
        return True

    if act.startswith("set_lock:"):
        cmd_val = act.split(":", 1)[1]
        set_screen_lock_cmd(cmd_val)
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("net_spd:"):
        val = (act.split(":", 1)[1] == "on")
        set_network_show_bar_speed(val)
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("net_unit:"):
        u_val = act.split(":", 1)[1]
        if u_val in ("bytes", "bits"):
            STATE.set("network", "speed_unit", u_val)
            CONFIG.setdefault("network", {})["default_speed_unit"] = u_val
            _update_toml_key_in_section("network", "default_speed_unit", f'"{u_val}"')
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("net_if:"):
        if_val = act.split(":", 1)[1]
        STATE.set("network", "monitored_iface", if_val)
        CONFIG.setdefault("network", {})["monitored_interfaces"] = if_val
        _update_toml_key_in_section("network", "monitored_interfaces", f'"{if_val}"')
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("win_pct:") or act.startswith("med_pct:"):
        kind, val_s = act.split(":", 1)
        target = "window" if kind == "win_pct" else "media"
        active_all = set(sec_map["left"] + sec_map["center"] + sec_map["right"])
        both_active = ("niri/window" in active_all and "custom/media" in active_all)
        try:
            set_waybar_width_pct(target, int(round(float(val_s))), both_active_on_bar=both_active)
            _write_wb_config(data, reload_bar=False)
        except Exception:
            pass
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("up:") or act.startswith("down:"):
        direction, sec_k, idx_s = act.split(":", 2)
        idx = int(idx_s)
        lst = sec_map.get(sec_k, [])
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= idx < len(lst) and 0 <= swap_idx < len(lst):
            lst[idx], lst[swap_idx] = lst[swap_idx], lst[idx]

    elif act.startswith("move:") or act.startswith("add:"):
        _, mod_id, dest_sec = act.split(":", 2)
        for k in ("left", "center", "right"):
            if mod_id in sec_map[k]:
                sec_map[k].remove(mod_id)
        if dest_sec in sec_map:
            sec_map[dest_sec].append(mod_id)

    elif act.startswith("remove:"):
        mod_id = act.split(":", 1)[1]
        for k in ("left", "center", "right"):
            if mod_id in sec_map[k]:
                sec_map[k].remove(mod_id)

    data["modules-left"] = sec_map["left"]
    data["modules-center"] = sec_map["center"]
    data["modules-right"] = sec_map["right"]
    _write_wb_config(data, reload_bar=False)
    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_waybar_prompt(cc_panel, action_id: str, text: str) -> bool:
    if action_id == "cc_wb_lock_cmd":
        set_screen_lock_cmd(text)
        return True
    return False
