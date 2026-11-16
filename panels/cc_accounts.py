#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NC-UI Control Center — Accounts, Groups & Sessions (`panels/cc_accounts.py`) [Single Backend / Dual View]"""

import grp
import os
import pwd
import shlex
from core import probe
from core.i18n import get_active_language
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, SelectPill, StateBadge, Txt

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
    b = Box("ACCOUNTS // ERROR" if _ERR else "ACCOUNTS // STATUS", icon="\uf071" if _ERR else "\uf058")
    b.add_row(f"  {Txt(_ERR if _ERR else _MSG, role='warn' if _ERR else 'vitality')}")
    return [b]


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


def _build_profile_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    p = _probe_profile()
    cards = _feedback_cards()
    b = Box(
        "Profilo Utente & Shell" if is_it else "User Profile & Login Shell",
        icon="\uf007",
        actions=[ActionBtn("Change Password (Term)", "cc_acc:passwd", tone="warn")],
        elected=True,
    )
    b.add_row(" ")
    b.add_kv("Username / Full", Txt(f"{p['user']}  ({p['gecos']})", role="focus"))
    b.add_kv("UID / GID", Txt(f"{p['uid']} / {p['gid']}", role="vitality"))
    b.add_kv("Home Directory", Txt(p["home"], role="val"))
    b.add_kv("Login Shell", Txt(p["shell"], role="intent"))
    sh_pills = " ".join(SelectPill(os.path.basename(sh), f"cc_acc:chsh:{sh}", selected=(p["shell"] == sh)) for sh in p["shells"])
    b.add_kv("Switch Shell (chsh)", sh_pills, requires=["chsh"])
    b.add_row(" ")
    cards.append(b)
    return cards



def _build_groups_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    p = _probe_profile()
    grps = _probe_groups(p["user"])
    cards = _feedback_cards()
    b = Box(f"Gruppi Critici di Sistema ({p['user']})" if is_it else f"System Privilege Groups ({p['user']})", icon="\uf0c0", elected=True)
    b.add_row(" ")
    rows = []
    for g in grps:
        gn = g["name"]
        rows.append([
            Txt(gn, role="focus"),
            Txt(g["gid"], role="schema"),
            Txt(g["desc"], role="val"),
            StateBadge(g["member"], "MEMBER", "ABSENT" if not g["exists"] else "NONE"),
            ActionBtn("Remove", f"cc_acc:gdel:{gn}", tone="warn") if g["member"] else ActionBtn("Add User", f"cc_acc:gadd:{gn}", tone="apply", active=g["exists"]),
        ])
    b.add_table(headers=["GROUP", "GID", "SCOPE", "STATE", "ACTION"], widths=[12, 6, 26, 12, "flex"], rows=rows, aligns=["left", "left", "left", "left", "right"])
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cc_accounts_cards(cc_panel, canvas: Canvas, active_sub: str = "profile") -> list:
    is_it = (get_active_language() == "it")
    if active_sub == "groups":
        return _build_groups_standard(cc_panel, canvas, is_it)
    if active_sub == "sessions":
        return _build_sessions_standard(cc_panel, canvas, is_it)
    return _build_profile_standard(cc_panel, canvas, is_it)


def handle_cc_accounts_link(cc_panel, uri: str) -> bool:
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
        probe.open_terminal(["bash", "-c", "passwd; echo; read -p 'Press Enter to close...'"])
        _MSG = "Opened passwd prompt in terminal."
    elif cmd == "chsh" and arg:
        probe.open_terminal(["bash", "-c", f"chsh -s {shlex.quote(arg)}; echo; read -p 'Press Enter to close...'"])
        _MSG = f"Opened chsh -s {arg} in terminal."
    elif cmd == "gadd" and arg:
        probe.open_terminal(["bash", "-c", f"sudo usermod -aG {shlex.quote(arg)} {shlex.quote(user)} && id; echo; read -p 'Press Enter to close...'"])
        _MSG = f"Opened usermod -aG {arg} for {user} in terminal."
    elif cmd == "gdel" and arg:
        probe.open_terminal(["bash", "-c", f"sudo gpasswd -d {shlex.quote(user)} {shlex.quote(arg)} && id; echo; read -p 'Press Enter to close...'"])
        _MSG = f"Opened gpasswd -d {user} {arg} in terminal."
    elif cmd == "kill_sess" and arg:
        ok, _, err = probe.run_cmd_full(["loginctl", "terminate-session", arg], timeout=2.0)
        _MSG = f"Terminated session {arg}." if ok else ""
        _ERR = "" if ok else (err or f"Failed terminating session {arg}")
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
