#!/usr/bin/env python3
"""NC-UI Control Center — Printers & CUPS Backend Engine (`panels/cc_engine_printers.py`)."""

import concurrent.futures
import csv
import datetime
import getpass
import grp
import io
import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse

from core import log, probe
from core.completion import rank_candidates
from core.config import get_cache_dir
from core.i18n import t
from core.paths import find_bin
from core.state import STATE
from ui.kit import ActionBtn, Badge, Box, InlineField, StateBadge, Txt, feedback_cards as _kit_feedback_cards, get_active_inline_entry, set_feedback, start_inline_input

_CACHE: dict[str, tuple[float, object]] = {}
_LPOPT_META_CACHE: dict[str, tuple[float, dict]] = {}
_PENDING_SUDO: dict[str, object] = {}
_PENDING_INSTALL_SUDO: dict[str, object] = {}
_INSTALL_LOCK = threading.Lock()
_INSTALL_RUNNING = ""

_PRINTABLE_EXTS = (".pdf", ".ps", ".eps", ".txt", ".png", ".jpg", ".jpeg", ".svg")
_PPD_EXTS = (".ppd", ".ppd.gz")
_QUEUE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_OPT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PKG_NAME_RE = re.compile(r"^[a-z0-9@._+-]{1,128}$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}-\d{1,12}$")
_PAGE_RANGE_RE = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")
_IPP_JOBS_TEST_PATH = (
    os.path.join(os.environ["XDG_RUNTIME_DIR"], "ncui", "ncui_ipp_jobs.test")
    if os.environ.get("XDG_RUNTIME_DIR")
    else os.path.join(get_cache_dir(), "ncui_ipp_jobs.test")
)
_IPP_ATTRS_TEST = "/usr/share/cups/ipptool/get-printer-attributes.test"

_IPP_JOBS_TEST_SPEC = """{
    NAME "NC-UI Get Jobs"
    OPERATION Get-Jobs
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR language attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR keyword which-jobs $which_jobs
    ATTR boolean my-jobs false
    ATTR keyword requested-attributes job-id,job-printer-uri,job-state,job-state-reasons,job-name,job-originating-user-name,job-priority,copies,job-k-octets,job-impressions-completed,job-media-sheets-completed
    STATUS successful-ok
    DISPLAY job-id
    DISPLAY job-printer-uri
    DISPLAY job-state
    DISPLAY job-state-reasons
    DISPLAY job-name
    DISPLAY job-originating-user-name
    DISPLAY job-priority
    DISPLAY copies
    DISPLAY job-k-octets
    DISPLAY job-impressions-completed
    DISPLAY job-media-sheets-completed
}
"""

_BUILTIN_DRIVERS = [
    ("everywhere", "IPP Everywhere / AirPrint (Driverless)"),
    ("drv:///cupsfilters.drv/pwgrast.ppd", "Generic PWG Raster / IPP (cupsfilters)"),
    ("raw", "Raw Queue (Pass-Through)"),
    ("drv:///sample.drv/generic.ppd", "CUPS Generic PostScript Printer"),
    ("drv:///sample.drv/generpcl.ppd", "CUPS Generic PCL Laser Printer"),
    ("drv:///sample.drv/deskjet.ppd", "HP DeskJet Series (CUPS Sample)"),
    ("drv:///sample.drv/laserjet.ppd", "HP LaserJet Series (CUPS Sample)"),
    ("drv:///sample.drv/epson9.ppd", "Epson 9-Pin Impact Series"),
    ("drv:///sample.drv/epson24.ppd", "Epson 24-Pin Impact Series"),
    ("drv:///sample.drv/zebracpl.ppd", "Zebra CPCL Label Printer"),
    ("drv:///sample.drv/zebraep2.ppd", "Zebra EPL2 Label Printer"),
    ("drv:///sample.drv/zebrazpl.ppd", "Zebra ZPL Label Printer"),
]

_PROTO_PRESETS = [
    ("socket", "TCP/IP", "socket://{host}:9100"),
    ("ipp", "IPP / AirPrint", "ipp://{host}:631/ipp/print"),
    ("ipps", "IPPS (TLS)", "ipps://{host}:631/ipp/print"),
    ("lpd", "LPD / LPR", "lpd://{host}/BINARY_P1"),
    ("smb", "SMB / Windows", "smb://{host}/Printer"),
    ("usb", "USB Local", "usb://"),
]

# Mapping of BOTH PPD keys and IPP keys that support system-wide `<key>-default=<val>` via `lpadmin`
_LPADMIN_DEFAULT_KEYS = {
    "PageSize",
    "Duplex",
    "ColorModel",
    "cupsPrintQuality",
    "Resolution",
    "InputSlot",
    "MediaType",
    "media",
    "sides",
    "print-color-mode",
    "print-quality",
    "orientation-requested",
    "number-up",
    "print-scaling",
    "job-sheets",
}


def _cups_bin(name: str) -> str:
    p = find_bin(name)
    if p:
        return p
    for d in ("/usr/sbin", "/sbin"):
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return ""


def _bar_cache_path() -> str:
    return os.path.join(get_cache_dir(), "printers_bar_cache.json")


def _run_c(args: list[str], timeout: float = 2.0, input_data: bytes = None) -> tuple[bool, str, str]:
    return probe.run_cmd_full(["/usr/bin/env", "LC_ALL=C"] + list(args), timeout=timeout, input_data=input_data)


def _cached(key: str, ttl: float, loader):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and (now - hit[0]) < ttl:
        return hit[1]
    val = loader()
    _CACHE[key] = (now, val)
    return val


def invalidate_printers_cache(keep_drivers: bool = True) -> None:
    drv = _CACHE.get("drivers_list") if keep_drivers else None
    _CACHE.clear()
    _LPOPT_META_CACHE.clear()
    if drv is not None:
        _CACHE["drivers_list"] = drv
    try:
        os.remove(_bar_cache_path())
    except OSError:
        pass


def _set_fb(ok: bool, msg: str) -> None:
    set_feedback("printers", ok, msg, key="fb")


def feedback_cards() -> list:
    return _kit_feedback_cards("printers", "cc_printers.status_title", key="fb", ttl=16.0)


def get_pending_confirm(cc_panel) -> str:
    raw = cc_panel.get_state("prn_confirm", None)
    if isinstance(raw, dict) and (time.monotonic() - float(raw.get("ts", 0.0))) < 12.0:
        return str(raw.get("target", "") or "")
    return ""


def set_pending_confirm(cc_panel, target: str) -> None:
    cc_panel.set_state("prn_confirm", {"target": target, "ts": time.monotonic()} if target else None)


def _current_user() -> str:
    return getpass.getuser() or os.environ.get("USER", "root")


def _user_in_cups_groups() -> tuple[bool, list[str]]:
    u = _current_user()
    if u == "root":
        return True, ["root"]
    found: list[str] = []
    try:
        gids = set(os.getgroups())
        for gname in ("sys", "lp", "wheel", "lpadmin"):
            try:
                gr = grp.getgrnam(gname)
                if gr.gr_gid in gids or u in gr.gr_mem:
                    found.append(gname)
            except KeyError:
                continue
    except Exception:
        pass
    return ("sys" in found or "wheel" in found or "lpadmin" in found), found


def _unit_enabled_fallback(unit: str) -> bool:
    for d in (
        "/etc/systemd/system/multi-user.target.wants",
        "/etc/systemd/system/sockets.target.wants",
        "/etc/systemd/system/printer.target.wants",
    ):
        if os.path.lexists(os.path.join(d, unit)):
            return True
    sc_bin = _cups_bin("systemctl")
    if not sc_bin:
        return False
    ok, out, _ = _run_c([sc_bin, "is-enabled", unit], timeout=0.6)
    return bool(ok and out.strip() in ("enabled", "static", "indirect", "alias"))


