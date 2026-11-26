#!/usr/bin/env python3
"""NC-UI Disks & Storage Topology Engine (`panels/cc_engine_disks.py`)."""

import json
import os
import re
import shlex
import time
from core import probe
from core.i18n import t
from core.privileges import get_privileged_action
from core.resilience import run_helper_async as _run_helper_async
from core.state import STATE
from ui.kit import feedback_cards, set_feedback
from ui.markup import format_bytes_short


def _set_sys_fb(ok: bool, msg: str) -> None:
    set_feedback("system", ok, msg)


def _feedback_cards() -> list:
    return feedback_cards("system", "cc_system.system_control_status")


def _human_bytes(val_b: int | None) -> str:
    if val_b is None or val_b < 0:
        return "-"
    return format_bytes_short(int(val_b))


def _collect_disks_data() -> dict:
    cols = (
        "NAME,KNAME,PATH,PKNAME,TYPE,SIZE,FSTYPE,FSVER,LABEL,PARTLABEL,"
        "PARTTYPE,PARTFLAGS,UUID,PARTUUID,MOUNTPOINTS,FSUSE%,FSAVAIL,FSUSED,"
        "MODEL,SERIAL,TRAN,ROTA,PTTYPE,RM,RO,STATE"
    )
    ok, out = probe.run_cmd(["lsblk", "-J", "-b", "-o", cols], timeout=2.0)
    raw_devs = []
    if ok and out:
        try:
            raw_devs = json.loads(out).get("blockdevices") or []
        except Exception:
            raw_devs = []

    smart_cache = {}
    smart_path = "/var/lib/ncui/disks-smart.json"
    if os.path.isfile(smart_path):
        try:
            with open(smart_path, "r", encoding="utf-8", errors="ignore") as f:
                smart_cache = json.load(f) or {}
        except Exception:
            smart_cache = {}

    def _parse_bytes_int(raw_val) -> int:
        s = str(raw_val or "").strip().rstrip("bB")
        if not s:
            return 0
        try:
            return max(0, int(float(s)))
        except ValueError:
            return 0

    disks_smart = smart_cache.get("disks") if isinstance(smart_cache.get("disks"), dict) else {}
    part_flags_map = smart_cache.get("part_flags") if isinstance(smart_cache.get("part_flags"), dict) else {}
    raw_vgs = smart_cache.get("vgs") if isinstance(smart_cache.get("vgs"), list) else []
    raw_lvs = smart_cache.get("lvs") if isinstance(smart_cache.get("lvs"), list) else []

    vgs = []
    for r in raw_vgs:
        vg_n = str(r.get("vg_name") or "").strip()
        if not vg_n:
            continue
        sz_b = _parse_bytes_int(r.get("vg_size"))
        fr_b = _parse_bytes_int(r.get("vg_free"))
        vgs.append({
            "vg_name": vg_n,
            "size_b": sz_b,
            "size_h": format_bytes_short(sz_b) if sz_b > 0 else "-",
            "free_b": fr_b,
            "free_h": format_bytes_short(fr_b) if fr_b > 0 else "-",
        })

    lvs = []
    lv_meta_by_dm: dict[str, dict] = {}
    for r in raw_lvs:
        vg_n = str(r.get("vg_name") or "-").strip()
        lv_n = str(r.get("lv_name") or "-").strip()
        sz_b = _parse_bytes_int(r.get("lv_size"))
        sz_h = format_bytes_short(sz_b) if sz_b > 0 else (str(r.get("lv_size") or "-"))
        attr = str(r.get("lv_attr") or "-").strip()
        dp_raw = str(r.get("data_percent") or "").strip().rstrip("%")
        try:
            dp_f = float(dp_raw) if dp_raw else None
        except ValueError:
            dp_f = None
        is_snap = attr.lower().startswith("s")
        is_orig = attr.lower().startswith("o")
        item = {
            "vg_name": vg_n,
            "lv_name": lv_n,
            "size_b": sz_b,
            "size_h": sz_h,
            "lv_attr": attr,
            "data_pct": dp_f,
            "data_pct_s": f"{dp_f:.1f}%" if dp_f is not None else "-",
            "is_snap": is_snap,
            "is_orig": is_orig,
        }
        lvs.append(item)
        dm_k = f"{vg_n.replace('-', '--')}-{lv_n.replace('-', '--')}"
        lv_meta_by_dm[dm_k] = item
        lv_meta_by_dm[f"{vg_n}-{lv_n}"] = item

    physical_disks = []
    empty_rm = []
    all_volumes = []

    def _walk_children(node: dict, disk_name: str, depth: int, seen_paths: set[str]) -> None:
        children = node.get("children") or []
        for ch in children:
            cname = str(ch.get("name") or "")
            cpath = str(ch.get("path") or f"/dev/{cname}")
            ctype = str(ch.get("type") or "")
            # Filter intermediate device-mapper CoW plumbing (-real / -cow) while descending at same depth
            if ctype == "lvm" and cname.endswith(("-real", "-cow")):
                _walk_children(ch, disk_name, depth, seen_paths)
                continue
            if cpath in seen_paths:
                continue
            seen_paths.add(cpath)

            fst = str(ch.get("fstype") or "")
            csz = int(ch.get("size") or 0)
            mps = [str(m) for m in (ch.get("mountpoints") or []) if m]
            mp_clean = ", ".join("SWAP" if m == "[SWAP]" else m for m in mps)
            use_raw = str(ch.get("fsuse%") or "").strip().rstrip("%")
            try:
                use_pct = float(use_raw) if use_raw else None
            except ValueError:
                use_pct = None

            lv_meta = lv_meta_by_dm.get(cname) if ctype == "lvm" else None
            cow_pct = lv_meta["data_pct"] if (lv_meta and lv_meta["is_snap"]) else None
            lv_attr = lv_meta["lv_attr"] if lv_meta else ""
            is_snap = bool(lv_meta and lv_meta["is_snap"])
            cow_size_b = lv_meta["size_b"] if (lv_meta and lv_meta["is_snap"]) else csz
            cow_size_h = lv_meta["size_h"] if (lv_meta and lv_meta["is_snap"]) else (format_bytes_short(csz) if csz > 0 else "-")

            flags = []
            ptype = str(ch.get("parttype") or "").lower()
            if ptype == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b":
                flags.append("ESP")
            elif ptype == "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f":
                flags.append("SWAP-PT")
            elif ptype == "e6d6d379-f507-44c2-a23c-238f2a3df928":
                flags.append("LVM-PT")
            if fst == "crypto_LUKS" or ctype == "crypt":
                flags.append("LUKS2" if fst == "crypto_LUKS" else "CRYPT")
            if fst == "LVM2_member":
                flags.append("LVM-PV")
            if ctype == "lvm":
                flags.append("SNAP-CoW" if is_snap else "LVM-LV")
            if mp_clean == "SWAP":
                flags.append("SWAP")
            if ch.get("ro"):
                flags.append("RO")
            for pf in part_flags_map.get(cpath) or []:
                if pf not in flags:
                    flags.append(pf)

            all_volumes.append({
                "disk": disk_name,
                "depth": depth,
                "name": cname,
                "lv_short": lv_meta["lv_name"] if lv_meta else cname,
                "vg_name": lv_meta["vg_name"] if lv_meta else "",
                "path": cpath,
                "type": ctype,
                "size_b": cow_size_b if is_snap else csz,
                "size_h": cow_size_h if is_snap else (format_bytes_short(csz) if csz > 0 else "-"),
                "virt_size_h": format_bytes_short(csz) if csz > 0 else "-",
                "fstype": fst or ctype,
                "label": str(ch.get("label") or ch.get("partlabel") or ""),
                "mountpoint": mp_clean or "-",
                "mounted": bool(mps),
                "use_pct": use_pct,
                "cow_pct": cow_pct,
                "is_snap": is_snap,
                "lv_attr": lv_attr,
                "fsused_h": format_bytes_short(int(ch["fsused"])) if ch.get("fsused") else "-",
                "flags": ", ".join(flags) if flags else "-",
                "uuid": str(ch.get("uuid") or ""),
            })
            _walk_children(ch, disk_name, depth + 1, seen_paths)

    for d in raw_devs:
        dname = str(d.get("name") or "")
        dpath = str(d.get("path") or f"/dev/{dname}")
        dtype = str(d.get("type") or "")
        dsz = int(d.get("size") or 0)
        if dname.startswith(("zram", "loop", "ram")):
            continue
        if dtype in ("disk", "rom") and dsz == 0 and d.get("rm"):
            empty_rm.append({
                "name": dname,
                "path": dpath,
                "model": str(d.get("model") or "Removable").strip(),
                "tran": str(d.get("tran") or "-").upper(),
            })
            continue
        if dtype != "disk" or dsz <= 0:
            continue

        _walk_children(d, dname, 0, set())
        d_vols = [v for v in all_volumes if v["disk"] == dname and v["depth"] == 0]
        alloc_b = sum(v["size_b"] for v in d_vols)
        sm = disks_smart.get(dpath) if isinstance(disks_smart.get(dpath), dict) else {}
        physical_disks.append({
            "name": dname,
            "path": dpath,
            "size_b": dsz,
            "size_h": format_bytes_short(dsz),
            "alloc_b": alloc_b,
            "alloc_h": format_bytes_short(alloc_b),
            "part_count": len(d_vols),
            "model": str(d.get("model") or "Block Disk").strip(),
            "serial": str(d.get("serial") or "-").strip(),
            "tran": str(d.get("tran") or "-").upper(),
            "media": "HDD" if d.get("rota") else ("NVMe" if str(d.get("tran") or "").lower() == "nvme" else "SSD"),
            "pttype": str(d.get("pttype") or "-").upper(),
            "state": str(d.get("state") or "running").upper(),
            "smart": sm,
        })

    sel_disk = str(STATE.get("system", "selected_disk", "") or "")
    disk_names = [d["name"] for d in physical_disks]
    if sel_disk not in disk_names:
        sel_disk = disk_names[0] if disk_names else ""
    sel_vol = str(STATE.get("system", "selected_volume", "") or "")
    scope = str(STATE.get("system", "disks_scope", "disk") or "disk")
    if scope not in ("disk", "all"):
        scope = "disk"

    priv_d = get_privileged_action("disks_inspect")
    return {
        "physical_disks": physical_disks,
        "empty_rm": empty_rm,
        "volumes": all_volumes,
        "vgs": vgs,
        "lvs": lvs,
        "selected_disk": sel_disk,
        "selected_volume": sel_vol,
        "scope": scope,
        "smart_updated_at": str(smart_cache.get("updated_at") or ""),
        "has_smart_cache": bool(smart_cache),
        "priv_disks_ok": bool(priv_d and priv_d.check_installed()),
        "disks_busy": str(STATE.get("system", "disks_busy", "") or ""),
    }



