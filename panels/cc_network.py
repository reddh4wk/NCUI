#!/usr/bin/env python3
import copy
import ipaddress
import json
import os
import re
import threading
import time
import urllib.request

from gi.repository import GLib

from core.config import CONFIG, CONFIG_DIR, THEME, get_network_show_bar_speed
from core.state import STATE
from core.i18n import get_active_language
from core import probe, log
from ui.canvas import Canvas
from ui.kit import (
    PALETTE,
    Box,
    Btn,
    Badge,
    StatusDot,
    ToggleExpandBtn,
    Divider,
    Txt,
    SelectPill,
    ActionBtn,
    StateBadge,
    FoldToggle,
    InspectorCell,
    ui_pill,
    ui_act_btn,
    ui_status_badge,
    ui_fold_toggle,
)
from ui.markup import format_speed
from panels.base import BasePanel

BAR_SAMPLE_FILE = "/tmp/nc_net_bar_sample.json"
FAST_NET_CACHE_FILE = "/tmp/nc_net_fast_cache.json"
WAN_CACHE_FILE = os.path.expanduser("~/.cache/nc-ui/wan_cache.json")
PING_CACHE_FILE = "/tmp/nc_net_ping_cache.json"
WIFI_CACHE_FILE = "/tmp/nc_net_wifi_cache.json"
PROXY_ENV_FILE = os.path.join(CONFIG_DIR, "proxy.env")

SIGNAL_BARS = ["\u2581___", "\u2581\u2583__", "\u2581\u2583\u2585_", "\u2581\u2583\u2585\u2587"]
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

_NET_LOCK = threading.Lock()
_BG_WORKER_RUNNING = False
_APPLY_FEEDBACK = {}

