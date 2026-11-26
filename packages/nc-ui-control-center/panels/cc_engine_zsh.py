#!/usr/bin/env python3
"""NC-UI Zsh Theme Engine, Config Persistence & Guided Terminal Wizard."""

from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
import sys
import tomllib

from core import probe
from core.config import get_config_dir
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import get_semantic_palette, hex_to_rgba_f, set_feedback

ZSH_PRESET_IDS = (
    "nightcity_tactical",
    "netrunner_hud",
    "cyberdeck_split",
    "matrix_minimal",
    "hardened_ops",
)

HIST_SIZE_OPTIONS = (5000, 10000, 50000)

_DEFAULT_ZSH_CFG: dict = {
    "preset": "nightcity_tactical",
    "show_git": True,
    "show_exit": True,
    "show_duration": True,
    "show_clock": True,
    "show_context": True,
    "auto_sync_theme": True,
    "hist_size": 10000,
    "share_history": True,
    "hist_ignore_dups": True,
    "completion_menu": True,
    "fix_keybindings": True,
    "auto_cd": False,
    "plugin_autosuggestions": True,
    "plugin_syntax_highlighting": True,
}

_ZSHRC_BEGIN = "# >>> NC-UI ZSH >>>"
_ZSHRC_END = "# <<< NC-UI ZSH <<<"

_PLUGIN_PATHS = {
    "autosuggestions": (
        "/usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh",
        "/usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh",
    ),
    "syntax_highlighting": (
        "/usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh",
        "/usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh",
    ),
}


def zsh_toml_path(cfg_dir: str | None = None) -> str:
    return os.path.join(cfg_dir or get_config_dir(), "zsh.toml")


def zsh_theme_file_path(cfg_dir: str | None = None) -> str:
    return os.path.join(cfg_dir or get_config_dir(), "zsh", "ncui-theme.zsh")


def user_zshrc_path(home_dir: str | None = None) -> str:
    return os.path.join(home_dir or os.path.expanduser("~"), ".zshrc")


def find_zsh_plugin_file(kind: str) -> str:
    for p in _PLUGIN_PATHS.get(kind, ()):
        if os.path.isfile(p):
            return p
    return ""


def has_zsh_plugin(kind: str) -> bool:
    return bool(find_zsh_plugin_file(kind))


def _norm_cfg(raw: dict | None) -> dict:
    cfg = copy.deepcopy(_DEFAULT_ZSH_CFG)
    if not isinstance(raw, dict):
        return cfg
    pr = str(raw.get("preset") or cfg["preset"]).strip().lower()
    cfg["preset"] = pr if pr in ZSH_PRESET_IDS else cfg["preset"]
    for bk in (
        "show_git", "show_exit", "show_duration", "show_clock", "show_context",
        "auto_sync_theme", "share_history", "hist_ignore_dups",
        "completion_menu", "fix_keybindings", "auto_cd",
        "plugin_autosuggestions", "plugin_syntax_highlighting",
    ):
        if bk in raw:
            cfg[bk] = bool(raw[bk])
    try:
        hs = int(raw.get("hist_size", cfg["hist_size"]))
        cfg["hist_size"] = hs if hs in HIST_SIZE_OPTIONS else max(1000, min(200000, hs))
    except (TypeError, ValueError):
        pass
    return cfg


def load_saved_zsh_config(cfg_dir: str | None = None) -> dict:
    path = zsh_toml_path(cfg_dir)
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            return _norm_cfg(data.get("zsh") if isinstance(data.get("zsh"), dict) else data)
        except Exception:
            pass
    return copy.deepcopy(_DEFAULT_ZSH_CFG)


def save_zsh_config_toml(cfg: dict, cfg_dir: str | None = None) -> str:
    norm = _norm_cfg(cfg)
    path = zsh_toml_path(cfg_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# Managed by NC-UI Personalization -> Zsh", "[zsh]"]
    for k, v in norm.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, int):
            lines.append(f"{k} = {v}")
        else:
            lines.append(f'{k} = "{v}"')
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


def get_editor_zsh_config() -> dict:
    st = STATE.get("zsh", "draft_cfg", None)
    if isinstance(st, dict):
        return _norm_cfg(st)
    saved = load_saved_zsh_config()
    STATE.set("zsh", "draft_cfg", copy.deepcopy(saved), immediate=False)
    return saved


def _set_editor_zsh_config(cfg: dict) -> None:
    STATE.set("zsh", "draft_cfg", _norm_cfg(cfg), immediate=False)


def is_zsh_draft_dirty() -> bool:
    return get_editor_zsh_config() != load_saved_zsh_config()


def is_zsh_theme_applied(cfg_dir: str | None = None, home_dir: str | None = None) -> bool:
    tf = zsh_theme_file_path(cfg_dir)
    rc = user_zshrc_path(home_dir)
    if not os.path.isfile(tf) or not os.path.isfile(rc):
        return False
    try:
        txt = open(rc, "r", encoding="utf-8", errors="ignore").read()
        return _ZSHRC_BEGIN in txt and _ZSHRC_END in txt
    except OSError:
        return False


def _palette_hex() -> dict[str, str]:
    p = get_semantic_palette()
    keys = ("intent", "focus", "focus_val", "schema", "structure", "vitality", "attention", "interrupt", "fault")
    return {k: str(p.get(k) or "#00f0ff")[:7] for k in keys}


