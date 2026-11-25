#!/usr/bin/env python3
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, SelectPill, Slider, Txt
from panels.cc_engine_sound import (
    get_max_volume,
    get_sink_inputs,
    get_source_outputs,
    _feedback_cards,
    _get_pw_clock_meta,
)


def _build_stream_table_box(
    title: str,
    icon: str,
    streams: list[dict],
    scope: str,
    empty_msg: str,
    max_vol: int,
    canvas: Canvas,
) -> Box:
    box = Box(title, icon=icon, highlighted=False)
    if not streams:
        box.add_row(f"  {Txt(empty_msg, role='inactive')}")
        return box

    min_btn_chars = max(len(t("audio.mute")), len(t("audio.unmute")), 6)
    rows = []
    for a in streams:
        sid = a["index"]
        sl = Slider(
            a["vol"],
            f"cc_aud:{scope}:vol:{sid}",
            min_val=0,
            max_val=max_vol,
            width=20,
            show_pct=True,
            step_buttons=True,
            scroll_prefix=f"cc_aud:{scope}:scroll:{sid}",
            enabled=not a["mute"],
        )
        mb = ActionBtn(
            t("audio.unmute") if a["mute"] else t("audio.mute"),
            f"cc_aud:{scope}:mute:{sid}",
            tone="apply" if a["mute"] else "danger_soft",
            min_chars=min_btn_chars,
        )
        rows.append([
            Txt(canvas.truncate_plain(a["label"], 26), role="focus"),
            sl,
            mb,
        ])
    box.add_table(
        headers=[t("cc_audio.col_app"), t("cc_audio.col_volume"), t("cc_audio.col_action")],
        widths=[26, 36, 10],
        rows=rows,
        aligns=["left", "left", "right"],
        show_headers=True,
    )
    return box


def _build_mixer_standard(cc_panel, canvas: Canvas) -> list:
    out_apps = get_sink_inputs()
    in_apps = get_source_outputs()
    max_vol = get_max_volume()
    quantum, rate = _get_pw_clock_meta()
    cards = _feedback_cards()

    pw_box = Box(
        t("cc_audio.pipewire_dsp_clock_quantum_low_lat"),
        icon="\uf1de",
        actions=[Txt(f"Q:{quantum}  R:{rate}", role="schema")],
        highlighted=False,
    )
    auto_lbl = t("cc_audio.auto")
    q_btns = " ".join(SelectPill(auto_lbl if q == "0" else q, f"cc_aud:pw_q:{q}", selected=(quantum == ("auto" if q == "0" else q))) for q in ("0", "64", "128", "256", "512", "1024"))
    r_btns = " ".join(SelectPill(auto_lbl if r == "0" else f"{int(r)//1000}k", f"cc_aud:pw_r:{r}", selected=(rate == ("auto" if r == "0" else r))) for r in ("0", "44100", "48000", "96000", "192000"))
    pw_box.add_kv(t("cc_audio.clock_quantum"), q_btns, requires=["pw-metadata"])
    pw_box.add_kv(t("cc_audio.sample_rate"), r_btns, requires=["pw-metadata"])
    cards.append(pw_box)

    cards.append(
        _build_stream_table_box(
            t("cc_audio.output_streams", p0=len(out_apps)),
            "\uf028",
            out_apps,
            "app",
            t("audio.no_active_apps"),
            max_vol,
            canvas,
        )
    )
    cards.append(
        _build_stream_table_box(
            t("cc_audio.input_streams", p0=len(in_apps)),
            "\uf130",
            in_apps,
            "rec",
            t("cc_audio.no_active_input_apps"),
            max_vol,
            canvas,
        )
    )
    return cards


def build_cards(cc_panel, canvas: Canvas) -> list:
    return _build_mixer_standard(cc_panel, canvas)