def get_cups_daemon_state() -> dict:
    def _load() -> dict:
        sock_exists = os.path.exists("/run/cups/cups.sock")
        lpstat_bin = _cups_bin("lpstat")
        lpadmin_bin = _cups_bin("lpadmin")
        systemctl_bin = _cups_bin("systemctl")
        sched_ok, sched_out, _ = _run_c([lpstat_bin, "-r"], timeout=0.7) if lpstat_bin else (False, "", "")
        scheduler_up = bool(sched_ok and "is running" in sched_out.lower())

        if systemctl_bin:
            _ok_s, out_s, _ = _run_c([systemctl_bin, "is-active", "cups.service", "cups.socket", "avahi-daemon.service"], timeout=0.6)
        else:
            out_s = ""
        u_lines = [ln.strip() for ln in (out_s or "").splitlines()]
        svc_active = (len(u_lines) > 0 and u_lines[0] == "active") or scheduler_up
        sock_active = (len(u_lines) > 1 and u_lines[1] == "active") or sock_exists
        avahi_active = (len(u_lines) > 2 and u_lines[2] == "active") or os.path.exists("/run/avahi-daemon/socket")
        svc_enabled = _unit_enabled_fallback("cups.service") or _unit_enabled_fallback("cups.socket")
        in_grp, grp_list = _user_in_cups_groups()

        spool_files = 0
        spool_bytes = 0
        if os.path.isdir("/var/spool/cups"):
            try:
                for ent in os.scandir("/var/spool/cups"):
                    if ent.is_file(follow_symlinks=False):
                        spool_files += 1
                        spool_bytes += ent.stat(follow_symlinks=False).st_size
            except OSError:
                pass

        has_cups_pdf = os.path.exists("/usr/lib/cups/backend/cups-pdf")
        return {
            "cups_installed": bool(lpstat_bin and lpadmin_bin),
            "has_systemctl": bool(systemctl_bin),
            "scheduler_up": scheduler_up,
            "service_active": svc_active,
            "socket_active": sock_active,
            "service_enabled": svc_enabled,
            "avahi_active": avahi_active,
            "in_sysgroup": in_grp,
            "groups": grp_list,
            "spool_files": spool_files,
            "spool_bytes": spool_bytes,
            "has_cups_pdf": has_cups_pdf,
            "tools": {
                "cups": bool(lpadmin_bin),
                "driverless": probe.has_bin("driverless"),
                "ipptool": probe.has_bin("ipptool"),
                "avahi": probe.has_bin("avahi-browse"),
                "gs": probe.has_bin("gs"),
                "smbclient": probe.has_bin("smbclient"),
            },
            "driver_packs": {
                "cups-pdf": has_cups_pdf,
                "gutenprint": os.path.exists("/usr/lib/cups/driver/gutenprint.5.3")
                or os.path.isdir("/usr/share/cups/model/gutenprint"),
                "foomatic": probe.has_bin("foomatic-rip") or os.path.exists("/usr/lib/cups/filter/foomatic-rip"),
                "hplip": os.path.exists("/usr/lib/cups/backend/hp") or probe.has_bin("hp-setup"),
                "brlaser": os.path.exists("/usr/lib/cups/filter/rastertobrlaser")
                or os.path.exists("/usr/share/cups/drv/brlaser.drv"),
            },
        }

    return _cached("daemon_state", 4.0, _load)


def build_offline_spooler_card(daemon: dict) -> Box:
    """Card unificata e coerente mostrata quando `cups.service` è fermo nelle viste operative."""
    acts = [StateBadge(False, t("common.active"), t("cc_printers.stopped"))]
    if not daemon.get("cups_installed"):
        acts.append(Badge(t("ui.missing", p0="cups"), style="warn"))
    elif daemon.get("has_systemctl", bool(_cups_bin("systemctl"))):
        acts.append(ActionBtn(t("cc_printers.start_cups"), "cc_prn:svc:start:cups.service", tone="apply"))
    else:
        acts.append(Badge(t("ui.missing", p0="systemctl"), style="warn"))
    acts.append(ActionBtn(t("cc_printers.server_tab_btn"), "cc_prn:goto_subtab:server", tone="neutral"))
    sp_box = Box(
        t("cc_printers.spooler_offline_title"),
        icon="\uf02f",
        actions=acts,
        highlighted=False,
    )
    sp_box.add_kv(
        t("cc_printers.cups_package"),
        StateBadge(daemon["cups_installed"], t("cc_printers.installed"), t("cc_printers.missing_bin")),
    )
    sp_box.add_kv(
        t("cc_printers.boot_autostart"),
        StateBadge(daemon["service_enabled"], t("common.enabled"), t("common.disabled")),
        requires=["systemctl"],
    )
    sp_box.add_kv(
        t("cc_printers.kv_avahi_mdns"),
        StateBadge(daemon["avahi_active"], t("common.active"), t("cc_printers.stopped")),
    )
    sp_box.add_row(f"  {Txt(t('cc_printers.spooler_offline_hint'), role='attention')}")
    return sp_box


def _classify_uri(uri: str) -> str:
    u = str(uri or "").strip().lower()
    if u.startswith("ipps://"):
        return "IPPS"
    if u.startswith("ipp://"):
        return "IPP"
    if u.startswith("dnssd://") or u.startswith("implicitclass://"):
        return "MDNS"
    if u.startswith("socket://"):
        return "TCP:9100"
    if u.startswith("usb://"):
        return "USB"
    if u.startswith("smb://"):
        return "SMB"
    if u.startswith("lpd://"):
        return "LPD"
    if u.startswith("cups-pdf:") or "pdf" in u:
        return "PDF"
    return "LOCAL"


def humanize_ppd_choice(opt_key: str, raw_val: str) -> str:
    """Converte i token tecnici PPD/IPP nei corrispondenti nomi leggibili localizzati."""
    v = str(raw_val or "").strip()
    vl = v.lower()
    kl = str(opt_key or "").lower()

    if kl in ("duplex", "sides"):
        if vl in ("none", "one-sided", "false", "off"):
            return t("cc_printers.duplex_off")
        if vl in ("duplexnotumble", "two-sided-long-edge"):
            return t("cc_printers.duplex_long")
        if vl in ("duplextumble", "two-sided-short-edge"):
            return t("cc_printers.duplex_short")

    if kl in ("colormodel", "print-color-mode"):
        if vl in ("gray", "grayscale", "monochrome", "black", "devicegray", "processgray"):
            return t("cc_printers.color_mono")
        if vl in ("rgb", "srgb", "cmyk", "color", "auto", "devicecmyk"):
            return t("cc_printers.color_rgb")

    if kl in ("cupsprintquality", "print-quality"):
        if vl in ("3", "draft", "fastdraft", "economy"):
            return t("cc_printers.qual_draft")
        if vl in ("4", "normal", "standard"):
            return t("cc_printers.qual_normal")
        if vl in ("5", "high", "best", "fine", "photo"):
            return t("cc_printers.qual_high")

    if kl in ("inputslot", "media-source"):
        if vl in ("auto", "autoselect", "default"):
            return t("cc_printers.tray_auto")
        if vl in ("manual", "manualfeed", "bypass", "mptray", "multipurpose", "by-pass-tray"):
            return t("cc_printers.tray_manual")
        m_tr = re.match(r"^tray[-_]?(\d+)$", vl)
        if m_tr:
            return f"{t('cc_printers.tray_n')} {m_tr.group(1)}"
        if vl == "upper":
            return f"{t('cc_printers.tray_n')} 1"
        if vl == "lower":
            return f"{t('cc_printers.tray_n')} 2"

    if kl == "mediatype":
        if vl in ("plain", "stationery", "auto"):
            return t("cc_printers.mtype_plain")
        if vl in ("thick", "heavy", "cardstock"):
            return t("cc_printers.mtype_thick")
        if vl in ("recycled",):
            return t("cc_printers.mtype_recycled")
        if vl in ("glossy", "photo", "photographic"):
            return t("cc_printers.mtype_photo")
        if vl in ("envelope",):
            return t("cc_printers.mtype_envelope")
        if vl in ("labels", "label"):
            return t("cc_printers.mtype_labels")

    if vl in ("true", "on"):
        return t("common.on")
    if vl in ("false", "off"):
        return t("common.off")
    if vl == "none":
        return t("common.none")
    return v


def _ensure_ipp_jobs_test_file() -> str:
    if os.path.islink(_IPP_JOBS_TEST_PATH):
        return ""
    if not os.path.isfile(_IPP_JOBS_TEST_PATH):
        try:
            os.makedirs(os.path.dirname(_IPP_JOBS_TEST_PATH), mode=0o700, exist_ok=True)
            fd = os.open(_IPP_JOBS_TEST_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_IPP_JOBS_TEST_SPEC)
        except FileExistsError:
            pass
        except OSError:
            return ""
    return _IPP_JOBS_TEST_PATH


