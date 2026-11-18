# -*- coding: utf-8 -*-
"""NC-UI Control Center — Updates Engine (`panels/cc_engine_updates.py`)"""

import json
import os
import re
import shlex
import time
from core import log, probe
from core.config import THEME
from core.i18n import t
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    FeedbackBox,
    InlineFilter,
    StateBadge,
    Txt,
    get_active_inline_entry,
    get_inline_entry_buffer,
    start_inline_input,
)

_STATE = {
    "initialized": False,
    "loading_local": False,
    "scanning": False,
    "scanned": False,
    "last_ts": "",
    "official": [],
    "aur": [],
    "foreign": [],
    "orphans": [],
    "flatpak_updates": [],
    "flatpak_installed": [],
    "total_dl_mib": 0.0,
    "total_orphan_mib": 0.0,
    "cache_info": "-",
    "cache_bytes": -1,
    "cache_count": -1,
    "mtimes": [0.0, 0.0, 0.0],
    "saved_epoch": 0.0,
    "src_filter": "all",
    "aur_filter": "all",
    "fp_filter": "all",
    "search_query": "",
    "search_pacman": "",
    "search_aur": "",
    "search_flatpak": "",
    "search_orphans": "",
    "show_all_updates": False,
    "show_all_pacman": False,
    "show_all_aur": False,
    "show_all_flatpak": False,
    "show_all_orphans": False,
    "msg": "",
    "err": "",
}

_PKGLIST_SCRIPT = "/usr/local/sbin/nc-pkglist-save.sh"
_CACHE_DIR = "/var/cache/pacman/pkg"
_LOCAL_DB_DIR = "/var/lib/pacman/local"
_SYNC_DB_DIR = "/var/lib/pacman/sync"
_CACHE_FILE = os.path.expanduser("~/.cache/nc-ui/updates_cache.json")
_UPD_LINE_RE = re.compile(r"^([^\s]+)\s+([^\s]+)\s+->\s+([^\s]+)")
_PERSIST_KEYS = (
    "scanned",
    "last_ts",
    "official",
    "aur",
    "foreign",
    "orphans",
    "flatpak_updates",
    "flatpak_installed",
    "total_dl_mib",
    "total_orphan_mib",
    "cache_info",
    "cache_bytes",
    "cache_count",
    "mtimes",
    "saved_epoch",
)


def _feedback_cards() -> list:
    if not _STATE["msg"] and not _STATE["err"]:
        return []
    is_err = bool(_STATE["err"])
    return [FeedbackBox(t("cc_updates.status_title"), not is_err, _STATE["err"] if is_err else _STATE["msg"])]


def build_sync_actions(extra_actions: list = None) -> list:
    scan_lbl = t("cc_updates.scanning_dots") if _STATE["scanning"] else t("cc_updates.scan_updates")
    badge_off = t("cc_updates.synced_at", p0=_STATE["last_ts"]) if _STATE["scanned"] else t("cc_updates.local_at", p0=_STATE["last_ts"] or "-")
    status_item = Badge(t("cc_updates.scanning"), style="warn") if _STATE["scanning"] else Txt(badge_off, role="schema")
    acts = [
        status_item,
        ActionBtn("\uf021", "cc_upd:refresh", tone="neutral"),
        ActionBtn(scan_lbl, "cc_upd:scan", tone="apply"),
    ]
    if extra_actions:
        acts.extend(a for a in extra_actions if a)
    return acts


