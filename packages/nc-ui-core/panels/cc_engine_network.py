#!/usr/bin/env python3
import copy
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.request

from core.config import CONFIG, THEME, get_cache_dir, get_config_dir, get_network_show_bar_speed
from core.state import STATE
from core.i18n import t
from core import probe, log
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    FoldToggle,
    SelectPill,
    StateBadge,
    Txt,
    make_feedback_cards,
    set_feedback,
    start_inline_input,
)
from ui.markup import format_speed
from panels.base import BasePanel

PROXY_ENV_FILE = os.path.join(get_config_dir(), "proxy.env")

SIGNAL_BARS = ["\u2581___", "\u2581\u2583__", "\u2581\u2583\u2585_", "\u2581\u2583\u2585\u2587"]
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

_NET_LOCK = threading.Lock()
_BG_WORKER_RUNNING = False


def _cache_path(name: str, ensure_dir: bool = False) -> str:
    d = get_cache_dir()
    if ensure_dir:
        os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def get_proxy_env_path() -> str:
    return os.path.join(get_config_dir(), "proxy.env")


_NET_STATE = {
    "initialized": False,
    "nm_running": True,
    "ip_map": {},
    "ip6_map": {},
    "gw_map": {},
    "routes": [],
    "nm_profiles": {},
    "wifi": {
        "available": True,
        "has_hw": True,
        "enabled": False,
        "powersave": "default",
        "active_band": "auto",
        "active_mac_priv": "",
        "networks": [],
        "saved": [],
    },
    "bt": {"available": True, "has_controller": False, "powered": False, "scanning": False, "paired": [], "discovered": []},
    "vpns": [],
    "proxy": {"mode": "none", "http_host": "", "http_port": 8080, "socks_host": "", "socks_port": 1080, "autoconfig_url": ""},
    "wan": {"ip": t("network.detecting"), "badge": "", "badge_style": "info"},
    "pings": {},
    "saved_wifi_ssids": set(),
    "ts": {
        "ip": 0.0,
        "nm": 0.0,
        "wifi": 0.0,
        "bt": 0.0,
        "vpn": 0.0,
        "wan": 0.0,
        "ping": 0.0,
    },
}


def is_resolved_wan_ip(ip_str: str) -> bool:
    try:
        ipaddress.ip_address(str(ip_str or "").strip())
        return True
    except ValueError:
        return False


def _spaced_rows(rows: list[list]) -> list[list]:
    """Inserisce una riga vuota di respiro verticale tra ogni riga dati di una tabella."""
    if not rows:
        return []
    n_cols = len(rows[0])
    spacer = [" "] * n_cols
    out = []
    for idx, r in enumerate(rows):
        if idx > 0:
            out.append(list(spacer))
        out.append(r)
    return out


def validate_network_field(field: str, raw_val: str) -> tuple[bool, str, str]:
    s = str(raw_val or "").strip()
    if field == "ip_cidr":
        if not s:
            return True, "", ""
        if "/" not in s:
            s = f"{s}/24"
        try:
            iface_ip = ipaddress.IPv4Interface(s)
            if iface_ip.ip.is_unspecified or iface_ip.ip.is_multicast:
                return False, "", t("cc_network.err_ipv4_reserved", p0=s)
            return True, str(iface_ip), ""
        except ValueError:
            return False, "", t("cc_network.err_ipv4_fmt", p0=raw_val)

    if field == "gateway":
        if not s:
            return True, "", ""
        try:
            gw_ip = ipaddress.IPv4Address(s)
            if gw_ip.is_unspecified or gw_ip.is_multicast:
                return False, "", t("cc_network.err_gw_invalid", p0=s)
            return True, str(gw_ip), ""
        except ValueError:
            return False, "", t("cc_network.err_gw_fmt", p0=raw_val)

    if field in ("dns", "ip6_dns"):
        if not s:
            return True, "", ""
        addr_cls = ipaddress.IPv4Address if field == "dns" else ipaddress.IPv6Address
        err_key = "cc_network.err_dns4_fmt" if field == "dns" else "cc_network.err_dns6_fmt"
        clean_ips = []
        for tk in (tok.strip() for tok in re.split(r"[,;\s]+", s) if tok.strip()):
            try:
                clean_ips.append(str(addr_cls(tk)))
            except ValueError:
                return False, "", t(err_key, p0=tk)
        return True, ", ".join(clean_ips), ""

    if field == "mtu":
        if not s or s.lower() == "auto":
            return True, "auto", ""
        if not s.isdigit() or not (68 <= int(s) <= 9216):
            return False, "", t("cc_network.err_mtu_range", p0=s)
        return True, s, ""

    if field == "cloned_mac":
        if not s:
            return True, "", ""
        if s.lower() in ("permanent", "preserve", "random", "stable"):
            return True, s.lower(), ""
        if not _MAC_RE.match(s):
            return False, "", t("cc_network.err_mac_fmt", p0=s)
        return True, s.upper(), ""

    if field == "static_route":
        parts = s.split()
        if not parts:
            return False, "", t("cc_network.err_route_empty")
        try:
            net_obj = ipaddress.IPv4Network(parts[0], strict=False)
            norm = [str(net_obj)]
            if len(parts) >= 2:
                norm.append(str(ipaddress.IPv4Address(parts[1])))
            if len(parts) >= 3:
                if not parts[2].isdigit() or int(parts[2]) < 0:
                    return False, "", t("cc_network.err_route_metric", p0=parts[2])
                norm.append(parts[2])
            return True, " ".join(norm), ""
        except ValueError as e:
            return False, "", t("cc_network.err_route_syntax", p0=s, p1=e)

    return True, s, ""


def read_proc_net_dev() -> dict:
    res = {}
    try:
        with open("/proc/net/dev", "r", encoding="utf-8") as f:
            for line in f.readlines()[2:]:
                if ":" not in line:
                    continue
                name, stats_raw = line.split(":", 1)
                name = name.strip()
                if name == "lo":
                    continue
                fields = stats_raw.split()
                if len(fields) >= 9:
                    res[name] = {"rx": int(fields[0]), "tx": int(fields[8])}
    except Exception as e:
        log.error(f"read_proc_net_dev error: {e}")
    return res