def _query_ipp_jobs_meta(mode: str, printer: str = "") -> dict[int, dict]:
    """Interroga in un'unica chiamata IPP locale titolo documento, priorità, copie e pagine."""
    if not probe.has_bin("ipptool"):
        return {}
    tpath = _ensure_ipp_jobs_test_file()
    if not tpath:
        return {}
    uri = f"ipp://localhost:631/printers/{printer}" if printer else "ipp://localhost:631/"
    ok, out, _ = _run_c(["ipptool", "-c", "-T", "1", "-d", f"which_jobs={mode}", uri, tpath], timeout=1.4)
    if not ok or not out:
        return {}
    meta: dict[int, dict] = {}
    try:
        reader = csv.DictReader(io.StringIO(out))
        for row in reader:
            raw_id = str(row.get("job-id", "") or "").strip()
            if not raw_id.isdigit():
                continue
            jnum = int(raw_id)
            jstate_raw = str(row.get("job-state", "") or "").strip().lower()
            st_map = {
                "3": "pending",
                "pending": "pending",
                "4": "held",
                "pending-held": "held",
                "5": "printing",
                "processing": "printing",
                "6": "stopped",
                "processing-stopped": "stopped",
                "7": "canceled",
                "canceled": "canceled",
                "8": "aborted",
                "aborted": "aborted",
                "9": "completed",
                "completed": "completed",
            }
            prio_s = str(row.get("job-priority", "") or "").strip()
            cop_s = str(row.get("copies", "") or "").strip()
            sh_s = str(row.get("job-media-sheets-completed", "") or row.get("job-impressions-completed", "") or "").strip()
            k_oct = str(row.get("job-k-octets", "") or "").strip()
            p_uri = str(row.get("job-printer-uri", "") or "").strip()
            prn_from_uri = p_uri.rsplit("/", 1)[-1] if "/printers/" in p_uri else ""
            reasons = str(row.get("job-state-reasons", "") or "").strip()
            if reasons.lower() in ("none", "job-incoming", "job-printing"):
                reasons = ""
            meta[jnum] = {
                "title": str(row.get("job-name", "") or "").strip(),
                "user": str(row.get("job-originating-user-name", "") or "").strip(),
                "state": st_map.get(jstate_raw, ""),
                "priority": int(prio_s) if prio_s.isdigit() else 50,
                "copies": int(cop_s) if cop_s.isdigit() and int(cop_s) > 0 else 1,
                "pages": int(sh_s) if sh_s.isdigit() else 0,
                "size": int(k_oct) * 1024 if k_oct.isdigit() else 0,
                "printer": prn_from_uri,
                "reasons": reasons,
            }
    except Exception:
        pass
    return meta


def get_print_jobs(which: str = "not-completed", printer: str = "") -> list[dict]:
    if not get_cups_daemon_state()["scheduler_up"]:
        return []
    mode = "completed" if which == "completed" else "not-completed"
    key = f"jobs:{mode}:{printer or '*'}"

    def _load() -> list[dict]:
        ipp_meta = _query_ipp_jobs_meta(mode, printer=printer)
        cmd = ["lpstat", "-W", mode, "-o"]
        if printer:
            cmd.append(printer)
        ok, out, _ = _run_c(cmd, timeout=1.5)

        lpq_titles: dict[int, str] = {}
        held_ids: set[int] = set()
        printing_ids: set[int] = set()
        if mode == "not-completed" and probe.has_bin("lpq"):
            ok_q, out_q, _ = _run_c(["lpq", "-a"] if not printer else ["lpq", "-P", printer], timeout=1.0)
            if ok_q and out_q:
                for qln in out_q.splitlines():
                    parts = qln.split()
                    if len(parts) >= 5 and parts[2].isdigit() and parts[0].lower().endswith(("st", "nd", "rd", "th", "active", "held")):
                        jn = int(parts[2])
                        rank = parts[0].lower()
                        if rank == "active":
                            printing_ids.add(jn)
                        elif rank == "held":
                            held_ids.add(jn)
                        # Format: Rank Owner Job File(s) Total Size (e.g. `... 305152 bytes`)
                        if len(parts) >= 6 and parts[-1].lower() == "bytes":
                            title_s = " ".join(parts[3:-2]).strip()
                            if title_s and title_s != "(stdin)":
                                lpq_titles[jn] = title_s

        jobs: list[dict] = []
        seen_nums: set[int] = set()
        if ok and out:
            for ln in out.splitlines():
                s = ln.strip()
                if not s or s.startswith(("\t", "Status:", "Alerts:", "queued ")):
                    continue
                m = re.match(r"^(\S+)-(\d+)\s+(\S+)\s+(\d+)\s+(.+)$", s)
                if not m:
                    continue
                prn, jnum_s, user, sz_s, ts_str = m.groups()
                jnum = int(jnum_s)
                seen_nums.add(jnum)
                jid = f"{prn}-{jnum}"
                im = ipp_meta.get(jnum, {})
                if mode == "completed":
                    st = im.get("state") or "completed"
                else:
                    st = im.get("state") or (
                        "printing" if jnum in printing_ids else ("held" if jnum in held_ids else "pending")
                    )
                raw_title = im.get("title") or lpq_titles.get(jnum) or ""
                if raw_title in ("", "Untitled", "(stdin)", "Unknown"):
                    raw_title = f"Job #{jnum}"
                jobs.append({
                    "id": jid,
                    "printer": prn,
                    "num": jnum,
                    "user": im.get("user") or user,
                    "size": int(sz_s) or im.get("size", 0),
                    "submitted": ts_str.strip(),
                    "state": st,
                    "title": raw_title,
                    "priority": int(im.get("priority", 50)),
                    "copies": int(im.get("copies", 1)),
                    "pages": int(im.get("pages", 0)),
                    "reasons": str(im.get("reasons", "")),
                })
        return jobs

    return _cached(key, 1.5, _load)


def _get_lpoptions_summary_cached(printer_name: str) -> dict:
    now = time.monotonic()
    hit = _LPOPT_META_CACHE.get(printer_name)
    if hit is not None and (now - hit[0]) < 15.0:
        return hit[1]
    res = {"make_model": "", "shared": False, "error_policy": "retry-job", "description": "", "location": ""}
    if not probe.has_bin("lpoptions"):
        _LPOPT_META_CACHE[printer_name] = (now, res)
        return res
    ok_o, out_o, _ = _run_c(["lpoptions", "-p", printer_name], timeout=1.0)
    if ok_o and out_o:
        try:
            toks = shlex.split(out_o)
        except ValueError:
            toks = out_o.split()
        for tk in toks:
            if "=" not in tk:
                continue
            k, v = tk.split("=", 1)
            if k == "printer-make-and-model":
                res["make_model"] = v
            elif k == "printer-is-shared":
                res["shared"] = v.lower() in ("true", "1", "yes")
            elif k == "printer-error-policy":
                res["error_policy"] = v
            elif k == "printer-info":
                res["description"] = v
            elif k == "printer-location":
                res["location"] = v
    _LPOPT_META_CACHE[printer_name] = (now, res)
    return res