def compute_table_fit_rows(cc_panel, canvas, header_kv_count: int, has_section: bool = True) -> int:
    from gi.repository import Pango
    sh = getattr(cc_panel, "shell", None) if cc_panel else None
    avail_h = sh.get_cc_avail_body_height_px(extra_top_cards=_feedback_cards()) if (sh and hasattr(sh, "get_cc_avail_body_height_px")) else 780
    std_row_h = getattr(canvas, "_std_row_h_px", 0)
    if std_row_h <= 0:
        sample_r = canvas.row(" ")
        std_row_h = max(12, int(round(canvas.measure_line_height(sample_r) / Pango.SCALE)))
        canvas._std_row_h_px = std_row_h
    base_h = int(canvas.base_row_h_px)
    fixed_rows = header_kv_count + 1 + (2 if has_section else 0) + 1 + 2 + 1 + 1
    fixed_bases = 3 if has_section else 2
    fixed_px = (fixed_bases * base_h) + (fixed_rows * std_row_h) + 8
    return max(6, int((avail_h - fixed_px) // std_row_h))


def get_tab_query(tab: str) -> tuple[str, str]:
    eid = f"cc_upd_filter_{tab}"
    raw_q = (
        get_inline_entry_buffer()
        if get_active_inline_entry() == eid
        else str(_STATE.get(f"search_{tab}", "") or "")
    ).strip()
    return raw_q, raw_q.lower()


def add_tab_filter_row(box: Box, canvas, tab: str, raw_q: str, right_markup: str = "") -> None:
    eff_kw = max(14, min(32, int(THEME["geometry"].get("default_key_width", 20))))
    r_cols = (canvas.measure_cols(right_markup) + 2) if right_markup else 0
    avail_val_cols = canvas.inner_cols - (eff_kw + 3) - r_cols
    flt_w = max(10, min(32 if not right_markup else 28, avail_val_cols - 6))
    box.add_kv(
        t("cc_updates.filter_btn"),
        InlineFilter(
            f"cc_upd_filter_{tab}",
            raw_q,
            t("cc_updates.filter_placeholder"),
            f"cc_upd:search_open:{tab}",
            clear_uri=f"cc_upd:clear_search:{tab}",
            width=flt_w,
            active=bool(raw_q),
        ),
        right_markup=right_markup,
    )


def add_tab_footer_row(box: Box, tab: str, shown_count: int, total_count: int, max_fit: int, show_all: bool) -> None:
    toggle_btn = ""
    if total_count > max_fit or show_all:
        toggle_lbl = t("cc_updates.show_compact_btn", p0=max_fit) if show_all else t("cc_updates.show_all_btn", p0=total_count)
        toggle_btn = ActionBtn(toggle_lbl, f"cc_upd:toggle_all:{tab}", tone="neutral")
    note = t("cc_updates.filter_count_lbl", p0=shown_count, p1=total_count)
    box.add_row(f"  {Txt(note, role='schema')}", right_markup=toggle_btn)


def _parse_mib(size_str: str) -> float:
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMG]?i?B)\s*$", str(size_str or ""), re.IGNORECASE)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("kib", "kb"):
        return val / 1024.0
    if unit in ("gib", "gb"):
        return val * 1024.0
    if unit == "b":
        return val / (1024.0 * 1024.0)
    return val


def _get_pacman_mtimes() -> list[float]:
    out = []
    for p in (_LOCAL_DB_DIR, _SYNC_DB_DIR, _CACHE_DIR):
        try:
            out.append(round(os.stat(p).st_mtime, 3))
        except OSError:
            out.append(0.0)
    return out


def _format_cache_info(total_bytes: int, file_count: int, fallback: str = "-") -> str:
    if total_bytes < 0 or file_count < 0:
        return fallback
    if total_bytes >= 1024 ** 3:
        sz = f"{total_bytes / (1024 ** 3):.2f} GiB"
    else:
        sz = f"{total_bytes / (1024 ** 2):.1f} MiB"
    return f"{sz} ({t('cc_updates.pkgs_unit', p0=file_count)})"


def _probe_cache_stats(prev_mtime: float = -1.0, prev_bytes: int = -1, prev_count: int = -1) -> tuple[int, int, str]:
    if not os.path.isdir(_CACHE_DIR):
        return -1, -1, "-"
    cache_mtime = _get_pacman_mtimes()[2]
    if prev_bytes >= 0 and prev_count >= 0 and cache_mtime > 0 and abs(cache_mtime - prev_mtime) < 0.001:
        return prev_bytes, prev_count, _format_cache_info(prev_bytes, prev_count)
    total_bytes = 0
    file_count = 0
    try:
        for entry in os.scandir(_CACHE_DIR):
            try:
                if entry.is_file(follow_symlinks=False):
                    total_bytes += entry.stat(follow_symlinks=False).st_size
                    file_count += 1
            except OSError:
                continue
        return total_bytes, file_count, _format_cache_info(total_bytes, file_count)
    except OSError:
        _, out, _ = probe.run_cmd_full(["du", "-sh", _CACHE_DIR], timeout=4.0)
        return -1, -1, (out.split()[0] if out else "-")


