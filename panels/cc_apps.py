#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import get_active_language
from core.state import STATE
from panels.display import resolve_script_path
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, Divider, SelectPill, StateBadge, Txt

NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")

MIME_SLOTS = [
    ("browser", "Web Browser", "x-scheme-handler/https", ["brave-browser.desktop", "firefox.desktop", "chromium.desktop", "librewolf.desktop"]),
    ("terminal", "Terminal (NC-UI)", "nc_terminal", ["ghostty", "kitty", "foot", "alacritty"]),
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
    b = Box("Apps & Systemd Status", icon="\uf00c" if fb["ok"] else "\uf071")
    b.add_row(" ")
    b.add_row(f"  {Txt(' ' + fb['msg'] + ' ', role='vitality' if fb['ok'] else 'fault', bg=True)}")
    b.add_row(" ")
    return [b]


# --- Floating Rules Helpers ---
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


# --- MIME Helpers ---
def _query_mime_defaults() -> dict:
    res = {}
    has_xdg = probe.has_bin("xdg-mime")
    for key, _lbl, mime, _cands in MIME_SLOTS:
        if mime == "nc_terminal":
            res[key] = str(CONFIG.get("general", {}).get("terminal", "auto"))
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


# --- Autostart & Systemd Helpers ---
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


# =====================================================================
# SUBTAB 1: FLOATING RULES
# =====================================================================

def _build_floating_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    floating_ids = get_niri_floating_rules()
    active_wins = get_niri_active_windows()
    cards = _feedback_cards()

    hdr = Box(
        "Regole Finestre Floating Niri (nc_util_floater.sh)" if is_it else "Niri Floating Window Rules (nc_util_floater.sh)",
        icon="\uf2d2",
        actions=[ActionBtn("+ Add App-ID", "cc_flt:prompt_add", tone="apply"), ActionBtn("TUI", "cc_flt:open_tui", tone="select")],
        elected=True,
    )
    hdr.add_row(" ")
    hdr.add_kv("Config File", Txt(NIRI_CONFIG_FILE, role="val"), right_markup=Badge(f"{len(floating_ids)} RULES", style="info"))
    hdr.add_row(" ")
    cards.append(hdr)

    act_box = Box("Applicazioni Attive (Windows Picker)" if is_it else "Running Applications (Window Picker)", icon="\uf2d0")
    act_box.add_row(" ")
    if not active_wins:
        act_box.add_row(f"  {Txt('No active windows detected via niri IPC.' if not is_it else 'Nessuna finestra attiva rilevata.', role='inactive')}")
    else:
        rows = []
        for w in active_wins[:10]:
            aid = w["app_id"]
            is_flt = aid in floating_ids
            st = StateBadge(True, "FLOATING", on_tone="warn") if is_flt else StateBadge(True, "TILED", on_tone="select")
            btn = ActionBtn("Unfloat", f"cc_flt:toggle:{aid}", tone="danger_soft") if is_flt else ActionBtn("Set Float", f"cc_flt:toggle:{aid}", tone="apply")
            rows.append([Txt(aid[:24], role="focus"), st, Txt(canvas.truncate_plain(w["title"], 26), role="schema"), btn])
        act_box.add_table(headers=["APP-ID", "MODE", "TITLE", "ACTION"], widths=[24, 12, 26, "flex"], rows=rows, aligns=["left", "left", "left", "right"])
    act_box.add_row(" ")
    cards.append(act_box)

    r_box = Box(f"Regole Floating in config.kdl ({len(floating_ids)})" if is_it else f"Configured Floating Rules ({len(floating_ids)})", icon="\uf0c9")
    r_box.add_row(" ")
    if not floating_ids:
        r_box.add_row(f"  {Txt('No floating rules configured.', role='inactive')}")
    else:
        for fid in floating_ids:
            r_box.add_row(f"  {Txt('●', role='intent')} {Txt(fid, role='focus')}", right_markup=ActionBtn("Remove", f"cc_flt:toggle:{fid}", tone="danger_soft"))
    r_box.add_row(" ")
    cards.append(r_box)
    return cards



# =====================================================================
# SUBTAB 2: DEFAULT APPS (MIME)
# =====================================================================

def _build_mime_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    defs = _query_mime_defaults()
    avail_desk = _available_desktop_files()
    cards = _feedback_cards()

    box = Box(
        "Applicazioni Predefinite (xdg-mime / config.toml)" if is_it else "Default Applications (xdg-mime / config.toml)",
        icon="\uf1b2",
        actions=[ActionBtn("Refresh", "cc_apps:refresh", tone="neutral")],
        elected=True,
    )
    box.add_row(" ")
    for key, lbl, mime, cands in MIME_SLOTS:
        cur = defs.get(key, "unset")
        if mime == "nc_terminal":
            opts = " ".join(SelectPill(c, f"cc_apps:set_term:{c}", selected=(cur == c)) for c in cands)
            box.add_kv(lbl, opts)
        else:
            installed_cands = [c for c in cands if c in avail_desk]
            btns = " ".join(SelectPill(c.replace(".desktop", "")[:14], f"cc_apps:set_mime:{mime}:{c}", selected=(cur == c)) for c in installed_cands[:3])
            cur_short = cur.replace(".desktop", "")
            box.add_kv(lbl, f"{Txt(cur_short, role='focus' if cur != 'unset' else 'inactive')}  {btns}",
                       right_markup=ActionBtn("Custom..", f"cc_apps:prompt_mime:{mime}", tone="neutral"), requires=["xdg-mime"])
    box.add_row(" ")
    cards.append(box)
    return cards


# =====================================================================
# PUBLIC ENTRY POINTS
# =====================================================================

def build_cc_apps_cards(cc_panel, canvas: Canvas, active_sub: str = "floating") -> list:
    is_it = (get_active_language() == "it")
    if active_sub == "mime":
        return _build_mime_standard(cc_panel, canvas, is_it)
    if active_sub == "autostart":
        return _build_autostart_standard(cc_panel, canvas, is_it)
    return _build_floating_standard(cc_panel, canvas, is_it)


def handle_cc_apps_link(cc_panel, uri: str) -> bool:
    if uri.startswith("cc_flt:"):
        act = uri[len("cc_flt:"):]
        if act == "open_tui":
            probe.open_terminal(["bash", resolve_script_path("nc_util_floater.sh", fallback_alias="nc-niri-floater.sh")])
            return True
        if act == "prompt_add":
            cc_panel.request_prompt("cc_flt_add", "Application app-id to open floating:", "", title="Add Floating Rule", required=True)
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
        _set_apps_fb(True, f"Default NC-UI terminal set to {term}")
    elif act.startswith("set_mime:"):
        _, mime, desktop = act.split(":", 2)
        ok, _o, err = probe.run_cmd_full(["xdg-mime", "default", desktop, mime], timeout=2.0)
        _set_apps_fb(ok, f"Set {desktop} for {mime}" if ok else (err or "xdg-mime failed"))
    elif act.startswith("prompt_mime:"):
        mime = act.split(":", 1)[1]
        cc_panel.request_prompt(f"cc_apps_mime|{mime}", f".desktop handler for {mime}:", "", title=f"Default for {mime}", required=True)
        return True
    elif act == "prompt_spawn":
        cc_panel.request_prompt("cc_apps_spawn", "Command & args to spawn at startup (e.g. swaybg -i ...):", "", title="+ spawn-at-startup", required=True)
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
