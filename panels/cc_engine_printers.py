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

from core import probe
from core.completion import rank_candidates
from core.i18n import t
from core.privileges import execute_privileged, get_privileged_action, request_privilege_modal
from core.state import STATE
from ui.kit import ActionBtn, Box, FeedbackBox, StateBadge, Txt, start_inline_input

_CACHE: dict[str, tuple[float, object]] = {}
_LPOPT_META_CACHE: dict[str, tuple[float, dict]] = {}
_SMART_PROBE_CACHE: dict[str, dict] = {}
_AUTO_DRV_PKG_CACHE: dict[str, tuple[float, list[dict]]] = {}
_AUTO_DRV_LOADING: set[str] = set()
_AUTO_DRV_LOCK = threading.Lock()
_PENDING_SUDO: dict[str, object] = {}
_PENDING_INSTALL_SUDO: dict[str, object] = {}
_DISC_LOCK = False
_TCP_SCAN_LOCK = threading.Lock()
_TCP_SCAN_RUNNING = False
_DRV_LOCK = threading.Lock()
_DRV_LOADING = False
_INSTALL_LOCK = threading.Lock()
_INSTALL_RUNNING = ""

_PRINTABLE_EXTS = (".pdf", ".ps", ".eps", ".txt", ".png", ".jpg", ".jpeg", ".svg")
_PPD_EXTS = (".ppd", ".ppd.gz")
_QUEUE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PAGE_RANGE_RE = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")
_IPP_JOBS_TEST_PATH = "/tmp/.ncui_ipp_jobs.test"

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