def get_printers_snapshot() -> dict:
    st = get_cups_daemon_state()
    if not st["scheduler_up"]:
        return {"default": "", "printers": [], "classes": [], "has_pdf_queue": False}

    def _load() -> dict:
        default_prn = ""
        uris: dict[str, str] = {}
        accepting_map: dict[str, bool] = {}
        classes: list[dict] = []
        prn_states: dict[str, tuple[bool, str]] = {}
        job_counts: dict[str, int] = {}

        # 1. Single consolidated `lpstat -t` call for default, classes, URIs, accepting, states & active job counts
        ok_t, out_t, _ = _run_c(["lpstat", "-t"], timeout=1.6)
        if ok_t and out_t:
            cur_cls: dict | None = None
            for ln in out_t.splitlines():
                if not ln.strip():
                    continue
                if cur_cls is not None:
                    if ln.startswith(("\t", " ")):
                        m_name = ln.strip()
                        if m_name and m_name != "unknown":
                            cur_cls["members"].append(m_name)
                        continue
                    cur_cls = None

                s = ln.strip()
                if s.startswith("system default destination:"):
                    default_prn = s.split(":", 1)[1].strip()
                elif s.startswith("members of class ") and s.endswith(":"):
                    cname = s[len("members of class ") : -1].strip()
                    cur_cls = {"name": cname, "members": []}
                    classes.append(cur_cls)
                elif s.startswith("device for "):
                    m_dev = re.match(r"^device for (\S+):\s*(.+)$", s)
                    if m_dev:
                        uris[m_dev.group(1)] = m_dev.group(2).strip()
                elif " not accepting requests" in s:
                    accepting_map[s.split()[0]] = False
                elif " accepting requests" in s:
                    accepting_map[s.split()[0]] = True
                elif s.startswith("printer "):
                    rest = s[len("printer ") :].strip()
                    pname = rest.split()[0]
                    low = rest.lower()
                    en = "disabled" not in low
                    pst = "disabled" if not en else ("printing" if "now printing" in low else "idle")
                    prn_states[pname] = (en, pst)
                else:
                    m_jb = re.match(r"^(\S+)-(\d+)\s+\S+\s+\d+\s+.+$", s)
                    if m_jb:
                        jp = m_jb.group(1)
                        job_counts[jp] = job_counts.get(jp, 0) + 1

        prn_classes: dict[str, list[str]] = {}
        for cl in classes:
            for mem in cl["members"]:
                prn_classes.setdefault(mem, []).append(cl["name"])

        # 2. Single `lpstat -l -p` call for verbose descriptions, locations & hardware alerts
        verbose_meta: dict[str, dict] = {}
        ok_p, out_p, _ = _run_c(["lpstat", "-l", "-p"], timeout=1.4)
        if ok_p and out_p:
            cur_v: dict | None = None
            for ln in out_p.splitlines():
                if ln.startswith("printer "):
                    pname = ln[len("printer ") :].strip().split()[0]
                    cur_v = {"description": "", "location": "", "alerts": ""}
                    verbose_meta[pname] = cur_v
                    if pname not in prn_states:
                        low = ln.lower()
                        en = "disabled" not in low
                        prn_states[pname] = (en, "disabled" if not en else ("printing" if "now printing" in low else "idle"))
                elif cur_v is not None and ln.startswith("\t"):
                    kv = ln.strip()
                    if kv.startswith("Description:"):
                        cur_v["description"] = kv.split(":", 1)[1].strip()
                    elif kv.startswith("Location:"):
                        cur_v["location"] = kv.split(":", 1)[1].strip()
                    elif kv.startswith("Alerts:"):
                        al = kv.split(":", 1)[1].strip()
                        cur_v["alerts"] = "" if al.lower() in ("none", "") else al

        all_names = list(dict.fromkeys(list(prn_states.keys()) + list(uris.keys())))
        printers: list[dict] = []
        for pname in all_names:
            en, pst = prn_states.get(pname, (True, "idle"))
            uri = uris.get(pname, "")
            vm = verbose_meta.get(pname, {})
            lpo = _get_lpoptions_summary_cached(pname)
            printers.append({
                "name": pname,
                "is_default": (pname == default_prn),
                "state": pst,
                "enabled": en,
                "accepting": accepting_map.get(pname, True),
                "uri": uri,
                "proto": _classify_uri(uri),
                "description": vm.get("description") or lpo["description"],
                "location": vm.get("location") or lpo["location"],
                "make_model": lpo["make_model"],
                "shared": lpo["shared"],
                "error_policy": lpo["error_policy"],
                "alerts": vm.get("alerts", ""),
                "jobs_count": job_counts.get(pname, 0),
                "classes": prn_classes.get(pname, []),
            })

        has_pdf = any(p["proto"] == "PDF" or p["uri"].startswith("cups-pdf:") for p in printers)
        return {"default": default_prn, "printers": printers, "classes": classes, "has_pdf_queue": has_pdf}

    return _cached("snapshot", 1.8, _load)


def get_printer_classes() -> list[dict]:
    return list(get_printers_snapshot()["classes"])


def _write_bar_cache(bar_mode: str, sock_exists: bool, res: dict) -> None:
    try:
        path = _bar_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "mode": bar_mode, "sock_exists": sock_exists, "summary": res}, f)
    except OSError:
        pass


def get_printers_bar_summary(include_jobs: bool = False) -> dict:
    """Riepilogo rapido per l'indicatore Waybar a comparsa automatica `custom/printers`."""
    bar_mode = str(STATE.get("waybar", "printers_bar_mode", "auto") or "auto").lower()
    if bar_mode not in ("auto", "always"):
        bar_mode = "auto"

    sock_exists = os.path.exists("/run/cups/cups.sock")
    cache_path = _bar_cache_path()
    if not include_jobs and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if (
                isinstance(cached, dict)
                and (time.time() - float(cached.get("ts", 0.0))) < 4.0
                and cached.get("mode") == bar_mode
                and cached.get("sock_exists") == sock_exists
                and "summary" in cached
            ):
                return cached["summary"]
        except Exception:
            pass

    if not include_jobs:
        if not sock_exists:
            scheduler_up = False
        else:
            lpstat_bin = _cups_bin("lpstat")
            sched_ok, sched_out, _ = _run_c([lpstat_bin, "-r"], timeout=0.6) if lpstat_bin else (False, "", "")
            scheduler_up = bool(sched_ok and "is running" in sched_out.lower())
        daemon = {"scheduler_up": scheduler_up}
    else:
        daemon = get_cups_daemon_state()

    empty_snap = {"default": "", "printers": [], "classes": [], "has_pdf_queue": False}
    if not daemon["scheduler_up"]:
        is_vis = (bar_mode == "always")
        res = {
            "visible": is_vis,
            "mode": bar_mode,
            "css": "offline" if is_vis else "module-nc-empty",
            "count_txt": "OFF" if is_vis else "",
            "jobs": [],
            "snap": empty_snap,
            "daemon": daemon,
            "alerts": [],
            "held_count": 0,
            "total_jobs": 0,
        }
        if not include_jobs:
            _write_bar_cache(bar_mode, sock_exists, res)
        return res

    snap = get_printers_snapshot()
    printers = snap["printers"]
    total_jobs = sum(int(p.get("jobs_count", 0)) for p in printers)
    jobs = get_print_jobs("not-completed") if (include_jobs or total_jobs > 0) else []
    if jobs:
        total_jobs = max(total_jobs, len(jobs))

    alerts = [(p["name"], p["alerts"]) for p in printers if p.get("alerts")]
    paused_with_jobs = [p for p in printers if not p["enabled"] and int(p.get("jobs_count", 0)) > 0]
    held_count = sum(1 for j in jobs if j["state"] == "held")

    crit_tokens = ("error", "jam", "empty", "cover-open", "door-open", "offline", "stopped", "shutting-down")
    has_crit = any(any(tok in al.lower() for tok in crit_tokens) for _, al in alerts) or any(
        j["state"] in ("aborted", "stopped") for j in jobs
    )
    has_warn = bool(paused_with_jobs or held_count > 0 or alerts)

    if has_crit:
        css, count_txt, is_vis = "crit", (str(total_jobs) if total_jobs > 0 else "!"), True
    elif has_warn:
        css, count_txt, is_vis = "warn", (str(total_jobs) if total_jobs > 0 else "!"), True
    elif total_jobs > 0:
        css, count_txt, is_vis = "printing", str(total_jobs), True
    elif bar_mode == "always":
        css, count_txt, is_vis = "", "", True
    else:
        css, count_txt, is_vis = "module-nc-empty", "", False

    res = {
        "visible": is_vis,
        "mode": bar_mode,
        "css": css,
        "count_txt": count_txt,
        "jobs": jobs,
        "snap": snap,
        "daemon": daemon,
        "alerts": alerts,
        "held_count": held_count,
        "total_jobs": total_jobs,
    }
    if not include_jobs:
        _write_bar_cache(bar_mode, sock_exists, res)
    return res


