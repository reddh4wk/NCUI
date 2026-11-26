#!/usr/bin/env python3
import glob
import json
import os
import time
from core import probe, wallpaper as wp
from core.i18n import t
from ui.canvas import Canvas
from ui.kit import Box, Slider, Txt, ActionBtn, StateBadge, set_slider_meta_value, sync_boxes_width
from ui.output_map import NiriOutputMap
from panels.base import BasePanel


def _get_backlight_info() -> dict | None:
    devs = sorted(glob.glob("/sys/class/backlight/*"))
    if not devs:
        return None
    def _rank(p: str) -> tuple[int, str]:
        bn = os.path.basename(p)
        if bn.startswith("intel_backlight"):
            return (0, bn)
        if bn.startswith("amdgpu_bl"):
            return (1, bn)
        if bn.startswith("acpi_video"):
            return (2, bn)
        return (3, bn)
    for d in sorted(devs, key=_rank):
        try:
            with open(os.path.join(d, "max_brightness"), "r", encoding="utf-8") as f:
                max_b = int(f.read().strip())
            with open(os.path.join(d, "brightness"), "r", encoding="utf-8") as f:
                cur = int(f.read().strip())
            if max_b <= 0:
                continue
            pct = max(1, min(100, int(round((cur / max_b) * 100.0))))
            writable = probe.has_bin("brightnessctl") or os.access(os.path.join(d, "brightness"), os.W_OK)
            return {"dev": os.path.basename(d), "path": d, "cur": cur, "max": max_b, "pct": pct, "writable": writable}
        except Exception:
            continue
    return None


def _apply_backlight(pct: int) -> None:
    bl = _get_backlight_info()
    if not bl or not bl["writable"]:
        return
    clamped = max(1, min(100, int(pct)))
    if probe.has_bin("brightnessctl"):
        probe.run_cmd(["brightnessctl", "-d", bl["dev"], "set", f"{clamped}%"], timeout=1.5)
    else:
        try:
            raw = max(1, min(bl["max"], int(round((clamped / 100.0) * bl["max"]))))
            with open(os.path.join(bl["path"], "brightness"), "w", encoding="utf-8") as f:
                f.write(str(raw))
        except OSError:
            pass


