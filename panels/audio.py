#!/usr/bin/env python3
from core.config import THEME
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import Box, Divider, Slider, Txt, ActionBtn, StateBadge
from ui.widgets import AsciiCard
from panels.base import BasePanel
from panels.cc_audio import (
    speaker_icon,
    get_max_volume,
    get_default_sink_status,
    get_audio_devices,
    get_sink_inputs,
    set_default_device,
    toggle_device_mute,
    set_device_volume,
    step_device_volume,
    toggle_app_mute,
    set_app_volume,
    step_app_volume,
)

AUDIO_PANEL_COLS = 42


class AudioPanel(BasePanel):
    hover_highlight = True

    def __init__(self, panel_id: str = "audio", shell=None):
        self.id = panel_id
        self.title = t("audio.title")
        self.icon = "\uf028"
        super().__init__(shell)

    def get_cols(self, force_expanded: bool = False) -> int:
        if force_expanded or self.is_expanded:
            return int(THEME["geometry"]["expanded_cols"])
        return AUDIO_PANEL_COLS

    def get_bar_pill(self) -> dict:
        vol, muted = get_default_sink_status()
        ic = speaker_icon(vol, muted)
        return {"text": Txt(ic, role="focus" if not muted else "inactive"), "tooltip": "", "class": "module-nc"}

    def _build_device_cards(self, devices: list[dict], is_input: bool, canvas: Canvas, target_cols: int, max_desc: int) -> list:
        prefix = "in" if is_input else "out"
        max_vol = get_max_volume()
        cards = []
        if not devices:
            empty_box = Box(
                t("audio.no_device_box"),
                icon="\uf131" if is_input else "\uf6a9",
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            msg = t("audio.no_input_detected") if is_input else t("audio.no_output_detected")
            empty_box.add_row(Txt(msg, role="inactive"))
            return [empty_box]
        for d in devices:
            icon = ("\uf131" if d["mute"] else "\uf130") if is_input else speaker_icon(d["vol"], d["mute"])
            disp_desc = canvas.truncate_plain(d["desc"], max_desc)
            mute_btn = ActionBtn(
                t("audio.unmute") if d["mute"] else t("audio.mute"),
                f"aud:{prefix}:mute:{d['name']}",
                tone="warn" if d["mute"] else "neutral",
                active=bool(d["mute"]),
            )
            def_badge = [StateBadge(True, t("audio.default_badge", "DEFAULT"), on_tone="ok")] if d["is_default"] else []
            box = Box(
                disp_desc,
                icon=icon,
                actions=def_badge,
                elected=d["is_default"],
                scroll_action=f"aud:{prefix}:scroll:{d['name']}",
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            slider_m = Slider(d["vol"], f"aud:{prefix}:vol:{d['name']}", min_val=0, max_val=max_vol, width=15, show_pct=True)
            st_badge = StateBadge(
                not d["mute"],
                d["state"],
                "MUTE",
                on_tone="ok" if d["state"] == "RUNNING" else "ok_soft",
            )
            vol_lbl = f"{t('audio.vol_label'):<6}"
            st_lbl = f"{t('audio.state_label'):<6}"
            sp_lbl = f"{t('audio.spec_label'):<6}"
            box.add_row(f"  {Txt(vol_lbl, role='schema')} {slider_m}")
            box.add_row(f"  {Txt(st_lbl, role='schema')} {st_badge}")
            box.add_row(
                f"  {Txt(sp_lbl, role='schema')} {Txt(d['spec'], role='val')}",
                right_markup=mute_btn,
            )
            cards.append(
                AsciiCard(
                    box,
                    canvas,
                    on_link=self.shell.dispatch_uri if self.shell else None,
                    on_row_click=(lambda name=d["name"], inp=is_input: self._select_default_and_close(inp, name)),
                )
            )
        return cards

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        expanded = force_expanded or self.is_expanded
        target_cols = int(THEME["geometry"]["expanded_cols"]) if expanded else AUDIO_PANEL_COLS
        canvas.set_cols(target_cols)
        max_desc = 58 if expanded else 29
        max_vol = get_max_volume()
        cards = []

        cards.append(Divider(t("audio.output_devices"), icon="\uf028", align="right"))
        cards.extend(self._build_device_cards(get_audio_devices(is_input=False), False, canvas, target_cols, max_desc))

        cards.append(Divider(t("audio.input_devices"), icon="\uf130", align="right"))
        cards.extend(self._build_device_cards(get_audio_devices(is_input=True), True, canvas, target_cols, max_desc))

        cards.append(Divider(t("audio.app_mixer"), icon="\uf1de", align="right"))
        apps = get_sink_inputs()
        if not apps:
            empty_mixer = Box(
                t("audio.app_mixer").title(),
                icon="\uf1de",
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            empty_mixer.add_row(Txt(t("audio.no_active_apps"), role="inactive"))
            cards.append(empty_mixer)
        else:
            for app in apps:
                app_ic = "\uf6a9" if app["mute"] else "\uf001"
                app_title = canvas.truncate_plain(app["label"], max_desc)
                app_mute = ActionBtn(
                    t("audio.unmute") if app["mute"] else t("audio.mute"),
                    f"aud:app:mute:{app['index']}",
                    tone="warn" if app["mute"] else "neutral",
                    active=bool(app["mute"]),
                )
                app_box = Box(
                    app_title,
                    icon=app_ic,
                    scroll_action=f"aud:app:scroll:{app['index']}",
                    fixed_width=True,
                    min_cols=target_cols,
                    max_cols=target_cols,
                )
                app_slider = Slider(app["vol"], f"aud:app:vol:{app['index']}", min_val=0, max_val=max_vol, width=14, show_pct=True)
                app_box.add_row(
                    f"  {Txt(t('audio.mixer_label'), role='schema')} {app_slider}",
                    right_markup=app_mute,
                )
                cards.append(app_box)
        return cards

    def _select_default_and_close(self, is_input: bool, dev_name: str) -> None:
        set_default_device(is_input, dev_name)
        if self.shell:
            self.shell.dispatch_uri("ui:close")

    def on_link(self, uri: str) -> None:
        parts = uri.split(":", 3)
        if len(parts) < 4 or parts[0] != "aud":
            return
        _, scope, action, rest = parts
        if scope in ("out", "in"):
            is_in = (scope == "in")
            if action == "def":
                set_default_device(is_in, rest)
            elif action == "mute":
                toggle_device_mute(is_in, rest)
            elif action == "vol":
                name, val = rest.rsplit(":", 1)
                set_device_volume(is_in, name, int(val))
            elif action == "scroll":
                name, direction = rest.rsplit(":", 1)
                step_device_volume(is_in, name, 5 if direction == "up" else -5)
        elif scope == "app":
            if action == "mute":
                toggle_app_mute(int(rest))
            elif action == "vol":
                idx, val = rest.rsplit(":", 1)
                set_app_volume(int(idx), int(val))
            elif action == "scroll":
                idx, direction = rest.rsplit(":", 1)
                step_app_volume(int(idx), 5 if direction == "up" else -5)
        self.request_throttled_refresh()


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return AudioPanel(panel_id, shell)