def ensure_disks_probed(cc_panel) -> None:
    if os.environ.get("NC_UI_OFFSCREEN") == "1":
        return
    if STATE.get("system", "disks_autoloaded", False):
        return
    STATE.set("system", "disks_autoloaded", True)
    if os.path.isfile("/var/lib/ncui/disks-smart.json"):
        try:
            if (time.time() - os.path.getmtime("/var/lib/ncui/disks-smart.json")) < 300.0:
                return
        except OSError:
            pass
    priv_d = get_privileged_action("disks_inspect")
    if priv_d and priv_d.check_installed():
        handle_cc_disks_link(cc_panel, "cc_sys:disks_probe")




def handle_cc_disks_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_sys:"):
        return False
    act = uri[len("cc_sys:"):]
    if act == "disks_probe":
        def _on_dprobe(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                _set_sys_fb(True, t("cc_system.disks_fb_probed"))
            else:
                _set_sys_fb(False, t("cc_system.disks_fb_err", p0=(out.splitlines()[-1] if out else "error")[:140]))

        _run_helper_async(cc_panel, "disks_busy", "SMART", "nc-disks", "disks_inspect", ["probe"], timeout=35.0, on_done=_on_dprobe)
    elif act.startswith("disk_sel:"):
        dname = act.split(":", 1)[1]
        if re.match(r"^[a-zA-Z0-9._-]{1,32}$", dname):
            STATE.set("system", "selected_disk", dname)
            STATE.set("system", "selected_volume", "")
    elif act.startswith("vol_sel:"):
        vname = act.split(":", 1)[1]
        if re.match(r"^[a-zA-Z0-9._/-]{1,48}$", vname):
            cur = str(STATE.get("system", "selected_volume", "") or "")
            STATE.set("system", "selected_volume", "" if cur == vname else vname)
    elif act in ("disks_scope:disk", "disks_scope:all"):
        STATE.set("system", "disks_scope", act.split(":", 1)[1])
    elif act.startswith("disk_smart_term:"):
        dp = act.split(":", 1)[1]
        if probe.has_bin("smartctl") and re.match(r"^/dev/[a-zA-Z0-9._-]+$", dp):
            from ui.embedded_tui import start_panel_terminal

            start_panel_terminal(cc_panel, t("common.term_title", p0=f"SMART {dp}"), ["sudo", "smartctl", "-a", dp])
        return True
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
