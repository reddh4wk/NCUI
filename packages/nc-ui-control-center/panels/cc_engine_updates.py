# -*- coding: utf-8 -*-
"""NC-UI Control Center — Updates Engine (`panels/cc_engine_updates.py`)"""

import json
import os
import re
import shlex
import time
from core import log, probe
from core.config import THEME, get_cache_dir
from core.i18n import t
from core.paths import find_bin
from core.resilience import resolve_sbin_helper
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    InlineFilter,
    Txt,
    feedback_cards,
    get_active_inline_entry,
    get_inline_entry_buffer,
    set_feedback,
    start_inline_input,
)

_STATE = {
    "initialized": False,
    "loading_local": False,
    "scanning": False,
    "scanned": False,
    "backend": "",
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
}

_sbin_vault = resolve_sbin_helper("nc-vault")
_PKGLIST_SCRIPT = _sbin_vault if os.path.isfile(_sbin_vault) else (find_bin("nc-vault") or _sbin_vault)
_UPD_LINE_RE = re.compile(r"^([^\s]+)\s+([^\s]+)\s+->\s+([^\s]+)")
_APT_UPD_RE = re.compile(r"^([^\s/]+)/([^\s]+)\s+([^\s]+)\s+\S+\s+\[upgradable from:\s+([^\]]+)\]")
_APT_ORPHAN_RE = re.compile(r"^(?:Remv|Purg)\s+([^\s:]+)(?::[^\s]+)?\s+\[([^\]]+)\]")
_DNF_UPD_RE = re.compile(r"^([^\s]+)\.([^\s.]+)\s+([^\s]+)\s+([^\s]+)$")
_PERSIST_KEYS = (
    "scanned",
    "backend",
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


def get_pkg_backend() -> str:
    fam = probe.get_os_release().get("family", "unknown")
    if fam == "arch" and probe.has_bin("pacman"):
        return "pacman"
    if fam == "debian" and (probe.has_bin("apt") or probe.has_bin("apt-get")):
        return "apt"
    if fam == "fedora" and (probe.has_bin("dnf") or probe.has_bin("dnf5")):
        return "dnf"
    if fam == "suse" and probe.has_bin("zypper"):
        return "zypper"
    if probe.has_bin("pacman"):
        return "pacman"
    if probe.has_bin("apt") or probe.has_bin("apt-get"):
        return "apt"
    if probe.has_bin("dnf") or probe.has_bin("dnf5"):
        return "dnf"
    if probe.has_bin("zypper"):
        return "zypper"
    return "unknown"


def _dnf_bin() -> str:
    return "dnf" if probe.has_bin("dnf") else ("dnf5" if probe.has_bin("dnf5") else "dnf")


def _is_tumbleweed() -> bool:
    rel = probe.get_os_release()
    return rel.get("id") == "opensuse-tumbleweed" or "tumbleweed" in rel.get("id_like", "")


def _cache_file() -> str:
    return os.path.join(get_cache_dir(), "updates_cache.json")


def _feedback_cards() -> list:
    return feedback_cards("updates", "cc_updates.status_title")


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


def _backend_cache_dir(backend: str) -> str:
    if backend == "pacman":
        return "/var/cache/pacman/pkg"
    if backend == "apt":
        return "/var/cache/apt/archives"
    if backend == "dnf":
        return "/var/cache/libdnf5" if os.path.isdir("/var/cache/libdnf5") else "/var/cache/dnf"
    if backend == "zypper":
        return "/var/cache/zypp/packages"
    return ""


def _get_backend_mtimes(backend: str) -> list[float]:
    if backend == "pacman":
        paths = ("/var/lib/pacman/local", "/var/lib/pacman/sync", "/var/cache/pacman/pkg")
    elif backend == "apt":
        paths = ("/var/lib/dpkg/status", "/var/lib/apt/lists", "/var/cache/apt/archives")
    elif backend == "dnf":
        paths = ("/var/lib/rpm", _backend_cache_dir("dnf"), "/etc/yum.repos.d")
    elif backend == "zypper":
        paths = ("/var/lib/rpm", "/var/cache/zypp/raw", "/var/cache/zypp/packages")
    else:
        return [0.0, 0.0, 0.0]
    out = []
    for p in paths:
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


def _probe_cache_stats(backend: str, prev_mtime: float = -1.0, prev_bytes: int = -1, prev_count: int = -1) -> tuple[int, int, str]:
    cdir = _backend_cache_dir(backend)
    if not cdir or not os.path.isdir(cdir):
        return -1, -1, "-"
    cache_mtime = _get_backend_mtimes(backend)[2]
    if prev_bytes >= 0 and prev_count >= 0 and cache_mtime > 0 and abs(cache_mtime - prev_mtime) < 0.001:
        return prev_bytes, prev_count, _format_cache_info(prev_bytes, prev_count)
    total_bytes = 0
    file_count = 0
    try:
        if backend in ("dnf", "zypper"):
            for root, _, files in os.walk(cdir):
                for fn in files:
                    if not fn.endswith(".rpm"):
                        continue
                    try:
                        total_bytes += os.lstat(os.path.join(root, fn)).st_size
                        file_count += 1
                    except OSError:
                        continue
        else:
            for entry in os.scandir(cdir):
                try:
                    if backend == "apt" and not entry.name.endswith(".deb"):
                        continue
                    if entry.is_file(follow_symlinks=False):
                        total_bytes += entry.stat(follow_symlinks=False).st_size
                        file_count += 1
                except OSError:
                    continue
        return total_bytes, file_count, _format_cache_info(total_bytes, file_count)
    except OSError:
        _, out, _ = probe.run_cmd_full(["du", "-sh", cdir], timeout=4.0)
        return -1, -1, (out.split()[0] if out else "-")


def _load_disk_cache() -> bool:
    cf = _cache_file()
    if not os.path.exists(cf):
        return False
    try:
        with open(cf, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "flatpak_installed" not in data:
            return False
        if data.get("backend") != get_pkg_backend():
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
    cf = _cache_file()
    try:
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        payload = {k: _STATE.get(k) for k in _PERSIST_KEYS}
        tmp = f"{cf}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, cf)
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


def _probe_rpm_meta(names: list[str]) -> dict[str, tuple[str, str, float, str]]:
    if not names or not probe.has_bin("rpm"):
        return {}
    meta: dict[str, tuple[str, str, float, str]] = {}
    for i in range(0, len(names), 120):
        chunk = names[i : i + 120]
        _, out, _ = probe.run_cmd_full(
            ["rpm", "-q", "--qf", "%{NAME}|%{VERSION}-%{RELEASE}|%{SIZE}|%{SUMMARY}\n"] + chunk,
            timeout=5.0,
        )
        for ln in (out or "").splitlines():
            if not ln.strip() or ln.startswith("package "):
                continue
            parts = ln.split("|", 3)
            if len(parts) != 4:
                continue
            nm, vr, sz_raw, ds = [p.strip() for p in parts]
            try:
                mib = int(sz_raw) / (1024.0 * 1024.0)
            except ValueError:
                mib = 0.0
            sz_str = f"{mib:.1f} MiB" if mib >= 0.1 else f"{int(mib * 1024):.0f} KiB"
            meta[nm] = (vr, sz_str, mib, ds)
    return meta


def _probe_apt_updates_and_orphans() -> tuple[list[dict], list[dict]]:
    off: list[dict] = []
    if probe.has_bin("apt"):
        _, out_up, _ = probe.run_cmd_full(["env", "LC_ALL=C", "apt", "list", "--upgradable"], timeout=12.0)
        for ln in (out_up or "").splitlines():
            m = _APT_UPD_RE.match(ln.strip())
            if not m:
                continue
            pkg, repo, new_v, old_v = m.group(1), m.group(2), m.group(3), m.group(4)
            off.append({
                "name": pkg,
                "repo": repo.split(",", 1)[0],
                "old_ver": old_v,
                "new_ver": new_v,
                "dl_size": "-",
                "dl_mib": 0.0,
                "source": "repo",
                "_search": f"{pkg} {repo} {new_v}".lower(),
            })

    orphans: list[dict] = []
    if probe.has_bin("apt-get"):
        _, out_ar, _ = probe.run_cmd_full(["env", "LC_ALL=C", "apt-get", "-s", "autoremove"], timeout=8.0)
        raw_orphs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for ln in (out_ar or "").splitlines():
            m = _APT_ORPHAN_RE.match(ln.strip())
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                raw_orphs.append((m.group(1), m.group(2)))
        if raw_orphs:
            dq_meta: dict[str, tuple[str, str, float, str]] = {}
            if probe.has_bin("dpkg-query"):
                _, out_dq, _ = probe.run_cmd_full(
                    ["dpkg-query", "-W", "-f=${Package}|${Version}|${Installed-Size}|${binary:Summary}\n"] + [n for n, _ in raw_orphs],
                    timeout=5.0,
                )
                for ln in (out_dq or "").splitlines():
                    parts = ln.split("|", 3)
                    if len(parts) == 4:
                        nm, vr, kb_s, ds = [p.strip() for p in parts]
                        try:
                            mib = float(kb_s) / 1024.0
                        except ValueError:
                            mib = 0.0
                        sz_disp = f"{mib:.1f} MiB" if mib >= 1.0 else f"{int(mib * 1024)} KiB"
                        dq_meta[nm] = (vr, sz_disp, mib, ds)
            for nm, vr_fb in raw_orphs:
                vr, sz_disp, mib, ds = dq_meta.get(nm, (vr_fb, "-", 0.0, ""))
                orphans.append({
                    "name": nm,
                    "ver": vr,
                    "size": sz_disp,
                    "size_mib": mib,
                    "desc": ds,
                    "_search": f"{nm} {vr} {ds}".lower(),
                })
    return off, orphans


def _probe_dnf_updates_and_orphans(scan: bool) -> tuple[list[dict], list[dict]]:
    dnf_bin = _dnf_bin()
    cmd = ["env", "LC_ALL=C", dnf_bin] + ([] if scan else ["-C"]) + ["check-update", "-q"]
    _, out_up, _ = probe.run_cmd_full(cmd, timeout=45.0 if scan else 12.0)
    raw_up: list[tuple[str, str, str]] = []
    for ln in (out_up or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.lower().startswith("obsoleting packages"):
            break
        if ln[:1].isspace():
            continue
        m = _DNF_UPD_RE.match(s)
        if m:
            raw_up.append((m.group(1), m.group(3), m.group(4)))

    rpm_up = _probe_rpm_meta([r[0] for r in raw_up])
    off = [
        {
            "name": nm,
            "repo": repo,
            "old_ver": rpm_up.get(nm, ("-", "-", 0.0, ""))[0],
            "new_ver": new_v,
            "dl_size": "-",
            "dl_mib": 0.0,
            "source": "repo",
            "_search": f"{nm} {repo} {new_v}".lower(),
        }
        for nm, new_v, repo in raw_up
    ]

    orphans: list[dict] = []
    _, out_or, _ = probe.run_cmd_full(
        ["env", "LC_ALL=C", dnf_bin, "repoquery", "--unneeded", "-q", "--qf", "%{name}"],
        timeout=15.0,
    )
    onames = [ln.strip() for ln in (out_or or "").splitlines() if ln.strip() and not ln.startswith("Last metadata")]
    if onames:
        rpm_or = _probe_rpm_meta(onames)
        for nm in onames:
            vr, sz_disp, mib, ds = rpm_or.get(nm, ("-", "-", 0.0, ""))
            orphans.append({
                "name": nm,
                "ver": vr,
                "size": sz_disp,
                "size_mib": mib,
                "desc": ds,
                "_search": f"{nm} {vr} {ds}".lower(),
            })
    return off, orphans


def _probe_zypper_updates_and_orphans(scan: bool) -> tuple[list[dict], list[dict]]:
    cmd = ["env", "LC_ALL=C", "zypper", "--non-interactive"] + ([] if scan else ["--no-refresh"]) + ["-q", "lu"]
    _, out_up, _ = probe.run_cmd_full(cmd, timeout=45.0 if scan else 12.0)
    off: list[dict] = []
    for ln in (out_up or "").splitlines():
        cols = [c.strip() for c in ln.split("|")]
        if len(cols) >= 5 and cols[0] == "v":
            repo, nm, old_v, new_v = cols[1], cols[2], cols[3], cols[4]
            off.append({
                "name": nm,
                "repo": repo,
                "old_ver": old_v,
                "new_ver": new_v,
                "dl_size": "-",
                "dl_mib": 0.0,
                "source": "repo",
                "_search": f"{nm} {repo} {new_v}".lower(),
            })

    orphans: list[dict] = []
    _, out_or, _ = probe.run_cmd_full(
        ["env", "LC_ALL=C", "zypper", "--non-interactive", "packages", "--unneeded"],
        timeout=12.0,
    )
    raw_or: list[tuple[str, str]] = []
    for ln in (out_or or "").splitlines():
        cols = [c.strip() for c in ln.split("|")]
        if len(cols) >= 4 and cols[0] in ("i", "i+"):
            raw_or.append((cols[2], cols[3]))
    if raw_or:
        rpm_or = _probe_rpm_meta([n for n, _ in raw_or])
        for nm, vr_fb in raw_or:
            vr, sz_disp, mib, ds = rpm_or.get(nm, (vr_fb, "-", 0.0, ""))
            orphans.append({
                "name": nm,
                "ver": vr,
                "size": sz_disp,
                "size_mib": mib,
                "desc": ds,
                "_search": f"{nm} {vr} {ds}".lower(),
            })
    return off, orphans


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
    backend = get_pkg_backend()
    off: list[dict] = []
    aur: list[dict] = []
    foreign_list: list[dict] = []
    orphans: list[dict] = []

    if backend == "pacman":
        foreign_names = set()
        if probe.has_bin("pacman"):
            _, out_qm, _ = probe.run_cmd_full(["pacman", "-Qmq"], timeout=4.0)
            qms = [ln.strip() for ln in (out_qm or "").splitlines() if ln.strip()]
            foreign_names = set(qms)
            foreign_list = _probe_local_installed(qms)

            _, out_qu, _ = probe.run_cmd_full(["pacman", "-Qu"], timeout=5.0)
            qu_lines = [ln.strip() for ln in (out_qu or "").splitlines() if ln.strip()]
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

            _, out_qdt, _ = probe.run_cmd_full(["pacman", "-Qdtq"], timeout=4.0)
            qdt = [ln.strip() for ln in (out_qdt or "").splitlines() if ln.strip()]
            orphans = _probe_local_installed(qdt)
    elif backend == "apt":
        off, orphans = _probe_apt_updates_and_orphans()
    elif backend == "dnf":
        off, orphans = _probe_dnf_updates_and_orphans(scan=False)
    elif backend == "zypper":
        off, orphans = _probe_zypper_updates_and_orphans(scan=False)

    fp_installed = _probe_flatpak_local()
    fp_ids = {item.get("app_id", "") for item in fp_installed}
    fp_updates = [item for item in (prev_fp_updates or []) if item.get("app_id", "") in fp_ids] if prev_scanned else []

    c_mtime = prev_mtimes[2] if len(prev_mtimes) == 3 else -1.0
    c_bytes, c_count, c_info = _probe_cache_stats(backend, c_mtime, prev_bytes, prev_count)
    return {
        "backend": backend,
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
        "mtimes": _get_backend_mtimes(backend),
        "saved_epoch": time.time(),
    }


def _apply_local_snapshot(snap: dict) -> None:
    _STATE["backend"] = snap["backend"]
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
    backend = get_pkg_backend()
    if not _STATE["initialized"]:
        _load_disk_cache()
    _STATE["backend"] = backend
    _STATE["cache_info"] = _format_cache_info(
        int(_STATE.get("cache_bytes", -1)),
        int(_STATE.get("cache_count", -1)),
        str(_STATE.get("cache_info") or "-"),
    )
    cur_mtimes = _get_backend_mtimes(backend)
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
    backend = get_pkg_backend()
    _STATE["scanning"] = True
    scan_msg = t("cc_updates.scanning_msg") if backend == "pacman" else t("cc_updates.scanning_msg_generic", p0=backend.upper())
    set_feedback("updates", True, scan_msg)
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()

    prev_mtimes = list(_STATE.get("mtimes") or [0.0, 0.0, 0.0])
    prev_bytes = int(_STATE.get("cache_bytes", -1))
    prev_count = int(_STATE.get("cache_count", -1))

    def _worker():
        try:
            off: list[dict] = []
            aur: list[dict] = []
            foreign_list: list[dict] = []
            orphans: list[dict] = []

            if backend == "pacman":
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

                if probe.has_bin("pacman"):
                    _, out_qdt, _ = probe.run_cmd_full(["pacman", "-Qdtq"], timeout=5.0)
                    qdt = [ln.strip() for ln in (out_qdt or "").splitlines() if ln.strip()]
                    orphans = _probe_local_installed(qdt)
            elif backend == "apt":
                off, orphans = _probe_apt_updates_and_orphans()
            elif backend == "dnf":
                off, orphans = _probe_dnf_updates_and_orphans(scan=True)
            elif backend == "zypper":
                off, orphans = _probe_zypper_updates_and_orphans(scan=True)

            fp_installed = _probe_flatpak_local()
            fp_updates = _probe_flatpak_updates()

            c_mtime = prev_mtimes[2] if len(prev_mtimes) == 3 else -1.0
            c_bytes, c_count, c_info = _probe_cache_stats(backend, c_mtime, prev_bytes, prev_count)
            return off, aur, foreign_list, orphans, fp_updates, fp_installed, c_bytes, c_count, c_info
        except Exception as exc:
            return exc

    def _done(res):
        _STATE["scanning"] = False
        _STATE["scanned"] = True
        _STATE["initialized"] = True
        if isinstance(res, Exception):
            set_feedback("updates", False, str(res))
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return
        off, aur, foreign_list, orphans, fp_updates, fp_installed, c_bytes, c_count, cache_txt = res
        _STATE["backend"] = backend
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
        _STATE["mtimes"] = _get_backend_mtimes(backend)
        _STATE["saved_epoch"] = time.time()
        _save_disk_cache()
        done_msg = (
            t("cc_updates.scan_done_msg", p0=_STATE["last_ts"], p1=len(off), p2=len(aur), p3=len(orphans))
            if backend == "pacman"
            else t("cc_updates.scan_done_msg_generic", p0=_STATE["last_ts"], p1=len(off), p2=len(orphans))
        )
        set_feedback("updates", True, done_msg)
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_worker, on_done=_done)


def _open_pkg_terminal(shell_cmd: str, ok_msg: str) -> None:
    if probe.open_terminal(["bash", "-c", f"{shell_cmd}; echo; read -p 'Press Enter to close...'"]):
        set_feedback("updates", True, ok_msg)
    else:
        set_feedback("updates", False, t("cc_updates.no_terminal_err"))


def handle_cc_updates_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_upd:"):
        return False
    cmd = uri.split(":", 1)[1]
    backend = get_pkg_backend()
    aur_tool = probe.get_preferred_pkg_manager(aur_only=True)

    if cmd == "scan":
        _start_scan(cc_panel)
        return True
    if cmd == "refresh":
        ensure_updates_loaded(cc_panel, force=True)
        return True
    elif cmd in ("upgrade_term", "upgrade_official"):
        if backend == "pacman":
            tool = f"{aur_tool} -Syu" if (cmd == "upgrade_term" and aur_tool) else "sudo pacman -Syu"
            ok_msg = t("cc_updates.term_full_msg", p0=tool) if cmd == "upgrade_term" else t("cc_updates.term_off_msg")
        elif backend == "apt":
            tool = "sudo apt update && sudo apt upgrade"
            ok_msg = t("cc_updates.term_full_msg", p0=tool)
        elif backend == "dnf":
            tool = f"sudo {_dnf_bin()} upgrade"
            ok_msg = t("cc_updates.term_full_msg", p0=tool)
        elif backend == "zypper":
            tool = "sudo zypper dup" if _is_tumbleweed() else "sudo zypper up"
            ok_msg = t("cc_updates.term_full_msg", p0=tool)
        else:
            set_feedback("updates", False, t("cc_updates.pm_not_detected"))
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        banner = shlex.quote(t("cc_updates.term_banner_upgrade"))
        _open_pkg_terminal(f"printf '%s\\n' {banner}; {tool}", ok_msg)
    elif cmd == "upgrade_aur":
        if backend != "pacman":
            set_feedback("updates", False, t("cc_updates.aur_arch_only"))
        elif not aur_tool:
            set_feedback("updates", False, t("cc_updates.aur_helper_missing"))
        else:
            tool = f"{aur_tool} -Sua"
            _open_pkg_terminal(tool, t("cc_updates.term_aur_msg", p0=tool))
    elif cmd.startswith("pkg_up:"):
        pkg = cmd.split(":", 1)[1].strip()
        qpkg = shlex.quote(pkg)
        if backend == "pacman":
            is_aur = any(item["name"] == pkg for item in _STATE["aur"])
            tool = f"{aur_tool} -S {qpkg}" if (is_aur and aur_tool) else f"sudo pacman -S {qpkg}"
        elif backend == "apt":
            tool = f"sudo apt install --only-upgrade {qpkg}"
        elif backend == "dnf":
            tool = f"sudo {_dnf_bin()} upgrade {qpkg}"
        elif backend == "zypper":
            tool = f"sudo zypper update {qpkg}"
        else:
            set_feedback("updates", False, t("cc_updates.pm_not_detected"))
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        _open_pkg_terminal(tool, t("cc_updates.term_pkg_msg", p0=pkg))
    elif cmd in ("paccache", "clean_cache"):
        if backend == "pacman":
            _open_pkg_terminal("sudo paccache -rk2", t("cc_updates.term_cache_msg"))
        elif backend == "apt":
            c_cmd = "sudo apt-get autoclean && sudo apt-get clean"
            _open_pkg_terminal(c_cmd, t("cc_updates.term_cache_msg_generic", p0=c_cmd))
        elif backend == "dnf":
            c_cmd = f"sudo {_dnf_bin()} clean packages"
            _open_pkg_terminal(c_cmd, t("cc_updates.term_cache_msg_generic", p0=c_cmd))
        elif backend == "zypper":
            c_cmd = "sudo zypper clean --all"
            _open_pkg_terminal(c_cmd, t("cc_updates.term_cache_msg_generic", p0=c_cmd))
        else:
            set_feedback("updates", False, t("cc_updates.pm_not_detected"))
    elif cmd == "rm_orphans":
        if backend == "pacman":
            o_cmd = "pkgs=$(pacman -Qdtq); if [ -n \"$pkgs\" ]; then echo \"$pkgs\" | sudo pacman -Rns -; else echo 'No orphan packages found.'; fi"
            ok_msg = t("cc_updates.term_rm_orphans_msg")
        elif backend == "apt":
            o_cmd = "sudo apt-get autoremove"
            ok_msg = t("cc_updates.term_rm_orphans_msg_generic", p0=o_cmd)
        elif backend == "dnf":
            o_cmd = f"sudo {_dnf_bin()} autoremove"
            ok_msg = t("cc_updates.term_rm_orphans_msg_generic", p0=o_cmd)
        elif backend == "zypper":
            o_cmd = "pkgs=$(zypper --non-interactive packages --unneeded | awk -F'|' 'NR>2 && $1 ~ /i/ {gsub(/ /, \"\", $3); print $3}'); if [ -n \"$pkgs\" ]; then sudo zypper remove -u $pkgs; else echo 'No orphan packages found.'; fi"
            ok_msg = t("cc_updates.term_rm_orphans_msg_generic", p0="sudo zypper remove -u")
        else:
            set_feedback("updates", False, t("cc_updates.pm_not_detected"))
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        _open_pkg_terminal(o_cmd, ok_msg)
    elif cmd.startswith("rm_orphan:") or cmd.startswith("rm_foreign:"):
        pkg = cmd.split(":", 1)[1].strip()
        qpkg = shlex.quote(pkg)
        if backend == "pacman":
            r_cmd = f"sudo pacman -Rns {qpkg}"
            ok_msg = t("cc_updates.term_rm_one_msg", p0=pkg)
        elif backend == "apt":
            r_cmd = f"sudo apt-get purge {qpkg}"
            ok_msg = t("cc_updates.term_rm_one_msg_generic", p0=r_cmd)
        elif backend == "dnf":
            r_cmd = f"sudo {_dnf_bin()} remove {qpkg}"
            ok_msg = t("cc_updates.term_rm_one_msg_generic", p0=r_cmd)
        elif backend == "zypper":
            r_cmd = f"sudo zypper remove -u {qpkg}"
            ok_msg = t("cc_updates.term_rm_one_msg_generic", p0=r_cmd)
        else:
            set_feedback("updates", False, t("cc_updates.pm_not_detected"))
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        _open_pkg_terminal(r_cmd, ok_msg)
    elif cmd.startswith("keep_orphan:"):
        pkg = cmd.split(":", 1)[1].strip()
        qpkg = shlex.quote(pkg)
        if backend == "pacman":
            k_cmd = f"sudo pacman -D --asexplicit {qpkg}"
            ok_msg = t("cc_updates.term_keep_one_msg", p0=pkg)
        elif backend == "apt":
            k_cmd = f"sudo apt-mark manual {qpkg}"
            ok_msg = t("cc_updates.term_keep_one_msg_generic", p0=k_cmd)
        elif backend == "dnf":
            dbin = _dnf_bin()
            k_cmd = f"sudo {dbin} mark {'user' if dbin == 'dnf5' else 'install'} {qpkg}"
            ok_msg = t("cc_updates.term_keep_one_msg_generic", p0=k_cmd)
        else:
            set_feedback("updates", False, t("cc_updates.pm_not_detected"))
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        _open_pkg_terminal(k_cmd, ok_msg)
    elif cmd == "save_pkglist":
        if _PKGLIST_SCRIPT and os.path.exists(_PKGLIST_SCRIPT):
            from ui.embedded_tui import start_panel_terminal

            start_panel_terminal(
                cc_panel,
                t("common.term_title", p0="nc-vault pkglist"),
                [
                    "bash",
                    "-c",
                    f"read -r -p 'Pkglist label [snapshot]: ' lbl; printf '%s\\n' \"${{lbl:-snapshot}}\" | sudo {shlex.quote(_PKGLIST_SCRIPT)} pkglist-stdin",
                ],
            )
            return True
        set_feedback("updates", False, t("cc_updates.missing_script_err", p0=_PKGLIST_SCRIPT or "nc-vault"))
    elif cmd == "upgrade_flatpak":
        _open_pkg_terminal("flatpak update", t("cc_updates.term_fp_full_msg"))
    elif cmd == "flatpak_unused":
        _open_pkg_terminal("flatpak uninstall --unused", t("cc_updates.term_fp_unused_msg"))
    elif cmd.startswith("fp_up:"):
        app_id = cmd.split(":", 1)[1].strip()
        _open_pkg_terminal(f"flatpak update {shlex.quote(app_id)}", t("cc_updates.term_fp_one_msg", p0=app_id))
    elif cmd.startswith("fp_rm:"):
        app_id = cmd.split(":", 1)[1].strip()
        _open_pkg_terminal(f"flatpak uninstall {shlex.quote(app_id)}", t("cc_updates.term_fp_rm_msg", p0=app_id))
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
