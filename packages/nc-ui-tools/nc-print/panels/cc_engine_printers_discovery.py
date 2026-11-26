#!/usr/bin/env python3
"""NC-UI Printers — Device Discovery, LAN Scan & Driver Matching."""

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


_SMART_PROBE_CACHE: dict[str, dict] = {}
_AUTO_DRV_PKG_CACHE: dict[str, tuple[float, list[dict]]] = {}
_AUTO_DRV_LOADING: set[str] = set()
_AUTO_DRV_LOCK = threading.Lock()
_DISC_LOCK = False
_TCP_SCAN_LOCK = threading.Lock()
_TCP_SCAN_RUNNING = False
_DRV_LOCK = threading.Lock()
_DRV_LOADING = False

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
    _core()._set_fb(True, t("cc_printers.fb_scanning"))

    def _scan() -> list[dict]:
        seen: set[str] = set()
        items: list[dict] = []
        avahi_up = _core().get_cups_daemon_state()["avahi_active"]

        def _add(uri: str, label: str, source: str) -> None:
            u = uri.strip()
            if not u or u in seen or "://" not in u:
                return
            if u.endswith("://") and u != "usb://":
                return
            seen.add(u)
            items.append({
                "uri": u,
                "proto": _core()._classify_uri(u),
                "label": label.strip() or u,
                "source": source,
            })

        if avahi_up and probe.has_bin("driverless"):
            ok, out, _ = _core()._run_c(["driverless"], timeout=3.5)
            if ok and out:
                for ln in out.splitlines():
                    u = ln.strip()
                    if "://" in u:
                        _add(u, u.split("/")[-1] or "IPP Everywhere Printer", "driverless")

        lpinfo_bin = _core()._cups_bin("lpinfo")
        if _core().get_cups_daemon_state()["scheduler_up"] and lpinfo_bin:
            cmd_v = [lpinfo_bin, "-v"] if avahi_up else [lpinfo_bin, "--exclude-schemes", "dnssd,driverless", "-v"]
            ok, out, _ = _core()._run_c(cmd_v, timeout=4.0)
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
                ok, out, _ = _core()._run_c(["avahi-browse", "-rtpk", svc], timeout=2.5)
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
        _core()._set_fb(True, t("cc_printers.fb_scan_done", p0=len(found)))
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
        ok, out, _ = _core()._run_c(["ip", "-j", "-4", "addr", "show", "up", "scope", "global"], timeout=1.5)
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
        return (primary or secondary or ["192.168.1.0/24"]), local_ips

    return _core()._cached("local_ipv4_info", 15.0, _load)


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
    label = str(dv.get("label", "") or t("cc_printers.network_printer_ip", p0=ip)).strip()
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
    raw_lbl = re.sub(r"[^A-Za-z0-9_-]+", "_", str(label or "").split("(")[0].strip()).strip("_-")[:32]
    loc_net_pfx = re.sub(r"[^a-z0-9_-]+", "_", t("cc_printers.network_printer_ip", p0="").split("(")[0].strip().lower()).strip("_-")
    low = raw_lbl.lower()
    if not raw_lbl or low.startswith("network_printer") or (loc_net_pfx and low.startswith(loc_net_pfx)):
        raw_lbl = re.sub(r"[^A-Za-z0-9_-]+", "_", fallback).strip("_-")[:32] or "Printer"
    return raw_lbl


