#!/usr/bin/env python3
"""NC-UI Bootloader & Limine Studio Engine (`panels/cc_engine_boot.py`)."""

import glob
import json
import os
import re
import shlex
import time
from core import probe
from core.i18n import t
from core.privileges import get_privileged_action
from core.resilience import read_env_kv as _read_env_kv, run_helper_async as _run_helper_async
from core.state import STATE
from ui.kit import FeedbackBox, start_inline_input

_LIMINE_CANDIDATES = ("/boot/limine.conf", "/efi/limine/limine.conf", "/boot/limine/limine.conf")
_DEFAULT_ENTRIES = ["linux-cachyos", "linux-cachyos-hardened", "Golden Image Recovery", "Windows"]


def _set_sys_fb(ok: bool, msg: str) -> None:
    STATE.set("system", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _feedback_cards() -> list:
    fb = STATE.get("system", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return [FeedbackBox(t("cc_system.system_control_status"), bool(fb["ok"]), fb["msg"])]
    return []


def _norm_hex6(raw: str, default: str = "#ffffff") -> str:
    s = str(raw or "").strip().lstrip("#")
    if len(s) == 8 and re.match(r"^[0-9a-fA-F]{8}$", s):
        s = s[2:]
    if len(s) == 6 and re.match(r"^[0-9a-fA-F]{6}$", s):
        return f"#{s.lower()}"
    return default


def _parse_bool_yesno(raw: str, default: bool = False) -> bool:
    s = str(raw or "").strip().lower()
    if s in ("yes", "true", "1", "on"):
        return True
    if s in ("no", "false", "0", "off"):
        return False
    return default


_LIMINE_THEME_PRESETS: dict[str, dict] = {
    "fallout": {
        "term_foreground": "#67d97a",
        "term_foreground_bright": "#ffffff",
        "term_background_rgb": "#000000",
        "term_background_alpha_pct": 50,
        "interface_branding_color": "#67d97a",
        "backdrop": "#000000",
        "term_palette": ["#000000", "#ff0000", "#67d97a", "#ffff00", "#0000ff", "#ff00ff", "#67d97a", "#ffffff"],
        "term_palette_bright": ["#333333", "#ff0000", "#67d97a", "#ffff00", "#0000ff", "#ff00ff", "#67d97a", "#ffffff"],
    },
    "nightcity": {
        "term_foreground": "#8c9bba",
        "term_foreground_bright": "#00f0ff",
        "term_background_rgb": "#080a0f",
        "term_background_alpha_pct": 80,
        "interface_branding_color": "#00f0ff",
        "backdrop": "#080a0f",
        "term_palette": ["#080a0f", "#ff2442", "#00f57a", "#ffe619", "#3b82f6", "#ff2a9d", "#00f0ff", "#dce6f7"],
        "term_palette_bright": ["#43495c", "#ff4d66", "#33ff99", "#ffeb4d", "#60a5fa", "#ff55b2", "#33f3ff", "#ffffff"],
    },
    "amber": {
        "term_foreground": "#ffb000",
        "term_foreground_bright": "#fff1c2",
        "term_background_rgb": "#140d02",
        "term_background_alpha_pct": 80,
        "interface_branding_color": "#ffb000",
        "backdrop": "#0e0901",
        "term_palette": ["#140d02", "#ff4422", "#a3d900", "#ffb000", "#cc7a00", "#ff5577", "#ffd040", "#f2dec4"],
        "term_palette_bright": ["#4a3512", "#ff6644", "#c2ff1a", "#ffd040", "#ff991a", "#ff8899", "#ffe580", "#fff1c2"],
    },
    "arch": {
        "term_foreground": "#d8dee9",
        "term_foreground_bright": "#1793d1",
        "term_background_rgb": "#0d1117",
        "term_background_alpha_pct": 85,
        "interface_branding_color": "#1793d1",
        "backdrop": "#0d1117",
        "term_palette": ["#0d1117", "#f85149", "#3fb950", "#d29922", "#1793d1", "#bc8cff", "#39c5cf", "#d8dee9"],
        "term_palette_bright": ["#484f58", "#ff7b72", "#56d364", "#e3b341", "#38bdf8", "#d2a8ff", "#56d4dd", "#ffffff"],
    },
}


def _parse_limine_conf_text(raw: str) -> dict:
    def_pal = list(_LIMINE_THEME_PRESETS["arch"]["term_palette"])
    def_pal_b = list(_LIMINE_THEME_PRESETS["arch"]["term_palette_bright"])
    model = {
        "timeout": "5",
        "default_entry": "1",
        "remember_last_entry": False,
        "verbose": False,
        "quiet": False,
        "interface_resolution": "",
        "interface_branding": "",
        "interface_branding_color": "#00aaaa",
        "interface_help_hidden": False,
        "wallpaper": "",
        "wallpaper_style": "stretched",
        "backdrop": "#000000",
        "term_margin": 0,
        "term_margin_set": False,
        "term_margin_gradient": "",
        "term_foreground": "#d8dee9",
        "term_foreground_bright": "#ffffff",
        "term_background_rgb": "#000000",
        "term_background_alpha_pct": 100,
        "term_background_set": False,
        "term_palette": def_pal,
        "term_palette_bright": def_pal_b,
        "extra_globals": [],
        "nodes": [],
    }
    cur_node = None
    in_entries = False
    for raw_ln in (raw or "").splitlines():
        s = raw_ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("/"):
            in_entries = True
            slashes = len(s) - len(s.lstrip("/"))
            rest = s[slashes:]
            expanded = rest.startswith("+")
            title = (rest[1:] if expanded else rest).strip()
            cur_node = {
                "id": len(model["nodes"]),
                "depth": max(1, slashes),
                "expanded": expanded,
                "title": title,
                "comment": "",
                "protocol": "",
                "path_key": "path",
                "path": "",
                "cmdline": "",
                "extra_lines": [],
            }
            model["nodes"].append(cur_node)
            continue
        if ":" not in s:
            if cur_node is not None:
                cur_node["extra_lines"].append(s)
            continue
        k, v = s.split(":", 1)
        kl = k.strip().lower()
        v = v.strip()
        if in_entries and cur_node is not None:
            if kl == "comment":
                if not cur_node["comment"]:
                    cur_node["comment"] = v
                else:
                    cur_node["extra_lines"].append(f"comment: {v}")
            elif kl == "protocol":
                cur_node["protocol"] = v
            elif kl in ("path", "image_path", "kernel_path"):
                cur_node["path_key"] = kl
                cur_node["path"] = v
            elif kl in ("cmdline", "kernel_cmdline"):
                cur_node["cmdline"] = v
            else:
                cur_node["extra_lines"].append(f"{kl}: {v}")
            continue

        if kl == "timeout":
            model["timeout"] = v
        elif kl == "default_entry":
            model["default_entry"] = v
        elif kl == "remember_last_entry":
            model["remember_last_entry"] = _parse_bool_yesno(v, False)
        elif kl == "verbose":
            model["verbose"] = _parse_bool_yesno(v, False)
        elif kl == "quiet":
            model["quiet"] = _parse_bool_yesno(v, False)
        elif kl == "interface_resolution":
            model["interface_resolution"] = v
        elif kl == "interface_branding":
            model["interface_branding"] = v
        elif kl in ("interface_branding_colour", "interface_branding_color"):
            model["interface_branding_color"] = _norm_hex6(v, "#00aaaa")
        elif kl == "interface_help_hidden":
            model["interface_help_hidden"] = _parse_bool_yesno(v, False)
        elif kl == "wallpaper":
            model["wallpaper"] = v
        elif kl == "wallpaper_style" and v.lower() in ("stretched", "centered", "tiled"):
            model["wallpaper_style"] = v.lower()
        elif kl == "backdrop":
            model["backdrop"] = _norm_hex6(v, "#000000")
        elif kl == "term_margin":
            try:
                model["term_margin"] = max(0, min(200, int(v)))
                model["term_margin_set"] = True
            except ValueError:
                pass
        elif kl == "term_margin_gradient":
            model["term_margin_gradient"] = v
        elif kl == "term_foreground":
            model["term_foreground"] = _norm_hex6(v, "#ffffff")
        elif kl == "term_foreground_bright":
            model["term_foreground_bright"] = _norm_hex6(v, "#ffffff")
        elif kl == "term_background":
            hx = v.lstrip("#")
            if len(hx) == 8 and re.match(r"^[0-9a-fA-F]{8}$", hx):
                tt = int(hx[:2], 16)
                model["term_background_alpha_pct"] = max(0, min(100, 100 - int(round((tt / 255.0) * 100.0))))
                model["term_background_rgb"] = f"#{hx[2:].lower()}"
                model["term_background_set"] = True
            elif len(hx) == 6 and re.match(r"^[0-9a-fA-F]{6}$", hx):
                model["term_background_rgb"] = f"#{hx.lower()}"
                model["term_background_alpha_pct"] = 100
                model["term_background_set"] = True
        elif kl == "term_palette":
            cols = [_norm_hex6(c, def_pal[idx]) for idx, c in enumerate(v.split(";")) if idx < 8]
            if cols:
                model["term_palette"] = (cols + def_pal[len(cols):])[:8]
        elif kl == "term_palette_bright":
            cols = [_norm_hex6(c, def_pal_b[idx]) for idx, c in enumerate(v.split(";")) if idx < 8]
            if cols:
                model["term_palette_bright"] = (cols + def_pal_b[len(cols):])[:8]
        else:
            model["extra_globals"].append((kl, v))

    if model["wallpaper"] and not model["term_background_set"]:
        # Limine uses 80000000 (50% opacity) automatically when wallpaper is displayed
        model["term_background_alpha_pct"] = 50
    if model["wallpaper"] and not model["term_margin_set"]:
        model["term_margin"] = 64
    return model


def _limine_compute_node_flags(nodes: list[dict]) -> None:
    for idx, n in enumerate(nodes):
        has_child = idx + 1 < len(nodes) and nodes[idx + 1]["depth"] > n["depth"]
        n["is_dir"] = has_child and not n.get("path")
        n["id"] = idx


def _render_limine_conf(model: dict) -> str:
    lines = ["# Managed by NC-UI Bootloader Studio", ""]
    tp = ";".join(c.lstrip("#") for c in model.get("term_palette", [])[:8])
    tpb = ";".join(c.lstrip("#") for c in model.get("term_palette_bright", [])[:8])
    if tp:
        lines.append(f"term_palette: {tp}")
    if tpb:
        lines.append(f"term_palette_bright: {tpb}")
    alpha_pct = max(0, min(100, int(model.get("term_background_alpha_pct", 100))))
    tt = int(round(((100 - alpha_pct) / 100.0) * 255.0))
    tbg_rgb = _norm_hex6(model.get("term_background_rgb", "#000000"), "#000000").lstrip("#")
    lines.append(f"term_background: {tt:02x}{tbg_rgb}")
    lines.append(f"term_foreground: {_norm_hex6(model.get('term_foreground', '#d8dee9')).lstrip('#')}")
    lines.append(f"term_foreground_bright: {_norm_hex6(model.get('term_foreground_bright', '#ffffff')).lstrip('#')}")
    if str(model.get("interface_branding", "")).strip():
        lines.append(f"interface_branding: {model['interface_branding'].strip()}")
    else:
        lines.append("interface_branding:")
    if model.get("interface_branding_color"):
        lines.append(f"interface_branding_colour: {_norm_hex6(model['interface_branding_color']).lstrip('#')}")
    if model.get("wallpaper"):
        lines.append(f"wallpaper: {model['wallpaper']}")
        if model.get("wallpaper_style") and model["wallpaper_style"] != "stretched":
            lines.append(f"wallpaper_style: {model['wallpaper_style']}")
        if model.get("wallpaper_style") == "centered":
            lines.append(f"backdrop: {_norm_hex6(model.get('backdrop', '#000000')).lstrip('#')}")
    elif _norm_hex6(model.get("backdrop", "#000000")) != "#000000":
        lines.append(f"backdrop: {_norm_hex6(model['backdrop']).lstrip('#')}")

    lines.append("")
    lines.append(f"timeout: {model.get('timeout', '5')}")
    if str(model.get("default_entry", "")).strip():
        lines.append(f"default_entry: {model['default_entry']}")
    lines.append(f"verbose: {'yes' if model.get('verbose') else 'no'}")
    if model.get("quiet"):
        lines.append("quiet: yes")
    lines.append(f"term_margin: {int(model.get('term_margin', 0))}")
    if model.get("term_margin_gradient"):
        lines.append(f"term_margin_gradient: {model['term_margin_gradient']}")
    if model.get("interface_resolution"):
        lines.append(f"interface_resolution: {model['interface_resolution']}")
    lines.append(f"remember_last_entry: {'yes' if model.get('remember_last_entry') else 'no'}")
    if model.get("interface_help_hidden"):
        lines.append("interface_help_hidden: yes")
    for ek, ev in model.get("extra_globals") or []:
        lines.append(f"{ek}: {ev}")

    nodes = model.get("nodes") or []
    _limine_compute_node_flags(nodes)
    for idx, n in enumerate(nodes):
        indent = "  " * max(0, int(n["depth"]) - 1)
        if n["depth"] == 1 and idx > 0:
            lines.append("")
        plus = "+" if (n.get("is_dir") and n.get("expanded")) else ""
        lines.append(f"{indent}{'/' * int(n['depth'])}{plus}{n['title']}")
        opt_ind = indent + "  "
        if n.get("comment"):
            lines.append(f"{opt_ind}comment: {n['comment']}")
        if n.get("protocol"):
            lines.append(f"{opt_ind}protocol: {n['protocol']}")
        if n.get("path"):
            lines.append(f"{opt_ind}{n.get('path_key', 'path')}: {n['path']}")
        if n.get("cmdline"):
            lines.append(f"{opt_ind}cmdline: {n['cmdline']}")
        for ex in n.get("extra_lines") or []:
            lines.append(f"{opt_ind}{ex}")
    lines.append("")
    return "\n".join(lines)


def _clone_limine_model(m: dict) -> dict:
    return json.loads(json.dumps(m))


def _load_limine_studio_data() -> dict:
    conf_path = next((p for p in _LIMINE_CANDIDATES if os.path.exists(p)), "")
    raw = ""
    readable = False
    cached = False
    if conf_path:
        try:
            raw = open(conf_path, "r", encoding="utf-8", errors="ignore").read()
            readable = True
        except Exception:
            pass
    if not raw and os.path.isfile("/var/lib/ncui/limine.conf.cache"):
        try:
            raw = open("/var/lib/ncui/limine.conf.cache", "r", encoding="utf-8", errors="ignore").read()
            cached = True
        except Exception:
            pass
    rescue_env = _read_env_kv("/var/lib/ncui/rescue-status.env")
    if not conf_path and rescue_env.get("LIMINE_CONF_PATH"):
        conf_path = rescue_env["LIMINE_CONF_PATH"]

    live_model = _parse_limine_conf_text(raw)
    _limine_compute_node_flags(live_model["nodes"])

    draft = STATE.get("telemetry", "limine_draft", None)
    is_dirty = isinstance(draft, dict)
    model = draft if is_dirty else live_model
    _limine_compute_node_flags(model["nodes"])

    wp_cache = rescue_env.get("LIMINE_WALLPAPER_CACHE") or "/var/lib/ncui/limine-wallpaper.cache"
    wp_preview = str(STATE.get("telemetry", "limine_wp_preview_path", "") or "")
    if wp_preview and os.path.isfile(wp_preview):
        wp_image_file = wp_preview
    elif os.path.isfile(wp_cache):
        wp_image_file = wp_cache
    else:
        wp_image_file = ""
    if not model.get("wallpaper"):
        wp_image_file = ""

    esp_imgs = [s.strip() for s in str(rescue_env.get("LIMINE_ESP_IMAGES") or "").split(",") if s.strip()]
    priv_r = get_privileged_action("rescue_manage")
    sel_id = int(STATE.get("telemetry", "limine_selected_id", -1) or -1)
    leaf_ids = [n["id"] for n in model["nodes"] if not n.get("is_dir")]
    if sel_id < 0 or sel_id >= len(model["nodes"]):
        sel_id = leaf_ids[0] if leaf_ids else (0 if model["nodes"] else -1)

    return {
        "conf_path": conf_path or "/boot/limine.conf",
        "readable": readable,
        "cached": cached,
        "is_dirty": is_dirty,
        "model": model,
        "wp_image_file": wp_image_file,
        "esp_images": esp_imgs,
        "selected_id": sel_id,
        "priv_rescue_ok": bool(priv_r and priv_r.check_installed()),
        "rescue_busy": str(STATE.get("system", "rescue_busy", "") or ""),
    }


def _stage_limine_draft(mutator) -> None:
    st = _load_limine_studio_data()
    draft = _clone_limine_model(st["model"])
    mutator(draft)
    _limine_compute_node_flags(draft["nodes"])
    STATE.set("telemetry", "limine_draft", draft)



def _probe_limine() -> dict:
    conf_path = next((p for p in _LIMINE_CANDIDATES if os.path.exists(p)), "")
    timeout = "5"
    default_entry = "1"
    entries = []
    readable = False
    cached = False
    if conf_path:
        try:
            raw = open(conf_path, "r", encoding="utf-8", errors="ignore").read()
            readable = True
            m_to = re.search(r"(?m)^\s*timeout\s*:\s*(\d+)", raw)
            if m_to:
                timeout = m_to.group(1)
            m_df = re.search(r"(?m)^\s*default_entry\s*:\s*(\d+)", raw)
            if m_df:
                default_entry = m_df.group(1)
            for ln in raw.splitlines():
                s = ln.strip()
                if s.startswith("/"):
                    entries.append(s.lstrip("/").strip())
        except Exception:
            pass
    rescue_env = _read_env_kv("/var/lib/ncui/rescue-status.env")
    if not readable and rescue_env:
        if rescue_env.get("LIMINE_CONF_PATH"):
            conf_path = rescue_env["LIMINE_CONF_PATH"]
            cached = True
        if rescue_env.get("LIMINE_TIMEOUT"):
            timeout = rescue_env["LIMINE_TIMEOUT"]
        if rescue_env.get("LIMINE_DEFAULT_ENTRY"):
            default_entry = rescue_env["LIMINE_DEFAULT_ENTRY"]
        if rescue_env.get("LIMINE_ENTRIES"):
            entries = [e for e in rescue_env["LIMINE_ENTRIES"].split("||") if e]
            cached = True
    if not entries:
        entries = list(_DEFAULT_ENTRIES)
    ukis = sorted(glob.glob("/boot/EFI/Linux/*.efi") + glob.glob("/efi/EFI/Linux/*.efi"))
    dr_env = _read_env_kv("/var/lib/ncui/dr-status.env") or _read_env_kv("/var/lib/nightcity/dr-status.env")
    if not ukis:
        ukis = [f"/boot/EFI/Linux/{u}" for u in dr_env.get("UKI_NAMES", "").split(",") if u]
    snap_dir = "/var/lib/ncui/snapshots" if os.path.isdir("/var/lib/ncui/snapshots") else "/var/lib/nightcity/snapshots"
    snaps_n = len([p for p in glob.glob(f"{snap_dir}/*") if not os.path.basename(p).startswith(".")]) if os.path.isdir(snap_dir) else 0
    return {
        "conf_path": conf_path or "/boot/limine.conf",
        "readable": readable,
        "cached": cached,
        "timeout": timeout,
        "default_entry": default_entry,
        "entries": entries[:8],
        "ukis": ukis,
        "snaps": snaps_n,
    }


def handle_cc_boot_link(cc_panel, uri: str) -> bool:
    if uri.startswith("cc_set:limine_"):
        parts = uri.split(":", 2)
        cmd = parts[1]
        arg = parts[2] if len(parts) > 2 else ""
        if cmd == "limine_edit":
            cp = _probe_limine()["conf_path"]
            probe.open_terminal(["bash", "-c", f"sudo ${{EDITOR:-nano}} {shlex.quote(cp)}; echo; read -p 'Press Enter to close...'"])
            _set_sys_fb(True, t("cc_settings.opened_in_terminal_editor", p0=cp))
        elif cmd in ("limine_to", "limine_def") and arg.isdigit():
            lkey = "timeout" if cmd == "limine_to" else "default_entry"
            _stage_limine_draft(lambda m, k=lkey, v=str(int(arg)): m.__setitem__(k, v))
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    if uri.startswith("cc_boot:"):
        sub = uri[len("cc_boot:"):]
        if sub == "apply":
            draft = STATE.get("telemetry", "limine_draft", None)
            if not isinstance(draft, dict):
                _set_sys_fb(True, t("cc_system.limine_fb_no_changes"))
            else:
                conf_txt = _render_limine_conf(draft)
                def _on_apply(opened: bool, rc: int, out: str):
                    if opened:
                        return
                    if rc == 0:
                        STATE.set("telemetry", "limine_draft", None)
                        STATE.set("telemetry", "limine_wp_preview_path", "")
                        _set_sys_fb(True, t("cc_system.limine_fb_applied"))
                    else:
                        _set_sys_fb(False, t("cc_system.limine_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

                _run_helper_async(cc_panel, "rescue_busy", "LIMINE", "nc-rescue", "rescue_manage", ["limine-apply-stdin"], stdin_data=conf_txt, timeout=25.0, on_done=_on_apply)
        elif sub == "reset":
            STATE.set("telemetry", "limine_draft", None)
            STATE.set("telemetry", "limine_wp_preview_path", "")
            _set_sys_fb(True, t("cc_system.limine_fb_reset"))
        elif sub.startswith("preset:"):
            pid = sub.split(":", 1)[1]
            pres = _LIMINE_THEME_PRESETS.get(pid)
            if pres:
                def _apply_pres(m, pr=pres):
                    for pk, pv in pr.items():
                        m[pk] = list(pv) if isinstance(pv, list) else pv
                _stage_limine_draft(_apply_pres)
        elif sub.startswith("to:"):
            v = sub.split(":", 1)[1]
            if v in ("0", "3", "5", "10", "no"):
                _stage_limine_draft(lambda m, val=v: m.__setitem__("timeout", val))
        elif sub == "toggle_remember":
            _stage_limine_draft(lambda m: m.__setitem__("remember_last_entry", not bool(m.get("remember_last_entry"))))
        elif sub == "toggle_verbose":
            _stage_limine_draft(lambda m: m.__setitem__("verbose", not bool(m.get("verbose"))))
        elif sub.startswith("res:"):
            rval = sub.split(":", 1)[1]
            _stage_limine_draft(lambda m, val=("" if rval == "auto" else rval): m.__setitem__("interface_resolution", val))
        elif sub.startswith("margin:"):
            try:
                mv = max(0, min(160, int(round(float(sub.split(":", 1)[1])))))
                _stage_limine_draft(lambda m, val=mv: m.__setitem__("term_margin", val))
                cc_panel.request_throttled_refresh()
                return True
            except ValueError:
                pass
        elif sub.startswith("margin_scroll:"):
            st = _load_limine_studio_data()
            cur_m = int(st["model"].get("term_margin", 0))
            step = 8 if sub.endswith(":up") else -8
            _stage_limine_draft(lambda m, val=max(0, min(160, cur_m + step)): m.__setitem__("term_margin", val))
            cc_panel.request_throttled_refresh()
            return True
        elif sub.startswith("opacity:"):
            try:
                ov = max(0, min(100, int(round(float(sub.split(":", 1)[1])))))
                _stage_limine_draft(lambda m, val=ov: m.__setitem__("term_background_alpha_pct", val))
                cc_panel.request_throttled_refresh()
                return True
            except ValueError:
                pass
        elif sub.startswith("opacity_scroll:"):
            st = _load_limine_studio_data()
            cur_o = int(st["model"].get("term_background_alpha_pct", 50))
            step = 10 if sub.endswith(":up") else -10
            _stage_limine_draft(lambda m, val=max(0, min(100, cur_o + step)): m.__setitem__("term_background_alpha_pct", val))
            cc_panel.request_throttled_refresh()
            return True
        elif sub == "wp_cycle_style":
            order = ("stretched", "centered", "tiled")
            st = _load_limine_studio_data()
            cur_s = st["model"].get("wallpaper_style", "stretched")
            nxt = order[(order.index(cur_s) + 1) % len(order)] if cur_s in order else "stretched"
            _stage_limine_draft(lambda m, val=nxt: m.__setitem__("wallpaper_style", val))
        elif sub == "wp_clear":
            STATE.set("telemetry", "limine_wp_preview_path", "")
            _stage_limine_draft(lambda m: m.__setitem__("wallpaper", ""))
        elif sub.startswith("wp_esp:"):
            rel_i = sub.split(":", 1)[1]
            if rel_i and ".." not in rel_i and re.match(r"^[a-zA-Z0-9._/-]+\.(png|jpg|jpeg|bmp)$", rel_i, re.IGNORECASE):
                _stage_limine_draft(lambda m, r=rel_i: m.__setitem__("wallpaper", f"boot():/{r.lstrip('/')}"))
        elif sub == "wp_import":
            cc_panel.request_prompt("cc_boot_wp_path", t("cc_system.boot_prompt_wp_lbl"), "", title=t("cc_system.boot_prompt_wp_title"), completion="path_file", required=True)
            return True
        elif sub == "branding_open":
            st = _load_limine_studio_data()
            start_inline_input(
                "limine_branding",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=str(st["model"].get("interface_branding", "")),
                max_len=48,
                strip_leading_hash=False,
                uppercase=False,
                on_commit=lambda s: _stage_limine_draft(lambda m, v=s.strip(): m.__setitem__("interface_branding", v)),
            )
            return True
        elif sub.startswith("set_col:"):
            _, ckey, hx = sub.split(":", 2)
            valid_h = _norm_hex6(hx, "")
            if valid_h:
                if ckey in ("term_foreground", "term_foreground_bright", "term_background_rgb", "interface_branding_color", "backdrop"):
                    _stage_limine_draft(lambda m, k=ckey, v=valid_h: m.__setitem__(k, v))
                elif ckey.startswith("pal_") and ckey[4:].isdigit():
                    idx = int(ckey[4:])
                    if 0 <= idx < 8:
                        _stage_limine_draft(lambda m, i=idx, v=valid_h: m["term_palette"].__setitem__(i, v))
        elif sub.startswith("inline_col:"):
            ckey = sub.split(":", 1)[1]
            st = _load_limine_studio_data()
            m = st["model"]
            if ckey in ("term_foreground", "term_foreground_bright", "term_background_rgb", "interface_branding_color", "backdrop"):
                init_hx = str(m.get(ckey, "#ffffff")).upper()
            elif ckey.startswith("pal_") and ckey[4:].isdigit() and 0 <= int(ckey[4:]) < 8:
                init_hx = str(m["term_palette"][int(ckey[4:])]).upper()
            else:
                return True

            def _commit_hx(raw_h: str, k=ckey):
                vh = _norm_hex6(raw_h, "")
                if not vh:
                    _set_sys_fb(False, t("cc_system.limine_fb_err", p0=raw_h))
                    return
                if k.startswith("pal_"):
                    i = int(k[4:])
                    _stage_limine_draft(lambda mod, idx=i, v=vh: mod["term_palette"].__setitem__(idx, v))
                else:
                    _stage_limine_draft(lambda mod, key=k, v=vh: mod.__setitem__(key, v))

            start_inline_input(
                f"cc_lim_col_{ckey}",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=init_hx,
                max_len=7,
                strip_leading_hash=False,
                uppercase=True,
                validator=lambda s: bool(_norm_hex6(s, "")),
                on_commit=_commit_hx,
            )
            return True
        elif sub.startswith("preview_sel:"):
            arg_id = sub.split(":", 1)[1]
            if arg_id.isdigit():
                nid = int(arg_id)
                st = _load_limine_studio_data()
                nodes = st["model"]["nodes"]
                if 0 <= nid < len(nodes):
                    if nodes[nid].get("is_dir"):
                        _stage_limine_draft(lambda m, i=nid: m["nodes"][i].__setitem__("expanded", not bool(m["nodes"][i].get("expanded"))))
                    else:
                        STATE.set("telemetry", "limine_selected_id", nid)
        elif sub.startswith("sel_node:"):
            arg_id = sub.split(":", 1)[1]
            if arg_id.isdigit():
                STATE.set("telemetry", "limine_selected_id", int(arg_id))
        elif sub.startswith("toggle_dir:"):
            arg_id = sub.split(":", 1)[1]
            if arg_id.isdigit():
                nid = int(arg_id)
                _stage_limine_draft(lambda m, i=nid: m["nodes"][i].__setitem__("expanded", not bool(m["nodes"][i].get("expanded"))) if 0 <= i < len(m["nodes"]) else None)
        elif sub.startswith("set_default:"):
            arg_id = sub.split(":", 1)[1]
            if arg_id.isdigit():
                _stage_limine_draft(lambda m, v=arg_id: m.__setitem__("default_entry", v))
        elif sub.startswith("move_up:") or sub.startswith("move_dn:"):
            op, arg_id = sub.split(":", 1)
            if arg_id.isdigit():
                nid = int(arg_id)
                def _mv(m, i=nid, up=(op == "move_up")):
                    ns = m["nodes"]
                    j = i - 1 if up else i + 1
                    if 0 <= i < len(ns) and 0 <= j < len(ns):
                        ns[i], ns[j] = ns[j], ns[i]
                        STATE.set("telemetry", "limine_selected_id", j)
                _stage_limine_draft(_mv)
        elif sub.startswith("del_node:"):
            arg_id = sub.split(":", 1)[1]
            if arg_id.isdigit():
                nid = int(arg_id)
                _stage_limine_draft(lambda m, i=nid: m["nodes"].pop(i) if (0 <= i < len(m["nodes"]) and len(m["nodes"]) > 1) else None)
        elif sub.startswith("edit_node:"):
            _, fld, arg_id = sub.split(":", 2)
            if arg_id.isdigit() and fld in ("title", "comment", "path", "cmdline"):
                nid = int(arg_id)
                st = _load_limine_studio_data()
                if 0 <= nid < len(st["model"]["nodes"]):
                    cur_v = str(st["model"]["nodes"][nid].get(fld, ""))
                    start_inline_input(
                        f"lim_node_{fld}_{nid}",
                        shell=cc_panel.shell,
                        trigger_uri=uri,
                        initial_text=cur_v,
                        max_len=140,
                        strip_leading_hash=False,
                        uppercase=False,
                        validator=lambda s, f=fld: bool(s.strip()) if f in ("title", "path") else True,
                        on_commit=lambda s, i=nid, f=fld: _stage_limine_draft(lambda m: m["nodes"][i].__setitem__(f, s.strip()) if 0 <= i < len(m["nodes"]) else None),
                    )
                    return True
        elif sub == "add_entry":
            cc_panel.request_prompt("cc_boot_add_title", t("cc_system.boot_prompt_add_lbl"), "", title=t("cc_system.boot_prompt_add_title"), required=True)
            return True

        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    return False


def handle_cc_boot_prompt(cc_panel, action_id: str, text: str) -> bool:
    if action_id == "cc_boot_wp_path":
        wp_p = str(text or "").strip()
        if not wp_p or ".." in wp_p or not os.path.isfile(wp_p):
            _set_sys_fb(False, t("cc_system.limine_fb_err", p0=wp_p or "missing file"))
            return True
        def _on_wp(opened: bool, rc: int, out: str, src_f=wp_p):
            if opened:
                return
            if rc == 0 and "WALLPAPER_OK|" in out:
                parts = out.splitlines()[-1].split("|")
                wp_val = parts[1] if len(parts) > 1 else ""
                wp_name = parts[2] if len(parts) > 2 else os.path.basename(src_f)
                if wp_val:
                    STATE.set("telemetry", "limine_wp_preview_path", src_f)
                    _stage_limine_draft(lambda m, val=wp_val: m.__setitem__("wallpaper", val))
                _set_sys_fb(True, t("cc_system.limine_fb_wp_staged", p0=wp_name))
            else:
                _set_sys_fb(False, t("cc_system.limine_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(
            cc_panel,
            "rescue_busy",
            "WALL",
            "nc-rescue",
            "rescue_manage",
            ["limine-wallpaper-stdin"],
            stdin_data=f"{wp_p}\n",
            timeout=25.0,
            on_done=_on_wp,
        )
        return True
    if action_id == "cc_boot_add_title":
        title_s = str(text or "").strip()
        if not title_s:
            return True
        cc_panel.request_prompt(
            f"cc_boot_add_path|{title_s}",
            t("cc_system.boot_prompt_path_lbl", p0=title_s),
            "boot():/EFI/Linux/",
            title=t("cc_system.boot_prompt_add_title"),
            required=True,
        )
        return True
    if action_id.startswith("cc_boot_add_path|"):
        title_s = action_id.split("|", 1)[1].strip()
        path_s = str(text or "").strip()
        if not title_s or not path_s:
            return True
        def _add_n(m, tt=title_s, pp=path_s):
            nodes = m["nodes"]
            nodes.append({
                "id": len(nodes),
                "depth": 1,
                "expanded": False,
                "is_dir": False,
                "title": tt,
                "comment": "",
                "protocol": "efi",
                "path_key": "path",
                "path": pp,
                "cmdline": "",
                "extra_lines": [],
            })
            STATE.set("telemetry", "limine_selected_id", len(nodes) - 1)
        _stage_limine_draft(_add_n)
        _set_sys_fb(True, t("cc_system.limine_fb_entry_added", p0=title_s))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    return False
