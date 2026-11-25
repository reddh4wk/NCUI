# -*- coding: utf-8 -*-
"""NC-UI Control Center — Auth, Security & Keys (`panels/cc_auth.py`) [Single Backend / Dual View]"""

import glob
import os
import re
import shlex
import stat
import subprocess
from core import probe
from core.i18n import get_active_language, t
from core.privileges import (
    build_privilege_modal_card,
    get_privileged_action,
    has_polkit_agent,
    list_privileged_actions,
    request_privilege_modal,
)
from core.resilience import resolve_sbin_helper
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Badge,
    Box,
    FeedbackBox,
    StateBadge,
    Txt,
    cancel_inline_entry,
    start_inline_input,
)

_MSG = ""
_ERR = ""

_USB_VENDOR_FALLBACKS = {
    "1b1c": "Corsair USB Device",
    "8087": "Intel Bluetooth / Wireless",
    "1d6b": "Linux Foundation Root Hub",
    "0bda": "Realtek USB Controller",
    "0b05": "ASUS AURA / USB Controller",
    "05e3": "Genesys Logic USB Hub",
    "174c": "ASMedia USB Controller",
    "1a40": "Terminus USB Hub",
}



def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    return [FeedbackBox(t("cc_auth.status_title"), not bool(_ERR), _ERR if _ERR else _MSG)]


def _probe_tpm_luks() -> dict:
    tpm_present = os.path.exists("/sys/class/tpm/tpm0") or os.path.exists("/dev/tpmrm0")
    sb_setup = sb_enabled = False
    sb_lines = []
    if probe.has_bin("sbctl"):
        rc, out, _ = probe.run_cmd_full(["sbctl", "status"], timeout=1.5)
        if out:
            sb_lines = [ln.strip() for ln in out.splitlines() if ln.strip()][:8]
            low = out.lower()
            sb_enabled = "secure boot:\tenabled" in low or "secure boot: enabled" in low or "installed:\t✓" in low
            sb_setup = "setup mode:\tenabled" in low
    else:
        for p in glob.glob("/sys/firmware/efi/efivars/SecureBoot-*"):
            try:
                raw = open(p, "rb").read()
                sb_enabled = len(raw) >= 5 and raw[4] == 1
            except Exception:
                pass

    luks_devs = []
    ok, out, _ = probe.run_cmd_full(["lsblk", "-nrpo", "NAME,FSTYPE,SIZE"], timeout=1.5)
    if ok and out:
        for ln in out.splitlines():
            parts = ln.split()
            if len(parts) >= 2 and parts[1] == "crypto_LUKS":
                luks_devs.append((parts[0], parts[2] if len(parts) > 2 else ""))

    ukis = sorted(glob.glob("/boot/EFI/Linux/*.efi") + glob.glob("/efi/EFI/Linux/*.efi"))
    return {
        "tpm": tpm_present,
        "sb_enabled": sb_enabled,
        "sb_setup": sb_setup,
        "sb_lines": sb_lines,
        "luks_devs": luks_devs,
        "ukis": ukis,
    }


def _resolve_ssh_auth_sock() -> str:
    """Resolve active `SSH_AUTH_SOCK` from env or standard systemd/desktop runtime sockets."""
    env_sock = str(os.environ.get("SSH_AUTH_SOCK", "") or "").strip()
    if env_sock:
        try:
            if stat.S_ISSOCK(os.stat(env_sock).st_mode):
                return env_sock
        except OSError:
            pass
    rt = str(os.environ.get("XDG_RUNTIME_DIR", "") or "").strip() or f"/run/user/{os.getuid()}"
    for cand in (
        f"{rt}/ssh-agent.socket",
        f"{rt}/gcr/ssh",
        f"{rt}/keyring/ssh",
        f"{rt}/gnupg/S.gpg-agent.ssh",
    ):
        try:
            if stat.S_ISSOCK(os.stat(cand).st_mode):
                return cand
        except OSError:
            continue
    return ""


