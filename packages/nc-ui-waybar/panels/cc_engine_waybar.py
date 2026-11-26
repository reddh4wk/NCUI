#!/usr/bin/env python3
import ast
import copy
import importlib.util
import json
import os
import re
from core.config import (
    CONFIG,
    get_network_show_bar_speed,
    set_network_show_bar_speed,
    set_waybar_width_pct,
    estimate_waybar_free_chars_budget,
    get_screen_lock_cmd,
    set_screen_lock_cmd,
    apply_waybar_transparency,
    get_waybar_config_dir,
    strip_jsonc_comments,
    _update_toml_key_in_section,
)
from core.paths import find_package_file, iter_user_plugin_dirs
from core.state import STATE
from core.i18n import get_active_language, t
from ui.kit import feedback_cards, handle_scroll_action, set_feedback

REPO_WB_CONF = find_package_file("config", "waybar", "config.jsonc")

SPACER_LEFT_MOD = "custom/nc-spacer-l"
SPACER_RIGHT_MOD = "custom/nc-spacer-r"
INTERNAL_SPACER_MODS = (SPACER_LEFT_MOD, SPACER_RIGHT_MOD)
CENTER_ANCHOR_MOD = "custom/calendar"
_SPACER_DEF = {"format": "\u200b", "expand": True, "tooltip": False}


def _unpack_logical_sections(data: dict) -> dict:
    """Convert physical Waybar spacer-packed lists back into logical left/center/right lists."""
    left = list(data.get("modules-left", []) or [])
    center = list(data.get("modules-center", []) or [])
    right = list(data.get("modules-right", []) or [])
    pre_center: list[str] = []
    post_center: list[str] = []

    if SPACER_LEFT_MOD in left:
        idx = left.index(SPACER_LEFT_MOD)
        pre_center = [m for m in left[idx + 1:] if m not in INTERNAL_SPACER_MODS]
        left = [m for m in left[:idx] if m not in INTERNAL_SPACER_MODS]
    else:
        left = [m for m in left if m not in INTERNAL_SPACER_MODS]

    if SPACER_RIGHT_MOD in right:
        idx = right.index(SPACER_RIGHT_MOD)
        post_center = [m for m in right[:idx] if m not in INTERNAL_SPACER_MODS]
        right = [m for m in right[idx + 1:] if m not in INTERNAL_SPACER_MODS]
    else:
        right = [m for m in right if m not in INTERNAL_SPACER_MODS]

    center = [m for m in center if m not in INTERNAL_SPACER_MODS]
    data["modules-left"] = left
    data["modules-center"] = pre_center + center + post_center
    data["modules-right"] = right
    for sp in INTERNAL_SPACER_MODS:
        data.pop(sp, None)
    return data


def _pack_physical_sections(data: dict) -> dict:
    """Pack logical center modules around a dead-center `custom/calendar` using invisible flex spacers."""
    left = [m for m in (data.get("modules-left", []) or []) if m not in INTERNAL_SPACER_MODS]
    center = [m for m in (data.get("modules-center", []) or []) if m not in INTERNAL_SPACER_MODS]
    right = [m for m in (data.get("modules-right", []) or []) if m not in INTERNAL_SPACER_MODS]

    if CENTER_ANCHOR_MOD in center:
        cal_idx = center.index(CENTER_ANCHOR_MOD)
        pre_c = center[:cal_idx]
        post_c = center[cal_idx + 1:]
        data["modules-left"] = left + ([SPACER_LEFT_MOD] + pre_c if pre_c else [])
        data["modules-center"] = [CENTER_ANCHOR_MOD]
        data["modules-right"] = (post_c + [SPACER_RIGHT_MOD] if post_c else []) + right
        data["fixed-center"] = True
        data["expand-left"] = bool(pre_c)
        data["expand-right"] = bool(post_c)
        if pre_c:
            data[SPACER_LEFT_MOD] = dict(_SPACER_DEF)
        else:
            data.pop(SPACER_LEFT_MOD, None)
        if post_c:
            data[SPACER_RIGHT_MOD] = dict(_SPACER_DEF)
        else:
            data.pop(SPACER_RIGHT_MOD, None)
    else:
        data["modules-left"] = left
        data["modules-center"] = center
        data["modules-right"] = right
        data["expand-left"] = False
        data["expand-right"] = False
        data.pop(SPACER_LEFT_MOD, None)
        data.pop(SPACER_RIGHT_MOD, None)
    return data


