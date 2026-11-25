#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — Accounts, Groups & Sessions (`panels/cc_accounts.py`) [Single Backend / Dual View]"""

import grp
import os
import platform
import pwd
import shlex
from core import probe
from core.i18n import get_active_language, t
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, FeedbackBox, SelectPill, StateBadge, Txt

_MSG = ""
_ERR = ""
_CRITICAL_GROUPS = [
    ("wheel", "sudo / admin privilege"),
    ("docker", "Docker daemon socket"),
    ("libvirt", "KVM / QEMU hypervisor"),
    ("wireshark", "Packet capture"),
    ("video", "GPU / DRM display"),
    ("input", "Raw libinput / evdev"),
    ("uucp", "Serial / USB TTL"),
]

def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    return [FeedbackBox(t("cc_accounts.status_title"), not bool(_ERR), _ERR if _ERR else _MSG)]


def _probe_profile() -> dict:
    uid = os.getuid()
    gid = os.getgid()
    try:
        pw = pwd.getpwuid(uid)
        user, home, shell, gecos = pw.pw_name, pw.pw_dir, pw.pw_shell, pw.pw_gecos or pw.pw_name
    except Exception:
        user = os.environ.get("USER", "user")
        home = os.path.expanduser("~")
        shell = os.environ.get("SHELL", "/bin/bash")
        gecos = user

    shells = [shell] if shell else []
    seen_names = {os.path.basename(shell)} if shell else set()
    if os.path.exists("/etc/shells"):
        try:
            for ln in open("/etc/shells", "r", encoding="utf-8", errors="ignore"):
                s = ln.strip()
                bn = os.path.basename(s)
                if s and not s.startswith("#") and bn not in seen_names and bn not in ("rbash", "git-shell", "systemd-home-fallback-shell"):
                    shells.append(s)
                    seen_names.add(bn)
        except Exception:
            pass
    if len(shells) <= 1:
        for s in ("/bin/bash", "/usr/bin/zsh", "/usr/bin/fish"):
            if os.path.basename(s) not in seen_names:
                shells.append(s)
                seen_names.add(os.path.basename(s))

    return {"user": user, "uid": uid, "gid": gid, "home": home, "shell": shell, "gecos": gecos, "shells": shells[:6]}


def _probe_groups(user: str) -> list[dict]:
    active_gids = set(os.getgroups())
    rows = []
    for gname, desc in _CRITICAL_GROUPS:
        exists = False
        member = False
        gid_val = "-"
        try:
            g = grp.getgrnam(gname)
            exists = True
            gid_val = str(g.gr_gid)
            member = (g.gr_gid in active_gids) or (user in g.gr_mem)
        except KeyError:
            pass
        rows.append({"name": gname, "desc": desc, "exists": exists, "member": member, "gid": gid_val})
    return rows


def _probe_sessions() -> list[dict]:
    sessions = []
    ok, out, _ = probe.run_cmd_full(["loginctl", "list-sessions", "--no-legend"], timeout=1.5)
    if ok and out:
        for ln in out.splitlines():
            parts = ln.split()
            if len(parts) >= 3:
                sid = parts[0]
                uid = parts[1]
                usr = parts[2]
                seat = parts[3] if len(parts) > 3 else "-"
                tty = parts[4] if len(parts) > 4 else "-"
                sessions.append({"id": sid, "uid": uid, "user": usr, "seat": seat, "tty": tty})
    return sessions


def _read_uptime() -> str:
    try:
        sec = int(float(open("/proc/uptime").read().split()[0]))
        d, r = divmod(sec, 86400)
        h, r = divmod(r, 3600)
        m, _ = divmod(r, 60)
        return f"{d}d {h:02d}h {m:02d}m" if d else f"{h:02d}h {m:02d}m"
    except Exception:
        return "N/A"


