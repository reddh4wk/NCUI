#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — Accounts, Groups & Sessions (`panels/cc_accounts.py`) [Single Backend / Dual View]"""

import grp
import os
import platform
import pwd
import shlex
import socket
from core import probe
from core.i18n import t
from ui.kit import feedback_cards, set_feedback

_CRITICAL_GROUPS = [
    ("wheel", "cc_accounts.grp_desc_wheel"),
    ("docker", "cc_accounts.grp_desc_docker"),
    ("libvirt", "cc_accounts.grp_desc_libvirt"),
    ("wireshark", "cc_accounts.grp_desc_wireshark"),
    ("video", "cc_accounts.grp_desc_video"),
    ("input", "cc_accounts.grp_desc_input"),
    ("uucp", "cc_accounts.grp_desc_uucp"),
]


def _feedback_cards() -> list:
    return feedback_cards("sysinfo", "cc_accounts.status_title")


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
    for gname, desc_key in _CRITICAL_GROUPS:
        eff_name = gname
        exists = False
        member = False
        gid_val = "-"
        try:
            g = grp.getgrnam(eff_name)
            exists = True
        except KeyError:
            g = None
            if gname == "wheel":
                try:
                    g = grp.getgrnam("sudo")
                    eff_name = "sudo"
                    exists = True
                except KeyError:
                    pass
        if g is not None:
            gid_val = str(g.gr_gid)
            member = (g.gr_gid in active_gids) or (user in g.gr_mem)
        rows.append({"name": eff_name, "desc": t(desc_key), "exists": exists, "member": member, "gid": gid_val})
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
    host = socket.gethostname().split(".", 1)[0].strip() or un.nodename
    try:
        os_rel = platform.freedesktop_os_release()
    except OSError:
        os_rel = {}
    os_info = probe.get_os_release()
    os_name = f"{os_info['pretty_name']} ({un.machine})"
    os_icon = _resolve_os_icon(os_rel)

    kernels: list[str] = []
    if probe.has_bin("pacman"):
        ok, out = probe.run_cmd(["pacman", "-Q", "linux-cachyos", "linux-cachyos-hardened"], timeout=1.0)
        if ok and out:
            kernels = [l.strip() for l in out.splitlines() if l.strip()]
    if not kernels and os.path.isdir("/lib/modules"):
        try:
            kernels = sorted(os.listdir("/lib/modules"))[:4]
        except OSError:
            pass
    if not kernels:
        kernels = [un.release]

    boot_ver = "-"
    if probe.has_bin("pacman"):
        ok_l, out_l = probe.run_cmd(["pacman", "-Q", "limine"], timeout=0.8)
        if ok_l and out_l:
            boot_ver = out_l.strip()
    if boot_ver == "-" and probe.has_bin("limine"):
        ok_l, out_l = probe.run_cmd(["limine", "--version"], timeout=0.8)
        boot_ver = out_l.splitlines()[0].strip() if (ok_l and out_l) else "limine"
    elif boot_ver == "-" and os.path.isdir("/sys/firmware/efi") and probe.has_bin("bootctl"):
        boot_ver = "systemd-boot"
    elif boot_ver == "-" and (probe.has_bin("grub-install") or probe.has_bin("grub2-install")):
        boot_ver = "GRUB"

    wm_ver = "-"
    if probe.has_bin("niri"):
        ok_n, out_n = probe.run_cmd(["niri", "--version"], timeout=0.8)
        if ok_n and out_n:
            wm_ver = out_n.strip()
    if wm_ver == "-":
        wm_ver = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("WAYLAND_DISPLAY") or "-"

    return {
        "host": host,
        "os_name": os_name,
        "os_icon": os_icon,
        "kernel_rel": un.release,
        "uptime": _read_uptime(),
        "kernels": kernels,
        "limine": boot_ver,
        "niri": wm_ver,
    }


def handle_cc_sysinfo_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_acc:"):
        return False
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
        msg = t("cc_accounts.session_terminated", p0=arg) if ok else (err or t("cc_accounts.session_term_failed", p0=arg))
        set_feedback("sysinfo", ok, msg)
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