def _user_wb_conf() -> str:
    return os.path.join(get_waybar_config_dir(), "config.jsonc")

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



def _pick_meta_text(val, lang: str) -> str:
    """Resolve optional third-party plugin fallback text (str or {'en': ..., 'it': ...})."""
    if isinstance(val, dict):
        return str(val.get(lang) or val.get("en") or next(iter(val.values()), "")).strip()
    return str(val or "").strip()


def _normalize_user_plugin_meta(meta: dict, panel_id: str, fpath: str) -> dict:
    out = dict(meta)
    out["_panel_id"] = panel_id
    out["_path"] = fpath
    out["_realpath"] = os.path.realpath(fpath)
    out["_is_symlink"] = os.path.islink(fpath)
    wb_raw = meta.get("waybar")
    wb = dict(wb_raw) if isinstance(wb_raw, dict) else {}
    wb.setdefault("class", "module-nc")
    wb.setdefault("exec", f"nc-bar-agent {panel_id}")
    wb.setdefault("return-type", "json")
    wb.setdefault("interval", 2)
    wb.setdefault("on-click", f"nc-ctl popup {panel_id}")
    wb["tooltip"] = True
    wb["tooltip-format"] = ""
    out["waybar"] = wb
    return out


_CUSTOM_SLUG_RE = re.compile(r"^[a-z0-9_]{1,15}$")


def _audit_plugin_warnings(tree: ast.AST, meta_raw: dict) -> list[str]:
    """Collect non-blocking AST sanity warnings without rejecting valid plugins."""
    warnings: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "sys" and fn.attr == "exit":
                warnings.append(t("cc_waybar.warn_plugin_top_exit"))
                break
        if isinstance(node, ast.If):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and isinstance(stmt.value.func.value, ast.Name)
                    and stmt.value.func.value.id == "sys"
                    and stmt.value.func.attr == "exit"
                ):
                    test_src = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
                    if "__name__" not in test_src:
                        warnings.append(t("cc_waybar.warn_plugin_top_exit"))
                        break

    wb = meta_raw.get("waybar") if isinstance(meta_raw.get("waybar"), dict) else {}
    for wk in ("exec", "on-click"):
        val = str(wb.get(wk) or "")
        if "/usr/local/lib/nc-ui" in val:
            warnings.append(t("cc_waybar.warn_plugin_legacy_path"))
            break

    missing_deps: list[str] = []
    ignore_pkgs = {"core", "ui", "panels", "gi"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".", 1)[0]
                if root_pkg not in ignore_pkgs and root_pkg not in missing_deps:
                    try:
                        if importlib.util.find_spec(root_pkg) is None:
                            missing_deps.append(root_pkg)
                    except (ImportError, ValueError):
                        missing_deps.append(root_pkg)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            root_pkg = node.module.split(".", 1)[0]
            if root_pkg not in ignore_pkgs and root_pkg not in missing_deps:
                try:
                    if importlib.util.find_spec(root_pkg) is None:
                        missing_deps.append(root_pkg)
                except (ImportError, ValueError):
                    missing_deps.append(root_pkg)
    if missing_deps:
        warnings.append(t("cc_waybar.warn_plugin_missing_deps", p0=", ".join(missing_deps[:3])))

    if not meta_raw.get("label") and not meta_raw.get("label_key"):
        warnings.append(t("cc_waybar.warn_plugin_no_label"))
    return warnings


