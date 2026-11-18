#!/usr/bin/env python3
from ui.canvas import Canvas
from panels.cc_sound_output import _build_io_standard


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_io_standard(cc_panel, canvas, is_input=True)
