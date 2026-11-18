#!/usr/bin/env python3
import json
import os
import shutil
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
from core.config import (
    get_ui_transparency_pct,
    set_ui_transparency_pct,
    apply_waybar_transparency,
)
from core.i18n import t
from core import probe
from ui.canvas import Canvas
from ui.kit import Box, Slider, Txt, ActionBtn, StateBadge, sync_boxes_width
from ui.widgets import AsciiCard
from panels.base import BasePanel


def resolve_script_path(script_name: str = "nc_util_desktop.sh", fallback_alias: str = "nc-desktop.sh") -> str:
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(root_dir, "utils", script_name),
        os.path.join(root_dir, script_name),
        os.path.join(root_dir, "bin", script_name),
        os.path.expanduser(f"~/Projects/ncui/utils/{script_name}"),
        os.path.expanduser(f"~/Projects/ncui/{script_name}"),
        os.path.expanduser(f"~/Projects/nc-ui/utils/{script_name}"),
        os.path.expanduser(f"~/Projects/nc-ui/{script_name}"),
        f"/usr/local/lib/nc-ui/utils/{script_name}",
        f"/usr/local/lib/nc-ui/{script_name}",
        f"/usr/local/bin/{script_name}",
        f"/usr/local/sbin/{script_name}",
        shutil.which(script_name) or "",
    ]
    if fallback_alias:
        candidates.extend([f"/usr/local/bin/{fallback_alias}", shutil.which(fallback_alias) or ""])
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return script_name


def run_desktop_cli(args: list) -> None:
    script = resolve_script_path()
    probe.spawn_bg(["bash", script] + [str(a) for a in args])