def _read_sysfs_str(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return default


def _load_fast_disk_cache() -> None:
    if _NET_STATE["initialized"]:
        return
    fast_f = _cache_path("net_fast_cache.json")
    wan_f = _cache_path("wan_cache.json")
    wifi_f = _cache_path("net_wifi_cache.json")
    ping_f = _cache_path("net_ping_cache.json")
    if os.path.exists(fast_f):
        try:
            with open(fast_f, "r", encoding="utf-8") as f:
                data = json.load(f)
            with _NET_LOCK:
                _NET_STATE["ip_map"] = data.get("ip_map", {})
                _NET_STATE["ip6_map"] = data.get("ip6_map", {})
                _NET_STATE["gw_map"] = data.get("gw_map", {})
                _NET_STATE["bt"] = data.get("bt", _NET_STATE["bt"])
                _NET_STATE["initialized"] = True
        except Exception:
            pass
    if os.path.exists(wan_f):
        try:
            with open(wan_f, "r", encoding="utf-8") as f:
                w = json.load(f)
                if w.get("ip"):
                    _NET_STATE["wan"] = _compute_wan_badge(w["ip"])
                    _NET_STATE["ts"]["wan"] = float(w.get("ts", 0.0))
        except Exception:
            pass
    if os.path.exists(wifi_f):
        try:
            with open(wifi_f, "r", encoding="utf-8") as f:
                nets = json.load(f)
                if isinstance(nets, list):
                    _NET_STATE["wifi"]["networks"] = nets
                    _NET_STATE["wifi"]["enabled"] = True
        except Exception:
            pass
    if os.path.exists(ping_f):
        try:
            with open(ping_f, "r", encoding="utf-8") as f:
                p = json.load(f)
                _NET_STATE["pings"] = p.get("results", {})
                _NET_STATE["ts"]["ping"] = float(p.get("ts", 0.0))
        except Exception:
            pass


def _save_fast_disk_cache() -> None:
    try:
        with _NET_LOCK:
            payload = {
                "ts": time.time(),
                "ip_map": _NET_STATE["ip_map"],
                "ip6_map": _NET_STATE["ip6_map"],
                "gw_map": _NET_STATE["gw_map"],
                "bt": _NET_STATE["bt"],
            }
        with open(_cache_path("net_fast_cache.json", ensure_dir=True), "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def get_interfaces_fast(speeds: dict = None) -> list[dict]:
    _load_fast_disk_cache()
    speeds = speeds or {}
    proc_devs = read_proc_net_dev()
    with _NET_LOCK:
        ip_map = dict(_NET_STATE["ip_map"])
        ip6_map = dict(_NET_STATE["ip6_map"])
        gw_map = dict(_NET_STATE["gw_map"])

    sys_net = "/sys/class/net"
    try:
        sys_ifaces = [d for d in os.listdir(sys_net) if d != "lo"] if os.path.exists(sys_net) else []
    except Exception:
        sys_ifaces = []

    all_names = sorted(set(list(proc_devs.keys()) + sys_ifaces + list(ip_map.keys())))
    result = []
    for name in all_names:
        base_p = f"/sys/class/net/{name}"
        is_wifi = name.startswith(("wl", "wlan")) or os.path.exists(f"{base_p}/wireless")
        is_vpn = name.startswith(("wg", "tun", "tap", "ipsec", "ppp", "tailscale"))
        is_br = name.startswith(("br", "virbr", "docker", "veth")) or os.path.exists(f"{base_p}/bridge")
        kind = "wifi" if is_wifi else ("vpn" if is_vpn else ("bridge" if is_br else "eth"))

        oper = _read_sysfs_str(f"{base_p}/operstate", "down").lower()
        flags_hex = _read_sysfs_str(f"{base_p}/flags", "0x0")
        try:
            is_up_flag = bool(int(flags_hex, 16) & 0x1)
        except ValueError:
            is_up_flag = False
        up = (oper == "up") or (oper == "unknown" and is_up_flag) or (name in ip_map and oper != "down")

        mac = _read_sysfs_str(f"{base_p}/address", "N/D")
        try:
            mtu = int(_read_sysfs_str(f"{base_p}/mtu", "1500"))
        except ValueError:
            mtu = 1500

        link_speed = "Wi-Fi" if is_wifi else ("Tunnel" if is_vpn else ("Bridge" if is_br else "N/D"))
        if kind == "eth" and up:
            sp_raw = _read_sysfs_str(f"{base_p}/speed", "")
            try:
                sp_val = int(sp_raw)
                if sp_val > 0:
                    link_speed = f"{sp_val / 1000.0:.1f}G" if sp_val >= 1000 else f"{sp_val}M"
            except ValueError:
                pass

        sp = speeds.get(name, {"rx": 0.0, "tx": 0.0})
        tot = proc_devs.get(name, {"rx": 0, "tx": 0})
        result.append({
            "name": name,
            "kind": kind,
            "up": up,
            "ip": ip_map.get(name, "N/D"),
            "ip6": ip6_map.get(name, ""),
            "mac": mac,
            "gw": gw_map.get(name, ""),
            "mtu": mtu,
            "link_speed": link_speed,
            "rx_rate": sp["rx"],
            "tx_rate": sp["tx"],
            "rx_total": tot["rx"],
            "tx_total": tot["tx"],
        })
    return result


def _is_nm_running() -> bool:
    if not probe.has_bin("nmcli"):
        return False
    ok, out = probe.run_cmd(["nmcli", "-t", "-f", "RUNNING", "general"], timeout=1.0)
    return bool(ok and "running" in out.lower())


def _collect_ip_and_routes() -> tuple[dict, dict, dict, list[str]]:
    ip_map, ip6_map, gw_map, routes = {}, {}, {}, []
    if not probe.has_bin("ip"):
        return ip_map, ip6_map, gw_map, routes
    ok, out = probe.run_cmd(["ip", "-j", "addr"], timeout=1.2)
    if ok and out:
        try:
            for item in json.loads(out):
                ifname = item.get("ifname", "")
                if not ifname or ifname == "lo":
                    continue
                for a in item.get("addr_info", []):
                    fam = a.get("family")
                    if fam == "inet" and ifname not in ip_map:
                        ip_map[ifname] = f"{a['local']}/{a['prefixlen']}"
                    elif fam == "inet6" and a.get("scope") == "global" and ifname not in ip6_map:
                        ip6_map[ifname] = f"{a['local']}/{a['prefixlen']}"
        except Exception:
            pass

    ok_r, out_r = probe.run_cmd(["ip", "-j", "route"], timeout=1.2)
    if ok_r and out_r:
        try:
            for r in json.loads(out_r):
                if r.get("dst") == "default":
                    dev = r.get("dev")
                    if dev and dev not in gw_map:
                        gw_map[dev] = r.get("gateway", "N/D")
        except Exception:
            pass

    ok_rt, out_rt = probe.run_cmd(["ip", "route"], timeout=1.0)
    if ok_rt and out_rt:
        routes = out_rt.splitlines()[:8]
    return ip_map, ip6_map, gw_map, routes


def _default_nm_profile(nm_running: bool = False) -> dict:
    return {
        "nm_running": nm_running,
        "dev_state": "unmanaged",
        "is_external": False,
        "managed": False,
        "verified_on_disk": False,
        "filename": "",
        "uuid": "",
        "con_name": "",
        "con_type": "ethernet",
        "available_profiles": [],
        "method": "auto",
        "ip_cidr": "",
        "gateway": "",
        "dns": "",
        "ignore_auto_dns": "no",
        "dns_search": "",
        "route_metric": -1,
        "never_default": "no",
        "static_routes": [],
        "ip6_method": "auto",
        "ip6_privacy": "default",
        "ip6_addr": "",
        "ip6_gw": "",
        "ip6_dns": "",
        "autoconnect": "yes",
        "autoconnect_priority": 0,
        "zone": "",
        "metered": "unknown",
        "mtu": "auto",
        "cloned_mac": "",
        "wol": "default",
        "lldp": "default",
        "wifi_ssid": "",
        "wifi_band": "auto",
    }


def _collect_nm_profile_details(ifname: str) -> dict:
    info = _default_nm_profile(False)
    if not ifname or not _is_nm_running():
        return info
    info["nm_running"] = True

    ok_d, out_d = probe.run_cmd(
        ["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CON-UUID,GENERAL.CONNECTION,IP4.DNS", "device", "show", ifname],
        timeout=1.5,
    )
    active_uuid = ""
    live_dns = []
    if ok_d and out_d:
        for line in out_d.splitlines():
            if line.startswith("GENERAL.STATE:"):
                st_raw = line.split(":", 1)[1].strip()
                info["dev_state"] = st_raw
                if "externally" in st_raw.lower():
                    info["is_external"] = True
            elif line.startswith("GENERAL.CON-UUID:"):
                val = line.split(":", 1)[1].strip()
                if val and val != "--":
                    active_uuid = val
            elif line.startswith("IP4.DNS"):
                val = line.split(":", 1)[1].strip()
                if val:
                    live_dns.append(val)

    is_wifi_if = ifname.startswith(("wl", "wlan")) or os.path.exists(f"/sys/class/net/{ifname}/wireless")

    ok_c, out_c = probe.run_cmd(
        ["nmcli", "-t", "-f", "UUID,NAME,TYPE,DEVICE,AUTOCONNECT,AUTOCONNECT-PRIORITY,FILENAME", "connection", "show"],
        timeout=1.5,
    )
    matching_profiles = []
    if ok_c and out_c:
        for line in out_c.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if len(parts) < 7:
                continue
            c_uuid = parts[0].strip()
            c_name = parts[1].replace("\\:", ":").strip()
            c_type = parts[2].strip()
            c_dev = parts[3].strip()
            c_auto = parts[4].strip() or "yes"
            c_prio = parts[5].strip() or "0"
            c_file = parts[6].replace("\\:", ":").strip()

            if c_type not in ("802-3-ethernet", "ethernet", "vlan", "bridge", "bond", "dummy", "802-11-wireless", "wifi", "vpn", "wireguard", "tun"):
                continue
            is_volatile_run = c_file.startswith("/run/") and info["is_external"] and (c_uuid == active_uuid)
            if is_volatile_run:
                continue

            matched = False
            if c_dev == ifname or c_name == ifname:
                matched = True
            else:
                ok_if, out_if = probe.run_cmd(["nmcli", "-g", "connection.interface-name", "connection", "show", c_uuid], timeout=0.8)
                bound_if = out_if.strip() if ok_if else ""
                if bound_if == ifname:
                    matched = True
                elif is_wifi_if and c_type in ("802-11-wireless", "wifi") and not bound_if:
                    matched = True

            if matched:
                matching_profiles.append({
                    "uuid": c_uuid,
                    "name": c_name,
                    "type": c_type,
                    "file": c_file,
                    "autoconnect": c_auto,
                    "priority": c_prio,
                    "active": (c_uuid == active_uuid),
                })

    matching_profiles.sort(key=lambda p: (not p["active"], p["name"].lower()))
    info["available_profiles"] = matching_profiles

    preferred_uuid = str(STATE.get("network", f"selected_prof_{ifname}", "") or "")
    chosen = next((p for p in matching_profiles if p["uuid"] == preferred_uuid), None)
    if chosen is None:
        chosen = next((p for p in matching_profiles if p["active"]), None)
    if chosen is None and matching_profiles:
        chosen = matching_profiles[0]

    if chosen is None:
        return info

    uuid = chosen["uuid"]
    info["managed"] = True
    info["uuid"] = uuid
    info["con_name"] = chosen["name"]
    info["con_type"] = chosen.get("type", "ethernet")
    info["filename"] = chosen["file"]
    info["verified_on_disk"] = bool(chosen["file"] and not chosen["file"].startswith("/run/"))

    fields = (
        "connection.id,connection.type,connection.autoconnect,connection.autoconnect-priority,connection.zone,connection.metered,connection.lldp,"
        "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns,ipv4.ignore-auto-dns,ipv4.dns-search,ipv4.route-metric,ipv4.never-default,ipv4.routes,"
        "ipv6.method,ipv6.ip6-privacy,ipv6.addresses,ipv6.gateway,ipv6.dns,"
        "802-3-ethernet.mtu,802-3-ethernet.cloned-mac-address,802-3-ethernet.wake-on-lan,"
        "802-11-wireless.ssid,802-11-wireless.band,802-11-wireless.mtu,802-11-wireless.cloned-mac-address"
    )
    ok_p, out_p = probe.run_cmd(["nmcli", "-t", "-f", fields, "connection", "show", uuid], timeout=1.8)
    if ok_p and out_p:
        for line in out_p.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            v = v.strip()
            if k == "connection.id" and v:
                info["con_name"] = v
            elif k == "connection.type" and v:
                info["con_type"] = v
            elif k == "connection.autoconnect":
                info["autoconnect"] = v or "yes"
            elif k == "connection.autoconnect-priority":
                try:
                    info["autoconnect_priority"] = int(v)
                except ValueError:
                    info["autoconnect_priority"] = 0
            elif k == "connection.zone":
                info["zone"] = v if v != "--" else ""
            elif k == "connection.metered":
                info["metered"] = v or "unknown"
            elif k == "connection.lldp":
                info["lldp"] = v if v != "--" else "default"
            elif k == "ipv4.method":
                info["method"] = v or "auto"
            elif k == "ipv4.addresses":
                info["ip_cidr"] = v if v != "--" else ""
            elif k == "ipv4.gateway":
                info["gateway"] = v if v != "--" else ""
            elif k == "ipv4.dns":
                info["dns"] = v if v and v != "--" else ", ".join(live_dns)
            elif k == "ipv4.ignore-auto-dns":
                info["ignore_auto_dns"] = v or "no"
            elif k == "ipv4.dns-search":
                info["dns_search"] = v if v != "--" else ""
            elif k == "ipv4.route-metric":
                try:
                    info["route_metric"] = int(v)
                except ValueError:
                    info["route_metric"] = -1
            elif k == "ipv4.never-default":
                info["never_default"] = v or "no"
            elif k == "ipv4.routes":
                if v and v != "--":
                    info["static_routes"] = [r.strip() for r in v.split(";") if r.strip()]
            elif k == "ipv6.method":
                info["ip6_method"] = v or "auto"
            elif k == "ipv6.ip6-privacy":
                info["ip6_privacy"] = v or "default"
            elif k == "ipv6.addresses":
                info["ip6_addr"] = v if v != "--" else ""
            elif k == "ipv6.gateway":
                info["ip6_gw"] = v if v != "--" else ""
            elif k == "ipv6.dns":
                info["ip6_dns"] = v if v != "--" else ""
            elif k == "802-3-ethernet.mtu" and not is_wifi_if:
                info["mtu"] = v if v and v != "0" else "auto"
            elif k == "802-3-ethernet.cloned-mac-address" and not is_wifi_if:
                info["cloned_mac"] = v if v != "--" else ""
            elif k == "802-3-ethernet.wake-on-lan":
                info["wol"] = v if v != "--" else "default"
            elif k == "802-11-wireless.ssid":
                info["wifi_ssid"] = v if v != "--" else ""
            elif k == "802-11-wireless.band":
                info["wifi_band"] = v if v and v != "--" else "auto"
            elif k == "802-11-wireless.mtu" and is_wifi_if:
                info["mtu"] = v if v and v != "0" else "auto"
            elif k == "802-11-wireless.cloned-mac-address" and is_wifi_if:
                info["cloned_mac"] = v if v != "--" else ""

    if not info["dns"] and live_dns:
        info["dns"] = ", ".join(live_dns)
    return info


def create_verified_nm_profile(ifname: str, con_name: str = None) -> tuple[bool, str, str]:
    if not _is_nm_running():
        return False, "", t("cc_network.err_nm_inactive_start")

    prof_name = (con_name or ifname).strip()
    is_wifi = ifname.startswith(("wl", "wlan")) or os.path.exists(f"/sys/class/net/{ifname}/wireless")
    nm_type = "wifi" if is_wifi else "ethernet"

    probe.run_cmd_full(["nmcli", "device", "set", ifname, "managed", "yes"], timeout=2.5)

    cmd = [
        "nmcli", "connection", "add",
        "type", nm_type,
        "ifname", ifname,
        "con-name", prof_name,
        "connection.autoconnect", "yes",
        "ipv4.method", "auto",
        "ipv6.method", "auto",
    ]
    if is_wifi:
        cmd += ["ssid", prof_name]

    ok, out, err = probe.run_cmd_full(cmd, timeout=5.0)
    if not ok:
        return False, "", (err or out or t("cc_network.err_nm_create", p0=ifname))

    ok_v, out_v, _ = probe.run_cmd_full(
        ["nmcli", "-g", "connection.uuid,connection.interface-name", "connection", "show", prof_name],
        timeout=2.0,
    )
    if not ok_v or not out_v.strip():
        return False, "", t("cc_network.err_nm_create_unconfirmed")

    lines = [l.strip() for l in out_v.splitlines() if l.strip()]
    created_uuid = lines[0] if lines else ""
    if not created_uuid:
        return False, "", t("cc_network.err_nm_no_uuid")

    STATE.set("network", f"selected_prof_{ifname}", created_uuid)
    return True, created_uuid, t("cc_network.fb_prof_created", p0=prof_name, p1=created_uuid[:8])


def clone_verified_nm_profile(ifname: str, src_uuid: str, new_name: str = None) -> tuple[bool, str, str]:
    if not _is_nm_running():
        return False, "", t("cc_network.err_nm_inactive")
    ok_n, out_n = probe.run_cmd(["nmcli", "-g", "connection.id", "connection", "show", src_uuid], timeout=1.5)
    base_name = out_n.strip() if (ok_n and out_n.strip()) else ifname
    target_name = (new_name or f"{base_name}-copy").strip()

    ok, out, err = probe.run_cmd_full(["nmcli", "connection", "clone", src_uuid, target_name], timeout=4.0)
    if not ok:
        return False, "", (err or out or t("cc_network.err_nm_clone", p0=base_name))

    ok_v, out_v = probe.run_cmd(["nmcli", "-g", "connection.uuid", "connection", "show", target_name], timeout=1.5)
    cloned_uuid = out_v.splitlines()[0].strip() if (ok_v and out_v.strip()) else ""
    if cloned_uuid:
        STATE.set("network", f"selected_prof_{ifname}", cloned_uuid)
    return True, cloned_uuid, t("cc_network.fb_prof_cloned", p0=target_name)


def apply_and_verify_nm_profile(ifname: str, uuid: str, buf: dict, nm_cur: dict, iface_up: bool = True) -> tuple[bool, str]:
    if not _is_nm_running():
        return False, t("cc_network.err_nm_inactive")

    method = buf.get("method", nm_cur.get("method", "auto"))
    ip_cidr = str(buf.get("ip_cidr", nm_cur.get("ip_cidr", "")) or "").strip()
    gw = str(buf.get("gateway", nm_cur.get("gateway", "")) or "").strip()
    dns = str(buf.get("dns", nm_cur.get("dns", "")) or "").strip()
    ignore_auto_dns = str(buf.get("ignore_auto_dns", nm_cur.get("ignore_auto_dns", "no")) or "no").strip()
    dns_search = str(buf.get("dns_search", nm_cur.get("dns_search", "")) or "").strip()
    route_metric = buf.get("route_metric", nm_cur.get("route_metric", -1))
    never_default = str(buf.get("never_default", nm_cur.get("never_default", "no")) or "no").strip()
    static_routes = buf.get("static_routes", nm_cur.get("static_routes", []))

    ip6_method = str(buf.get("ip6_method", nm_cur.get("ip6_method", "auto")) or "auto").strip()
    ip6_privacy = str(buf.get("ip6_privacy", nm_cur.get("ip6_privacy", "default")) or "default").strip()
    ip6_dns = str(buf.get("ip6_dns", nm_cur.get("ip6_dns", "")) or "").strip()

    con_name = str(buf.get("con_name", nm_cur.get("con_name", ifname)) or ifname).strip()
    autoconn = str(buf.get("autoconnect", nm_cur.get("autoconnect", "yes")) or "yes").strip()
    prio = buf.get("autoconnect_priority", nm_cur.get("autoconnect_priority", 0))
    zone = str(buf.get("zone", nm_cur.get("zone", "")) or "").strip()
    metered = str(buf.get("metered", nm_cur.get("metered", "unknown")) or "unknown").strip()
    mtu = str(buf.get("mtu", nm_cur.get("mtu", "auto")) or "auto").strip()
    cloned_mac = str(buf.get("cloned_mac", nm_cur.get("cloned_mac", "")) or "").strip()
    wol = str(buf.get("wol", nm_cur.get("wol", "default")) or "default").strip()
    lldp = str(buf.get("lldp", nm_cur.get("lldp", "default")) or "default").strip()
    wifi_band = str(buf.get("wifi_band", nm_cur.get("wifi_band", "auto")) or "auto").strip()

    if method == "manual":
        ok_ip, ip_cidr, err_ip = validate_network_field("ip_cidr", ip_cidr)
        if not ok_ip or not ip_cidr:
            return False, (err_ip or t("cc_network.err_nm_manual_ip_req"))
        if gw:
            ok_gw, gw, err_gw = validate_network_field("gateway", gw)
            if not ok_gw:
                return False, err_gw

    cmd = ["nmcli", "connection", "modify", uuid]
    cmd += ["connection.id", con_name, "connection.autoconnect", autoconn, "connection.autoconnect-priority", str(prio)]
    cmd += ["connection.zone", zone, "connection.metered", metered]
    if lldp in ("default", "rx", "disable"):
        cmd += ["connection.lldp", lldp]

    if method == "auto":
        cmd += ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""]
    elif method == "manual":
        cmd += ["ipv4.addresses", ip_cidr, "ipv4.method", "manual", "ipv4.gateway", gw]
    else:
        cmd += ["ipv4.method", method, "ipv4.addresses", "", "ipv4.gateway", ""]

    clean_dns = dns.replace(",", " ").strip()
    cmd += ["ipv4.dns", clean_dns, "ipv4.ignore-auto-dns", ignore_auto_dns]
    cmd += ["ipv4.dns-search", dns_search, "ipv4.never-default", never_default]
    try:
        cmd += ["ipv4.route-metric", str(int(route_metric))]
    except ValueError:
        cmd += ["ipv4.route-metric", "-1"]

    if isinstance(static_routes, list):
        cmd += ["ipv4.routes", ",".join(static_routes)]

    cmd += ["ipv6.method", ip6_method, "ipv6.ip6-privacy", ip6_privacy, "ipv6.dns", ip6_dns.replace(",", " ").strip()]

    is_wifi = ifname.startswith(("wl", "wlan")) or os.path.exists(f"/sys/class/net/{ifname}/wireless") or ("wireless" in str(nm_cur.get("con_type", "")))
    mtu_val = "0" if (not mtu or mtu.lower() == "auto") else str(mtu)
    if not is_wifi:
        if mtu_val.isdigit():
            cmd += ["802-3-ethernet.mtu", mtu_val]
        cmd += ["802-3-ethernet.cloned-mac-address", cloned_mac]
        if wol in ("default", "magic", "ignore"):
            cmd += ["802-3-ethernet.wake-on-lan", wol]
    else:
        if mtu_val.isdigit():
            cmd += ["802-11-wireless.mtu", mtu_val]
        cmd += ["802-11-wireless.cloned-mac-address", cloned_mac]
        cmd += ["802-11-wireless.band", "" if wifi_band == "auto" else wifi_band]

    ok_m, out_m, err_m = probe.run_cmd_full(cmd, timeout=6.0)
    if not ok_m:
        return False, (err_m or out_m or t("cc_network.err_nm_modify"))

    ok_chk, out_chk, _ = probe.run_cmd_full(
        ["nmcli", "-g", "ipv4.method,ipv4.addresses,connection.autoconnect", "connection", "show", uuid],
        timeout=2.0,
    )
    if not ok_chk:
        return False, t("cc_network.err_nm_verify_readback")

    chk_lines = [l.strip() for l in out_chk.splitlines()]
    saved_method = chk_lines[0] if len(chk_lines) > 0 else ""
    saved_ip = chk_lines[1].replace("\\,", ",") if len(chk_lines) > 1 else ""
    if saved_method != method:
        return False, t("cc_network.err_nm_verify_method", p0=saved_method, p1=method)
    if method == "manual" and ip_cidr.split("/")[0] not in saved_ip:
        return False, t("cc_network.err_nm_verify_ip", p0=ip_cidr, p1=saved_ip)

    is_active_prof = any(p.get("uuid") == uuid and p.get("active") for p in nm_cur.get("available_profiles", []))
    if iface_up and (is_active_prof or not is_wifi):
        probe.run_cmd_full(["nmcli", "device", "set", ifname, "managed", "yes"], timeout=2.0)
        ok_u, out_u, err_u = probe.run_cmd_full(["nmcli", "connection", "up", uuid], timeout=10.0)
        if not ok_u:
            return False, t("cc_network.err_nm_activate", p0=ifname, p1=(err_u or out_u))

        if method == "manual" and probe.has_bin("ip"):
            expected_ip = ip_cidr.split("/")[0]
            kernel_ips = []
            for _ in range(6):
                ok_k, out_k = probe.run_cmd(["ip", "-j", "addr", "show", "dev", ifname], timeout=1.0)
                if ok_k and out_k:
                    try:
                        for item in json.loads(out_k):
                            for a in item.get("addr_info", []):
                                if a.get("family") == "inet":
                                    kernel_ips.append(f"{a.get('local')}/{a.get('prefixlen')}")
                    except Exception:
                        pass
                if any(k_ip.startswith(expected_ip + "/") for k_ip in kernel_ips):
                    break
                time.sleep(0.15)
            if not any(k_ip.startswith(expected_ip + "/") for k_ip in kernel_ips):
                return False, t("cc_network.err_nm_kernel_ip", p0=ifname, p1=(kernel_ips or t("cc_network.no_ip")), p2=ip_cidr)

    return True, t("cc_network.fb_prof_applied", p0=con_name, p1=ifname)


def _collect_wifi_state(force_rescan: bool = False) -> tuple[dict, set]:
    wifi_devs = [i["name"] for i in get_interfaces_fast() if i["kind"] == "wifi"]
    res = {
        "available": probe.has_bin("nmcli"),
        "has_hw": bool(wifi_devs),
        "enabled": False,
        "powersave": "default",
        "active_band": "auto",
        "active_mac_priv": "",
        "networks": [],
        "saved": [],
    }
    saved_ssids = set()
    if not res["available"] or not res["has_hw"]:
        return res, saved_ssids

    ok_r, out_r = probe.run_cmd(["nmcli", "radio", "wifi"], timeout=1.0)
    res["enabled"] = (ok_r and "enabled" in out_r.lower())

    if probe.has_bin("iw"):
        ok_ps, out_ps = probe.run_cmd(["iw", "dev", wifi_devs[0], "get", "power_save"], timeout=1.0)
        if ok_ps and out_ps:
            low_ps = out_ps.lower()
            if "power save: on" in low_ps:
                res["powersave"] = "on"
            elif "power save: off" in low_ps:
                res["powersave"] = "off"

    ok_s, out_s = probe.run_cmd(
        ["nmcli", "-t", "-f", "UUID,NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,DEVICE", "connection", "show"],
        timeout=1.5,
    )
    if ok_s and out_s:
        saved_list = []
        for line in out_s.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if len(parts) >= 6 and parts[2] in ("802-11-wireless", "wifi"):
                nm = parts[1].replace("\\:", ":")
                saved_ssids.add(nm)
                is_act = bool(parts[5] and parts[5] != "--")
                saved_list.append({
                    "uuid": parts[0],
                    "name": nm,
                    "autoconnect": parts[3],
                    "priority": parts[4],
                    "active": is_act,
                })
                if is_act:
                    ok_ab, out_ab = probe.run_cmd(
                        ["nmcli", "-t", "-f", "802-11-wireless.band,802-11-wireless.cloned-mac-address", "connection", "show", parts[0]],
                        timeout=1.0,
                    )
                    if ok_ab and out_ab:
                        for ln in out_ab.splitlines():
                            if ":" not in ln:
                                continue
                            k, v = ln.split(":", 1)
                            v = v.replace("\\:", ":").strip()
                            if k == "802-11-wireless.band":
                                res["active_band"] = v if v not in ("", "--") else "auto"
                            elif k == "802-11-wireless.cloned-mac-address":
                                res["active_mac_priv"] = v if v not in ("", "--") else ""
        res["saved"] = saved_list

    if not res["enabled"]:
        return res, saved_ssids

    rescan_arg = "yes" if force_rescan else "no"
    ok_w, out_w = probe.run_cmd(
        ["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,CHAN,RATE,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", rescan_arg],
        timeout=4.0 if force_rescan else 2.0,
    )
    if ok_w and out_w:
        seen_ssids = set()
        nets = []
        for line in out_w.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if len(parts) < 7:
                continue
            in_use = (parts[0].strip() == "*")
            raw_ssid = parts[1].replace("\\:", ":").strip()
            is_hidden = not bool(raw_ssid)
            ssid = raw_ssid or t("cc_network.hidden_ap")
            bssid = parts[2].replace("\\:", ":").strip()
            chan = parts[3].strip()
            rate = parts[4].strip()
            try:
                sig = int(parts[5].strip())
            except ValueError:
                sig = 0
            sec = parts[6].strip() or "OPEN"
            key = (ssid, in_use)
            if key in seen_ssids and not in_use:
                continue
            seen_ssids.add(key)
            nets.append({
                "in_use": in_use,
                "ssid": ssid,
                "hidden": is_hidden,
                "bssid": bssid,
                "chan": chan,
                "rate": rate,
                "signal": sig,
                "security": sec,
            })
        nets.sort(key=lambda x: (not x["in_use"], -x["signal"]))
        res["networks"] = nets
        try:
            with open(_cache_path("net_wifi_cache.json", ensure_dir=True), "w", encoding="utf-8") as f:
                json.dump(nets, f)
        except Exception:
            pass
    return res, saved_ssids


def _collect_bluetooth_state() -> dict:
    bt = {
        "available": probe.has_bin("bluetoothctl"),
        "has_controller": False,
        "powered": False,
        "scanning": False,
        "paired": [],
        "discovered": [],
    }
    if not bt["available"]:
        return bt

    ok_s, out_s = probe.run_cmd(["bluetoothctl", "show"], timeout=1.0)
    if not ok_s or "Controller " not in out_s:
        return bt
    bt["has_controller"] = True
    for line in out_s.splitlines():
        if "Powered: yes" in line:
            bt["powered"] = True
        elif "Discovering: yes" in line:
            bt["scanning"] = True

    if not bt["powered"]:
        return bt

    paired_macs = set()
    ok_p, out_p = probe.run_cmd(["bluetoothctl", "devices", "Paired"], timeout=1.0)
    if ok_p and out_p:
        for line in out_p.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) < 3 or parts[0] != "Device":
                continue
            mac, name = parts[1], parts[2]
            paired_macs.add(mac)
            connected = False
            battery = None
            ok_i, out_i = probe.run_cmd(["bluetoothctl", "info", mac], timeout=0.8)
            if ok_i and out_i:
                for iline in out_i.splitlines():
                    if "Connected: yes" in iline:
                        connected = True
                    elif "Battery Percentage" in iline:
                        m = re.search(r"\((\d+)\)", iline)
                        if m:
                            battery = int(m.group(1))
            low = name.lower()
            if any(k in low for k in ("headset", "headphone", "buds", "airpods", "wh-", "wf-", "sony", "audio")):
                icon = "\uf025"
            elif any(k in low for k in ("mouse", "mx", "xornet", "razor", "logi")):
                icon = "\uf8cc"
            elif any(k in low for k in ("key", "k3", "apex", "board")):
                icon = "\uf11c"
            elif any(k in low for k in ("phone", "pixel", "iphone", "galaxy")):
                icon = "\uf3cd"
            else:
                icon = "\uf294"
            bt["paired"].append({
                "mac": mac,
                "name": name,
                "connected": connected,
                "battery": battery,
                "icon": icon,
            })

    bt["paired"].sort(key=lambda d: (not d["connected"], d["name"].lower()))

    if bt["scanning"] or paired_macs:
        ok_all, out_all = probe.run_cmd(["bluetoothctl", "devices"], timeout=0.8)
        if ok_all and out_all:
            for line in out_all.splitlines():
                parts = line.strip().split(" ", 2)
                if len(parts) >= 3 and parts[0] == "Device":
                    mac, name = parts[1], parts[2]
                    if mac not in paired_macs:
                        bt["discovered"].append({"mac": mac, "name": name})
    return bt


