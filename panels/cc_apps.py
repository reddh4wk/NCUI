#!/usr/bin/env python3
import json
import os
import re
import time
from core import probe
from core.config import CONFIG, _update_toml_key_in_section, get_cc_panel_style
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


def _build_floating_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    floating_ids = get_niri_floating_rules()
    active_wins = get_niri_active_windows()
    cards = _feedback_cards()

    b = Box(
        "COMPOSITOR WINDOW-RULE FILTER // FLOATING MATRIX",
        icon="\uf2d2",
        actions=[ActionBtn("+ INJECT APP-ID", "cc_flt:prompt_add", tone="apply", active=True)],
        elected=True,
    )
    b.add_row(" ")
    b.add_row(f"  {Txt('[KDL RULE ENGINE] ──►', role='intent')} {Badge(f'{len(floating_ids)} FLOATING', style='warn')}  {ActionBtn('LAUNCH FLOATER TUI', 'cc_flt:open_tui', tone='select')}")
    b.add_section("CONFIGURED FLOATING MATCHERS")
    for fid in floating_ids:
        b.add_row(f"  {Txt('┣━ [FLOAT]─►', role='attention')} {Txt(fid, role='focus')}", right_markup=ActionBtn("REMOVE", f"cc_flt:toggle:{fid}", tone="danger_soft"))
    if active_wins:
        b.add_section("LIVE WAYLAND CLIENTS")
        for w in active_wins[:8]:
            aid = w["app_id"]
            is_flt = aid in floating_ids
            b.add_row(f"  {Txt('╠═', role='structure')} {StateBadge(is_flt, 'FLOAT', 'TILED', on_tone='warn')} {Txt(aid, role='focus')}", right_markup=ActionBtn("TOGGLE", f"cc_flt:toggle:{aid}", tone="select"))
    b.add_row(" ")
    cards.append(b)
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


def _build_mime_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    defs = _query_mime_defaults()
    cards = _feedback_cards()
    b = Box("XDG-MIME DISPATCHER SWITCHBOARD // DEFAULT HANDLERS", icon="\uf1b2", elected=True)
    b.add_row(" ")
    for key, lbl, mime, cands in MIME_SLOTS:
        cur = defs.get(key, "unset").replace(".desktop", "")
        if mime == "nc_terminal":
            btns = " ".join(SelectPill(c.upper(), f"cc_apps:set_term:{c}", selected=(cur == c)) for c in cands)
            b.add_row(f"  {Txt(f'[{lbl:<16}]─►', role='schema')} {btns}")
        else:
            b.add_row(f"  {Txt(f'[{lbl:<16}]─►', role='schema')} {Txt(cur, role='vitality' if cur != 'unset' else 'inactive')}",
                      right_markup=ActionBtn("ASSIGN..", f"cc_apps:prompt_mime:{mime}", tone="select"))
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# SUBTAB 3: AUTOSTART & SYSTEMD SERVICES
# =====================================================================