def _probe_keys() -> dict:
    ssh_pubs = []
    for p in sorted(glob.glob(os.path.expanduser("~/.ssh/*.pub"))):
        try:
            content = open(p, "r", encoding="utf-8", errors="ignore").read().strip()
            parts = content.split()
            ktype = parts[0] if parts else "key"
            comment = parts[2] if len(parts) > 2 else os.path.basename(p)
            ssh_pubs.append({"path": p, "name": os.path.basename(p), "type": ktype, "comment": comment, "raw": content})
        except Exception:
            pass

    agent_keys = []
    sock = _resolve_ssh_auth_sock()
    if sock and probe.has_bin("ssh-add"):
        ok, out, _ = probe.run_cmd_full(["env", f"SSH_AUTH_SOCK={sock}", "ssh-add", "-l"], timeout=1.5)
        if ok and out:
            agent_keys = [ln.strip() for ln in out.splitlines() if ln.strip()][:8]

    gpg_keys = []
    if probe.has_bin("gpg"):
        ok, out, _ = probe.run_cmd_full(["gpg", "--batch", "--no-tty", "--list-secret-keys", "--keyid-format=short"], timeout=1.5)
        if ok and out:
            for ln in out.splitlines():
                s = ln.strip()
                if s.startswith(("sec", "uid")):
                    gpg_keys.append(s)

    return {"pubs": ssh_pubs, "agent": agent_keys, "gpg": gpg_keys[:12]}


def _probe_polkit() -> dict:
    agents = []
    ok, out, _ = probe.run_cmd_full(["pgrep", "-af", "polkit|soteria"], timeout=1.5)
    if ok and out:
        agents = [ln.strip() for ln in out.splitlines() if "pgrep" not in ln][:5]

    _, grp_out, _ = probe.run_cmd_full(["id", "-Gn"], timeout=1.5)
    groups = grp_out.split() if grp_out else []
    in_wheel = "wheel" in groups
    fido_mod = os.path.exists("/usr/lib/security/pam_u2f.so")
    fido_keys = os.path.exists(os.path.expanduser("~/.config/Yubico/u2f_keys"))
    _, usb_out, _ = probe.run_cmd_full(["systemctl", "is-active", "usbguard"], timeout=1.5)
    usb_active = usb_out.strip() == "active"
    return {
        "agents": agents,
        "in_wheel": in_wheel,
        "groups": groups,
        "fido_mod": fido_mod,
        "fido_keys": fido_keys,
        "usb_active": usb_active,
    }


def _decode_usb_class(ifaces: str, vid_pid: str) -> str:
    tokens = [tok.strip().lower() for tok in str(ifaces or "").split() if tok.strip()]
    if any(tok.startswith("09:") for tok in tokens) or str(vid_pid).lower().startswith("1d6b:"):
        return "HUB"
    if any(tok.startswith("0e:") for tok in tokens):
        return "VIDEO"
    if any(tok.startswith("01:") for tok in tokens):
        return "AUDIO"
    if any(tok.startswith("08:") for tok in tokens):
        return "STORAGE"
    if any(tok.startswith("e0:") for tok in tokens):
        return "WIRELESS"
    if "03:01:01" in tokens:
        return "KEYBOARD"
    if "03:01:02" in tokens:
        return "MOUSE"
    if any(tok.startswith("03:") for tok in tokens):
        return "HID"
    if any(tok.startswith("ff:") for tok in tokens):
        return "VENDOR"
    return "DEVICE"


