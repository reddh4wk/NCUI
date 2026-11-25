#!/usr/bin/env python3
"""Parametric Tactical Blueprint Cairo micro-diagrams for Verbose Hover Help."""

import math
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

from core.config import THEME  # noqa: E402
from ui.kit import get_semantic_palette  # noqa: E402

VALID_HELP_DIAGRAMS = frozenset({
    "hanoi_sync",
    "lvm_snap",
    "disk_luks_lvm",
    "cpu_power_stack",
    "pipewire_flow",
    "ui_overlay_flow",
    "network_stack",
    "firewall_nft",
    "boot_recovery",
    "security_trust",
    "input_pipeline",
})


def _hex_rgba(hx: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    s = str(hx or "#ffffff").strip().lstrip("#")
    if len(s) == 8:
        s = s[2:]
    if len(s) == 6:
        try:
            return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0, float(alpha))
        except ValueError:
            pass
    return (1.0, 1.0, 1.0, float(alpha))


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(float(r), max(0.0, w * 0.5), max(0.0, h * 0.5))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi * 0.5, 0.0)
    cr.arc(x + w - r, y + h - r, r, 0.0, math.pi * 0.5)
    cr.arc(x + r, y + h - r, r, math.pi * 0.5, math.pi)
    cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
    cr.close_path()