def _build_autostart_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    spawns = _read_niri_spawns()
    user_svcs = _read_systemd_services(user=True)
    sys_svcs = _read_systemd_services(user=False)
    cards = _feedback_cards()

    sp_box = Box(
        f"Niri spawn-at-startup ({len(spawns)})",
        icon="\uf135",
        actions=[ActionBtn("+ Add Startup..", "cc_apps:prompt_spawn", tone="apply", active=True)],
        elected=True,
    )
    sp_box.add_row(" ")
    if not spawns:
        sp_box.add_row(f"  {Txt('No spawn-at-startup rules found.', role='inactive')}")
    else:
        for sp in spawns[:12]:
            sp_idx = sp["idx"]
            st = StateBadge(not sp["disabled"], "ACTIVE", "DISABLED")
            t_btn = ActionBtn("Disable" if not sp["disabled"] else "Enable", f"cc_apps:toggle_spawn:{sp_idx}", tone="warn" if not sp["disabled"] else "apply")
            sp_box.add_row(
                f"  {st}  {Txt(canvas.truncate_plain(sp['cmd'], 42), role='focus' if not sp['disabled'] else 'inactive')}",
                right_markup=f"{t_btn} {ActionBtn('x', f'cc_apps:del_spawn:{sp_idx}', tone='danger_soft')}",
            )
    sp_box.add_row(" ")
    cards.append(sp_box)

    sv_box = Box(f"Servizi Utente (systemctl --user) [{len(user_svcs)}]" if is_it else f"User Services (systemctl --user) [{len(user_svcs)}]", icon="\uf085")
    sv_box.add_row(" ")
    if not user_svcs:
        sv_box.add_row(f"  {Txt('No user systemd units active (or dbus unavailable).', role='inactive')}")
    else:
        rows = []
        for sv in user_svcs[:10]:
            is_act = sv["active"] == "active"
            u_n = sv["unit"]
            rows.append([
                Txt(u_n[:28], role="focus" if is_act else "inactive"),
                StateBadge(is_act, sv["sub"].upper(), sv["active"].upper()),
                f"{ActionBtn('Restart', f'cc_apps:svc_user:restart:{u_n}', tone='select')} {ActionBtn('Stop', f'cc_apps:svc_user:stop:{u_n}', tone='danger_soft')}",
            ])
        sv_box.add_table(headers=["UNIT", "STATE", "ACTIONS"], widths=[30, 14, "flex"], rows=rows, aligns=["left", "left", "right"])

    def _build_sys_ro(b: Box):
        if not sys_svcs:
            b.add_row(f"  {Txt('No system services queried.', role='inactive')}")
        else:
            s_rows = [[Txt(s["unit"][:26], role="val"), StateBadge(s["active"] == "active", s["sub"].upper(), "FAILED", on_tone="ok"), Txt(canvas.truncate_plain(s["desc"], 28), role="schema")] for s in sys_svcs[:12]]
            b.add_table(headers=["SYSTEM UNIT", "STATE", "DESCRIPTION"], widths=[28, 12, "flex"], rows=s_rows)

    sv_box.add_collapsible(cc_panel, "apps_sys_svcs", f"Ispettore Servizi di Sistema (Read-Only) [{len(sys_svcs)}]" if is_it else f"System Services Inspector (Read-Only) [{len(sys_svcs)}]", _build_sys_ro)
    sv_box.add_row(" ")
    cards.append(sv_box)
    return cards


def _build_autostart_cyberpunk(cc_panel, canvas: Canvas, is_it: bool) -> list:
    spawns = _read_niri_spawns()
    user_svcs = _read_systemd_services(user=True)
    cards = _feedback_cards()

    b = Box(
        "SESSION INIT SEQUENCER // NIRI SPAWN & SYSTEMD DAEMONS",
        icon="\uf135",
        actions=[ActionBtn("+ INJECT SPAWN", "cc_apps:prompt_spawn", tone="apply", active=True)],
        elected=True,
    )
    b.add_row(" ")
    b.add_row(f"  {Txt('[STAGE 1: NIRI SPAWN-AT-STARTUP]', role='intent')}")
    for sp in spawns[:10]:
        sp_idx = sp["idx"]
        st = StateBadge(not sp["disabled"], "RUN", "MASK")
        b.add_row(f"  {Txt('┣━►', role='structure')} {st} {Txt(canvas.truncate_plain(sp['cmd'], 42), role='focus' if not sp['disabled'] else 'inactive')}",
                  right_markup=f"{ActionBtn('TOGGLE', f'cc_apps:toggle_spawn:{sp_idx}', tone='neutral')} {ActionBtn('DEL', f'cc_apps:del_spawn:{sp_idx}', tone='danger_soft')}")
    b.add_section(f"STAGE 2: SYSTEMD USER BUS ({len(user_svcs)} UNITS)")
    for sv in user_svcs[:6]:
        u_n = sv["unit"]
        b.add_row(f"  {Txt('╠═', role='structure')} {StateBadge(sv['active']=='active', sv['sub'].upper(), 'DOWN')} {Txt(u_n[:32], role='val')}",
                  right_markup=f"{ActionBtn('RESTART', f'cc_apps:svc_user:restart:{u_n}', tone='select')} {ActionBtn('STOP', f'cc_apps:svc_user:stop:{u_n}', tone='danger_soft')}")
    b.add_row(" ")
    cards.append(b)
    return cards


# =====================================================================
# PUBLIC ENTRY POINTS
# =====================================================================

def build_cc_apps_cards(cc_panel, canvas: Canvas, active_sub: str = "floating") -> list:
    is_it = (get_active_language() == "it")
    style = get_cc_panel_style()
    if active_sub == "mime":
        return _build_mime_cyberpunk(cc_panel, canvas, is_it) if style == "cyberpunk" else _build_mime_standard(cc_panel, canvas, is_it)
    if active_sub == "autostart":
        return _build_autostart_cyberpunk(cc_panel, canvas, is_it) if style == "cyberpunk" else _build_autostart_standard(cc_panel, canvas, is_it)
    return _build_floating_cyberpunk(cc_panel, canvas, is_it) if style == "cyberpunk" else _build_floating_standard(cc_panel, canvas, is_it)


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