def _collect_vpn_profiles() -> list[dict]:
    vpns = []
    if not probe.has_bin("nmcli"):
        return vpns
    ok, out = probe.run_cmd(
        ["nmcli", "-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT,DEVICE", "connection", "show"],
        timeout=1.4,
    )
    if ok and out:
        for line in out.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if len(parts) >= 5 and parts[2] in ("vpn", "wireguard", "tun"):
                uuid = parts[1].strip()
                dev = parts[4].strip()
                active = bool(dev and dev != "--")
                never_def = "no"
                ok_nd, out_nd = probe.run_cmd(["nmcli", "-g", "ipv4.never-default", "connection", "show", uuid], timeout=0.8)
                if ok_nd and out_nd.strip():
                    never_def = out_nd.strip()
                vpns.append({
                    "name": parts[0].replace("\\:", ":"),
                    "uuid": uuid,
                    "type": parts[2],
                    "autoconnect": parts[3],
                    "device": dev if active else "",
                    "active": active,
                    "split_tunnel": (never_def == "yes"),
                })
    return vpns


def _collect_proxy_state() -> dict:
    return {
        "mode": str(STATE.get("network", "proxy_mode", "none")),
        "http_host": str(STATE.get("network", "proxy_http_host", "")),
        "http_port": int(STATE.get("network", "proxy_http_port", 8080) or 8080),
        "socks_host": str(STATE.get("network", "proxy_socks_host", "")),
        "socks_port": int(STATE.get("network", "proxy_socks_port", 1080) or 1080),
        "autoconfig_url": str(STATE.get("network", "proxy_pac_url", "")),
    }