def apply_tcp_device_to_draft(dv: dict, force_port: int = 0) -> dict:
    nd = _normalize_tcp_device(dv)
    port = force_port if force_port in (9100, 631, 515) else int(nd["tcp_port"])
    ip = nd["ip"]
    return _core().update_add_draft(
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
    return _core().update_add_draft(
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
    if 631 in open_ports and probe.has_bin("ipptool") and os.path.isfile(_core()._IPP_ATTRS_TEST):
        ok, out, _ = _core()._run_c(["ipptool", "-T", "1", "-t", f"ipp://{ip}:631/ipp/print", _core()._IPP_ATTRS_TEST], timeout=1.3)
        if ok and out:
            for key in ("printer-make-and-model", "printer-info", "printer-name"):
                m = re.search(rf"{key}\s*\([^)]+\)\s*=\s*(.+)", out)
                if m and m.group(1).strip():
                    return m.group(1).strip().strip('"')[:44]

    if probe.has_bin("snmpget"):
        ok, out, _ = _core()._run_c(
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
    return t("cc_printers.network_printer_ip", p0=ip)


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
        _core()._set_fb(False, t("cc_printers.err_bad_subnet"))
        return

    with _TCP_SCAN_LOCK:
        if _TCP_SCAN_RUNNING:
            return
        _TCP_SCAN_RUNNING = True

    STATE.set("printers", "tcp_scan_subnet", norm_spec)
    _core()._set_fb(True, t("cc_printers.fb_tcp_scanning", p0=norm_spec))
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
        _core()._set_fb(True, t("cc_printers.fb_tcp_scan_done", p0=len(found), p1=norm_spec))
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()
        return False

    probe.run_async(_scan, _done)


def _load_drivers_blocking() -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for drv_id, lbl in _core()._BUILTIN_DRIVERS:
        seen.add(drv_id)
        out.append((drv_id, lbl))
    lpstat_bin = _core()._cups_bin("lpstat")
    lpinfo_bin = _core()._cups_bin("lpinfo")
    ok_sched, out_sched, _ = _core()._run_c([lpstat_bin, "-r"], timeout=0.7) if lpstat_bin else (False, "", "")
    if ok_sched and "is running" in (out_sched or "").lower() and lpinfo_bin:
        avahi_up = os.path.exists("/run/avahi-daemon/socket")
        cmd_m = [lpinfo_bin, "-m"] if avahi_up else [lpinfo_bin, "--exclude-schemes", "dnssd,driverless", "-m"]
        ok, raw, _ = _core()._run_c(cmd_m, timeout=4.0)
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
    hit = _core()._CACHE.get("drivers_list")
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
        _core()._CACHE["drivers_list"] = (time.monotonic(), res)
        if cc_panel and getattr(cc_panel, "shell", None):
            cc_panel.shell.refresh_view()
        return False

    probe.run_async(_load_drivers_blocking, _done)


def get_cups_drivers(cc_panel=None) -> list[tuple[str, str]]:
    hit = _core()._CACHE.get("drivers_list")
    if hit is not None and (time.monotonic() - hit[0]) < 120.0:
        return hit[1]
    warm_cups_drivers_async(cc_panel)
    return list(_core()._BUILTIN_DRIVERS)


def search_cups_drivers(query: str, limit: int = 6, cc_panel=None) -> list[tuple[str, str]]:
    items = [(drv, lbl, f"{drv} {lbl}".lower()) for drv, lbl in get_cups_drivers(cc_panel)]
    return rank_candidates(items, query, limit=limit)


def _match_vendor_driver(model_name: str, drivers: list[tuple[str, str]]) -> tuple[str, str] | None:
    norm_txt = re.sub(r"[^a-z0-9]+", " ", str(model_name or "").lower()).strip()
    if not norm_txt:
        return None
    toks = norm_txt.split()
    model_codes = [tk for tk in toks if len(tk) >= 3 and any(c.isdigit() for c in tk)]
    brands = [b for b in _PRINTER_BRANDS if b in toks]
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


def get_smart_probe(host_or_uri: str) -> dict:
    k = str(host_or_uri or "").strip()
    return dict(_SMART_PROBE_CACHE.get(k, {}))


def _synthesize_driverless_ppd(host_or_uri: str, queue_hint: str) -> str:
    ipp_uri = host_or_uri if host_or_uri.startswith(("ipp://", "ipps://")) else f"ipp://{host_or_uri}:631/ipp/print"
    if not probe.has_bin("driverless"):
        return ""
    ok_dl, ppd_raw, _ = _core()._run_c(["driverless", ipp_uri], timeout=3.5)
    if not ok_dl or "*PPD-Adobe" not in (ppd_raw or ""):
        return ""
    safe_k = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(queue_hint or "printer"))[:48]
    cache_dir = get_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    ppd_path = os.path.join(cache_dir, f"autogen_{safe_k}.ppd")
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
            if has_ipp and ipp_target and probe.has_bin("ipptool") and os.path.isfile(_core()._IPP_ATTRS_TEST):
                ok_i, out_i, _ = _core()._run_c(["ipptool", "-T", "2", "-t", ipp_target, _core()._IPP_ATTRS_TEST], timeout=2.5)
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
            _core()._CACHE["drivers_list"] = (time.monotonic(), drvs)
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
_CURATED_BRAND_PKGS_BY_FAMILY: dict[str, dict[str, list[tuple[str, str, str]]]] = {
    "arch": {
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
    },
    "debian": {
        "brother": [("printer-driver-brlaser", "apt", "CUPS driver for Brother laser & LED printers")],
        "hp": [
            ("hplip", "apt", "HP Linux Imaging & Printing drivers"),
            ("printer-driver-hpcups", "apt", "HP Linux Printing and Imaging - CUPS Raster driver"),
        ],
        "epson": [("printer-driver-escpr", "apt", "Epson ESC/P-R inkjet CUPS driver")],
        "canon": [("printer-driver-gutenprint", "apt", "Printer drivers for CUPS")],
        "samsung": [("printer-driver-splix", "apt", "Driver for Samsung and Xerox SPL2/SPLc laser printers")],
    },
    "fedora": {
        "brother": [("printer-driver-brlaser", "dnf", "CUPS driver for Brother laser & LED printers")],
        "hp": [("hplip", "dnf", "HP Linux Imaging & Printing drivers")],
        "epson": [
            ("epson-inkjet-printer-escpr", "dnf", "Epson ESC/P-R inkjet CUPS driver"),
            ("epson-inkjet-printer-escpr2", "dnf", "Epson ESC/P-R 2 inkjet CUPS driver"),
        ],
        "samsung": [("splix", "dnf", "CUPS driver for Samsung SPL printers")],
    },
    "suse": {
        "brother": [("brlaser", "zypper", "CUPS driver for Brother laser & LED printers")],
        "hp": [("hplip", "zypper", "HP Linux Imaging & Printing drivers")],
        "epson": [("epson-inkjet-printer-escpr", "zypper", "Epson ESC/P-R inkjet CUPS driver")],
        "samsung": [("splix", "zypper", "CUPS driver for Samsung SPL printers")],
    },
}
_UNIVERSAL_PKGS_BY_FAMILY: dict[str, tuple[tuple[str, str, str], ...]] = {
    "arch": (
        ("foomatic-db-ppds", "extra", "Foomatic manufacturer PPD files"),
        ("foomatic-db-nonfree-ppds", "extra", "Foomatic non-free printer PPDs"),
        ("gutenprint", "extra", "Top quality CUPS printer drivers"),
    ),
    "debian": (
        ("foomatic-db-compressed-ppds", "apt", "Foomatic manufacturer PPD files"),
        ("openprinting-ppds", "apt", "OpenPrinting printer support PPDs"),
        ("printer-driver-all", "apt", "Printer drivers metapackage"),
    ),
    "fedora": (
        ("foomatic-db-ppds", "dnf", "Foomatic manufacturer PPD files"),
        ("gutenprint-cups", "dnf", "CUPS drivers from Gutenprint"),
    ),
    "suse": (
        ("foomatic-filters", "zypper", "Foomatic printer filters"),
        ("gutenprint", "zypper", "Top quality CUPS printer drivers"),
    ),
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
        if "foomatic" in iid or "openprinting-ppds" in iid:
            return 65
        if "printer-driver-all" in iid:
            return 64
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
    model = sig["raw_model"] or sig["full_code"].upper() or t("cc_printers.this_printer")
    brand = sig["brand"].title() if sig["brand"] else t("cc_printers.generic_printer_brand")
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
    elif pm == "zypper":
        for ln in out.splitlines():
            if "|" not in ln:
                continue
            cols = [c.strip() for c in ln.split("|")]
            if len(cols) < 3 or cols[0].lower() in ("s", "status") or cols[0].startswith("-"):
                continue
            if len(cols) > 3 and cols[3].strip().lower() == "srcpackage":
                continue
            pkg = cols[1]
            if not pkg or pkg.startswith("-"):
                continue
            desc = cols[2].strip()
            items.append({"pkg": pkg, "repo": "zypper", "ver": "", "installed": cols[0].startswith("i"), "desc": desc})
    else:
        for ln in out.splitlines():
            s = ln.strip()
            if not s:
                continue
            if pm in ("dnf", "dnf5", "yum"):
                if " : " not in s:
                    continue
                pkg_p, desc = s.split(" : ", 1)
                pkg = re.sub(r"\.(noarch|x86_64|aarch64|i\d86|src)$", "", pkg_p.strip())
            elif " - " in s:
                pkg_p, desc = s.split(" - ", 1)
                pkg = pkg_p.split()[0].strip()
            elif " : " in s:
                pkg_p, desc = s.split(" : ", 1)
                pkg = re.sub(r"\.(noarch|x86_64|aarch64|i\d86|src)$", "", pkg_p.strip())
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
        _ok, out, _ = _core()._run_c(["pacman", "-Qq"] + clean, timeout=1.2)
        return {ln.strip() for ln in (out or "").splitlines() if ln.strip()}
    if pm in ("apt", "apt-get", "nala") and probe.has_bin("dpkg-query"):
        _ok, out, _ = _core()._run_c(["dpkg-query", "-W", "-f=${Package}\n"] + clean, timeout=1.2)
        return {ln.strip() for ln in (out or "").splitlines() if ln.strip()}
    if pm in ("dnf", "dnf5", "yum", "zypper") and probe.has_bin("rpm"):
        _ok, out, _ = _core()._run_c(["rpm", "-q", "--qf", "%{NAME}\n"] + clean, timeout=1.2)
        return {ln.strip() for ln in (out or "").splitlines() if ln.strip() and "not installed" not in ln.lower()}
    return set()


def _search_pkg_drivers_blocking(sig: dict) -> list[dict]:
    pm = probe.get_preferred_pkg_manager()
    if not pm:
        return []
    family = probe.get_os_release().get("family", "unknown")
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
            ok_s, out_s, _ = _core()._run_c([tool, "-Ss", q], timeout=3.8 if idx == 0 else 3.0)
            if ok_s and out_s:
                for it in _parse_pkg_search_lines(tool, out_s):
                    _add_raw(it)
            elif tool != "pacman" and probe.has_bin("pacman"):
                ok_p, out_p, _ = _core()._run_c(["pacman", "-Ss", q], timeout=1.8)
                if ok_p and out_p:
                    for it in _parse_pkg_search_lines("pacman", out_p):
                        _add_raw(it)
    elif pm in ("apt", "apt-get", "nala") and probe.has_bin("apt-cache"):
        apt_qs = [q for q in (sig["full_code"], sig["brand"] or "printer-driver", "foomatic", "printer-driver-all") if q]
        for q in apt_qs:
            ok_a, out_a, _ = _core()._run_c(["apt-cache", "search", q], timeout=2.5)
            if ok_a and out_a:
                for it in _parse_pkg_search_lines(pm, out_a):
                    _add_raw(it)
    elif pm in ("dnf", "dnf5", "yum", "zypper"):
        for q in (sig["brand"] or "cups", "foomatic"):
            ok_d, out_d, _ = _core()._run_c([pm, "search", q], timeout=3.5)
            if ok_d and out_d:
                for it in _parse_pkg_search_lines(pm, out_d):
                    _add_raw(it)

    # Curated & universal fallbacks keyed by OS family
    if family == "arch" and sig["brand"] == "brother" and sig["full_code"]:
        _add_raw({"pkg": f"brother-{sig['full_code']}", "repo": "aur", "ver": "", "installed": False, "desc": f"LPR & CUPS driver for {sig['raw_model']}"})
    for pk, rp, ds in _CURATED_BRAND_PKGS_BY_FAMILY.get(family, {}).get(sig["brand"], ()):
        _add_raw({"pkg": pk, "repo": rp, "ver": "", "installed": False, "desc": ds})
    if not raw_pkgs or len(raw_pkgs) < 3:
        for pk, rp, ds in _UNIVERSAL_PKGS_BY_FAMILY.get(family, ()):
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
    pm = probe.get_preferred_pkg_manager() or ""

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
            "label": t("cc_printers.auto_ppd_lbl", p0=sig["full_code"].upper() or sig["raw_model"][:18]),
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
    pm = probe.get_preferred_pkg_manager() or ""
    if not pm:
        return

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
                _core()._CACHE["drivers_list"] = (time.monotonic(), drvs)
            else:
                _core()._CACHE.pop("drivers_list", None)
                drvs = _load_drivers_blocking()
                _core()._CACHE["drivers_list"] = (time.monotonic(), drvs)
            for entry in _SMART_PROBE_CACHE.values():
                if isinstance(entry, dict) and entry.get("model"):
                    entry["matched_drv"] = _match_vendor_driver(entry["model"], drvs)
            _AUTO_DRV_PKG_CACHE.clear()
            _core()._set_fb(True, t("cc_printers.fb_pkg_installed_ready", p0=pkg_name))
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
    ok, out, err = _core()._run_c(["cupstestppd", "-q", exp], timeout=2.5)
    if ok:
        return True, t("cc_printers.fb_ppd_valid", p0=os.path.basename(exp))
    detail = (err or out or t("cc_printers.ppd_invalid_badge")).splitlines()[0][:72]
    return False, t("cc_printers.err_ppd_invalid", p0=detail)

def _core():
    import panels.cc_engine_printers as m
    return m