def _load_disk_cache() -> bool:
    if not os.path.exists(_CACHE_FILE):
        return False
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        if "flatpak_installed" not in data:
            return False
        for k in _PERSIST_KEYS:
            if k in data:
                _STATE[k] = data[k]
        for key in ("official", "aur", "foreign", "orphans", "flatpak_updates", "flatpak_installed"):
            for item in _STATE.get(key) or []:
                if isinstance(item, dict) and "_search" not in item:
                    item["_search"] = " ".join(
                        str(item.get(f, "") or "")
                        for f in ("name", "app_id", "repo", "new_ver", "ver", "branch", "origin", "desc")
                    ).lower()
        if not isinstance(_STATE.get("mtimes"), list) or len(_STATE["mtimes"]) != 3:
            _STATE["mtimes"] = [0.0, 0.0, 0.0]
        _STATE["cache_info"] = _format_cache_info(
            int(_STATE.get("cache_bytes", -1)),
            int(_STATE.get("cache_count", -1)),
            str(_STATE.get("cache_info") or "-"),
        )
        _STATE["initialized"] = True
        return True
    except Exception as e:
        log.debug(f"updates disk cache load failed: {e}")
        return False


def _save_disk_cache() -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        payload = {k: _STATE.get(k) for k in _PERSIST_KEYS}
        tmp = f"{_CACHE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, _CACHE_FILE)
    except Exception as e:
        log.debug(f"updates disk cache save failed: {e}")


def _probe_repo_meta(names: list[str]) -> dict:
    if not names or not probe.has_bin("expac"):
        return {}
    meta = {}
    for i in range(0, len(names), 120):
        chunk = names[i : i + 120]
        ok, out, _ = probe.run_cmd_full(["expac", "-S", "-H", "M", "%n|%r|%v|%k"] + chunk, timeout=6.0)
        if not ok or not out:
            continue
        for ln in out.splitlines():
            parts = ln.split("|", 3)
            if len(parts) == 4:
                meta.setdefault(parts[0], []).append((parts[1], parts[2], parts[3], _parse_mib(parts[3])))
    return meta


def _probe_local_installed(names: list[str]) -> list[dict]:
    if not names:
        return []
    out_list = []
    if probe.has_bin("expac"):
        ok, out, _ = probe.run_cmd_full(["expac", "-H", "M", "-Q", "%n|%v|%m|%d"] + names, timeout=5.0)
        if ok and out:
            for ln in out.splitlines():
                parts = ln.split("|", 3)
                if len(parts) == 4:
                    nm, vr, sz_str, ds = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
                    out_list.append({
                        "name": nm,
                        "ver": vr,
                        "size": sz_str,
                        "size_mib": _parse_mib(sz_str),
                        "desc": ds,
                        "_search": f"{nm} {vr} {ds}".lower(),
                    })
            if out_list:
                return out_list
    _, out, _ = probe.run_cmd_full(["pacman", "-Q"] + names, timeout=5.0)
    for ln in (out or "").splitlines():
        sp = ln.split()
        if len(sp) >= 2:
            out_list.append({
                "name": sp[0],
                "ver": sp[1],
                "size": "-",
                "size_mib": 0.0,
                "desc": "",
                "_search": f"{sp[0]} {sp[1]}".lower(),
            })
    return out_list