def _write_proxy_env(pr: dict = None) -> tuple[bool, str]:
    if pr is None:
        pr = _collect_proxy_state()
    mode = pr.get("mode", "none")
    lines = ["# Managed by NC-UI Network Control Center"]
    if mode == "manual":
        http_u = f"http://{pr['http_host']}:{pr['http_port']}" if pr.get("http_host") else ""
        socks_u = f"socks5://{pr['socks_host']}:{pr['socks_port']}" if pr.get("socks_host") else ""
        no_prx = "localhost,127.0.0.1,10.0.0.0/8,192.168.0.0/16,172.16.0.0/12"
        lines.append('export PROXY_MODE="manual"')
        if http_u:
            lines.append(f'export HTTP_PROXY="{http_u}"')
            lines.append(f'export HTTPS_PROXY="{http_u}"')
            lines.append(f'export http_proxy="{http_u}"')
            lines.append(f'export https_proxy="{http_u}"')
        if socks_u:
            lines.append(f'export ALL_PROXY="{socks_u}"')
            lines.append(f'export all_proxy="{socks_u}"')
        lines.append(f'export NO_PROXY="{no_prx}"')
        lines.append(f'export no_proxy="{no_prx}"')
    elif mode == "auto":
        pac = pr.get("autoconfig_url", "")
        lines.append('export PROXY_MODE="auto"')
        lines.append(f'export PAC_AUTOCONFIG_URL="{pac}"')
    else:
        lines.append('export PROXY_MODE="none"')
        lines.append("unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy PAC_AUTOCONFIG_URL")

    try:
        env_p = get_proxy_env_path()
        os.makedirs(os.path.dirname(env_p), exist_ok=True)
        with open(env_p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True, ""
    except Exception as e:
        log.error(f"_write_proxy_env error: {e}")
        return False, str(e)


def _compute_wan_badge(ip_str: str) -> dict:
    wan_cfg = CONFIG.get("network", {}).get("wan", {})
    primary_ip = wan_cfg.get("primary_ip", "").strip()
    primary_lbl = wan_cfg.get("primary_label", "Primary WAN")
    failover_lbl = wan_cfg.get("failover_label", "Failover WAN")
    badge_text = ""
    badge_style = "info"
    if primary_ip and is_resolved_wan_ip(ip_str):
        if ip_str == primary_ip:
            badge_text = primary_lbl
            badge_style = "accent"
        else:
            badge_text = failover_lbl
            badge_style = "warn"
    return {"ip": ip_str, "badge": badge_text, "badge_style": badge_style}


def _collect_wan_ip() -> dict:
    try:
        req = urllib.request.Request("https://ifconfig.me/ip", headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=1.8) as resp:
            ip_val = resp.read().decode("utf-8").strip()
        if ip_val:
            with open(_cache_path("wan_cache.json", ensure_dir=True), "w", encoding="utf-8") as f:
                json.dump({"ip": ip_val, "ts": time.time()}, f)
            return _compute_wan_badge(ip_val)
    except Exception as e:
        log.debug(f"WAN IP fetch error: {e}")
    cur_wan = _NET_STATE["wan"]
    if is_resolved_wan_ip(cur_wan.get("ip", "")):
        return cur_wan
    return {"ip": t("common.nd"), "badge": t("cc_network.offline"), "badge_style": "crit"}


def get_watchdog_targets(ifaces: list[dict]) -> list[dict]:
    custom = STATE.get("network", "custom_watchdog", None)
    if isinstance(custom, list) and custom:
        return [
            {"name": item.get("name", item.get("host")), "host": item.get("host")}
            for item in custom
            if isinstance(item, dict) and item.get("host")
        ]
    configured = CONFIG.get("network", {}).get("watchdog", [])
    if configured:
        return [
            {"name": item.get("name", item.get("host")), "host": item.get("host")}
            for item in configured
            if item.get("host")
        ]
    targets = []
    for iface in ifaces:
        if iface["up"] and iface["gw"]:
            targets.append({"name": t("cc_network.watchdog_gateway", p0=iface["name"]), "host": iface["gw"]})
            break
    targets.append({"name": t("cc_network.watchdog_cloudflare_dns"), "host": "1.1.1.1"})
    return targets


def _collect_pings(targets: list[dict]) -> dict:
    if not probe.has_bin("ping"):
        return {tgt["host"]: {"ok": False, "missing": True, "ms": None} for tgt in targets}
    new_res = {}
    for tgt in targets:
        host = tgt["host"]
        ok, out = probe.run_cmd(["ping", "-c", "1", "-W", "1", host], timeout=1.2)
        if ok and out:
            m = re.search(r"time=([\d.]+)\s*ms", out)
            new_res[host] = {"ok": True, "ms": float(m.group(1)) if m else 0.5}
        else:
            new_res[host] = {"ok": False, "ms": None}
    try:
        with open(_cache_path("net_ping_cache.json", ensure_dir=True), "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "results": new_res}, f)
    except Exception:
        pass
    return new_res


class Panel(BasePanel):
    id = "network"
    icon = "\uf6ff"
    supports_expand = True
    hover_highlight = True
    default_tab = "ifaces"

    def __init__(self, shell=None):
        self.title = t("network.title") if shell is not None else self.id
        super().__init__(shell)
        _load_fast_disk_cache()
        self._last_sample = read_proc_net_dev()
        self._last_time = time.time()
        self._speeds = {}

    def _state_owner(self):
        return self.shell.panel if (self.shell and getattr(self.shell, "panel", None) is not None) else self

    def _fold(self, sec_key: str, default: bool = False, dirty: bool = False) -> tuple[bool, str]:
        return FoldToggle(self._state_owner(), sec_key, default=default, dirty=dirty, uri_prefix="ui:state")

    def _emit_fb(self, tab_key: str, ok: bool, msg: str) -> None:
        set_feedback("network", ok, msg, key=f"fb_{tab_key}")
        if self.shell:
            self.shell.refresh_view()

    def _resolve_mon_and_unit(self, ifaces: list[dict], prefer_ip: bool = False) -> tuple[str, str]:
        unit_mode = self.get_state("speed_unit", CONFIG["network"].get("default_speed_unit", "bytes"))
        mon_iface = self.get_state("monitored_iface", "auto")
        valid_names = [i["name"] for i in ifaces]
        if mon_iface not in valid_names and mon_iface != "ALL":
            up_ifs = [i["name"] for i in ifaces if i["up"] and (not prefer_ip or i["ip"] != "N/D")]
            mon_iface = up_ifs[0] if up_ifs else (valid_names[0] if valid_names else "N/D")
        return mon_iface, unit_mode

    def _run_nm_profile_action(self, iface: str, fn, ok_msg_fn=None, resync_ip: bool = False, clear_buf_on_ok: bool = False) -> None:
        def _task():
            res = fn()
            ok = bool(res[0])
            msg = res[1] if len(res) > 1 else ""
            nm_prof = _collect_nm_profile_details(iface)
            ip_res = _collect_ip_and_routes() if resync_ip else None
            with _NET_LOCK:
                _NET_STATE["nm_profiles"][iface] = nm_prof
                _NET_STATE["ts"]["nm"] = time.time()
                if ip_res is not None:
                    _NET_STATE["ip_map"], _NET_STATE["ip6_map"], _NET_STATE["gw_map"], _NET_STATE["routes"] = ip_res
                    _NET_STATE["ts"]["ip"] = time.time()
            if ok and ok_msg_fn is not None:
                msg = ok_msg_fn(msg)
            elif not ok and not msg:
                msg = t("cc_network.fb_action_failed", p0=iface)
            return ok, msg

        def _done(res):
            ok, msg = res
            if ok and clear_buf_on_ok:
                cur_all = self.get_state("edit_buffer", {})
                if isinstance(cur_all, dict) and iface in cur_all:
                    del cur_all[iface]
                    self.set_state("edit_buffer", cur_all)
            self._emit_fb("interfaces", ok, msg)
            return False

        probe.run_async(_task, on_done=_done)

    def _run_net_cmd(
        self,
        fb_tab: str,
        cmd: list[str],
        ok_msg: str,
        tab_hint: str,
        timeout: float = 5.0,
        verify_fn=None,
        clear_wifi_cache: bool = False,
    ) -> None:
        def _task():
            ok, out, err = probe.run_cmd_full(cmd, timeout=timeout)
            if ok and verify_fn is not None:
                ok_v, err_v = verify_fn()
                if not ok_v:
                    ok, err = False, err_v
            if clear_wifi_cache:
                wc = _cache_path("net_wifi_cache.json")
                if os.path.exists(wc):
                    try:
                        os.remove(wc)
                    except Exception:
                        pass
            return ok, (ok_msg if ok else t("cc_network.fb_action_failed", p0=(err or out or cmd[0])))

        def _done(res):
            ok, msg = res
            self._emit_fb(fb_tab, ok, msg)
            self._trigger_full_resync(tab_hint)
            return False

        probe.run_async(_task, on_done=_done)

    def _update_live_speeds(self) -> None:
        now = time.time()
        curr = read_proc_net_dev()
        dt = max(0.15, now - self._last_time)
        speeds = {}
        for name, data in curr.items():
            prev = self._last_sample.get(name, data)
            speeds[name] = {
                "rx": max(0.0, float(data["rx"] - prev["rx"]) / dt),
                "tx": max(0.0, float(data["tx"] - prev["tx"]) / dt),
            }
        self._speeds = speeds
        self._last_sample = curr
        self._last_time = now

    def _schedule_bg_refresh(self, mode_hint: str = "compact", sel_iface: str = "", force: bool = False) -> None:
        global _BG_WORKER_RUNNING
        with _NET_LOCK:
            if _BG_WORKER_RUNNING:
                return
            _BG_WORKER_RUNNING = True

        def _worker():
            global _BG_WORKER_RUNNING
            changed = False
            now = time.time()
            try:
                if force or (now - _NET_STATE["ts"]["ip"] >= 1.4) or not _NET_STATE["initialized"]:
                    ip_map, ip6_map, gw_map, routes = _collect_ip_and_routes()
                    nm_run = _is_nm_running()
                    with _NET_LOCK:
                        if (
                            ip_map != _NET_STATE["ip_map"]
                            or ip6_map != _NET_STATE["ip6_map"]
                            or gw_map != _NET_STATE["gw_map"]
                            or routes != _NET_STATE["routes"]
                            or nm_run != _NET_STATE["nm_running"]
                            or not _NET_STATE["initialized"]
                        ):
                            _NET_STATE["ip_map"] = ip_map
                            _NET_STATE["ip6_map"] = ip6_map
                            _NET_STATE["gw_map"] = gw_map
                            _NET_STATE["routes"] = routes
                            _NET_STATE["nm_running"] = nm_run
                            _NET_STATE["initialized"] = True
                            changed = True
                        _NET_STATE["ts"]["ip"] = time.time()

                if force or (now - _NET_STATE["ts"]["wan"] >= 60.0):
                    wan_info = _collect_wan_ip()
                    with _NET_LOCK:
                        if wan_info != _NET_STATE["wan"]:
                            _NET_STATE["wan"] = wan_info
                            changed = True
                        _NET_STATE["ts"]["wan"] = time.time()

                if mode_hint in ("ifaces", "wired") and sel_iface:
                    if force or (now - _NET_STATE["ts"]["nm"] >= 2.5) or (sel_iface not in _NET_STATE["nm_profiles"]):
                        nm_prof = _collect_nm_profile_details(sel_iface)
                        with _NET_LOCK:
                            if _NET_STATE["nm_profiles"].get(sel_iface) != nm_prof:
                                _NET_STATE["nm_profiles"][sel_iface] = nm_prof
                                changed = True
                            _NET_STATE["ts"]["nm"] = time.time()

                elif mode_hint in ("wifi", "wireless"):
                    if force or (now - _NET_STATE["ts"]["wifi"] >= 4.5):
                        wifi_st, saved_ssids = _collect_wifi_state(force_rescan=False)
                        with _NET_LOCK:
                            if wifi_st != _NET_STATE["wifi"]:
                                _NET_STATE["wifi"] = wifi_st
                                changed = True
                            _NET_STATE["saved_wifi_ssids"] = saved_ssids
                            _NET_STATE["ts"]["wifi"] = time.time()
                    if force or (now - _NET_STATE["ts"]["bt"] >= 3.5):
                        bt_st = _collect_bluetooth_state()
                        with _NET_LOCK:
                            if bt_st != _NET_STATE["bt"]:
                                _NET_STATE["bt"] = bt_st
                                changed = True
                            _NET_STATE["ts"]["bt"] = time.time()

                elif mode_hint in ("bt", "bluetooth", "compact"):
                    bt_ttl = 2.5 if mode_hint in ("bt", "bluetooth") else 4.0
                    if force or (now - _NET_STATE["ts"]["bt"] >= bt_ttl):
                        bt_st = _collect_bluetooth_state()
                        with _NET_LOCK:
                            if bt_st != _NET_STATE["bt"]:
                                _NET_STATE["bt"] = bt_st
                                changed = True
                            _NET_STATE["ts"]["bt"] = time.time()

                elif mode_hint in ("vpn", "watchdog"):
                    if force or (now - _NET_STATE["ts"]["vpn"] >= 3.0):
                        vpns = _collect_vpn_profiles()
                        prx = _collect_proxy_state()
                        with _NET_LOCK:
                            if vpns != _NET_STATE["vpns"] or prx != _NET_STATE["proxy"]:
                                _NET_STATE["vpns"] = vpns
                                _NET_STATE["proxy"] = prx
                                changed = True
                            _NET_STATE["ts"]["vpn"] = time.time()

                    if force or (now - _NET_STATE["ts"]["ping"] >= 8.0):
                        ifaces_snap = get_interfaces_fast()
                        targets = get_watchdog_targets(ifaces_snap)
                        pings = _collect_pings(targets)
                        with _NET_LOCK:
                            if pings != _NET_STATE["pings"]:
                                _NET_STATE["pings"] = pings
                                changed = True
                            _NET_STATE["ts"]["ping"] = time.time()

                if changed:
                    _save_fast_disk_cache()
            except Exception as e:
                log.error(f"Network bg worker error: {e}")
            finally:
                with _NET_LOCK:
                    _BG_WORKER_RUNNING = False

            if changed and self.shell:
                def _ui_update():
                    if self.shell and not self.shell.is_pointer_busy() and not self.shell.prompt_bar.get_visible():
                        self.shell.refresh_view()
                    return False

                from gi.repository import GLib
                GLib.idle_add(_ui_update)

        threading.Thread(target=_worker, daemon=True).start()

    def get_bar_pill(self) -> dict:
        show_speed = get_network_show_bar_speed()

        speeds = {}
        if show_speed:
            now = time.time()
            curr_dev = read_proc_net_dev()
            prev_dev, prev_ts = curr_dev, now - 1.0
            bar_f = _cache_path("bar_sample.json")
            if os.path.exists(bar_f):
                try:
                    with open(bar_f, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                        prev_ts = float(s_data.get("ts", now - 1.0))
                        prev_dev = s_data.get("devs", curr_dev)
                except Exception:
                    pass
            try:
                with open(_cache_path("bar_sample.json", ensure_dir=True), "w", encoding="utf-8") as f:
                    json.dump({"ts": now, "devs": curr_dev}, f)
            except Exception:
                pass

            dt = max(0.2, now - prev_ts)
            for k, v in curr_dev.items():
                pr = prev_dev.get(k, v)
                speeds[k] = {
                    "rx": max(0.0, float(v["rx"] - pr.get("rx", v["rx"])) / dt),
                    "tx": max(0.0, float(v["tx"] - pr.get("tx", v["tx"])) / dt),
                }

        ip_map, ip6_map, gw_map, _ = _collect_ip_and_routes()
        bt = _collect_bluetooth_state()
        with _NET_LOCK:
            _NET_STATE["ip_map"] = ip_map
            _NET_STATE["ip6_map"] = ip6_map
            _NET_STATE["gw_map"] = gw_map
            _NET_STATE["bt"] = bt
            _NET_STATE["initialized"] = True
        _save_fast_disk_cache()

        ifaces = get_interfaces_fast(speeds)
        eth_ifaces = [i for i in ifaces if i["kind"] == "eth"]
        wifi_ifaces = [i for i in ifaces if i["kind"] == "wifi"]

        eth_role = "focus" if any(i["up"] for i in eth_ifaces) else "inactive"
        wifi_role = "focus" if any(i["up"] for i in wifi_ifaces) else "inactive"
        bt_role = "focus" if bt["powered"] else "inactive"

        font = THEME["typography"]["font_family"]
        sep = f"<span font_family='{font}'>  </span>"
        icons_part = (
            f"{Txt('\uf6ff', role=eth_role)}{sep}"
            f"{Txt('\uf1eb', role=wifi_role)}{sep}"
            f"{Txt('\uf294', role=bt_role)}"
        )

        if not show_speed:
            return {
                "text": icons_part,
                "tooltip": "",
                "class": "module-nc",
            }

        vpn_ifaces = [i for i in ifaces if i["kind"] == "vpn" and i["up"]]
        if vpn_ifaces:
            icons_part += f" {Txt('\uf132', role='focus')}"

        mon_iface, unit_mode = self._resolve_mon_and_unit(ifaces, prefer_ip=True)

        if mon_iface == "ALL":
            rx_val = sum(s["rx"] for s in speeds.values())
            tx_val = sum(s["tx"] for s in speeds.values())
            disp_iface = "ALL"
        else:
            sp = speeds.get(mon_iface, {"rx": 0.0, "tx": 0.0})
            rx_val, tx_val = sp["rx"], sp["tx"]
            disp_iface = mon_iface

        rx_str = format_speed(rx_val, unit_mode)
        tx_str = format_speed(tx_val, unit_mode)

        rx_ic_role = "focus" if rx_val > 64.0 else "inactive"
        tx_ic_role = "focus" if tx_val > 64.0 else "inactive"
        text = (
            f"{icons_part}   "
            f"<span font_family='{font}'>{Txt(rx_str, role='focus')}</span> {Txt('\uf063', role=rx_ic_role)}  "
            f"<span font_family='{font}'>{Txt(tx_str, role='focus')}</span> {Txt('\uf062', role=tx_ic_role)}  "
            f"<span font_family='{font}'>{Txt(disp_iface, role='schema')}</span>"
        )
        return {"text": text, "tooltip": "", "class": "module-nc"}

    def on_bar_action(self, action: str, args: list = None) -> None:
        if action == "cycle-iface":
            ifaces = get_interfaces_fast()
            candidates = [i["name"] for i in ifaces] + ["ALL"]
            if not candidates:
                return
            curr = self.get_state("monitored_iface", candidates[0])
            idx = (candidates.index(curr) + 1) % len(candidates) if curr in candidates else 0
            new_iface = candidates[idx]
            self.set_state("monitored_iface", new_iface)
        elif action == "toggle-unit":
            curr_u = self.get_state("speed_unit", CONFIG["network"].get("default_speed_unit", "bytes"))
            new_u = "bits" if curr_u == "bytes" else "bytes"
            self.set_state("speed_unit", new_u)

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        self._update_live_speeds()
        expanded = force_expanded or self.is_expanded
        ifaces = get_interfaces_fast(self._speeds)
        mon_iface, unit_mode = self._resolve_mon_and_unit(ifaces)

        if not expanded:
            self._schedule_bg_refresh(mode_hint="compact")
            return self._build_compact_view(canvas, ifaces, mon_iface, unit_mode)
        return self._build_expanded_view(canvas, ifaces, mon_iface, unit_mode)

    def _build_compact_view(self, canvas: Canvas, ifaces: list[dict], mon_iface: str, unit_mode: str) -> list:
        show_bar_spd = get_network_show_bar_speed()

        with _NET_LOCK:
            wan = copy.deepcopy(_NET_STATE["wan"])
            bt = copy.deepcopy(_NET_STATE["bt"])

        box = Box(
            t("cc_network.network_comms"),
            icon="\uf6ff",
            actions=[
                ActionBtn(unit_mode.upper(), "net:toggle_unit", tone="neutral"),
                ActionBtn("\uf013", "ui:open_settings:network", tone="neutral"),
                ActionBtn("+", "ui:toggle_expand", tone="neutral"),
            ],
            highlighted=False,
        )

        if wan["badge"]:
            if wan["badge_style"] == "crit":
                wan_badge = f" {Badge(wan['badge'], style='crit')}"
            else:
                wan_badge = f" {StateBadge(True, wan['badge'], on_tone='warn' if wan['badge_style'] == 'warn' else 'ok')}"
        else:
            wan_badge = ""
        wan_copy_btn = ActionBtn(t("cc_network.copy"), "ui:copy:" + wan["ip"], tone="neutral") if is_resolved_wan_ip(wan["ip"]) else ""
        box.add_kv(
            t("cc_network.wan_ip"),
            f"{Txt(wan['ip'], role='focus' if is_resolved_wan_ip(wan['ip']) else 'inactive')}{wan_badge}",
            key_width=10,
            right_markup=wan_copy_btn,
            icon="\uf0ac",
        )
        active_gws = [i["gw"] for i in ifaces if i["up"] and i["gw"]]
        if active_gws:
            box.add_kv(t("cc_network.gateway"), Txt(active_gws[0], role="val"), key_width=10, icon="\uf0e8")

        up_ifaces = [i for i in ifaces if i["up"]]
        down_ifaces = [i for i in ifaces if not i["up"]]

        for iface in up_ifaces:
            ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else "\uf6ff")
            is_mon = (iface["name"] == mon_iface)
            mon_uri = "net:mon:" + iface["name"]
            dn_uri = "net:ifdown:" + iface["name"]
            has_ip = (iface["ip"] != "N/D")
            ip_copy_btn = ActionBtn(t("cc_network.copy"), "ui:copy:" + iface["ip"].split("/")[0], tone="neutral") if has_ip else ""
            sec_acts = []
            if show_bar_spd:
                sec_acts.append(SelectPill(t("cc_network.bar"), mon_uri, selected=is_mon))
            sec_acts.append(ActionBtn(t("cc_network.down"), dn_uri, tone="danger_soft"))
            box.add_section(f"{ic} {iface['name']} ({iface['link_speed']})", actions=sec_acts)
            box.add_kv(
                t("cc_network.ipv4"),
                Txt(iface["ip"] if has_ip else t("common.nd"), role="focus" if has_ip else "inactive"),
                key_width=10,
                right_markup=ip_copy_btn,
                icon="\uf124",
            )
            rx_s = format_speed(iface["rx_rate"], unit_mode)
            tx_s = format_speed(iface["tx_rate"], unit_mode)
            box.add_row(f"  {Txt('\uf063', role='ok_soft')} {rx_s}   {Txt('\uf062', role='schema')} {tx_s}")

        if down_ifaces:
            box.add_section(t("cc_network.inactive_interfaces"))
            for iface in down_ifaces:
                ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else "\uf6ff")
                iname = f"{iface['name']:<10}"
                left = f"  {ic} {Txt(iname, role='inactive')} {StateBadge(False, t('common.up'), t('common.down'))}"
                mon_uri = "net:mon:" + iface["name"]
                up_uri = "net:ifup:" + iface["name"]
                up_b = ActionBtn(t("cc_network.up"), up_uri, tone="apply")
                bar_p = SelectPill(t("cc_network.bar"), mon_uri, selected=(iface["name"] == mon_iface))
                right_m = f"{bar_p} {up_b}" if show_bar_spd else up_b
                box.add_row(left, right_markup=right_m)

        bt_scanning = bool(bt.get("scanning", False))
        bt_ready = bool(bt.get("available") and bt.get("has_controller"))
        bt_actions = []
        if bt_ready:
            if bt["powered"]:
                bt_actions.append(ActionBtn(t("cc_network.scan"), "bt:scan", tone="warn" if bt_scanning else "neutral", modal_active=bt_scanning))
                bt_actions.append(ActionBtn(t("cc_network.off_2"), "bt:power:off", tone="danger_soft"))
            else:
                bt_actions.append(ActionBtn(t("cc_network.on_2"), "bt:power:on", tone="apply"))
        bt_lbl = t("common.on") if bt["powered"] else t("common.off")
        box.add_section(t("cc_network.bluetooth", p0=bt_lbl), actions=bt_actions)
        if not bt.get("available"):
            box.add_row(f"  {Txt(t('cc_network.bluetoothctl_service_not_found_on'), role='inactive')}")
        elif not bt.get("has_controller"):
            box.add_row(f"  {Txt(t('cc_network.bt_no_controller'), role='inactive')}")
        elif not bt["powered"]:
            box.add_row(f"  {Txt(t('cc_network.controller_is_off'), role='inactive')}")
        elif not bt["paired"]:
            box.add_row(f"  {Txt(t('cc_network.no_paired_devices'), role='inactive')}")
        else:
            for dev in bt["paired"][:4]:
                bat_v = dev.get("battery")
                bat = f" {Txt('\uf240 ' + str(bat_v) + '%', role='ok_soft')}" if bat_v is not None else ""
                name_short = canvas.truncate_plain(dev["name"], 18)
                st_role = "vitality" if dev["connected"] else "schema"
                left = f"  {dev['icon']} {Txt(name_short, role=st_role)}{bat}"
                disc_uri = "bt:disc:" + dev["mac"]
                conn_uri = "bt:conn:" + dev["mac"]
                act = ActionBtn(t("cc_network.disc"), disc_uri, tone="danger_soft") if dev["connected"] else ActionBtn(t("cc_network.conn"), conn_uri, tone="apply")
                box.add_row(left, right_markup=act)

        return [box]

    def _build_expanded_view(self, canvas: Canvas, ifaces: list[dict], mon_iface: str, unit_mode: str, forced_tab: str = None, is_cc: bool = False) -> list:
        from panels.cc_network_interfaces import build_wired_tab_cards
        from panels.cc_network_wifi_bt import build_wifi_tab_cards
        from panels.cc_network_vpn_proxy import build_vpn_tab_cards
        from panels.cc_network_wan_routing import build_wan_tab_cards

        tab = forced_tab or self.active_tab
        cards = []

        if not is_cc:
            hdr_box = Box(
                t("cc_network.nc_ui_network_radio_control_center"),
                icon="\uf6ff",
                actions=[
                    ActionBtn(t("cc_network.unit", p0=unit_mode.upper()), "net:toggle_unit", tone="neutral"),
                    ActionBtn(t("cc_network.refresh"), "net:force_refresh", tone="neutral"),
                    ActionBtn(t("cc_network.cc"), "ui:open_settings:network", tone="neutral"),
                    ActionBtn("-", "ui:toggle_expand", tone="neutral"),
                ],
                highlighted=True,
            )
            tabs = [
                ("ifaces",   t("cc_network.1_interfaces_profiles")),
                ("wifi",     t("cc_network.tab_wifi_bt")),
                ("vpn",      t("cc_network.tab_vpn_proxy")),
                ("watchdog", t("cc_network.tab_wan_routing")),
            ]
            hdr_box.add_tabs(tab, tabs)
            cards.append(hdr_box)

        if tab in ("wifi", "wireless", "wifi_bt"):
            self._schedule_bg_refresh(mode_hint="wifi")
            cards.extend(build_wifi_tab_cards(self, canvas))
        elif tab in ("bt", "bluetooth"):
            self._schedule_bg_refresh(mode_hint="bt")
            cards.extend(self._build_bt_cards(canvas))
        elif tab in ("vpn", "vpn_proxy"):
            self._schedule_bg_refresh(mode_hint="vpn")
            cards.extend(build_vpn_tab_cards(self, canvas))
        elif tab in ("watchdog", "wan_routing"):
            self._schedule_bg_refresh(mode_hint="vpn")
            cards.extend(build_wan_tab_cards(self, canvas, ifaces))
        else:
            sel_iface = self.get_state("selected_iface", mon_iface if mon_iface != "ALL" else (ifaces[0]["name"] if ifaces else ""))
            if ifaces and sel_iface not in [i["name"] for i in ifaces]:
                sel_iface = ifaces[0]["name"]
            self._schedule_bg_refresh(mode_hint="ifaces", sel_iface=sel_iface)
            cards.extend(build_wired_tab_cards(self, canvas, ifaces, mon_iface, sel_iface, unit_mode))

        return cards

    def _build_bt_cards(self, canvas: Canvas) -> list:
        with _NET_LOCK:
            bt = copy.deepcopy(_NET_STATE["bt"])

        st_badge = StateBadge(bt["powered"], t("cc_network.on"), t("cc_network.off"))
        box = Box(
            t("cc_network.controller_bluetooth_bluez"),
            icon="\uf294",
            highlighted=False,
        )

        if not bt["available"]:
            box.actions = [Badge(t("ui.missing", p0="bluetoothctl"), style="warn")]
            box.add_row(f"  {Txt(t('cc_network.bluetoothctl_service_not_found_on'), role='inactive')}")
            return [box]

        if not bt.get("has_controller"):
            box.actions = [StateBadge(False, "", t("cc_network.bt_not_detected"))]
            box.add_row(f"  {Txt(t('cc_network.bt_no_controller'), role='inactive')}")
            return [box]

        pwr_btn = (
            ActionBtn(t("cc_network.turn_off"), "bt:power:off", tone="danger_soft")
            if bt["powered"]
            else ActionBtn(t("cc_network.turn_on"), "bt:power:on", tone="apply")
        )
        scan_lbl = (t("cc_network.scanning")) if bt["scanning"] else (t("cc_network.start_scan_15s"))
        scan_btn = ActionBtn(scan_lbl, "bt:scan", tone="warn" if bt["scanning"] else "neutral", modal_active=bool(bt["scanning"])) if bt["powered"] else ""
        box.actions = [a for a in [st_badge, scan_btn, pwr_btn] if a]

        if not bt["powered"]:
            box.add_row(f"  {Txt(t('cc_network.turn_on_the_bluetooth_controller_t'), role='inactive')}")
            return [box]

        if not bt["paired"]:
            box.add_row(f"  {Txt(t('cc_network.no_paired_devices'), role='inactive')}")
        else:
            conn_mc = max(len(t("cc_network.disc")), len(t("cc_network.conn")))
            rows = []
            for d in bt["paired"]:
                is_c = bool(d["connected"])
                st = StateBadge(is_c, t("cc_network.connected"), t("cc_network.standby"))
                if d["battery"] is not None:
                    b_val = int(d["battery"])
                    b_role = "ok_soft" if b_val >= 50 else ("attention" if b_val >= 20 else "fault")
                    bat = Txt(f"\uf240 {b_val}%", role=b_role)
                else:
                    bat = Txt("\u2014", role="inactive")

                disc_uri = "bt:disc:" + d["mac"]
                conn_uri = "bt:conn:" + d["mac"]
                rem_uri = "bt:remove:" + d["mac"]
                conn_b = (
                    ActionBtn(t("cc_network.disc"), disc_uri, tone="danger_soft", min_chars=conn_mc)
                    if is_c
                    else ActionBtn(t("cc_network.conn"), conn_uri, tone="apply", min_chars=conn_mc)
                )
                rem_b = ActionBtn("x", rem_uri, tone="danger_soft")
                name_trunc = canvas.truncate_plain(d["name"], 21)
                dev_cell = Txt(f"{d['icon']} {name_trunc}", role="focus")

                rows.append([
                    dev_cell,
                    Txt(d["mac"], role="schema"),
                    st,
                    bat,
                    f"{conn_b} {rem_b}",
                ])
            box.add_table(
                headers=[
                    t("cc_network.device"),
                    t("cc_network.mac_address"),
                    t("cc_network.state"),
                    t("cc_network.bat"),
                    t("cc_network.actions"),
                ],
                widths=[23, 18, 13, 8, "flex"],
                rows=_spaced_rows(rows),
                aligns=["left", "left", "left", "left", "right"],
            )

        cards = [box]

        disc_list = bt.get("discovered", [])
        if disc_list or bt["scanning"]:
            opened, fold_btn = self._fold("bt_disc", default=bool(bt["scanning"]))
            disc_box = Box(
                t("cc_network.nearby_discovered_devices", p0=len(disc_list)),
                icon="\uf294",
                actions=[fold_btn],
            )
            if not opened:
                disc_box.add_row(
                    f"  {Txt(t('cc_network.click_expand_to_view_nearby_unpair'), role='schema')}"
                )
            else:
                if not disc_list:
                    disc_box.add_row(f"  {Txt(t('cc_network.scanning_for_nearby_devices'), role='inactive')}")
                else:
                    disc_rows = []
                    for d in disc_list[:8]:
                        pair_uri = "bt:pair:" + d["mac"]
                        disc_rows.append([
                            f"\uf294 {Txt(canvas.truncate_plain(d['name'], 26), role='val')}",
                            Txt(d["mac"], role="schema"),
                            ActionBtn(t("cc_network.pair_trust"), pair_uri, tone="apply"),
                        ])
                    disc_box.add_table(
                        headers=[
                            t("cc_network.device"),
                            t("cc_network.mac_address"),
                            t("cc_network.actions"),
                        ],
                        widths=[30, 19, "flex"],
                        rows=_spaced_rows(disc_rows),
                        aligns=["left", "left", "right"],
                    )
            cards.append(disc_box)

        return cards

    def _set_edit_field(self, iface: str, field: str, value) -> None:
        buf_all = self.get_state("edit_buffer", {})
        if not isinstance(buf_all, dict):
            buf_all = {}
        iface_buf = buf_all.get(iface, {})
        iface_buf[field] = value
        if field in ("ip_cidr", "gateway") and value:
            iface_buf["method"] = "manual"
        buf_all[iface] = iface_buf
        self.set_state("edit_buffer", buf_all)

    def _trigger_full_resync(self, mode_hint: str = None) -> None:
        with _NET_LOCK:
            for k in ("ip", "nm", "wifi", "bt", "vpn"):
                _NET_STATE["ts"][k] = 0.0
        hint = mode_hint or (self.active_tab if self.is_expanded else "compact")
        sel = self.get_state("selected_iface", "")
        self._schedule_bg_refresh(mode_hint=hint, sel_iface=sel, force=True)

    def _vpn_name(self, uuid: str) -> str:
        with _NET_LOCK:
            for v in _NET_STATE["vpns"]:
                if v.get("uuid") == uuid:
                    return str(v.get("name") or uuid[:8])
        return uuid[:8]

    def _jump_subtab(self, subtab: str, mode_hint: str) -> None:
        cc_p = self.shell.panel if (self.shell and hasattr(getattr(self.shell, "panel", None), "set_subtab")) else None
        if cc_p is not None and getattr(cc_p, "section", "") == "network":
            cc_p.set_subtab(subtab)
        else:
            self.active_tab = mode_hint
            STATE.set("control_center", "subtab_network", subtab)
        self._trigger_full_resync(mode_hint)
        if self.shell:
            self.shell.refresh_view()

    @staticmethod
    def _split_proxy_endpoint(s: str) -> tuple[str, str, str]:
        if s.startswith("["):
            host_part, brk, rest = s.partition("]")
            if not brk or not host_part[1:].strip() or (rest and not rest.startswith(":")):
                return "", ":", ""
            return host_part + "]", (":" if rest else ""), (rest[1:] if rest else "")
        if ":" in s:
            host, sep, port_s = s.rpartition(":")
            if ":" in host:
                return "", ":", ""
            return host, sep, port_s
        return s, "", ""

    @classmethod
    def _validate_proxy_endpoint(cls, raw: str) -> tuple[bool, str]:
        s = str(raw or "").strip()
        if not s:
            return True, ""
        if " " in s:
            return False, t("cc_network.err_proxy_endpoint")
        host, sep, port_s = cls._split_proxy_endpoint(s)
        if not host.strip():
            return False, t("cc_network.err_proxy_endpoint")
        if sep and (not port_s.isdigit() or not (1 <= int(port_s) <= 65535)):
            return False, t("cc_network.err_proxy_endpoint")
        return True, ""

    @staticmethod
    def _validate_int_range(raw: str, lo: int, hi: int) -> tuple[bool, str]:
        s = str(raw or "").strip()
        try:
            v = int(s)
        except ValueError:
            return False, t("cc_network.err_no_int", p0=s)
        if not (lo <= v <= hi):
            return False, t("cc_network.err_no_int", p0=s)
        return True, ""

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        val_raw = str(text or "").strip()

        if action_id == "net_virtual_br" and val_raw:
            self._run_net_cmd(
                "interfaces",
                ["nmcli", "connection", "add", "type", "bridge", "ifname", val_raw, "con-name", val_raw],
                t("cc_network.fb_bridge_created", p0=val_raw),
                "ifaces",
                verify_fn=lambda: (lambda r: (r[0], r[2] or r[1]))(probe.run_cmd_full(["nmcli", "connection", "show", val_raw], timeout=2.0)),
            )
            return
        if action_id == "net_vlan_create" and val_raw:
            parts = [p.strip() for p in val_raw.split("|")]
            if len(parts) < 2 or not parts[0] or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 4094):
                self._emit_fb("interfaces", False, t("cc_network.invalid_vlan_syntax_use_iface_id_e"))
                return
            p_dev, v_id = parts[0], parts[1]
            v_name = f"{p_dev}.{v_id}"
            self._run_net_cmd(
                "interfaces",
                ["nmcli", "connection", "add", "type", "vlan", "con-name", v_name, "ifname", v_name, "dev", p_dev, "id", v_id],
                t("cc_network.fb_vlan_created", p0=v_name),
                "ifaces",
                verify_fn=lambda: (lambda r: (r[0], r[2] or r[1]))(probe.run_cmd_full(["nmcli", "connection", "show", v_name], timeout=2.0)),
            )
            return
        if action_id == "wifi_hidden" and val_raw:
            parts = [p.strip() for p in val_raw.split("|", 1)]
            ssid = parts[0]
            pwd = parts[1] if len(parts) > 1 else ""
            cmd = ["nmcli", "dev", "wifi", "connect", ssid, "hidden", "yes"] + (["password", pwd] if pwd else [])
            self._run_net_cmd("wifi_bt", cmd, t("cc_network.fb_wifi_hidden_conn", p0=ssid), "wifi", timeout=15.0, clear_wifi_cache=True)
            return
        if action_id == "wifi_hotspot" and val_raw:
            parts = [p.strip() for p in val_raw.split("|", 1)]
            ssid = parts[0]
            pwd = parts[1] if len(parts) > 1 else ""
            if not ssid or len(pwd) < 8:
                self._emit_fb("wifi_bt", False, t("cc_network.err_hotspot_pwd_len"))
                return
            self._run_net_cmd(
                "wifi_bt",
                ["nmcli", "dev", "wifi", "hotspot", "ssid", ssid, "password", pwd],
                t("cc_network.fb_wifi_hotspot", p0=ssid),
                "wifi",
                timeout=10.0,
                clear_wifi_cache=True,
            )
            return
        if action_id.startswith("vpn_import|") and val_raw:
            v_type = action_id.split("|", 1)[1]
            fp = os.path.expanduser(val_raw)
            self._run_net_cmd(
                "vpn_proxy",
                ["nmcli", "connection", "import", "type", v_type, "file", fp],
                t("cc_network.fb_vpn_imported"),
                "vpn",
                timeout=6.0,
            )
            return
        if action_id.startswith("vpn_dns|"):
            u_id = action_id.split("|", 1)[1]
            ok_d, clean_d, err_d = validate_network_field("dns", val_raw)
            if not ok_d:
                self._emit_fb("vpn_proxy", False, err_d)
                return
            self._run_net_cmd(
                "vpn_proxy",
                ["nmcli", "connection", "modify", u_id, "ipv4.dns", clean_d.replace(",", " ")],
                t("cc_network.fb_vpn_dns", p0=self._vpn_name(u_id)),
                "vpn",
            )
            return
        if action_id in ("net_proxy_http", "net_proxy_socks"):
            kind_p = "http" if action_id == "net_proxy_http" else "socks"
            def_port = 8080 if kind_p == "http" else 1080
            ok_ep, err_ep = self._validate_proxy_endpoint(val_raw)
            if not ok_ep:
                self._emit_fb("vpn_proxy", False, err_ep)
                return
            if not val_raw:
                STATE.set("network", f"proxy_{kind_p}_host", "")
                STATE.set("network", f"proxy_{kind_p}_port", def_port)
            else:
                host, _, port_s = self._split_proxy_endpoint(val_raw)
                port = int(port_s) if (port_s.isdigit() and 1 <= int(port_s) <= 65535) else def_port
                STATE.set("network", f"proxy_{kind_p}_host", host.strip())
                STATE.set("network", f"proxy_{kind_p}_port", port)
            ok_w, err_w = _write_proxy_env()
            self._emit_fb("vpn_proxy", ok_w, t("cc_network.fb_proxy_saved") if ok_w else t("cc_network.fb_action_failed", p0=err_w))
            self._trigger_full_resync("vpn")
            return
        if action_id == "net_proxy_pac":
            STATE.set("network", "proxy_pac_url", val_raw)
            ok_w, err_w = _write_proxy_env()
            self._emit_fb("vpn_proxy", ok_w, t("cc_network.fb_proxy_saved") if ok_w else t("cc_network.fb_action_failed", p0=err_w))
            self._trigger_full_resync("vpn")
            return
        if action_id == "net_add_ping" and val_raw:
            parts = [p.strip() for p in val_raw.split("|", 1)]
            host = parts[-1]
            name = parts[0] if len(parts) > 1 else host
            cur_list = list(get_watchdog_targets(get_interfaces_fast()))
            cur_list.append({"name": name, "host": host})
            STATE.set("network", "custom_watchdog", cur_list)
            self._emit_fb("wan_routing", True, t("cc_network.fb_ping_added", p0=host))
            self._trigger_full_resync("vpn")
            return

        if "|" not in action_id:
            return
        kind, target = action_id.split("|", 1)

        if kind == "net_rename_prof" and "|" in target and val_raw:
            iface, uuid = target.split("|", 1)

            def _ren():
                ok, out, err = probe.run_cmd_full(["nmcli", "connection", "modify", uuid, "connection.id", val_raw], timeout=3.0)
                if not ok:
                    return False, (err or out)
                ok_v, out_v, _ = probe.run_cmd_full(["nmcli", "-g", "connection.id", "connection", "show", uuid], timeout=1.5)
                if not ok_v or out_v.strip() != val_raw:
                    return False, t("cc_network.err_nm_verify_readback")
                return True, t("cc_network.fb_prof_renamed", p0=val_raw)

            self._run_nm_profile_action(iface, _ren)
            return

        if kind in ("net_ip", "net_gw", "net_dns", "net_ip6_dns", "net_mtu", "net_cmac"):
            field_map = {
                "net_ip": "ip_cidr",
                "net_gw": "gateway",
                "net_dns": "dns",
                "net_ip6_dns": "ip6_dns",
                "net_mtu": "mtu",
                "net_cmac": "cloned_mac",
            }
            f_name = field_map[kind]
            ok_v, norm_v, err_v = validate_network_field(f_name, val_raw)
            if not ok_v:
                self._emit_fb("interfaces", False, err_v)
                return
            self._set_edit_field(target, f_name, norm_v)
            return

        if kind == "net_add_route" and val_raw:
            ok_r, norm_r, err_r = validate_network_field("static_route", val_raw)
            if not ok_r:
                self._emit_fb("interfaces", False, err_r)
                return
            with _NET_LOCK:
                nm_c = copy.deepcopy(_NET_STATE["nm_profiles"].get(target, {}))
            buf_all = self.get_state("edit_buffer", {})
            cur_rts = list((buf_all.get(target, {}) if isinstance(buf_all, dict) else {}).get("static_routes", nm_c.get("static_routes", [])))
            if norm_r not in cur_rts:
                cur_rts.append(norm_r)
            self._set_edit_field(target, "static_routes", cur_rts)
            return

        if kind == "net_new_prof" and val_raw:
            self._execute_verified_create(target, val_raw)
        elif kind == "net_cname" and val_raw:
            self._set_edit_field(target, "con_name", val_raw)
        elif kind == "net_prio":
            ok_i, err_i = self._validate_int_range(val_raw, -999, 999)
            if ok_i:
                self._set_edit_field(target, "autoconnect_priority", int(val_raw))
            else:
                self._emit_fb("interfaces", False, err_i)
        elif kind == "net_search":
            self._set_edit_field(target, "dns_search", val_raw)
        elif kind == "net_metric":
            ok_i, err_i = self._validate_int_range(val_raw, -1, 99999)
            if ok_i:
                self._set_edit_field(target, "route_metric", int(val_raw))
            else:
                self._emit_fb("interfaces", False, err_i)
        elif kind == "wifi_pass" and val_raw:
            self._run_net_cmd(
                "wifi_bt",
                ["nmcli", "dev", "wifi", "connect", target, "password", val_raw],
                t("cc_network.fb_wifi_conn", p0=target),
                "wifi",
                timeout=15.0,
                clear_wifi_cache=True,
            )

    def _execute_verified_create(self, iface: str, prof_name: str = None) -> None:
        self._run_nm_profile_action(
            iface,
            lambda: (lambda res: (res[0], res[2]))(create_verified_nm_profile(iface, prof_name or iface)),
        )

    def on_link(self, uri: str) -> None:
        log.debug(f"NetworkPanel.on_link: {uri}")

        if uri == "net:toggle_unit":
            self.on_bar_action("toggle-unit")
            self.request_throttled_refresh()
        elif uri == "net:force_refresh":
            self._trigger_full_resync()
            self.request_throttled_refresh()
        elif uri.startswith("net:toggle_sec:"):
            sec_key = uri.split(":", 2)[2]
            cur_map = self.get_state("open_sections", {})
            if not isinstance(cur_map, dict):
                cur_map = {}
            cur_map[sec_key] = not bool(cur_map.get(sec_key, False))
            self.set_state("open_sections", cur_map)
            self.request_throttled_refresh()
        elif uri == "net:inline_virtual":
            start_inline_input(
                "net_virtual_br",
                shell=self.shell,
                trigger_uri=uri,
                initial_text="br0",
                max_len=20,
                validator=lambda s: bool(s.strip()),
                on_commit=lambda s: self.on_prompt_submit("net_virtual_br", s),
            )
        elif uri == "net:inline_vlan":
            ifaces = get_interfaces_fast()
            fallback_if = ifaces[0]["name"] if ifaces else ""
            sel = self.get_state("selected_iface", fallback_if)
            start_inline_input(
                "net_vlan_create",
                shell=self.shell,
                trigger_uri=uri,
                initial_text=f"{sel}|10" if sel else "",
                max_len=28,
                validator=lambda s: "|" in s and bool(s.split("|", 1)[0].strip()) and s.split("|", 1)[1].strip().isdigit() and 1 <= int(s.split("|", 1)[1].strip()) <= 4094,
                on_commit=lambda s: self.on_prompt_submit("net_vlan_create", s),
            )
        elif uri.startswith("net:inline_field:"):
            _, _, kind, iface = uri.split(":", 3)
            meta_m = {
                "net_prio": ("autoconnect_priority", 6, "int_prio"),
                "net_ip": ("ip_cidr", 24, "ip_cidr"),
                "net_gw": ("gateway", 20, "gateway"),
                "net_dns": ("dns", 40, "dns"),
                "net_search": ("dns_search", 40, ""),
                "net_metric": ("route_metric", 6, "int_metric"),
                "net_ip6_dns": ("ip6_dns", 48, "ip6_dns"),
                "net_mtu": ("mtu", 8, "mtu"),
                "net_cmac": ("cloned_mac", 20, "cloned_mac"),
            }.get(kind)
            if meta_m:
                fld, ml, val_fld = meta_m
                with _NET_LOCK:
                    nm_c = copy.deepcopy(_NET_STATE["nm_profiles"].get(iface, {}))
                buf_c = (self.get_state("edit_buffer", {}) or {}).get(iface, {})
                raw_v = buf_c.get(fld, nm_c.get(fld, ""))
                cur_val = "" if raw_v is None else str(raw_v)
                if val_fld == "int_prio":
                    vfn = lambda s: self._validate_int_range(s, -999, 999)
                elif val_fld == "int_metric":
                    vfn = lambda s: self._validate_int_range(s, -1, 99999)
                elif val_fld:
                    vfn = lambda s, vf=val_fld: (lambda res: (res[0], res[2]))(validate_network_field(vf, s))
                else:
                    vfn = None
                start_inline_input(
                    entry_id=f"{kind}_{iface}",
                    shell=self.shell,
                    trigger_uri=uri,
                    initial_text=cur_val,
                    max_len=ml,
                    validator=vfn,
                    on_commit=lambda s, k=kind, tg=iface: self.on_prompt_submit(f"{k}|{tg}", s),
                )
        elif uri.startswith("net:prompt_add_route:"):
            iface = uri.split(":", 2)[2]
            self.request_prompt(f"net_add_route|{iface}", t("cc_net_wired.route_prompt_ph"), "", title=t("cc_net_wired.add_route"), required=True)
        elif uri == "net:flush_dns":
            def _flush_d():
                did_any = False
                errs = []
                if probe.has_bin("resolvectl"):
                    ok_r, out_r, err_r = probe.run_cmd_full(["resolvectl", "flush-caches"], timeout=2.0)
                    if ok_r:
                        did_any = True
                    else:
                        errs.append(err_r or out_r or "resolvectl")
                if probe.has_bin("pkill"):
                    ok_p, _, _ = probe.run_cmd_full(["pkill", "-HUP", "-x", "dnsmasq"], timeout=1.0)
                    if ok_p:
                        did_any = True
                if did_any:
                    return True, t("cc_network.fb_dns_flushed")
                return False, t("cc_network.fb_action_failed", p0=(errs[0] if errs else t("cc_network.err_no_dns_cache_service")))

            def _done_flush_d(res):
                self._emit_fb("wan_routing", res[0], res[1])
                self._trigger_full_resync("vpn")
                return False

            probe.run_async(_flush_d, on_done=_done_flush_d)
        elif uri == "net:flush_arp":
            if not probe.has_bin("ip"):
                self._emit_fb("wan_routing", False, t("cc_network.fb_action_failed", p0="ip"))
                return
            self._run_net_cmd(
                "wan_routing",
                ["ip", "neigh", "flush", "nud", "stale"],
                t("cc_network.fb_arp_flushed"),
                "vpn",
                timeout=2.0,
            )
        elif uri.startswith("net:del_ping:"):
            host_to_rm = uri.split(":", 2)[2]
            cur_list = [t_item for t_item in get_watchdog_targets(get_interfaces_fast()) if t_item.get("host") != host_to_rm]
            STATE.set("network", "custom_watchdog", cur_list)
            self._emit_fb("wan_routing", True, t("cc_network.fb_ping_del", p0=host_to_rm))
            self._trigger_full_resync("vpn")
        elif uri.startswith("net:if_filter:"):
            flt = uri.split(":", 2)[2]
            self.set_state("iface_filter", flt)
            self.request_throttled_refresh()
        elif uri == "net:jump_wifi_tab":
            self._jump_subtab("wifi_bt", "wifi")
        elif uri.startswith("net:jump_iface:"):
            iface = uri.split(":", 2)[2]
            self.set_state("selected_iface", iface)
            self.set_state("iface_filter", "all")
            self._jump_subtab("interfaces", "ifaces")
        elif uri.startswith("net:jump_wifi_prof:"):
            _, _, iface, uuid = uri.split(":", 3)
            self.set_state("selected_iface", iface)
            self.set_state("iface_filter", "all")
            STATE.set("network", f"selected_prof_{iface}", uuid)
            self._jump_subtab("interfaces", "ifaces")
        elif uri.startswith("net:sel_iface:"):
            iface = uri.split(":", 2)[2]
            self.set_state("selected_iface", iface)
            self._trigger_full_resync("ifaces")
            self.request_throttled_refresh()
        elif uri.startswith("net:mon:"):
            iface = uri.split(":", 2)[2]
            self.set_state("monitored_iface", iface)
            self.set_state("selected_iface", iface)
            self.request_throttled_refresh()
        elif uri.startswith("net:ifup:") or uri.startswith("net:ifdown:"):
            parts = uri.split(":", 2)
            up = (parts[1] == "ifup")
            iface = parts[2]

            def _toggle_if():
                if _is_nm_running():
                    if up:
                        probe.run_cmd_full(["nmcli", "device", "set", iface, "managed", "yes"], timeout=2.0)
                    ok, out, err = probe.run_cmd_full(["nmcli", "device", "connect" if up else "disconnect", iface], timeout=6.0)
                elif probe.has_bin("ip"):
                    ok, out, err = probe.run_cmd_full(["ip", "link", "set", iface, "up" if up else "down"], timeout=3.0)
                else:
                    return False, t("cc_network.fb_action_failed", p0="nmcli/ip")
                if not ok:
                    return False, t("cc_network.fb_action_failed", p0=(err or out or iface))
                # Read back kernel operstate / link flag
                time.sleep(0.15)
                oper = _read_sysfs_str(f"/sys/class/net/{iface}/operstate", "down").lower()
                if not up and oper == "up":
                    return False, t("cc_network.fb_action_failed", p0=iface)
                return True, t("cc_network.fb_iface_up" if up else "cc_network.fb_iface_down", p0=iface)

            def _done_if(res):
                self._emit_fb("interfaces", res[0], res[1])
                self._trigger_full_resync()
                return False

            probe.run_async(_toggle_if, on_done=_done_if)
        elif uri.startswith("net:buf:"):
            _, _, iface, key, val = uri.split(":", 4)
            if key in ("route_metric", "autoconnect_priority"):
                try:
                    val = int(val)
                except ValueError:
                    pass
            self._set_edit_field(iface, key, val)
            self.request_throttled_refresh()
        elif uri.startswith("net:del_route:"):
            _, _, iface, idx_s = uri.split(":", 3)
            with _NET_LOCK:
                nm_c = copy.deepcopy(_NET_STATE["nm_profiles"].get(iface, {}))
            buf_all = self.get_state("edit_buffer", {})
            cur_rts = list((buf_all.get(iface, {}) if isinstance(buf_all, dict) else {}).get("static_routes", nm_c.get("static_routes", [])))
            idx = int(idx_s)
            if 0 <= idx < len(cur_rts):
                cur_rts.pop(idx)
                self._set_edit_field(iface, "static_routes", cur_rts)
            self.request_throttled_refresh()
        elif uri.startswith("net:nm_reset:"):
            iface = uri.split(":", 2)[2]
            buf_all = self.get_state("edit_buffer", {})
            if isinstance(buf_all, dict) and iface in buf_all:
                del buf_all[iface]
                self.set_state("edit_buffer", buf_all)
            STATE.set("network", "fb_interfaces", {})
            self.request_throttled_refresh()
        elif uri.startswith("net:nm_create:"):
            iface = uri.split(":", 2)[2]
            self._execute_verified_create(iface, iface)
        elif uri.startswith("net:inline_new_prof:"):
            iface = uri.split(":", 2)[2]
            start_inline_input(
                f"net_new_prof_{iface}",
                shell=self.shell,
                trigger_uri=uri,
                initial_text=f"{iface}-static",
                max_len=32,
                validator=lambda s: bool(s.strip()),
                on_commit=lambda s, i=iface: self.on_prompt_submit(f"net_new_prof|{i}", s),
            )
        elif uri.startswith("net:nm_sel_prof:"):
            _, _, iface, uuid = uri.split(":", 3)
            STATE.set("network", f"selected_prof_{iface}", uuid)
            buf_all = self.get_state("edit_buffer", {})
            if isinstance(buf_all, dict) and iface in buf_all:
                del buf_all[iface]
                self.set_state("edit_buffer", buf_all)
            self._trigger_full_resync("ifaces")
        elif uri.startswith("net:nm_clone_prof:"):
            _, _, iface, uuid = uri.split(":", 3)
            self._run_nm_profile_action(
                iface,
                lambda: (lambda res: (res[0], res[2]))(clone_verified_nm_profile(iface, uuid)),
            )
        elif uri.startswith("net:nm_up_prof:") or uri.startswith("net:nm_down_prof:"):
            parts = uri.split(":", 3)
            is_up_act = (parts[1] == "nm_up_prof")
            iface, uuid = parts[2], parts[3]

            def _prof_updown():
                if is_up_act:
                    probe.run_cmd_full(["nmcli", "device", "set", iface, "managed", "yes"], timeout=2.0)
                    ok, out, err = probe.run_cmd_full(["nmcli", "connection", "up", uuid, "ifname", iface], timeout=10.0)
                    if not ok:
                        ok, out, err = probe.run_cmd_full(["nmcli", "connection", "up", uuid], timeout=10.0)
                else:
                    ok, out, err = probe.run_cmd_full(["nmcli", "connection", "down", uuid], timeout=6.0)
                msg = t("cc_network.fb_prof_up" if is_up_act else "cc_network.fb_prof_down", p0=iface) if ok else (err or out)
                return ok, msg

            self._run_nm_profile_action(iface, _prof_updown, resync_ip=True)
        elif uri.startswith("net:nm_auto_prof:"):
            _, _, iface, uuid, new_ac = uri.split(":", 4)

            def _prof_ac():
                ok, out, err = probe.run_cmd_full(["nmcli", "connection", "modify", uuid, "connection.autoconnect", new_ac], timeout=3.0)
                if not ok:
                    return False, (err or out)
                return True, t("cc_network.fb_prof_auto", p0=new_ac.upper())

            self._run_nm_profile_action(iface, _prof_ac)
        elif uri.startswith("net:inline_rename_prof:"):
            _, _, iface, uuid = uri.split(":", 3)
            with _NET_LOCK:
                nm_c = copy.deepcopy(_NET_STATE["nm_profiles"].get(iface, {}))
            cur_n = next((p["name"] for p in nm_c.get("available_profiles", []) if p["uuid"] == uuid), nm_c.get("con_name", iface))
            start_inline_input(
                f"net_ren_{uuid}",
                shell=self.shell,
                trigger_uri=uri,
                initial_text=cur_n,
                max_len=36,
                validator=lambda s: bool(s.strip()),
                on_commit=lambda s, i=iface, u=uuid: self.on_prompt_submit(f"net_rename_prof|{i}|{u}", s),
            )
        elif uri.startswith("net:nm_del_prof:"):
            _, _, iface, uuid = uri.split(":", 3)

            def _prof_del():
                ok, out, err = probe.run_cmd_full(["nmcli", "connection", "delete", uuid], timeout=4.0)
                return ok, (t("cc_network.fb_prof_del") if ok else (err or out))

            self._run_nm_profile_action(iface, _prof_del)
        elif uri.startswith("net:nm_apply:"):
            _, _, iface, uuid = uri.split(":", 3)
            buf_all = self.get_state("edit_buffer", {})
            buf = copy.deepcopy(buf_all.get(iface, {}) if isinstance(buf_all, dict) else {})
            with _NET_LOCK:
                nm_cur = copy.deepcopy(_NET_STATE["nm_profiles"].get(iface, {}))
            ifaces_now = get_interfaces_fast()
            iface_up = any(i["name"] == iface and i["up"] for i in ifaces_now)
            self._run_nm_profile_action(
                iface,
                lambda: apply_and_verify_nm_profile(iface, uuid, buf, nm_cur, iface_up=iface_up),
                resync_ip=True,
                clear_buf_on_ok=True,
            )
        elif uri.startswith("net:proxy_mode:"):
            m_val = uri.split(":", 2)[2]
            STATE.set("network", "proxy_mode", m_val)
            ok_w, err_w = _write_proxy_env()
            self._emit_fb("vpn_proxy", ok_w, t("cc_network.fb_proxy_mode", p0=m_val.upper()) if ok_w else t("cc_network.fb_action_failed", p0=err_w))
            self._trigger_full_resync("vpn")
        elif uri in ("net:inline_proxy_http", "net:inline_proxy_socks"):
            kind_p = "http" if uri == "net:inline_proxy_http" else "socks"
            with _NET_LOCK:
                pr = copy.deepcopy(_NET_STATE["proxy"])
            cur = f"{pr[f'{kind_p}_host']}:{pr[f'{kind_p}_port']}" if pr[f"{kind_p}_host"] else ""
            start_inline_input(
                f"net_proxy_{kind_p}",
                shell=self.shell,
                trigger_uri=uri,
                initial_text=cur,
                max_len=36,
                validator=self._validate_proxy_endpoint,
                on_commit=lambda s, aid=f"net_proxy_{kind_p}": self.on_prompt_submit(aid, s),
            )
        elif uri == "net:prompt_proxy_pac":
            with _NET_LOCK:
                pr = copy.deepcopy(_NET_STATE["proxy"])
            self.request_prompt("net_proxy_pac", t("cc_network.pac_autoconfig_url"), pr["autoconfig_url"], title=t("cc_network.pac_url"))
        elif uri == "net:inline_add_ping":
            start_inline_input("net_add_ping", shell=self.shell, trigger_uri=uri, initial_text="", max_len=40, validator=lambda s: bool(s.strip()), on_commit=lambda s: self.on_prompt_submit("net_add_ping", s))
        elif uri in ("net:refresh_wan", "net:refresh_ping"):
            target_f = _cache_path("wan_cache.json" if uri == "net:refresh_wan" else "net_ping_cache.json")
            if os.path.exists(target_f):
                try:
                    os.remove(target_f)
                except Exception:
                    pass
            with _NET_LOCK:
                _NET_STATE["ts"]["wan" if uri == "net:refresh_wan" else "ping"] = 0.0
            self._emit_fb("wan_routing", True, t("cc_network.fb_wan_refresh"))
            self._schedule_bg_refresh(mode_hint="vpn", force=True)

        elif uri.startswith("wifi:"):
            parts = uri.split(":", 2)
            act = parts[1]
            rest = parts[2] if len(parts) > 2 else ""
            if act == "radio":
                with _NET_LOCK:
                    _NET_STATE["wifi"]["enabled"] = (rest == "on")
                self.request_throttled_refresh()
                self._run_net_cmd("wifi_bt", ["nmcli", "radio", "wifi", rest], t("cc_network.fb_wifi_radio", p0=rest.upper()), "wifi", timeout=3.0)
            elif act == "powersave":
                if not probe.has_bin("iw"):
                    self._emit_fb("wifi_bt", False, t("cc_network.fb_action_failed", p0="iw"))
                    return
                wifi_devs = [i["name"] for i in get_interfaces_fast() if i["kind"] == "wifi"]
                if not wifi_devs:
                    self._emit_fb("wifi_bt", False, t("cc_net_wifi.no_wifi_adapter"))
                    return
                ps_mode = "on" if rest == "3" else "off"
                target_wdev = wifi_devs[0]
                with _NET_LOCK:
                    _NET_STATE["wifi"]["powersave"] = ps_mode

                def _verify_ps():
                    ok_v, out_v, err_v = probe.run_cmd_full(["iw", "dev", target_wdev, "get", "power_save"], timeout=1.5)
                    if ok_v and f"power save: {ps_mode}" in out_v.lower():
                        return True, ""
                    return False, (err_v or out_v or target_wdev)

                self._run_net_cmd(
                    "wifi_bt",
                    ["iw", "dev", target_wdev, "set", "power_save", ps_mode],
                    t("cc_network.fb_wifi_powersave", p0=ps_mode.upper()),
                    "wifi",
                    timeout=2.0,
                    verify_fn=_verify_ps,
                )
            elif act == "band":
                u_id, b_val = rest.split(":", 1)
                band_arg = "" if b_val == "auto" else b_val
                self._run_net_cmd(
                    "wifi_bt",
                    ["nmcli", "connection", "modify", u_id, "802-11-wireless.band", band_arg],
                    t("cc_network.fb_wifi_band", p0=b_val),
                    "wifi",
                    timeout=3.0,
                )
            elif act == "mac_priv":
                u_id, m_val = rest.split(":", 1)
                self._run_net_cmd(
                    "wifi_bt",
                    ["nmcli", "connection", "modify", u_id, "802-11-wireless.cloned-mac-address", m_val],
                    t("cc_network.fb_wifi_mac_priv", p0=m_val),
                    "wifi",
                    timeout=3.0,
                )
            elif act == "rescan":
                self._run_net_cmd(
                    "wifi_bt",
                    ["nmcli", "dev", "wifi", "rescan"],
                    t("cc_network.fb_wifi_scan"),
                    "wifi",
                    timeout=6.0,
                    clear_wifi_cache=True,
                )
            elif act == "prompt_hidden":
                self.request_prompt("wifi_hidden", t("cc_network.ssid_password_optional"), "", title=t("cc_network.connect_hidden_wi_fi"), required=True)
            elif act == "prompt_hotspot":
                host_prefix = socket.gethostname().split(".")[0] or "ncui"
                self.request_prompt("wifi_hotspot", t("cc_network.ssid_hotspot_password_wpa2_min_8_c"), f"{host_prefix}-AP | ", title=t("cc_network.start_wi_fi_hotspot"), required=True)
            elif act == "prompt_pass" and rest:
                self.request_prompt(f"wifi_pass|{rest}", t("cc_network.enter_password_for", p0=rest), secret=True, title=rest, required=True)
            elif act == "toggle_auto":
                u_id, new_ac = rest.split(":", 1)
                self._run_net_cmd(
                    "wifi_bt",
                    ["nmcli", "connection", "modify", u_id, "connection.autoconnect", new_ac],
                    t("cc_network.fb_wifi_auto", p0=new_ac.upper()),
                    "wifi",
                    timeout=3.0,
                )
            elif act == "up_uuid":
                self._run_net_cmd("wifi_bt", ["nmcli", "connection", "up", rest], t("cc_network.fb_wifi_up", p0=rest[:8]), "wifi", timeout=8.0)
            elif act == "del_uuid":
                self._run_net_cmd("wifi_bt", ["nmcli", "connection", "delete", rest], t("cc_network.fb_wifi_del"), "wifi", timeout=4.0)
            elif act == "disc":
                self._run_net_cmd("wifi_bt", ["nmcli", "connection", "down", rest], t("cc_network.fb_wifi_disc", p0=rest), "wifi", timeout=5.0, clear_wifi_cache=True)
            elif act == "forget":
                self._run_net_cmd("wifi_bt", ["nmcli", "connection", "delete", rest], t("cc_network.fb_wifi_forget", p0=rest), "wifi", timeout=5.0, clear_wifi_cache=True)
            elif act == "conn":
                ssid, sep, sec = rest.rpartition(":")
                if not sep:
                    ssid, sec = rest, ""
                sec = sec or "OPEN"
                is_open = sec in ("", "OPEN", "--")
                with _NET_LOCK:
                    saved_profiles = set(_NET_STATE["saved_wifi_ssids"])

                if ssid not in saved_profiles and not is_open:
                    self.request_prompt(f"wifi_pass|{ssid}", t("cc_network.enter_password_for", p0=ssid), secret=True)
                    return

                def _try_conn():
                    ok, out, err = probe.run_cmd_full(["nmcli", "connection", "up", ssid], timeout=8.0)
                    if not ok and is_open:
                        ok, out, err = probe.run_cmd_full(["nmcli", "dev", "wifi", "connect", ssid], timeout=10.0)
                    wc = _cache_path("net_wifi_cache.json")
                    if os.path.exists(wc):
                        try:
                            os.remove(wc)
                        except Exception:
                            pass
                    return ok, (err or out)

                def _after_conn(res):
                    ok, err_s = res
                    if not ok and self.shell:
                        self.request_prompt(f"wifi_pass|{ssid}", t("cc_network.enter_password_for", p0=ssid), secret=True)
                    else:
                        self._emit_fb("wifi_bt", ok, t("cc_network.fb_wifi_conn", p0=ssid) if ok else t("cc_network.fb_action_failed", p0=err_s))
                        self._trigger_full_resync("wifi")
                    return False

                probe.run_async(_try_conn, on_done=_after_conn)

        elif uri.startswith("bt:"):
            parts = uri.split(":", 2)
            act = parts[1]
            arg = parts[2] if len(parts) > 2 else ""
            if not probe.has_bin("bluetoothctl"):
                self._emit_fb("wifi_bt", False, t("cc_network.fb_action_failed", p0="bluetoothctl"))
                return
            if act == "power":
                with _NET_LOCK:
                    _NET_STATE["bt"]["powered"] = (arg == "on")
                self.request_throttled_refresh()
                self._run_net_cmd("wifi_bt", ["bluetoothctl", "power", arg], t("cc_network.fb_bt_power", p0=arg.upper()), "bt", timeout=3.0)
            elif act == "scan":
                probe.spawn_bg(["bluetoothctl", "--timeout", "15", "scan", "on"])
                with _NET_LOCK:
                    _NET_STATE["bt"]["scanning"] = True
                self._state_owner().set_state("bt_disc", True)
                self._emit_fb("wifi_bt", True, t("cc_network.fb_bt_scan"))
                from gi.repository import GLib
                GLib.timeout_add(1500, lambda: self._trigger_full_resync("bt") or False)
            elif act in ("conn", "disc", "remove", "pair") and arg:
                bt_cmd = {"conn": "connect", "disc": "disconnect", "remove": "remove", "pair": "pair"}[act]

                def _do_bt():
                    if act == "pair":
                        for sub_c, tout in (("pair", 10.0), ("trust", 3.0), ("connect", 6.0)):
                            ok_b, out_b, err_b = probe.run_cmd_full(["bluetoothctl", sub_c, arg], timeout=tout)
                            if not ok_b:
                                return False, (err_b or out_b or sub_c)
                        return True, ""
                    ok_b, out_b, err_b = probe.run_cmd_full(["bluetoothctl", bt_cmd, arg], timeout=8.0)
                    return ok_b, (err_b or out_b)

                def _done_bt(res):
                    ok, err_s = res
                    self._emit_fb("wifi_bt", ok, t("cc_network.fb_bt_conn", p0=arg, p1=act) if ok else t("cc_network.fb_action_failed", p0=err_s))
                    self._trigger_full_resync("bt")
                    return False

                probe.run_async(_do_bt, on_done=_done_bt)

        elif uri.startswith("vpn:"):
            parts = uri.split(":", 2)
            act = parts[1]
            rest = parts[2] if len(parts) > 2 else ""
            if act == "prompt_import":
                exts = [".conf"] if rest == "wireguard" else [".ovpn", ".conf"]
                self.request_prompt(f"vpn_import|{rest}", t("cc_network.path_to_file", p0="conf" if rest == "wireguard" else "ovpn"), "~/", title=t("cc_network.import", p0=rest.title()), required=True, completion=("path_ext", exts))
            elif act == "toggle_auto":
                u_id, ac_val = rest.split(":", 1)
                self._run_net_cmd(
                    "vpn_proxy",
                    ["nmcli", "connection", "modify", u_id, "connection.autoconnect", ac_val],
                    t("cc_network.fb_vpn_auto", p0=self._vpn_name(u_id), p1=ac_val.upper()),
                    "vpn",
                    timeout=3.0,
                )
            elif act == "toggle_split":
                u_id, nd_val = rest.split(":", 1)
                lbl = t("cc_net_vpn.split_tunnel") if nd_val == "yes" else t("cc_net_vpn.full_tunnel")
                self._run_net_cmd(
                    "vpn_proxy",
                    ["nmcli", "connection", "modify", u_id, "ipv4.never-default", nd_val],
                    t("cc_network.fb_vpn_split", p0=self._vpn_name(u_id), p1=lbl),
                    "vpn",
                    timeout=4.0,
                )
            elif act == "inline_dns":
                start_inline_input(
                    f"vpn_dns_{rest}",
                    shell=self.shell,
                    trigger_uri=uri,
                    initial_text="",
                    max_len=40,
                    validator=lambda s: (lambda res: (res[0], res[2]))(validate_network_field("dns", s)),
                    on_commit=lambda s, u=rest: self.on_prompt_submit(f"vpn_dns|{u}", s),
                )
            elif act == "del":
                self._run_net_cmd("vpn_proxy", ["nmcli", "connection", "delete", rest], t("cc_network.fb_vpn_del"), "vpn", timeout=4.0)
            elif act in ("up", "down"):
                self._run_net_cmd(
                    "vpn_proxy",
                    ["nmcli", "connection", "up" if act == "up" else "down", rest],
                    t("cc_network.fb_vpn_state", p0=self._vpn_name(rest), p1=act.upper()),
                    "vpn",
                    timeout=8.0,
                )