def _parse_usbguard_raw_fallback(out: str) -> list[dict]:
    devs: list[dict] = []
    for ln in out.splitlines():
        s = ln.strip()
        if not s:
            continue
        m_head = re.match(r"^(\d+):\s+(allow|block|reject)\s+", s)
        if not m_head:
            continue
        did, status = m_head.group(1), m_head.group(2)
        m_id = re.search(r"\bid\s+([^\s]+)", s)
        vid_pid = m_id.group(1) if m_id else ""
        m_port = re.search(r'\bvia-port\s+"([^"]*)"', s)
        port = m_port.group(1) if m_port else ""
        m_conn = re.search(r'\bwith-connect-type\s+"([^"]*)"', s)
        conn = m_conn.group(1) if m_conn else ""
        m_if_b = re.search(r"\bwith-interface\s+\{\s*([^}]+)\s*\}", s)
        m_if_s = re.search(r"\bwith-interface\s+([^\s]+)", s)
        ifaces = (m_if_b.group(1) if m_if_b else (m_if_s.group(1) if m_if_s else "")).strip()
        m_name = re.search(r'\bname\s+"((?:[^"\\]|\\.)*)"', s)
        raw_name = (m_name.group(1) if m_name else "").strip()
        vid = vid_pid.split(":", 1)[0].lower() if ":" in vid_pid else ""
        disp_name = raw_name if (raw_name and raw_name != "(unnamed)") else _USB_VENDOR_FALLBACKS.get(vid, "(unnamed)")
        cls = _decode_usb_class(ifaces, vid_pid)
        devs.append({
            "id": did,
            "status": status,
            "vid_pid": vid_pid,
            "port": port,
            "connect": conn,
            "ifaces": ifaces,
            "name": disp_name,
            "cls": cls,
            "is_hub": (cls == "HUB"),
            "is_root_hub": vid_pid.lower().startswith("1d6b:"),
        })
    return devs


def probe_usbguard_devices() -> dict:
    """Query `nc-usbguard list-tsv` via least-privilege sudoers and return parsed USB devices."""
    _, usb_out, _ = probe.run_cmd_full(["systemctl", "is-active", "usbguard"], timeout=1.0)
    active = usb_out.strip() == "active"
    priv = get_privileged_action("usbguard_inspect")
    priv_ok = bool(priv and priv.check_installed())
    helper = resolve_sbin_helper("nc-usbguard")

    devices: list[dict] = []
    err = ""
    if priv_ok and os.path.isfile(helper):
        ok, out, run_err = probe.run_cmd_full(["sudo", "-n", helper, "list-tsv"], timeout=2.5)
        if ok:
            for ln in out.splitlines():
                if not ln.strip():
                    continue
                cols = ln.split("\t")
                if len(cols) < 7:
                    continue
                did, status, vid_pid, port, conn, ifaces, raw_name = [c.strip() for c in cols[:7]]
                vid = vid_pid.split(":", 1)[0].lower() if ":" in vid_pid else ""
                disp_name = raw_name if (raw_name and raw_name != "(unnamed)") else _USB_VENDOR_FALLBACKS.get(vid, "(unnamed)")
                cls = _decode_usb_class(ifaces, vid_pid)
                devices.append({
                    "id": did,
                    "status": status,
                    "vid_pid": vid_pid,
                    "port": port,
                    "connect": conn,
                    "ifaces": ifaces,
                    "name": disp_name,
                    "cls": cls,
                    "is_hub": (cls == "HUB"),
                    "is_root_hub": vid_pid.lower().startswith("1d6b:"),
                })
        else:
            err = run_err or "usbguard query failed"
    elif probe.has_bin("usbguard") and priv_ok:
        ok, out, run_err = probe.run_cmd_full(["sudo", "-n", "/usr/bin/usbguard", "list-devices"], timeout=2.5)
        if ok:
            devices = _parse_usbguard_raw_fallback(out)
        else:
            err = run_err or "usbguard list-devices failed"

    return {
        "active": active,
        "installed": probe.has_bin("usbguard"),
        "priv_ok": priv_ok,
        "devices": devices,
        "error": err,
    }