def _inspect_plugin_ast(fpath: str, collect_warnings: bool = False) -> tuple[bool, dict | str]:
    """Validate and inspect an external Waybar plugin file strictly via AST (zero execution)."""
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fpath)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False, t("cc_waybar.err_plugin_syntax")

    meta_raw = None
    has_panel = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(tgt, ast.Name) and tgt.id == "PLUGIN_META"
            for tgt in node.targets
        ):
            try:
                val = ast.literal_eval(node.value)
                if isinstance(val, dict):
                    meta_raw = val
            except Exception:
                meta_raw = None
        elif isinstance(node, ast.ClassDef) and node.name == "Panel":
            has_panel = True
        elif isinstance(node, ast.FunctionDef) and node.name == "create_panel":
            has_panel = True

    if meta_raw is None:
        return False, t("cc_waybar.err_plugin_no_meta")
    mod_id = str(meta_raw.get("module_id") or "").strip()
    if not mod_id.startswith("custom/") or len(mod_id) <= len("custom/"):
        return False, t("cc_waybar.err_plugin_bad_mod_id")
    mod_slug = mod_id[len("custom/"):]
    if not _CUSTOM_SLUG_RE.fullmatch(mod_slug):
        return False, t("cc_waybar.err_plugin_slug_len")
    if mod_id in KNOWN_WAYBAR_PLUGINS:
        return False, t("cc_waybar.err_plugin_builtin_collision", p0=mod_id)
    if "waybar" in meta_raw and meta_raw["waybar"] is not None and not isinstance(meta_raw["waybar"], dict):
        return False, t("cc_waybar.err_plugin_bad_waybar")
    if not has_panel:
        return False, t("cc_waybar.err_plugin_no_panel")

    raw_pid = str(meta_raw.get("panel_id") or "").strip()
    fname = os.path.basename(fpath)
    if raw_pid:
        if not _CUSTOM_SLUG_RE.fullmatch(raw_pid):
            return False, t("cc_waybar.err_plugin_slug_len")
        panel_id = raw_pid
    elif fname.startswith("wp_") and fname.endswith(".py") and _CUSTOM_SLUG_RE.fullmatch(fname[3:-3]):
        panel_id = fname[3:-3]
    else:
        panel_id = mod_slug

    norm = _normalize_user_plugin_meta(meta_raw, panel_id, fpath)
    if collect_warnings:
        norm["_warnings"] = _audit_plugin_warnings(tree, meta_raw)
    return True, norm


_USER_PLUGINS_CACHE: tuple | None = None
_WB_JSONC_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}


def _discover_user_waybar_plugins() -> dict[str, dict]:
    """Discover external user Waybar plugins via AST inspection of PLUGIN_META."""
    global _USER_PLUGINS_CACHE
    sig_parts: list[tuple] = []
    entries: list[tuple[str, str]] = []
    for d in iter_user_plugin_dirs():
        if not os.path.isdir(d):
            continue
        try:
            fnames = sorted(os.listdir(d))
        except OSError:
            continue
        dir_sigs: list[tuple] = []
        for fname in fnames:
            if not (fname.startswith("wp_") and fname.endswith(".py")):
                continue
            fpath = os.path.join(d, fname)
            try:
                st = os.stat(fpath)
            except OSError:
                continue
            if not os.path.isfile(fpath):
                continue
            dir_sigs.append((fname, os.path.realpath(fpath), st.st_mtime_ns, st.st_size))
            entries.append((fname, fpath))
        sig_parts.append((d, tuple(dir_sigs)))
    sig = tuple(sig_parts)
    if _USER_PLUGINS_CACHE is not None and _USER_PLUGINS_CACHE[0] == sig:
        return dict(_USER_PLUGINS_CACHE[1])

    out: dict[str, dict] = {}
    for _fname, fpath in entries:
        ok, meta = _inspect_plugin_ast(fpath)
        if ok and isinstance(meta, dict):
            mod_id = str(meta.get("module_id") or "").strip()
            if mod_id and mod_id not in out:
                out[mod_id] = meta
    _USER_PLUGINS_CACHE = (sig, out)
    return dict(out)