class NiriOutputMap(Gtk.Fixed):
    SNAP_PX = 14

    def __init__(self, outputs_data: dict, refresh_cb, map_width: int = 380, padding: int = 16):
        super().__init__()
        self.refresh_cb = refresh_cb
        self.outputs = outputs_data
        self.map_width = map_width
        self.padding = padding
        self.offset_x = padding
        self.offset_y = padding
        self.map_h = 140
        self.min_x = 0
        self.min_y = 0
        self.scale = 1.0
        self.drag_state = None
        self.items = {}
        self._build_map()
        self.connect("size-allocate", self._on_size_allocate)

    def _compute_geometry(self, target_w: int) -> dict[str, tuple[int, int, int, int]]:
        valid_outs = {k: v for k, v in self.outputs.items() if v.get("logical")}
        if not valid_outs:
            return {}
        self.min_x = min(v["logical"]["x"] for v in valid_outs.values())
        self.min_y = min(v["logical"]["y"] for v in valid_outs.values())
        max_x = max(v["logical"]["x"] + v["logical"]["width"] for v in valid_outs.values())
        max_y = max(v["logical"]["y"] + v["logical"]["height"] for v in valid_outs.values())
        logical_w = max(1, max_x - self.min_x)
        logical_h = max(1, max_y - self.min_y)
        self.scale = max(0.01, (target_w - (self.padding * 2)) / float(max(logical_w, 5120)))

        raw = {}
        for out_name, info in valid_outs.items():
            log_d = info["logical"]
            w = max(40, int(round(log_d["width"] * self.scale)))
            h = max(28, int(round(log_d["height"] * self.scale)))
            rel_x = int(round((log_d["x"] - self.min_x) * self.scale))
            rel_y = int(round((log_d["y"] - self.min_y) * self.scale))
            raw[out_name] = (rel_x, rel_y, w, h)

        min_rx = min(rx for rx, _ry, _w, _h in raw.values())
        min_ry = min(ry for _rx, ry, _w, _h in raw.values())
        max_rx = max(rx + w for rx, _ry, w, _h in raw.values())
        max_ry = max(ry + h for _rx, ry, _w, h in raw.values())
        used_w = max(1, max_rx - min_rx)
        used_h = max(1, max_ry - min_ry)

        self.map_h = max(used_h + (self.padding * 2), 140)
        self.offset_x = max(self.padding, int(round((target_w - used_w) / 2.0))) - min_rx
        self.offset_y = max(self.padding, int(round((self.map_h - used_h) / 2.0))) - min_ry
        return {
            out_name: (self.offset_x + rx, self.offset_y + ry, w, h)
            for out_name, (rx, ry, w, h) in raw.items()
        }

    def _build_map(self):
        geom = self._compute_geometry(self.map_width)
        if not geom:
            return
        self.set_size_request(self.map_width, self.map_h)

        for out_name, (x, y, w, h) in geom.items():
            log_d = self.outputs[out_name]["logical"]
            eb = Gtk.EventBox()
            eb.set_name("niri-map-out")
            eb.set_size_request(w, h)
            eb.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
            )
            lbl = Gtk.Label(label=f"{out_name}\n{log_d['width']}x{log_d['height']}")
            lbl.set_justify(Gtk.Justification.CENTER)
            eb.add(lbl)
            self.items[out_name] = {
                "widget": eb, "x": x, "y": y, "w": w, "h": h,
                "log_w": log_d["width"], "log_h": log_d["height"],
                "log_x": log_d["x"], "log_y": log_d["y"],
            }
            eb.connect("button-press-event", self.on_press, out_name)
            eb.connect("motion-notify-event", self.on_motion)
            eb.connect("button-release-event", self.on_release)
            self.put(eb, x, y)

    def _on_size_allocate(self, _widget, alloc):
        if self.drag_state is not None or not self.items:
            return
        if alloc.width <= (self.padding * 2) or abs(alloc.width - self.map_width) <= 1:
            return
        self.map_width = alloc.width
        geom = self._compute_geometry(self.map_width)
        for out_name, (x, y, w, h) in geom.items():
            it = self.items.get(out_name)
            if not it:
                continue
            it["x"], it["y"], it["w"], it["h"] = x, y, w, h
            it["widget"].set_size_request(w, h)
            self.move(it["widget"], x, y)

    def _compute_snapped(self, moving_name: str, raw_x: float, raw_y: float):
        me = self.items[moving_name]
        w, h = me["w"], me["h"]
        snap_x, snap_y = raw_x, raw_y
        best_dx, best_dy = self.SNAP_PX + 1, self.SNAP_PX + 1
        snapped_to = {"x": None, "y": None}
        for other_name, ot in self.items.items():
            if other_name == moving_name:
                continue
            for tx, tag in [
                (ot["x"] - w, ("left_of", other_name)),
                (ot["x"] + ot["w"], ("right_of", other_name)),
                (ot["x"], ("align_left", other_name)),
                (ot["x"] + ot["w"] - w, ("align_right", other_name)),
                (ot["x"] + (ot["w"] - w) // 2, ("align_center_x", other_name)),
            ]:
                d = abs(raw_x - tx)
                if d < best_dx:
                    best_dx, snap_x, snapped_to["x"] = d, tx, tag
            for ty, tag in [
                (ot["y"] - h, ("above", other_name)),
                (ot["y"] + ot["h"], ("below", other_name)),
                (ot["y"], ("align_top", other_name)),
                (ot["y"] + ot["h"] - h, ("align_bottom", other_name)),
                (ot["y"] + (ot["h"] - h) // 2, ("align_center_y", other_name)),
            ]:
                d = abs(raw_y - ty)
                if d < best_dy:
                    best_dy, snap_y, snapped_to["y"] = d, ty, tag
        alloc = self.get_allocation()
        max_w = max(self.map_width, alloc.width)
        max_h = max(140, alloc.height)
        snap_x = max(2, min(snap_x, max_w - w - 2))
        snap_y = max(2, min(snap_y, max_h - h - 2))
        return int(round(snap_x)), int(round(snap_y)), snapped_to

    def on_press(self, widget, event, out_name):
        if event.button == 1:
            it = self.items[out_name]
            self.drag_state = {
                "widget": widget, "out_name": out_name,
                "start_x": it["x"], "start_y": it["y"],
                "mouse_x": event.x_root, "mouse_y": event.y_root,
            }
        return True

    def on_motion(self, widget, event):
        if self.drag_state and self.drag_state["widget"] == widget:
            dx = event.x_root - self.drag_state["mouse_x"]
            dy = event.y_root - self.drag_state["mouse_y"]
            sx, sy, _ = self._compute_snapped(
                self.drag_state["out_name"],
                self.drag_state["start_x"] + dx,
                self.drag_state["start_y"] + dy,
            )
            self.move(widget, sx, sy)
        return True

    def on_release(self, widget, event):
        if self.drag_state and self.drag_state["widget"] == widget:
            out_name = self.drag_state["out_name"]
            dx = event.x_root - self.drag_state["mouse_x"]
            dy = event.y_root - self.drag_state["mouse_y"]
            sx, sy, snapped_to = self._compute_snapped(
                out_name,
                self.drag_state["start_x"] + dx,
                self.drag_state["start_y"] + dy,
            )
            self.drag_state = None
            me = self.items[out_name]
            new_log_x = int(round((sx - self.offset_x) / self.scale)) + self.min_x
            new_log_y = int(round((sy - self.offset_y) / self.scale)) + self.min_y
            if snapped_to["x"]:
                kind, ref_name = snapped_to["x"]
                ref = self.items[ref_name]
                if kind == "left_of": new_log_x = ref["log_x"] - me["log_w"]
                elif kind == "right_of": new_log_x = ref["log_x"] + ref["log_w"]
                elif kind == "align_left": new_log_x = ref["log_x"]
                elif kind == "align_right": new_log_x = ref["log_x"] + ref["log_w"] - me["log_w"]
                elif kind == "align_center_x": new_log_x = ref["log_x"] + (ref["log_w"] - me["log_w"]) // 2
            if snapped_to["y"]:
                kind, ref_name = snapped_to["y"]
                ref = self.items[ref_name]
                if kind == "above": new_log_y = ref["log_y"] - me["log_h"]
                elif kind == "below": new_log_y = ref["log_y"] + ref["log_h"]
                elif kind == "align_top": new_log_y = ref["log_y"]
                elif kind == "align_bottom": new_log_y = ref["log_y"] + ref["log_h"] - me["log_h"]
                elif kind == "align_center_y": new_log_y = ref["log_y"] + (ref["log_h"] - me["log_h"]) // 2
            final_coords = {k: (v["log_x"], v["log_y"]) for k, v in self.items.items()}
            final_coords[out_name] = (new_log_x, new_log_y)
            base_x = min(c[0] for c in final_coords.values())
            base_y = min(c[1] for c in final_coords.values())
            for name, (fx, fy) in final_coords.items():
                probe.run_cmd(["niri", "msg", "output", name, "position", "set", str(int(fx - base_x)), str(int(fy - base_y))])
            GLib.timeout_add(250, lambda: not self.refresh_cb())
        return True


class Panel(BasePanel):
    id = "display"
    icon = "\uf108"

    def __init__(self, shell=None):
        self.title = t("display.title")
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
                padding=max(16, int(round(canvas.char_w * 2))),
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
            GLib.timeout_add(350, lambda: not self.shell.refresh_view())