def _apply_usbguard_rule(cc_panel, op: str, dev_id: str, persist_mode: str) -> tuple[bool, str, bool]:
    if op not in ("allow", "block") or not str(dev_id).isdigit():
        return False, "Invalid USBGuard request", False
    mode_tok = "temp" if persist_mode == "temp" else "perm"
    helper = resolve_sbin_helper("nc-usbguard")
    priv = get_privileged_action("usbguard_inspect")
    payload = f"{op}:{mode_tok}:{dev_id}\n"

    if priv and priv.check_installed() and os.path.isfile(helper):
        try:
            res = subprocess.run(
                ["sudo", "-n", helper, "apply-stdin"],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=4.0,
            )
            ok, out = (res.returncode == 0), (res.stdout or "").strip()
        except Exception as e:
            ok, out = False, str(e)
        low = out.lower()
        if not ok and any(tok in low for tok in ("password is required", "not allowed", "no tty present")):
            request_privilege_modal(cc_panel, "usbguard_inspect")
            return False, "", True
        return ok, out, False

    if has_polkit_agent() and probe.has_bin("pkexec") and os.path.isfile(helper):
        try:
            res = subprocess.run(
                ["pkexec", helper, "apply-stdin"],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=12.0,
            )
            return (res.returncode == 0), (res.stdout or "").strip(), False
        except Exception as e:
            return False, str(e), False

    request_privilege_modal(cc_panel, "usbguard_inspect")
    return False, "", True