def link_custom_waybar_plugin(raw_path: str) -> tuple[bool, str]:
    """Validate and symlink a third-party .py plugin into ~/.local/share/ncui/plugins."""
    src = os.path.abspath(os.path.expanduser(str(raw_path or "").strip()))
    if not src or not os.path.isfile(src):
        return False, t("cc_waybar.err_plugin_not_found", p0=src or "-")
    if not src.endswith(".py"):
        return False, t("cc_waybar.err_plugin_not_py")

    ok, res = _inspect_plugin_ast(src, collect_warnings=True)
    if not ok or not isinstance(res, dict):
        return False, str(res)

    mod_id = str(res["module_id"])
    panel_id = str(res["_panel_id"])
    real_src = os.path.realpath(src)
    existing = _discover_user_waybar_plugins().get(mod_id)
    if existing and os.path.realpath(str(existing.get("_path") or "")) != real_src:
        return False, t("cc_waybar.err_plugin_dup_mod_id", p0=mod_id)

    target_dir = iter_user_plugin_dirs()[0]
    os.makedirs(target_dir, exist_ok=True)
    link_name = f"wp_{panel_id}.py"
    link_path = os.path.join(target_dir, link_name)

    if os.path.lexists(link_path):
        if os.path.islink(link_path):
            if os.path.realpath(link_path) != real_src:
                os.unlink(link_path)
                os.symlink(real_src, link_path)
        elif os.path.realpath(link_path) != real_src:
            return False, t("cc_waybar.err_plugin_regular_exists", p0=link_path)
    else:
        os.symlink(real_src, link_path)

    warns = res.get("_warnings") or []
    if warns:
        _USER_PLUGINS_CACHE = None
        return True, t("cc_waybar.fb_plugin_linked_warn", p0=mod_id, p1=link_name, p2="; ".join(warns))
    _USER_PLUGINS_CACHE = None
    return True, t("cc_waybar.fb_plugin_linked", p0=mod_id, p1=link_name)


def unlink_custom_waybar_plugin(mod_id: str, reload_if_active: bool = True) -> tuple[bool, str]:
    """Remove a symlinked third-party plugin (and/or orphaned custom/* Waybar entry)."""
    if not mod_id.startswith("custom/") or mod_id in KNOWN_WAYBAR_PLUGINS:
        return False, t("cc_waybar.err_plugin_not_found", p0=mod_id)

    removed_link = False
    meta = _discover_user_waybar_plugins().get(mod_id)
    if meta:
        fpath = str(meta.get("_path") or "")
        if fpath and os.path.islink(fpath):
            os.unlink(fpath)
            removed_link = True
        elif fpath and os.path.isfile(fpath):
            return False, t("cc_waybar.err_plugin_not_symlink", p0=fpath)

    slug = mod_id[len("custom/"):]
    for d in iter_user_plugin_dirs():
        cand = os.path.join(d, f"wp_{slug}.py")
        if os.path.islink(cand) and not os.path.exists(cand):
            os.unlink(cand)
            removed_link = True

    _USER_PLUGINS_CACHE = None
    data = _read_wb_config()
    changed = False
    was_active = False
    for sec in ("modules-left", "modules-center", "modules-right"):
        lst = list(data.get(sec) or [])
        if mod_id in lst:
            data[sec] = [m for m in lst if m != mod_id]
            changed = True
            was_active = True
    if mod_id in data:
        data.pop(mod_id, None)
        changed = True
    if changed:
        _write_wb_config(data, reload_bar=(reload_if_active and was_active))
    if not removed_link and not changed:
        return False, t("cc_waybar.err_plugin_not_found", p0=mod_id)
    return True, t("cc_waybar.fb_plugin_unlinked", p0=mod_id)


def _wb_plugin_meta(m_id: str) -> tuple[str, str, str]:
    slug = m_id.replace("/", "_")
    icon = KNOWN_WAYBAR_PLUGINS.get(m_id, {}).get("icon", "\uf12e")
    if m_id in KNOWN_WAYBAR_PLUGINS:
        return icon, t(f"cc_waybar.p_{slug}_lbl"), t(f"cc_waybar.p_{slug}_desc")
    user_meta = _discover_user_waybar_plugins().get(m_id)
    if user_meta:
        lang = get_active_language()
        u_icon = str(user_meta.get("icon") or "\uf12e")
        fb_lbl = _pick_meta_text(user_meta.get("label"), lang) or m_id
        fb_desc = _pick_meta_text(user_meta.get("desc"), lang) or fb_lbl
        u_lbl = t(user_meta["label_key"], default=fb_lbl) if user_meta.get("label_key") else fb_lbl
        u_desc = t(user_meta["desc_key"], default=fb_desc) if user_meta.get("desc_key") else fb_desc
        return u_icon, u_lbl, u_desc
    return icon, m_id, m_id


