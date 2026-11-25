#!/usr/bin/env python3
import ast
import json
import os
import re
import subprocess
from core.config import (
    CONFIG,
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
from core.paths import find_all_package_files, find_package_file, iter_user_plugin_dirs
from core.state import STATE
from core.i18n import get_active_language, t
from ui.canvas import Canvas
from ui.kit import Box, Badge, FeedbackBox, Slider, ProgressBar, Divider, Txt, SelectPill, ActionBtn, StateBadge
from panels.cc_engine_network import get_interfaces_fast

USER_WB_CONF = os.path.expanduser("~/.config/waybar/config.jsonc")
REPO_WB_CONF = find_package_file("config", "waybar", "config.jsonc")

KNOWN_WAYBAR_PLUGINS = {
    "niri/workspaces":      {"icon": "\uf111"},
    "niri/window":          {"icon": "\uf2d0"},
    "custom/media":         {"icon": "\uf001"},
    "custom/settings":      {"icon": "\uf013"},
    "custom/calendar":      {"icon": "\uf073"},
    "custom/desktop":       {"icon": "\uf108"},
    "custom/cliphist":      {"icon": "\uf0ea"},
    "custom/screenshot":    {"icon": "\uf030"},
    "custom/printers":      {"icon": "\uf02f"},
    "custom/audio":         {"icon": "\uf028"},
    "custom/netcycle":      {"icon": "\uf6ff"},
    "custom/cpu":           {"icon": "\uf2db"},
    "custom/ram":           {"icon": "\uf538"},
    "custom/gpu":           {"icon": "\U000f08ae"},
    "custom/ups":           {"icon": "\uf240"},
    "custom/kb_layout":     {"icon": "\uf11c"},
    "custom/telegram":      {"icon": "\ue217"},
    "custom/notifications": {"icon": "\uf0f3"},
    "custom/session":       {"icon": "\uf011"},
}



def _discover_user_waybar_plugins() -> dict[str, dict]:
    """Discover external user Waybar plugins via AST inspection of PLUGIN_META."""
    out: dict[str, dict] = {}
    for d in iter_user_plugin_dirs():
        if not os.path.isdir(d):
            continue
        try:
            fnames = sorted(os.listdir(d))
        except OSError:
            continue
        for fname in fnames:
            if not (fname.startswith("wp_") and fname.endswith(".py")):
                continue
            fpath = os.path.join(d, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=fpath)
                for node in tree.body:
                    if isinstance(node, ast.Assign) and any(
                        isinstance(tgt, ast.Name) and tgt.id == "PLUGIN_META"
                        for tgt in node.targets
                    ):
                        meta = ast.literal_eval(node.value)
                        if isinstance(meta, dict):
                            mod_id = str(meta.get("module_id") or "").strip()
                            if mod_id.startswith("custom/") and mod_id not in out:
                                out[mod_id] = meta
                        break
            except Exception:
                continue
    return out


def _wb_plugin_meta(m_id: str) -> tuple[str, str, str]:
    slug = m_id.replace("/", "_")
    icon = KNOWN_WAYBAR_PLUGINS.get(m_id, {}).get("icon", "\uf12e")
    if m_id in KNOWN_WAYBAR_PLUGINS:
        return icon, t(f"cc_waybar.p_{slug}_lbl"), t(f"cc_waybar.p_{slug}_desc")
    user_meta = _discover_user_waybar_plugins().get(m_id)
    if user_meta:
        u_icon = str(user_meta.get("icon") or "\uf12e")
        u_lbl = t(user_meta["label_key"]) if user_meta.get("label_key") else str(user_meta.get("label") or m_id)
        u_desc = t(user_meta["desc_key"]) if user_meta.get("desc_key") else str(user_meta.get("desc") or m_id)
        return u_icon, u_lbl, u_desc
    return icon, m_id, m_id


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
    active_mods = set(
        (data.get("modules-left") or [])
        + (data.get("modules-center") or [])
        + (data.get("modules-right") or [])
    )
    for mod_id, meta in _discover_user_waybar_plugins().items():
        wb_def = meta.get("waybar")
        if mod_id in active_mods and isinstance(wb_def, dict):
            if not isinstance(data.get(mod_id), dict):
                data[mod_id] = dict(wb_def)
            else:
                for sub_k, sub_v in wb_def.items():
                    if sub_k != "max-length":
                        data[mod_id][sub_k] = sub_v
            data[mod_id]["tooltip"] = False
        elif mod_id not in active_mods and mod_id in data:
            data.pop(mod_id, None)
    return data


def _read_wb_config() -> dict:
    data = _load_wb_jsonc(USER_WB_CONF) or _load_wb_jsonc(REPO_WB_CONF)
    if not data:
        data = {"modules-left": [], "modules-center": [], "modules-right": []}
    return _backfill_wb_modules(data)


def _write_wb_config(data: dict, reload_bar: bool = False) -> None:
    from core.config import get_layout_margins
    _backfill_wb_modules(data)
    _, outer_m = get_layout_margins()
    data["margin-top"] = outer_m
    data["margin-left"] = outer_m
    data["margin-right"] = outer_m
    budget = estimate_waybar_free_chars_budget()
    data.setdefault("niri/window", {})["max-length"] = int(budget["window_chars"])
    for k, v in data.items():
        if isinstance(v, dict) and (k.startswith("custom/") or k.startswith("niri/")):
            v["tooltip"] = False

    paths = _unique_existing_paths(
        REPO_WB_CONF,
        *find_all_package_files("config", "waybar", "config.jsonc"),
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


_WB_FB = {"ok": True, "msg": ""}


def _set_wb_fb(ok: bool, msg: str) -> None:
    _WB_FB["ok"] = bool(ok)
    _WB_FB["msg"] = str(msg or "")


def get_wb_feedback_cards() -> list:
    if _WB_FB["msg"]:
        return [FeedbackBox(t("cc_waybar.status_title"), bool(_WB_FB["ok"]), _WB_FB["msg"])]
    return []


def handle_cc_waybar_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_wb:"):
        return False
    act = uri[len("cc_wb:"):]
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
        _set_wb_fb(True, t("cc_waybar.fb_applied"))
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act == "prompt_lock_cmd":
        cur = get_screen_lock_cmd()
        ph = t("cc_waybar.screen_lock_command_e_g_swaylock_f")
        cc_panel.request_prompt("cc_wb_lock_cmd", ph, cur, title=t("cc_waybar.screen_lock_command"), completion="shell_cmd")
        return True

    if act.startswith("set_lock:"):
        cmd_val = act.split(":", 1)[1]
        set_screen_lock_cmd(cmd_val)
        _set_wb_fb(True, t("cc_waybar.fb_lock_set") if cmd_val.strip() else t("cc_waybar.fb_lock_cleared"))
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("net_spd:"):
        val = (act.split(":", 1)[1] == "on")
        set_network_show_bar_speed(val)
        _set_wb_fb(True, t("cc_waybar.fb_net_speed"))
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("net_unit:"):
        u_val = act.split(":", 1)[1]
        if u_val in ("bytes", "bits"):
            STATE.set("network", "speed_unit", u_val)
            CONFIG.setdefault("network", {})["default_speed_unit"] = u_val
            _update_toml_key_in_section("network", "default_speed_unit", f'"{u_val}"')
            _set_wb_fb(True, t("cc_waybar.fb_net_unit", p0=u_val.upper()))
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True

    if act.startswith("net_if:"):
        if_val = act.split(":", 1)[1]
        STATE.set("network", "monitored_iface", if_val)
        CONFIG.setdefault("network", {})["monitored_interfaces"] = if_val
        _update_toml_key_in_section("network", "monitored_interfaces", f'"{if_val}"')
        _set_wb_fb(True, t("cc_waybar.fb_net_if", p0=if_val))
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
            from core import probe
            snap = json.loads(json.dumps(data))
            probe.enqueue_latest("wb_width_write", lambda d=snap: _write_wb_config(d, reload_bar=False), debounce_ms=120)
        except Exception:
            pass
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True

    if act.startswith("sys_glow:"):
        STATE.set("waybar", "sys_glow_enabled", act.split(":", 1)[1] == "1")
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True

    if act.startswith("sys_glow_thresh:"):
        try:
            v = max(50, min(95, int(round(float(act.split(":", 1)[1])))))
            STATE.set("waybar", "sys_glow_threshold", v)
        except Exception:
            pass
        if cc_panel:
            cc_panel.request_throttled_refresh()
        return True

    if act.startswith("prn_vis:"):
        val = act.split(":", 1)[1]
        if val in ("auto", "always"):
            STATE.set("waybar", "printers_bar_mode", val)
            if val == "always":
                data = _read_wb_config()
                if "custom/printers" not in (sec_map["left"] + sec_map["center"] + sec_map["right"]):
                    r_list = sec_map["right"]
                    idx = r_list.index("custom/audio") if "custom/audio" in r_list else len(r_list)
                    r_list.insert(idx, "custom/printers")
                    data["modules-right"] = r_list
                    _write_wb_config(data, reload_bar=False)
            _set_wb_fb(True, t("cc_waybar.fb_prn_vis", p0=val.upper()))
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
    _set_wb_fb(True, t("cc_waybar.fb_layout_updated"))
    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_waybar_prompt(cc_panel, action_id: str, text: str) -> bool:
    if action_id == "cc_wb_lock_cmd":
        set_screen_lock_cmd(text)
        _set_wb_fb(True, t("cc_waybar.fb_lock_set") if str(text or "").strip() else t("cc_waybar.fb_lock_cleared"))
        return True
    return False