def _probe_flatpak_local() -> list[dict]:
    if not probe.has_bin("flatpak"):
        return []
    ok, out, _ = probe.run_cmd_full(
        ["flatpak", "list", "--columns=name,application,version,branch,size,origin"],
        timeout=4.0,
    )
    if not ok or not out:
        return []
    items = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\t")
        while len(parts) < 6:
            parts.append("")
        nm = parts[0].strip()
        app_id = parts[1].strip()
        ver = parts[2].strip() or "-"
        branch = parts[3].strip() or "-"
        sz = parts[4].strip().replace("\xa0", " ") or "-"
        origin = parts[5].strip() or "flathub"
        items.append({
            "name": nm or app_id,
            "app_id": app_id,
            "ver": ver,
            "branch": branch,
            "size": sz,
            "origin": origin,
            "_search": f"{nm} {app_id} {ver} {branch} {origin}".lower(),
        })
    return items


def _probe_flatpak_updates() -> list[dict]:
    if not probe.has_bin("flatpak"):
        return []
    ok, out, _ = probe.run_cmd_full(
        ["flatpak", "remote-ls", "--updates", "--columns=name,application,version,branch,download-size,origin"],
        timeout=15.0,
    )
    if not ok or not out:
        return []
    items = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\t")
        while len(parts) < 6:
            parts.append("")
        nm = parts[0].strip()
        app_id = parts[1].strip()
        new_ver = parts[2].strip() or "update"
        branch = parts[3].strip() or "-"
        dl_sz = parts[4].strip().replace("\xa0", " ") or "-"
        origin = parts[5].strip() or "flathub"
        items.append({
            "name": nm or app_id,
            "app_id": app_id,
            "new_ver": new_ver,
            "branch": branch,
            "dl_size": dl_sz,
            "origin": origin,
            "_search": f"{nm} {app_id} {new_ver} {branch} {origin}".lower(),
        })
    return items


def _parse_update_lines(lines: list[str], foreign_names: set[str], repo_meta: dict, force_aur: bool = False) -> tuple[list[dict], list[dict]]:
    off = []
    aur = []
    for ln in lines:
        m = _UPD_LINE_RE.match(ln.strip())
        if not m:
            continue
        pkg, old_v, new_v = m.group(1), m.group(2), m.group(3)
        if force_aur or pkg in foreign_names:
            aur.append({
                "name": pkg,
                "repo": "aur",
                "old_ver": old_v,
                "new_ver": new_v,
                "dl_size": "-",
                "dl_mib": 0.0,
                "source": "aur",
                "_search": f"{pkg} aur {new_v}".lower(),
            })
            continue
        entries = repo_meta.get(pkg, [])
        chosen = next((e for e in entries if e[1] == new_v), entries[0] if entries else ("sync", new_v, "-", 0.0))
        off.append({
            "name": pkg,
            "repo": chosen[0],
            "old_ver": old_v,
            "new_ver": new_v,
            "dl_size": chosen[2],
            "dl_mib": chosen[3],
            "source": "repo",
            "_search": f"{pkg} {chosen[0]} {new_v}".lower(),
        })
    return off, aur