def _hex_to_ansi(hex_s: str, bold: bool = False) -> str:
    r, g, b, _ = hex_to_rgba_f(hex_s, 1.0)
    b_seq = "1;" if bold else ""
    return f"\033[{b_seq}38;2;{int(round(r * 255))};{int(round(g * 255))};{int(round(b * 255))}m"


def _render_preview_tokens(cfg: dict, *, git_branch: str = "main*", exit_code: int = 1, dur: str = "1.8s", clock: str = "09:22") -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return `(line1_tokens, line2_tokens)` of `(text, palette_role)` for the active preset."""
    preset = cfg["preset"]
    show_git = bool(cfg["show_git"])
    show_exit = bool(cfg["show_exit"])
    show_dur = bool(cfg["show_duration"])
    show_clk = bool(cfg["show_clock"])
    show_ctx = bool(cfg["show_context"])

    ctx_txt = "SSH" if show_ctx else ""
    line1: list[tuple[str, str]] = []
    line2: list[tuple[str, str]] = []

    if preset == "nightcity_tactical":
        line1.extend([("┌──(", "intent"), ("user", "focus"), ("@", "schema"), ("nightcity", "focus_val"), (")", "intent")])
        if ctx_txt:
            line1.extend([("─[", "structure"), (ctx_txt, "attention"), ("]", "structure")])
        line1.extend([("─[", "structure"), ("~/ncui", "focus"), ("]", "structure")])
        if show_git:
            line1.extend([("─(", "structure"), (f"⎇ {git_branch}", "vitality"), (")", "structure")])
        if show_dur:
            line1.extend([(" ", "structure"), (dur, "attention")])
        if show_exit:
            line1.extend([(" ", "structure"), (f"✗{exit_code}", "fault")])
        if show_clk:
            line1.extend([(" ", "structure"), (clock, "schema")])
        line2.extend([("└──", "intent"), ("❯ ", "fault" if show_exit else "intent"), ("git status", "focus_val")])
    elif preset == "netrunner_hud":
        line1.extend([("[", "structure"), ("● ", "intent"), ("user@nightcity", "focus"), ("] ", "structure")])
        if ctx_txt:
            line1.extend([("[", "structure"), (ctx_txt, "attention"), ("] ", "structure")])
        line1.extend([("[", "structure"), ("~/ncui", "focus_val"), ("] ", "structure")])
        if show_git:
            line1.extend([("[", "structure"), (f"⎇ {git_branch}", "vitality"), ("] ", "structure")])
        if show_dur:
            line1.append((f"{dur} ", "attention"))
        if show_exit:
            line1.append((f"[{exit_code}] ", "fault"))
        line1.extend([("❯ ", "intent"), ("nc-ctl", "focus_val")])
    elif preset == "cyberdeck_split":
        line1.extend([("── ", "structure"), ("user@nightcity", "intent"), (" ── ", "structure"), ("~/ncui", "focus"), (" ", "structure")])
        if show_git:
            line1.extend([("── ", "structure"), (f"⎇ {git_branch}", "vitality"), (" ", "structure")])
        if ctx_txt:
            line1.extend([("── ", "structure"), (ctx_txt, "attention"), (" ", "structure")])
        line1.append(("────── ", "structure"))
        if show_dur:
            line1.append((f"{dur} ", "attention"))
        if show_clk:
            line1.append((f"{clock} ", "schema"))
        line1.append(("──", "structure"))
        line2.extend([("▸ ", "fault" if show_exit else "intent"), ("sudo nc-rescue sync", "focus_val")])
    elif preset == "matrix_minimal":
        line1.extend([("▏ ", "intent"), ("user", "focus"), (" ▕ ", "intent"), ("~/ncui", "focus_val")])
        if show_git:
            line1.append((f" ⎇ {git_branch}", "vitality"))
        if show_dur:
            line1.append((f" · {dur}", "attention"))
        if show_exit:
            line1.append((f" · {exit_code}", "fault"))
        line1.extend([(" ❯ ", "intent"), ("ls -la", "focus_val")])
    else:
        # hardened_ops
        if show_ctx:
            line1.extend([("[", "structure"), ("LIVE|SSH", "attention"), ("] ", "structure")])
        line1.extend([("(", "schema"), ("user@nightcity", "focus"), (") [", "schema"), ("~/ncui", "focus_val"), ("]", "schema")])
        if show_git:
            line1.extend([(" (", "structure"), (f"⎇ {git_branch}", "vitality"), (")", "structure")])
        if show_exit:
            line1.extend([(" [", "structure"), (f"EXIT {exit_code}", "fault"), ("]", "structure")])
        if show_dur or show_clk:
            tail = " ".join(x for x in (dur if show_dur else "", clock if show_clk else "") if x)
            line1.append((f" {tail}", "schema"))
        line2.extend([("❯ ", "intent"), ("nc-chroot", "focus_val")])
    return line1, line2


def render_zsh_preview_lines(cfg: dict, canvas: Canvas) -> list[str]:
    p = get_semantic_palette()
    l1, l2 = _render_preview_tokens(cfg)
    out = []
    for line in (l1, l2):
        if not line:
            continue
        segs = [f"<span color='{p.get(role, p['focus_val'])}'>{Canvas.escape(txt)}</span>" for txt, role in line]
        out.append("".join(segs))
    return out


def generate_ncui_zsh_theme(cfg: dict) -> str:
    norm = _norm_cfg(cfg)
    pal = _palette_hex()
    p_fn_map = {
        "nightcity_tactical": "_ncui_prompt_nightcity",
        "netrunner_hud": "_ncui_prompt_netrunner",
        "cyberdeck_split": "_ncui_prompt_cyberdeck",
        "matrix_minimal": "_ncui_prompt_matrix",
        "hardened_ops": "_ncui_prompt_hardened",
    }
    fn_name = p_fn_map[norm["preset"]]
    b_git = 1 if norm["show_git"] else 0
    b_exit = 1 if norm["show_exit"] else 0
    b_dur = 1 if norm["show_duration"] else 0
    b_clk = 1 if norm["show_clock"] else 0
    b_ctx = 1 if norm["show_context"] else 0

    lines = [
        "# Generated by NC-UI Zsh Theme Engine — synced with theme.toml",
        "autoload -U colors && colors",
        "setopt PROMPT_SUBST",
        "zmodload zsh/datetime 2>/dev/null || true",
        "",
        f"typeset -g NCUI_C_INTENT='{pal['intent']}'",
        f"typeset -g NCUI_C_FOCUS='{pal['focus']}'",
        f"typeset -g NCUI_C_VAL='{pal['focus_val']}'",
        f"typeset -g NCUI_C_SCHEMA='{pal['schema']}'",
        f"typeset -g NCUI_C_STRUCT='{pal['structure']}'",
        f"typeset -g NCUI_C_VITAL='{pal['vitality']}'",
        f"typeset -g NCUI_C_ATTN='{pal['attention']}'",
        f"typeset -g NCUI_C_INTR='{pal['interrupt']}'",
        f"typeset -g NCUI_C_FAULT='{pal['fault']}'",
        "",
        f"typeset -gi NCUI_OPT_GIT={b_git}",
        f"typeset -gi NCUI_OPT_EXIT={b_exit}",
        f"typeset -gi NCUI_OPT_DUR={b_dur}",
        f"typeset -gi NCUI_OPT_CLK={b_clk}",
        f"typeset -gi NCUI_OPT_CTX={b_ctx}",
        "",
        "typeset -g _ncui_cmd_start=''",
        "typeset -g _ncui_cmd_dur=''",
        "typeset -g _ncui_git_seg=''",
        "typeset -g _ncui_ctx_seg=''",
        "",
        "_ncui_preexec() {",
        "    if (( NCUI_OPT_DUR )); then",
        "        _ncui_cmd_start=\"${EPOCHREALTIME:-$SECONDS}\"",
        "    fi",
        "}",
        "",
        "_ncui_update_dur() {",
        "    _ncui_cmd_dur=''",
        "    (( NCUI_OPT_DUR )) || return 0",
        "    [[ -n \"$_ncui_cmd_start\" ]] || return 0",
        "    local now=\"${EPOCHREALTIME:-$SECONDS}\"",
        "    local delta=$(( now - _ncui_cmd_start ))",
        "    _ncui_cmd_start=''",
        "    if (( delta >= 2.0 )); then",
        "        if (( delta >= 60.0 )); then",
        "            local mins=$(( int(delta) / 60 ))",
        "            local secs=$(( int(delta) % 60 ))",
        "            _ncui_cmd_dur=\"${mins}m${secs}s\"",
        "        else",
        "            printf -v _ncui_cmd_dur '%.1fs' \"$delta\"",
        "        fi",
        "    fi",
        "}",
        "",
        "_ncui_update_git() {",
        "    _ncui_git_seg=''",
        "    (( NCUI_OPT_GIT )) || return 0",
        "    command -v git >/dev/null 2>&1 || return 0",
        "    local br",
        "    br=\"$(git symbolic-ref --quiet --short HEAD 2>/dev/null || git rev-parse --short HEAD 2>/dev/null)\" || return 0",
        "    [[ -n \"$br\" ]] || return 0",
        "    local dirty=''",
        "    [[ -n \"$(git status --porcelain --ignore-submodules -uno 2>/dev/null | head -n1)\" ]] && dirty='*'",
        "    if [[ -n \"$dirty\" ]]; then",
        "        _ncui_git_seg=\"%F{${NCUI_C_ATTN}}⎇ ${br}${dirty}%f\"",
        "    else",
        "        _ncui_git_seg=\"%F{${NCUI_C_VITAL}}⎇ ${br}%f\"",
        "    fi",
        "}",
        "",
        "_ncui_update_ctx() {",
        "    _ncui_ctx_seg=''",
        "    (( NCUI_OPT_CTX )) || return 0",
        "    local -a tags=()",
        "    (( EUID == 0 )) && tags+=('ROOT')",
        "    [[ -n \"${SSH_CONNECTION:-}\" ]] && tags+=('SSH')",
        "    [[ -d /run/recovery-ram ]] && tags+=('RECOVERY')",
        "    [[ -r /etc/debian_chroot ]] && tags+=('CHROOT')",
        "    [[ -n \"${VIRTUAL_ENV:-}\" ]] && tags+=(\"VENV:${VIRTUAL_ENV:t}\")",
        "    (( ${#tags[@]} > 0 )) && _ncui_ctx_seg=\"${(j:|:)tags}\"",
        "}",
        "",
        "_ncui_prompt_nightcity() {",
        "    local rc=$1",
        "    local ctx='' gitp='' rp_parts=()",
        "    [[ -n \"$_ncui_ctx_seg\" ]] && ctx=\"%F{${NCUI_C_STRUCT}}─[%F{${NCUI_C_ATTN}}${_ncui_ctx_seg}%F{${NCUI_C_STRUCT}}]%f\"",
        "    [[ -n \"$_ncui_git_seg\" ]] && gitp=\"%F{${NCUI_C_STRUCT}}─(${_ncui_git_seg}%F{${NCUI_C_STRUCT}})%f\"",
        "    local arrow=\"%F{${NCUI_C_INTENT}}❯%f\"",
        "    (( NCUI_OPT_EXIT && rc != 0 )) && arrow=\"%F{${NCUI_C_FAULT}}❯%f\"",
        "    PROMPT=\"%F{${NCUI_C_INTENT}}┌──(%F{${NCUI_C_FOCUS}}%n%F{${NCUI_C_SCHEMA}}@%F{${NCUI_C_VAL}}%m%F{${NCUI_C_INTENT}})${ctx}%F{${NCUI_C_STRUCT}}─[%F{${NCUI_C_FOCUS}}%~%F{${NCUI_C_STRUCT}}]${gitp}%f\"$'\\n'\"%F{${NCUI_C_INTENT}}└──${arrow} %f\"",
        "    (( NCUI_OPT_DUR )) && [[ -n \"$_ncui_cmd_dur\" ]] && rp_parts+=(\"%F{${NCUI_C_ATTN}}${_ncui_cmd_dur}%f\")",
        "    (( NCUI_OPT_EXIT && rc != 0 )) && rp_parts+=(\"%F{${NCUI_C_FAULT}}✗${rc}%f\")",
        "    (( NCUI_OPT_CLK )) && rp_parts+=(\"%F{${NCUI_C_SCHEMA}}%D{%H:%M}%f\")",
        "    RPROMPT=\"${(j:  :)rp_parts}\"",
        "}",
        "",
        "_ncui_prompt_netrunner() {",
        "    local rc=$1",
        "    local ctx='' gitp='' ex='' dur=''",
        "    [[ -n \"$_ncui_ctx_seg\" ]] && ctx=\"%F{${NCUI_C_STRUCT}}[%F{${NCUI_C_ATTN}}${_ncui_ctx_seg}%F{${NCUI_C_STRUCT}}] \"",
        "    [[ -n \"$_ncui_git_seg\" ]] && gitp=\"%F{${NCUI_C_STRUCT}}[${_ncui_git_seg}%F{${NCUI_C_STRUCT}}] \"",
        "    (( NCUI_OPT_DUR )) && [[ -n \"$_ncui_cmd_dur\" ]] && dur=\"%F{${NCUI_C_ATTN}}${_ncui_cmd_dur} \"",
        "    (( NCUI_OPT_EXIT && rc != 0 )) && ex=\"%F{${NCUI_C_FAULT}}[${rc}] \"",
        "    PROMPT=\"%F{${NCUI_C_STRUCT}}[%F{${NCUI_C_INTENT}}● %F{${NCUI_C_FOCUS}}%n@%m%F{${NCUI_C_STRUCT}}] ${ctx}[%F{${NCUI_C_VAL}}%~%F{${NCUI_C_STRUCT}}] ${gitp}${dur}${ex}%F{${NCUI_C_INTENT}}❯ %f\"",
        "    RPROMPT=\"$( (( NCUI_OPT_CLK )) && echo \"%F{${NCUI_C_SCHEMA}}%D{%H:%M}%f\" )\"",
        "}",
        "",
        "_ncui_prompt_cyberdeck() {",
        "    local rc=$1",
        "    local gitp='' ctx='' dur='' clk='' arrow=\"%F{${NCUI_C_INTENT}}▸%f\"",
        "    [[ -n \"$_ncui_git_seg\" ]] && gitp=\" %F{${NCUI_C_STRUCT}}── ${_ncui_git_seg}\"",
        "    [[ -n \"$_ncui_ctx_seg\" ]] && ctx=\" %F{${NCUI_C_STRUCT}}── %F{${NCUI_C_ATTN}}${_ncui_ctx_seg}\"",
        "    (( NCUI_OPT_DUR )) && [[ -n \"$_ncui_cmd_dur\" ]] && dur=\" %F{${NCUI_C_ATTN}}${_ncui_cmd_dur}\"",
        "    (( NCUI_OPT_CLK )) && clk=\" %F{${NCUI_C_SCHEMA}}%D{%H:%M}\"",
        "    (( NCUI_OPT_EXIT && rc != 0 )) && arrow=\"%F{${NCUI_C_FAULT}}▸ [${rc}]%f\"",
        "    PROMPT=\"%F{${NCUI_C_STRUCT}}── %F{${NCUI_C_INTENT}}%n@%m %F{${NCUI_C_STRUCT}}── %F{${NCUI_C_FOCUS}}%~${gitp}${ctx} %F{${NCUI_C_STRUCT}}────${dur}${clk} %F{${NCUI_C_STRUCT}}──%f\"$'\n'\"${arrow} %f\"",
        "    RPROMPT=''",
        "}",
        "",
        "_ncui_prompt_matrix() {",
        "    local rc=$1",
        "    local gitp='' dur='' ex='' ctx=''",
        "    [[ -n \"$_ncui_ctx_seg\" ]] && ctx=\"%F{${NCUI_C_ATTN}}${_ncui_ctx_seg} \"",
        "    [[ -n \"$_ncui_git_seg\" ]] && gitp=\" ${_ncui_git_seg}\"",
        "    (( NCUI_OPT_DUR )) && [[ -n \"$_ncui_cmd_dur\" ]] && dur=\" %F{${NCUI_C_ATTN}}· ${_ncui_cmd_dur}\"",
        "    (( NCUI_OPT_EXIT && rc != 0 )) && ex=\" %F{${NCUI_C_FAULT}}· ${rc}\"",
        "    PROMPT=\"${ctx}%F{${NCUI_C_INTENT}}▏ %F{${NCUI_C_FOCUS}}%n %F{${NCUI_C_INTENT}}▕ %F{${NCUI_C_VAL}}%~${gitp}${dur}${ex} %F{${NCUI_C_INTENT}}❯ %f\"",
        "    RPROMPT=\"$( (( NCUI_OPT_CLK )) && echo \"%F{${NCUI_C_SCHEMA}}%D{%H:%M}%f\" )\"",
        "}",
        "",
        "_ncui_prompt_hardened() {",
        "    local rc=$1",
        "    local ctx='' gitp='' ex='' rp_parts=()",
        "    [[ -n \"$_ncui_ctx_seg\" ]] && ctx=\"%F{${NCUI_C_STRUCT}}[%F{${NCUI_C_ATTN}}${_ncui_ctx_seg}%F{${NCUI_C_STRUCT}}] \"",
        "    [[ -n \"$_ncui_git_seg\" ]] && gitp=\" %F{${NCUI_C_STRUCT}}(${_ncui_git_seg}%F{${NCUI_C_STRUCT}})\"",
        "    (( NCUI_OPT_EXIT && rc != 0 )) && ex=\" %F{${NCUI_C_STRUCT}}[%F{${NCUI_C_FAULT}}EXIT ${rc}%F{${NCUI_C_STRUCT}}]\"",
        "    PROMPT=\"${ctx}%F{${NCUI_C_SCHEMA}}(%F{${NCUI_C_FOCUS}}%n@%m%F{${NCUI_C_SCHEMA}}) [%F{${NCUI_C_VAL}}%~%F{${NCUI_C_SCHEMA}}]${gitp}${ex}%f\"$'\n'\"%F{${NCUI_C_INTENT}}❯ %f\"",
        "    (( NCUI_OPT_DUR )) && [[ -n \"$_ncui_cmd_dur\" ]] && rp_parts+=(\"%F{${NCUI_C_ATTN}}${_ncui_cmd_dur}%f\")",
        "    (( NCUI_OPT_CLK )) && rp_parts+=(\"%F{${NCUI_C_SCHEMA}}%D{%H:%M}%f\")",
        "    RPROMPT=\"${(j:  :)rp_parts}\"",
        "}",
        "",
        "_ncui_precmd() {",
        "    local rc=$?",
        "    _ncui_update_dur",
        "    _ncui_update_git",
        "    _ncui_update_ctx",
        f"    {fn_name} \"$rc\"",
        "}",
        "",
        "autoload -Uz add-zsh-hook",
        "add-zsh-hook -D preexec _ncui_preexec 2>/dev/null || true",
        "add-zsh-hook -D precmd _ncui_precmd 2>/dev/null || true",
        "add-zsh-hook preexec _ncui_preexec",
        "add-zsh-hook precmd _ncui_precmd",
        "",
        "# Shell behavior",
        f"HISTSIZE={norm['hist_size']}",
        f"SAVEHIST={norm['hist_size']}",
        "HISTFILE=~/.zsh_history",
        "setopt APPEND_HISTORY",
        "setopt INC_APPEND_HISTORY",
        "setopt SHARE_HISTORY" if norm["share_history"] else "unsetopt SHARE_HISTORY",
        "setopt HIST_IGNORE_DUPS" if norm["hist_ignore_dups"] else "unsetopt HIST_IGNORE_DUPS",
        "setopt AUTO_CD" if norm["auto_cd"] else "unsetopt AUTO_CD",
    ]

    if norm["completion_menu"]:
        lines.extend([
            "mkdir -p ~/.cache/zsh 2>/dev/null || true",
            "autoload -Uz compinit && compinit -d ~/.cache/zsh/zcompdump",
            "zstyle ':completion:*' menu select",
            "if [ -x /usr/bin/dircolors ]; then",
            "    test -r ~/.dircolors && eval \"$(dircolors -b ~/.dircolors)\" || eval \"$(dircolors -b)\"",
            "    export ZLS_COLORS=\"${LS_COLORS}\"",
            "    zstyle ':completion:*' list-colors \"${(s.:.)LS_COLORS}\"",
            "fi",
        ])

    if norm["fix_keybindings"]:
        lines.extend([
            "bindkey '^[[3~' delete-char",
            "bindkey '^[[H' beginning-of-line",
            "bindkey '^[[F' end-of-line",
            "bindkey '^[[1;5C' forward-word",
            "bindkey '^[[1;5D' backward-word",
        ])

    if norm["plugin_autosuggestions"]:
        p_as = find_zsh_plugin_file("autosuggestions")
        if p_as:
            lines.append(f"[ -r '{p_as}' ] && source '{p_as}'")
    if norm["plugin_syntax_highlighting"]:
        p_sh = find_zsh_plugin_file("syntax_highlighting")
        if p_sh:
            lines.append(f"[ -r '{p_sh}' ] && source '{p_sh}'")

    return "\n".join(lines) + "\n"


def _patch_zshrc_source(theme_file: str, home_dir: str | None = None) -> tuple[bool, str]:
    rc_path = user_zshrc_path(home_dir)
    os.makedirs(os.path.dirname(rc_path), exist_ok=True)
    orig = open(rc_path, "r", encoding="utf-8", errors="ignore").read() if os.path.isfile(rc_path) else ""
    bak_path = f"{rc_path}.ncui.bak"
    if orig and not os.path.exists(bak_path):
        with open(bak_path, "w", encoding="utf-8") as bf:
            bf.write(orig)

    block = f'{_ZSHRC_BEGIN}\n[ -r "{theme_file}" ] && source "{theme_file}"\n{_ZSHRC_END}\n'
    if _ZSHRC_BEGIN in orig and _ZSHRC_END in orig:
        new_txt = re.sub(
            rf"{re.escape(_ZSHRC_BEGIN)}.*?{re.escape(_ZSHRC_END)}\n?",
            block,
            orig,
            flags=re.S,
        )
    else:
        # Comment out standalone PROMPT= lines outside our block so the NC-UI theme isn't overridden
        patched_lines = []
        for ln in orig.splitlines():
            if re.match(r"^\s*(?:export\s+)?PROMPT=", ln):
                patched_lines.append(f"# [ncui-replaced] {ln}")
            else:
                patched_lines.append(ln)
        base = "\n".join(patched_lines).rstrip("\n")
        new_txt = (base + "\n\n" if base else "") + block

    tmp = f"{rc_path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_txt)
    os.replace(tmp, rc_path)
    return True, rc_path


def apply_zsh_config(cfg: dict, *, cfg_dir: str | None = None, home_dir: str | None = None) -> tuple[bool, str]:
    norm = _norm_cfg(cfg)
    tf = zsh_theme_file_path(cfg_dir)
    os.makedirs(os.path.dirname(tf), exist_ok=True)
    script = generate_ncui_zsh_theme(norm)
    tmp_tf = f"{tf}.tmp.{os.getpid()}"
    with open(tmp_tf, "w", encoding="utf-8") as f:
        f.write(script)
    if shutil.which("zsh"):
        res = subprocess.run(["zsh", "-n", tmp_tf], capture_output=True, text=True, timeout=3.0)
        if res.returncode != 0:
            try:
                os.unlink(tmp_tf)
            except OSError:
                pass
            return False, (res.stderr or "zsh syntax check failed").strip()[:140]
    os.replace(tmp_tf, tf)
    save_zsh_config_toml(norm, cfg_dir=cfg_dir)
    _patch_zshrc_source(tf, home_dir=home_dir)
    return True, tf


def sync_zsh_theme_if_enabled(cfg_dir: str | None = None) -> bool:
    tf = zsh_theme_file_path(cfg_dir)
    if not os.path.isfile(tf):
        return False
    cfg = load_saved_zsh_config(cfg_dir)
    if not cfg.get("auto_sync_theme", True):
        return False
    script = generate_ncui_zsh_theme(cfg)
    tmp_tf = f"{tf}.tmp.{os.getpid()}"
    with open(tmp_tf, "w", encoding="utf-8") as f:
        f.write(script)
    os.replace(tmp_tf, tf)
    return True


def handle_cc_zsh_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_zsh:"):
        return False
    act = uri[len("cc_zsh:"):]
    cfg = get_editor_zsh_config()

    if act.startswith("preset:"):
        pid = act.split(":", 1)[1].strip()
        if pid not in ZSH_PRESET_IDS:
            return False
        cfg["preset"] = pid
        _set_editor_zsh_config(cfg)
        return True
    elif act.startswith("toggle:"):
        key = act.split(":", 1)[1].strip()
        if key == "plugin_autosuggestions" and not has_zsh_plugin("autosuggestions"):
            set_feedback("zsh", False, t("cc_zsh.optdep_autosuggestions"))
            return True
        if key == "plugin_syntax_highlighting" and not has_zsh_plugin("syntax_highlighting"):
            set_feedback("zsh", False, t("cc_zsh.optdep_syntax_highlighting"))
            return True
        if key not in _DEFAULT_ZSH_CFG or not isinstance(_DEFAULT_ZSH_CFG[key], bool):
            return False
        cfg[key] = not bool(cfg.get(key))
        _set_editor_zsh_config(cfg)
        return True
    elif act.startswith("sync:"):
        cfg["auto_sync_theme"] = (act.split(":", 1)[1] == "1")
        _set_editor_zsh_config(cfg)
        return True
    elif act.startswith("hist_size:"):
        try:
            hs = int(act.split(":", 1)[1])
        except ValueError:
            return False
        if hs not in HIST_SIZE_OPTIONS:
            return False
        cfg["hist_size"] = hs
        _set_editor_zsh_config(cfg)
        return True
    elif act == "reset":
        saved = load_saved_zsh_config()
        _set_editor_zsh_config(saved)
        set_feedback("zsh", True, t("cc_zsh.fb_reset"))
        return True
    elif act == "apply":
        ok, info = apply_zsh_config(cfg)
        if ok:
            _set_editor_zsh_config(load_saved_zsh_config())
            set_feedback("zsh", True, t("cc_zsh.fb_applied", p0=info))
        else:
            set_feedback("zsh", False, t("cc_zsh.fb_err", p0=info))
        return True
    elif act == "launch_wizard":
        if not probe.has_bin("zsh"):
            set_feedback("zsh", False, t("cc_zsh.optdep_zsh"))
            return True
        from ui.embedded_tui import start_panel_terminal

        def _on_exit() -> bool:
            STATE.set("zsh", "draft_cfg", copy.deepcopy(load_saved_zsh_config()), immediate=False)
            if cc_panel and getattr(cc_panel, "shell", None):
                cc_panel.shell.refresh_view()
            return False

        start_panel_terminal(
            cc_panel,
            t("cc_zsh.wizard_term_title"),
            [sys.executable, os.path.abspath(__file__), "--wizard"],
            icon="\uf120",
            on_exit=_on_exit,
        )
        return True
    return False


# =====================================================================
# Interactive Guided Zsh Setup Wizard (Embedded Terminal)
# =====================================================================

def _ask_choice(prompt_txt: str, valid: set[str], default: str, c_int: str, c_foc: str, c_sch: str, c_rst: str) -> str:
    while True:
        sys.stdout.write(f"  {c_int}›{c_rst} {c_foc}{prompt_txt}{c_rst} {c_sch}[{default}]{c_rst}: ")
        sys.stdout.flush()
        raw = sys.stdin.readline()
        if not raw:
            raise KeyboardInterrupt
        ans = raw.strip().lower()
        if ans == "q":
            raise KeyboardInterrupt
        if not ans:
            return default
        if ans in valid:
            return ans


def _ask_yes_no(label: str, default: bool, c_int: str, c_foc: str, c_sch: str, c_rst: str) -> bool:
    d_str = "Y/n" if default else "y/N"
    ans = _ask_choice(f"{label} ({d_str})", {"y", "yes", "s", "si", "n", "no"}, "y" if default else "n", c_int, c_foc, c_sch, c_rst)
    return ans in ("y", "yes", "s", "si")


def _print_ansi_preview(cfg: dict, pal: dict[str, str], c_rst: str) -> None:
    l1, l2 = _render_preview_tokens(cfg)
    for ln in (l1, l2):
        if not ln:
            continue
        sys.stdout.write("    " + "".join(f"{_hex_to_ansi(pal.get(role, pal['focus_val']))}{txt}{c_rst}" for txt, role in ln) + "\n")


def run_zsh_wizard() -> int:
    pal = _palette_hex()
    c_rst = "\033[0m"
    c_int = _hex_to_ansi(pal["intent"], bold=True)
    c_foc = _hex_to_ansi(pal["focus"], bold=True)
    c_val = _hex_to_ansi(pal["focus_val"])
    c_sch = _hex_to_ansi(pal["schema"])
    c_str = _hex_to_ansi(pal["structure"])
    c_vit = _hex_to_ansi(pal["vitality"], bold=True)
    c_att = _hex_to_ansi(pal["attention"], bold=True)

    cfg = copy.deepcopy(load_saved_zsh_config())
    try:
        sys.stdout.write(f"\n  {c_int}╔══════════════════════════════════════════════════════════════════╗{c_rst}\n")
        sys.stdout.write(f"  {c_int}║{c_rst} {c_foc}NC-UI ZSH CYBERPUNK SETUP WIZARD{c_rst}                                 {c_int}║{c_rst}\n")
        sys.stdout.write(f"  {c_int}╚══════════════════════════════════════════════════════════════════╝{c_rst}\n")
        sys.stdout.write(f"  {c_sch}Answer step by step (Enter = keep default, 'q' or Ctrl+C = cancel).{c_rst}\n\n")

        # Step 1: Cyberpunk Preset
        sys.stdout.write(f"  {c_att}[1/5]{c_rst} {c_foc}Choose Cyberpunk Prompt Aesthetic:{c_rst}\n")
        cur_idx = str(ZSH_PRESET_IDS.index(cfg["preset"]) + 1)
        for i, pid in enumerate(ZSH_PRESET_IDS, start=1):
            mark = f"{c_int}●{c_rst}" if pid == cfg["preset"] else f"{c_sch}○{c_rst}"
            sys.stdout.write(f"  {mark} {c_foc}{i}){c_rst} {c_val}{pid}{c_rst}\n")
            _print_ansi_preview({**cfg, "preset": pid}, pal, c_rst)
        pick = _ask_choice("Select preset (1-5)", {"1", "2", "3", "4", "5"}, cur_idx, c_int, c_foc, c_sch, c_rst)
        cfg["preset"] = ZSH_PRESET_IDS[int(pick) - 1]
        sys.stdout.write("\n")

        # Step 2: Prompt Modules
        sys.stdout.write(f"  {c_att}[2/5]{c_rst} {c_foc}Prompt Telemetry Modules:{c_rst}\n")
        cfg["show_git"] = _ask_yes_no("Show Git branch & dirty state", cfg["show_git"], c_int, c_foc, c_sch, c_rst)
        cfg["show_exit"] = _ask_yes_no("Show non-zero exit code indicator", cfg["show_exit"], c_int, c_foc, c_sch, c_rst)
        cfg["show_duration"] = _ask_yes_no("Show execution duration for commands >= 2s", cfg["show_duration"], c_int, c_foc, c_sch, c_rst)
        cfg["show_clock"] = _ask_yes_no("Show right-side clock (RPROMPT)", cfg["show_clock"], c_int, c_foc, c_sch, c_rst)
        cfg["show_context"] = _ask_yes_no("Show ROOT/SSH/CHROOT/RECOVERY/VENV context badges", cfg["show_context"], c_int, c_foc, c_sch, c_rst)
        sys.stdout.write("\n")

        # Step 3: Palette Auto-Sync
        sys.stdout.write(f"  {c_att}[3/5]{c_rst} {c_foc}NC-UI Palette Synchronization:{c_rst}\n")
        cfg["auto_sync_theme"] = _ask_yes_no("Automatically re-color Zsh whenever NC-UI theme changes", cfg["auto_sync_theme"], c_int, c_foc, c_sch, c_rst)
        sys.stdout.write("\n")

        # Step 4: Shell Behavior & Plugins
        sys.stdout.write(f"  {c_att}[4/5]{c_rst} {c_foc}Shell Behavior & Optional Plugins:{c_rst}\n")
        h_map = {"1": 5000, "2": 10000, "3": 50000}
        h_def = next((k for k, v in h_map.items() if v == cfg["hist_size"]), "2")
        h_pick = _ask_choice("History size: 1=5000  2=10000  3=50000", {"1", "2", "3"}, h_def, c_int, c_foc, c_sch, c_rst)
        cfg["hist_size"] = h_map[h_pick]
        cfg["share_history"] = _ask_yes_no("Share history live across Zsh terminals (SHARE_HISTORY)", cfg["share_history"], c_int, c_foc, c_sch, c_rst)
        cfg["hist_ignore_dups"] = _ask_yes_no("Ignore consecutive duplicate commands", cfg["hist_ignore_dups"], c_int, c_foc, c_sch, c_rst)
        cfg["completion_menu"] = _ask_yes_no("Enable colored Zsh completion menu + dircolors", cfg["completion_menu"], c_int, c_foc, c_sch, c_rst)
        cfg["fix_keybindings"] = _ask_yes_no("Bind Home/End/Delete/Ctrl+Arrows properly", cfg["fix_keybindings"], c_int, c_foc, c_sch, c_rst)
        cfg["auto_cd"] = _ask_yes_no("Enable AUTO_CD (type folder path directly to cd)", cfg["auto_cd"], c_int, c_foc, c_sch, c_rst)
        if has_zsh_plugin("autosuggestions"):
            cfg["plugin_autosuggestions"] = _ask_yes_no("Enable zsh-autosuggestions", cfg["plugin_autosuggestions"], c_int, c_foc, c_sch, c_rst)
        else:
            sys.stdout.write(f"  {c_sch}  (zsh-autosuggestions not installed — skipped){c_rst}\n")
        if has_zsh_plugin("syntax_highlighting"):
            cfg["plugin_syntax_highlighting"] = _ask_yes_no("Enable zsh-syntax-highlighting", cfg["plugin_syntax_highlighting"], c_int, c_foc, c_sch, c_rst)
        else:
            sys.stdout.write(f"  {c_sch}  (zsh-syntax-highlighting not installed — skipped){c_rst}\n")
        sys.stdout.write("\n")

        # Step 5: Preview & Apply
        sys.stdout.write(f"  {c_att}[5/5]{c_rst} {c_foc}Final Preview:{c_rst}\n")
        _print_ansi_preview(cfg, pal, c_rst)
        sys.stdout.write(f"\n  {c_str}Target theme:{c_rst} {c_val}{zsh_theme_file_path()}{c_rst}\n")
        sys.stdout.write(f"  {c_str}Target rc:   {c_rst} {c_val}{user_zshrc_path()}{c_rst}\n\n")
        if not _ask_yes_no("Apply configuration now", True, c_int, c_foc, c_sch, c_rst):
            sys.stdout.write(f"\n  {c_sch}Cancelled — no files modified.{c_rst}\n")
            return 0
        ok, info = apply_zsh_config(cfg)
        if not ok:
            sys.stderr.write(f"\n  Error applying Zsh theme: {info}\n")
            return 1
        sys.stdout.write(f"\n  {c_vit}✓ Applied!{c_rst} {c_val}Run 'source ~/.zshrc' or open a new terminal.{c_rst}\n")
        return 0
    except KeyboardInterrupt:
        sys.stdout.write(f"\n  {c_sch}Wizard aborted — no changes written.{c_rst}\n")
        return 0


if __name__ == "__main__":
    if "--wizard" in sys.argv[1:]:
        pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if pkg_root not in sys.path:
            sys.path.insert(0, pkg_root)
        raise SystemExit(run_zsh_wizard())
