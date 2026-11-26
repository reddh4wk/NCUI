#!/usr/bin/env python3
"""Shared adaptive NC-UI CLI theme loader & terminal UI primitives (mirrors nc-cli-ui.sh)."""

import os
import sys

from core.config import THEME

_JUST_HEADER = False


def _hex_to_fg(hex_col: str) -> str:
    h = str(hex_col or "").strip().lstrip("#")
    if len(h) != 6:
        return ""
    try:
        return f"\033[38;2;{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}m"
    except ValueError:
        return ""


def _palette() -> dict[str, str]:
    col = THEME.get("colors") or {}
    asc = (THEME.get("ascii") or {}).get("chars") or {}
    use_clr = sys.stdout.isatty() and ("NO_COLOR" not in os.environ)
    res: dict[str, str] = {
        "tl": str(asc.get("tl") or "╭"),
        "tr": str(asc.get("tr") or "╮"),
        "bl": str(asc.get("bl") or "╰"),
        "br": str(asc.get("br") or "╯"),
        "h": str(asc.get("h") or "─"),
        "v": str(asc.get("v") or "│"),
    }
    if use_clr:
        res.update({
            "reset": "\033[0m",
            "bold": "\033[1m",
            "struct": _hex_to_fg(col.get("structure", "#3e465c")),
            "schema": _hex_to_fg(col.get("schema", "#8b9bb8")),
            "dim": _hex_to_fg(col.get("inactive", "#596278")),
            "focus": _hex_to_fg(col.get("focus", "#ffffff")),
            "val": _hex_to_fg(col.get("focus_val", "#dce6f7")),
            "intent": _hex_to_fg(col.get("intent", "#00f0ff")),
            "ok": _hex_to_fg(col.get("vitality", "#00f57a")),
            "warn": _hex_to_fg(col.get("attention", "#ffe619")),
            "err": _hex_to_fg(col.get("fault", "#ff2442")),
        })
    else:
        for k in ("reset", "bold", "struct", "schema", "dim", "focus", "val", "intent", "ok", "warn", "err"):
            res[k] = ""
    return res


def _wrap(key: str, text: str, bold: bool = False) -> str:
    p = _palette()
    pre = (p["bold"] if bold else "") + p.get(key, "")
    return f"{pre}{text}{p['reset']}" if pre else str(text)


def c_focus(text: str) -> str:
    return _wrap("focus", text, bold=True)


def c_val(text: str) -> str:
    return _wrap("val", text)


def c_intent(text: str) -> str:
    return _wrap("intent", text)


def c_ok(text: str) -> str:
    return _wrap("ok", text)


def c_warn(text: str) -> str:
    return _wrap("warn", text)


def c_err(text: str) -> str:
    return _wrap("err", text)


def c_dim(text: str) -> str:
    return _wrap("dim", text)


def c_struct(text: str) -> str:
    return _wrap("struct", text)


def msg_info(msg: str) -> None:
    p = _palette()
    print(f"  {p['schema']}::{p['reset']} {msg}")


def msg_step(msg: str) -> None:
    p = _palette()
    print(f"  {p['intent']}⯈{p['reset']} {p['val']}{msg}{p['reset']}")


def msg_ok(msg: str) -> None:
    p = _palette()
    print(f"  {p['ok']}✓{p['reset']} {p['val']}{msg}{p['reset']}")


def msg_warn(msg: str) -> None:
    p = _palette()
    print(f"  {p['warn']}!{p['reset']} {p['warn']}{msg}{p['reset']}", file=sys.stderr)


def msg_err(msg: str) -> None:
    p = _palette()
    print(f"  {p['err']}✗ ERROR:{p['reset']} {msg}", file=sys.stderr)


def ui_header(title: str, subtitle: str = "") -> None:
    global _JUST_HEADER
    p = _palette()
    inner = 66
    if len(title) > inner - 3:
        title = title[: inner - 3]
    pad = max(1, inner - 2 - len(title))
    print(f"\n  {p['struct']}{p['tl']}{p['h'] * inner}{p['tr']}{p['reset']}")
    print(
        f"  {p['struct']}{p['v']}{p['reset']}  "
        f"{p['bold']}{p['focus']}{title}{p['reset']}"
        f"{' ' * pad}{p['struct']}{p['v']}{p['reset']}"
    )
    if subtitle:
        if len(subtitle) > inner - 3:
            subtitle = subtitle[: inner - 6] + "..."
        spad = max(1, inner - 2 - len(subtitle))
        print(
            f"  {p['struct']}{p['v']}{p['reset']}  "
            f"{p['schema']}{subtitle}{p['reset']}"
            f"{' ' * spad}{p['struct']}{p['v']}{p['reset']}"
        )
    print(f"  {p['struct']}{p['bl']}{p['h'] * inner}{p['br']}{p['reset']}")
    _JUST_HEADER = True


def ui_section(tag: str, title: str = "") -> None:
    global _JUST_HEADER
    p = _palette()
    width = 68
    sys.stdout.write("\n" if _JUST_HEADER else "\n\n")
    _JUST_HEADER = False
    if not title:
        used = 5 + len(tag) + 1
        fill = max(4, width - used)
        print(
            f"  {p['struct']}{p['h'] * 2} {p['bold']}{p['focus']}{tag}{p['reset']} "
            f"{p['struct']}{p['h'] * fill}{p['reset']}"
        )
    else:
        used = 5 + len(tag) + 3 + len(title) + 1
        fill = max(4, width - used)
        print(
            f"  {p['struct']}{p['h'] * 2} {p['intent']}{tag}{p['reset']} "
            f"{p['struct']}·{p['reset']} {p['bold']}{p['focus']}{title}{p['reset']} "
            f"{p['struct']}{p['h'] * fill}{p['reset']}"
        )


def ui_kv(key: str, val: str, extra: str = "", key_w: int = 16) -> None:
    global _JUST_HEADER
    _JUST_HEADER = False
    p = _palette()
    if extra:
        print(f"  {p['schema']}{key:<{key_w}}{p['reset']} {p['val']}{val}{p['reset']} {p['dim']}{extra}{p['reset']}")
    else:
        print(f"  {p['schema']}{key:<{key_w}}{p['reset']} {p['val']}{val}{p['reset']}")


def ui_help_row(key: str, desc: str, w: int = 26) -> None:
    global _JUST_HEADER
    _JUST_HEADER = False
    p = _palette()
    print(f"  {p['intent']}{key:<{w}}{p['reset']} {p['val']}{desc}{p['reset']}")