_NET_STATE = {
    "initialized": False,
    "nm_running": True,
    "ip_map": {},
    "ip6_map": {},
    "gw_map": {},
    "routes": [],
    "nm_profiles": {},
    "wifi": {"available": True, "enabled": False, "powersave": "default", "networks": [], "saved": []},
    "bt": {"available": True, "powered": False, "scanning": False, "paired": [], "discovered": []},
    "vpns": [],
    "proxy": {"mode": "none", "http_host": "", "http_port": 8080, "socks_host": "", "socks_port": 1080, "autoconfig_url": ""},
    "wan": {"ip": "Rilevamento...", "badge": "", "badge_style": "info"},
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


def is_sec_open(panel, sec_key: str, default: bool = False) -> bool:
    cur_map = panel.get_state("open_sections", {})
    if not isinstance(cur_map, dict):
        return default
    return bool(cur_map.get(sec_key, default))


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
                return False, "", f"Indirizzo IPv4 '{s}' riservato o non valido."
            return True, str(iface_ip), ""
        except ValueError:
            return False, "", f"Formato IPv4/CIDR non valido: '{raw_val}' (usa es. 10.10.0.10/24)."

    if field == "gateway":
        if not s:
            return True, "", ""
        try:
            gw_ip = ipaddress.IPv4Address(s)
            if gw_ip.is_unspecified or gw_ip.is_multicast:
                return False, "", f"Gateway '{s}' non valido."
            return True, str(gw_ip), ""
        except ValueError:
            return False, "", f"Indirizzo Gateway IPv4 non valido: '{raw_val}'."

    if field == "dns":
        if not s:
            return True, "", ""
        tokens = [t.strip() for t in re.split(r"[,;\s]+", s) if t.strip()]
        clean_ips = []
        for tk in tokens:
            try:
                ip_obj = ipaddress.IPv4Address(tk)
                clean_ips.append(str(ip_obj))
            except ValueError:
                return False, "", f"Server DNS IPv4 non valido: '{tk}'."
        return True, ", ".join(clean_ips), ""

    if field == "ip6_dns":
        if not s:
            return True, "", ""
        tokens = [t.strip() for t in re.split(r"[,;\s]+", s) if t.strip()]
        clean_ips = []
        for tk in tokens:
            try:
                ip_obj = ipaddress.IPv6Address(tk)
                clean_ips.append(str(ip_obj))
            except ValueError:
                return False, "", f"Server DNS IPv6 non valido: '{tk}'."
        return True, ", ".join(clean_ips), ""

    if field == "mtu":
        if not s or s.lower() == "auto":
            return True, "auto", ""
        if not s.isdigit() or not (68 <= int(s) <= 9216):
            return False, "", f"MTU '{s}' fuori range (valori ammessi: 68 .. 9216 oppure 'auto')."
        return True, s, ""

    if field == "cloned_mac":
        if not s:
            return True, "", ""
        if s.lower() in ("permanent", "preserve", "random", "stable"):
            return True, s.lower(), ""
        if not _MAC_RE.match(s):
            return False, "", f"MAC Address '{s}' non valido (usa XX:XX:XX:XX:XX:XX, permanent, random o stable)."
        return True, s.upper(), ""

    if field == "static_route":
        parts = s.split()
        if not parts:
            return False, "", "Stringa rotta vuota."
        try:
            net_obj = ipaddress.IPv4Network(parts[0], strict=False)
            norm = [str(net_obj)]
            if len(parts) >= 2:
                gw_obj = ipaddress.IPv4Address(parts[1])
                norm.append(str(gw_obj))
            if len(parts) >= 3:
                if not parts[2].isdigit() or int(parts[2]) < 0:
                    return False, "", f"Metrica rotta non valida: '{parts[2]}'."
                norm.append(parts[2])
            return True, " ".join(norm), ""
        except ValueError as e:
            return False, "", f"Sintassi rotta statica non valida ('{s}'): {e}"

    return True, s, ""


def _set_feedback(target_key: str, state: str, msg: str = "", duration_ms: int = 1600, refresh_cb=None) -> None:
    with _NET_LOCK:
        _APPLY_FEEDBACK[target_key] = {"state": state, "msg": msg, "ts": time.time()}
    if refresh_cb:
        GLib.idle_add(lambda: refresh_cb() or False)

    if duration_ms > 0 and state in ("ok", "err"):
        stamp = _APPLY_FEEDBACK[target_key]["ts"]

        def _clear():
            with _NET_LOCK:
                cur = _APPLY_FEEDBACK.get(target_key)
                if cur and cur.get("ts") == stamp:
                    if state == "ok":
                        _APPLY_FEEDBACK.pop(target_key, None)
                    else:
                        cur["state"] = "err_banner"
            if refresh_cb:
                refresh_cb()
            return False

        GLib.timeout_add(duration_ms, _clear)


def _get_feedback(target_key: str) -> dict:
    with _NET_LOCK:
        return dict(_APPLY_FEEDBACK.get(target_key, {}))


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
    if os.path.exists(FAST_NET_CACHE_FILE):
        try:
            with open(FAST_NET_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with _NET_LOCK:
                _NET_STATE["ip_map"] = data.get("ip_map", {})
                _NET_STATE["ip6_map"] = data.get("ip6_map", {})
                _NET_STATE["gw_map"] = data.get("gw_map", {})
                _NET_STATE["bt"] = data.get("bt", _NET_STATE["bt"])
                _NET_STATE["initialized"] = True
        except Exception:
            pass
    if os.path.exists(WAN_CACHE_FILE):
        try:
            with open(WAN_CACHE_FILE, "r", encoding="utf-8") as f:
                w = json.load(f)
                if w.get("ip"):
                    _NET_STATE["wan"] = _compute_wan_badge(w["ip"])
                    _NET_STATE["ts"]["wan"] = float(w.get("ts", 0.0))
        except Exception:
            pass
    if os.path.exists(WIFI_CACHE_FILE):
        try:
            with open(WIFI_CACHE_FILE, "r", encoding="utf-8") as f:
                nets = json.load(f)
                if isinstance(nets, list):
                    _NET_STATE["wifi"]["networks"] = nets
                    _NET_STATE["wifi"]["enabled"] = True
        except Exception:
            pass
    if os.path.exists(PING_CACHE_FILE):
        try:
            with open(PING_CACHE_FILE, "r", encoding="utf-8") as f:
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
        with open(FAST_NET_CACHE_FILE, "w", encoding="utf-8") as f:
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
        return False, "", "Servizio NetworkManager non attivo (systemctl start NetworkManager)."

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
        return False, "", (err or out or f"Impossibile creare il profilo NM per {ifname}")

    ok_v, out_v, _ = probe.run_cmd_full(
        ["nmcli", "-g", "connection.uuid,connection.interface-name", "connection", "show", prof_name],
        timeout=2.0,
    )
    if not ok_v or not out_v.strip():
        return False, "", "Creazione profilo non confermata da nmcli."

    lines = [l.strip() for l in out_v.splitlines() if l.strip()]
    created_uuid = lines[0] if lines else ""
    if not created_uuid:
        return False, "", "UUID profilo non restituito da NetworkManager."

    STATE.set("network", f"selected_prof_{ifname}", created_uuid)
    return True, created_uuid, f"Profilo '{prof_name}' ({created_uuid[:8]}) creato e verificato."


def clone_verified_nm_profile(ifname: str, src_uuid: str, new_name: str = None) -> tuple[bool, str, str]:
    if not _is_nm_running():
        return False, "", "Servizio NetworkManager non attivo."
    ok_n, out_n = probe.run_cmd(["nmcli", "-g", "connection.id", "connection", "show", src_uuid], timeout=1.5)
    base_name = out_n.strip() if (ok_n and out_n.strip()) else ifname
    target_name = (new_name or f"{base_name}-copy").strip()

    ok, out, err = probe.run_cmd_full(["nmcli", "connection", "clone", src_uuid, target_name], timeout=4.0)
    if not ok:
        return False, "", (err or out or f"Impossibile clonare il profilo '{base_name}'.")

    ok_v, out_v = probe.run_cmd(["nmcli", "-g", "connection.uuid", "connection", "show", target_name], timeout=1.5)
    cloned_uuid = out_v.splitlines()[0].strip() if (ok_v and out_v.strip()) else ""
    if cloned_uuid:
        STATE.set("network", f"selected_prof_{ifname}", cloned_uuid)
    return True, cloned_uuid, f"Profilo clonato come '{target_name}'."


def apply_and_verify_nm_profile(ifname: str, uuid: str, buf: dict, nm_cur: dict, iface_up: bool = True) -> tuple[bool, str]:
    if not _is_nm_running():
        return False, "Servizio NetworkManager non attivo."

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
            return False, (err_ip or "Metodo Manuale richiede un indirizzo IPv4/CIDR valido (es. 10.10.0.10/24).")
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
        return False, (err_m or out_m or "Errore nmcli durante la modifica del profilo.")

    ok_chk, out_chk, _ = probe.run_cmd_full(
        ["nmcli", "-g", "ipv4.method,ipv4.addresses,connection.autoconnect", "connection", "show", uuid],
        timeout=2.0,
    )
    if not ok_chk:
        return False, "Impossibile verificare il profilo dopo il salvataggio."

    chk_lines = [l.strip() for l in out_chk.splitlines()]
    saved_method = chk_lines[0] if len(chk_lines) > 0 else ""
    saved_ip = chk_lines[1].replace("\\,", ",") if len(chk_lines) > 1 else ""
    if saved_method != method:
        return False, f"Verifica NM fallita: metodo salvato '{saved_method}' != richiesto '{method}'."
    if method == "manual" and ip_cidr.split("/")[0] not in saved_ip:
        return False, f"Verifica NM fallita: IP '{ip_cidr}' non presente nel profilo salvato ('{saved_ip}')."

    is_active_prof = any(p.get("uuid") == uuid and p.get("active") for p in nm_cur.get("available_profiles", []))
    if iface_up and (is_active_prof or not is_wifi):
        probe.run_cmd_full(["nmcli", "device", "set", ifname, "managed", "yes"], timeout=2.0)
        ok_u, out_u, err_u = probe.run_cmd_full(["nmcli", "connection", "up", uuid], timeout=10.0)
        if not ok_u:
            return False, f"Profilo salvato, ma attivazione su {ifname} fallita: {err_u or out_u}"

        if method == "manual":
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
                return False, f"Profilo salvato, ma il kernel su {ifname} riporta ancora {kernel_ips or 'nessun IP'} anziché {ip_cidr}."

    return True, f"Profilo '{con_name}' applicato e verificato su {ifname}."


def _collect_wifi_state(force_rescan: bool = False) -> tuple[dict, set]:
    res = {"available": probe.has_bin("nmcli"), "enabled": False, "powersave": "default", "networks": [], "saved": []}
    saved_ssids = set()
    if not res["available"]:
        return res, saved_ssids

    ok_r, out_r = probe.run_cmd(["nmcli", "radio", "wifi"], timeout=1.0)
    res["enabled"] = (ok_r and "enabled" in out_r.lower())

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
                saved_list.append({
                    "uuid": parts[0],
                    "name": nm,
                    "autoconnect": parts[3],
                    "priority": parts[4],
                    "active": bool(parts[5] and parts[5] != "--"),
                })
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
            ssid = parts[1].replace("\\:", ":").strip() or "<Hidden>"
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
                "bssid": bssid,
                "chan": chan,
                "rate": rate,
                "signal": sig,
                "security": sec,
            })
        nets.sort(key=lambda x: (not x["in_use"], -x["signal"]))
        res["networks"] = nets
        try:
            with open(WIFI_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(nets, f)
        except Exception:
            pass
    return res, saved_ssids


def _collect_bluetooth_state() -> dict:
    bt = {
        "available": probe.has_bin("bluetoothctl"),
        "powered": False,
        "scanning": False,
        "paired": [],
        "discovered": [],
    }
    if not bt["available"]:
        return bt

    ok_s, out_s = probe.run_cmd(["bluetoothctl", "show"], timeout=1.0)
    if not ok_s:
        return bt
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


def _write_proxy_env(pr: dict = None) -> None:
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
        os.makedirs(os.path.dirname(PROXY_ENV_FILE), exist_ok=True)
        with open(PROXY_ENV_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        log.error(f"_write_proxy_env error: {e}")


def _compute_wan_badge(ip_str: str) -> dict:
    wan_cfg = CONFIG.get("network", {}).get("wan", {})
    primary_ip = wan_cfg.get("primary_ip", "").strip()
    primary_lbl = wan_cfg.get("primary_label", "Primary WAN")
    failover_lbl = wan_cfg.get("failover_label", "Failover WAN")
    badge_text = ""
    badge_style = "info"
    if primary_ip and ip_str not in ("Rilevamento...", "N/D", ""):
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
            os.makedirs(os.path.dirname(WAN_CACHE_FILE), exist_ok=True)
            with open(WAN_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"ip": ip_val, "ts": time.time()}, f)
            return _compute_wan_badge(ip_val)
    except Exception as e:
        log.debug(f"WAN IP fetch error: {e}")
    return _NET_STATE["wan"]


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
            targets.append({"name": f"Gateway ({iface['name']})", "host": iface["gw"]})
            break
    targets.append({"name": "Cloudflare DNS", "host": "1.1.1.1"})
    return targets


def _collect_pings(targets: list[dict]) -> dict:
    new_res = {}
    for t in targets:
        host = t["host"]
        ok, out = probe.run_cmd(["ping", "-c", "1", "-W", "1", host], timeout=1.2)
        if ok and out:
            m = re.search(r"time=([\d.]+)\s*ms", out)
            new_res[host] = {"ok": True, "ms": float(m.group(1)) if m else 0.5}
        else:
            new_res[host] = {"ok": False, "ms": None}
    try:
        with open(PING_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "results": new_res}, f)
    except Exception:
        pass
    return new_res


