#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.completion import normalize_desktop_id
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import get_active_language, t
from core.state import STATE
from panels.wp_display import resolve_script_path
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, FeedbackBox, SelectPill, StateBadge, Txt, start_inline_input

NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")

MIME_SLOTS = [
    ("browser", "Web Browser", "x-scheme-handler/https", ["brave-browser.desktop", "firefox.desktop", "chromium.desktop", "librewolf.desktop"]),
    ("terminal", "Terminal (NC-UI)", "nc_terminal", ["ghostty", "kitty", "foot", "alacritty"]),
    ("pkg_manager", "Package / AUR Helper", "nc_pkg_manager", ["paru", "yay", "pacman", "apt", "dnf", "zypper"]),
    ("files", "File Manager", "inode/directory", ["org.gnome.Nautilus.desktop", "thunar.desktop", "org.kde.dolphin.desktop", "nemo.desktop"]),
    ("editor", "Text / Code Editor", "text/plain", ["code.desktop", "nvim.desktop", "org.gnome.TextEditor.desktop", "kate.desktop"]),
    ("pdf", "PDF Reader", "application/pdf", ["org.pwmt.zathura.desktop", "org.gnome.Evince.desktop", "okularApplication_pdf.desktop"]),
    ("image", "Image Viewer", "image/png", ["imv.desktop", "org.gnome.Loupe.desktop", "swayimg.desktop", "satty.desktop"]),
    ("video", "Audio / Video Player", "video/mp4", ["mpv.desktop", "vlc.desktop", "celluloid.desktop"]),
]



