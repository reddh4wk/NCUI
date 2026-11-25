#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Notifications Engine (`panels/cc_engine_notifications.py`)."""

import datetime
import glob
import json
import os
import re
import shlex
import shutil
import socket
import sys
import time
from core import probe
from core.config import (
    get_sys_notif_opacity,
    set_sys_notif_opacity_pct,
)
from core.i18n import t
from core.paths import find_bin, resolve_script_path
from core.state import STATE
from ui.kit import FeedbackBox, start_inline_input

HIST_FILE = os.path.expanduser("~/.cache/nc_notifications.json")
ADDONS_CONF = os.path.expanduser("~/.config/nc-ui/nc-addons.conf")
RULES_FILE = os.path.expanduser("~/.config/nc-ui/notifications_rules.json")
_NOTIFY_SCRIPT = find_bin("nc-notify", fallback_aliases=("nc_util_notify.sh",)) or resolve_script_path("nc-notify", fallback_alias="nc_util_notify.sh")

_NTF_MSG = ""
_NTF_ERR = ""
_COMMON_APPS = ["discord", "slack", "firefox", "thunderbird", "spotify", "chromium", "steam"]
_POPUP_POSITIONS = ("top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right")
_POPUP_EFFECTS = ("slide", "fade", "slide_fade", "none")
_POPUP_BIN = find_bin("nc-notify-popup") or "nc-notify-popup"


def _get_app_icon(app_name: str) -> str:
    low = (app_name or "").lower()
    if "telegram" in low:
        return "\ue217"
    if "firefox" in low:
        return "\uf269"
    if "brave" in low or "chrome" in low or "chromium" in low:
        return "\uf268"
    if "discord" in low:
        return "\uf392"
    if "spotify" in low:
        return "\uf1bc"
    if "slack" in low:
        return "\uf198"
    if "thunderbird" in low or "mail" in low:
        return "\uf0e0"
    if "screenshot" in low or "satty" in low:
        return "\uf030"
    if "ghostty" in low or "kitty" in low or "terminal" in low:
        return "\uf120"
    return "\uf0f3"


get_app_icon = _get_app_icon


def get_popup_socket_path() -> str:
    return f"/tmp/nc_notify_popup_{os.getuid()}.sock"


def is_in_quiet_hours(rules: dict, now: datetime.datetime = None) -> bool:
    """Return True if quiet hours are enabled and current local time falls inside quiet_window."""
    if not rules or not rules.get("quiet_enabled"):
        return False
    m = re.match(r"^\s*(\d{2}):(\d{2})-(\d{2}):(\d{2})\s*$", str(rules.get("quiet_window") or ""))
    if not m:
        return False
    sh, sm, eh, em = (int(x) for x in m.groups())
    if sh > 23 or eh > 23 or sm > 59 or em > 59:
        return False
    cur_dt = now or datetime.datetime.now()
    cur_m = cur_dt.hour * 60 + cur_dt.minute
    start_m = sh * 60 + sm
    end_m = eh * 60 + em
    if start_m == end_m:
        return True
    if start_m > end_m:
        return cur_m >= start_m or cur_m < end_m
    return start_m <= cur_m < end_m


def spawn_notification_popup(notif: dict, force: bool = False) -> bool:
    """Dispatch a notification popup to the live Layer-Shell host or spawn one detached."""
    payload = {
        "id": notif.get("id", int(time.time() * 1000)),
        "app": str(notif.get("app_name") or notif.get("app") or "NC-UI").strip() or "NC-UI",
        "title": str(notif.get("title") or notif.get("summary") or "").strip(),
        "body": str(notif.get("body") or "").strip(),
        "urgency": str(notif.get("urgency") or "normal").strip().lower(),
        "time": str(notif.get("time") or time.strftime("%H:%M")).strip(),
        "preview": bool(force),
        "opacity": get_sys_notif_opacity(),
    }
    raw = json.dumps(payload, ensure_ascii=False)
    sock_path = get_popup_socket_path()
    if os.path.exists(sock_path):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.25)
                s.connect(sock_path)
                s.sendall((raw + "\n").encode("utf-8"))
            return True
        except Exception:
            pass
    popup_bin = _POPUP_BIN if os.path.exists(_POPUP_BIN) else find_bin("nc-notify-popup")
    if popup_bin and os.path.exists(popup_bin):
        probe.spawn_bg([sys.executable, popup_bin, "--payload", raw])
        return True
    return False



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


