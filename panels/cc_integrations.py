#!/usr/bin/env python3
import json
import os
import re
from core.state import STATE
from core.i18n import get_active_language
from core import probe, log
from ui.canvas import Canvas
from ui.kit import Box, Badge, Txt, SelectPill, ActionBtn, StateBadge
from ui.widgets import CustomWidgetCard
from panels.display import NiriOutputMap, resolve_script_path, run_desktop_cli

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NC_DESKTOP_BASE = os.path.expanduser("~/.local/nc-desktop")
WALL_DIR = os.path.join(NC_DESKTOP_BASE, "wallpapers")
STATE_DIR = os.path.join(NC_DESKTOP_BASE, "state")
PROF_DIR = os.path.join(NC_DESKTOP_BASE, "profiles")
NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")


def get_niri_outputs_data() -> dict:
    ok, out = probe.run_cmd(["niri", "msg", "-j", "outputs"], timeout=1.5)
    if ok and out:
        try:
            return json.loads(out)
        except Exception:
            pass
    return {}


# =====================================================================
# 1. SEZIONE DISPLAY NEL CONTROL CENTER
# =====================================================================

def build_cc_display_cards(cc_panel, canvas: Canvas, active_sub: str) -> list:
    is_it = (get_active_language() == "it")

    outputs_data = get_niri_outputs_data()
    cards = []

    if active_sub == "layout":
        hdr = Box(
            "Multi-Monitor Spatial Topology (Niri)" if not is_it else "Topologia Spaziale Multi-Monitor (Niri)",
            icon="\uf108",
            actions=[
                ActionBtn("Auto Position All", "cc_desk:pos_auto_all", tone="neutral"),
                ActionBtn("TUI Dashboard", "cc_desk:open_tui", tone="select"),
            ],
            highlighted=True,
        )
        hdr.add_row(
            f"  {Txt('Drag monitors below to snap positions or use quick resolution controls.' if not is_it else 'Trascina i monitor nella mappa per allinearli o usa i controlli rapidi.', role='schema')}"
        )
        cards.append(hdr)

        if outputs_data:
            niri_map = NiriOutputMap(
                outputs_data,
                refresh_cb=(lambda: cc_panel.shell.refresh_view() if cc_panel.shell else None),
                map_width=int(canvas.total_px - 28),
            )
            cards.append(CustomWidgetCard(niri_map))

        for out_name, info in sorted(outputs_data.items()):
            logical = info.get("logical") or {}
            modes = info.get("modes") or []
            cur_idx = info.get("current_mode")
            cur_str = f"{logical.get('width', 0)}x{logical.get('height', 0)}"
            if cur_idx is not None and 0 <= cur_idx < len(modes):
                cm = modes[cur_idx]
                rr = cm.get("refresh_rate", 0)
                hz = rr / 1000.0 if rr > 1000 else float(rr)
                cur_str = f"{cm.get('width')}x{cm.get('height')}@{hz:.0f}Hz"

            o_box = Box(
                f"{out_name} \u2014 {info.get('model', 'Display')}",
                icon="\uf26c",
                actions=[
                    ActionBtn("Set Max / Native Res", f"cc_desk:res_max:{out_name}", tone="apply", active=True),
                    ActionBtn("Auto Pos", f"cc_desk:pos_auto:{out_name}", tone="neutral"),
                ],
            )
            meta_s = f"Pos: ({logical.get('x', 0)}, {logical.get('y', 0)})  Scale: {logical.get('scale', 1.0)}x"
            o_box.add_kv(
                "Current Mode" if not is_it else "Modalit\u00e0 Attuale",
                f"{Txt(cur_str, role='focus')}   {Txt(meta_s, role='schema')}",
                key_width=16,
            )
            cards.append(o_box)
        return cards

    for out_name, info in sorted(outputs_data.items()):
        logical = info.get("logical") or {}
        cur_scale = float(logical.get("scale", 1.0))
        modes = info.get("modes") or []
        cur_idx = info.get("current_mode")

        s_box = Box(
            f"{out_name} ({info.get('model', 'Monitor')})",
            icon="\uf108",
            actions=[
                Badge(f"Scale {cur_scale:g}x", style="info"),
                ActionBtn("Custom Scale..", f"cc_desk:scale_prompt:{out_name}", tone="neutral"),
            ],
            elected=True,
        )
        scale_btns = "  ".join(
            SelectPill(
                f"{sv:g}x",
                f"cc_desk:scale:{out_name}:{sv:g}",
                selected=(abs(cur_scale - sv) < 0.03),
            )
            for sv in (1.0, 1.25, 1.5, 1.75, 2.0)
        )
        s_box.add_row(f"  {Txt('Scaling Factor:' if not is_it else 'Fattore di Scala:', role='schema')}   {scale_btns}")

        s_box.add_section("Supported Hardware Modes (nc_util_desktop.sh -r)" if not is_it else "Modalit\u00e0 Video Supportate (nc_util_desktop.sh -r)")
        seen = set()
        mode_btns = []
        for idx, m in enumerate(modes):
            w, h = m.get("width", 0), m.get("height", 0)
            rr = m.get("refresh_rate", 0)
            hz = rr / 1000.0 if rr > 1000 else float(rr)
            m_spec = f"{w}x{h}@{hz:.0f}"
            if m_spec in seen:
                continue
            seen.add(m_spec)
            is_cur = (idx == cur_idx)
            mode_btns.append(
                SelectPill(
                    f"{m_spec}Hz",
                    f"cc_desk:res:{out_name}:{m_spec}",
                    selected=is_cur,
                )
            )
            if len(mode_btns) >= 8:
                break

        for i in range(0, len(mode_btns), 4):
            s_box.add_row("  " + "  ".join(mode_btns[i : i + 4]))
        cards.append(s_box)

    return cards


