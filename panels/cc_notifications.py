#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — Notifications (`panels/cc_notifications.py`) [Single Backend / Dual View]"""

import json
import os
import re
from core import probe
from core.config import get_cc_panel_style
from core.i18n import get_active_language
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt

HIST_FILE = os.path.expanduser("~/.cache/nc_notifications.json")
ADDONS_CONF = os.path.expanduser("~/.config/nc-ui/nc-addons.conf")
RULES_FILE = os.path.expanduser("~/.config/nc-ui/notifications_rules.json")
_NOTIFY_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "nc_util_notify.sh")

_MSG = ""
_ERR = ""
_COMMON_APPS = ["discord", "slack", "firefox", "thunderbird", "spotify", "chromium", "steam"]


# =====================================================================
# POPUP COMPATIBILITY EXPORTS (used by panels/notifications.py)
# =====================================================================

def load_notifs() -> list[dict]:
    if not os.path.exists(HIST_FILE):
        return []
    try:
        with open(HIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_notifs(notifs: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(HIST_FILE), exist_ok=True)
        with open(HIST_FILE, "w", encoding="utf-8") as f:
            json.dump(notifs, f)
    except Exception:
        pass


def get_active_notifs() -> list[dict]:
    return [n for n in load_notifs() if not n.get("deleted")]


def clear_all_notifs() -> None:
    notifs = load_notifs()
    for n in notifs:
        n["deleted"] = True
    save_notifs(notifs)


def delete_notif(target_id: str) -> None:
    notifs = load_notifs()
    for n in notifs:
        if str(n.get("id")) == str(target_id):
            n["deleted"] = True
    save_notifs(notifs)


def clear_app_notifs(app_name: str) -> None:
    notifs = load_notifs()
    tgt = str(app_name or "").strip().lower()
    for n in notifs:
        a = str(n.get("app_name") or n.get("app") or "").strip().lower()
        if a == tgt:
            n["deleted"] = True
    save_notifs(notifs)


# =====================================================================
# BACKEND CONFIG / RULES HELPERS
# =====================================================================

def _load_rules() -> dict:
    base = {
        "dnd": False,
        "quiet_enabled": False,
        "quiet_window": "23:00-07:00",
        "min_urgency": "low",
        "muted_apps": [],
        "group_by_app": False,
    }
    if os.path.exists(RULES_FILE):
        try:
            data = json.load(open(RULES_FILE, "r", encoding="utf-8"))
            if isinstance(data, dict):
                base.update(data)
        except Exception:
            pass
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS") and probe.has_bin("swaync-client"):
        ok, out, _ = probe.run_cmd_full(["swaync-client", "-D"], timeout=0.8)
        if ok and out.strip().lower() in ("true", "false"):
            base["dnd"] = (out.strip().lower() == "true")
    return base


def _save_rules(rules: dict) -> None:
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2)


def _get_max_notifications() -> int:
    if os.path.exists(ADDONS_CONF):
        try:
            for ln in open(ADDONS_CONF, "r", encoding="utf-8", errors="ignore"):
                m = re.match(r"^\s*max_notifications\s*=\s*(\d+)", ln)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
    return 50