def _set_apps_fb(ok: bool, msg: str) -> None:
    STATE.set("apps", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_apps_fb() -> dict:
    fb = STATE.get("apps", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _feedback_cards() -> list:
    fb = _get_apps_fb()
    if not fb:
        return []
    return [FeedbackBox(t("cc_apps.apps_systemd_status"), bool(fb["ok"]), fb["msg"])]


def get_niri_floating_rules() -> list[str]:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return []
    try:
        content = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
        pat = re.compile(r'window-rule\s*\{\s*match\s+app-id="([^"]+)"\s+open-floating\s+true\s*\}', re.MULTILINE)
        return sorted(set(m.group(1).replace("\\\\.", ".") for m in pat.finditer(content)))
    except Exception:
        return []


def get_niri_active_windows() -> list[dict]:
    ok, out = probe.run_cmd(["niri", "msg", "-j", "windows"], timeout=1.2)
    if not ok or not out:
        return []
    try:
        seen, res = set(), []
        for w in json.loads(out):
            aid = str(w.get("app_id") or "").strip()
            if aid and aid not in seen:
                seen.add(aid)
                res.append({"app_id": aid, "title": str(w.get("title") or "")})
        return sorted(res, key=lambda x: x["app_id"].lower())
    except Exception:
        return []


def toggle_niri_floating_rule(app_id: str) -> tuple[bool, str]:
    clean_id = str(app_id or "").strip()
    if not clean_id:
        return False, "Empty app-id"
    esc_app = clean_id.replace(".", "\\\\.")
    os.makedirs(os.path.dirname(NIRI_CONFIG_FILE), exist_ok=True)
    content = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read() if os.path.exists(NIRI_CONFIG_FILE) else ""
    rule_re = re.compile(r'\n*window-rule\s*\{\s*match\s+app-id="' + re.escape(esc_app) + r'"\s+open-floating\s+true\s*\}', re.MULTILINE)
    removed = bool(rule_re.search(content))
    if removed:
        new_content = rule_re.sub("", content)
    else:
        new_content = content.rstrip() + f'\n\nwindow-rule {{\n    match app-id="{esc_app}"\n    open-floating true\n}}\n'
    try:
        open(NIRI_CONFIG_FILE, "w", encoding="utf-8").write(new_content)
        return True, f"{'Removed' if removed else 'Added'} floating rule for {clean_id}"
    except Exception as e:
        return False, str(e)


def _query_mime_defaults() -> dict:
    res = {}
    has_xdg = probe.has_bin("xdg-mime")
    for key, _lbl, mime, _cands in MIME_SLOTS:
        if mime == "nc_terminal":
            res[key] = str(CONFIG.get("general", {}).get("terminal", "auto"))
        elif mime == "nc_pkg_manager":
            res[key] = probe.get_preferred_pkg_manager() or "auto"
        elif has_xdg:
            ok, out = probe.run_cmd(["xdg-mime", "query", "default", mime], timeout=0.8)
            res[key] = out.strip() if (ok and out.strip()) else "unset"
        else:
            res[key] = "unset"
    return res


def _available_desktop_files() -> set[str]:
    found = set()
    for d in ("/usr/share/applications", os.path.expanduser("~/.local/share/applications")):
        if os.path.isdir(d):
            try:
                found.update(f for f in os.listdir(d) if f.endswith(".desktop"))
            except Exception:
                pass
    return found


def _read_niri_spawns() -> list[dict]:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return []
    items = []
    for idx, line in enumerate(open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read().splitlines()):
        m = re.match(r"^\s*(//\s*)?spawn-at-startup\s+(.+?)\s*$", line)
        if m:
            disabled = bool(m.group(1))
            raw_cmd = m.group(2).strip()
            items.append({"idx": idx, "disabled": disabled, "cmd": raw_cmd})
    return items


def _toggle_niri_spawn(line_idx: int, remove: bool = False) -> tuple[bool, str]:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return False, "config.kdl missing"
    lines = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read().splitlines()
    if not (0 <= line_idx < len(lines)):
        return False, "Invalid spawn line"
    if remove:
        lines.pop(line_idx)
    else:
        ln = lines[line_idx]
        if ln.lstrip().startswith("//"):
            lines[line_idx] = re.sub(r"^(\s*)//\s*", r"\1", ln, count=1)
        else:
            lines[line_idx] = "// " + ln.lstrip()
    open(NIRI_CONFIG_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return True, "Updated spawn-at-startup in config.kdl"


def _add_niri_spawn(cmd_args: str) -> tuple[bool, str]:
    tokens = [t.strip() for t in cmd_args.strip().split() if t.strip()]
    if not tokens:
        return False, "Empty command"
    quoted = " ".join(f'"{t}"' for t in tokens)
    txt = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read() if os.path.exists(NIRI_CONFIG_FILE) else ""
    open(NIRI_CONFIG_FILE, "w", encoding="utf-8").write(txt.rstrip() + f"\nspawn-at-startup {quoted}\n")
    return True, f"Added spawn-at-startup {quoted}"


def _read_systemd_services(user: bool = True) -> list[dict]:
    cmd = ["systemctl"] + (["--user"] if user else []) + ["list-units", "--type=service", "--no-pager", "--no-legend", "--plain"]
    ok, out = probe.run_cmd(cmd, timeout=1.5)
    services = []
    if ok and out:
        for line in out.splitlines()[:24]:
            parts = line.split(None, 4)
            if len(parts) >= 4:
                services.append({
                    "unit": parts[0],
                    "active": parts[2],
                    "sub": parts[3],
                    "desc": parts[4] if len(parts) > 4 else "",
                })
    return services


def handle_cc_apps_link(cc_panel, uri: str) -> bool:
    if uri.startswith("cc_flt:"):
        act = uri[len("cc_flt:"):]
        if act == "open_tui":
            probe.open_terminal(["bash", resolve_script_path("nc_util_floater.sh", fallback_alias="nc-niri-floater.sh")])
            return True
        if act == "pick_open":
            cur_q = str(STATE.get("apps", "flt_query", "") or "")
            def _commit_raw_flt(txt: str) -> None:
                val = str(txt or "").strip()
                STATE.set("apps", "flt_query", "")
                if val and val not in get_niri_floating_rules():
                    ok, msg = toggle_niri_floating_rule(val)
                    _set_apps_fb(ok, msg)
            start_inline_input(
                entry_id="cc_flt_picker",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=cur_q,
                max_len=40,
                is_picker=True,
                on_change=lambda txt: STATE.set("apps", "flt_query", str(txt or "")),
                on_cancel=lambda: STATE.set("apps", "flt_query", ""),
                on_commit=_commit_raw_flt,
            )
            return True
        if act == "pick_clear":
            STATE.set("apps", "flt_query", "")
            return True
        if act.startswith("add_app:"):
            aid = act.split(":", 1)[1]
            STATE.set("apps", "flt_query", "")
            if aid and aid not in get_niri_floating_rules():
                ok, msg = toggle_niri_floating_rule(aid)
                _set_apps_fb(ok, msg)
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        if act.startswith("toggle:"):
            ok, msg = toggle_niri_floating_rule(act.split(":", 1)[1])
            _set_apps_fb(ok, msg)
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True

    if not uri.startswith("cc_apps:"):
        return False
    act = uri[len("cc_apps:"):]

    if act == "refresh":
        pass
    elif act.startswith("set_term:"):
        term = act.split(":", 1)[1]
        CONFIG.setdefault("general", {})["terminal"] = term
        _update_toml_key_in_section("general", "terminal", f'"{term}"')
        _set_apps_fb(True, t("cc_apps.default_nc_ui_terminal_set_to", p0=term))
    elif act.startswith("set_pkg_mgr:"):
        pm = act.split(":", 1)[1]
        CONFIG.setdefault("general", {})["pkg_manager"] = pm
        _update_toml_key_in_section("general", "pkg_manager", f'"{pm}"')
        _set_apps_fb(True, t("cc_apps.default_pkg_mgr_set_to", p0=pm))
    elif act.startswith("set_mime:"):
        _, mime, desktop = act.split(":", 2)
        STATE.set("apps", "mime_pick_slot", "")
        STATE.set("apps", "mime_pick_query", "")
        ok, _o, err = probe.run_cmd_full(["xdg-mime", "default", desktop, mime], timeout=2.0)
        _set_apps_fb(ok, f"Set {desktop} for {mime}" if ok else (err or "xdg-mime failed"))
    elif act.startswith("pick_mime:"):
        mime = act.split(":", 1)[1]
        slot_key = next((k for k, _l, m, _c in MIME_SLOTS if m == mime), mime.replace("/", "_"))
        STATE.set("apps", "mime_pick_slot", mime)
        cur_q = str(STATE.get("apps", "mime_pick_query", "") or "")
        def _commit_raw_mime(txt: str, target_mime: str = mime) -> None:
            STATE.set("apps", "mime_pick_slot", "")
            STATE.set("apps", "mime_pick_query", "")
            desk = normalize_desktop_id(txt)
            if desk:
                ok, _o, err = probe.run_cmd_full(["xdg-mime", "default", desk, target_mime], timeout=2.0)
                _set_apps_fb(ok, f"Set {desk} for {target_mime}" if ok else (err or "xdg-mime failed"))
        def _cancel_mime() -> None:
            STATE.set("apps", "mime_pick_slot", "")
            STATE.set("apps", "mime_pick_query", "")
        start_inline_input(
            entry_id=f"mime_pick_{slot_key}",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=48,
            is_picker=True,
            on_change=lambda txt: STATE.set("apps", "mime_pick_query", str(txt or "")),
            on_cancel=_cancel_mime,
            on_commit=_commit_raw_mime,
        )
        return True
    elif act.startswith("pick_mime_clear:"):
        STATE.set("apps", "mime_pick_query", "")
    elif act == "prompt_spawn":
        cc_panel.request_prompt("cc_apps_spawn", t("cc_apps.command_args_to_spawn_at_startup_e"), "", title=t("cc_apps.spawn_at_startup"), required=True, completion="shell_cmd")
        return True
    elif act.startswith("toggle_spawn:"):
        ok, msg = _toggle_niri_spawn(int(act.split(":", 1)[1]), remove=False)
        _set_apps_fb(ok, msg)
    elif act.startswith("del_spawn:"):
        ok, msg = _toggle_niri_spawn(int(act.split(":", 1)[1]), remove=True)
        _set_apps_fb(ok, msg)
    elif act.startswith("svc_user:"):
        _, op, unit = act.split(":", 2)
        ok, _o, err = probe.run_cmd_full(["systemctl", "--user", op, unit], timeout=3.0)
        _set_apps_fb(ok, f"systemctl --user {op} {unit}" if ok else (err or "Service action failed"))

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_apps_prompt(cc_panel, action_id: str, text: str) -> bool:
    val = str(text or "").strip()
    if not val:
        return False
    if action_id == "cc_flt_add":
        ok, msg = toggle_niri_floating_rule(val)
        _set_apps_fb(ok, msg)
        return True
    if action_id.startswith("cc_apps_mime|"):
        mime = action_id.split("|", 1)[1]
        ok, _o, err = probe.run_cmd_full(["xdg-mime", "default", val, mime], timeout=2.0)
        _set_apps_fb(ok, f"Set {val} for {mime}" if ok else (err or "xdg-mime failed"))
        return True
    if action_id == "cc_apps_spawn":
        ok, msg = _add_niri_spawn(val)
        _set_apps_fb(ok, msg)
        return True
    return False