def _collect_local_fast(
    prev_mtimes: list[float],
    prev_bytes: int,
    prev_count: int,
    prev_aur: list[dict],
    prev_scanned: bool,
    prev_fp_updates: list[dict] = None,
) -> dict:
    foreign_names = set()
    foreign_list = []
    if probe.has_bin("pacman"):
        _, out_qm, _ = probe.run_cmd_full(["pacman", "-Qmq"], timeout=4.0)
        qms = [ln.strip() for ln in (out_qm or "").splitlines() if ln.strip()]
        foreign_names = set(qms)
        foreign_list = _probe_local_installed(qms)

    qu_lines = []
    if probe.has_bin("pacman"):
        _, out_qu, _ = probe.run_cmd_full(["pacman", "-Qu"], timeout=5.0)
        if out_qu:
            qu_lines = [ln.strip() for ln in out_qu.splitlines() if ln.strip()]

    pkg_names = [_UPD_LINE_RE.match(ln).group(1) for ln in qu_lines if _UPD_LINE_RE.match(ln)]
    repo_meta = _probe_repo_meta(pkg_names)
    off, aur = _parse_update_lines(qu_lines, foreign_names, repo_meta)

    if prev_scanned and not aur and prev_aur:
        installed_vers = {item["name"]: item.get("ver", "") for item in foreign_list}
        aur = [
            item for item in prev_aur
            if item.get("name") in foreign_names
            and installed_vers.get(item["name"], "") == item.get("old_ver", "")
        ]

    orphans = []
    if probe.has_bin("pacman"):
        _, out_qdt, _ = probe.run_cmd_full(["pacman", "-Qdtq"], timeout=4.0)
        qdt = [ln.strip() for ln in (out_qdt or "").splitlines() if ln.strip()]
        orphans = _probe_local_installed(qdt)

    fp_installed = _probe_flatpak_local()
    fp_ids = {item.get("app_id", "") for item in fp_installed}
    fp_updates = [item for item in (prev_fp_updates or []) if item.get("app_id", "") in fp_ids] if prev_scanned else []

    c_mtime = prev_mtimes[2] if len(prev_mtimes) == 3 else -1.0
    c_bytes, c_count, c_info = _probe_cache_stats(c_mtime, prev_bytes, prev_count)
    return {
        "official": off,
        "aur": aur,
        "foreign": foreign_list,
        "orphans": orphans,
        "flatpak_updates": fp_updates,
        "flatpak_installed": fp_installed,
        "total_dl_mib": sum(item["dl_mib"] for item in off),
        "total_orphan_mib": sum(item["size_mib"] for item in orphans),
        "cache_bytes": c_bytes,
        "cache_count": c_count,
        "cache_info": c_info,
        "last_ts": time.strftime("%H:%M:%S"),
        "mtimes": _get_pacman_mtimes(),
        "saved_epoch": time.time(),
    }


def _apply_local_snapshot(snap: dict) -> None:
    _STATE["official"] = snap["official"]
    _STATE["aur"] = snap["aur"]
    _STATE["foreign"] = snap["foreign"]
    _STATE["orphans"] = snap["orphans"]
    _STATE["flatpak_updates"] = snap.get("flatpak_updates", [])
    _STATE["flatpak_installed"] = snap.get("flatpak_installed", [])
    _STATE["total_dl_mib"] = snap["total_dl_mib"]
    _STATE["total_orphan_mib"] = snap["total_orphan_mib"]
    _STATE["cache_bytes"] = snap["cache_bytes"]
    _STATE["cache_count"] = snap["cache_count"]
    _STATE["cache_info"] = _format_cache_info(snap["cache_bytes"], snap["cache_count"], snap["cache_info"])
    _STATE["last_ts"] = snap["last_ts"]
    _STATE["mtimes"] = snap["mtimes"]
    _STATE["saved_epoch"] = snap["saved_epoch"]
    _STATE["initialized"] = True
    _STATE["loading_local"] = False
    _save_disk_cache()


def _start_local_probe_async(cc_panel=None) -> None:
    if _STATE["loading_local"] or _STATE["scanning"]:
        return
    _STATE["loading_local"] = True
    started_epoch = time.time()
    prev_mtimes = list(_STATE.get("mtimes") or [0.0, 0.0, 0.0])
    prev_bytes = int(_STATE.get("cache_bytes", -1))
    prev_count = int(_STATE.get("cache_count", -1))
    prev_aur = list(_STATE.get("aur") or [])
    prev_fp_up = list(_STATE.get("flatpak_updates") or [])
    prev_scanned = bool(_STATE.get("scanned"))

    def _worker():
        try:
            return _collect_local_fast(prev_mtimes, prev_bytes, prev_count, prev_aur, prev_scanned, prev_fp_up)
        except Exception as exc:
            return exc

    def _done(res):
        _STATE["loading_local"] = False
        if isinstance(res, Exception):
            log.debug(f"updates async probe failed: {res}")
            return
        if _STATE["scanning"] or float(_STATE.get("saved_epoch") or 0.0) > started_epoch:
            return
        _apply_local_snapshot(res)
        if cc_panel and getattr(cc_panel, "shell", None) and getattr(cc_panel, "section", "") == "updates":
            cc_panel.shell.refresh_view()

    probe.run_async(_worker, on_done=_done)