def _load_rules() -> dict:
    base = {
        "dnd": False,
        "quiet_enabled": False,
        "quiet_window": "23:00-07:00",
        "min_urgency": "low",
        "muted_apps": [],
        "clickable_apps": {},
        "group_by_app": False,
        "popup_enabled": False,
        "popup_timeout": 5,
        "popup_position": "top-right",
        "popup_effect": "slide",
    }
    if os.path.exists(RULES_FILE):
        try:
            data = json.load(open(RULES_FILE, "r", encoding="utf-8"))
            if isinstance(data, dict):
                base.update(data)
        except Exception:
            pass
    if not isinstance(base.get("clickable_apps"), dict):
        base["clickable_apps"] = {}
    base["popup_enabled"] = bool(base.get("popup_enabled", False))
    try:
        base["popup_timeout"] = max(0, min(120, int(base.get("popup_timeout", 5))))
    except Exception:
        base["popup_timeout"] = 5
    if base.get("popup_position") not in _POPUP_POSITIONS:
        base["popup_position"] = "top-right"
    if base.get("popup_effect") not in _POPUP_EFFECTS:
        base["popup_effect"] = "slide"
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


def _notif_feedback_cards() -> list:
    if not _NTF_MSG and not _NTF_ERR:
        return []
    return [FeedbackBox(t("cc_notifications.status_title"), not bool(_NTF_ERR), _NTF_ERR if _NTF_ERR else _NTF_MSG)]


def _collected_apps(rules: dict) -> list[str]:
    seen = list(dict.fromkeys(_COMMON_APPS + list(rules.get("muted_apps", []))))
    for n in load_notifs()[-40:]:
        a = str(n.get("app") or "").strip().lower()
        if a and a not in seen:
            seen.append(a)
    return seen[:14]


def get_archive_unique_apps(rules: dict = None) -> list[str]:
    """Extract ordered unique application names recorded in the notification archive."""
    rules = rules if rules is not None else _load_rules()
    seen_lower = {}
    ordered = []
    all_notifs = load_notifs()
    for n in all_notifs:
        if n.get("deleted"):
            continue
        raw = str(n.get("app_name") or n.get("app") or "").strip()
        if raw and raw.lower() not in seen_lower:
            seen_lower[raw.lower()] = raw
            ordered.append(raw)
    for n in all_notifs:
        raw = str(n.get("app_name") or n.get("app") or "").strip()
        if raw and raw.lower() not in seen_lower:
            seen_lower[raw.lower()] = raw
            ordered.append(raw)
    for k, v in (rules.get("clickable_apps") or {}).items():
        disp = str((v or {}).get("app") or k).strip() if isinstance(v, dict) else str(k).strip()
        if disp and k.lower() not in seen_lower:
            seen_lower[k.lower()] = disp
            ordered.append(disp)
    return ordered


