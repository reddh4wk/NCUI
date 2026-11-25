#!/usr/bin/env python3
import json
import os
import shutil
from core.config import (
    get_ui_transparency_pct,
    set_ui_transparency_pct,
    apply_waybar_transparency,
)
from core.i18n import t
from core import probe
from core.paths import resolve_script_path, run_desktop_cli
from ui.canvas import Canvas
from ui.kit import Box, Slider, Txt, ActionBtn, StateBadge, sync_boxes_width
from ui.output_map import NiriOutputMap
from panels.base import BasePanel


class Panel(BasePanel):
    id = "display"
    icon = "\uf108"

    def __init__(self, shell=None):
        self.title = t("display.title") if shell is not None else self.id
        super().__init__(shell)
        self.niri_map = None

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
            res = f"{logical.get('width', 0)}x{logical.get('height', 0)} (Scale {logical.get('scale', 1.0)}x)"
            state_file = os.path.expanduser(f"~/.local/nc-desktop/state/{out_name}.json")
            mode, detail = nd_lbl, no_bg_lbl
            if os.path.exists(state_file):
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    mode = st.get("mode", nd_lbl)
                    if mode in ("ncmatrix", "cmatrix"):
                        jp_tag = " JP" if st.get("japanese") == "true" else ""
                        detail = f"NCMatrix{jp_tag}: {st.get('color', 'green')} | {t('display.speed_label')}: {st.get('speed', '4')}"
                    elif mode == "slideshow":
                        detail = f"{t('display.dir_label')}: {os.path.basename(st.get('target', '').rstrip('/'))} | {st.get('interval', '')}"
                    elif mode == "image":
                        detail = f"{t('display.file_label')}: {os.path.basename(st.get('target', ''))}"
                    elif mode == "solid":
                        detail = f"{t('display.color_label')}: {st.get('target', '')}"
                except Exception:
                    pass

            m_box = Box(out_name, icon="\uf108", highlighted=False)
            m_box.add_row(" ")
            m_box.add_row(f"  {Txt(t('display.resolution_label') + ':', role='schema')} {Txt(res, role='val')}")
            wp_badge = StateBadge(mode != nd_lbl, mode.upper(), nd_lbl, on_tone="ok")
            m_box.add_row(f"  {Txt(t('display.wallpaper_label') + ':', role='schema')} {wp_badge}")
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

        if out_boxes:
            from ui.widgets import AsciiCard
            map_box = Box(t("cc_display.multi_monitor_spatial_topology_nir"), icon="\uf108", highlighted=False)
            map_box.add_row(" ")
            map_box.open_bottom = True
            foot = Box("", highlighted=False)
            foot.open_top = True
            foot.add_row(" ")
            shared_cols = sync_boxes_width([map_box, foot, *out_boxes], canvas, canvas.total_cols)
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
            ]

        map_box = Box(t("cc_display.multi_monitor_spatial_topology_nir"), icon="\uf108", highlighted=False)
        map_box.add_row(" ")
        map_box.add_row(f"  {Txt(t('cc_display.output_powered_off_in_niri'), role='inactive')}")
        map_box.add_row(" ")
        return [map_box]

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        val = str(text or "").strip()
        if not val:
            return
        if action_id.startswith("disp_set_img|"):
            out_name = action_id.split("|", 1)[1]
            run_desktop_cli(["-o", out_name, "-i", val])
            if self.shell:
                from gi.repository import GLib
                GLib.timeout_add(350, lambda: not self.shell.refresh_view())
            return
        if action_id == "disp_custom_cmd":
            import shlex
            try:
                args = shlex.split(val)
            except ValueError:
                args = val.split()
            run_desktop_cli(args)
            if self.shell:
                from gi.repository import GLib
                GLib.timeout_add(350, lambda: not self.shell.refresh_view())

    def on_link(self, uri: str) -> None:
        if not uri.startswith("disp:"):
            return
        act = uri[len("disp:"):]
        if act.startswith("trans:"):
            try:
                val = int(round(float(act.split(":", 1)[1])))
                set_ui_transparency_pct(val, sync_waybar=False)
                if self.shell:
                    self.shell._apply_css()
                    self.shell.refresh_view()
            except Exception:
                pass
            return
        if act.startswith("trans_scroll:"):
            direction = act.rsplit(":", 1)[1]
            cur = get_ui_transparency_pct()
            set_ui_transparency_pct(cur + (5 if direction == "up" else -5), sync_waybar=False)
            if self.shell:
                self.shell._apply_css()
                self.shell.refresh_view()
            return
        if act == "trans_apply":
            if self.shell:
                self.shell.bump_leave_guard(1.5)
            apply_waybar_transparency()
            return
        if act == "swap":
            run_desktop_cli(["--swap", "1", "2"])
        elif act == "restore":
            run_desktop_cli(["--restore"])
        elif act == "open_tui":
            script = resolve_script_path()
            probe.open_terminal(["bash", script, "-I"])
            if self.shell:
                self.shell.dispatch_uri("ui:close")
            return
        elif act == "prompt_cmd":
            self.request_prompt(
                "disp_custom_cmd",
                t("display.custom_cmd_prompt"),
                "",
                title="nc_util_desktop.sh",
            )
            return
        elif act.startswith("set_img:"):
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
        elif act.startswith("next:"):
            run_desktop_cli(["-o", act.split(":", 1)[1], "--next"])
        elif act.startswith("pause:"):
            run_desktop_cli(["-o", act.split(":", 1)[1], "--pause"])
        elif act.startswith("resume:"):
            run_desktop_cli(["-o", act.split(":", 1)[1], "--resume"])
        elif act.startswith("matrix:"):
            run_desktop_cli(["-o", act.split(":", 1)[1], "-c", "-C", "green"])
        elif act.startswith("matrix_jp:"):
            run_desktop_cli(["-o", act.split(":", 1)[1], "-c", "-C", "green", "-j"])

        if self.shell:
            from gi.repository import GLib
            GLib.timeout_add(350, lambda: not self.shell.refresh_view())