def ensure_updates_loaded(cc_panel=None, force: bool = False) -> None:
    if not _STATE["initialized"]:
        _load_disk_cache()
    _STATE["cache_info"] = _format_cache_info(
        int(_STATE.get("cache_bytes", -1)),
        int(_STATE.get("cache_count", -1)),
        str(_STATE.get("cache_info") or "-"),
    )
    cur_mtimes = _get_pacman_mtimes()
    needs_refresh = (
        force
        or (not _STATE["initialized"])
        or (cur_mtimes != _STATE.get("mtimes"))
        or (time.time() - float(_STATE.get("saved_epoch") or 0.0) > 300.0)
    )
    if needs_refresh and not _STATE["scanning"] and not _STATE["loading_local"]:
        if os.environ.get("NC_UI_OFFSCREEN") == "1" and not _STATE["initialized"]:
            snap = _collect_local_fast(
                list(_STATE.get("mtimes") or [0.0, 0.0, 0.0]),
                int(_STATE.get("cache_bytes", -1)),
                int(_STATE.get("cache_count", -1)),
                list(_STATE.get("aur") or []),
                bool(_STATE.get("scanned")),
                list(_STATE.get("flatpak_updates") or []),
            )
            _apply_local_snapshot(snap)
        else:
            _start_local_probe_async(cc_panel)


def _start_scan(cc_panel) -> None:
    if _STATE["scanning"]:
        return
    _STATE["scanning"] = True
    _STATE["err"] = ""
    _STATE["msg"] = t("cc_updates.scanning_msg")
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()

    prev_mtimes = list(_STATE.get("mtimes") or [0.0, 0.0, 0.0])
    prev_bytes = int(_STATE.get("cache_bytes", -1))
    prev_count = int(_STATE.get("cache_count", -1))

    def _worker():
        try:
            _, out_qm, _ = probe.run_cmd_full(["pacman", "-Qmq"], timeout=5.0) if probe.has_bin("pacman") else (False, "", "")
            qms = [ln.strip() for ln in (out_qm or "").splitlines() if ln.strip()]
            foreign_names = set(qms)
            foreign_list = _probe_local_installed(qms)

            qu_lines = []
            if probe.has_bin("checkupdates"):
                _, out_cu, _ = probe.run_cmd_full(["checkupdates", "--nocolor"], timeout=45.0)
                if out_cu:
                    qu_lines = [ln.strip() for ln in out_cu.splitlines() if ln.strip()]
            if not qu_lines and probe.has_bin("pacman"):
                _, out_qu, _ = probe.run_cmd_full(["pacman", "-Qu"], timeout=6.0)
                if out_qu:
                    qu_lines = [ln.strip() for ln in out_qu.splitlines() if ln.strip()]

            pkg_names = [_UPD_LINE_RE.match(ln).group(1) for ln in qu_lines if _UPD_LINE_RE.match(ln)]
            repo_meta = _probe_repo_meta(pkg_names)
            off, aur = _parse_update_lines(qu_lines, foreign_names, repo_meta)

            aur_tool = probe.get_preferred_pkg_manager(aur_only=True)
            if aur_tool:
                _, out_aur, _ = probe.run_cmd_full([aur_tool, "-Qua"], timeout=30.0)
                if out_aur:
                    seen_aur = {item["name"] for item in aur}
                    _, parsed_aur = _parse_update_lines(out_aur.splitlines(), foreign_names, {}, force_aur=True)
                    for item in parsed_aur:
                        if item["name"] not in seen_aur:
                            aur.append(item)
                            seen_aur.add(item["name"])

            orphans = []
            if probe.has_bin("pacman"):
                _, out_qdt, _ = probe.run_cmd_full(["pacman", "-Qdtq"], timeout=5.0)
                qdt = [ln.strip() for ln in (out_qdt or "").splitlines() if ln.strip()]
                orphans = _probe_local_installed(qdt)

            fp_installed = _probe_flatpak_local()
            fp_updates = _probe_flatpak_updates()

            c_mtime = prev_mtimes[2] if len(prev_mtimes) == 3 else -1.0
            c_bytes, c_count, c_info = _probe_cache_stats(c_mtime, prev_bytes, prev_count)
            return off, aur, foreign_list, orphans, fp_updates, fp_installed, c_bytes, c_count, c_info
        except Exception as exc:
            return exc

    def _done(res):
        _STATE["scanning"] = False
        _STATE["scanned"] = True
        _STATE["initialized"] = True
        if isinstance(res, Exception):
            _STATE["err"] = str(res)
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return
        off, aur, foreign_list, orphans, fp_updates, fp_installed, c_bytes, c_count, cache_txt = res
        _STATE["official"] = off
        _STATE["aur"] = aur
        _STATE["foreign"] = foreign_list
        _STATE["orphans"] = orphans
        _STATE["flatpak_updates"] = fp_updates
        _STATE["flatpak_installed"] = fp_installed
        _STATE["total_dl_mib"] = sum(item["dl_mib"] for item in off)
        _STATE["total_orphan_mib"] = sum(item["size_mib"] for item in orphans)
        _STATE["cache_bytes"] = c_bytes
        _STATE["cache_count"] = c_count
        _STATE["cache_info"] = _format_cache_info(c_bytes, c_count, cache_txt)
        _STATE["last_ts"] = time.strftime("%H:%M:%S")
        _STATE["mtimes"] = _get_pacman_mtimes()
        _STATE["saved_epoch"] = time.time()
        _save_disk_cache()
        _STATE["msg"] = t("cc_updates.scan_done_msg", p0=_STATE["last_ts"], p1=len(off), p2=len(aur), p3=len(orphans))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_worker, on_done=_done)