def _parse_ppd_option_groups(printer_name: str) -> dict[str, tuple[str, str]]:
    ppd_file = f"/etc/cups/ppd/{printer_name}.ppd"
    groups: dict[str, tuple[str, str]] = {}
    if not os.path.isfile(ppd_file):
        return groups
    cur_gid = "General"
    cur_glbl = t("cc_printers.ppd_grp_general")
    try:
        with open(ppd_file, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                s = ln.strip()
                if s.startswith("*OpenGroup:"):
                    spec = s.split(":", 1)[1].strip()
                    if "/" in spec:
                        gid, glbl = spec.split("/", 1)
                        cur_gid = re.sub(r"[^A-Za-z0-9_-]+", "_", gid.strip()) or "General"
                        cur_glbl = glbl.strip() or cur_gid
                    else:
                        cur_gid = re.sub(r"[^A-Za-z0-9_-]+", "_", spec) or "General"
                        cur_glbl = spec or cur_gid
                elif s.startswith("*CloseGroup"):
                    cur_gid = "General"
                    cur_glbl = t("cc_printers.ppd_grp_general")
                elif s.startswith("*OpenUI *"):
                    head = s[len("*OpenUI *") :].split(":", 1)[0]
                    opt_k = head.split("/", 1)[0].strip()
                    if opt_k:
                        groups[opt_k] = (cur_gid, cur_glbl)
    except OSError:
        pass
    return groups


def get_printer_options(printer_name: str) -> dict:
    if not printer_name or not get_cups_daemon_state()["scheduler_up"]:
        return {"ppd": {}, "job": {}, "extra": [], "extra_groups": []}

    def _load() -> dict:
        ppd_opts: dict[str, dict] = {}
        has_lpo = probe.has_bin("lpoptions")
        ok_l, out_l, _ = _run_c(["lpoptions", "-p", printer_name, "-l"], timeout=1.5) if has_lpo else (False, "", "")
        if ok_l and out_l:
            for ln in out_l.splitlines():
                if ":" not in ln:
                    continue
                head, raw_vals = ln.split(":", 1)
                key = head.split("/", 1)[0].strip()
                label = head.split("/", 1)[1].strip() if "/" in head else key
                choices = []
                current = ""
                for tok in raw_vals.strip().split():
                    if tok.startswith("*"):
                        val = tok[1:]
                        current = val
                        choices.append(val)
                    else:
                        choices.append(tok)
                if key and choices:
                    ppd_opts[key] = {"key": key, "label": label, "current": current or choices[0], "choices": choices}

        job_opts: dict[str, str] = {
            "media": "A4",
            "sides": "one-sided",
            "print-color-mode": "color",
            "print-quality": "4",
            "orientation-requested": "3",
            "number-up": "1",
            "print-scaling": "auto",
            "job-sheets": "none",
        }
        ok_j, out_j, _ = _run_c(["lpoptions", "-p", printer_name], timeout=1.0) if has_lpo else (False, "", "")
        if ok_j and out_j:
            try:
                toks = shlex.split(out_j)
            except ValueError:
                toks = out_j.split()
            for tk in toks:
                if "=" in tk:
                    k, v = tk.split("=", 1)
                    job_opts[k] = v.split(",", 1)[0] if k == "job-sheets" else v

        known_keys = {"PageSize", "Duplex", "ColorModel", "cupsPrintQuality", "Resolution", "InputSlot", "MediaType"}
        grp_map = _parse_ppd_option_groups(printer_name)
        def_grp = ("General", t("cc_printers.ppd_grp_general"))
        extra: list[dict] = []
        extra_groups: list[tuple[str, str]] = []
        seen_grps: set[str] = set()
        for k, v in ppd_opts.items():
            if k in known_keys or not v["choices"]:
                continue
            gid, glbl = grp_map.get(k, def_grp)
            item = dict(v)
            item["group"] = gid
            item["group_label"] = glbl
            extra.append(item)
            if gid not in seen_grps:
                seen_grps.add(gid)
                extra_groups.append((gid, glbl))
        return {"ppd": ppd_opts, "job": job_opts, "extra": extra, "extra_groups": extra_groups}

    return _cached(f"opts:{printer_name}", 2.5, _load)


def get_printer_supplies(printer_name: str) -> dict:
    if not printer_name or not get_cups_daemon_state()["scheduler_up"]:
        return {"markers": [], "reasons": [], "commands": []}

    def _load() -> dict:
        markers: list[dict] = []
        reasons: list[str] = []
        commands: list[str] = []

        ppd_file = f"/etc/cups/ppd/{printer_name}.ppd"
        if os.path.isfile(ppd_file):
            try:
                raw_ppd = open(ppd_file, "r", encoding="utf-8", errors="ignore").read(65536)
                m_cmd = re.search(r'^\*cupsCommands:\s*"([^"]+)"', raw_ppd, re.MULTILINE)
                if m_cmd:
                    commands = [c.strip() for c in m_cmd.group(1).split() if c.strip()]
            except OSError:
                pass

        if probe.has_bin("ipptool") and os.path.isfile(_IPP_ATTRS_TEST):
            ok, out, _ = _run_c(
                ["ipptool", "-t", "-T", "1", f"ipp://localhost:631/printers/{printer_name}", _IPP_ATTRS_TEST],
                timeout=1.5,
            )
            if ok and out:
                names, levels, types = [], [], []
                for ln in out.splitlines():
                    s = ln.strip()
                    if s.startswith("marker-names "):
                        names = [x.strip().strip('"') for x in s.split("=", 1)[-1].split(",")]
                    elif s.startswith("marker-levels "):
                        levels = [int(x) for x in re.findall(r"-?\d+", s.split("=", 1)[-1])]
                    elif s.startswith("marker-types "):
                        types = [x.strip() for x in s.split("=", 1)[-1].split(",")]
                    elif s.startswith("printer-state-reasons "):
                        vals = [x.strip() for x in s.split("=", 1)[-1].split(",")]
                        reasons = [r for r in vals if r and r != "none"]
                for idx, nm in enumerate(names):
                    lvl = levels[idx] if idx < len(levels) else -1
                    mtype = types[idx] if idx < len(types) else "toner"
                    if nm:
                        markers.append({"name": nm, "level": lvl, "type": mtype})

        return {"markers": markers, "reasons": reasons, "commands": commands}

    return _cached(f"supplies:{printer_name}", 8.0, _load)


def get_cups_server_settings() -> dict:
    def _load() -> dict:
        cfg = {
            "_share_printers": False,
            "_remote_any": False,
            "_remote_admin": False,
            "_user_cancel_any": False,
            "_debug_logging": False,
            "port": "631",
            "web_interface": True,
        }
        ctl_bin = _cups_bin("cupsctl")
        if get_cups_daemon_state()["scheduler_up"] and ctl_bin:
            ok, out, _ = _run_c([ctl_bin], timeout=1.2)
            if ok and out:
                for ln in out.splitlines():
                    if "=" in ln:
                        k, v = ln.strip().split("=", 1)
                        if k in cfg and isinstance(cfg[k], bool):
                            cfg[k] = v.strip() in ("1", "true", "yes", "on")
                        elif k.lower() == "webinterface":
                            cfg["web_interface"] = v.strip().lower() in ("yes", "1", "true", "on")
                return cfg

        if os.path.isfile("/etc/cups/cupsd.conf"):
            try:
                for ln in open("/etc/cups/cupsd.conf", "r", encoding="utf-8", errors="ignore"):
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split()
                    if len(parts) >= 2:
                        k_l, v_l = parts[0].lower(), parts[1].lower()
                        if k_l == "browsing":
                            cfg["_share_printers"] = v_l in ("on", "yes", "true", "1")
                        elif k_l == "loglevel":
                            cfg["_debug_logging"] = v_l.startswith("debug")
                        elif k_l == "webinterface":
                            cfg["web_interface"] = v_l in ("yes", "on", "true", "1")
                        elif k_l == "listen" and ":" in parts[1]:
                            cfg["port"] = parts[1].rsplit(":", 1)[-1]
            except OSError:
                pass
        return cfg

    return _cached("server_cfg", 2.5, _load)


_DISMISSED_LOG_LINES: set[str] = set()


def _read_raw_cups_lines(raw_max: int = 64) -> list[str]:
    err_log = "/var/log/cups/error_log"
    if os.path.isfile(err_log) and os.access(err_log, os.R_OK):
        try:
            with open(err_log, "r", encoding="utf-8", errors="ignore") as f:
                raw_lines = [ln.strip() for ln in f.readlines()[-raw_max:] if ln.strip()]
            if raw_lines:
                return raw_lines
        except OSError:
            pass
    ok, out, _ = _run_c(["journalctl", "-u", "cups.service", "-n", str(raw_max), "--no-pager", "-o", "short-iso"], timeout=1.0)
    if ok and out and "No entries" not in out:
        return [ln.strip() for ln in out.splitlines() if ln.strip()][-raw_max:]
    return []


def get_cups_logs(max_lines: int = 10) -> list[str]:
    def _load() -> list[str]:
        raw_lines = _read_raw_cups_lines(max(64, max_lines * 4))
        return [ln for ln in raw_lines if ln not in _DISMISSED_LOG_LINES][-max_lines:]

    return _cached("cups_logs", 3.0, _load)


def dismiss_cups_logs_view() -> None:
    hit = _CACHE.get("cups_logs")
    if hit and isinstance(hit[1], list):
        _DISMISSED_LOG_LINES.update(hit[1])
    _DISMISSED_LOG_LINES.update(_read_raw_cups_lines(64))
    _CACHE.pop("cups_logs", None)


def is_install_running() -> str:
    return str(_INSTALL_RUNNING or "")



# =====================================================================
# PRIVILEGED & UNPRIVILEGED EXECUTION HELPER
# =====================================================================

def _needs_elevation(stderr_txt: str, stdout_txt: str) -> bool:
    blob = f"{stderr_txt} {stdout_txt}".lower()
    return any(
        tok in blob
        for tok in (
            "unauthorized",
            "forbidden",
            "permission denied",
            "authentication",
            "access denied",
            "operation not permitted",
            "not allowed",
            "interactive authentication",
        )
    )


def _resolve_exec_cmd(cmd: list[str]) -> list[str] | None:
    if not cmd:
        return None
    exe = cmd[0]
    if os.path.isabs(exe):
        return list(cmd) if (os.path.isfile(exe) and os.access(exe, os.X_OK)) else None
    found = _cups_bin(exe)
    return ([found] + list(cmd[1:])) if found else None


def _exec_admin(cc_panel, cmd: list[str], ok_msg: str, try_direct: bool = True, after_fn=None) -> None:
    _exec_admin_batch(cc_panel, [cmd], ok_msg, try_direct=try_direct, after_fn=after_fn)


def _exec_admin_batch(cc_panel, cmds: list[list[str]], ok_msg: str, try_direct: bool = True, after_fn=None) -> None:
    if not cmds:
        return
    resolved: list[list[str]] = []
    for c in cmds:
        rc = _resolve_exec_cmd(c)
        if not rc:
            _set_fb(False, t("cc_printers.err_missing_bin", p0=os.path.basename(c[0]) if c else "?"))
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return
        resolved.append(rc)

    remaining = resolved
    while remaining:
        cmd = remaining[0]
        if try_direct:
            ok, out, err = _run_c(cmd, timeout=5.0)
            if ok:
                remaining.pop(0)
                continue
            if not _needs_elevation(err, out):
                _set_fb(False, (err or out or t("cc_printers.err_cmd_failed"))[:110])
                if cc_panel and cc_panel.shell:
                    cc_panel.shell.refresh_view()
                return
        ok_s, out_s, err_s = _run_c(["sudo", "-n"] + list(cmd), timeout=5.0)
        if ok_s:
            remaining.pop(0)
            continue
        break

    if not remaining:
        invalidate_printers_cache()
        if callable(after_fn):
            after_fn()
        _set_fb(True, ok_msg)
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return

    _PENDING_SUDO.clear()
    _PENDING_SUDO["cmds"] = remaining
    _PENDING_SUDO["ok_msg"] = ok_msg
    _PENDING_SUDO["after_fn"] = after_fn
    cc_panel.request_prompt(
        "cc_prn_sudo",
        t("cc_printers.prompt_sudo_pwd", p0=" ".join(remaining[0][:3])),
        "",
        secret=True,
        title=t("cc_printers.prompt_sudo_title"),
        required=True,
    )


def _build_diagnostic_ps(printer_name: str) -> bytes:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_p = printer_name.replace("(", "[").replace(")", "]")
    ps = f"""%!PS-Adobe-3.0
%%Title: NC-UI CUPS Calibration Test Page
%%Pages: 1
%%BoundingBox: 24 24 571 818
%%EndComments
%%Page: 1 1
gsave
0.8 setlinewidth 0.25 setgray
36 36 523 770 rectstroke
/Courier-Bold findfont 16 scalefont setfont
0.05 setgray
54 772 moveto (NC-UI // CUPS DIAGNOSTIC & CALIBRATION PAGE) show
/Courier findfont 11 scalefont setfont
54 748 moveto (Queue Name : {safe_p}) show
54 732 moveto (Timestamp  : {ts}) show
54 716 moveto (Host       : {socket.gethostname()}) show
0.5 setlinewidth 54 702 moveto 540 702 lineto stroke
/Courier-Bold findfont 11 scalefont setfont
54 680 moveto (1. Grayscale Step Wedge [0% - 100%]) show
0 1 9 {{
  dup /i exch def
  1.0 i 9.0 div sub setgray
  54 i 48 mul add 640 44 26 rectfill
  0 setgray 0.4 setlinewidth
  54 i 48 mul add 640 44 26 rectstroke
}} for
0 setgray
54 612 moveto (2. CMYK Channel Registration Bars) show
1 0 0 0 setcmykcolor 54 576 115 22 rectfill
0 1 0 0 setcmykcolor 179 576 115 22 rectfill
0 0 1 0 setcmykcolor 304 576 115 22 rectfill
0 0 0 1 setcmykcolor 429 576 115 22 rectfill
0 setgray
/Courier findfont 9 scalefont setfont
54 540 moveto (Border frame inset: 36 pt [0.5 in] - Verify all 4 edges print without clipping.) show
grestore
showpage
%%EOF
"""
    return ps.encode("ascii", errors="replace")


def print_test_page(cc_panel, printer_name: str) -> None:
    lp_bin = _cups_bin("lp")
    if not lp_bin:
        _set_fb(False, t("cc_printers.err_missing_bin", p0="lp"))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return
    cups_tp = "/usr/share/cups/data/testprint"
    if os.path.isfile(cups_tp):
        ok, out, err = _run_c([lp_bin, "-d", printer_name, "-t", "NC-UI Test Page", cups_tp], timeout=4.0)
    else:
        ok, out, err = _run_c(
            [lp_bin, "-d", printer_name, "-t", "NC-UI Calibration Page"],
            timeout=4.0,
            input_data=_build_diagnostic_ps(printer_name),
        )
    invalidate_printers_cache()
    _set_fb(ok, t("cc_printers.fb_test_page_sent", p0=printer_name) if ok else (err or out or t("cc_printers.err_cmd_failed"))[:110])
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()


def send_cups_maintenance_cmd(cc_panel, printer_name: str, command_line: str, label: str) -> None:
    lp_bin = _cups_bin("lp")
    if not lp_bin:
        _set_fb(False, t("cc_printers.err_missing_bin", p0="lp"))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return
    payload = f"#CUPS-COMMAND\n{command_line}\n".encode("utf-8")
    ok, out, err = _run_c(
        [lp_bin, "-d", printer_name, "-t", f"CUPS {label}", "-o", "document-format=application/vnd.cups-command"],
        timeout=4.0,
        input_data=payload,
    )
    invalidate_printers_cache()
    _set_fb(ok, t("cc_printers.fb_maint_sent", p0=label, p1=printer_name) if ok else (err or out or t("cc_printers.err_cmd_failed"))[:110])
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()


def identify_printer(cc_panel, printer_name: str) -> None:
    test_f = "/usr/share/cups/ipptool/identify-printer.test"
    if not probe.has_bin("ipptool") or not os.path.isfile(test_f):
        _set_fb(False, t("cc_printers.err_identify_unsupported"))
    else:
        ok, out, err = _run_c(["ipptool", "-T", "2", f"ipp://localhost:631/printers/{printer_name}", test_f], timeout=3.0)
        _set_fb(
            ok,
            t("cc_printers.fb_identify_sent", p0=printer_name)
            if ok
            else (err or out or t("cc_printers.err_identify_unsupported"))[:110],
        )
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()


# =====================================================================
# STAGED ADD-PRINTER, DRIVER-SWITCH & QUICK-PRINT COMPOSER STATE
# =====================================================================

def get_add_draft() -> dict:
    d = STATE.get("printers", "add_draft", None)
    if not isinstance(d, dict):
        d = {}
    return {
        "name": str(d.get("name", "") or ""),
        "desc": str(d.get("desc", "") or ""),
        "loc": str(d.get("loc", "") or ""),
        "proto": str(d.get("proto", "socket") or "socket"),
        "tcp_port": int(d.get("tcp_port", 9100) or 9100),
        "host": str(d.get("host", "") or ""),
        "uri": str(d.get("uri", "") or ""),
        "drv_mode": str(d.get("drv_mode", "auto") or "auto"),
        "drv_id": str(d.get("drv_id", "drv:///cupsfilters.drv/pwgrast.ppd") or "drv:///cupsfilters.drv/pwgrast.ppd"),
        "drv_query": str(d.get("drv_query", "") or ""),
        "ppd_path": str(d.get("ppd_path", "") or ""),
        "selected_pkg": str(d.get("selected_pkg", "") or ""),
        "make_default": bool(d.get("make_default", True)),
        "shared": bool(d.get("shared", False)),
    }


def update_add_draft(**kwargs) -> dict:
    cur = get_add_draft()
    cur.update(kwargs)
    STATE.set("printers", "add_draft", cur)
    return cur


def reset_add_draft() -> None:
    STATE.set("printers", "add_draft", {"_cleared": True})


def get_drv_edit_state() -> dict:
    d = STATE.get("printers", "drv_edit", None)
    if not isinstance(d, dict):
        d = {}
    return {
        "printer": str(d.get("printer", "") or ""),
        "mode": str(d.get("mode", "everywhere") or "everywhere"),
        "drv_id": str(d.get("drv_id", "everywhere") or "everywhere"),
        "query": str(d.get("query", "") or ""),
        "ppd_path": str(d.get("ppd_path", "") or ""),
    }


def update_drv_edit_state(**kwargs) -> dict:
    cur = get_drv_edit_state()
    cur.update(kwargs)
    STATE.set("printers", "drv_edit", cur)
    return cur


def get_quick_print_draft() -> dict:
    d = STATE.get("printers", "qprint_draft", None)
    if not isinstance(d, dict):
        d = {}
    copies = int(d.get("copies", 1) or 1)
    return {
        "file": str(d.get("file", "") or ""),
        "copies": max(1, min(99, copies)),
        "pages": str(d.get("pages", "") or ""),
        "fit": bool(d.get("fit", False)),
        "sides": str(d.get("sides", "default") or "default"),
        "color": str(d.get("color", "default") or "default"),
    }


def update_quick_print_draft(**kwargs) -> dict:
    cur = get_quick_print_draft()
    cur.update(kwargs)
    cur["copies"] = max(1, min(99, int(cur.get("copies", 1) or 1)))
    STATE.set("printers", "qprint_draft", cur)
    return cur


def submit_quick_print(cc_panel, printer_name: str, file_override: str = "") -> None:
    qp = get_quick_print_draft()
    raw_path = file_override or qp["file"]
    exp = os.path.expanduser(str(raw_path or "").strip())
    if not exp or not os.path.isfile(exp):
        _set_fb(False, t("cc_printers.err_file_not_found", p0=raw_path or t("common.unset")))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return

    lp_bin = _cups_bin("lp")
    if not lp_bin:
        _set_fb(False, t("cc_printers.err_missing_bin", p0="lp"))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return
    cmd = [lp_bin, "-d", printer_name, "-t", os.path.basename(exp)]
    if qp["copies"] > 1:
        cmd += ["-n", str(qp["copies"])]
    if qp["pages"]:
        cmd += ["-P", qp["pages"]]
    if qp["fit"]:
        cmd += ["-o", "fit-to-page"]
    if qp["sides"] != "default":
        cmd += ["-o", f"sides={qp['sides']}"]
    if qp["color"] != "default":
        cmd += ["-o", f"print-color-mode={qp['color']}"]
    cmd.append(exp)

    ok, out, err = _run_c(cmd, timeout=5.0)
    invalidate_printers_cache()
    _set_fb(
        ok,
        t("cc_printers.fb_file_queued_opts", p0=os.path.basename(exp), p1=printer_name, p2=qp["copies"])
        if ok
        else (err or out or t("cc_printers.err_cmd_failed"))[:110],
    )
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()


def get_proto_presets() -> list[tuple[str, str, str]]:
    lbl_map = {"smb": t("cc_printers.proto_smb"), "usb": t("cc_printers.proto_usb")}
    return [(pid, lbl_map.get(pid, lbl), tpl) for pid, lbl, tpl in _PROTO_PRESETS]


def _start_install_flow(
    cc_panel,
    print_test: bool = False,
    sudo_pass: str = "",
    draft_override: dict | None = None,
    smart_override: dict | None = None,
) -> None:
    global _INSTALL_RUNNING
    shell = getattr(cc_panel, "shell", None)
    d = dict(draft_override or get_add_draft())
    if not _QUEUE_NAME_RE.match(d.get("name", "")):
        _set_fb(False, t("cc_printers.err_bad_queue_name"))
        if shell:
            shell.refresh_view()
        return
    if "://" not in d.get("uri", "") or d["uri"] == "usb://":
        _set_fb(False, t("cc_printers.err_bad_uri"))
        if shell:
            shell.refresh_view()
        return
    if d.get("drv_mode") == "ppd":
        v_ok, v_msg = _disc.validate_ppd_file(d.get("ppd_path", ""))
        if not v_ok:
            _set_fb(False, v_msg)
            if shell:
                shell.refresh_view()
            return

    smart = dict(smart_override or _disc.get_smart_probe(d.get("host") or d.get("uri") or ""))
    with _INSTALL_LOCK:
        if _INSTALL_RUNNING:
            return
        _INSTALL_RUNNING = d["name"]

    _set_fb(True, t("cc_printers.fb_installing_printer", p0=d["name"]))
    if shell:
        shell.refresh_view()

    def _work() -> tuple[str, str]:
        def _priv_exec(cmd_args: list[str], timeout: float = 8.0) -> tuple[object, str, str]:
            if not cmd_args[0].endswith("systemctl"):
                ok_d, out_d, err_d = _run_c(cmd_args, timeout=timeout)
                if ok_d:
                    return True, out_d, err_d
                if not _needs_elevation(err_d, out_d):
                    return False, out_d, err_d

            ok_n, out_n, err_n = _run_c(["sudo", "-n"] + list(cmd_args), timeout=timeout)
            if ok_n:
                return True, out_n, err_n

            if sudo_pass:
                ok_s, out_s, err_s = _run_c(
                    ["sudo", "-k", "-S", "-p", ""] + list(cmd_args),
                    timeout=timeout,
                    input_data=f"{sudo_pass}\n".encode("utf-8"),
                )
                return bool(ok_s), out_s, err_s

            return "NEED_AUTH", out_n, err_n

        # 1. Auto-start CUPS scheduler if down
        lpstat_bin = _cups_bin("lpstat")
        lpadmin_bin = _cups_bin("lpadmin")
        if not lpadmin_bin:
            return "ERR", t("cc_printers.err_missing_bin", p0="lpadmin")
        sched_ok, sched_out, _ = _run_c([lpstat_bin, "-r"], timeout=0.8) if lpstat_bin else (False, "", "")
        if not (sched_ok and "is running" in (sched_out or "").lower()):
            sc_bin = _cups_bin("systemctl")
            if not sc_bin:
                return "ERR", t("cc_printers.err_missing_bin", p0="systemctl")
            res_s, out_s, err_s = _priv_exec([sc_bin, "start", "cups.service"], timeout=6.0)
            if res_s == "NEED_AUTH":
                return "NEED_SUDO", "systemctl start cups.service"
            if not res_s:
                return "ERR", (err_s or out_s or t("cc_printers.err_spooler_down"))[:140]
            if lpstat_bin:
                for _ in range(12):
                    time.sleep(0.2)
                    s_ok, s_out, _ = _run_c([lpstat_bin, "-r"], timeout=0.6)
                    if s_ok and "is running" in (s_out or "").lower():
                        break

        # 2. Resolve effective driver & synthesize PPD if TCP/IP + Auto
        strategy, drv_args, _ = _disc.resolve_effective_driver(d, smart)
        host_ipp = d.get("host") or (urlparse(d["uri"]).hostname or "")
        if d.get("drv_mode") == "auto" and d["uri"].startswith(("socket://", "lpd://")) and strategy in ("pcl", "pwgrast", "ps") and host_ipp:
            synth = _disc._synthesize_driverless_ppd(host_ipp, d["name"])
            if synth:
                drv_args = ["-P", synth]

        base = [lpadmin_bin, "-p", d["name"], "-E", "-v", d["uri"]]
        meta: list[str] = []
        if d.get("desc"):
            meta += ["-D", d["desc"]]
        if d.get("loc"):
            meta += ["-L", d["loc"]]
        meta += [
            "-o",
            f"printer-is-shared={'true' if d.get('shared') else 'false'}",
            "-o",
            "printer-error-policy=retry-job",
        ]

        # 3. Provision printer queue with fallback chain on everywhere errors
        ok_inst, out_i, err_i = _priv_exec(base + drv_args + meta, timeout=12.0)
        if ok_inst == "NEED_AUTH":
            return "NEED_SUDO", f"lpadmin -p {d['name']}"
        if not ok_inst and drv_args == ["-m", "everywhere"]:
            synth2 = _disc._synthesize_driverless_ppd(d["uri"], d["name"])
            if synth2:
                ok_inst, out_i, err_i = _priv_exec(base + ["-P", synth2] + meta, timeout=10.0)
            if not ok_inst:
                fb_m = "drv:///cupsfilters.drv/pwgrast.ppd" if os.path.isfile("/usr/share/cups/drv/cupsfilters.drv") else "drv:///sample.drv/generpcl.ppd"
                ok_inst, out_i, err_i = _priv_exec(base + ["-m", fb_m] + meta, timeout=10.0)

        if not ok_inst:
            return "ERR", (err_i or out_i or t("cc_printers.err_cmd_failed"))[:140]

        # 4. Force enable, accept jobs & apply system default
        if _cups_bin("cupsenable"):
            _priv_exec([_cups_bin("cupsenable"), d["name"]], timeout=4.0)
        if _cups_bin("cupsaccept"):
            _priv_exec([_cups_bin("cupsaccept"), d["name"]], timeout=4.0)
        if d.get("make_default"):
            if probe.has_bin("lpoptions"):
                _run_c(["lpoptions", "-d", d["name"]], timeout=1.5)
            _priv_exec([lpadmin_bin, "-d", d["name"]], timeout=3.0)

        # 5. Verify queue registration on CUPS
        if lpstat_bin:
            ok_v, out_v, err_v = _run_c([lpstat_bin, "-p", d["name"]], timeout=2.0)
            if not ok_v:
                return "ERR", (err_v or out_v or t("cc_printers.err_cmd_failed"))[:140]

        # 6. Optional calibration test page
        if print_test:
            lp_bin = _cups_bin("lp")
            ok_lp = False
            if lp_bin:
                ok_lp, _o, _e = _run_c(
                    [lp_bin, "-d", d["name"], "-t", "NC-UI Calibration Page"],
                    timeout=4.0,
                    input_data=_build_diagnostic_ps(d["name"]),
                )
            return ("OK_TEST" if ok_lp else "OK"), d["name"]
        return "OK", d["name"]

    def _on_done(outcome: tuple[str, str]) -> bool:
        global _INSTALL_RUNNING
        with _INSTALL_LOCK:
            _INSTALL_RUNNING = ""
        kind, detail = outcome
        if kind == "NEED_SUDO":
            _PENDING_INSTALL_SUDO.clear()
            _PENDING_INSTALL_SUDO.update({"d": d, "smart": smart, "print_test": print_test})
            _set_fb(True, t("cc_printers.prompt_sudo_pwd", p0=detail))
            cc_panel.request_prompt(
                "cc_prn_sudo_install",
                t("cc_printers.prompt_sudo_pwd", p0=detail),
                "",
                secret=True,
                title=t("cc_printers.prompt_sudo_title"),
                required=True,
            )
            if shell:
                shell.refresh_view()
            return False

        invalidate_printers_cache()
        if kind in ("OK", "OK_TEST"):
            cc_panel.set_state("prn_wiz_target", "")
            cc_panel.set_state("prn_selected", detail)
            _set_fb(
                True,
                t("cc_printers.fb_prn_installed_tested" if kind == "OK_TEST" else "cc_printers.fb_prn_installed_verified", p0=detail),
            )
        else:
            _set_fb(False, detail)
        if shell:
            shell.refresh_view()
        return False

    probe.run_async(_work, _on_done)


# =====================================================================
# SHARED UI HELPERS & LINK / PROMPT DISPATCHERS
# =====================================================================

def render_scalar_kv(
    bx: Box,
    canvas,
    label: str,
    entry_id: str,
    trigger_uri: str,
    current_val: str,
    placeholder: str,
    empty_btn_lbl: str = "",
    width: int = 40,
    extra_right: str = "",
) -> None:
    if get_active_inline_entry() == entry_id:
        fld = InlineField(entry_id, trigger_uri, value=current_val, placeholder=placeholder, width=width)
        bx.add_kv(label, fld, right_markup=extra_right)
        return
    val_m = (
        Txt(canvas.truncate_plain(current_val, 44 if extra_right else 50), role="val")
        if current_val
        else Txt(placeholder if empty_btn_lbl else t("common.unset"), role="inactive")
    )
    btn_lbl = t("cc_printers.edit_dots_btn") if (current_val or not empty_btn_lbl) else empty_btn_lbl
    r_m = " ".join(x for x in (ActionBtn(btn_lbl, trigger_uri, tone="neutral"), extra_right) if x)
    bx.add_kv(label, val_m, right_markup=r_m)


def add_ppd_picker_kv(bx: Box, ppd_path: str, browse_uri: str) -> None:
    ppd_btn = ActionBtn(t("cc_printers.browse_ppd_btn"), browse_uri, tone="neutral")
    if ppd_path:
        v_ok, _ = _disc.validate_ppd_file(ppd_path)
        st_b = StateBadge(True, t("cc_printers.ppd_valid_badge")) if v_ok else Badge(t("cc_printers.ppd_invalid_badge"), style="crit")
        bx.add_kv(t("cc_printers.kv_ppd_file"), f"{Txt(os.path.basename(ppd_path), role='val')}  {st_b}", right_markup=ppd_btn)
    else:
        bx.add_kv(t("cc_printers.kv_ppd_file"), Txt(t("common.unset"), role="inactive"), right_markup=ppd_btn)



# --- Re-exports for backward compatibility with cc_printers_* and wp_printers ---
import panels.cc_engine_printers_discovery as _disc
from panels.cc_engine_printers_discovery import (  # noqa: F401,E402
    _AUTO_DRV_LOADING,
    _AUTO_DRV_LOCK,
    _AUTO_DRV_PKG_CACHE,
    _BRAND_NORM,
    _DISC_LOCK,
    _DRV_LOADING,
    _DRV_LOCK,
    _PRINTER_BRANDS,
    _SMART_PROBE_CACHE,
    _TCP_SCAN_LOCK,
    _TCP_SCAN_RUNNING,
    _check_installed_pkgs,
    _describe_driver_reason,
    _enrich_tcp_printer,
    _load_drivers_blocking,
    _local_ipv4_info,
    _match_vendor_driver,
    _normalize_tcp_device,
    _parse_pkg_search_lines,
    _parse_printer_signature,
    _probe_tcp_printer_host,
    _sanitized_queue_name,
    _score_driver_candidate,
    _search_pkg_drivers_blocking,
    _synthesize_driverless_ppd,
    _watch_pkg_install_async,
    apply_disc_device_to_draft,
    apply_tcp_device_to_draft,
    build_tcp_uri,
    get_auto_found_drivers,
    get_cups_drivers,
    get_discovered_devices,
    get_local_ipv4_subnets,
    get_smart_probe,
    get_tcp_scan_state,
    is_discovery_running,
    is_drivers_loading,
    is_tcp_scan_running,
    parse_tcp_scan_targets,
    probe_printer_host,
    resolve_effective_driver,
    search_cups_drivers,
    trigger_auto_driver_search_async,
    trigger_device_discovery,
    trigger_smart_probe_async,
    trigger_tcp_lan_scan,
    validate_ppd_file,
    warm_cups_drivers_async,
)
from panels.cc_engine_printers_actions import (  # noqa: F401,E402
    handle_cc_printers_link,
    handle_cc_printers_prompt,
)