def _load_wb_jsonc(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
        cached = _WB_JSONC_CACHE.get(path)
        if cached is not None and cached[0] == sig:
            return copy.deepcopy(cached[1])
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(strip_jsonc_comments(f.read()))
        parsed = data if isinstance(data, dict) else {}
        _WB_JSONC_CACHE[path] = (sig, copy.deepcopy(parsed))
        return parsed
    except Exception:
        return {}


def _backfill_wb_modules(data: dict) -> dict:
    base = _load_wb_jsonc(REPO_WB_CONF)
    for k, v in base.items():
        if k in INTERNAL_SPACER_MODS:
            continue
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
            data[mod_id]["tooltip"] = True
            data[mod_id]["tooltip-format"] = ""
        elif mod_id not in active_mods and mod_id in data:
            data.pop(mod_id, None)
    return data


def _read_wb_config() -> dict:
    data = _load_wb_jsonc(_user_wb_conf()) or _load_wb_jsonc(REPO_WB_CONF)
    if not data:
        data = {"modules-left": [], "modules-center": [], "modules-right": []}
    return _unpack_logical_sections(_backfill_wb_modules(data))


def _write_wb_config(data: dict, reload_bar: bool = False) -> None:
    from core.config import get_layout_margins
    _backfill_wb_modules(data)
    _pack_physical_sections(data)
    _, outer_m = get_layout_margins()
    data["margin-top"] = outer_m
    data["margin-left"] = outer_m
    data["margin-right"] = outer_m
    budget = estimate_waybar_free_chars_budget()
    data.setdefault("niri/window", {})["max-length"] = int(budget["window_chars"])
    managed_mods = set(KNOWN_WAYBAR_PLUGINS) | set(_discover_user_waybar_plugins())
    for k, v in data.items():
        if isinstance(v, dict) and k in managed_mods and k not in INTERNAL_SPACER_MODS:
            if k.startswith("custom/") and k != "custom/media":
                v["tooltip"] = True
                v["tooltip-format"] = ""
            else:
                v["tooltip"] = False

    user_conf = _user_wb_conf()
    _WB_JSONC_CACHE.pop(user_conf, None)
    try:
        os.makedirs(os.path.dirname(user_conf), exist_ok=True)
        with open(user_conf, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass

    if reload_bar:
        apply_waybar_transparency()


def _set_wb_fb(ok: bool, msg: str) -> None:
    set_feedback("waybar", ok, msg)


def get_wb_feedback_cards() -> list:
    return feedback_cards("waybar", "cc_waybar.status_title")


def handle_cc_waybar_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_wb:"):
        return False
    if handle_scroll_action(uri, "cc_wb:guide_scroll", "waybar", "plugin_guide_scroll", step=3, anchor="top", panel=cc_panel):
        return True
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

    if act.startswith("prompt_link_plugin") and act == "prompt_link_plugin":
        cc_panel.request_prompt(
            "cc_wb_link_plugin",
            t("cc_waybar.prompt_link_plugin_ph"),
            "~/",
            title=t("cc_waybar.prompt_link_plugin_title"),
            required=True,
            completion=("path_ext", (".py",)),
        )
        return True

    if act.startswith("unlink:"):
        mod_id = act.split(":", 1)[1].strip()
        if cc_panel.shell:
            cc_panel.shell.bump_leave_guard(1.5)
        ok_u, msg_u = unlink_custom_waybar_plugin(mod_id)
        _set_wb_fb(ok_u, msg_u)
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
    if action_id == "cc_wb_link_plugin":
        ok_l, msg_l = link_custom_waybar_plugin(text)
        _set_wb_fb(ok_l, msg_l)
        if cc_panel.shell:
            cc_panel.shell.refresh_view()
        return True
    return False