def handle_cc_updates_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_upd:"):
        return False
    _STATE["msg"] = ""
    _STATE["err"] = ""
    cmd = uri.split(":", 1)[1]
    aur_tool = probe.get_preferred_pkg_manager(aur_only=True)

    if cmd == "scan":
        _start_scan(cc_panel)
        return True
    if cmd == "refresh":
        ensure_updates_loaded(cc_panel, force=True)
        return True
    elif cmd == "upgrade_term":
        tool = f"{aur_tool} -Syu" if aur_tool else "sudo pacman -Syu"
        probe.open_terminal(["bash", "-c", f"echo '=== Nightcity Protected System Upgrade ==='; {tool}; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_full_msg", p0=tool)
    elif cmd == "upgrade_official":
        probe.open_terminal(["bash", "-c", "echo '=== Official Arch Upgrade ==='; sudo pacman -Syu; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_off_msg")
    elif cmd == "upgrade_aur":
        tool = f"{aur_tool} -Sua" if aur_tool else "echo 'No AUR helper (paru/yay) found.'"
        probe.open_terminal(["bash", "-c", f"echo '=== AUR Package Upgrade ==='; {tool}; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_aur_msg", p0=tool)
    elif cmd.startswith("pkg_up:"):
        pkg = cmd.split(":", 1)[1].strip()
        is_aur = any(item["name"] == pkg for item in _STATE["aur"])
        qpkg = shlex.quote(pkg)
        tool = f"{aur_tool} -S {qpkg}" if (is_aur and aur_tool) else f"sudo pacman -S {qpkg}"
        probe.open_terminal(["bash", "-c", f"{tool}; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_pkg_msg", p0=pkg)
    elif cmd == "paccache":
        probe.open_terminal(["bash", "-c", "sudo paccache -rk2; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_cache_msg")
    elif cmd == "rm_orphans":
        probe.open_terminal(["bash", "-c", "pkgs=$(pacman -Qdtq); if [ -n \"$pkgs\" ]; then echo \"$pkgs\" | sudo pacman -Rns -; else echo 'No orphan packages found.'; fi; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_rm_orphans_msg")
    elif cmd.startswith("rm_orphan:") or cmd.startswith("rm_foreign:"):
        pkg = cmd.split(":", 1)[1].strip()
        probe.open_terminal(["bash", "-c", f"sudo pacman -Rns {shlex.quote(pkg)}; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_rm_one_msg", p0=pkg)
    elif cmd.startswith("keep_orphan:"):
        pkg = cmd.split(":", 1)[1].strip()
        probe.open_terminal(["bash", "-c", f"sudo pacman -D --asexplicit {shlex.quote(pkg)}; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_keep_one_msg", p0=pkg)
    elif cmd == "save_pkglist":
        if os.path.exists(_PKGLIST_SCRIPT):
            probe.open_terminal(["bash", "-c", f"sudo {shlex.quote(_PKGLIST_SCRIPT)}; echo; read -p 'Press Enter to close...'"])
            _STATE["msg"] = t("cc_updates.term_pkglist_msg", p0=_PKGLIST_SCRIPT)
        else:
            _STATE["err"] = t("cc_updates.missing_script_err", p0=_PKGLIST_SCRIPT)
    elif cmd == "upgrade_flatpak":
        probe.open_terminal(["bash", "-c", "echo '=== Flatpak Update ==='; flatpak update; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_fp_full_msg")
    elif cmd == "flatpak_unused":
        probe.open_terminal(["bash", "-c", "flatpak uninstall --unused; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_fp_unused_msg")
    elif cmd.startswith("fp_up:"):
        app_id = cmd.split(":", 1)[1].strip()
        probe.open_terminal(["bash", "-c", f"flatpak update {shlex.quote(app_id)}; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_fp_one_msg", p0=app_id)
    elif cmd.startswith("fp_rm:"):
        app_id = cmd.split(":", 1)[1].strip()
        probe.open_terminal(["bash", "-c", f"flatpak uninstall {shlex.quote(app_id)}; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = t("cc_updates.term_fp_rm_msg", p0=app_id)
    elif cmd.startswith("filter_src:"):
        val = cmd.split(":", 1)[1]
        if val in ("all", "official", "aur"):
            _STATE["src_filter"] = val
    elif cmd.startswith("aur_filter:"):
        val = cmd.split(":", 1)[1]
        if val in ("all", "pending", "installed"):
            _STATE["aur_filter"] = val
    elif cmd.startswith("fp_filter:"):
        val = cmd.split(":", 1)[1]
        if val in ("all", "pending", "installed"):
            _STATE["fp_filter"] = val
    elif cmd == "toggle_all" or cmd.startswith("toggle_all:"):
        tab = cmd.split(":", 1)[1] if ":" in cmd else "updates"
        key = f"show_all_{tab}" if tab in ("pacman", "aur", "flatpak", "orphans") else "show_all_updates"
        _STATE[key] = not bool(_STATE.get(key))
    elif cmd == "search_open" or cmd.startswith("search_open:"):
        tab = cmd.split(":", 1)[1] if ":" in cmd else ""
        if tab in ("pacman", "aur", "flatpak", "orphans"):
            s_key, eid, turi = f"search_{tab}", f"cc_upd_filter_{tab}", f"cc_upd:search_open:{tab}"
        else:
            s_key, eid, turi = "search_query", "cc_upd_filter", "cc_upd:search_open"
        if cc_panel:
            start_inline_input(
                entry_id=eid,
                shell=cc_panel.shell,
                trigger_uri=turi,
                initial_text=_STATE.get(s_key, ""),
                max_len=48,
                on_change=lambda txt, k=s_key: _STATE.__setitem__(k, str(txt or "")),
                on_commit=lambda txt, k=s_key: _STATE.__setitem__(k, str(txt or "").strip()),
            )
        return True
    elif cmd == "clear_search" or cmd.startswith("clear_search:"):
        tab = cmd.split(":", 1)[1] if ":" in cmd else ""
        s_key = f"search_{tab}" if tab in ("pacman", "aur", "flatpak", "orphans") else "search_query"
        _STATE[s_key] = ""
    else:
        return False
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_updates_prompt(cc_panel, action_id: str, text: str) -> bool:
    if action_id != "cc_upd_search":
        return False
    _STATE["search_query"] = str(text or "").strip()
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