# =====================================================================
# 2. SEZIONE PERSONALIZATION -> WALLPAPER & MATRIX (nc_util_desktop.sh)
# =====================================================================

def build_cc_desktop_wallpaper_cards(cc_panel, canvas: Canvas) -> list:
    is_it = (get_active_language() == "it")

    outputs_data = get_niri_outputs_data()
    out_names = sorted(outputs_data.keys())
    target_out = str(STATE.get("desktop", "target_out", "all"))
    if target_out != "all" and target_out not in out_names:
        target_out = "all"

    cards = []

    out_btns = [
        SelectPill("ALL", "cc_desk:target:all", selected=(target_out == "all"))
    ] + [
        SelectPill(o, f"cc_desk:target:{o}", selected=(target_out == o))
        for o in out_names
    ]

    set_img_lbl = "\uf03e Imposta immagine..." if is_it else "\uf03e Set image..."

    ctrl_box = Box(
        "NC-Desktop Wallpaper & Matrix Engine" if not is_it else "Motore Sfondi, Slideshow & Matrix (nc_util_desktop.sh)",
        icon="\uf03e",
        actions=[
            ActionBtn("Swap 1\u21942", "cc_desk:swap", tone="select"),
            ActionBtn("Restore", "cc_desk:restore", tone="neutral"),
            ActionBtn("Stop Daemons", "cc_desk:stop", tone="danger_soft"),
        ],
        elected=True,
    )
    ctrl_box.add_row(f"  {Txt('Target Output:' if not is_it else 'Schermo Target:', role='schema')}  " + "  ".join(out_btns))
    ctrl_box.add_section("Quick Actions & Slideshow Transport" if not is_it else "Azioni Rapide & Controlli Slideshow")
    ctrl_box.add_row(
        f"  {ActionBtn(set_img_lbl, 'cc_desk:prompt_img', tone='select', active=True)}  "
        f"{ActionBtn('\uf005 Save to Favorites', 'cc_desk:fav', tone='neutral')}  "
        f"{ActionBtn('\uf051 Next', 'cc_desk:next', tone='select')}  "
        f"{ActionBtn('\uf04c Pause', 'cc_desk:pause', tone='warn')}  "
        f"{ActionBtn('\uf04b Resume', 'cc_desk:resume', tone='apply')}"
    )
    cards.append(ctrl_box)

    cm_col = str(STATE.get("desktop", "cm_color", "green"))
    cm_spd = str(STATE.get("desktop", "cm_speed", "4"))
    cm_jp = bool(STATE.get("desktop", "cm_jp", True))

    cm_box = Box(
        "CMatrix Native Wayland Shader (ASCII / Katakana JP)",
        icon="\uf120",
        actions=[
            SelectPill(
                "\uf00c Katakana JP" if cm_jp else "ASCII Standard",
                "cc_desk:cm_toggle_jp",
                selected=cm_jp,
            ),
            ActionBtn("\uf04b Launch Matrix", "cc_desk:cm_launch", tone="apply", active=True),
        ],
    )
    col_btns = " ".join(
        SelectPill(c.title(), f"cc_desk:cm_col:{c}", selected=(cm_col == c))
        for c in ("green", "cyan", "blue", "red", "magenta", "yellow", "white")
    )
    spd_btns = " ".join(
        SelectPill(str(s), f"cc_desk:cm_spd:{s}", selected=(cm_spd == str(s)))
        for s in (1, 2, 4, 6, 8)
    )
    cm_box.add_row(f"  {Txt('Color:', role='schema')}  {col_btns}")
    cm_box.add_row(f"  {Txt('Speed:', role='schema')}  {spd_btns}")
    cards.append(cm_box)

    uni_box = Box(
        "Unicolor & Slideshow Folders (~/.local/nc-desktop/wallpapers)",
        icon="\uf53f",
        actions=[
            ActionBtn(set_img_lbl, "cc_desk:prompt_img", tone="select"),
            ActionBtn("Custom Hex..", "cc_desk:prompt_hex", tone="neutral"),
        ],
    )
    uni_presets = " ".join(
        ActionBtn(c, f"cc_desk:solid:{c}", tone="neutral")
        for c in ("cyber", "anthracite", "mocha", "crust", "black", "lavender", "mauve", "sky")
    )
    uni_box.add_row(f"  {Txt('Unicolor:', role='schema')}  {uni_presets}")

    dirs = []
    if os.path.exists(WALL_DIR):
        try:
            dirs = sorted([d for d in os.listdir(WALL_DIR) if os.path.isdir(os.path.join(WALL_DIR, d))])
        except Exception:
            pass
    if dirs:
        uni_box.add_section("Start Folder Slideshow" if not is_it else "Avvia Slideshow da Cartella")
        dir_btns = [ActionBtn(f"\uf07b {d}", f"cc_desk:slide:{d}", tone="select") for d in dirs[:8]]
        for i in range(0, len(dir_btns), 4):
            uni_box.add_row("  " + "  ".join(dir_btns[i : i + 4]))

    profs = []
    if os.path.exists(PROF_DIR):
        try:
            profs = sorted([d for d in os.listdir(PROF_DIR) if os.path.isdir(os.path.join(PROF_DIR, d))])
        except Exception:
            pass
    uni_box.add_section(
        "Saved Desktop Profiles" if not is_it else "Profili Desktop Salvati",
        actions=[ActionBtn("+ Save Profile", "cc_desk:prompt_save_prof", tone="apply")],
    )
    if not profs:
        uni_box.add_row(f"  {Txt('No saved profiles yet.' if not is_it else 'Nessun profilo salvato.', role='inactive')}")
    else:
        p_btns = "  ".join(ActionBtn(f"\uf07c {p}", f"cc_desk:load_prof:{p}", tone="select") for p in profs[:8])
        uni_box.add_row(f"  {p_btns}")

    cards.append(uni_box)
    return cards