class Panel(BasePanel):
    id = "network"
    title = "Network & Comms"
    icon = "\uf6ff"
    supports_expand = True
    hover_highlight = True
    default_tab = "ifaces"

    def __init__(self, shell=None):
        super().__init__(shell)
        _load_fast_disk_cache()
        self._last_sample = read_proc_net_dev()
        self._last_time = time.time()
        self._speeds = {}

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

                GLib.idle_add(_ui_update)

        threading.Thread(target=_worker, daemon=True).start()

    def get_bar_pill(self) -> dict:
        show_speed = get_network_show_bar_speed()

        speeds = {}
        if show_speed:
            now = time.time()
            curr_dev = read_proc_net_dev()
            prev_dev, prev_ts = curr_dev, now - 1.0
            if os.path.exists(BAR_SAMPLE_FILE):
                try:
                    with open(BAR_SAMPLE_FILE, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                        prev_ts = float(s_data.get("ts", now - 1.0))
                        prev_dev = s_data.get("devs", curr_dev)
                except Exception:
                    pass
            try:
                with open(BAR_SAMPLE_FILE, "w", encoding="utf-8") as f:
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

        mon_iface = self.get_state("monitored_iface", "auto")
        unit_mode = self.get_state("speed_unit", CONFIG["network"].get("default_speed_unit", "bytes"))
        valid_names = [i["name"] for i in ifaces]

        if mon_iface == "ALL":
            rx_val = sum(s["rx"] for s in speeds.values())
            tx_val = sum(s["tx"] for s in speeds.values())
            disp_iface = "ALL"
        else:
            if mon_iface not in valid_names:
                up_ifs = [i["name"] for i in ifaces if i["up"] and i["ip"] != "N/D"]
                mon_iface = up_ifs[0] if up_ifs else (valid_names[0] if valid_names else "N/D")
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
        unit_mode = self.get_state("speed_unit", CONFIG["network"].get("default_speed_unit", "bytes"))
        mon_iface = self.get_state("monitored_iface", "auto")
        if mon_iface not in [i["name"] for i in ifaces] and mon_iface != "ALL":
            up_ifs = [i["name"] for i in ifaces if i["up"]]
            mon_iface = up_ifs[0] if up_ifs else (ifaces[0]["name"] if ifaces else "N/D")

        if not expanded:
            self._schedule_bg_refresh(mode_hint="compact")
            return self._build_compact_view(canvas, ifaces, mon_iface, unit_mode)
        return self._build_expanded_view(canvas, ifaces, mon_iface, unit_mode)

    def _build_compact_view(self, canvas: Canvas, ifaces: list[dict], mon_iface: str, unit_mode: str) -> list:
        is_it = (get_active_language() == "it")
        show_bar_spd = get_network_show_bar_speed()

        with _NET_LOCK:
            wan = copy.deepcopy(_NET_STATE["wan"])
            bt = copy.deepcopy(_NET_STATE["bt"])

        box = Box(
            "Network & Comms",
            icon="\uf6ff",
            actions=[
                ActionBtn(unit_mode.upper(), "net:toggle_unit", tone="neutral"),
                ActionBtn("\uf013", "ui:open_settings:network", tone="select"),
                ToggleExpandBtn(False),
            ],
        )

        wan_badge = f" {Badge(wan['badge'], style=wan['badge_style'])}" if wan["badge"] else ""
        wan_copy_uri = "ui:copy:" + wan["ip"]
        box.add_kv(
            "WAN IP",
            f"{Txt(wan['ip'], role='focus')}{wan_badge}",
            key_width=10,
            right_markup=ActionBtn("Copy", wan_copy_uri, tone="neutral"),
            icon="\uf0ac",
        )
        active_gws = [i["gw"] for i in ifaces if i["up"] and i["gw"]]
        if active_gws:
            box.add_kv("Gateway", Txt(active_gws[0], role="val"), key_width=10, icon="\uf0e8")

        up_ifaces = [i for i in ifaces if i["up"]]
        down_ifaces = [i for i in ifaces if not i["up"]]

        for iface in up_ifaces:
            ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else "\uf6ff")
            is_mon = (iface["name"] == mon_iface)
            mon_uri = "net:mon:" + iface["name"]
            dn_uri = "net:ifdown:" + iface["name"]
            ip_copy_uri = "ui:copy:" + iface["ip"].split("/")[0]
            sec_acts = []
            if show_bar_spd:
                sec_acts.append(SelectPill("Bar", mon_uri, selected=is_mon))
            sec_acts.append(ActionBtn("Down", dn_uri, tone="danger_soft"))
            box.add_section(f"{ic} {iface['name']} ({iface['link_speed']})", actions=sec_acts)
            box.add_kv(
                "IPv4",
                Txt(iface["ip"], role="focus"),
                key_width=10,
                right_markup=ActionBtn("Copy", ip_copy_uri, tone="neutral"),
                icon="\uf124",
            )
            rx_s = format_speed(iface["rx_rate"], unit_mode)
            tx_s = format_speed(iface["tx_rate"], unit_mode)
            box.add_row(f"  {Txt('\uf063', role='ok_soft')} {rx_s}   {Txt('\uf062', role='intent_soft')} {tx_s}")

        if down_ifaces:
            box.add_section("Interfacce Inattive" if is_it else "Inactive Interfaces")
            for iface in down_ifaces:
                ic = "\uf1eb" if iface["kind"] == "wifi" else ("\uf132" if iface["kind"] == "vpn" else "\uf6ff")
                iname = f"{iface['name']:<10}"
                left = f"  {ic} {Txt(iname, role='inactive')} {StateBadge(False, 'UP', 'DOWN')}"
                mon_uri = "net:mon:" + iface["name"]
                up_uri = "net:ifup:" + iface["name"]
                up_b = ActionBtn("Up", up_uri, tone="apply")
                right_m = f"{ActionBtn('Bar', mon_uri, tone='neutral')} {up_b}" if show_bar_spd else up_b
                box.add_row(left, right_markup=right_m)

        bt_actions = [
            ActionBtn("Scan", "bt:scan", tone="select", active=bt.get("scanning", False)) if bt["powered"] else "",
            ActionBtn("Off", "bt:power:off", tone="danger_soft") if bt["powered"] else ActionBtn("On", "bt:power:on", tone="apply"),
        ]
        box.add_section(f"\uf294 Bluetooth ({'ON' if bt['powered'] else 'OFF'})", actions=[a for a in bt_actions if a])
        if not bt["powered"]:
            box.add_row(f"  {Txt('Controller spento.' if is_it else 'Controller is off.', role='inactive')}")
        elif not bt["paired"]:
            box.add_row(f"  {Txt('Nessun dispositivo associato.' if is_it else 'No paired devices.', role='inactive')}")
        else:
            for dev in bt["paired"][:4]:
                bat_v = dev.get("battery")
                bat = f" {Txt('\uf240 ' + str(bat_v) + '%', role='ok_soft')}" if bat_v is not None else ""
                name_short = canvas.truncate_plain(dev["name"], 18)
                st_role = "vitality" if dev["connected"] else "schema"
                left = f"  {dev['icon']} {Txt(name_short, role=st_role)}{bat}"
                disc_uri = "bt:disc:" + dev["mac"]
                conn_uri = "bt:conn:" + dev["mac"]
                act = ActionBtn("Disc", disc_uri, tone="danger_soft") if dev["connected"] else ActionBtn("Conn", conn_uri, tone="apply")
                box.add_row(left, right_markup=act)

        return [box]

    def _build_expanded_view(self, canvas: Canvas, ifaces: list[dict], mon_iface: str, unit_mode: str, forced_tab: str = None, is_cc: bool = False) -> list:
        from panels.cc_net_wired import build_wired_tab_cards
        from panels.cc_net_wifi import build_wifi_tab_cards
        from panels.cc_net_vpn import build_vpn_tab_cards
        from panels.cc_net_wan import build_wan_tab_cards

        tab = forced_tab or self.active_tab
        is_it = (get_active_language() == "it")
        cards = []

        if not is_cc:
            hdr_box = Box(
                "NC-UI NETWORK & RADIO CONTROL CENTER",
                icon="\uf6ff",
                actions=[
                    ActionBtn(f"Unit: {unit_mode.upper()}", "net:toggle_unit", tone="neutral"),
                    ActionBtn("\uf021 Refresh", "net:force_refresh", tone="select"),
                    ActionBtn("\uf013 CC", "ui:open_settings:network", tone="neutral"),
                    ToggleExpandBtn(True),
                ],
                highlighted=True,
            )
            tabs = [
                ("ifaces",   "1. \uf6ff Interfacce & Profili" if is_it else "1. \uf6ff Interfaces & Profiles"),
                ("wifi",     "2. \uf1eb Wi-Fi & BT"),
                ("vpn",      "3. \uf132 VPN & Proxy"),
                ("watchdog", "4. \uf0ac WAN & Routing"),
            ]
            hdr_box.add_tabs(tab, tabs)
            cards.append(hdr_box)

        if tab in ("wifi", "wireless"):
            self._schedule_bg_refresh(mode_hint="wifi")
            cards.extend(build_wifi_tab_cards(self, canvas))
        elif tab in ("bt", "bluetooth"):
            self._schedule_bg_refresh(mode_hint="bt")
            cards.extend(self._build_bt_cards(canvas))
        elif tab == "vpn":
            self._schedule_bg_refresh(mode_hint="vpn")
            cards.extend(build_vpn_tab_cards(self, canvas))
        elif tab == "watchdog":
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
        is_it = (get_active_language() == "it")

        with _NET_LOCK:
            bt = copy.deepcopy(_NET_STATE["bt"])

        st_badge = StateBadge(bt["powered"], "ACCESO" if is_it else "ON", "SPENTO" if is_it else "OFF")
        box = Box(
            "Controller Bluetooth (BlueZ)",
            icon="\uf294",
            elected=False,
        )
        box.add_row(" ")

        if not bt["available"]:
            box.add_row(f"  {Txt('Servizio bluetoothctl non trovato sul sistema.' if is_it else 'bluetoothctl service not found on system.', role='inactive')}")
            box.add_row(" ")
            return [box]

        pwr_btn = (
            ActionBtn("Spegni Controller" if is_it else "Turn Off", "bt:power:off", tone="danger_soft")
            if bt["powered"]
            else ActionBtn("Accendi Controller" if is_it else "Turn On", "bt:power:on", tone="apply", active=True)
        )
        scan_lbl = ("\u25cf Scan Attivo" if is_it else "\u25cf Scanning") if bt["scanning"] else ("\uf021 Avvia Scan (15s)" if is_it else "\uf021 Start Scan (15s)")
        scan_btn = ActionBtn(scan_lbl, "bt:scan", tone="select", active=bt["scanning"]) if bt["powered"] else ""
        box.actions = [a for a in [st_badge, scan_btn, pwr_btn] if a]

        if not bt["powered"]:
            box.add_row(f"  {Txt('Accendi il controller Bluetooth per gestire i dispositivi associati.' if is_it else 'Turn on the Bluetooth controller to manage paired devices.', role='inactive')}")
            box.add_row(" ")
            return [box]

        if not bt["paired"]:
            box.add_row(f"  {Txt('Nessun dispositivo associato.' if is_it else 'No paired devices.', role='inactive')}")
        else:
            rows = []
            for d in bt["paired"]:
                is_c = bool(d["connected"])
                st = StateBadge(is_c, "Connesso" if is_it else "Connected", "Standby")
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
                    ActionBtn("Disc", disc_uri, tone="danger_soft")
                    if is_c
                    else ActionBtn("Conn", conn_uri, tone="apply")
                )
                rem_b = ActionBtn("x", rem_uri, tone="danger_soft")
                name_trunc = canvas.truncate_plain(d["name"], 21)
                if is_c:
                    dev_cell = Txt(f" {d['icon']} {name_trunc} ", role="vitality", bg=True)
                else:
                    dev_cell = Txt(f" {d['icon']} {name_trunc}", role="focus")

                rows.append([
                    dev_cell,
                    Txt(d["mac"], role="schema"),
                    st,
                    bat,
                    f"{conn_b} {rem_b}",
                ])
            box.add_table(
                headers=[
                    "DISPOSITIVO" if is_it else "DEVICE",
                    "MAC ADDRESS",
                    "STATO" if is_it else "STATE",
                    "BAT",
                    "AZIONI" if is_it else "ACTIONS",
                ],
                widths=[23, 18, 13, 8, "flex"],
                rows=_spaced_rows(rows),
                aligns=["left", "left", "left", "left", "right"],
            )

        box.add_row(" ")
        cards = [box]

        disc_list = bt.get("discovered", [])
        if disc_list or bt["scanning"]:
            opened, fold_btn = FoldToggle(self, "bt_disc", default=bool(bt["scanning"]), is_it=is_it)
            disc_box = Box(
                f"Dispositivi Rilevati nelle Vicinanze ({len(disc_list)})" if is_it else f"Nearby Discovered Devices ({len(disc_list)})",
                icon="\uf294",
                actions=[fold_btn],
            )
            if not opened:
                disc_box.add_row(
                    f"  {Txt('Clicca [▸ Espandi] per visualizzare i dispositivi non associati rilevati.' if is_it else 'Click [▸ Expand] to view nearby unpaired devices.', role='schema')}"
                )
            else:
                disc_box.add_row(" ")
                if not disc_list:
                    disc_box.add_row(f"  {Txt('Ricerca dispositivi in corso...' if is_it else 'Scanning for nearby devices...', role='inactive')}")
                else:
                    disc_rows = []
                    for d in disc_list[:8]:
                        pair_uri = "bt:pair:" + d["mac"]
                        disc_rows.append([
                            f"\uf294 {Txt(canvas.truncate_plain(d['name'], 26), role='val')}",
                            Txt(d["mac"], role="schema"),
                            ActionBtn("\uf0c1 Pair & Trust", pair_uri, tone="apply"),
                        ])
                    disc_box.add_table(
                        headers=[
                            "DISPOSITIVO" if is_it else "DEVICE",
                            "MAC ADDRESS",
                            "AZIONI" if is_it else "ACTIONS",
                        ],
                        widths=[30, 19, "flex"],
                        rows=_spaced_rows(disc_rows),
                        aligns=["left", "left", "right"],
                    )
                disc_box.add_row(" ")
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

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        val_raw = str(text or "").strip()
        refresh_cb = (lambda: self.shell.refresh_view() if self.shell else None)

        if action_id == "net_virtual_br" and val_raw:
            probe.run_async(
                lambda: probe.run_cmd_full(["nmcli", "connection", "add", "type", "bridge", "ifname", val_raw, "con-name", val_raw]),
                on_done=lambda _: self._trigger_full_resync("ifaces"),
            )
            return
        if action_id == "net_vlan_create" and val_raw:
            parts = [p.strip() for p in val_raw.split("|")]
            if len(parts) < 2 or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 4094):
                _set_feedback(
                    self.get_state("selected_iface", ""),
                    "err",
                    "Sintassi VLAN errata. Usa: interfaccia|ID (es. enp5s0|10, ID 1..4094).",
                    duration_ms=3500,
                    refresh_cb=refresh_cb,
                )
                return
            p_dev, v_id = parts[0], parts[1]
            v_name = f"{p_dev}.{v_id}"
            probe.run_async(
                lambda: probe.run_cmd_full(["nmcli", "connection", "add", "type", "vlan", "con-name", v_name, "ifname", v_name, "dev", p_dev, "id", v_id]),
                on_done=lambda _: self._trigger_full_resync("ifaces"),
            )
            return
        if action_id == "wifi_hidden" and val_raw:
            parts = [p.strip() for p in val_raw.split("|", 1)]
            ssid = parts[0]
            pwd = parts[1] if len(parts) > 1 else ""
            cmd = ["nmcli", "dev", "wifi", "connect", ssid, "hidden", "yes"] + (["password", pwd] if pwd else [])
            probe.run_async(lambda: probe.run_cmd_full(cmd, timeout=15.0), on_done=lambda _: self._trigger_full_resync("wifi"))
            return
        if action_id == "wifi_hotspot" and val_raw:
            parts = [p.strip() for p in val_raw.split("|", 1)]
            ssid = parts[0]
            pwd = parts[1] if len(parts) > 1 else "ncuihotspot123"
            cmd = ["nmcli", "dev", "wifi", "hotspot", "ssid", ssid, "password", pwd]
            probe.run_async(lambda: probe.run_cmd_full(cmd, timeout=10.0), on_done=lambda _: self._trigger_full_resync("wifi"))
            return
        if action_id.startswith("vpn_import|") and val_raw:
            v_type = action_id.split("|", 1)[1]
            fp = os.path.expanduser(val_raw)
            probe.run_async(
                lambda: probe.run_cmd_full(["nmcli", "connection", "import", "type", v_type, "file", fp], timeout=6.0),
                on_done=lambda _: self._trigger_full_resync("vpn"),
            )
            return
        if action_id.startswith("vpn_dns|"):
            u_id = action_id.split("|", 1)[1]
            ok_d, clean_d, _err_d = validate_network_field("dns", val_raw)
            if ok_d:
                probe.run_async(
                    lambda: probe.run_cmd_full(["nmcli", "connection", "modify", u_id, "ipv4.dns", clean_d.replace(",", " ")]),
                    on_done=lambda _: self._trigger_full_resync("vpn"),
                )
            return
        if action_id == "net_proxy_http" and val_raw:
            host, _, port_s = val_raw.partition(":")
            port = int(port_s) if (port_s.isdigit() and 1 <= int(port_s) <= 65535) else 8080
            STATE.set("network", "proxy_http_host", host.strip())
            STATE.set("network", "proxy_http_port", port)
            _write_proxy_env()
            self._trigger_full_resync("vpn")
            return
        if action_id == "net_proxy_socks" and val_raw:
            host, _, port_s = val_raw.partition(":")
            port = int(port_s) if (port_s.isdigit() and 1 <= int(port_s) <= 65535) else 1080
            STATE.set("network", "proxy_socks_host", host.strip())
            STATE.set("network", "proxy_socks_port", port)
            _write_proxy_env()
            self._trigger_full_resync("vpn")
            return
        if action_id == "net_proxy_pac":
            STATE.set("network", "proxy_pac_url", val_raw)
            _write_proxy_env()
            self._trigger_full_resync("vpn")
            return
        if action_id == "net_add_ping" and val_raw:
            parts = [p.strip() for p in val_raw.split("|", 1)]
            host = parts[-1]
            name = parts[0] if len(parts) > 1 else host
            cur_list = list(get_watchdog_targets(get_interfaces_fast()))
            cur_list.append({"name": name, "host": host})
            STATE.set("network", "custom_watchdog", cur_list)
            self._trigger_full_resync("vpn")
            return

        if "|" not in action_id:
            return
        kind, target = action_id.split("|", 1)

        if kind == "net_rename_prof" and "|" in target and val_raw:
            iface, uuid = target.split("|", 1)

            def _do_ren():
                ok, out, err = probe.run_cmd_full(["nmcli", "connection", "modify", uuid, "connection.id", val_raw], timeout=3.0)
                nm_prof = _collect_nm_profile_details(iface)
                with _NET_LOCK:
                    _NET_STATE["nm_profiles"][iface] = nm_prof
                    _NET_STATE["ts"]["nm"] = time.time()
                return ok, (f"Profilo rinominato in '{val_raw}'." if ok else (err or out))

            def _done_ren(res):
                ok, msg = res
                _set_feedback(iface, "ok" if ok else "err", msg, duration_ms=1600 if ok else 3200, refresh_cb=refresh_cb)

            probe.run_async(_do_ren, on_done=_done_ren)
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
                _set_feedback(target, "err", err_v, duration_ms=3500, refresh_cb=refresh_cb)
                return
            self._set_edit_field(target, f_name, norm_v)
            return

        if kind == "net_add_route" and val_raw:
            ok_r, norm_r, err_r = validate_network_field("static_route", val_raw)
            if not ok_r:
                _set_feedback(target, "err", err_r, duration_ms=3500, refresh_cb=refresh_cb)
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
            try:
                self._set_edit_field(target, "autoconnect_priority", int(val_raw))
            except ValueError:
                pass
        elif kind == "net_search":
            self._set_edit_field(target, "dns_search", val_raw)
        elif kind == "net_metric":
            try:
                self._set_edit_field(target, "route_metric", int(val_raw))
            except ValueError:
                pass
        elif kind == "wifi_pass" and val_raw:
            def _do_wifi_conn():
                ok, out, err = probe.run_cmd_full(["nmcli", "dev", "wifi", "connect", target, "password", val_raw], timeout=15.0)
                if not ok:
                    log.error(f"Connessione Wi-Fi a '{target}' fallita: {err or out}")
                if os.path.exists(WIFI_CACHE_FILE):
                    os.remove(WIFI_CACHE_FILE)

            probe.run_async(_do_wifi_conn, on_done=lambda _: self._trigger_full_resync("wifi"))

    def _execute_verified_create(self, iface: str, prof_name: str = None) -> None:
        refresh_cb = (lambda: self.shell.refresh_view() if self.shell else None)

        def _task():
            ok, _uuid, msg = create_verified_nm_profile(iface, prof_name or iface)
            nm_prof = _collect_nm_profile_details(iface)
            with _NET_LOCK:
                _NET_STATE["nm_profiles"][iface] = nm_prof
                _NET_STATE["ts"]["nm"] = time.time()
            return ok, msg

        def _done(res):
            ok, msg = res
            _set_feedback(iface, "ok" if ok else "err", msg, duration_ms=1800 if ok else 3500, refresh_cb=refresh_cb)

        probe.run_async(_task, on_done=_done)

    def on_link(self, uri: str) -> None:
        log.debug(f"NetworkPanel.on_link: {uri}")
        refresh_cb = (lambda: self.shell.refresh_view() if self.shell else None)

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
        elif uri == "net:prompt_virtual":
            self.request_prompt("net_virtual_br", "Bridge interface name (e.g. br0):", "br0", title="Create NM Bridge", required=True)
        elif uri == "net:prompt_vlan":
            sel = self.get_state("selected_iface", "enp5s0")
            self.request_prompt("net_vlan_create", "Interfaccia Madre | VLAN ID (1..4094):", f"{sel}|10", title="Create 802.1Q VLAN", required=True)
        elif uri == "net:flush_dns":
            def _flush_d():
                if probe.has_bin("resolvectl"):
                    probe.run_cmd(["resolvectl", "flush-caches"], timeout=2.0)
                probe.run_cmd(["pkill", "-HUP", "dnsmasq"], timeout=1.0)

            probe.run_async(_flush_d, on_done=lambda _: self._trigger_full_resync("vpn"))
        elif uri == "net:flush_arp":
            probe.run_async(
                lambda: probe.run_cmd(["ip", "neigh", "flush", "nud", "stale"], timeout=2.0),
                on_done=lambda _: refresh_cb() if refresh_cb else None,
            )
        elif uri.startswith("net:del_ping:"):
            host_to_rm = uri.split(":", 2)[2]
            cur_list = [t for t in get_watchdog_targets(get_interfaces_fast()) if t.get("host") != host_to_rm]
            STATE.set("network", "custom_watchdog", cur_list)
            self._trigger_full_resync("vpn")
        elif uri.startswith("net:if_filter:"):
            flt = uri.split(":", 2)[2]
            self.set_state("iface_filter", flt)
            self.request_throttled_refresh()
        elif uri == "net:jump_wifi_tab":
            self.active_tab = "wifi"
            STATE.set("control_center", "subtab_network", "wireless")
            self._trigger_full_resync("wifi")
            self.request_throttled_refresh()
        elif uri.startswith("net:jump_iface:"):
            iface = uri.split(":", 2)[2]
            self.set_state("selected_iface", iface)
            self.set_state("iface_filter", "all")
            self.active_tab = "ifaces"
            STATE.set("control_center", "subtab_network", "wired")
            self._trigger_full_resync("ifaces")
            self.request_throttled_refresh()
        elif uri.startswith("net:jump_wifi_prof:"):
            _, _, iface, uuid = uri.split(":", 3)
            self.set_state("selected_iface", iface)
            self.set_state("iface_filter", "all")
            STATE.set("network", f"selected_prof_{iface}", uuid)
            self.active_tab = "ifaces"
            STATE.set("control_center", "subtab_network", "wired")
            self._trigger_full_resync("ifaces")
            self.request_throttled_refresh()
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
                    probe.run_cmd_full(["nmcli", "device", "connect" if up else "disconnect", iface], timeout=6.0)
                else:
                    probe.run_cmd_full(["ip", "link", "set", iface, "up" if up else "down"], timeout=3.0)

            probe.run_async(_toggle_if, on_done=lambda _: self._trigger_full_resync())
        elif uri.startswith("net:buf:"):
            _, _, iface, key, val = uri.split(":", 4)
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
            with _NET_LOCK:
                _APPLY_FEEDBACK.pop(iface, None)
            self.request_throttled_refresh()
        elif uri.startswith("net:nm_create:"):
            iface = uri.split(":", 2)[2]
            self._execute_verified_create(iface, iface)
        elif uri.startswith("net:nm_prompt_new:"):
            iface = uri.split(":", 2)[2]
            self.request_prompt(f"net_new_prof|{iface}", f"New profile name for {iface}:", f"{iface}-static", title=f"+ Profile ({iface})", required=True)
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

            def _do_clone():
                ok, _c_uuid, msg = clone_verified_nm_profile(iface, uuid)
                nm_prof = _collect_nm_profile_details(iface)
                with _NET_LOCK:
                    _NET_STATE["nm_profiles"][iface] = nm_prof
                    _NET_STATE["ts"]["nm"] = time.time()
                return ok, msg

            def _done_clone(res):
                ok, msg = res
                _set_feedback(iface, "ok" if ok else "err", msg, duration_ms=1800 if ok else 3500, refresh_cb=refresh_cb)

            probe.run_async(_do_clone, on_done=_done_clone)
        elif uri.startswith("net:nm_up_prof:") or uri.startswith("net:nm_down_prof:"):
            parts = uri.split(":", 3)
            is_up_act = (parts[1] == "nm_up_prof")
            iface, uuid = parts[2], parts[3]

            def _do_prof_updown():
                if is_up_act:
                    probe.run_cmd_full(["nmcli", "device", "set", iface, "managed", "yes"], timeout=2.0)
                    ok, out, err = probe.run_cmd_full(["nmcli", "connection", "up", uuid, "ifname", iface], timeout=10.0)
                    if not ok:
                        ok, out, err = probe.run_cmd_full(["nmcli", "connection", "up", uuid], timeout=10.0)
                else:
                    ok, out, err = probe.run_cmd_full(["nmcli", "connection", "down", uuid], timeout=6.0)
                nm_prof = _collect_nm_profile_details(iface)
                ip_map, ip6_map, gw_map, routes = _collect_ip_and_routes()
                with _NET_LOCK:
                    _NET_STATE["nm_profiles"][iface] = nm_prof
                    _NET_STATE["ip_map"] = ip_map
                    _NET_STATE["ip6_map"] = ip6_map
                    _NET_STATE["gw_map"] = gw_map
                    _NET_STATE["routes"] = routes
                    _NET_STATE["ts"]["nm"] = time.time()
                    _NET_STATE["ts"]["ip"] = time.time()
                return ok, (f"Profilo {'attivato' if is_up_act else 'disattivato'} su {iface}." if ok else (err or out))

            def _done_prof_updown(res):
                ok, msg = res
                _set_feedback(iface, "ok" if ok else "err", msg, duration_ms=1600 if ok else 3200, refresh_cb=refresh_cb)

            probe.run_async(_do_prof_updown, on_done=_done_prof_updown)
        elif uri.startswith("net:nm_auto_prof:"):
            _, _, iface, uuid, new_ac = uri.split(":", 4)

            def _do_prof_ac():
                probe.run_cmd_full(["nmcli", "connection", "modify", uuid, "connection.autoconnect", new_ac], timeout=3.0)
                nm_prof = _collect_nm_profile_details(iface)
                with _NET_LOCK:
                    _NET_STATE["nm_profiles"][iface] = nm_prof
                    _NET_STATE["ts"]["nm"] = time.time()

            probe.run_async(_do_prof_ac, on_done=lambda _: refresh_cb() if refresh_cb else None)
        elif uri.startswith("net:nm_rename_prof:"):
            _, _, iface, uuid = uri.split(":", 3)
            with _NET_LOCK:
                nm_c = copy.deepcopy(_NET_STATE["nm_profiles"].get(iface, {}))
            cur_n = next((p["name"] for p in nm_c.get("available_profiles", []) if p["uuid"] == uuid), nm_c.get("con_name", iface))
            self.request_prompt(f"net_rename_prof|{iface}|{uuid}", f"Nuovo nome per il profilo ({iface}):", cur_n, title="Rename NM Profile", required=True)
        elif uri.startswith("net:nm_del_prof:"):
            _, _, iface, uuid = uri.split(":", 3)

            def _del_p():
                probe.run_cmd_full(["nmcli", "connection", "delete", uuid], timeout=4.0)
                nm_prof = _collect_nm_profile_details(iface)
                with _NET_LOCK:
                    _NET_STATE["nm_profiles"][iface] = nm_prof
                    _NET_STATE["ts"]["nm"] = time.time()

            probe.run_async(_del_p, on_done=lambda _: refresh_cb() if refresh_cb else None)
        elif uri.startswith("net:nm_apply:"):
            _, _, iface, uuid = uri.split(":", 3)
            buf_all = self.get_state("edit_buffer", {})
            buf = copy.deepcopy(buf_all.get(iface, {}) if isinstance(buf_all, dict) else {})
            with _NET_LOCK:
                nm_cur = copy.deepcopy(_NET_STATE["nm_profiles"].get(iface, {}))
            ifaces_now = get_interfaces_fast()
            iface_up = any(i["name"] == iface and i["up"] for i in ifaces_now)

            def _do_apply_verified():
                ok, msg = apply_and_verify_nm_profile(iface, uuid, buf, nm_cur, iface_up=iface_up)
                nm_prof = _collect_nm_profile_details(iface)
                ip_map, ip6_map, gw_map, routes = _collect_ip_and_routes()
                with _NET_LOCK:
                    _NET_STATE["nm_profiles"][iface] = nm_prof
                    _NET_STATE["ip_map"] = ip_map
                    _NET_STATE["ip6_map"] = ip6_map
                    _NET_STATE["gw_map"] = gw_map
                    _NET_STATE["routes"] = routes
                    _NET_STATE["ts"]["nm"] = time.time()
                    _NET_STATE["ts"]["ip"] = time.time()
                return ok, msg

            def _on_apply_done(res):
                ok, msg = res
                if ok:
                    cur_all = self.get_state("edit_buffer", {})
                    if isinstance(cur_all, dict) and iface in cur_all:
                        del cur_all[iface]
                        self.set_state("edit_buffer", cur_all)
                    _set_feedback(iface, "ok", msg, duration_ms=1400, refresh_cb=refresh_cb)
                else:
                    _set_feedback(iface, "err", msg, duration_ms=2800, refresh_cb=refresh_cb)

            probe.run_async(_do_apply_verified, on_done=_on_apply_done)
        elif uri.startswith("net:proxy_mode:"):
            m_val = uri.split(":", 2)[2]
            STATE.set("network", "proxy_mode", m_val)
            _write_proxy_env()
            self._trigger_full_resync("vpn")
        elif uri == "net:prompt_proxy_http":
            with _NET_LOCK:
                pr = copy.deepcopy(_NET_STATE["proxy"])
            cur = f"{pr['http_host']}:{pr['http_port']}" if pr["http_host"] else ""
            self.request_prompt("net_proxy_http", "HTTP Proxy host:port (e.g. 127.0.0.1:8080):", cur, title="HTTP Proxy", required=True)
        elif uri == "net:prompt_proxy_socks":
            with _NET_LOCK:
                pr = copy.deepcopy(_NET_STATE["proxy"])
            cur = f"{pr['socks_host']}:{pr['socks_port']}" if pr["socks_host"] else ""
            self.request_prompt("net_proxy_socks", "SOCKS5 Proxy host:port (e.g. 127.0.0.1:1080):", cur, title="SOCKS5 Proxy", required=True)
        elif uri == "net:prompt_proxy_pac":
            with _NET_LOCK:
                pr = copy.deepcopy(_NET_STATE["proxy"])
            self.request_prompt("net_proxy_pac", "PAC Autoconfig URL:", pr["autoconfig_url"], title="PAC URL")
        elif uri == "net:prompt_add_ping":
            self.request_prompt("net_add_ping", "Nome | IP/Host (es. Firewall OT | 10.10.0.254):", "", title="Add Watchdog Target", required=True)
        elif uri in ("net:refresh_wan", "net:refresh_ping"):
            target_f = WAN_CACHE_FILE if uri == "net:refresh_wan" else PING_CACHE_FILE
            if os.path.exists(target_f):
                try:
                    os.remove(target_f)
                except Exception:
                    pass
            with _NET_LOCK:
                _NET_STATE["ts"]["wan" if uri == "net:refresh_wan" else "ping"] = 0.0
            self._schedule_bg_refresh(mode_hint="vpn", force=True)

        elif uri.startswith("wifi:"):
            parts = uri.split(":", 2)
            act = parts[1]
            rest = parts[2] if len(parts) > 2 else ""
            if act == "radio":
                with _NET_LOCK:
                    _NET_STATE["wifi"]["enabled"] = (rest == "on")
                self.request_throttled_refresh()
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "radio", "wifi", rest], timeout=3.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "powersave":
                wifi_devs = [i["name"] for i in get_interfaces_fast() if i["kind"] == "wifi"]
                target_wdev = wifi_devs[0] if wifi_devs else "wlan0"
                with _NET_LOCK:
                    _NET_STATE["wifi"]["powersave"] = "on" if rest == "3" else "off"
                probe.run_async(
                    lambda: probe.run_cmd(["iw", "dev", target_wdev, "set", "power_save", "on" if rest == "3" else "off"], timeout=2.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "band":
                u_id, b_val = rest.split(":", 1)
                band_arg = "" if b_val == "auto" else b_val
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "modify", u_id, "802-11-wireless.band", band_arg], timeout=3.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "mac_priv":
                u_id, m_val = rest.split(":", 1)
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "modify", u_id, "802-11-wireless.cloned-mac-address", m_val], timeout=3.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "rescan":
                if os.path.exists(WIFI_CACHE_FILE):
                    os.remove(WIFI_CACHE_FILE)

                def _do_rescan():
                    probe.run_cmd(["nmcli", "dev", "wifi", "rescan"], timeout=6.0)
                    wifi_st, saved_ssids = _collect_wifi_state(force_rescan=False)
                    with _NET_LOCK:
                        _NET_STATE["wifi"] = wifi_st
                        _NET_STATE["saved_wifi_ssids"] = saved_ssids
                        _NET_STATE["ts"]["wifi"] = time.time()

                probe.run_async(_do_rescan, on_done=lambda _: refresh_cb() if refresh_cb else None)
            elif act == "prompt_hidden":
                self.request_prompt("wifi_hidden", "SSID | Password (opzionale):", "", title="Connect Hidden Wi-Fi", required=True)
            elif act == "prompt_hotspot":
                self.request_prompt("wifi_hotspot", "SSID Hotspot | Password WPA2 (min 8 char):", "Nightcity-AP | ncui12345", title="Start Wi-Fi Hotspot", required=True)
            elif act == "toggle_auto":
                u_id, new_ac = rest.split(":", 1)
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "modify", u_id, "connection.autoconnect", new_ac], timeout=3.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "up_uuid":
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "up", rest], timeout=8.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "del_uuid":
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "delete", rest], timeout=4.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "disc":
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "down", rest], timeout=5.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "forget":
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "delete", rest], timeout=5.0),
                    on_done=lambda _: self._trigger_full_resync("wifi"),
                )
            elif act == "conn":
                ssid, _, sec = rest.partition(":")
                sec = sec or "OPEN"
                is_open = sec in ("", "OPEN", "--")
                with _NET_LOCK:
                    saved_profiles = set(_NET_STATE["saved_wifi_ssids"])

                if ssid not in saved_profiles and not is_open:
                    self.request_prompt(f"wifi_pass|{ssid}", f"Inserisci password per {ssid}...", secret=True)
                    return

                def _try_conn():
                    ok, _ = probe.run_cmd(["nmcli", "connection", "up", ssid], timeout=8.0)
                    if not ok and is_open:
                        ok, _ = probe.run_cmd(["nmcli", "dev", "wifi", "connect", ssid], timeout=10.0)
                    if os.path.exists(WIFI_CACHE_FILE):
                        os.remove(WIFI_CACHE_FILE)
                    return ok

                def _after_conn(ok):
                    if not ok and self.shell:
                        self.request_prompt(f"wifi_pass|{ssid}", f"Inserisci password per {ssid}...", secret=True)
                    else:
                        self._trigger_full_resync("wifi")

                probe.run_async(_try_conn, on_done=_after_conn)

        elif uri.startswith("bt:"):
            parts = uri.split(":", 2)
            act = parts[1]
            arg = parts[2] if len(parts) > 2 else ""
            if act == "power":
                with _NET_LOCK:
                    _NET_STATE["bt"]["powered"] = (arg == "on")
                self.request_throttled_refresh()
                probe.run_async(
                    lambda: probe.run_cmd(["bluetoothctl", "power", arg], timeout=3.0),
                    on_done=lambda _: self._trigger_full_resync("bt"),
                )
            elif act == "scan":
                probe.spawn_bg(["bash", "-c", "bluetoothctl --timeout 15 scan on"])
                with _NET_LOCK:
                    _NET_STATE["bt"]["scanning"] = True
                cur_map = self.get_state("open_sections", {})
                if not isinstance(cur_map, dict):
                    cur_map = {}
                cur_map["bt_disc"] = True
                self.set_state("open_sections", cur_map)
                self.request_throttled_refresh()
                GLib.timeout_add(1500, lambda: self._trigger_full_resync("bt") or False)
            elif act in ("conn", "disc", "remove", "pair") and arg:
                bt_cmd = {"conn": "connect", "disc": "disconnect", "remove": "remove", "pair": "pair"}[act]

                def _do_bt():
                    if act == "pair":
                        probe.run_cmd(["bluetoothctl", "pair", arg], timeout=10.0)
                        probe.run_cmd(["bluetoothctl", "trust", arg], timeout=3.0)
                        probe.run_cmd(["bluetoothctl", "connect", arg], timeout=6.0)
                    else:
                        probe.run_cmd(["bluetoothctl", bt_cmd, arg], timeout=8.0)

                probe.run_async(_do_bt, on_done=lambda _: self._trigger_full_resync("bt"))

        elif uri.startswith("vpn:"):
            parts = uri.split(":", 2)
            act = parts[1]
            rest = parts[2] if len(parts) > 2 else ""
            if act == "prompt_import":
                self.request_prompt(f"vpn_import|{rest}", f"Percorso file .{'conf' if rest == 'wireguard' else 'ovpn'}:", "~/", title=f"Import {rest.title()}", required=True)
            elif act == "toggle_auto":
                u_id, ac_val = rest.split(":", 1)
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "modify", u_id, "connection.autoconnect", ac_val], timeout=3.0),
                    on_done=lambda _: self._trigger_full_resync("vpn"),
                )
            elif act == "toggle_split":
                u_id, nd_val = rest.split(":", 1)
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "modify", u_id, "ipv4.never-default", nd_val], timeout=4.0),
                    on_done=lambda _: self._trigger_full_resync("vpn"),
                )
            elif act == "dns_prompt":
                self.request_prompt(f"vpn_dns|{rest}", "DNS dedicati per questo tunnel VPN (separati da virgola):", "", title="VPN Custom DNS")
            elif act == "del":
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "delete", rest], timeout=4.0),
                    on_done=lambda _: self._trigger_full_resync("vpn"),
                )
            elif act in ("up", "down"):
                probe.run_async(
                    lambda: probe.run_cmd(["nmcli", "connection", "up" if act == "up" else "down", rest], timeout=8.0),
                    on_done=lambda _: self._trigger_full_resync("vpn"),
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
    unit_mode = net_p.get_state("speed_unit", CONFIG["network"].get("default_speed_unit", "bytes"))
    mon_iface = net_p.get_state("monitored_iface", "auto")
    if mon_iface not in [i["name"] for i in ifaces] and mon_iface != "ALL":
        up_ifs = [i["name"] for i in ifaces if i["up"]]
        mon_iface = up_ifs[0] if up_ifs else (ifaces[0]["name"] if ifaces else "N/D")
    return net_p._build_expanded_view(canvas, ifaces, mon_iface, unit_mode, forced_tab=active_sub, is_cc=True)


def build_cc_bluetooth_cards(cc_panel, canvas: Canvas, active_sub: str) -> list:
    net_p = _get_cc_net_panel(cc_panel)
    net_p._schedule_bg_refresh(mode_hint="bt")
    return net_p._build_bt_cards(canvas)


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


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return Panel(shell)