def handle_cc_auth_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if not uri.startswith("cc_auth:"):
        return False
    _MSG = ""
    _ERR = ""
    cancel_inline_entry(refresh=False)
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if cmd == "sbctl_verify":
        from ui.embedded_tui import start_panel_terminal

        start_panel_terminal(
            cc_panel,
            t("common.term_title", p0="sbctl verify"),
            ["bash", "-c", "sudo sbctl status; echo; sudo sbctl verify"],
        )
        return True
    elif cmd == "luks_term":
        from ui.embedded_tui import start_panel_terminal

        d = _probe_tpm_luks()
        dev = d["luks_devs"][0][0] if d["luks_devs"] else "/dev/nvme0n1p3"
        start_panel_terminal(
            cc_panel,
            t("common.term_title", p0=f"luksDump {os.path.basename(dev)}"),
            ["sudo", "cryptsetup", "luksDump", dev],
        )
        return True
    elif cmd == "tpm_enroll":
        from ui.embedded_tui import start_panel_terminal

        d = _probe_tpm_luks()
        dev = d["luks_devs"][0][0] if d["luks_devs"] else "/dev/nvme0n1p3"
        start_panel_terminal(
            cc_panel,
            t("common.term_title", p0=f"TPM2 {os.path.basename(dev)}"),
            ["bash", "-c", f"echo 'Inspect or wipe/enroll TPM2 slots:'; sudo systemd-cryptenroll {shlex.quote(dev)} --tpm2-device=list"],
        )
        return True
    elif cmd == "copy_pub" and arg:
        p = os.path.expanduser(f"~/.ssh/{os.path.basename(arg)}")
        if os.path.isfile(p):
            probe.copy_clipboard(open(p, "r", encoding="utf-8", errors="ignore").read().strip())
            _MSG = t("cc_auth.copied_to_clipboard", p0=arg)
        else:
            _ERR = t("cc_auth.not_found", p0=arg)
    elif cmd == "ssh_add":
        from ui.embedded_tui import start_panel_terminal

        sock = _resolve_ssh_auth_sock()
        if not sock:
            probe.run_cmd(["systemctl", "--user", "start", "ssh-agent.socket"], timeout=2.0)
            sock = _resolve_ssh_auth_sock()

        if not sock:
            bash_cmd = (
                "echo 'No active SSH authentication agent socket found.'; "
                "echo 'Enable it with: systemctl --user enable --now ssh-agent.socket'; "
                "exit 1"
            )
        else:
            loaded_set: set[tuple[str, str]] = set()
            ok_l, out_l, _ = probe.run_cmd_full(["env", f"SSH_AUTH_SOCK={sock}", "ssh-add", "-L"], timeout=1.5)
            if ok_l and out_l:
                for ln in out_l.splitlines():
                    flds = ln.strip().split()
                    if len(flds) >= 2:
                        loaded_set.add((flds[0], flds[1]))

            priv_keys: list[str] = []
            unloaded_privs: list[str] = []
            for pub_p in sorted(glob.glob(os.path.expanduser("~/.ssh/*.pub"))):
                priv_p = pub_p[:-4]
                if not os.path.isfile(priv_p):
                    continue
                priv_keys.append(priv_p)
                try:
                    pub_flds = open(pub_p, "r", encoding="utf-8", errors="ignore").read().strip().split()
                except Exception:
                    pub_flds = []
                if len(pub_flds) < 2 or (pub_flds[0], pub_flds[1]) not in loaded_set:
                    unloaded_privs.append(priv_p)

            env_prefix = f"export SSH_AUTH_SOCK={shlex.quote(sock)}; "
            if unloaded_privs:
                bash_cmd = f"{env_prefix}ssh-add {shlex.join(unloaded_privs)} && echo && ssh-add -l"
            elif priv_keys:
                bash_cmd = f"{env_prefix}echo 'All ~/.ssh keys are already loaded in ssh-agent:'; echo; ssh-add -l"
            else:
                bash_cmd = f"{env_prefix}ssh-add; echo && ssh-add -l"

        start_panel_terminal(
            cc_panel,
            t("common.term_title", p0="ssh-add"),
            ["bash", "-c", bash_cmd],
        )
        return True
    elif cmd == "sudo_term":
        from ui.embedded_tui import start_panel_terminal

        start_panel_terminal(
            cc_panel,
            t("common.term_title", p0="sudo -l / visudo"),
            ["bash", "-c", "sudo -l; echo; sudo visudo -c"],
        )
        return True
    elif cmd == "usbguard_term":
        from ui.embedded_tui import start_panel_terminal

        helper = resolve_sbin_helper("nc-usbguard")
        term_cmd = ["sudo", helper, "list"] if os.path.exists(helper) else ["sudo", "usbguard", "list-devices"]
        start_panel_terminal(cc_panel, t("common.term_title", p0="usbguard"), term_cmd)
        return True
    elif cmd == "usb_refresh":
        pass
    elif cmd == "usb_auth":
        request_privilege_modal(cc_panel, "usbguard_inspect")
    elif cmd == "usb_scope":
        STATE.set("auth", "usb_show_hubs", arg == "all")
    elif cmd == "usb_persist":
        STATE.set("auth", "usb_persist", "temp" if arg == "temp" else "perm")
    elif cmd == "usb_status_cycle":
        cur = str(STATE.get("auth", "usb_status_filter", "all") or "all")
        nxt = {"all": "allow", "allow": "block"}.get(cur, "all")
        STATE.set("auth", "usb_status_filter", nxt)
    elif cmd == "usb_q_open":
        cur_q = str(STATE.get("auth", "usb_query", "") or "")
        start_inline_input(
            "cc_auth_usb_q",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=36,
            on_change=lambda s: STATE.set("auth", "usb_query", s.strip()),
            on_commit=lambda s: STATE.set("auth", "usb_query", s.strip()),
        )
        return True
    elif cmd == "usb_q_clear":
        STATE.set("auth", "usb_query", "")
    elif cmd == "usb_act" and ":" in arg:
        op, did = arg.split(":", 1)
        persist = str(STATE.get("auth", "usb_persist", "perm") or "perm")
        ok, msg, opened_modal = _apply_usbguard_rule(cc_panel, op, did, persist)
        if not opened_modal:
            if ok:
                scope_lbl = t("cc_auth.usb_persist_temp") if persist == "temp" else t("cc_auth.usb_persist_perm")
                _MSG = t("cc_auth.fb_usb_ok", p0=op.upper(), p1=did, p2=scope_lbl)
            else:
                _ERR = msg or t("cc_auth.fb_usb_err", p0=did)
    elif cmd == "priv_inspect" and arg:
        STATE.set("privileges", "inspect_id", arg)
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