_CC_NET_INSTANCE = None


def _get_cc_net_panel(cc_panel) -> Panel:
    global _CC_NET_INSTANCE
    if _CC_NET_INSTANCE is None:
        _CC_NET_INSTANCE = Panel(shell=cc_panel.shell)
    else:
        _CC_NET_INSTANCE.shell = cc_panel.shell
    return _CC_NET_INSTANCE


def build_cc_network_cards(cc_panel, canvas: Canvas, active_sub: str) -> list:
    net_p = _get_cc_net_panel(cc_panel)
    net_p._update_live_speeds()
    ifaces = get_interfaces_fast(net_p._speeds)
    mon_iface, unit_mode = net_p._resolve_mon_and_unit(ifaces)
    return net_p._build_expanded_view(canvas, ifaces, mon_iface, unit_mode, forced_tab=active_sub, is_cc=True)


def handle_cc_network_link(cc_panel, uri: str) -> bool:
    if uri.startswith(("net:", "wifi:", "bt:", "vpn:")):
        net_p = _get_cc_net_panel(cc_panel)
        net_p.on_link(uri)
        return True
    return False


def handle_cc_network_prompt(cc_panel, action_id: str, text: str) -> bool:
    if action_id.startswith(("net_", "wifi_", "vpn_")):
        net_p = _get_cc_net_panel(cc_panel)
        net_p.on_prompt_submit(action_id, text)
        return True
    return False