def _resolve_app_meta(app_name: str) -> dict:
    """Resolve launch command and matching identifiers from .desktop entries or PATH."""
    raw = str(app_name or "").strip()
    if not raw:
        return {"cmd": "", "tokens": set(), "low": "", "slug": "", "first": ""}
    low = raw.lower()
    slug = re.sub(r"[^a-z0-9]+", "", low)
    first_word = low.split()[0] if low.split() else low
    tokens = {tok for tok in (low, slug) if tok}
    if len(first_word) >= 3:
        tokens.add(first_word)

    dirs = (
        os.path.expanduser("~/.local/share/applications"),
        "/usr/local/share/applications",
        "/usr/share/applications",
        "/var/lib/flatpak/exports/share/applications",
    )
    best = None
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "*.desktop")):
            stem = os.path.basename(path)[:-8].lower()
            if "url-handler" in stem:
                continue
            try:
                txt = open(path, "r", encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            m_entry = re.search(r"^\[Desktop Entry\]\s*$", txt, re.M)
            entry_txt = txt[m_entry.end():].split("\n[", 1)[0] if m_entry else txt.split("\n[", 1)[0]
            if re.search(r"^NoDisplay\s*=\s*true", entry_txt, re.M | re.I):
                continue
            m_name = re.search(r"^Name\s*=\s*(.+)$", entry_txt, re.M)
            m_exec = re.search(r"^Exec\s*=\s*(.+)$", entry_txt, re.M)
            if not m_exec:
                continue
            m_wm = re.search(r"^StartupWMClass\s*=\s*(.+)$", entry_txt, re.M)
            d_name = m_name.group(1).strip().lower() if m_name else ""
            d_slug = re.sub(r"[^a-z0-9]+", "", d_name)
            wm_class = m_wm.group(1).strip().lower() if m_wm else ""
            wm_slug = re.sub(r"[^a-z0-9]+", "", wm_class)
            exec_clean = re.sub(r"\s+%[fFuUdDnNickvm]", "", m_exec.group(1).strip())
            exec_clean = re.sub(r"\s+--$", "", exec_clean).strip()
            score = 0
            if d_name == low or stem == low or wm_class == low:
                score = 100
            elif d_slug == slug or wm_slug == slug or (slug and slug in stem.replace(".", "")):
                score = 85
            elif first_word and (d_name.startswith(first_word) or first_word in stem or first_word in wm_class):
                score = 70
            if score > 0 and exec_clean and (best is None or score > best[0]):
                best = (score, exec_clean, stem, wm_class)

    cmd = ""
    if best:
        cmd, stem, wm_class = best[1], best[2], best[3]
        for cand in (stem, wm_class):
            if cand:
                tokens.add(cand)
                c_slug = re.sub(r"[^a-z0-9]+", "", cand)
                if c_slug:
                    tokens.add(c_slug)
        try:
            bin_tok = shlex.split(cmd)[0]
        except Exception:
            bin_tok = cmd.split()[0] if cmd.split() else ""
        if bin_tok:
            bin_base = os.path.basename(bin_tok).lower()
            tokens.add(bin_base)
            b_slug = re.sub(r"[^a-z0-9]+", "", bin_base)
            if b_slug:
                tokens.add(b_slug)
    else:
        for cand in (raw, low, first_word):
            if cand and shutil.which(cand):
                cmd = cand
                c_low = cand.lower()
                tokens.add(c_low)
                c_slug = re.sub(r"[^a-z0-9]+", "", c_low)
                if c_slug:
                    tokens.add(c_slug)
                break

    return {"cmd": cmd, "tokens": tokens, "low": low, "slug": slug, "first": first_word}


def resolve_app_launch_cmd(app_name: str) -> str:
    """Resolve default launch command for a notification's source application via .desktop or PATH."""
    return _resolve_app_meta(app_name)["cmd"]


def _focus_niri_app_window(app_name: str, notif: dict = None) -> tuple[bool, str]:
    """Find an open niri window matching app_name and focus it."""
    if not probe.has_bin("niri"):
        return False, ""
    if not os.environ.get("NIRI_SOCKET"):
        socks = sorted(glob.glob(f"/run/user/{os.getuid()}/niri*.sock"))
        if socks:
            os.environ["NIRI_SOCKET"] = socks[0]
    ok, out = probe.run_cmd(["niri", "msg", "-j", "windows"], timeout=0.8)
    if not ok or not out:
        return False, ""
    try:
        windows = json.loads(out)
    except Exception:
        return False, ""
    if not isinstance(windows, list) or not windows:
        return False, ""

    meta = _resolve_app_meta(app_name)
    tokens = meta["tokens"]
    n_title = str((notif or {}).get("title") or (notif or {}).get("summary") or "").strip().lower()
    n_body = str((notif or {}).get("body") or "").strip().lower()
    notif_words = [p for p in re.findall(r"[a-z0-9_-]+", f"{n_title} {n_body}") if len(p) >= 4]

    best_win = None
    best_score = 0
    for w in windows:
        if not isinstance(w, dict) or w.get("id") is None:
            continue
        app_id = str(w.get("app_id") or "").strip().lower()
        app_id_slug = re.sub(r"[^a-z0-9]+", "", app_id)
        w_title = str(w.get("title") or "").strip().lower()
        score = 0
        if app_id in tokens or (app_id_slug and app_id_slug in tokens):
            score = 100
        elif any(len(tok) >= 3 and (tok in app_id or tok in app_id_slug or (len(app_id_slug) >= 3 and app_id_slug in tok)) for tok in tokens):
            score = 85
        elif (len(meta["low"]) >= 3 and meta["low"] in w_title) or (len(meta["first"]) >= 4 and meta["first"] in w_title):
            score = 60
        if score == 0:
            continue
        if w.get("is_urgent"):
            score += 3
        if w_title and ((len(n_title) >= 3 and n_title in w_title) or any(part in w_title for part in notif_words)):
            score += 5
        if score > best_score:
            best_score = score
            best_win = w

    if not best_win:
        return False, ""
    wid = best_win["id"]
    ok_f, _, _ = probe.run_cmd_full(["niri", "msg", "action", "focus-window", "--id", str(wid)], timeout=1.0)
    if ok_f:
        return True, f"focus:{best_win.get('app_id') or app_name} (#{wid})"
    return False, ""


def get_app_click_config(app_name: str, rules: dict = None) -> dict | None:
    """Return click config dict if app_name is registered as clickable, else None."""
    key = str(app_name or "").strip().lower()
    if not key:
        return None
    rules = rules if rules is not None else _load_rules()
    cmap = rules.get("clickable_apps") or {}
    if key not in cmap:
        return None
    val = cmap[key]
    if isinstance(val, dict):
        return {"app": str(val.get("app") or app_name).strip(), "cmd": str(val.get("cmd") or "").strip()}
    return {"app": str(app_name).strip(), "cmd": ""}


def trigger_notification_click(notif_or_id, rules: dict = None) -> tuple[bool, str]:
    """Execute custom action, or focus open niri window / launch source program for a clickable notification."""
    rules = rules if rules is not None else _load_rules()
    notif = notif_or_id if isinstance(notif_or_id, dict) else None
    app_name = ""
    if notif is not None:
        app_name = str(notif.get("app_name") or notif.get("app") or "").strip()
    else:
        s_id = str(notif_or_id or "").strip()
        for n in load_notifs():
            if str(n.get("id", "")) == s_id:
                notif = n
                app_name = str(n.get("app_name") or n.get("app") or "").strip()
                break
        if not app_name:
            app_name = s_id
    cfg = get_app_click_config(app_name, rules)
    if not cfg:
        return False, ""
    custom_cmd = str(cfg.get("cmd") or "").strip()
    if custom_cmd:
        probe.spawn_bg(["bash", "-c", custom_cmd])
        return True, custom_cmd
    # ponytail: focus existing niri window before spawning a new instance
    ok_focus, focus_desc = _focus_niri_app_window(app_name, notif)
    if ok_focus:
        return True, focus_desc
    cmd = resolve_app_launch_cmd(app_name)
    if not cmd:
        return False, ""
    probe.spawn_bg(["bash", "-c", cmd])
    return True, cmd


def handle_cc_notifications_link(cc_panel, uri: str) -> bool:
    global _NTF_MSG, _NTF_ERR
    if not uri.startswith("cc_notif:"):
        return False
    _NTF_MSG = ""
    _NTF_ERR = ""
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
        _NTF_MSG = t("cc_notifications.do_not_disturb_2", p0=t("common.enabled").lower() if dnd else t("common.disabled").lower())
    elif cmd == "quiet_toggle":
        rules["quiet_enabled"] = not rules["quiet_enabled"]
        _save_rules(rules)
        _NTF_MSG = t(
            "cc_notifications.quiet_hours_2",
            p0=t("common.enabled").lower() if rules["quiet_enabled"] else t("common.disabled").lower(),
            p1=rules["quiet_window"],
        )
    elif cmd == "quiet_win" and arg:
        rules["quiet_window"] = arg
        rules["quiet_enabled"] = True
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.quiet_schedule_set_to", p0=arg)
    elif cmd == "inline_quiet" and cc_panel:
        start_inline_input(
            "cc_notif_quiet",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=rules["quiet_window"],
            max_len=13,
            validator=lambda s: bool(re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", s.strip())),
            on_commit=lambda s: handle_cc_notifications_prompt(cc_panel, "cc_notif_quiet", s),
        )
        return True
    elif cmd == "app_pick_open" and cc_panel:
        cur_q = str(STATE.get("settings", "notif_app_query", "") or "")
        start_inline_input(
            "cc_notif_app_picker",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=36,
            is_picker=True,
            on_change=lambda s: STATE.set("settings", "notif_app_query", str(s or "")),
            on_cancel=lambda: STATE.set("settings", "notif_app_query", ""),
            on_commit=lambda s: (STATE.set("settings", "notif_app_query", ""), handle_cc_notifications_prompt(cc_panel, "cc_notif_mute_app", s)),
        )
        return True
    elif cmd == "app_pick_clear":
        STATE.set("settings", "notif_app_query", "")
    elif cmd == "max" and arg.isdigit():
        _set_max_notifications(int(arg))
        _NTF_MSG = t("cc_notifications.set_max_notifications_in_nc_addons", p0=arg)
    elif cmd == "urg" and arg in ("low", "normal", "critical"):
        rules["min_urgency"] = arg
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.minimum_notification_urgency_set_t", p0=arg)
    elif cmd == "group_app":
        val = (arg == "1")
        rules["group_by_app"] = val
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.fb_group_on") if val else t("cc_notifications.fb_group_off")
    elif cmd == "popup_en" and arg in ("0", "1"):
        val = (arg == "1")
        rules["popup_enabled"] = val
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.fb_popup_en", p0=t("common.enabled").lower() if val else t("common.disabled").lower())
    elif cmd == "popup_timeout" and arg.isdigit():
        sec = max(0, min(120, int(arg)))
        rules["popup_timeout"] = sec
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.fb_popup_timeout_sticky") if sec == 0 else t("cc_notifications.fb_popup_timeout_set", p0=sec)
    elif cmd == "inline_popup_timeout" and cc_panel:
        start_inline_input(
            "cc_notif_popup_timeout",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(rules.get("popup_timeout", 5)),
            max_len=4,
            validator=lambda s: s.strip().isdigit() and 0 <= int(s.strip()) <= 120,
            on_commit=lambda s: handle_cc_notifications_prompt(cc_panel, "cc_notif_popup_timeout", s),
        )
        return True
    elif cmd == "popup_pos" and arg in _POPUP_POSITIONS:
        rules["popup_position"] = arg
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.fb_popup_pos", p0=arg)
    elif cmd == "popup_effect" and arg in _POPUP_EFFECTS:
        rules["popup_effect"] = arg
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.fb_popup_effect", p0=arg.replace("_", "+"))
    elif cmd == "popup_op":
        try:
            set_sys_notif_opacity_pct(int(round(float(arg))))
        except Exception:
            pass
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True
    elif cmd == "popup_preview":
        ok_p = spawn_notification_popup(
            {
                "id": int(time.time() * 1000),
                "app": "NC-UI",
                "title": t("cc_notifications.preview_title"),
                "body": t("cc_notifications.preview_body"),
                "urgency": "normal",
                "time": time.strftime("%H:%M"),
            },
            force=True,
        )
        if ok_p:
            _NTF_MSG = t("cc_notifications.fb_popup_preview")
        else:
            _NTF_ERR = t("cc_notifications.neither_nc_util_notify_sh_nor_noti")
    elif cmd == "del" and arg:
        delete_notif(arg)
        _NTF_MSG = t("cc_notifications.fb_notif_dismissed")
    elif cmd == "mute_app" and arg:
        STATE.set("settings", "notif_app_query", "")
        muted = set(a.lower() for a in rules.get("muted_apps", []))
        muted.add(arg.lower())
        rules["muted_apps"] = sorted(muted)
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.muted_notifications_from", p0=arg)
    elif cmd == "allow_app" and arg:
        muted = set(a.lower() for a in rules.get("muted_apps", []))
        muted.discard(arg.lower())
        rules["muted_apps"] = sorted(muted)
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.allowed_notifications_from", p0=arg)
    elif cmd == "click_toggle" and arg:
        cmap = dict(rules.get("clickable_apps") or {})
        key = arg.strip().lower()
        if key in cmap:
            cmap.pop(key, None)
            _NTF_MSG = t("cc_notifications.disabled_clickable_msg", p0=arg)
        else:
            cmap[key] = {"app": arg.strip(), "cmd": ""}
            _NTF_MSG = t("cc_notifications.enabled_clickable_msg", p0=arg)
        rules["clickable_apps"] = cmap
        _save_rules(rules)
    elif cmd == "prompt_click_cmd" and arg:
        cmap = rules.get("clickable_apps") or {}
        cur_cmd = str((cmap.get(arg.strip().lower()) or {}).get("cmd", "")).strip()
        auto_cmd = resolve_app_launch_cmd(arg) or t("cc_notifications.no_cmd_found")
        if cc_panel:
            cc_panel.request_prompt(
                f"cc_notif_click_cmd|{arg.strip()}",
                t("cc_notifications.custom_action_placeholder", p0=auto_cmd),
                cur_cmd,
                title=t("cc_notifications.custom_action_title", p0=arg.strip()),
                completion="shell_cmd",
            )
        return True
    elif cmd == "trigger_click" and arg:
        ok, executed = trigger_notification_click(arg, rules)
        if ok:
            _NTF_MSG = t("cc_notifications.triggered_click_msg", p0=executed)
        else:
            _NTF_ERR = t("cc_notifications.triggered_click_err", p0=arg)
    elif cmd == "test":
        nscript = _NOTIFY_SCRIPT if os.path.exists(_NOTIFY_SCRIPT) else (find_bin("nc-notify", fallback_aliases=("nc_util_notify.sh",)) or resolve_script_path("nc-notify", fallback_alias="nc_util_notify.sh"))
        if nscript and os.path.exists(nscript):
            probe.spawn_bg([nscript, "--title", "Nightcity Test", "--body", "Notification dispatch operational."])
            _NTF_MSG = t("cc_notifications.dispatched_test_notification_via_n")
        elif probe.has_bin("notify-send"):
            probe.spawn_bg(["notify-send", "Nightcity Test", "Notification dispatch operational."])
            _NTF_MSG = t("cc_notifications.dispatched_test_notification_via_n_2")
        else:
            _NTF_ERR = t("cc_notifications.neither_nc_util_notify_sh_nor_noti")
    elif cmd == "clear":
        clear_all_notifs()
        _NTF_MSG = t("cc_notifications.cleared_notification_archive")
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_notifications_prompt(cc_panel, action_id: str, text: str) -> bool:
    global _NTF_MSG, _NTF_ERR
    if action_id == "cc_notif_quiet" and text.strip():
        win = text.strip()
        if re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", win):
            rules = _load_rules()
            rules["quiet_window"] = win
            rules["quiet_enabled"] = True
            _save_rules(rules)
            _NTF_MSG = t("cc_notifications.quiet_window_updated_to", p0=win)
        else:
            _NTF_ERR = t("cc_notifications.invalid_format_use_hh_mm_hh_mm")
    elif action_id == "cc_notif_popup_timeout":
        raw = text.strip()
        if raw.isdigit() and 0 <= int(raw) <= 120:
            sec = int(raw)
            rules = _load_rules()
            rules["popup_timeout"] = sec
            _save_rules(rules)
            _NTF_MSG = t("cc_notifications.fb_popup_timeout_sticky") if sec == 0 else t("cc_notifications.fb_popup_timeout_set", p0=sec)
        else:
            _NTF_ERR = t("cc_notifications.fb_popup_timeout_err")
    elif action_id == "cc_notif_mute_app" and text.strip():
        app = text.strip().lower()
        rules = _load_rules()
        muted = set(a.lower() for a in rules.get("muted_apps", []))
        muted.add(app)
        rules["muted_apps"] = sorted(muted)
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.added_suppression_rule_for", p0=app)
    elif action_id.startswith("cc_notif_click_cmd|"):
        app_name = action_id.split("|", 1)[1].strip()
        custom_cmd = str(text or "").strip()
        rules = _load_rules()
        cmap = dict(rules.get("clickable_apps") or {})
        key = app_name.lower()
        entry = cmap.get(key) if isinstance(cmap.get(key), dict) else {"app": app_name, "cmd": ""}
        entry["cmd"] = custom_cmd
        cmap[key] = entry
        rules["clickable_apps"] = cmap
        _save_rules(rules)
        _NTF_MSG = t("cc_notifications.custom_action_saved", p0=app_name) if custom_cmd else t("cc_notifications.custom_action_cleared", p0=app_name)
    else:
        return False
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