_OS_NERD_ICONS = {
    "arch": "\uf303",
    "archlinux": "\uf303",
    "cachyos": "\uf385",
    "endeavouros": "\uf322",
    "manjaro": "\uf312",
    "garuda": "\uf337",
    "artix": "\uf31f",
    "arcolinux": "\uf346",
    "archcraft": "\uf345",
    "debian": "\uf306",
    "ubuntu": "\uf31b",
    "linuxmint": "\uf30e",
    "pop": "\uf32a",
    "kali": "\uf327",
    "parrot": "\uf329",
    "elementary": "\uf309",
    "zorin": "\uf32f",
    "fedora": "\uf30a",
    "nobara": "\uf380",
    "rhel": "\uf316",
    "redhat": "\uf316",
    "centos": "\uf304",
    "almalinux": "\uf31d",
    "rocky": "\uf32b",
    "opensuse": "\uf314",
    "opensuse-tumbleweed": "\uf37d",
    "opensuse-leap": "\uf37e",
    "gentoo": "\uf30d",
    "nixos": "\uf313",
    "void": "\uf32e",
    "alpine": "\uf300",
    "slackware": "\uf318",
    "solus": "\uf32d",
    "qubes": "\uf342",
    "tails": "\uf343",
    "freebsd": "\uf30c",
    "openbsd": "\uf328",
}


def _resolve_os_icon(os_rel: dict) -> str:
    if not probe.has_nerd_font():
        return ""
    ids = [os_rel.get("ID", "").lower(), *os_rel.get("ID_LIKE", "").lower().split()]
    for key in ids:
        if key in _OS_NERD_ICONS:
            return _OS_NERD_ICONS[key]
    return "\uf31a"


def _collect_about_data() -> dict:
    un = os.uname()
    try:
        os_rel = platform.freedesktop_os_release()
    except OSError:
        os_rel = {}
    os_pretty = os_rel.get("PRETTY_NAME") or os_rel.get("NAME") or "Arch Linux"
    os_name = f"{os_pretty} ({un.machine})"
    os_icon = _resolve_os_icon(os_rel)
    kernels = []
    ok, out = probe.run_cmd(["pacman", "-Q", "linux-cachyos", "linux-cachyos-hardened"], timeout=1.0)
    if ok and out:
        kernels = [l.strip() for l in out.splitlines() if l.strip()]
    limine_ver = "N/A"
    ok_l, out_l = probe.run_cmd(["pacman", "-Q", "limine"], timeout=0.8)
    if ok_l and out_l:
        limine_ver = out_l.strip()
    niri_ver = "N/A"
    if probe.has_bin("niri"):
        ok_n, out_n = probe.run_cmd(["niri", "--version"], timeout=0.8)
        if ok_n and out_n:
            niri_ver = out_n.strip()
    return {
        "host": un.nodename,
        "os_name": os_name,
        "os_icon": os_icon,
        "kernel_rel": un.release,
        "uptime": _read_uptime(),
        "kernels": kernels,
        "limine": limine_ver,
        "niri": niri_ver,
    }


def handle_cc_sysinfo_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if not uri.startswith("cc_acc:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""
    user = _probe_profile()["user"]

    if cmd == "passwd":
        from ui.embedded_tui import start_panel_terminal

        start_panel_terminal(cc_panel, t("common.term_title", p0="passwd"), ["passwd"])
        return True
    elif cmd == "chsh" and arg:
        from ui.embedded_tui import start_panel_terminal

        start_panel_terminal(cc_panel, t("common.term_title", p0=f"chsh -s {arg}"), ["chsh", "-s", arg])
        return True
    elif cmd == "gadd" and arg:
        from ui.embedded_tui import start_panel_terminal

        start_panel_terminal(
            cc_panel,
            t("common.term_title", p0=f"usermod + {arg}"),
            ["bash", "-c", f"sudo usermod -aG {shlex.quote(arg)} {shlex.quote(user)} && id"],
        )
        return True
    elif cmd == "gdel" and arg:
        from ui.embedded_tui import start_panel_terminal

        start_panel_terminal(
            cc_panel,
            t("common.term_title", p0=f"gpasswd - {arg}"),
            ["bash", "-c", f"sudo gpasswd -d {shlex.quote(user)} {shlex.quote(arg)} && id"],
        )
        return True
    elif cmd == "kill_sess" and arg:
        ok, _, err = probe.run_cmd_full(["loginctl", "terminate-session", arg], timeout=2.0)
        _MSG = f"Terminated session {arg}." if ok else ""
        _ERR = "" if ok else (err or f"Failed terminating session {arg}")
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