def _set_fb(ok: bool, msg: str) -> None:
    STATE.set("printers", "fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def feedback_cards() -> list:
    fb = STATE.get("printers", "fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 16.0:
        return [FeedbackBox(t("cc_printers.status_title"), bool(fb["ok"]), str(fb["msg"]))]
    return []


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
    ok, out, _ = _run_c(["systemctl", "is-enabled", unit], timeout=0.6)
    return bool(ok and out.strip() in ("enabled", "static", "indirect", "alias"))


def _unit_active(unit: str) -> bool:
    ok, out, _ = _run_c(["systemctl", "is-active", unit], timeout=0.6)
    return bool(ok and out.strip() == "active")


def get_cups_daemon_state() -> dict:
    def _load() -> dict:
        sock_exists = os.path.exists("/run/cups/cups.sock")
        sched_ok, sched_out, _ = _run_c(["lpstat", "-r"], timeout=0.7) if probe.has_bin("lpstat") else (False, "", "")
        scheduler_up = bool(sched_ok and "is running" in sched_out.lower())

        _ok_s, out_s, _ = _run_c(["systemctl", "is-active", "cups.service", "cups.socket", "avahi-daemon.service"], timeout=0.6)
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
            "cups_installed": probe.has_bin("lpstat") and probe.has_bin("lpadmin"),
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
                "cups": probe.has_bin("lpadmin"),
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
    sp_box = Box(
        t("cc_printers.spooler_offline_title"),
        icon="\uf02f",
        actions=[
            StateBadge(False, t("common.active"), t("cc_printers.stopped")),
            ActionBtn(t("cc_printers.start_cups"), "cc_prn:svc:start:cups.service", tone="apply"),
            ActionBtn(t("cc_printers.server_tab_btn"), "cc_prn:goto_subtab:server", tone="neutral"),
        ],
        highlighted=False,
    )
    sp_box.add_row(" ")
    sp_box.add_kv(
        t("cc_printers.cups_package"),
        StateBadge(daemon["cups_installed"], t("cc_printers.installed"), t("cc_printers.missing_bin")),
    )
    sp_box.add_kv(
        t("cc_printers.boot_autostart"),
        StateBadge(daemon["service_enabled"], t("common.enabled"), t("common.disabled")),
    )
    sp_box.add_kv(
        t("cc_printers.kv_avahi_mdns"),
        StateBadge(daemon["avahi_active"], t("common.active"), t("cc_printers.stopped")),
    )
    sp_box.add_row(f"  {Txt(t('cc_printers.spooler_offline_hint'), role='attention')}")
    sp_box.add_row(" ")
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
    if not os.path.isfile(_IPP_JOBS_TEST_PATH):
        try:
            with open(_IPP_JOBS_TEST_PATH, "w", encoding="utf-8") as f:
                f.write(_IPP_JOBS_TEST_SPEC)
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


def get_printers_bar_summary(include_jobs: bool = False) -> dict:
    """Riepilogo rapido per l'indicatore Waybar a comparsa automatica `custom/printers`."""
    bar_mode = str(STATE.get("waybar", "printers_bar_mode", "auto") or "auto").lower()
    if bar_mode not in ("auto", "always"):
        bar_mode = "auto"

    daemon = get_cups_daemon_state()
    empty_snap = {"default": "", "printers": [], "classes": [], "has_pdf_queue": False}
    if not daemon["scheduler_up"]:
        is_vis = (bar_mode == "always")
        return {
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

    return {
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


def get_printer_options(printer_name: str) -> dict:
    if not printer_name or not get_cups_daemon_state()["scheduler_up"]:
        return {"ppd": {}, "job": {}, "extra": []}

    def _load() -> dict:
        ppd_opts: dict[str, dict] = {}
        ok_l, out_l, _ = _run_c(["lpoptions", "-p", printer_name, "-l"], timeout=1.5)
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
        ok_j, out_j, _ = _run_c(["lpoptions", "-p", printer_name], timeout=1.0)
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
        extra = [v for k, v in ppd_opts.items() if k not in known_keys and 2 <= len(v["choices"]) <= 8][:8]
        return {"ppd": ppd_opts, "job": job_opts, "extra": extra}

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

        test_file = "/usr/share/cups/ipptool/get-printer-attributes.test"
        if probe.has_bin("ipptool") and os.path.isfile(test_file):
            ok, out, _ = _run_c(
                ["ipptool", "-t", "-T", "1", f"ipp://localhost:631/printers/{printer_name}", test_file],
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
        if get_cups_daemon_state()["scheduler_up"] and probe.has_bin("cupsctl"):
            ok, out, _ = _run_c(["cupsctl"], timeout=1.2)
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


# =====================================================================
# DISCOVERY, DRIVER CATALOG & TCP PORT PROBE
# =====================================================================

def get_discovered_devices() -> list[dict]:
    res = STATE.get("printers", "discovered", [])
    return res if isinstance(res, list) else []


def is_discovery_running() -> bool:
    return bool(_DISC_LOCK)


def trigger_device_discovery(cc_panel) -> None:
    global _DISC_LOCK
    if _DISC_LOCK:
        return
    _DISC_LOCK = True
    _set_fb(True, t("cc_printers.fb_scanning"))

    def _scan() -> list[dict]:
        seen: set[str] = set()
        items: list[dict] = []
        avahi_up = get_cups_daemon_state()["avahi_active"]

        def _add(uri: str, label: str, source: str) -> None:
            u = uri.strip()
            if not u or u in seen or "://" not in u:
                return
            if u.endswith("://") and u != "usb://":
                return
            seen.add(u)
            items.append({
                "uri": u,
                "proto": _classify_uri(u),
                "label": label.strip() or u,
                "source": source,
            })

        if avahi_up and probe.has_bin("driverless"):
            ok, out, _ = _run_c(["driverless"], timeout=3.5)
            if ok and out:
                for ln in out.splitlines():
                    u = ln.strip()
                    if "://" in u:
                        _add(u, u.split("/")[-1] or "IPP Everywhere Printer", "driverless")

        if get_cups_daemon_state()["scheduler_up"] and probe.has_bin("lpinfo"):
            cmd_v = ["lpinfo", "-v"] if avahi_up else ["lpinfo", "--exclude-schemes", "dnssd,driverless", "-v"]
            ok, out, _ = _run_c(cmd_v, timeout=4.0)
            if ok and out:
                for ln in out.splitlines():
                    parts = ln.strip().split(None, 1)
                    if len(parts) == 2 and "://" in parts[1]:
                        cls_kind, u = parts
                        if u in ("ipp", "ipps", "http", "https", "socket", "lpd", "smb", "beh"):
                            continue
                        _add(u, f"{cls_kind.upper()} · {u.split('://', 1)[-1][:42]}", "lpinfo")

        if avahi_up and probe.has_bin("avahi-browse"):
            for svc in ("_ipp._tcp", "_ipps._tcp", "_pdl-datastream._tcp"):
                ok, out, _ = _run_c(["avahi-browse", "-rtpk", svc], timeout=2.5)
                if not ok or not out:
                    continue
                for ln in out.splitlines():
                    if not ln.startswith("="):
                        continue
                    cols = ln.split(";")
                    if len(cols) < 9:
                        continue
                    name = cols[3].replace("\\032", " ").replace("\\064", "@")
                    host = cols[7].strip()
                    port = cols[8].strip() or "631"
                    txt_blob = cols[9] if len(cols) > 9 else ""
                    rp_m = re.search(r'"rp=([^"]+)"', txt_blob)
                    rp = rp_m.group(1).lstrip("/") if rp_m else "ipp/print"
                    if svc == "_pdl-datastream._tcp":
                        u = f"socket://{host}:{port}"
                    elif svc == "_ipps._tcp":
                        u = f"ipps://{host}:{port}/{rp}"
                    else:
                        u = f"ipp://{host}:{port}/{rp}"
                    _add(u, f"{name} ({host})", "mDNS")

        return items

    def _done(found: list[dict]) -> bool:
        global _DISC_LOCK
        _DISC_LOCK = False
        STATE.set("printers", "discovered", found)
        _set_fb(True, t("cc_printers.fb_scan_done", p0=len(found)))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return False

    probe.run_async(_scan, _done)


def _local_ipv4_info() -> tuple[list[str], set[str]]:
    """Rileva le subnet IPv4 locali (/24) dalle interfacce UP global (cached 15s)."""
    def _load() -> tuple[list[str], set[str]]:
        primary: list[str] = []
        secondary: list[str] = []
        local_ips: set[str] = set()
        ok, out, _ = _run_c(["ip", "-j", "-4", "addr", "show", "up", "scope", "global"], timeout=1.5)
        if ok and out:
            try:
                ifaces = json.loads(out)
            except Exception:
                ifaces = []
            skip_pfx = ("lo", "docker", "virbr", "veth", "br-", "tun", "tap", "wg", "tailscale")
            for iface in ifaces:
                ifname = str(iface.get("ifname", "") or "")
                is_virt = ifname.startswith(skip_pfx)
                for ai in iface.get("addr_info", ()):
                    if ai.get("family") != "inet" or not ai.get("local"):
                        continue
                    ip_s = str(ai["local"])
                    local_ips.add(ip_s)
                    try:
                        pfx = min(32, max(24, int(ai.get("prefixlen", 24))))
                        net_s = str(ipaddress.ip_interface(f"{ip_s}/{pfx}").network)
                    except ValueError:
                        continue
                    dst = secondary if is_virt else primary
                    if net_s not in primary and net_s not in secondary:
                        dst.append(net_s)
        return (primary or secondary or ["10.10.0.0/24"]), local_ips

    return _cached("local_ipv4_info", 15.0, _load)


def get_local_ipv4_subnets() -> list[str]:
    return _local_ipv4_info()[0]


def parse_tcp_scan_targets(spec: str) -> tuple[bool, str, list[str]]:
    """Valida e converte CIDR (/24../32), range (A.B.C.1-254) o singolo IPv4."""
    s = str(spec or "").strip()
    if not s:
        return False, "", []
    try:
        if "/" in s:
            net = ipaddress.ip_network(s, strict=False)
            if net.version != 4 or net.prefixlen < 24:
                return False, "", []
            ips = [str(ip) for ip in net.hosts()] or [str(net.network_address)]
            return True, net.with_prefixlen, ips
        if "-" in s:
            left, _, right = s.partition("-")
            start_ip = ipaddress.IPv4Address(left.strip())
            r_s = right.strip()
            if r_s.isdigit():
                end_oct = int(r_s)
                if not 0 <= end_oct <= 255:
                    return False, "", []
                end_int = (int(start_ip) & 0xFFFFFF00) | end_oct
            else:
                end_int = int(ipaddress.IPv4Address(r_s))
            start_int = int(start_ip)
            if end_int < start_int or (end_int - start_int + 1) > 256:
                return False, "", []
            ips = [str(ipaddress.IPv4Address(x)) for x in range(start_int, end_int + 1)]
            return True, f"{start_ip}-{r_s}", ips
        ip = ipaddress.IPv4Address(s)
        return True, str(ip), [str(ip)]
    except ValueError:
        return False, "", []


def build_tcp_uri(host: str, tcp_port: int = 9100) -> str:
    h = str(host or "").strip()
    if not h:
        return ""
    p = int(tcp_port or 9100)
    if p == 631:
        return f"ipp://{h}:631/ipp/print"
    if p == 515:
        return f"lpd://{h}/BINARY_P1"
    return f"socket://{h}:9100"


def _normalize_tcp_device(dv: dict) -> dict:
    raw_ports = [int(p) for p in dv.get("open_ports", ()) if int(p) in (9100, 631, 515)]
    open_ports = [p for p in (9100, 631, 515) if p in raw_ports] or [9100]
    pref_port = int(dv.get("tcp_port") or 0)
    best_port = pref_port if pref_port in open_ports else open_ports[0]
    ip = str(dv.get("ip", "") or "").strip()
    label = str(dv.get("label", "") or f"Network Printer ({ip})").strip()
    return {
        "ip": ip,
        "ports": " ".join(f":{p}" for p in open_ports),
        "open_ports": open_ports,
        "tcp_port": best_port,
        "uri": build_tcp_uri(ip, best_port),
        "proto": f"TCP/IP :{best_port}",
        "proto_key": "socket",
        "drv_mode": "auto",
        "label": label,
        "source": "TCP/IP LAN",
    }


def _sanitized_queue_name(label: str, fallback: str = "Printer") -> str:
    raw_lbl = re.sub(r"[^A-Za-z0-9_-]+", "_", str(label or "").split("(")[0].strip()).strip("_")[:32]
    if not raw_lbl or raw_lbl.lower().startswith("network_printer"):
        raw_lbl = re.sub(r"[^A-Za-z0-9_-]+", "_", fallback).strip("_")[:32] or "Printer"
    return raw_lbl


def apply_tcp_device_to_draft(dv: dict, force_port: int = 0) -> dict:
    nd = _normalize_tcp_device(dv)
    port = force_port if force_port in (9100, 631, 515) else int(nd["tcp_port"])
    ip = nd["ip"]
    return update_add_draft(
        uri=build_tcp_uri(ip, port),
        host=ip,
        proto="socket",
        tcp_port=port,
        name=_sanitized_queue_name(nd["label"], f"Printer_{ip.replace('.', '_')}"),
        desc=nd["label"][:48],
        drv_mode="auto",
    )


def apply_disc_device_to_draft(dv: dict) -> dict:
    uri = str(dv.get("uri", "") or "")
    parsed = urlparse(uri) if "://" in uri else None
    host_s = (parsed.hostname or "") if parsed else ""
    p_map = {"IPP": "ipp", "IPPS": "ipps", "TCP:9100": "socket", "RAW:9100": "socket", "LPD": "lpd", "SMB": "smb", "USB": "usb"}
    proto_id = p_map.get(dv.get("proto", ""), "ipp")
    tcp_p = int(parsed.port or (9100 if proto_id == "socket" else (515 if proto_id == "lpd" else 631))) if parsed else 9100
    return update_add_draft(
        uri=uri,
        host=host_s,
        proto=proto_id,
        tcp_port=tcp_p if tcp_p in (9100, 631, 515) else 9100,
        name=_sanitized_queue_name(dv.get("label", ""), f"Printer_{host_s.replace('.', '_')}" if host_s else "USB_Printer"),
        desc=str(dv.get("label", "") or "")[:48],
        drv_mode="auto",
    )


def get_tcp_scan_state() -> dict:
    subnets, _ = _local_ipv4_info()
    cur = str(STATE.get("printers", "tcp_scan_subnet", "") or "").strip()
    if not cur:
        cur = subnets[0]
        STATE.set("printers", "tcp_scan_subnet", cur)
    raw_devs = STATE.get("printers", "tcp_discovered", [])
    devs = [_normalize_tcp_device(x) for x in raw_devs if isinstance(x, dict) and x.get("ip")] if isinstance(raw_devs, list) else []
    return {
        "subnets": subnets,
        "active_subnet": cur,
        "is_custom": cur not in subnets,
        "scanning": bool(_TCP_SCAN_RUNNING),
        "devices": devs,
        "scanned_once": bool(STATE.get("printers", "tcp_scanned_once", False)),
    }


def is_tcp_scan_running() -> bool:
    return bool(_TCP_SCAN_RUNNING)


def _enrich_tcp_printer(ip: str, open_ports: list[int]) -> str:
    """Identifica marca/modello tramite ipptool (:631), snmpget, PJL (:9100) o rDNS."""
    test_spec = "/usr/share/cups/ipptool/get-printer-attributes.test"
    if 631 in open_ports and probe.has_bin("ipptool") and os.path.isfile(test_spec):
        ok, out, _ = _run_c(["ipptool", "-T", "1", "-t", f"ipp://{ip}:631/ipp/print", test_spec], timeout=1.3)
        if ok and out:
            for key in ("printer-make-and-model", "printer-info", "printer-name"):
                m = re.search(rf"{key}\s*\([^)]+\)\s*=\s*(.+)", out)
                if m and m.group(1).strip():
                    return m.group(1).strip().strip('"')[:44]

    if probe.has_bin("snmpget"):
        ok, out, _ = _run_c(
            ["snmpget", "-v1", "-c", "public", "-t", "1", "-r", "0", "-Oqv", ip, "1.3.6.1.2.1.25.3.2.1.3.1", "1.3.6.1.2.1.1.1.0"],
            timeout=1.3,
        )
        if ok and out:
            for ln in out.splitlines():
                clean = ln.strip().strip('"')
                if clean and "No Such" not in clean and "Timeout" not in clean:
                    return clean[:44]

    if 9100 in open_ports:
        try:
            with socket.create_connection((ip, 9100), timeout=0.35) as sock:
                sock.settimeout(0.35)
                sock.sendall(b"\x1b%-12345X@PJL INFO ID\r\n\x1b%-12345X")
                raw = sock.recv(512).decode("utf-8", errors="ignore")
                m = re.search(r'@PJL\s+INFO\s+ID\s*[\r\n]+"?([^"\r\n]+)"?', raw)
                if m and m.group(1).strip():
                    return m.group(1).strip()[:44]
        except OSError:
            pass

    try:
        host_n, _, _ = socket.gethostbyaddr(ip)
        if host_n and host_n != ip:
            return host_n.split(".", 1)[0][:44]
    except OSError:
        pass
    return f"Network Printer ({ip})"


def _probe_tcp_printer_host(ip: str, timeout: float = 0.32) -> dict | None:
    found: set[int] = set()
    for port in (9100, 631, 515):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                found.add(port)
        except OSError:
            pass
    if not found:
        return None
    if len(found) < 3:
        for port in (9100, 631, 515):
            if port in found:
                continue
            try:
                with socket.create_connection((ip, port), timeout=0.85):
                    found.add(port)
            except OSError:
                pass
    open_ports = [p for p in (9100, 631, 515) if p in found]
    return _normalize_tcp_device({
        "ip": ip,
        "open_ports": open_ports,
        "tcp_port": open_ports[0],
        "label": _enrich_tcp_printer(ip, open_ports),
    })


def trigger_tcp_lan_scan(cc_panel, subnet_spec: str = "") -> None:
    global _TCP_SCAN_RUNNING
    spec = (subnet_spec or get_tcp_scan_state()["active_subnet"]).strip()
    ok, norm_spec, targets = parse_tcp_scan_targets(spec)
    if not ok or not targets:
        _set_fb(False, t("cc_printers.err_bad_subnet"))
        return

    with _TCP_SCAN_LOCK:
        if _TCP_SCAN_RUNNING:
            return
        _TCP_SCAN_RUNNING = True

    STATE.set("printers", "tcp_scan_subnet", norm_spec)
    _set_fb(True, t("cc_printers.fb_tcp_scanning", p0=norm_spec))
    _, local_ips = _local_ipv4_info()
    scan_ips = targets if len(targets) == 1 else [ip for ip in targets if ip not in local_ips]

    def _scan() -> list[dict]:
        found: list[dict] = []
        workers = min(64, max(1, len(scan_ips)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_probe_tcp_printer_host, ip) for ip in scan_ips]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    res = fut.result()
                    if res:
                        found.append(res)
                except Exception:
                    continue
        found.sort(key=lambda d: int(ipaddress.IPv4Address(d["ip"])))
        return found

    def _done(found: list[dict]) -> bool:
        global _TCP_SCAN_RUNNING
        with _TCP_SCAN_LOCK:
            _TCP_SCAN_RUNNING = False
        STATE.set("printers", "tcp_discovered", found)
        STATE.set("printers", "tcp_scanned_once", True)
        _set_fb(True, t("cc_printers.fb_tcp_scan_done", p0=len(found), p1=norm_spec))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return False

    probe.run_async(_scan, _done)


def _load_drivers_blocking() -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for drv_id, lbl in _BUILTIN_DRIVERS:
        seen.add(drv_id)
        out.append((drv_id, lbl))
    ok_sched, out_sched, _ = _run_c(["lpstat", "-r"], timeout=0.7) if probe.has_bin("lpstat") else (False, "", "")
    if ok_sched and "is running" in (out_sched or "").lower() and probe.has_bin("lpinfo"):
        avahi_up = os.path.exists("/run/avahi-daemon/socket")
        cmd_m = ["lpinfo", "-m"] if avahi_up else ["lpinfo", "--exclude-schemes", "dnssd,driverless", "-m"]
        ok, raw, _ = _run_c(cmd_m, timeout=4.0)
        if ok and raw:
            for ln in raw.splitlines():
                parts = ln.strip().split(None, 1)
                if len(parts) == 2 and parts[0] not in seen:
                    seen.add(parts[0])
                    out.append((parts[0], parts[1].strip()))
    return out


def is_drivers_loading() -> bool:
    return bool(_DRV_LOADING)


def warm_cups_drivers_async(cc_panel=None) -> None:
    global _DRV_LOADING
    hit = _CACHE.get("drivers_list")
    if hit is not None and (time.monotonic() - hit[0]) < 120.0:
        return
    with _DRV_LOCK:
        if _DRV_LOADING:
            return
        _DRV_LOADING = True

    def _done(res: list[tuple[str, str]]) -> bool:
        global _DRV_LOADING
        with _DRV_LOCK:
            _DRV_LOADING = False
        _CACHE["drivers_list"] = (time.monotonic(), res)
        if cc_panel and getattr(cc_panel, "shell", None):
            cc_panel.shell.refresh_view()
        return False

    probe.run_async(_load_drivers_blocking, _done)


def get_cups_drivers(cc_panel=None) -> list[tuple[str, str]]:
    hit = _CACHE.get("drivers_list")
    if hit is not None and (time.monotonic() - hit[0]) < 120.0:
        return hit[1]
    warm_cups_drivers_async(cc_panel)
    return list(_BUILTIN_DRIVERS)


def search_cups_drivers(query: str, limit: int = 6, cc_panel=None) -> list[tuple[str, str]]:
    items = [(drv, lbl, f"{drv} {lbl}".lower()) for drv, lbl in get_cups_drivers(cc_panel)]
    return rank_candidates(items, query, limit=limit)


def _match_vendor_driver(model_name: str, drivers: list[tuple[str, str]]) -> tuple[str, str] | None:
    norm_txt = re.sub(r"[^a-z0-9]+", " ", str(model_name or "").lower()).strip()
    if not norm_txt:
        return None
    toks = norm_txt.split()
    model_codes = [tk for tk in toks if len(tk) >= 3 and any(c.isdigit() for c in tk)]
    brands = [b for b in ("brother", "hewlett", "hp", "epson", "canon", "xerox", "kyocera", "samsung", "ricoh", "lexmark", "zebra") if b in toks]
    if not model_codes:
        return None
    best = None
    best_score = 0
    for drv_id, lbl in drivers:
        if drv_id in ("everywhere", "raw") or "sample.drv" in drv_id or "cupsfilters.drv" in drv_id:
            continue
        dl_norm = re.sub(r"[^a-z0-9]+", "", f"{drv_id} {lbl}".lower())
        dl_words = f"{drv_id} {lbl}".lower()
        score = 0
        for code in model_codes:
            if code in dl_norm:
                score += len(code) * 3
        if score == 0:
            continue
        if brands and any(b in dl_words for b in brands):
            score += 10
        if score > best_score:
            best_score = score
            best = (drv_id, lbl)
    return best if best_score >= 9 else None


def is_install_running() -> str:
    return str(_INSTALL_RUNNING or "")


def get_smart_probe(host_or_uri: str) -> dict:
    k = str(host_or_uri or "").strip()
    return dict(_SMART_PROBE_CACHE.get(k, {}))


def _synthesize_driverless_ppd(host_or_uri: str, queue_hint: str) -> str:
    ipp_uri = host_or_uri if host_or_uri.startswith(("ipp://", "ipps://")) else f"ipp://{host_or_uri}:631/ipp/print"
    if not probe.has_bin("driverless"):
        return ""
    ok_dl, ppd_raw, _ = _run_c(["driverless", ipp_uri], timeout=3.5)
    if not ok_dl or "*PPD-Adobe" not in (ppd_raw or ""):
        return ""
    safe_k = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(queue_hint or "printer"))[:48]
    os.makedirs(os.path.expanduser("~/.cache/nc-ui"), exist_ok=True)
    ppd_path = os.path.expanduser(f"~/.cache/nc-ui/autogen_{safe_k}.ppd")
    with open(ppd_path, "w", encoding="utf-8") as pf:
        pf.write(ppd_raw)
    return ppd_path


def trigger_smart_probe_async(cc_panel, host: str, uri: str, label: str, open_ports: list[int] | None = None) -> None:
    key = (host or uri).strip()
    if not key:
        return
    existing = _SMART_PROBE_CACHE.get(key)
    now_ts = time.monotonic()
    if existing:
        age = now_ts - float(existing.get("ts", 0.0))
        if (existing.get("probing") and age < 8.0) or (not existing.get("probing") and age < 20.0):
            return

    _SMART_PROBE_CACHE[key] = {
        "probing": True,
        "ts": now_ts,
        "ports_ms": {},
        "open_ports": list(open_ports or []),
        "formats": [],
        "duplex": False,
        "color": False,
        "model": label,
        "matched_drv": None,
        "synth_ppd_path": "",
    }

    def _work() -> dict:
        ports_ms: dict[int, int] = {}
        found_ports: list[int] = list(open_ports or [])
        formats: list[str] = []
        duplex = False
        color = False
        model = str(label or "")
        synth_ppd = ""
        matched_drv = None
        drvs = None
        try:
            if host and host != "localhost":
                for port in (9100, 631, 515):
                    t0 = time.monotonic()
                    try:
                        with socket.create_connection((host, port), timeout=0.65):
                            ports_ms[port] = max(1, int(round((time.monotonic() - t0) * 1000.0)))
                            if port not in found_ports:
                                found_ports.append(port)
                    except OSError:
                        continue
            found_ports = [p for p in (9100, 631, 515) if p in found_ports]

            has_ipp = (631 in found_ports) or uri.startswith(("ipp://", "ipps://"))
            ipp_target = uri if uri.startswith(("ipp://", "ipps://")) else (f"ipp://{host}:631/ipp/print" if host else "")
            test_spec = "/usr/share/cups/ipptool/get-printer-attributes.test"
            if has_ipp and ipp_target and probe.has_bin("ipptool") and os.path.isfile(test_spec):
                ok_i, out_i, _ = _run_c(["ipptool", "-T", "2", "-t", ipp_target, test_spec], timeout=2.5)
                if ok_i and out_i:
                    m_mod = re.search(r"printer-make-and-model\s*\([^)]+\)\s*=\s*(.+)", out_i)
                    if m_mod and m_mod.group(1).strip():
                        model = m_mod.group(1).strip().strip('"')[:48]
                    m_fmt = re.search(r"document-format-supported\s*\([^)]+\)\s*=\s*(.+)", out_i)
                    if m_fmt:
                        fblob = m_fmt.group(1).lower()
                        for tok, name in (
                            ("application/pdf", "PDF"),
                            ("image/pwg-raster", "PWG-Raster"),
                            ("image/urf", "Apple-URF"),
                            ("pcl", "PCL"),
                            ("postscript", "PostScript"),
                        ):
                            if tok in fblob and name not in formats:
                                formats.append(name)
                    m_sides = re.search(r"sides-supported\s*\([^)]+\)\s*=\s*(.+)", out_i)
                    if m_sides and "two-sided" in m_sides.group(1).lower():
                        duplex = True
                    m_col = re.search(r"color-supported\s*\([^)]+\)\s*=\s*(.+)", out_i)
                    if m_col and "true" in m_col.group(1).lower():
                        color = True

            if has_ipp and ipp_target:
                synth_ppd = _synthesize_driverless_ppd(ipp_target, host or key)

            drvs = _load_drivers_blocking()
            matched_drv = _match_vendor_driver(model or label, drvs)
        except Exception as e:
            log.debug(f"trigger_smart_probe_async error: {e}")

        return {
            "probing": False,
            "ts": time.monotonic(),
            "ports_ms": ports_ms,
            "open_ports": found_ports,
            "formats": formats,
            "duplex": duplex,
            "color": color,
            "model": model,
            "matched_drv": matched_drv,
            "synth_ppd_path": synth_ppd,
            "drivers": drvs,
        }

    def _done(res: dict) -> bool:
        drvs = res.pop("drivers", None)
        if drvs:
            _CACHE["drivers_list"] = (time.monotonic(), drvs)
        _SMART_PROBE_CACHE[key] = res
        if host and res.get("open_ports"):
            raw_devs = STATE.get("printers", "tcp_discovered", [])
            if isinstance(raw_devs, list):
                changed = False
                for x in raw_devs:
                    if isinstance(x, dict) and x.get("ip") == host and set(x.get("open_ports", [])) != set(res["open_ports"]):
                        x["open_ports"] = list(res["open_ports"])
                        x["ports"] = " ".join(f":{p}" for p in res["open_ports"])
                        changed = True
                if changed:
                    STATE.set("printers", "tcp_discovered", raw_devs)
        if cc_panel and getattr(cc_panel, "shell", None):
            cc_panel.shell.refresh_view()
        return False

    probe.run_async(_work, _done)


def resolve_effective_driver(d: dict, smart: dict | None = None) -> tuple[str, list[str], str]:
    sm = smart or get_smart_probe(d.get("host") or d.get("uri") or "")
    mode = str(d.get("drv_mode", "auto") or "auto")
    if mode == "ppd":
        p = os.path.expanduser(str(d.get("ppd_path", "") or "").strip())
        return ("ppd", ["-P", p] if p else [], os.path.basename(p) if p else t("common.unset"))
    if mode == "raw":
        return ("raw", ["-m", "raw"], t("cc_printers.drv_raw"))
    if mode == "db":
        drv_id = d.get("drv_id") or "drv:///cupsfilters.drv/pwgrast.ppd"
        if drv_id in ("everywhere", "auto"):
            drv_id = "drv:///cupsfilters.drv/pwgrast.ppd"
        lbl = dict(get_cups_drivers()).get(drv_id, drv_id)
        return ("db", ["-m", drv_id], lbl)

    matched = sm.get("matched_drv")
    if isinstance(matched, (tuple, list)) and len(matched) == 2:
        return ("vendor_db", ["-m", matched[0]], str(matched[1]))
    uri = str(d.get("uri", "") or "")
    if uri.startswith(("ipp://", "ipps://", "dnssd://", "implicitclass://")):
        return ("everywhere", ["-m", "everywhere"], t("cc_printers.auto_drv_everywhere"))
    synth = str(sm.get("synth_ppd_path", "") or "")
    if synth and os.path.isfile(synth):
        m_lbl = str(sm.get("model") or d.get("desc") or d.get("host") or d.get("name") or "")
        return ("synth_ppd", ["-P", synth], t("cc_printers.auto_drv_synth_ppd", p0=m_lbl[:34]))
    fmts = sm.get("formats", ())
    if "PWG-Raster" in fmts and os.path.isfile("/usr/share/cups/drv/cupsfilters.drv"):
        return ("pwgrast", ["-m", "drv:///cupsfilters.drv/pwgrast.ppd"], "Generic PWG Raster (Driverless)")
    if "PostScript" in fmts and "PCL" not in fmts:
        return ("ps", ["-m", "drv:///sample.drv/generic.ppd"], "CUPS Generic PostScript")
    if os.path.isfile("/usr/share/cups/drv/cupsfilters.drv") and (631 in sm.get("open_ports", ())):
        return ("pwgrast", ["-m", "drv:///cupsfilters.drv/pwgrast.ppd"], "Generic PWG Raster / IPP (cupsfilters)")
    return ("pcl", ["-m", "drv:///sample.drv/generpcl.ppd"], "CUPS Generic PCL Laser")


_PRINTER_BRANDS = (
    "brother", "hp", "hewlett", "epson", "canon", "samsung", "kyocera",
    "ricoh", "lexmark", "xerox", "oki", "okidata", "zebra", "pantum", "konica", "sharp",
)
_BRAND_NORM = {"hewlett": "hp", "okidata": "oki"}
_CURATED_BRAND_PKGS: dict[str, list[tuple[str, str, str]]] = {
    "brother": [
        ("brlaser", "aur", "CUPS driver for Brother laser & LED printers"),
        ("brgenml1", "aur", "Brother generic ML1/LPR printer driver"),
    ],
    "hp": [
        ("hplip", "extra", "HP Linux Imaging & Printing drivers"),
        ("hplip-plugin", "aur", "Binary plugin for HPLIP scanners & lasers"),
    ],
    "epson": [
        ("epson-inkjet-printer-escpr2", "aur", "Epson ESC/P-R 2 inkjet CUPS driver"),
        ("epson-inkjet-printer-escpr", "aur", "Epson ESC/P-R inkjet CUPS driver"),
    ],
    "canon": [
        ("cnijfilter2", "aur", "Canon IJ CUPS printer driver"),
        ("cndrvcups-lb", "aur", "Canon UFR II / LIPSLX laser driver"),
    ],
    "samsung": [
        ("splix", "aur", "CUPS driver for Samsung SPL printers"),
        ("uld", "aur", "Samsung Unified Linux Driver"),
    ],
    "kyocera": [("kyocera-sane-cups", "aur", "Kyocera ECOSYS/TASKalfa PPD collection")],
}


def _parse_printer_signature(model_text: str, uri: str = "") -> dict:
    raw = str(model_text or "").strip()
    norm = re.sub(r"[^a-z0-9-]+", " ", raw.lower()).strip()
    words = [w for w in norm.split() if w and w not in ("series", "printer", "network", "usb", "cups", "driver", "local", "mdp")]
    brand = ""
    for w in words:
        w_base = w.split("-")[0]
        if w_base in _PRINTER_BRANDS:
            brand = _BRAND_NORM.get(w_base, w_base)
            break

    full_code = ""
    sub_code = ""
    digits = ""
    tokens: list[str] = []
    for w in words:
        flat = w.replace("-", "")
        if any(c.isdigit() for c in flat) and len(flat) >= 3:
            tokens.append(flat)
            if not full_code or len(flat) > len(full_code):
                full_code = flat
            if "-" in w:
                sub = w.split("-")[-1]
                if any(c.isdigit() for c in sub):
                    sub_code = sub
            d_m = re.search(r"\d{3,5}", flat)
            if d_m and not digits:
                digits = d_m.group(0)
        elif w != brand and len(w) >= 2:
            tokens.append(w)

    blob_low = f"{raw} {uri}".lower()
    is_laser = any(k in blob_low for k in ("laser", "dcp-l", "hl-l", "mfc-l", "laserjet", "cdw", "ecosys"))
    key = f"{brand}:{full_code or '_'.join(tokens[:3]) or 'generic'}"
    return {
        "key": key,
        "raw_model": raw,
        "brand": brand,
        "full_code": full_code,
        "sub_code": sub_code,
        "digits": digits,
        "tokens": tokens,
        "is_laser": is_laser,
    }


def _score_driver_candidate(
    sig: dict,
    item_id: str,
    title: str,
    desc: str = "",
    is_pkg: bool = False,
    open_ports: list[int] | None = None,
    formats: list[str] | None = None,
    is_color: bool = False,
) -> int:
    iid = str(item_id or "").lower()
    blob_txt = f"{iid} {title} {desc}".lower()
    blob_flat = re.sub(r"[^a-z0-9]+", "", blob_txt)
    brand = sig["brand"]
    ports = set(open_ports or ())
    fmts = set(formats or ())

    # Escludi tassativamente driver specifici di marche diverse dalla stampante rilevata
    if brand:
        foreign = set(_PRINTER_BRANDS) - {brand, *(k for k, v in _BRAND_NORM.items() if v == brand)}
        if any(re.search(rf"(?<![a-z0-9]){re.escape(fb)}(?![a-z0-9])", blob_txt) for fb in foreign):
            if "foomatic" not in iid and "gutenprint" not in iid:
                return 0
        if brand != "hp" and ("deskjet" in blob_txt or "laserjet" in blob_txt or "designjet" in blob_txt):
            return 0
        if brand != "zebra" and "zebra" in blob_txt:
            return 0
    else:
        # Senza marca nota, escludi driver iperspecifici di marche casuali
        if any(b in blob_txt for b in ("deskjet", "laserjet", "epson 9-pin", "epson 24-pin", "zebra ")):
            return 0

    if iid in ("raw", "drv:///cupsfilters.drv/textonly.ppd"):
        return 0

    def _cap_adjust(sc_val: int) -> int:
        if is_color and re.search(r"\b(monochrome|mono only|black[- ]and[- ]white)\b", blob_txt):
            return max(68, sc_val - 15)
        return sc_val

    # 1. Corrispondenza esatta modello (94..99)
    fc = sig["full_code"]
    sc = sig["sub_code"]
    dig = sig["digits"]
    if fc and fc in blob_flat:
        return 99 if is_pkg else 96
    if sc and sc in blob_flat and (not brand or brand in blob_txt):
        return 96 if is_pkg else 94
    if brand and brand in blob_txt and dig and dig in blob_txt:
        return 94 if is_pkg else 92

    # 2. Driver famiglia / serie dello stesso produttore (universale per marca, con penalità B/N su stampanti a colori)
    if brand == "brother" and ("brlaser" in blob_txt or "brgenml1" in blob_txt):
        base_f = 70 if (is_color and "brlaser" in blob_txt) else 89
        return _cap_adjust(base_f)
    if brand == "hp" and ("hplip" in blob_txt or "hp" in iid):
        return _cap_adjust(88)
    if brand == "epson" and ("escpr" in blob_txt or "epson-inkjet" in blob_txt):
        return _cap_adjust(88)
    if brand == "canon" and ("cnijfilter" in blob_txt or "cndrvcups" in blob_txt or "carps" in blob_txt):
        return _cap_adjust(88)
    if brand == "samsung" and ("splix" in blob_txt or "uld" in iid):
        return _cap_adjust(88)
    if brand and brand in blob_txt and any(k in blob_txt for k in ("cups", "driver", "ppd", "filter", "laser", "inkjet", "lpr")):
        return _cap_adjust(88 if is_pkg else 82)

    # 3. Protocollo driverless verificato su rete / PDL (72..79)
    if iid == "everywhere":
        return 78 if (631 in ports or "PWG-Raster" in fmts or "PDF" in fmts or not ports) else 58
    if "pwgrast.ppd" in iid:
        return 74 if (631 in ports or "PWG-Raster" in fmts or not ports) else 56

    # 4. Suite PPD universali (58..68)
    if is_pkg:
        if "foomatic-db-nonfree-ppds" in iid:
            return 66
        if "foomatic-db-ppds" in iid or iid == "foomatic-db-engine":
            return 65
        if "gutenprint" in iid:
            return 60
        return 0

    # 5. Driver generici compatibili PDL (48..55)
    if "generpcl.ppd" in iid:
        return 54 if (sig["is_laser"] or "PCL" in fmts or not fmts) else 45
    if "generic.ppd" in iid:
        if fmts and "PostScript" not in fmts and "PCL" in fmts:
            return 0
        return 50 if "PostScript" in fmts else 46
    return 0


def _describe_driver_reason(sig: dict, item_id: str, title: str, desc: str, score: int) -> tuple[str, dict]:
    model = sig["raw_model"] or sig["full_code"].upper() or "this printer"
    brand = (sig["brand"] or "printer").title()
    if score >= 90:
        return "exact", {"model": model, "brand": brand, "pkg": title}
    if score >= 80:
        return "family", {"model": model, "brand": brand, "pkg": title}
    if score >= 60:
        return "universal", {"model": model, "brand": brand, "pkg": title}
    return "pdl", {"model": model, "brand": brand, "pkg": title}


def _parse_pkg_search_lines(pm: str, out: str) -> list[dict]:
    items: list[dict] = []
    if not out:
        return items
    if pm in ("paru", "yay", "pikaur", "trizen", "pacman"):
        lines = out.splitlines()
        i = 0
        while i < len(lines):
            hdr = lines[i].rstrip()
            if hdr and not hdr[0].isspace() and "/" in hdr:
                m = re.match(r"^([^/\s]+)/([^\s]+)\s+([^\s]+)(.*)$", hdr.strip())
                desc = ""
                if i + 1 < len(lines) and lines[i + 1].startswith((" ", "\t")):
                    desc = lines[i + 1].strip()
                    i += 1
                if m:
                    repo, pkg, ver, tail = m.groups()
                    items.append({
                        "pkg": pkg.strip(),
                        "repo": repo.strip(),
                        "ver": ver.strip(),
                        "installed": "[installed" in tail.lower(),
                        "desc": desc,
                    })
            i += 1
    else:
        for ln in out.splitlines():
            s = ln.strip()
            if not s:
                continue
            if " - " in s:
                pkg_p, desc = s.split(" - ", 1)
                pkg = pkg_p.split()[0].strip()
            elif " : " in s:
                pkg_p, desc = s.split(" : ", 1)
                pkg = pkg_p.split(".")[0].strip()
            else:
                continue
            if pkg:
                items.append({"pkg": pkg, "repo": pm, "ver": "", "installed": False, "desc": desc.strip()})
    return items


def _check_installed_pkgs(pm: str, pkg_names: list[str]) -> set[str]:
    clean = sorted({p for p in pkg_names if p})
    if not clean:
        return set()
    if pm in ("paru", "yay", "pikaur", "trizen", "pacman") and probe.has_bin("pacman"):
        ok, out, _ = _run_c(["pacman", "-Qq"] + clean, timeout=1.2)
        if out:
            return {ln.strip() for ln in out.splitlines() if ln.strip()}
        inst: set[str] = set()
        for p in clean:
            ok1, _, _ = _run_c(["pacman", "-Qq", p], timeout=0.6)
            if ok1:
                inst.add(p)
        return inst
    if pm == "apt" and probe.has_bin("dpkg-query"):
        ok, out, _ = _run_c(["dpkg-query", "-W", "-f=${Package}\n"] + clean, timeout=1.2)
        return {ln.strip() for ln in (out or "").splitlines() if ln.strip()}
    if pm in ("dnf", "zypper") and probe.has_bin("rpm"):
        ok, out, _ = _run_c(["rpm", "-q", "--qf", "%{NAME}\n"] + clean, timeout=1.2)
        return {ln.strip() for ln in (out or "").splitlines() if ln.strip() and "not installed" not in ln.lower()}
    return set()


def _search_pkg_drivers_blocking(sig: dict) -> list[dict]:
    pm = probe.get_preferred_pkg_manager()
    raw_pkgs: dict[str, dict] = {}

    def _add_raw(it: dict) -> None:
        pk = it["pkg"]
        if pk not in raw_pkgs:
            raw_pkgs[pk] = dict(it)

    queries: list[str] = []
    if sig["full_code"]:
        queries.append(sig["full_code"])
    if sig["brand"]:
        queries.append({"brother": "brlaser", "hp": "hplip", "epson": "escpr", "canon": "cnijfilter", "samsung": "splix"}.get(sig["brand"], sig["brand"]))
    queries.append("foomatic-db")

    if pm in ("paru", "yay", "pikaur", "trizen", "pacman"):
        for idx, q in enumerate(queries):
            if not q:
                continue
            tool = "pacman" if (q == "foomatic-db" and probe.has_bin("pacman")) else pm
            ok_s, out_s, _ = _run_c([tool, "-Ss", q], timeout=3.8 if idx == 0 else 3.0)
            if ok_s and out_s:
                for it in _parse_pkg_search_lines(tool, out_s):
                    _add_raw(it)
            elif tool != "pacman" and probe.has_bin("pacman"):
                ok_p, out_p, _ = _run_c(["pacman", "-Ss", q], timeout=1.8)
                if ok_p and out_p:
                    for it in _parse_pkg_search_lines("pacman", out_p):
                        _add_raw(it)
    elif pm == "apt" and probe.has_bin("apt-cache"):
        for q in (sig["brand"] or "printer-driver", "printer-driver-all"):
            ok_a, out_a, _ = _run_c(["apt-cache", "search", q], timeout=2.5)
            if ok_a and out_a:
                for it in _parse_pkg_search_lines("apt", out_a):
                    _add_raw(it)
    elif pm in ("dnf", "zypper"):
        for q in (sig["brand"] or "cups", "foomatic"):
            ok_d, out_d, _ = _run_c([pm, "search", q], timeout=3.5)
            if ok_d and out_d:
                for it in _parse_pkg_search_lines(pm, out_d):
                    _add_raw(it)

    # Fallback/Integrazione deterministica per Arch/AUR (se offline o in sandbox)
    if sig["brand"] == "brother" and sig["full_code"]:
        _add_raw({"pkg": f"brother-{sig['full_code']}", "repo": "aur", "ver": "", "installed": False, "desc": f"LPR & CUPS driver for {sig['raw_model']}"})
    for pk, rp, ds in _CURATED_BRAND_PKGS.get(sig["brand"], ()):
        _add_raw({"pkg": pk, "repo": rp, "ver": "", "installed": False, "desc": ds})
    if not raw_pkgs or len(raw_pkgs) < 3:
        for pk, rp, ds in (
            ("foomatic-db-ppds", "extra", "Foomatic manufacturer PPD files"),
            ("foomatic-db-nonfree-ppds", "extra", "Foomatic non-free printer PPDs"),
            ("gutenprint", "extra", "Top quality CUPS printer drivers"),
        ):
            _add_raw({"pkg": pk, "repo": rp, "ver": "", "installed": False, "desc": ds})

    inst_set = _check_installed_pkgs(pm, list(raw_pkgs.keys()))
    for pk, it in raw_pkgs.items():
        if pk in inst_set:
            it["installed"] = True
    return list(raw_pkgs.values())


def trigger_auto_driver_search_async(cc_panel, model_text: str, uri: str = "", force: bool = False) -> None:
    sig = _parse_printer_signature(model_text, uri)
    skey = sig["key"]
    hit = _AUTO_DRV_PKG_CACHE.get(skey)
    if not force and hit is not None and (time.monotonic() - hit[0]) < 90.0:
        return
    with _AUTO_DRV_LOCK:
        if skey in _AUTO_DRV_LOADING:
            return
        _AUTO_DRV_LOADING.add(skey)

    def _work() -> list[dict]:
        return _search_pkg_drivers_blocking(sig)

    def _done(pkgs: list[dict]) -> bool:
        with _AUTO_DRV_LOCK:
            _AUTO_DRV_LOADING.discard(skey)
        _AUTO_DRV_PKG_CACHE[skey] = (time.monotonic(), pkgs)
        if cc_panel and getattr(cc_panel, "shell", None):
            cc_panel.shell.refresh_view()
        return False

    probe.run_async(_work, _done)


def get_auto_found_drivers(cc_panel, d: dict, smart: dict | None = None) -> dict:
    sm = smart or get_smart_probe(d.get("host") or d.get("uri") or "")
    model_txt = str(sm.get("model") or d.get("desc") or d.get("name") or "").strip()
    sig = _parse_printer_signature(model_txt, d.get("uri", ""))
    skey = sig["key"]
    pm = probe.get_preferred_pkg_manager() or "pacman"

    hit = _AUTO_DRV_PKG_CACHE.get(skey)
    if hit is None or (time.monotonic() - hit[0]) >= 90.0:
        trigger_auto_driver_search_async(cc_panel, model_txt, d.get("uri", ""))
    pkgs = hit[1] if hit is not None else []
    searching = skey in _AUTO_DRV_LOADING

    open_ports = sm.get("open_ports") or []
    formats = sm.get("formats") or []
    is_color = bool(sm.get("color")) or bool(re.search(r"\b(color|colour|[a-z]+-\d+[a-z]*c[a-z]*)\b", model_txt.lower()))

    strat, _args, res_lbl = resolve_effective_driver(d, sm)
    if str(d.get("drv_mode", "auto") or "auto") == "db":
        local_id = d.get("drv_id") or "drv:///cupsfilters.drv/pwgrast.ppd"
        local_lbl = dict(get_cups_drivers(cc_panel)).get(local_id, local_id)
        local_score = _score_driver_candidate(sig, local_id, local_lbl, is_pkg=False, open_ports=open_ports, formats=formats, is_color=is_color) or 54
    else:
        local_lbl = res_lbl
        if strat == "vendor_db":
            local_score = 96
        elif strat == "synth_ppd":
            local_score = 82
        elif strat == "everywhere":
            local_score = _score_driver_candidate(sig, "everywhere", res_lbl, is_pkg=False, open_ports=open_ports, formats=formats, is_color=is_color) or 78
        elif strat == "pwgrast":
            local_score = _score_driver_candidate(sig, "drv:///cupsfilters.drv/pwgrast.ppd", res_lbl, is_pkg=False, open_ports=open_ports, formats=formats, is_color=is_color) or 74
        else:
            local_score = 54

    candidates: list[dict] = []

    synth = str(sm.get("synth_ppd_path", "") or "")
    if synth and os.path.isfile(synth) and strat != "synth_ppd":
        rk, rp = _describe_driver_reason(sig, synth, synth, synth, 96)
        candidates.append({
            "kind": "cups",
            "id": f"ppd:{synth}",
            "pkg": "",
            "label": f"Auto PPD ({sig['full_code'].upper() or sig['raw_model'][:18]})",
            "desc": synth,
            "score": 96,
            "installed": True,
            "tag": "AUTO",
            "better_than_local": bool(96 > local_score),
            "reason_kind": rk,
            "reason_params": rp,
        })

    for drv_id, lbl in get_cups_drivers(cc_panel):
        if "sample.drv" in drv_id or "cupsfilters.drv" in drv_id or drv_id in ("everywhere", "raw"):
            continue
        if lbl == local_lbl:
            continue
        sc = _score_driver_candidate(sig, drv_id, lbl, is_pkg=False, open_ports=open_ports, formats=formats, is_color=is_color)
        if sc >= 80:
            rk, rp = _describe_driver_reason(sig, drv_id, lbl, drv_id, sc)
            candidates.append({
                "kind": "cups",
                "id": drv_id,
                "pkg": "",
                "label": lbl,
                "desc": drv_id,
                "score": sc,
                "installed": True,
                "tag": "CUPS",
                "better_than_local": bool(sc > local_score),
                "reason_kind": rk,
                "reason_params": rp,
            })

    for it in pkgs:
        sc = _score_driver_candidate(sig, it["pkg"], it["pkg"], desc=it.get("desc", ""), is_pkg=True, open_ports=open_ports, formats=formats, is_color=is_color)
        if sc >= 45:
            rk, rp = _describe_driver_reason(sig, it["pkg"], it["pkg"], it.get("desc", ""), sc)
            candidates.append({
                "kind": "pkg",
                "id": f"pkg:{it['pkg']}",
                "pkg": it["pkg"],
                "repo": it.get("repo", pm),
                "label": it["pkg"],
                "desc": it.get("desc", ""),
                "score": sc,
                "installed": bool(it.get("installed")),
                "tag": "PKG OK" if it.get("installed") else it.get("repo", "pkg").upper(),
                "better_than_local": bool(sc > local_score),
                "reason_kind": rk,
                "reason_params": rp,
            })

    candidates.sort(key=lambda c: (-c["score"], 0 if c["installed"] else 1, c["label"].lower()))

    best_pkg_score = max((c["score"] for c in candidates if c["kind"] == "pkg" and not c["installed"]), default=0)
    score_gap = best_pkg_score - local_score
    if score_gap <= 0 or local_score >= 92:
        gap_advice = "local_optimal"
    elif local_score >= 74 and score_gap <= 25:
        gap_advice = "test_local_first"
    elif local_score < 74 and score_gap >= 20:
        gap_advice = "install_pkg_first"
    else:
        gap_advice = "local_optimal"

    raw_q = str(d.get("drv_query", "") or "").strip().lower()
    if raw_q:
        filtered = [c for c in candidates if raw_q in f"{c['label']} {c['desc']} {c['id']} {c.get('repo', '')}".lower()]
    else:
        filtered = candidates

    win_size = 5
    max_off = max(0, len(filtered) - win_size)
    raw_off = int(cc_panel.get_state("prn_drv_scroll", 0) or 0)
    off = max(0, min(max_off, raw_off))
    if off != raw_off:
        cc_panel.set_state("prn_drv_scroll", off)

    return {
        "items": filtered,
        "window": filtered[off : off + win_size],
        "total": len(filtered),
        "total_unfiltered": len(candidates),
        "offset": off,
        "win_size": win_size,
        "searching": searching,
        "pkg_mgr": pm,
        "sig_key": skey,
        "gap_advice": gap_advice,
        "local_driver": {"label": local_lbl, "score": local_score, "strategy": strat},
    }


def _watch_pkg_install_async(cc_panel, pkg_name: str) -> None:
    pm = probe.get_preferred_pkg_manager() or "pacman"

    def _work() -> tuple[bool, list[tuple[str, str]]]:
        for _ in range(60):
            time.sleep(1.5)
            if pkg_name in _check_installed_pkgs(pm, [pkg_name]):
                return True, _load_drivers_blocking()
        return False, []

    def _done(res: tuple[bool, list[tuple[str, str]]]) -> bool:
        installed, drvs = res
        if installed:
            if drvs:
                _CACHE["drivers_list"] = (time.monotonic(), drvs)
            else:
                _CACHE.pop("drivers_list", None)
                drvs = _load_drivers_blocking()
                _CACHE["drivers_list"] = (time.monotonic(), drvs)
            for entry in _SMART_PROBE_CACHE.values():
                if isinstance(entry, dict) and entry.get("model"):
                    entry["matched_drv"] = _match_vendor_driver(entry["model"], drvs)
            _AUTO_DRV_PKG_CACHE.clear()
            _set_fb(True, t("cc_printers.fb_pkg_installed_ready", p0=pkg_name))
            if cc_panel and getattr(cc_panel, "shell", None):
                cc_panel.shell.refresh_view()
        return False

    probe.run_async(_work, _done)


def probe_printer_host(target: str) -> tuple[bool, str]:
    raw = str(target or "").strip()
    if not raw or raw == "usb://":
        return False, t("cc_printers.err_empty_host")
    host = raw
    ports = [631, 9100, 515]
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        if parsed.port:
            ports = [parsed.port]
        elif parsed.scheme == "socket":
            ports = [9100]
        elif parsed.scheme in ("ipp", "ipps", "http", "https"):
            ports = [631]
        elif parsed.scheme == "lpd":
            ports = [515]
        elif parsed.scheme == "smb":
            ports = [445, 139]
    elif ":" in raw and not raw.startswith("["):
        h_p, p_s = raw.rsplit(":", 1)
        if p_s.isdigit():
            host, ports = h_p, [int(p_s)]

    if not host:
        return False, t("cc_printers.err_empty_host")

    open_res = []
    for port in ports:
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, int(port)), timeout=0.9):
                ms = int(round((time.monotonic() - t0) * 1000.0))
                open_res.append(f":{port} ({ms} ms)")
        except OSError:
            continue
    if open_res:
        return True, t("cc_printers.fb_probe_ok", p0=host, p1=", ".join(open_res))
    return False, t("cc_printers.fb_probe_fail", p0=host, p1="/".join(str(p) for p in ports))


def validate_ppd_file(path: str) -> tuple[bool, str]:
    exp = os.path.expanduser(str(path or "").strip())
    if not exp or not os.path.isfile(exp):
        return False, t("cc_printers.err_file_not_found", p0=path)
    if not probe.has_bin("cupstestppd"):
        return True, exp
    ok, out, err = _run_c(["cupstestppd", "-q", exp], timeout=2.5)
    if ok:
        return True, t("cc_printers.fb_ppd_valid", p0=os.path.basename(exp))
    detail = (err or out or "invalid PPD").splitlines()[0][:72]
    return False, t("cc_printers.err_ppd_invalid", p0=detail)


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


def _exec_admin(cc_panel, cmd: list[str], ok_msg: str, try_direct: bool = True, after_fn=None) -> None:
    if try_direct:
        ok, out, err = _run_c(cmd, timeout=5.0)
        if ok:
            invalidate_printers_cache()
            if callable(after_fn):
                after_fn()
            _set_fb(True, ok_msg)
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return
        if not _needs_elevation(err, out):
            _set_fb(False, (err or out or t("cc_printers.err_cmd_failed"))[:110])
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return

    ok_s, out_s, err_s = _run_c(["sudo", "-n"] + list(cmd), timeout=5.0)
    if ok_s:
        invalidate_printers_cache()
        if callable(after_fn):
            after_fn()
        _set_fb(True, ok_msg)
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return

    _PENDING_SUDO["cmd"] = list(cmd)
    _PENDING_SUDO["ok_msg"] = ok_msg
    _PENDING_SUDO["after_fn"] = after_fn
    cc_panel.request_prompt(
        "cc_prn_sudo",
        t("cc_printers.prompt_sudo_pwd", p0=" ".join(cmd[:3])),
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
    cups_tp = "/usr/share/cups/data/testprint"
    if os.path.isfile(cups_tp):
        ok, out, err = _run_c(["lp", "-d", printer_name, "-t", "NC-UI Test Page", cups_tp], timeout=4.0)
    else:
        fd, tmp_ps = tempfile.mkstemp(prefix="nc_testpage_", suffix=".ps", dir="/tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(_build_diagnostic_ps(printer_name))
            ok, out, err = _run_c(["lp", "-d", printer_name, "-t", "NC-UI Calibration Page", tmp_ps], timeout=4.0)
        finally:
            try:
                os.unlink(tmp_ps)
            except OSError:
                pass
    invalidate_printers_cache()
    _set_fb(ok, t("cc_printers.fb_test_page_sent", p0=printer_name) if ok else (err or out or t("cc_printers.err_cmd_failed"))[:110])
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()


def send_cups_maintenance_cmd(cc_panel, printer_name: str, command_line: str, label: str) -> None:
    payload = f"#CUPS-COMMAND\n{command_line}\n".encode("utf-8")
    ok, out, err = _run_c(
        ["lp", "-d", printer_name, "-t", f"CUPS {label}", "-o", "document-format=application/vnd.cups-command"],
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

    cmd = ["lp", "-d", printer_name, "-t", os.path.basename(exp)]
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
    return list(_PROTO_PRESETS)


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
        v_ok, v_msg = validate_ppd_file(d.get("ppd_path", ""))
        if not v_ok:
            _set_fb(False, v_msg)
            if shell:
                shell.refresh_view()
            return

    smart = dict(smart_override or get_smart_probe(d.get("host") or d.get("uri") or ""))
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
        sched_ok, sched_out, _ = _run_c(["lpstat", "-r"], timeout=0.8) if probe.has_bin("lpstat") else (False, "", "")
        if not (sched_ok and "is running" in (sched_out or "").lower()):
            res_s, out_s, err_s = _priv_exec(["/usr/bin/systemctl", "start", "cups.service"], timeout=6.0)
            if res_s == "NEED_AUTH":
                return "NEED_SUDO", "systemctl start cups.service"
            if not res_s:
                return "ERR", (err_s or out_s or t("cc_printers.err_spooler_down"))[:140]
            for _ in range(12):
                time.sleep(0.2)
                s_ok, s_out, _ = _run_c(["lpstat", "-r"], timeout=0.6)
                if s_ok and "is running" in (s_out or "").lower():
                    break

        # 2. Resolve effective driver & synthesize PPD if TCP/IP + Auto
        strategy, drv_args, _ = resolve_effective_driver(d, smart)
        host_ipp = d.get("host") or (urlparse(d["uri"]).hostname or "")
        if d.get("drv_mode") == "auto" and d["uri"].startswith(("socket://", "lpd://")) and strategy in ("pcl", "pwgrast", "ps") and host_ipp:
            synth = _synthesize_driverless_ppd(host_ipp, d["name"])
            if synth:
                drv_args = ["-P", synth]

        base = ["/usr/bin/lpadmin", "-p", d["name"], "-E", "-v", d["uri"]]
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
            synth2 = _synthesize_driverless_ppd(d["uri"], d["name"])
            if synth2:
                ok_inst, out_i, err_i = _priv_exec(base + ["-P", synth2] + meta, timeout=10.0)
            if not ok_inst:
                fb_m = "drv:///cupsfilters.drv/pwgrast.ppd" if os.path.isfile("/usr/share/cups/drv/cupsfilters.drv") else "drv:///sample.drv/generpcl.ppd"
                ok_inst, out_i, err_i = _priv_exec(base + ["-m", fb_m] + meta, timeout=10.0)

        if not ok_inst:
            return "ERR", (err_i or out_i or t("cc_printers.err_cmd_failed"))[:140]

        # 4. Force enable, accept jobs & apply system default
        _priv_exec(["/usr/bin/cupsenable", d["name"]], timeout=4.0)
        _priv_exec(["/usr/bin/cupsaccept", d["name"]], timeout=4.0)
        if d.get("make_default"):
            _run_c(["lpoptions", "-d", d["name"]], timeout=1.5)
            _priv_exec(["/usr/bin/lpadmin", "-d", d["name"]], timeout=3.0)

        # 5. Verify queue registration on CUPS
        ok_v, out_v, err_v = _run_c(["lpstat", "-p", d["name"]], timeout=2.0)
        if not ok_v:
            return "ERR", (err_v or out_v or t("cc_printers.err_cmd_failed"))[:140]

        # 6. Optional calibration test page
        if print_test:
            tmp_ps = ""
            ok_lp = False
            try:
                with tempfile.NamedTemporaryFile(prefix="ncui_test_", suffix=".ps", delete=False) as tf:
                    tmp_ps = tf.name
                    tf.write(_build_diagnostic_ps(d["name"]))
                ok_lp, _o, _e = _run_c(["lp", "-d", d["name"], "-t", "NC-UI Calibration Page", tmp_ps], timeout=4.0)
            finally:
                if tmp_ps:
                    try:
                        os.unlink(tmp_ps)
                    except OSError:
                        pass
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
# LINK & PROMPT DISPATCHERS (COLON-SAFE DETERMINISTIC ROUTING)
# =====================================================================

def handle_cc_printers_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_prn:"):
        return False

    rest = uri[len("cc_prn:") :]
    act, _, tail = rest.partition(":")
    arg1, _, arg2 = tail.partition(":")
    shell = getattr(cc_panel, "shell", None)

    if act not in ("ask_confirm", "delete_prn", "purge_queue", "class_delete"):
        set_pending_confirm(cc_panel, "")

    if act == "refresh":
        invalidate_printers_cache(keep_drivers=False)
        if shell:
            shell.refresh_view()
        return True

    if act == "ask_confirm" and tail:
        set_pending_confirm(cc_panel, tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "cancel_confirm":
        set_pending_confirm(cc_panel, "")
        if shell:
            shell.refresh_view()
        return True

    if act == "select_prn" and tail:
        cc_panel.set_state("prn_selected", tail)
        update_drv_edit_state(printer="")
        if shell:
            shell.refresh_view()
        return True

    if act == "select_job" and tail:
        cc_panel.set_state("prn_selected_job", tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "select_class" and tail:
        cc_panel.set_state("prn_selected_class", tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "goto_subtab" and tail:
        cc_panel.set_subtab(tail)
        if shell:
            shell.refresh_view()
        return True

    # --- Services, Privileges & Web UI ---
    if act == "webui":
        probe.spawn_bg(["xdg-open", "http://localhost:631"])
        _set_fb(True, t("cc_printers.fb_opened_webui"))
        if shell:
            shell.refresh_view()
        return True

    if act == "svc" and arg1 in ("start", "stop", "restart", "enable", "disable"):
        unit = arg2 or "cups.service"
        cmd = ["/usr/bin/systemctl", arg1, unit]
        _exec_admin(cc_panel, cmd, t("cc_printers.fb_svc_done", p0=unit, p1=arg1), try_direct=False)
        return True

    if act == "priv_modal" and tail:
        request_privilege_modal(cc_panel, tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "clear_logs":
        _exec_admin(
            cc_panel,
            ["/usr/bin/truncate", "-s", "0", "/var/log/cups/error_log"],
            t("cc_printers.fb_logs_cleared"),
            try_direct=False,
        )
        return True

    if act == "dismiss_logs_view":
        dismiss_cups_logs_view()
        if shell:
            shell.refresh_view()
        return True

    # --- Printer Lifecycle, In-Place Edit & Cloning ---
    if act == "set_default" and tail:
        _run_c(["lpoptions", "-d", tail], timeout=1.5)
        _exec_admin(cc_panel, ["/usr/bin/lpadmin", "-d", tail], t("cc_printers.fb_default_set", p0=tail))
        return True

    if act == "enable_prn" and tail:
        _exec_admin(cc_panel, ["/usr/bin/cupsenable", tail], t("cc_printers.fb_prn_resumed", p0=tail))
        return True

    if act == "pause_prn" and tail:
        _exec_admin(cc_panel, ["/usr/bin/cupsdisable", tail], t("cc_printers.fb_prn_paused", p0=tail))
        return True

    if act == "accept_prn" and arg1:
        is_on = (arg2 == "1")
        bin_p = "/usr/bin/cupsaccept" if is_on else "/usr/bin/cupsreject"
        msg = t("cc_printers.fb_accept_on", p0=arg1) if is_on else t("cc_printers.fb_accept_off", p0=arg1)
        _exec_admin(cc_panel, [bin_p, arg1], msg)
        return True

    if act == "share_prn" and arg1:
        val = "true" if arg2 == "1" else "false"
        _exec_admin(
            cc_panel,
            ["/usr/bin/lpadmin", "-p", arg1, "-o", f"printer-is-shared={val}"],
            t("cc_printers.fb_share_updated", p0=arg1, p1=val),
        )
        return True

    if act == "err_policy" and arg1 and arg2 in ("retry-job", "abort-job", "stop-printer"):
        _exec_admin(
            cc_panel,
            ["/usr/bin/lpadmin", "-p", arg1, "-o", f"printer-error-policy={arg2}"],
            t("cc_printers.fb_policy_set", p0=arg1, p1=arg2),
        )
        return True

    if act == "delete_prn" and tail:
        set_pending_confirm(cc_panel, "")
        _exec_admin(cc_panel, ["/usr/bin/lpadmin", "-x", tail], t("cc_printers.fb_prn_deleted", p0=tail))
        return True

    if act == "edit_uri_prompt" and tail:
        snap = get_printers_snapshot()
        prn = next((p for p in snap["printers"] if p["name"] == tail), None)
        cur_u = prn["uri"] if prn else "ipp://"
        cc_panel.request_prompt(
            f"cc_prn_uri|{tail}",
            t("cc_printers.prompt_edit_uri", p0=tail),
            cur_u,
            secret=False,
            title=t("cc_printers.prompt_edit_uri_title"),
            required=True,
        )
        return True

    if act == "clone_prompt" and tail:
        cc_panel.request_prompt(
            f"cc_prn_clone|{tail}",
            t("cc_printers.prompt_clone_name", p0=tail),
            f"{tail}_Profile",
            secret=False,
            title=t("cc_printers.prompt_clone_title"),
            required=True,
        )
        return True

    if act == "toggle_drv_edit" and tail:
        cur_e = get_drv_edit_state()
        if cur_e["printer"] == tail:
            update_drv_edit_state(printer="")
        else:
            update_drv_edit_state(printer=tail, mode="everywhere", drv_id="everywhere", query="", ppd_path="")
        if shell:
            shell.refresh_view()
        return True

    if act == "drv_edit_mode" and arg1 in ("everywhere", "db", "raw", "ppd"):
        kw = {"mode": arg1}
        if arg1 == "everywhere":
            kw["drv_id"] = "everywhere"
        elif arg1 == "raw":
            kw["drv_id"] = "raw"
        elif arg1 == "db" and get_drv_edit_state()["drv_id"] in ("everywhere", "raw"):
            kw["drv_id"] = "drv:///sample.drv/generic.ppd"
        update_drv_edit_state(**kw)
        if shell:
            shell.refresh_view()
        return True

    if act == "drv_edit_search":
        de = get_drv_edit_state()
        start_inline_input(
            "prn_drv_edit_in",
            shell=shell,
            trigger_uri=uri,
            initial_text=de["query"],
            max_len=48,
            strip_leading_hash=False,
            uppercase=False,
            on_change=lambda v: update_drv_edit_state(query=v),
            on_commit=lambda v: update_drv_edit_state(query=v),
        )
        return True

    if act == "drv_edit_clear":
        update_drv_edit_state(query="")
        if shell:
            shell.refresh_view()
        return True

    if act == "drv_edit_pick" and tail:
        update_drv_edit_state(mode="db", drv_id=tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "drv_edit_ppd":
        de = get_drv_edit_state()
        cc_panel.request_prompt(
            "cc_prn_edit_ppd",
            t("cc_printers.prompt_ppd_path"),
            de["ppd_path"] or "/usr/share/cups/model/",
            secret=False,
            title=t("cc_printers.prompt_ppd_title"),
            required=True,
            completion=("path_ext", _PPD_EXTS),
        )
        return True

    if act == "drv_edit_apply" and tail:
        de = get_drv_edit_state()
        if de["mode"] == "ppd":
            v_ok, v_msg = validate_ppd_file(de["ppd_path"])
            if not v_ok:
                _set_fb(False, v_msg)
                if shell:
                    shell.refresh_view()
                return True
            cmd = ["/usr/bin/lpadmin", "-p", tail, "-E", "-P", os.path.expanduser(de["ppd_path"])]
        else:
            model = "everywhere" if de["mode"] == "everywhere" else ("raw" if de["mode"] == "raw" else de["drv_id"])
            cmd = ["/usr/bin/lpadmin", "-p", tail, "-E", "-m", model]
        _exec_admin(
            cc_panel,
            cmd,
            t("cc_printers.fb_drv_updated", p0=tail),
            after_fn=lambda: update_drv_edit_state(printer=""),
        )
        return True

    if act == "test_page" and tail:
        print_test_page(cc_panel, tail)
        return True

    if act == "identify_prn" and tail:
        identify_printer(cc_panel, tail)
        return True

    if act == "maint" and arg1 and arg2:
        cmd_map = {
            "clean": ("Clean all", t("cc_printers.maint_clean")),
            "selftest": ("PrintSelfTestPage", t("cc_printers.maint_selftest")),
            "levels": ("ReportLevels", t("cc_printers.maint_levels")),
        }
        if arg2 in cmd_map:
            c_str, lbl = cmd_map[arg2]
            send_cups_maintenance_cmd(cc_panel, arg1, c_str, lbl)
        return True

    # --- Quick Print Job Composer ---
    if act == "print_file" and tail:
        # Open composer on devices & prompt for file
        cc_panel.set_state("prn_sec_qprint", True)
        qp = get_quick_print_draft()
        cc_panel.request_prompt(
            f"cc_prn_file|{tail}",
            t("cc_printers.prompt_print_file", p0=tail),
            qp["file"] or "~/",
            secret=False,
            title=t("cc_printers.prompt_print_file_title"),
            required=True,
            completion=("path_ext", _PRINTABLE_EXTS),
        )
        return True

    if act == "qp_pick_file":
        qp = get_quick_print_draft()
        cc_panel.request_prompt(
            "cc_prn_qp_setfile",
            t("cc_printers.prompt_qp_file"),
            qp["file"] or "~/",
            secret=False,
            title=t("cc_printers.prompt_print_file_title"),
            required=True,
            completion=("path_ext", _PRINTABLE_EXTS),
        )
        return True

    if act == "qp_copies" and tail:
        if tail.isdigit():
            update_quick_print_draft(copies=int(tail))
            if shell:
                shell.refresh_view()
        return True

    if act == "qp_copies_edit":
        qp = get_quick_print_draft()
        start_inline_input(
            "prn_qp_copies_in",
            shell=shell,
            trigger_uri=uri,
            initial_text=str(qp["copies"]),
            max_len=2,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda v: (v.strip().isdigit() and 1 <= int(v.strip()) <= 99, t("cc_printers.err_bad_copies")),
            on_commit=lambda v: update_quick_print_draft(copies=int(v.strip())),
        )
        return True

    if act == "qp_pages_all":
        update_quick_print_draft(pages="")
        if shell:
            shell.refresh_view()
        return True

    if act == "qp_pages_edit":
        qp = get_quick_print_draft()
        start_inline_input(
            "prn_qp_pages_in",
            shell=shell,
            trigger_uri=uri,
            initial_text=qp["pages"],
            max_len=24,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda v: (not v.strip() or bool(_PAGE_RANGE_RE.match(v.strip())), t("cc_printers.err_bad_pages")),
            on_commit=lambda v: update_quick_print_draft(pages=v.strip()),
        )
        return True

    if act == "qp_fit" and tail in ("0", "1"):
        update_quick_print_draft(fit=(tail == "1"))
        if shell:
            shell.refresh_view()
        return True

    if act == "qp_sides" and tail in ("default", "one-sided", "two-sided-long-edge", "two-sided-short-edge"):
        update_quick_print_draft(sides=tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "qp_color" and tail in ("default", "color", "monochrome"):
        update_quick_print_draft(color=tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "qp_submit" and tail:
        submit_quick_print(cc_panel, tail)
        return True

    # --- Inline Edit Description / Location ---
    if act in ("edit_desc", "edit_loc") and tail:
        snap = get_printers_snapshot()
        prn = next((p for p in snap["printers"] if p["name"] == tail), None)
        cur_val = (prn["description"] if act == "edit_desc" else prn["location"]) if prn else ""
        flag = "-D" if act == "edit_desc" else "-L"
        eid = f"prn_{act}_{tail}"

        def _commit_meta(val: str, p_name=tail, fl=flag):
            _exec_admin(cc_panel, ["/usr/bin/lpadmin", "-p", p_name, fl, val.strip()], t("cc_printers.fb_meta_saved", p0=p_name))

        start_inline_input(
            eid,
            shell=shell,
            trigger_uri=uri,
            initial_text=cur_val,
            max_len=64,
            strip_leading_hash=False,
            uppercase=False,
            on_commit=_commit_meta,
        )
        return True

    # --- Printing Preferences & PPD / IPP System + User Sync ---
    if act == "set_opt" and arg1 and "=" in arg2:
        opt_k, opt_v = arg2.split("=", 1)
        _run_c(["lpoptions", "-p", arg1, "-o", f"{opt_k}={opt_v}"], timeout=1.5)
        if opt_k in _LPADMIN_DEFAULT_KEYS or not opt_k.startswith("job-"):
            _exec_admin(
                cc_panel,
                ["/usr/bin/lpadmin", "-p", arg1, "-o", f"{opt_k}-default={opt_v}"],
                t("cc_printers.fb_opt_saved", p0=opt_k, p1=humanize_ppd_choice(opt_k, opt_v)),
            )
        else:
            invalidate_printers_cache()
            _set_fb(True, t("cc_printers.fb_opt_saved", p0=opt_k, p1=humanize_ppd_choice(opt_k, opt_v)))
            if shell:
                shell.refresh_view()
        return True

    if act == "reset_user_opts" and tail:
        _run_c(["lpoptions", "-x", tail], timeout=1.5)
        invalidate_printers_cache()
        _set_fb(True, t("cc_printers.fb_user_opts_reset", p0=tail))
        if shell:
            shell.refresh_view()
        return True

    # --- Spooler Queue & Jobs ---
    if act == "q_mode" and tail in ("not-completed", "completed"):
        cc_panel.set_state("prn_q_mode", tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "q_cycle_prn":
        snap = get_printers_snapshot()
        names = [""] + [p["name"] for p in snap["printers"]]
        cur_p = str(cc_panel.get_state("prn_q_printer", "") or "")
        nxt = names[(names.index(cur_p) + 1) % len(names)] if cur_p in names else ""
        cc_panel.set_state("prn_q_printer", nxt)
        if shell:
            shell.refresh_view()
        return True

    if act == "q_filter_open":
        cur_q = str(cc_panel.get_state("prn_q_filter", "") or "")
        start_inline_input(
            "prn_q_filter_in",
            shell=shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=40,
            strip_leading_hash=False,
            uppercase=False,
            on_change=lambda v: cc_panel.set_state("prn_q_filter", v),
            on_commit=lambda v: cc_panel.set_state("prn_q_filter", v),
        )
        return True

    if act == "q_filter_clear":
        cc_panel.set_state("prn_q_filter", "")
        if shell:
            shell.refresh_view()
        return True

    if act == "job_cancel" and tail:
        _exec_admin(cc_panel, ["/usr/bin/cancel", tail], t("cc_printers.fb_job_cancelled", p0=tail))
        return True

    if act == "job_hold" and arg1:
        mode = "hold" if arg2 == "1" else "resume"
        _exec_admin(cc_panel, ["/usr/bin/lp", "-i", arg1, "-H", mode], t("cc_printers.fb_job_hold_state", p0=arg1, p1=mode))
        return True

    if act == "job_reprint" and tail:
        _exec_admin(cc_panel, ["/usr/bin/lp", "-i", tail, "-H", "restart"], t("cc_printers.fb_job_reprinted", p0=tail))
        return True

    if act == "job_prio" and arg1 and arg2.isdigit():
        _exec_admin(cc_panel, ["/usr/bin/lp", "-i", arg1, "-q", arg2], t("cc_printers.fb_job_prio_set", p0=arg1, p1=arg2))
        return True

    if act == "job_move" and arg1 and arg2:
        _exec_admin(cc_panel, ["/usr/bin/lpmove", arg1, arg2], t("cc_printers.fb_job_moved", p0=arg1, p1=arg2))
        return True

    if act == "purge_queue":
        set_pending_confirm(cc_panel, "")
        cmd = ["/usr/bin/cancel", "-a"] + ([tail] if tail and tail != "ALL" else [])
        _exec_admin(cc_panel, cmd, t("cc_printers.fb_queue_purged", p0=tail or "ALL"))
        return True

    # --- Discovery, Add Printer Wizard, Virtual PDF & Classes ---
    if act == "scan_devices":
        trigger_device_discovery(cc_panel)
        if shell:
            shell.refresh_view()
        return True

    if act == "scan_all":
        trigger_tcp_lan_scan(cc_panel)
        trigger_device_discovery(cc_panel)
        if shell:
            shell.refresh_view()
        return True

    if act == "wiz_open_manual":
        update_add_draft(name="", desc="", loc="", proto="socket", tcp_port=9100, host="", uri="", drv_mode="auto", drv_query="", ppd_path="")
        cc_panel.set_state("prn_pkg_confirm", "")
        cc_panel.set_state("prn_wiz_target", "manual")
        warm_cups_drivers_async(cc_panel)
        if shell:
            shell.refresh_view()
        return True

    if act == "wiz_close":
        cc_panel.set_state("prn_wiz_target", "")
        cc_panel.set_state("prn_pkg_confirm", "")
        if shell:
            shell.refresh_view()
        return True

    if act == "open_installed" and tail:
        cc_panel.set_state("prn_selected", tail)
        cc_panel.set_state("prn_wiz_target", "")
        cc_panel.set_state("prn_pkg_confirm", "")
        cc_panel.set_subtab("devices")
        if shell:
            shell.refresh_view()
        return True

    if act in ("pick_disc", "wiz_open_disc") and tail.isdigit():
        devs = get_discovered_devices()
        idx = int(tail)
        if 0 <= idx < len(devs):
            dv = devs[idx]
            nd = apply_disc_device_to_draft(dv)
            cc_panel.set_state("prn_disc_sel", nd["uri"])
            cc_panel.set_state("prn_wiz_target", f"disc:{idx}")
            cc_panel.set_state("prn_pkg_confirm", "")
            warm_cups_drivers_async(cc_panel)
            trigger_smart_probe_async(cc_panel, nd["host"], nd["uri"], dv["label"])
            trigger_auto_driver_search_async(cc_panel, dv["label"], nd["uri"])
        if shell:
            shell.refresh_view()
        return True

    if act == "tcp_scan_run":
        trigger_tcp_lan_scan(cc_panel)
        if shell:
            shell.refresh_view()
        return True

    if act == "tcp_subnet_pick" and tail:
        ok_s, norm, _ = parse_tcp_scan_targets(tail)
        if ok_s:
            trigger_tcp_lan_scan(cc_panel, norm)
        if shell:
            shell.refresh_view()
        return True

    if act == "tcp_subnet_custom":
        cur_sub = get_tcp_scan_state()["active_subnet"]

        def _val_sub(v: str):
            s = v.strip()
            if not s:
                return (True, "")
            ok_s, _, _ = parse_tcp_scan_targets(s)
            return (ok_s, "" if ok_s else t("cc_printers.err_bad_subnet"))

        def _commit_sub(v: str):
            s = v.strip()
            if not s:
                STATE.set("printers", "tcp_scan_subnet", get_local_ipv4_subnets()[0])
                return
            ok_s, norm, _ = parse_tcp_scan_targets(s)
            if ok_s:
                trigger_tcp_lan_scan(cc_panel, norm)

        start_inline_input(
            "prn_tcp_subnet_in",
            shell=shell,
            trigger_uri=uri,
            initial_text=cur_sub,
            max_len=36,
            strip_leading_hash=False,
            uppercase=False,
            validator=_val_sub,
            on_commit=_commit_sub,
        )
        return True

    if act in ("pick_tcp_disc", "wiz_open_tcp") and tail.isdigit():
        devs = get_tcp_scan_state()["devices"]
        idx = int(tail)
        if 0 <= idx < len(devs):
            dv = devs[idx]
            cc_panel.set_state("prn_tcp_sel_idx", idx)
            cc_panel.set_state("prn_wiz_target", f"tcp:{idx}")
            cc_panel.set_state("prn_pkg_confirm", "")
            nd = apply_tcp_device_to_draft(dv)
            cc_panel.set_state("prn_disc_sel", nd["uri"])
            warm_cups_drivers_async(cc_panel)
            trigger_smart_probe_async(cc_panel, dv["ip"], nd["uri"], dv["label"], dv.get("open_ports"))
            trigger_auto_driver_search_async(cc_panel, dv["label"], nd["uri"])
        if shell:
            shell.refresh_view()
        return True

    if act == "pick_tcp_port" and arg1.isdigit() and arg2.isdigit():
        devs = get_tcp_scan_state()["devices"]
        idx, port = int(arg1), int(arg2)
        if 0 <= idx < len(devs):
            dv = devs[idx]
            cc_panel.set_state("prn_tcp_sel_idx", idx)
            nd = apply_tcp_device_to_draft(dv, force_port=port)
            cc_panel.set_state("prn_disc_sel", nd["uri"])
        if shell:
            shell.refresh_view()
        return True

    if act == "create_pdf_queue":
        ppd_cand = "/usr/share/cups/model/CUPS-PDF_opt.ppd"
        model_args = ["-P", ppd_cand] if os.path.isfile(ppd_cand) else ["-m", "drv:///sample.drv/generic.ppd"]
        cmd = ["/usr/bin/lpadmin", "-p", "Print_to_PDF", "-E", "-v", "cups-pdf:/", "-D", "Virtual PDF Printer"] + model_args
        _exec_admin(cc_panel, cmd, t("cc_printers.fb_pdf_created"))
        return True

    if act == "draft_tcp_port" and tail.isdigit():
        p_val = int(tail)
        if p_val in (9100, 631, 515):
            d = get_add_draft()
            kw = {"proto": "socket", "tcp_port": p_val}
            if d["host"]:
                kw["uri"] = build_tcp_uri(d["host"], p_val)
            if p_val == 631 and d["drv_mode"] == "db" and d["drv_id"] == "everywhere":
                kw["drv_mode"] = "everywhere"
            update_add_draft(**kw)
            if kw.get("uri"):
                cc_panel.set_state("prn_disc_sel", kw["uri"])
        if shell:
            shell.refresh_view()
        return True

    if act == "draft_proto" and tail:
        d = get_add_draft()
        if tail == "socket":
            tcp_devs = get_tcp_scan_state()["devices"]
            if not d["host"] and tcp_devs:
                nd = apply_tcp_device_to_draft(tcp_devs[0])
                cc_panel.set_state("prn_disc_sel", nd["uri"])
            else:
                new_uri = build_tcp_uri(d["host"], d["tcp_port"]) if d["host"] else ""
                update_add_draft(proto="socket", uri=new_uri)
        else:
            tpl = next((tp for pid, _l, tp in _PROTO_PRESETS if pid == tail), "ipp://{host}:631/ipp/print")
            if tail == "usb":
                new_uri = "usb://"
            elif d["host"] and "{host}" in tpl:
                new_uri = tpl.format(host=d["host"])
            else:
                new_uri = ""
            update_add_draft(proto=tail, uri=new_uri)
        if shell:
            shell.refresh_view()
        return True

    if act == "draft_field" and tail in ("name", "desc", "loc", "host", "uri"):
        d = get_add_draft()
        eid = f"prn_draft_{tail}"

        def _validate_draft(v: str, fld=tail):
            s = v.strip()
            if fld == "name":
                return (bool(_QUEUE_NAME_RE.match(s)), t("cc_printers.err_bad_queue_name"))
            if fld == "uri":
                return (("://" in s and len(s) >= 6), t("cc_printers.err_bad_uri"))
            return (True, "")

        def _commit_draft(v: str, fld=tail):
            s = v.strip()
            cur = get_add_draft()
            kw = {fld: s}
            if fld == "host" and s:
                if cur["proto"] == "socket":
                    kw["uri"] = build_tcp_uri(s, cur["tcp_port"])
                else:
                    tpl = next((tp for pid, _l, tp in _PROTO_PRESETS if pid == cur["proto"]), "")
                    if "{host}" in tpl:
                        kw["uri"] = tpl.format(host=s)
            update_add_draft(**kw)

        start_inline_input(
            eid,
            shell=shell,
            trigger_uri=uri,
            initial_text=d[tail],
            max_len=96 if tail == "uri" else 48,
            strip_leading_hash=False,
            uppercase=False,
            validator=_validate_draft,
            on_commit=_commit_draft,
        )
        return True

    if act == "draft_drv_mode" and tail in ("auto", "everywhere", "db", "raw", "ppd"):
        kw = {"drv_mode": tail}
        if tail == "everywhere":
            kw["drv_id"] = "everywhere"
        elif tail == "raw":
            kw["drv_id"] = "raw"
        elif tail == "db":
            cur_d = get_add_draft()
            warm_cups_drivers_async(cc_panel)
            trigger_auto_driver_search_async(cc_panel, cur_d.get("desc") or cur_d.get("name") or "", cur_d.get("uri", ""))
            cc_panel.set_state("prn_drv_scroll", 0)
            if cur_d["drv_id"] in ("everywhere", "raw", "auto", ""):
                kw["drv_id"] = "drv:///cupsfilters.drv/pwgrast.ppd"
        update_add_draft(**kw)
        if shell:
            shell.refresh_view()
        return True

    if act == "drv_search_open":
        d = get_add_draft()

        def _on_q(v: str):
            cc_panel.set_state("prn_drv_scroll", 0)
            update_add_draft(drv_query=v)

        start_inline_input(
            "prn_drv_picker",
            shell=shell,
            trigger_uri=uri,
            initial_text=d["drv_query"],
            max_len=48,
            strip_leading_hash=False,
            uppercase=False,
            on_change=_on_q,
            on_commit=_on_q,
        )
        return True

    if act == "drv_search_clear":
        cc_panel.set_state("prn_drv_scroll", 0)
        update_add_draft(drv_query="")
        if shell:
            shell.refresh_view()
        return True

    if act == "drv_box_scroll" and tail:
        cur_d = get_add_draft()
        cur_sm = get_smart_probe(cur_d.get("host") or cur_d.get("uri") or "")
        cur_off = int(cc_panel.get_state("prn_drv_scroll", 0) or 0)
        delta = -1 if tail == "up" else (1 if tail == "down" else (-999 if tail == "top" else 999))
        cc_panel.set_state("prn_drv_scroll", max(0, cur_off + delta))
        get_auto_found_drivers(cc_panel, cur_d, cur_sm)
        if shell:
            shell.refresh_view()
        return True

    if act == "drv_pkg_rescan":
        cur_d = get_add_draft()
        cur_sm = get_smart_probe(cur_d.get("host") or cur_d.get("uri") or "")
        m_txt = str(cur_sm.get("model") or cur_d.get("desc") or cur_d.get("name") or "")
        trigger_auto_driver_search_async(cc_panel, m_txt, cur_d.get("uri", ""), force=True)
        if shell:
            shell.refresh_view()
        return True

    if act == "pick_drv" and tail:
        if tail.startswith("ppd:"):
            update_add_draft(drv_mode="ppd", ppd_path=tail[4:])
        else:
            update_add_draft(drv_mode="db", drv_id=tail)
        if shell:
            shell.refresh_view()
        return True

    if act == "pick_pkg_drv" and tail:
        cur_d = get_add_draft()
        cur_sm = get_smart_probe(cur_d.get("host") or cur_d.get("uri") or "")
        _CACHE.pop("drivers_list", None)
        drvs = _load_drivers_blocking()
        _CACHE["drivers_list"] = (time.monotonic(), drvs)
        sig = _parse_printer_signature(f"{cur_sm.get('model') or cur_d.get('desc')} {tail}")
        matched = _match_vendor_driver(f"{tail} {sig['raw_model']}", drvs)
        if matched:
            update_add_draft(drv_mode="db", drv_id=matched[0])
            _set_fb(True, t("cc_printers.fb_pkg_drv_selected", p0=tail, p1=matched[1][:42]))
        else:
            update_add_draft(drv_mode="auto")
            _set_fb(True, t("cc_printers.fb_pkg_installed_ready", p0=tail))
        if shell:
            shell.refresh_view()
        return True

    if act == "cancel_pkg_confirm":
        cc_panel.set_state("prn_pkg_confirm", "")
        if shell:
            shell.refresh_view()
        return True

    if act in ("install_drv_pkg", "install_drv_pkg_force") and tail:
        pkg = tail.strip()
        if act == "install_drv_pkg":
            auto_items = get_auto_found_drivers(cc_panel, get_add_draft()).get("items", ())
            matched_it = next((c for c in auto_items if c.get("pkg") == pkg), None)
            if matched_it and int(matched_it.get("score", 0)) < 90 and cc_panel.get_state("prn_pkg_confirm", "") != pkg:
                cc_panel.set_state("prn_pkg_confirm", pkg)
                if shell:
                    shell.refresh_view()
                return True
        cc_panel.set_state("prn_pkg_confirm", "")
        pm = probe.get_preferred_pkg_manager() or "pacman"
        qpkg = shlex.quote(pkg)
        if pm in ("paru", "yay", "pikaur", "trizen"):
            cmd_line = f"{pm} -S --needed {qpkg}"
        elif pm == "pacman":
            cmd_line = f"sudo pacman -S --needed {qpkg}"
        elif pm == "apt":
            cmd_line = f"sudo apt install {qpkg}"
        elif pm in ("dnf", "zypper"):
            cmd_line = f"sudo {pm} install {qpkg}"
        else:
            cmd_line = f"sudo {pm} add {qpkg}"
        probe.open_terminal(["bash", "-c", f"echo '=== NC-UI Printer Driver Install ({pm}) ==='; {cmd_line}; echo; read -p 'Press Enter to close...'"])
        _watch_pkg_install_async(cc_panel, pkg)
        _set_fb(True, t("cc_printers.fb_pkg_install_launched", p0=pkg, p1=pm))
        if shell:
            shell.refresh_view()
        return True

    if act == "browse_ppd":
        d = get_add_draft()
        cc_panel.request_prompt(
            "cc_prn_ppd",
            t("cc_printers.prompt_ppd_path"),
            d["ppd_path"] or "/usr/share/cups/model/",
            secret=False,
            title=t("cc_printers.prompt_ppd_title"),
            required=True,
            completion=("path_ext", _PPD_EXTS),
        )
        return True

    if act == "draft_toggle" and tail in ("make_default", "shared"):
        d = get_add_draft()
        update_add_draft(**{tail: not d[tail]})
        if shell:
            shell.refresh_view()
        return True

    if act == "draft_reset":
        reset_add_draft()
        cc_panel.set_state("prn_disc_sel", "")
        if shell:
            shell.refresh_view()
        return True

    if act == "probe_uri":
        target = tail or get_add_draft()["uri"] or get_add_draft()["host"]
        ok, msg = probe_printer_host(target)
        _set_fb(ok, msg)
        if shell:
            shell.refresh_view()
        return True

    if act in ("install_printer", "install_printer_test"):
        _start_install_flow(cc_panel, print_test=(act == "install_printer_test"))
        return True

    # --- Classes / Pools (Master-Detail + Toggle Membership) ---
    if act == "class_new_prompt":
        snap = get_printers_snapshot()
        if not snap["printers"]:
            _set_fb(False, t("cc_printers.no_printers_hint"))
            if shell:
                shell.refresh_view()
            return True
        first_p = snap["default"] or snap["printers"][0]["name"]
        cc_panel.request_prompt(
            f"cc_prn_cls|{first_p}",
            t("cc_printers.prompt_new_class", p0=first_p),
            "Office_Pool",
            secret=False,
            title=t("cc_printers.prompt_class_title"),
            required=True,
        )
        return True

    if act == "class_toggle_member" and arg1 and arg2:
        classes = get_printer_classes()
        cl = next((c for c in classes if c["name"] == arg1), None)
        in_cls = bool(cl and arg2 in cl["members"])
        flag = "-r" if in_cls else "-c"
        _exec_admin(cc_panel, ["/usr/bin/lpadmin", "-p", arg2, flag, arg1], t("cc_printers.fb_class_updated", p0=arg1))
        return True

    if act == "class_delete" and tail:
        set_pending_confirm(cc_panel, "")
        _exec_admin(cc_panel, ["/usr/bin/lpadmin", "-x", tail], t("cc_printers.fb_class_deleted", p0=tail))
        return True

    # --- Global CUPS Server Policies (`cupsctl`) ---
    if act == "cupsctl" and arg1 in ("share-printers", "remote-any", "remote-admin", "user-cancel-any", "debug-logging"):
        if not get_cups_daemon_state()["scheduler_up"]:
            _set_fb(False, t("cc_printers.err_spooler_down"))
            if shell:
                shell.refresh_view()
            return True
        flag = f"--{arg1}" if arg2 == "1" else f"--no-{arg1}"
        _exec_admin(cc_panel, ["/usr/bin/cupsctl", flag], t("cc_printers.fb_cupsctl_saved", p0=flag))
        return True

    return False


def handle_cc_printers_prompt(cc_panel, action_id: str, text: str) -> bool:
    if not action_id.startswith("cc_prn_"):
        return False
    val = str(text or "").strip()
    shell = getattr(cc_panel, "shell", None)

    if action_id == "cc_prn_sudo":
        cmd = _PENDING_SUDO.get("cmd")
        ok_msg = str(_PENDING_SUDO.get("ok_msg", t("common.ok")))
        after_fn = _PENDING_SUDO.get("after_fn")
        _PENDING_SUDO.clear()
        if not cmd or not val:
            return True
        try:
            res = subprocess.run(
                ["sudo", "-k", "-S", "-p", ""] + list(cmd),
                input=f"{val}\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=12.0,
            )
            ok = res.returncode == 0
            out = (res.stdout or "").strip()
        except Exception as e:
            ok, out = False, str(e)
        invalidate_printers_cache()
        if ok and callable(after_fn):
            after_fn()
        _set_fb(ok, ok_msg if ok else (out or t("cc_printers.err_cmd_failed"))[:110])
        if shell:
            shell.refresh_view()
        return True

    if action_id == "cc_prn_sudo_install":
        info = dict(_PENDING_INSTALL_SUDO)
        _PENDING_INSTALL_SUDO.clear()
        if not info or not val:
            return True
        _start_install_flow(
            cc_panel,
            print_test=bool(info.get("print_test", False)),
            sudo_pass=val,
            draft_override=info.get("d"),
            smart_override=info.get("smart"),
        )
        return True

    if action_id.startswith("cc_prn_file|"):
        prn = action_id.split("|", 1)[1]
        exp = os.path.expanduser(val)
        if not os.path.isfile(exp):
            _set_fb(False, t("cc_printers.err_file_not_found", p0=val))
            return True
        update_quick_print_draft(file=val)
        if getattr(cc_panel, "active_subtab", "") == "queue":
            submit_quick_print(cc_panel, prn, file_override=val)
        else:
            cc_panel.set_state("prn_sec_qprint", True)
            _set_fb(True, t("cc_printers.fb_qp_file_ready", p0=os.path.basename(exp)))
        return True

    if action_id == "cc_prn_qp_setfile":
        exp = os.path.expanduser(val)
        if not os.path.isfile(exp):
            _set_fb(False, t("cc_printers.err_file_not_found", p0=val))
            return True
        update_quick_print_draft(file=val)
        _set_fb(True, t("cc_printers.fb_qp_file_ready", p0=os.path.basename(exp)))
        return True

    if action_id.startswith("cc_prn_uri|"):
        prn = action_id.split("|", 1)[1]
        if "://" not in val or len(val) < 6:
            _set_fb(False, t("cc_printers.err_bad_uri"))
            return True
        _exec_admin(cc_panel, ["/usr/bin/lpadmin", "-p", prn, "-E", "-v", val], t("cc_printers.fb_uri_updated", p0=prn))
        return True

    if action_id.startswith("cc_prn_clone|"):
        src_prn = action_id.split("|", 1)[1]
        if not _QUEUE_NAME_RE.match(val):
            _set_fb(False, t("cc_printers.err_bad_queue_name"))
            return True
        snap = get_printers_snapshot()
        src = next((p for p in snap["printers"] if p["name"] == src_prn), None)
        if not src or not src["uri"]:
            _set_fb(False, t("cc_printers.err_cmd_failed"))
            return True
        src_ppd = f"/etc/cups/ppd/{src_prn}.ppd"
        drv_args = ["-P", src_ppd] if os.path.isfile(src_ppd) else ["-m", "everywhere"]
        cmd = ["/usr/bin/lpadmin", "-p", val, "-E", "-v", src["uri"]] + drv_args
        if src["description"]:
            cmd += ["-D", f"{src['description']} (Clone)"]
        if src["location"]:
            cmd += ["-L", src["location"]]
        _exec_admin(
            cc_panel,
            cmd,
            t("cc_printers.fb_prn_cloned", p0=src_prn, p1=val),
            after_fn=lambda: cc_panel.set_state("prn_selected", val),
        )
        return True

    if action_id == "cc_prn_ppd":
        v_ok, v_msg = validate_ppd_file(val)
        update_add_draft(drv_mode="ppd", ppd_path=val)
        _set_fb(v_ok, v_msg)
        return True

    if action_id == "cc_prn_edit_ppd":
        v_ok, v_msg = validate_ppd_file(val)
        update_drv_edit_state(mode="ppd", ppd_path=val)
        _set_fb(v_ok, v_msg)
        return True

    if action_id.startswith("cc_prn_cls|"):
        prn = action_id.split("|", 1)[1]
        if not _QUEUE_NAME_RE.match(val):
            _set_fb(False, t("cc_printers.err_bad_queue_name"))
            return True
        _exec_admin(
            cc_panel,
            ["/usr/bin/lpadmin", "-p", prn, "-c", val],
            t("cc_printers.fb_class_updated", p0=val),
            after_fn=lambda: cc_panel.set_state("prn_selected_class", val),
        )
        return True

    return False
