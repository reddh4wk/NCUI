#!/usr/bin/env python3
from core.config import THEME
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import Box, Divider, Slider, Txt, ActionBtn, StateBadge
from ui.widgets import AsciiCard
from panels.base import BasePanel
from panels.cc_engine_sound import (
    speaker_icon,
    get_max_volume,
    get_default_sink_status,
    get_audio_devices,
    get_sink_inputs,
    set_default_device,
    step_device_volume,
    handle_cc_audio_link,
)

AUDIO_PANEL_COLS = 56


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
            empty_box.add_row(" ")
            empty_box.add_row(f"  {Txt(msg, role='inactive')}")
            empty_box.add_row(" ")
            return [empty_box]
        for d in devices:
            icon = ("\uf131" if d["mute"] else "\uf130") if is_input else speaker_icon(d["vol"], d["mute"])
            eff_max_desc = max_desc if (max_desc > 45 or not d["is_default"]) else 29
            disp_desc = canvas.truncate_plain(d["desc"], eff_max_desc)
            mute_btn = ActionBtn(
                t("audio.unmute") if d["mute"] else t("audio.mute"),
                f"aud:{prefix}:mute:{d['name']}",
                tone="warn" if d["mute"] else "neutral",
            )
            def_badge = [StateBadge(True, t("audio.default_badge"), on_tone="ok")] if d["is_default"] else []
            box = Box(
                disp_desc,
                icon=icon,
                actions=def_badge,
                highlighted=False,
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            box.add_row(" ")
            slider_m = Slider(d["vol"], f"aud:{prefix}:vol:{d['name']}", min_val=0, max_val=max_vol, width=15, show_pct=True, scroll_prefix=f"aud:{prefix}:scroll:{d['name']}")
            st_badge = StateBadge(
                not d["mute"],
                d["state"],
                t("audio.mute_2"),
                on_tone="ok" if d["state"] == "RUNNING" else "ok_soft",
            )
            vol_lbl = f"{t('audio.vol_label'):<6}"
            st_lbl = f"{t('audio.state_label'):<6}"
            box.add_row(f"  {Txt(vol_lbl, role='schema')} {slider_m}")
            box.add_row(
                f"  {Txt(st_lbl, role='schema')} {st_badge}",
                right_markup=mute_btn,
            )
            box.add_row(" ")
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
        max_desc = 58 if expanded else 40
        max_vol = get_max_volume()
        cards = []

        if expanded:
            cards.append(Divider(t("audio.output_devices"), icon="\uf028", align="right"))
        cards.extend(self._build_device_cards(get_audio_devices(is_input=False), False, canvas, target_cols, max_desc))

        if expanded:
            cards.append(Divider(t("audio.input_devices"), icon="\uf130", align="right"))
        cards.extend(self._build_device_cards(get_audio_devices(is_input=True), True, canvas, target_cols, max_desc))

        if expanded:
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
            empty_mixer.add_row(" ")
            empty_mixer.add_row(f"  {Txt(t('audio.no_active_apps'), role='inactive')}")
            empty_mixer.add_row(" ")
            cards.append(empty_mixer)
        else:
            for app in apps:
                app_ic = "\uf6a9" if app["mute"] else "\uf001"
                app_title = canvas.truncate_plain(app["label"], max_desc)
                app_mute = ActionBtn(
                    t("audio.unmute") if app["mute"] else t("audio.mute"),
                    f"aud:app:mute:{app['index']}",
                    tone="warn" if app["mute"] else "neutral",
                )
                app_box = Box(
                    app_title,
                    icon=app_ic,
                    fixed_width=True,
                    min_cols=target_cols,
                    max_cols=target_cols,
                )
                app_box.add_row(" ")
                app_slider = Slider(app["vol"], f"aud:app:vol:{app['index']}", min_val=0, max_val=max_vol, width=14, show_pct=True, scroll_prefix=f"aud:app:scroll:{app['index']}")
                app_box.add_row(
                    f"  {Txt(t('audio.mixer_label'), role='schema')} {app_slider}",
                    right_markup=app_mute,
                )
                app_box.add_row(" ")
                cards.append(app_box)
        return cards

    def _select_default_and_close(self, is_input: bool, dev_name: str) -> None:
        set_default_device(is_input, dev_name)
        if self.shell:
            self.shell.dispatch_uri("ui:close")

    def on_link(self, uri: str) -> None:
        handle_cc_audio_link(self, uri)

    def on_bar_action(self, action: str, extra: list = None) -> None:
        if action in ("vol-up", "vol-down"):
            devs = get_audio_devices(is_input=False)
            def_dev = next((d for d in devs if d.get("is_default")), devs[0] if devs else None)
            if def_dev:
                step_device_volume(False, def_dev["name"], 5 if action == "vol-up" else -5)


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return AudioPanel(panel_id, shell)