class _HelpDiagramWidget(Gtk.DrawingArea):
    """Lightweight Cairo architectural mini-diagram embedded inside verbose help cards."""

    def __init__(self, diagram_id: str, focus: str = "", inset_x: float = 14.0, height_px: int = 114) -> None:
        super().__init__()
        self._diag_id = str(diagram_id or "").strip()
        self._focus = str(focus or "").strip().lower()
        self._inset_x = max(8.0, float(inset_x))
        self._height_px = max(90, int(height_px))
        self._w = 480
        self.set_size_request(300, self._height_px)
        self.set_hexpand(True)
        self.connect("size-allocate", self._on_alloc)
        self.connect("draw", self._on_draw)

    def _on_alloc(self, _w, alloc) -> None:
        if alloc.width > 120 and abs(alloc.width - self._w) > 1:
            self._w = alloc.width
            self.queue_draw()

    @staticmethod
    def _txt(cr, text: str, x: float, y: float, fdesc, col: str, alpha: float = 1.0, max_w: float = 0.0, align=None) -> None:
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(fdesc)
        if max_w > 0.0:
            lay.set_width(int(max_w * Pango.SCALE))
            lay.set_ellipsize(Pango.EllipsizeMode.END)
        if align is not None:
            lay.set_alignment(align)
        lay.set_text(str(text), -1)
        cr.set_source_rgba(*_hex_rgba(col, alpha))
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lay)

    def _node(self, cr, x: float, y: float, w: float, h: float, title: str, sub: str, role_col: str, role_bg: str, active: bool, f_title, f_sub, pal: dict, dashed: bool = False) -> None:
        cr.save()
        _rounded_rect(cr, x + 0.5, y + 0.5, w - 1.0, h - 1.0, 4.5)
        cr.set_source_rgba(*_hex_rgba(role_bg if active else pal["structure_bg"], 0.85 if active else 0.60))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(role_col if active else pal["structure"], 0.98 if active else 0.90))
        cr.set_line_width(1.6 if active else 1.0)
        if dashed:
            cr.set_dash([3.0, 3.0])
        cr.stroke()
        cr.restore()
        pad = 7.0
        self._txt(cr, title, x + pad, y + 7.0, f_title, pal["focus"] if active else pal["schema"], 0.98 if active else 0.90, max_w=w - pad * 2.0)
        if sub:
            self._txt(cr, sub, x + pad, y + h - 20.0, f_sub, role_col if active else pal["inactive"], 0.94, max_w=w - pad * 2.0)

    @staticmethod
    def _arrow(cr, x0: float, y0: float, x1: float, y1: float, col: str, active: bool = False, bidir: bool = False) -> None:
        cr.save()
        cr.set_source_rgba(*_hex_rgba(col, 0.96 if active else 0.72))
        cr.set_line_width(1.7 if active else 1.1)
        if not active:
            cr.set_dash([3.0, 2.5])
        cr.move_to(x0, y0)
        cr.line_to(x1, y1)
        cr.stroke()
        cr.set_dash([])
        ang = math.atan2(y1 - y0, x1 - x0)
        ah = 5.5
        for ax, ay, dir_a in ([(x1, y1, ang), (x0, y0, ang + math.pi)] if bidir else [(x1, y1, ang)]):
            cr.move_to(ax, ay)
            cr.line_to(ax - ah * math.cos(dir_a - 0.45), ay - ah * math.sin(dir_a - 0.45))
            cr.line_to(ax - ah * math.cos(dir_a + 0.45), ay - ah * math.sin(dir_a + 0.45))
            cr.close_path()
            cr.fill()
        cr.restore()

    def _pipeline_nodes(self, cr, ux: float, uw: float, ny: float, nh: float, nodes: list[tuple], f_title, f_sub, pal: dict) -> None:
        n = len(nodes)
        gap = 20.0
        nw = max(72.0, (uw - gap * max(0, n - 1)) / float(n))
        cx = ux
        for idx, (tit, sub, col, bg, active, dashed) in enumerate(nodes):
            self._node(cr, cx, ny, nw, nh, tit, sub, col, bg, active, f_title, f_sub, pal, dashed=dashed)
            if idx < n - 1:
                next_act = nodes[idx + 1][4]
                arr_act = active or next_act
                arr_col = col if active else (nodes[idx + 1][2] if next_act else pal["schema"])
                self._arrow(cr, cx + nw + 2.0, ny + nh * 0.5, cx + nw + gap - 2.0, ny + nh * 0.5, arr_col, active=arr_act)
            cx += nw + gap

    def _on_draw(self, _w, cr) -> bool:
        alloc = self.get_allocation()
        w = max(300.0, float(alloc.width or self._w))
        h = max(90.0, float(alloc.height or self._height_px))
        bx = float(self._inset_x)
        bw = max(240.0, w - 2.0 * bx)
        pal = get_semantic_palette()
        ff = str((THEME.get("typography") or {}).get("font_family", "monospace") or "monospace")
        base_pt = float((THEME.get("typography") or {}).get("font_size", 11) or 11)

        f_hdr = Pango.FontDescription()
        f_hdr.set_family(ff)
        f_hdr.set_weight(Pango.Weight.BOLD)
        f_hdr.set_size(int(round(base_pt * 0.80 * Pango.SCALE)))

        f_title = Pango.FontDescription()
        f_title.set_family(ff)
        f_title.set_weight(Pango.Weight.BOLD)
        f_title.set_size(int(round(base_pt * 0.88 * Pango.SCALE)))

        f_sub = Pango.FontDescription()
        f_sub.set_family(ff)
        f_sub.set_size(int(round(base_pt * 0.78 * Pango.SCALE)))

        _rounded_rect(cr, bx + 0.5, 0.5, bw - 1.0, h - 1.0, 5.5)
        cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.68))
        cr.fill_preserve()
        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.90))
        cr.set_line_width(1.0)
        cr.stroke()

        ux = bx + 12.0
        uw = bw - 24.0
        ny = 28.0
        nh = h - 42.0
        f = self._focus

        if self._diag_id == "hanoi_sync":
            self._txt(cr, "FLOW // LOCAL HOST <-> VAULT <-> HANOI MATRIX", ux, 7.0, f_hdr, pal["intent"], 0.95, max_w=uw)
            n1 = (f in ("drift", "push", "pull", "mount", "share"), pal["intent"], pal["intent_bg"])
            n2 = (f in ("push", "pull", "mount", "pkglist", "share", "drift", "prune"), pal["vitality"], pal["vitality_bg"])
            n3 = (f in ("prune", "restore", "push"), pal["attention"], pal["attention_bg"])
            nw = (uw - 48.0) / 3.0
            nx3 = ux + 2.0 * (nw + 24.0)
            self._node(cr, ux, ny, nw, nh, "LOCAL /", "/etc · ~/.config", n1[1], n1[2], n1[0], f_title, f_sub, pal)
            self._node(cr, ux + nw + 24.0, ny, nw, nh, "VAULT STORAGE", "SMB·NFS·SSH", n2[1], n2[2], n2[0], f_title, f_sub, pal)
            self._node(cr, nx3, ny, nw, nh, "HANOI MATRIX", "", n3[1], n3[2], n3[0], f_title, f_sub, pal)

            # Compact 4x8 Hanoi rotation mini-matrix inside node 3
            cols = 8
            rows = 4
            mx = nx3 + 8.0
            mw = max(48.0, nw - 16.0)
            my = ny + 26.0
            mh = max(24.0, nh - 33.0)
            mcw = mw / float(cols)
            mch = mh / float(rows)
            sched_8 = (3, 0, 1, 0, 2, 0, 1, 0)
            latest_8 = {0: 7, 1: 6, 2: 4, 3: 0}
            row_cols = (
                (pal["intent"], pal["intent_bg"]),
                (pal["vitality"], pal["vitality_bg"]),
                (pal["attention"], pal["attention_bg"]),
                (pal["interrupt"], pal["interrupt_bg"]),
            )
            cr.save()
            for r in range(rows):
                ry = my + r * mch
                for c in range(cols):
                    cx0 = mx + c * mcw
                    _rounded_rect(cr, cx0 + 1.0, ry + 1.0, mcw - 2.0, mch - 2.0, 1.5)
                    if sched_8[c] == r:
                        rc, rbg = row_cols[r]
                        if c == latest_8[r]:
                            cr.set_source_rgba(*_hex_rgba(rbg, 0.96))
                            cr.fill_preserve()
                            cr.set_source_rgba(*_hex_rgba(rc, 0.96))
                            cr.set_line_width(1.0)
                        else:
                            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.30))
                            cr.fill_preserve()
                            cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.55))
                            cr.set_line_width(0.7)
                    else:
                        cr.set_source_rgba(*_hex_rgba(pal["structure_bg"], 0.38))
                        cr.fill_preserve()
                        cr.set_source_rgba(*_hex_rgba(pal["structure"], 0.32))
                        cr.set_line_width(0.6)
                    cr.stroke()
            cr.restore()

            ay = ny + nh * 0.5
            if f == "pull":
                self._arrow(cr, ux + nw + 22.0, ay, ux + nw + 2.0, ay, pal["attention"], active=True, bidir=False)
            elif f == "push":
                self._arrow(cr, ux + nw + 2.0, ay, ux + nw + 22.0, ay, pal["vitality"], active=True, bidir=False)
            elif f == "mount":
                self._arrow(cr, ux + nw + 2.0, ay, ux + nw + 22.0, ay, pal["intent"], active=True, bidir=False)
            elif f in ("drift", "share", ""):
                self._arrow(cr, ux + nw + 2.0, ay, ux + nw + 22.0, ay, pal["intent"], active=True, bidir=True)
            else:
                self._arrow(cr, ux + nw + 2.0, ay, ux + nw + 22.0, ay, pal["schema"], active=False, bidir=False)

            a2_act = f in ("push", "prune", "restore")
            a2_col = pal["attention"] if a2_act else pal["schema"]
            self._arrow(cr, ux + 2 * nw + 26.0, ay, ux + 2 * nw + 46.0, ay, a2_col, active=a2_act, bidir=(f == "restore"))

        elif self._diag_id == "lvm_snap":
            self._txt(cr, "PIPELINE // PRIMARY LV <-> COW SNAPSHOT <-> ESP UKI", ux, 7.0, f_hdr, pal["attention"], 0.95, max_w=uw)
            nw = (uw - 48.0) / 3.0
            ay = ny + nh * 0.5
            self._node(cr, ux, ny, nw, nh, "ROOT / VAR LV", "Live System", pal["intent"], pal["intent_bg"], f in ("capture", "hook", "promote", "backend"), f_title, f_sub, pal)
            self._node(cr, ux + nw + 24.0, ny, nw, nh, "COW SNAPSHOT", "AUTO / STABLE", pal["attention"], pal["attention_bg"], f in ("capture", "hook", "extend", "promote", "delete"), f_title, f_sub, pal)
            self._node(cr, ux + 2.0 * (nw + 24.0), ny, nw, nh, "ESP UKI", "Limine Slot", pal["vitality"], pal["vitality_bg"], f in ("capture", "hook", "promote", "backend"), f_title, f_sub, pal)

            if f == "promote":
                self._arrow(cr, ux + nw + 22.0, ay, ux + nw + 2.0, ay, pal["interrupt"], active=True, bidir=False)
            elif f in ("capture", "hook"):
                self._arrow(cr, ux + nw + 2.0, ay, ux + nw + 22.0, ay, pal["intent"], active=True, bidir=False)
            else:
                self._arrow(cr, ux + nw + 2.0, ay, ux + nw + 22.0, ay, pal["schema"], active=False, bidir=False)

            a2_act = f in ("capture", "hook", "promote", "backend")
            self._arrow(cr, ux + 2 * nw + 26.0, ay, ux + 2 * nw + 46.0, ay, pal["vitality"] if a2_act else pal["schema"], active=a2_act, bidir=False)

        elif self._diag_id == "disk_luks_lvm":
            self._txt(cr, "STACK // PHYSICAL DISK -> LUKS2 -> LVM VG/LVS", ux, 7.0, f_hdr, pal["intent"], 0.95, max_w=uw)
            self._pipeline_nodes(cr, ux, uw, ny, nh, [
                ("PHYS DISK", "NVMe / SMART", pal["vitality"], pal["vitality_bg"], f in ("smart", "disk", ""), False),
                ("LUKS2", "Crypt Mapper", pal["attention"], pal["attention_bg"], f in ("luks", "vol"), False),
                ("LVM VG0", "LV + CoW Snaps", pal["intent"], pal["intent_bg"], f in ("lvm", "vol", "scope"), False),
            ], f_title, f_sub, pal)

        elif self._diag_id == "cpu_power_stack":
            self._txt(cr, "CONTROL // PRESET -> GOV / EPP / BOOST -> THERMALS", ux, 7.0, f_hdr, pal["attention"], 0.95, max_w=uw)
            self._pipeline_nodes(cr, ux, uw, ny, nh, [
                ("PRESET / PPD", "System Policy", pal["intent"], pal["intent_bg"], f in ("preset", "posture", ""), False),
                ("CPU REGISTERS", "Gov · EPP · Boost", pal["attention"], pal["attention_bg"], f in ("gov", "epp", "boost"), False),
                ("FANS & SENSORS", "PWM Curve · Temp", pal["vitality"], pal["vitality_bg"], f in ("fan", "therm"), False),
            ], f_title, f_sub, pal)

        elif self._diag_id == "pipewire_flow":
            self._txt(cr, "GRAPH // APP STREAMS -> PIPEWIRE -> SINK / SOURCE", ux, 7.0, f_hdr, pal["intent"], 0.95, max_w=uw)
            nw = (uw - 48.0) / 3.0
            ay = ny + nh * 0.5
            n1_act = f in ("app", "mute")
            n2_act = f in ("vol", "mute", "scroll", "source")
            n3_act = f in ("sink", "source", "default")
            self._node(cr, ux, ny, nw, nh, "APP STREAMS", "Gain / Mute", pal["schema"], pal["structure_bg"], n1_act, f_title, f_sub, pal)
            self._node(cr, ux + nw + 24.0, ny, nw, nh, "PIPEWIRE", "Graph Mixer", pal["intent"], pal["intent_bg"], n2_act, f_title, f_sub, pal)
            self._node(cr, ux + 2.0 * (nw + 24.0), ny, nw, nh, "DEVICE I/O", "Default Route", pal["vitality"], pal["vitality_bg"], n3_act, f_title, f_sub, pal)

            a1_act = n1_act or (n2_act and f != "source")
            a1_col = pal["schema"] if n1_act else (pal["intent"] if a1_act else pal["schema"])
            self._arrow(cr, ux + nw + 2.0, ay, ux + nw + 22.0, ay, a1_col, active=a1_act, bidir=False)

            if f == "source":
                self._arrow(cr, ux + 2 * nw + 26.0, ay, ux + 2 * nw + 46.0, ay, pal["vitality"], active=True, bidir=True)
            else:
                a2_act = n2_act or n3_act
                a2_col = pal["intent"] if n2_act else (pal["vitality"] if n3_act else pal["schema"])
                self._arrow(cr, ux + 2 * nw + 26.0, ay, ux + 2 * nw + 46.0, ay, a2_col, active=a2_act, bidir=False)

        elif self._diag_id == "ui_overlay_flow":
            self._txt(cr, "UX // HOVER DELAY -> STANDARD OVERLAY -> VERBOSE", ux, 7.0, f_hdr, pal["intent"], 0.95, max_w=uw)
            self._pipeline_nodes(cr, ux, uw, ny, nh, [
                ("HOVER TARGET", "Pointer Idle", pal["schema"], pal["structure_bg"], f in ("hover", "delay"), False),
                ("STANDARD BOX", "Quick Context", pal["intent"], pal["intent_bg"], f in ("hover", "anchor", "filter"), False),
                ("VERBOSE + CAIRO", "In-Place Guide", pal["vitality"], pal["vitality_bg"], f in ("verbose", "log", ""), False),
            ], f_title, f_sub, pal)

        elif self._diag_id == "network_stack":
            self._txt(cr, "FLOW // IFACE · WIFI · BT -> NM PROFILES · VPN -> WAN · ROUTING", ux, 7.0, f_hdr, pal["intent"], 0.95, max_w=uw)
            self._pipeline_nodes(cr, ux, uw, ny, nh, [
                ("LINK & RADIO", "Iface · Wi-Fi · BT", pal["intent"], pal["intent_bg"], f in ("iface", "wifi", "bt", ""), False),
                ("NM & TUNNELS", "Profile · VPN · Proxy", pal["attention"], pal["attention_bg"], f in ("profile", "vpn", "proxy"), False),
                ("WAN & ROUTING", "Gateway · DNS · ARP", pal["vitality"], pal["vitality_bg"], f in ("wan", "dns", "route"), False),
            ], f_title, f_sub, pal)

        elif self._diag_id == "firewall_nft":
            self._txt(cr, "FILTER // TRAFFIC FLOW -> NFTABLES CHAINS -> ACCEPT / DROP", ux, 7.0, f_hdr, pal["attention"], 0.95, max_w=uw)
            self._pipeline_nodes(cr, ux, uw, ny, nh, [
                ("LIVE TRAFFIC", "Sockets & Conntrack", pal["schema"], pal["structure_bg"], f in ("conn", "flow"), False),
                ("NFT CHAINS", "Input · Output · Rules", pal["intent"], pal["intent_bg"], f in ("chain", "rule", "policy", ""), False),
                ("VERDICT & LIVE", "ACCEPT / DROP · Persist", pal["vitality"], pal["vitality_bg"], f in ("verdict", "apply"), False),
            ], f_title, f_sub, pal)

        elif self._diag_id == "boot_recovery":
            self._txt(cr, "RESILIENCE // ESP · LIMINE <-> ROOT LV <-> LIVE · VAULT", ux, 7.0, f_hdr, pal["attention"], 0.95, max_w=uw)
            nw = (uw - 48.0) / 3.0
            ay = ny + nh * 0.5
            n3_is_int = f in ("commit", "chroot")
            self._node(cr, ux, ny, nw, nh, "ESP & LIMINE", "limine.conf · UKI", pal["intent"], pal["intent_bg"], f in ("boot", "limine", "entry", "rescue", "chroot", ""), f_title, f_sub, pal)
            self._node(cr, ux + nw + 24.0, ny, nw, nh, "ENCRYPTED ROOT", "LUKS2 · LVM LV", pal["vitality"], pal["vitality_bg"], f in ("rescue", "commit", "chroot", "golden"), f_title, f_sub, pal)
            self._node(
                cr,
                ux + 2.0 * (nw + 24.0),
                ny,
                nw,
                nh,
                "LIVE / VAULT",
                "RAM OS · Golden",
                pal["interrupt"] if n3_is_int else pal["attention"],
                pal["interrupt_bg"] if n3_is_int else pal["attention_bg"],
                f in ("commit", "chroot", "golden"),
                f_title,
                f_sub,
                pal,
                dashed=n3_is_int,
            )

            if f == "rescue":
                self._arrow(cr, ux + nw + 22.0, ay, ux + nw + 2.0, ay, pal["intent"], active=True, bidir=False)
            elif f == "chroot":
                self._arrow(cr, ux + nw + 22.0, ay, ux + nw + 2.0, ay, pal["interrupt"], active=True, bidir=True)
            else:
                self._arrow(cr, ux + nw + 2.0, ay, ux + nw + 22.0, ay, pal["schema"], active=False, bidir=False)

            if f in ("commit", "chroot"):
                self._arrow(cr, ux + 2 * nw + 46.0, ay, ux + 2 * nw + 26.0, ay, pal["interrupt"], active=True, bidir=False)
            elif f == "golden":
                self._arrow(cr, ux + 2 * nw + 26.0, ay, ux + 2 * nw + 46.0, ay, pal["vitality"], active=True, bidir=False)
            else:
                self._arrow(cr, ux + 2 * nw + 26.0, ay, ux + 2 * nw + 46.0, ay, pal["schema"], active=False, bidir=False)

        elif self._diag_id == "security_trust":
            self._txt(cr, "TRUST // SBCTL · TPM2 -> LUKS2 · USBGUARD -> SUDOERS · KEYS", ux, 7.0, f_hdr, pal["intent"], 0.95, max_w=uw)
            self._pipeline_nodes(cr, ux, uw, ny, nh, [
                ("HW ROOT", "sbctl · TPM2 PCRs", pal["vitality"], pal["vitality_bg"], f in ("tpm", "sbctl", ""), False),
                ("DEVICE & CRYPT", "LUKS2 · USBGuard", pal["attention"], pal["attention_bg"], f in ("luks", "usb"), False),
                ("PRIVILEGE & ID", "Sudoers · SSH · Polkit", pal["intent"], pal["intent_bg"], f in ("sudoers", "keys", "polkit"), False),
            ], f_title, f_sub, pal)

        elif self._diag_id == "input_pipeline":
            self._txt(cr, "INPUT // EVDEV · GAMEPAD -> XKB · UINPUT -> NIRI BINDS", ux, 7.0, f_hdr, pal["intent"], 0.95, max_w=uw)
            self._pipeline_nodes(cr, ux, uw, ny, nh, [
                ("HW SENSORS", "Mouse · Gamepad · Evdev", pal["schema"], pal["structure_bg"], f in ("mouse", "gamepad", "device"), False),
                ("TRANSFORM", "XKB · Macros · Mapper", pal["intent"], pal["intent_bg"], f in ("xkb", "macro", "mapper", ""), False),
                ("COMPOSITOR", "Niri Keys & Capture", pal["vitality"], pal["vitality_bg"], f in ("bind", "conflict"), False),
            ], f_title, f_sub, pal)

        return False


def create_help_diagram(diagram_id: str, focus: str = "", inset_x: float = 14.0, height_px: int = 114) -> Gtk.Widget | None:
    """Instantiate a parametric Cairo micro-diagram for verbose hover descriptions."""
    d_id = str(diagram_id or "").strip()
    if d_id not in VALID_HELP_DIAGRAMS:
        return None
    return _HelpDiagramWidget(d_id, focus=focus, inset_x=inset_x, height_px=height_px)