class Panel(BasePanel):
    id = "display"
    icon = "\uf108"

    def __init__(self, shell=None):
        self.title = t("display.title") if shell is not None else self.id
        super().__init__(shell)
        self.niri_map = None
        self._bl_pending: tuple[float, int] | None = None

    def _current_bl_pct(self, bl: dict | None) -> int:
        pending = self._bl_pending
        if pending is not None:
            ts, val = pending
            if (time.monotonic() - ts) < 0.5:
                return val
            self._bl_pending = None
        return bl["pct"] if bl else 50

    def _queue_backlight(self, val: int) -> None:
        clamped = max(1, min(100, int(val)))
        self._bl_pending = (time.monotonic(), clamped)
        set_slider_meta_value("disp:bl", str(clamped))

        def _commit(v: int = clamped) -> None:
            _apply_backlight(v)
            pending = self._bl_pending
            if pending is not None and pending[1] == v:
                self._bl_pending = None

        probe.enqueue_latest("disp_backlight", _commit, debounce_ms=80)
        self.request_throttled_refresh()

    def is_busy(self) -> bool:
        return bool(self.niri_map and self.niri_map.drag_state is not None)

    def get_bar_pill(self) -> dict:
        return {"text": Txt("\uf108", role="focus"), "tooltip": "", "class": "module-nc"}

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        from panels.cc_engine_display import get_niri_outputs_data
        outputs_data = get_niri_outputs_data()

        out_boxes = []
        set_img_lbl = t("display.set_image")
        nd_lbl = t("common.nd")
        no_bg_lbl = t("display.no_managed_bg")

        for out_name, out_info in sorted(outputs_data.items()):
            logical = out_info.get("logical", {})
            if not logical:
                continue
            res = f"{logical.get('width', 0)}x{logical.get('height', 0)} ({t('display.scale_tag')} {logical.get('scale', 1.0)}x)"
            desk_base = os.environ.get("NC_DESKTOP_BASE") or os.path.expanduser("~/.local/nc-desktop")
            state_file = os.path.join(desk_base, "state", f"{out_name}.json")
            mode, detail = nd_lbl, no_bg_lbl
            if os.path.exists(state_file):
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    mode = st.get("mode", nd_lbl)
                    if mode in ("ncmatrix", "cmatrix"):
                        is_jp = st.get("japanese") in (True, "true") or st.get("charset") == "katakana"
                        jp_tag = " JP" if is_jp else ""
                        detail = f"NCMatrix{jp_tag}: {st.get('color', 'green')} | {t('display.speed_label')}: {st.get('speed', '4')}"
                    elif mode == "slideshow":
                        detail = f"{t('display.dir_label')}: {os.path.basename(st.get('target', '').rstrip('/'))} | {st.get('interval', '')}"
                    elif mode in ("image", "scene"):
                        detail = f"{t('display.file_label')}: {os.path.basename(st.get('target', ''))}"
                    elif mode == "solid":
                        detail = f"{t('display.color_label')}: {st.get('target', '')}"
                except Exception:
                    pass

            m_box = Box(out_name, icon="\uf108", highlighted=False)
            m_box.add_row(" ")
            m_box.add_kv(t("display.resolution_label"), Txt(res, role="val"), key_width=12)
            wp_badge = StateBadge(mode != nd_lbl, mode.upper(), nd_lbl, on_tone="ok")
            m_box.add_kv(t("display.wallpaper_label"), wp_badge, key_width=12)
            m_box.add_row(f"  {Txt(canvas.truncate_plain(detail, 36), role='inactive' if detail == no_bg_lbl else 'schema')}")
            m_box.add_section(t("display.controls_section"))
            if mode == "slideshow":
                m_box.add_row(
                    f"  {ActionBtn(t('display.skip'), f'disp:next:{out_name}', tone='neutral')}  "
                    f"  {ActionBtn(t('display.pause'), f'disp:pause:{out_name}', tone='warn')}  "
                    f"  {ActionBtn(t('display.play'), f'disp:resume:{out_name}', tone='apply')}  "
                    f"  {ActionBtn(set_img_lbl, f'disp:set_img:{out_name}', tone='neutral')}"
                )
            else:
                m_box.add_row(
                    f"  {ActionBtn(set_img_lbl, f'disp:set_img:{out_name}', tone='neutral')}  "
                    f"  {ActionBtn('NCMatrix', f'disp:matrix:{out_name}', tone='apply')}  "
                    f"  {ActionBtn('NCMatrix JP', f'disp:matrix_jp:{out_name}', tone='apply')}"
                )
            m_box.add_row(" ")
            out_boxes.append(m_box)

        extra_boxes = []
        bl = _get_backlight_info()
        if bl is not None:
            bl_box = Box(t("display.backlight_title"), icon="\uf185", highlighted=False)
            bl_box.add_row(" ")
            bl_slider = Slider(
                self._current_bl_pct(bl),
                "disp:bl",
                min_val=1,
                max_val=100,
                width=16,
                show_pct=True,
                step_buttons=True,
                step=5,
                scroll_prefix="disp:bl_scroll",
                enabled=bl["writable"],
            )
            bl_box.add_kv(
                t("display.brightness_kv"),
                bl_slider,
                key_width=12,
                requires=(None if bl["writable"] else ["brightnessctl"]),
            )
            bl_box.add_row(" ")
            extra_boxes.append(bl_box)

        if out_boxes:
            from ui.widgets import AsciiCard
            map_box = Box(t("cc_display.multi_monitor_spatial_topology_nir"), icon="\uf108", highlighted=False)
            map_box.add_row(" ")
            map_box.open_bottom = True
            foot = Box("", highlighted=False)
            foot.open_top = True
            foot.add_row(" ")
            shared_cols = sync_boxes_width([map_box, foot, *out_boxes, *extra_boxes], canvas, canvas.total_cols)
            self.niri_map = NiriOutputMap(
                outputs_data,
                refresh_cb=(lambda: self.shell.refresh_view() if self.shell else None),
                map_width=int(round(shared_cols * canvas.char_w)),
                padding=max(10, int(round(canvas.char_w * 1.5))),
                inset_x=max(12, int(round(canvas.char_w * 2))),
                fixed_height=max(180, int(round(canvas.char_h * 10.5))),
                font_px=max(9.0, float(canvas.char_w) * 1.35),
                show_footer=False,
            )
            return [
                AsciiCard(map_box, canvas, embedded_widget=self.niri_map, footer_box_model=foot),
                *out_boxes,
                *extra_boxes,
            ]

        map_box = Box(t("cc_display.multi_monitor_spatial_topology_nir"), icon="\uf108", highlighted=False)
        map_box.add_row(" ")
        map_box.add_row(f"  {Txt(t('cc_display.output_powered_off_in_niri'), role='inactive')}")
        map_box.add_row(" ")
        if extra_boxes:
            sync_boxes_width([map_box, *extra_boxes], canvas, canvas.total_cols)
        return [map_box, *extra_boxes]

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        val = str(text or "").strip()
        if not val:
            return
        if action_id.startswith("disp_set_img|"):
            out_name = action_id.split("|", 1)[1]
            wp.apply_image(out_name, val)
            if self.shell:
                from gi.repository import GLib
                GLib.timeout_add(350, lambda: not self.shell.refresh_view())
            return

    def on_link(self, uri: str) -> None:
        if not uri.startswith("disp:"):
            return
        act = uri[len("disp:"):]
        if act.startswith("bl:"):
            try:
                self._queue_backlight(int(round(float(act.split(":", 1)[1]))))
            except Exception:
                pass
            return
        if act.startswith("bl_scroll:"):
            direction = act.rsplit(":", 1)[1]
            cur = self._current_bl_pct(_get_backlight_info())
            self._queue_backlight(cur + (5 if direction == "up" else -5))
            return
        if act.startswith("set_img:"):
            out_name = act.split(":", 1)[1]
            ph = t("display.enter_image_path")
            self.request_prompt(
                f"disp_set_img|{out_name}",
                ph,
                "",
                title=f"\uf03e {out_name}",
                required=True,
                completion="path_file",
            )
            return
        elif act.startswith(("next:", "pause:", "resume:")):
            cmd_k, out_name = act.split(":", 1)
            wp.control_slideshow(out_name, cmd_k)
        elif act.startswith("matrix:"):
            wp.apply_ncmatrix(act.split(":", 1)[1], {**wp.default_ncmatrix_state(), "charset": "ascii"})
        elif act.startswith("matrix_jp:"):
            wp.apply_ncmatrix(act.split(":", 1)[1], {**wp.default_ncmatrix_state(), "charset": "katakana"})

        if self.shell:
            from gi.repository import GLib
            GLib.timeout_add(350, lambda: not self.shell.refresh_view())