# =====================================================================
# 3. SEZIONE APPS -> NIRI FLOATING WINDOW MANAGER (nc_util_floater.sh)
# =====================================================================

def get_niri_floating_rules() -> list[str]:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return []
    try:
        with open(NIRI_CONFIG_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        pattern = re.compile(
            r'window-rule\s*\{\s*match\s+app-id="([^"]+)"\s+open-floating\s+true\s*\}',
            re.MULTILINE,
        )
        return sorted(set(m.group(1).replace("\\\\.", ".") for m in pattern.finditer(content)))
    except Exception as e:
        log.error(f"get_niri_floating_rules error: {e}")
        return []


def get_niri_active_windows() -> list[dict]:
    ok, out = probe.run_cmd(["niri", "msg", "-j", "windows"], timeout=1.2)
    if not ok or not out:
        return []
    try:
        wins = json.loads(out)
        seen = set()
        res = []
        for w in wins:
            app_id = str(w.get("app_id") or "").strip()
            if not app_id or app_id in seen:
                continue
            seen.add(app_id)
            res.append({"app_id": app_id, "title": str(w.get("title") or "")})
        res.sort(key=lambda x: x["app_id"].lower())
        return res
    except Exception:
        return []


def toggle_niri_floating_rule(app_id: str) -> bool:
    clean_id = str(app_id or "").strip()
    if not clean_id:
        return False
    esc_app = clean_id.replace(".", "\\\\.")
    os.makedirs(os.path.dirname(NIRI_CONFIG_FILE), exist_ok=True)
    content = ""
    if os.path.exists(NIRI_CONFIG_FILE):
        try:
            with open(NIRI_CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            pass

    rule_re = re.compile(
        r'\n*window-rule\s*\{\s*match\s+app-id="' + re.escape(esc_app) + r'"\s*open-floating\s+true\s*\}',
        re.MULTILINE,
    )
    if rule_re.search(content):
        new_content = rule_re.sub("", content)
    else:
        block = f'\nwindow-rule {{\n    match app-id="{esc_app}"\n    open-floating true\n}}\n'
        new_content = content.rstrip() + "\n" + block

    try:
        with open(NIRI_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    except Exception as e:
        log.error(f"toggle_niri_floating_rule write error: {e}")
        return False


def build_cc_floater_cards(cc_panel, canvas: Canvas) -> list:
    is_it = (get_active_language() == "it")

    floating_ids = get_niri_floating_rules()
    active_wins = get_niri_active_windows()

    cards = []
    hdr_box = Box(
        "Niri Floating Window Rules (nc_util_floater.sh)" if not is_it else "Regole Finestre Galleggianti Niri (nc_util_floater.sh)",
        icon="\uf2d2",
        actions=[
            ActionBtn("+ Add App-ID", "cc_flt:prompt_add", tone="apply"),
            ActionBtn("Open Terminal TUI", "cc_flt:open_tui", tone="select"),
        ],
        elected=True,
    )
    hdr_box.add_row(
        f"  {Txt('Config:', role='schema')} {Txt(NIRI_CONFIG_FILE, role='focus')}   "
        f"{Txt('Rules:', role='schema')} {Txt(str(len(floating_ids)), role='intent')}"
    )
    cards.append(hdr_box)

    act_box = Box(
        "Running Applications (Click to Toggle Floating Rule)" if not is_it else "Applicazioni Attive (Clicca per commutare Floating)",
        icon="\uf2d0",
    )
    if not active_wins:
        act_box.add_row(f"  {Txt('No active windows detected.' if not is_it else 'Nessuna finestra attiva rilevata.', role='inactive')}")
    else:
        for w in active_wins:
            aid = w["app_id"]
            is_flt = (aid in floating_ids)
            st_badge = StateBadge(True, "FLOATING", on_tone="warn") if is_flt else StateBadge(True, "TILED", on_tone="select")
            toggle_btn = (
                ActionBtn("Remove Float", f"cc_flt:toggle:{aid}", tone="danger_soft")
                if is_flt
                else ActionBtn("Set Floating", f"cc_flt:toggle:{aid}", tone="apply")
            )
            title_short = canvas.truncate_plain(w["title"], 28)
            act_box.add_row(
                f"  {st_badge}  {Txt(aid, role='focus')}  {Txt(f'({title_short})', role='schema')}",
                right_markup=toggle_btn,
            )
    cards.append(act_box)

    rules_box = Box(
        "Configured Floating Rules in config.kdl" if not is_it else "Regole Floating Attive in config.kdl",
        icon="\uf0c9",
    )
    if not floating_ids:
        rules_box.add_row(f"  {Txt('No floating rules configured in config.kdl.' if not is_it else 'Nessuna regola floating presente in config.kdl.', role='inactive')}")
    else:
        for fid in floating_ids:
            rules_box.add_row(
                f"  {Txt('\u25cf', role='intent')}  {Txt(fid, role='focus')}",
                right_markup=ActionBtn("Remove Rule" if not is_it else "Rimuovi Regola", f"cc_flt:toggle:{fid}", tone="danger_soft"),
            )
    cards.append(rules_box)
    return cards


def handle_cc_integrations_link(cc_panel, uri: str) -> bool:
    is_it = (get_active_language() == "it")
    if uri.startswith("cc_desk:"):
        act = uri[len("cc_desk:"):]
        target_out = str(STATE.get("desktop", "target_out", "all"))
        if act == "open_tui":
            script = resolve_script_path("nc_util_desktop.sh", fallback_alias="nc-desktop.sh")
            probe.open_terminal(["bash", script, "-I"])
        elif act == "pos_auto_all":
            run_desktop_cli(["-o", "all", "--pos", "auto"])
        elif act.startswith("pos_auto:"):
            run_desktop_cli(["-o", act.split(":", 1)[1], "--pos", "auto"])
        elif act.startswith("res_max:"):
            run_desktop_cli(["-o", act.split(":", 1)[1], "-r", "max"])
        elif act.startswith("res:"):
            _, out_n, m_spec = act.split(":", 2)
            run_desktop_cli(["-o", out_n, "-r", m_spec])
        elif act.startswith("scale:"):
            _, out_n, sc_val = act.split(":", 2)
            run_desktop_cli(["-o", out_n, "--scale", sc_val])
        elif act.startswith("scale_prompt:"):
            out_n = act.split(":", 1)[1]
            cc_panel.request_prompt(f"cc_desk_scale|{out_n}", f"Scale factor for {out_n} (e.g. 1.25):", "1.0", title=f"Scale {out_n}", required=True)
            return True
        elif act.startswith("target:"):
            STATE.set("desktop", "target_out", act.split(":", 1)[1])
        elif act == "swap":
            run_desktop_cli(["--swap", "1", "2"])
        elif act in ("restore", "stop", "next", "pause", "resume", "fav"):
            run_desktop_cli(["-o", target_out, f"--{act}"])
        elif act == "cm_toggle_jp":
            cur = bool(STATE.get("desktop", "cm_jp", True))
            STATE.set("desktop", "cm_jp", not cur)
        elif act.startswith("cm_col:"):
            STATE.set("desktop", "cm_color", act.split(":", 1)[1])
        elif act.startswith("cm_spd:"):
            STATE.set("desktop", "cm_speed", act.split(":", 1)[1])
        elif act == "cm_launch":
            col = str(STATE.get("desktop", "cm_color", "green"))
            spd = str(STATE.get("desktop", "cm_speed", "4"))
            jp = bool(STATE.get("desktop", "cm_jp", True))
            cmd = ["-o", target_out, "-c", "-C", col, "-u", spd] + (["-j"] if jp else [])
            run_desktop_cli(cmd)
        elif act.startswith("solid:"):
            run_desktop_cli(["-o", target_out, "--unicolor", act.split(":", 1)[1]])
        elif act.startswith("slide:"):
            run_desktop_cli(["-o", target_out, "-d", act.split(":", 1)[1]])
        elif act.startswith("load_prof:"):
            run_desktop_cli(["--load-profile", act.split(":", 1)[1]])
        elif act == "prompt_img":
            ph = "Inserisci il percorso dell'immagine..." if is_it else "Enter image path..."
            cc_panel.request_prompt("cc_desk_img", ph, "", title=f"\uf03e {target_out}", required=True)
            return True
        elif act == "prompt_hex":
            cc_panel.request_prompt("cc_desk_hex", "Solid color name or #hex (e.g. #18181a):", "#18181a", title="Unicolor Background", required=True)
            return True
        elif act == "prompt_save_prof":
            cc_panel.request_prompt("cc_desk_save_prof", "Profile name (letters/numbers/-/_):", "", title="Save Desktop Profile", required=True)
            return True

        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if uri.startswith("cc_flt:"):
        act = uri[len("cc_flt:"):]
        if act == "open_tui":
            script = resolve_script_path("nc_util_floater.sh", fallback_alias="nc-niri-floater.sh")
            probe.open_terminal(["bash", script])
            return True
        if act == "prompt_add":
            cc_panel.request_prompt("cc_flt_add", "Application app-id to open floating:", "", title="Add Niri Floating Rule", required=True)
            return True
        if act.startswith("toggle:"):
            toggle_niri_floating_rule(act.split(":", 1)[1])
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True

    return False


def handle_cc_integrations_prompt(cc_panel, action_id: str, text: str) -> bool:
    val = str(text or "").strip()
    if not val:
        return False
    target_out = str(STATE.get("desktop", "target_out", "all"))
    if action_id.startswith("cc_desk_scale|"):
        out_n = action_id.split("|", 1)[1]
        run_desktop_cli(["-o", out_n, "--scale", val])
        return True
    if action_id == "cc_desk_img":
        run_desktop_cli(["-o", target_out, "-i", val])
        return True
    if action_id == "cc_desk_hex":
        run_desktop_cli(["-o", target_out, "--unicolor", val])
        return True
    if action_id == "cc_desk_save_prof":
        run_desktop_cli(["--save-profile", val])
        return True
    if action_id == "cc_flt_add":
        toggle_niri_floating_rule(val)
        return True
    return False