def _set_max_notifications(val: int) -> None:
    os.makedirs(os.path.dirname(ADDONS_CONF), exist_ok=True)
    lines = []
    found = False
    if os.path.exists(ADDONS_CONF):
        lines = open(ADDONS_CONF, "r", encoding="utf-8", errors="ignore").splitlines()
    out = []
    for ln in lines:
        if re.match(r"^\s*max_notifications\s*=", ln):
            out.append(f"max_notifications={val}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"max_notifications={val}")
    with open(ADDONS_CONF, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")


def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    b = Box("NOTIF // ERROR" if _ERR else "NOTIF // STATUS", icon="\uf071" if _ERR else "\uf058")
    b.add_row(f"  {Txt(_ERR if _ERR else _MSG, role='warn' if _ERR else 'vitality')}")
    return [b]


# =====================================================================
# DUAL VIEWS — SYSTEM
# =====================================================================

def _build_system_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    rules = _load_rules()
    max_n = _get_max_notifications()
    active = get_active_notifs()
    total = load_notifs()
    cards = _feedback_cards()

    b = Box(
        "Sistema Notifiche & Cronologia" if is_it else "System Notifications & Archive",
        icon="\uf0f3",
        actions=[
            StateBadge(rules["dnd"], "DND ON", "DND OFF", on_tone="warn"),
            ActionBtn("Test Notify", "cc_notif:test", tone="apply"),
            ActionBtn("Clear Archive", "cc_notif:clear", tone="danger_soft"),
        ],
        elected=True,
    )
    b.add_row(" ")
    b.add_kv("Do Not Disturb", StateBadge(rules["dnd"], "SILENCED", "DELIVERING"), right_markup=f"{SelectPill('ON', 'cc_notif:dnd:1', selected=rules['dnd'])} {SelectPill('OFF', 'cc_notif:dnd:0', selected=not rules['dnd'])}")
    q_state = StateBadge(rules["quiet_enabled"], rules["quiet_window"], "DISABLED")
    q_acts = f"{SelectPill('Toggle', 'cc_notif:quiet_toggle', selected=rules['quiet_enabled'])} {ActionBtn('Set..', 'cc_notif:prompt_quiet', tone='select')}"
    b.add_kv("Quiet Hours", q_state, right_markup=q_acts)
    b.add_kv("Quiet Presets", " ".join(SelectPill(w, f"cc_notif:quiet_win:{w}", selected=(rules["quiet_window"] == w)) for w in ("22:00-07:00", "23:00-07:00", "00:00-08:00")))
    m_pills = " ".join(SelectPill(str(n), f"cc_notif:max:{n}", selected=(max_n == n)) for n in (25, 50, 100, 200))
    b.add_kv("History Limit", Txt(f"{max_n} items ({len(active)} active / {len(total)} total)", role="intent"), right_markup=m_pills)
    grp_active = bool(rules.get("group_by_app", False))
    b.add_kv(
        "Raggruppa per App" if is_it else "Group by Application",
        StateBadge(grp_active, "RAGGRUPPATE" if is_it else "GROUPED", "CRONOLOGICO" if is_it else "CHRONOLOGICAL", on_tone="ok"),
        right_markup=f"{SelectPill('ON', 'cc_notif:group_app:1', selected=grp_active)} {SelectPill('OFF', 'cc_notif:group_app:0', selected=not grp_active)}"
    )

    def _fill_hist(bx: Box):
        for n in reversed(active[-8:]):
            app = str(n.get("app") or n.get("notation") or "sys")[:12]
            ttl = canvas.truncate_plain(str(n.get("title") or n.get("summary") or "(untitled)"), 44)
            bx.add_row(f"  {Badge(app, style='info')} {Txt(ttl, role='focus')}")
        if not active:
            bx.add_row(f"  {Txt('No active notifications in archive.', role='schema')}")

    b.add_collapsible(cc_panel, "notif_hist", f"Recent Archive ({len(active)} active)", _fill_hist)
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_system_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    rules = _load_rules()
    max_n = _get_max_notifications()
    active = get_active_notifs()
    cards = _feedback_cards()
    b = Box("DISPATCH DAEMON // DND, QUIET SCHEDULE & RING BUFFER", icon="\uf0f3", actions=[ActionBtn("TEST PING", "cc_notif:test", tone="apply"), ActionBtn("FLUSH ALL", "cc_notif:clear", tone="danger_soft")], elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[DND MODE]   ──►', role='vitality')} {StateBadge(rules['dnd'], 'SILENT', 'ACTIVE')}  {SelectPill('ENABLE', 'cc_notif:dnd:1', selected=rules['dnd'])} {SelectPill('DISABLE', 'cc_notif:dnd:0', selected=not rules['dnd'])}")
    b.add_row(f"  {Txt('[QUIET WIN]  ──►', role='intent')} {SelectPill('SCHEDULE', 'cc_notif:quiet_toggle', selected=rules['quiet_enabled'])} {Txt(rules['quiet_window'], role='focus')}  " + " ".join(SelectPill(w, f"cc_notif:quiet_win:{w}", selected=(rules["quiet_window"] == w)) for w in ("22:00-07:00", "23:00-07:00")))
    b.add_row(f"  {Txt('[RING LIMIT] ──►', role='schema')} {Txt(f'{max_n} MAX ({len(active)} LIVE)', role='focus')}  " + " ".join(SelectPill(str(n), f"cc_notif:max:{n}", selected=(max_n == n)) for n in (25, 50, 100, 200)))
    grp_active = bool(rules.get("group_by_app", False))
    b.add_row(f"  {Txt('[APP GROUPING] ──►', role='vitality')} {StateBadge(grp_active, 'CLUSTERED', 'FLAT')}  {SelectPill('ON', 'cc_notif:group_app:1', selected=grp_active)} {SelectPill('OFF', 'cc_notif:group_app:0', selected=not grp_active)}")

    def _fill_hist(bx: Box):
        for n in reversed(active[-6:]):
            app = str(n.get("app") or n.get("notation") or "sys")[:12]
            ttl = canvas.truncate_plain(str(n.get("title") or n.get("summary") or "(untitled)"), 44)
            bx.add_row(f"  {Txt('├─', role='structure')} {Badge(app, style='info')} {Txt(ttl, role='focus')}")
        if not active:
            bx.add_row(f"  {Txt('Buffer clean.', role='schema')}")

    b.add_collapsible(cc_panel, "notif_hist", f"BUFFER PREVIEW ({len(active)} ACTIVE)", _fill_hist)
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# DUAL VIEWS — APPS & URGENCY FILTERS
# =====================================================================

def _collected_apps(rules: dict) -> list[str]:
    seen = list(dict.fromkeys(_COMMON_APPS + list(rules.get("muted_apps", []))))
    for n in load_notifs()[-40:]:
        a = str(n.get("app") or "").strip().lower()
        if a and a not in seen:
            seen.append(a)
    return seen[:14]


def _build_apps_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    rules = _load_rules()
    muted = set(a.lower() for a in rules.get("muted_apps", []))
    apps = _collected_apps(rules)
    urg = rules.get("min_urgency", "low")
    cards = _feedback_cards()

    b = Box("Filtri Applicazione & Urgenza" if is_it else "Per-App & Urgency Notification Filters", icon="\uf1de", actions=[ActionBtn("+ Mute App..", "cc_notif:prompt_app", tone="select")], elected=True)
    b.add_row(" ")
    u_pills = " ".join(SelectPill(u.upper(), f"cc_notif:urg:{u}", selected=(urg == u)) for u in ("low", "normal", "critical"))
    b.add_kv("Minimum Urgency", Txt(urg, role="intent"), right_markup=u_pills)
    b.add_section("Application Rules")
    rows = []
    for a in apps:
        is_muted = a in muted
        rows.append([
            Txt(a, role="focus"),
            StateBadge(not is_muted, "ALLOWED", "SUPPRESSED", on_tone="ok" if not is_muted else "warn"),
            f"{SelectPill('Allow', f'cc_notif:allow_app:{a}', selected=not is_muted)} {SelectPill('Mute', f'cc_notif:mute_app:{a}', selected=is_muted)}",
        ])
    b.add_table(headers=["APPLICATION", "POLICY", "ACTION"], widths=[22, 16, "flex"], rows=rows, aligns=["left", "left", "right"])
    b.add_row(" ")
    cards.append(b)
    return cards


def _build_apps_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    rules = _load_rules()
    muted = set(a.lower() for a in rules.get("muted_apps", []))
    apps = _collected_apps(rules)
    urg = rules.get("min_urgency", "low")
    cards = _feedback_cards()

    b = Box("PACKET FILTER // APP WHITELIST & URGENCY GATE", icon="\uf1de", actions=[ActionBtn("+ MUTE APP", "cc_notif:prompt_app", tone="select")], elected=True)
    b.add_row(" ")
    b.add_row(f"  {Txt('[MIN URGENCY]─►', role='vitality')} " + " ".join(SelectPill(u.upper(), f"cc_notif:urg:{u}", selected=(urg == u)) for u in ("low", "normal", "critical")))
    b.add_section("PER-PROCESS DISPATCH RULES")
    rows = []
    for a in apps:
        is_muted = a in muted
        rows.append([
            f"{Txt('┣━', role='structure')} {Txt(a, role='focus')}",
            StateBadge(not is_muted, "PASS", "DROP"),
            f"{SelectPill('PASS', f'cc_notif:allow_app:{a}', selected=not is_muted)} {SelectPill('DROP', f'cc_notif:mute_app:{a}', selected=is_muted)}",
        ])
    b.add_table(headers=[], widths=[22, 14, "flex"], rows=rows, aligns=["left", "left", "right"])
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cc_notifications_cards(cc_panel, canvas: Canvas, active_sub: str = "system") -> list:
    is_it = (get_active_language() == "it")
    cyber = get_cc_panel_style() == "cyberpunk"
    if active_sub == "apps":
        return _build_apps_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_apps_standard(cc_panel, canvas, is_it)
    return _build_system_cyberpunk(cc_panel, canvas, is_it) if cyber else _build_system_standard(cc_panel, canvas, is_it)


def handle_cc_notifications_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if not uri.startswith("cc_notif:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    rules = _load_rules()
    if cmd == "dnd":
        dnd = (arg == "1")
        rules["dnd"] = dnd
        _save_rules(rules)
        if os.environ.get("DBUS_SESSION_BUS_ADDRESS") and probe.has_bin("swaync-client"):
            probe.run_cmd_full(["swaync-client", "-dn" if dnd else "-df"], timeout=1.0)
        _MSG = f"Do Not Disturb {'enabled' if dnd else 'disabled'}."
    elif cmd == "quiet_toggle":
        rules["quiet_enabled"] = not rules["quiet_enabled"]
        _save_rules(rules)
        _MSG = f"Quiet hours {'enabled' if rules['quiet_enabled'] else 'disabled'} ({rules['quiet_window']})."
    elif cmd == "quiet_win" and arg:
        rules["quiet_window"] = arg
        rules["quiet_enabled"] = True
        _save_rules(rules)
        _MSG = f"Quiet schedule set to {arg}."
    elif cmd == "prompt_quiet" and cc_panel and cc_panel.shell:
        cc_panel.shell.open_prompt("cc_notif_quiet", "Quiet schedule (HH:MM-HH:MM):", rules["quiet_window"])
        return True
    elif cmd == "prompt_app" and cc_panel and cc_panel.shell:
        cc_panel.shell.open_prompt("cc_notif_mute_app", "App name to suppress (e.g. discord):", "")
        return True
    elif cmd == "max" and arg.isdigit():
        _set_max_notifications(int(arg))
        _MSG = f"Set max_notifications={arg} in nc-addons.conf."
    elif cmd == "urg" and arg in ("low", "normal", "critical"):
        rules["min_urgency"] = arg
        _save_rules(rules)
        _MSG = f"Minimum notification urgency set to {arg}."
    elif cmd == "group_app":
        val = (arg == "1")
        rules["group_by_app"] = val
        _save_rules(rules)
        _MSG = "Raggruppamento per applicazione abilitato." if (val and is_it) else ("Application grouping enabled." if val else ("Raggruppamento disabilitato (cronologico)." if is_it else "Application grouping disabled (chronological)."))
    elif cmd == "mute_app" and arg:
        muted = set(a.lower() for a in rules.get("muted_apps", []))
        muted.add(arg.lower())
        rules["muted_apps"] = sorted(muted)
        _save_rules(rules)
        _MSG = f"Muted notifications from {arg}."
    elif cmd == "allow_app" and arg:
        muted = set(a.lower() for a in rules.get("muted_apps", []))
        muted.discard(arg.lower())
        rules["muted_apps"] = sorted(muted)
        _save_rules(rules)
        _MSG = f"Allowed notifications from {arg}."
    elif cmd == "test":
        if os.path.exists(_NOTIFY_SCRIPT):
            probe.spawn_bg([_NOTIFY_SCRIPT, "--title", "Nightcity Test", "--body", "Notification dispatch operational."])
            _MSG = "Dispatched test notification via nc_util_notify.sh."
        elif probe.has_bin("notify-send"):
            probe.spawn_bg(["notify-send", "Nightcity Test", "Notification dispatch operational."])
            _MSG = "Dispatched test notification via notify-send."
        else:
            _ERR = "Neither nc_util_notify.sh nor notify-send available."
    elif cmd == "clear":
        clear_all_notifs()
        _MSG = "Cleared notification archive."
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_notifications_prompt(cc_panel, action_id: str, text: str) -> bool:
    global _MSG, _ERR
    if action_id == "cc_notif_quiet" and text.strip():
        win = text.strip()
        if re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", win):
            rules = _load_rules()
            rules["quiet_window"] = win
            rules["quiet_enabled"] = True
            _save_rules(rules)
            _MSG = f"Quiet window updated to {win}."
        else:
            _ERR = "Invalid format (use HH:MM-HH:MM)."
    elif action_id == "cc_notif_mute_app" and text.strip():
        app = text.strip().lower()
        rules = _load_rules()
        muted = set(a.lower() for a in rules.get("muted_apps", []))
        muted.add(app)
        rules["muted_apps"] = sorted(muted)
        _save_rules(rules)
        _MSG = f"Added suppression rule for {app}."
    else:
        return False
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
