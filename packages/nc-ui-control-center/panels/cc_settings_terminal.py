#!/usr/bin/env python3
"""NC-UI Control Center — Interactive Embedded Terminal (`panels/cc_settings_terminal.py`)."""

import os
import shutil

from core.config import THEME
from core.i18n import t
from panels.cc_engine_resources import (
    get_active_tui,
    get_or_start_tui_session,
    set_active_tui,
)
from ui.canvas import Canvas
from ui.embedded_tui import EmbeddedTuiWidget, _compute_single_tui_size
from ui.kit import ActionBtn, Box
from ui.widgets import AsciiCard

SETTINGS_TERM_ID = "settings_terminal"


def _resolve_shell() -> tuple[str, list[str]]:
    shell_bin = os.environ.get("SHELL") or shutil.which("zsh") or shutil.which("bash") or "/bin/sh"
    home_dir = os.path.expanduser("~")
    cmd = ["env", "-C", home_dir, shell_bin] if os.path.isdir(home_dir) else [shell_bin]
    return os.path.basename(shell_bin), cmd


def build_cards(cc_panel, canvas: Canvas) -> list:
    shell_name, cmd = _resolve_shell()
    title = t("cc_settings.term_title", p0=shell_name)
    acts = [
        ActionBtn(t("cc_settings.term_clear"), "cc_set:term_clear", tone="neutral"),
        ActionBtn(t("cc_settings.term_restart"), "cc_set:term_restart", tone="neutral"),
    ]
    term_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)
    cols, rows, target_h = _compute_single_tui_size(
        cc_panel,
        canvas,
        [],
        title,
        "\uf120",
        min_rows=16,
        font_size_pt=term_pt,
        flush=True,
        actions=acts,
        has_footer=False,
    )
    sess = get_or_start_tui_session(SETTINGS_TERM_ID, cmd, rows=rows, cols=cols, cc_panel=cc_panel)
    if not get_active_tui():
        set_active_tui(SETTINGS_TERM_ID, shell=getattr(cc_panel, "shell", None))

    box = Box(title, icon="\uf120", actions=acts, highlighted=False, open_bottom=True)
    box.cairo_close_bottom = True
    shell = getattr(cc_panel, "shell", None)
    bot_inset = shell.get_cairo_bottom_inset_px() if (shell is not None and hasattr(shell, "get_cairo_bottom_inset_px")) else 0
    term_w = EmbeddedTuiWidget(
        SETTINGS_TERM_ID,
        sess,
        map_width=int(round(canvas.total_px)),
        char_w=canvas.char_w,
        target_height_px=target_h,
        mouse_reporting=False,
        show_cursor=True,
        font_size_pt=term_pt,
        flush=True,
        bot_inset_px=bot_inset,
    )
    return [AsciiCard(box, canvas, embedded_widget=term_w, footer_box_model=None)]
