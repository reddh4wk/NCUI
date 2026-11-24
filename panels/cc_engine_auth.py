# -*- coding: utf-8 -*-
"""NC-UI Control Center — Auth, Security & Keys (`panels/cc_auth.py`) [Single Backend / Dual View]"""

import glob
import os
import shlex
from core import probe
from core.i18n import get_active_language, t
from core.privileges import (
    build_privilege_modal_card,
    get_privileged_action,
    list_privileged_actions,
)
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, FeedbackBox, StateBadge, Txt

_MSG = ""
_ERR = ""
_USBGUARD_HELPER = "/usr/local/sbin/nc-usbguard-helper.sh"



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
    if os.environ.get("SSH_AUTH_SOCK") and probe.has_bin("ssh-add"):
        ok, out, _ = probe.run_cmd_full(["ssh-add", "-l"], timeout=1.5)
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


def handle_cc_auth_link(cc_panel, uri: str) -> bool:
    global _MSG, _ERR
    if not uri.startswith("cc_auth:"):
        return False
    _MSG = ""
    _ERR = ""
    parts = uri.split(":", 2)
    cmd = parts[1]
    arg = parts[2] if len(parts) > 2 else ""

    if cmd == "sbctl_verify":
        probe.open_terminal(["bash", "-c", "sudo sbctl status; echo; sudo sbctl verify; echo; read -p 'Press Enter to close...'"])
        _MSG = t("cc_auth.opened_sbctl_verification_in_termi")
    elif cmd == "luks_term":
        d = _probe_tpm_luks()
        dev = d["luks_devs"][0][0] if d["luks_devs"] else "/dev/nvme0n1p3"
        probe.open_terminal(["bash", "-c", f"sudo cryptsetup luksDump {shlex.quote(dev)}; echo; read -p 'Press Enter to close...'"])
        _MSG = t("cc_auth.opened_cryptsetup_luksdump_for", p0=dev)
    elif cmd == "tpm_enroll":
        d = _probe_tpm_luks()
        dev = d["luks_devs"][0][0] if d["luks_devs"] else "/dev/nvme0n1p3"
        probe.open_terminal(["bash", "-c", f"echo 'Inspect or wipe/enroll TPM2 slots:'; sudo systemd-cryptenroll {shlex.quote(dev)} --tpm2-device=list; echo; read -p 'Press Enter to close...'"])
        _MSG = t("cc_auth.opened_systemd_cryptenroll_inspect")
    elif cmd == "copy_pub" and arg:
        p = os.path.expanduser(f"~/.ssh/{os.path.basename(arg)}")
        if os.path.isfile(p):
            probe.copy_clipboard(open(p, "r", encoding="utf-8", errors="ignore").read().strip())
            _MSG = t("cc_auth.copied_to_clipboard", p0=arg)
        else:
            _ERR = t("cc_auth.not_found", p0=arg)
    elif cmd == "ssh_add":
        probe.open_terminal(["bash", "-c", "ssh-add; ssh-add -l; echo; read -p 'Press Enter to close...'"])
        _MSG = t("cc_auth.opened_ssh_add_terminal")
    elif cmd == "sudo_term":
        probe.open_terminal(["bash", "-c", "sudo -l; echo; sudo visudo -c; echo; read -p 'Press Enter to close...'"])
        _MSG = t("cc_auth.opened_sudoers_verification_in_ter")
    elif cmd == "usbguard_term":
        cmd_s = f"sudo {shlex.quote(_USBGUARD_HELPER)} list" if os.path.exists(_USBGUARD_HELPER) else "sudo usbguard list-devices"
        probe.open_terminal(["bash", "-c", f"{cmd_s}; echo; read -p 'Press Enter to close...'"])
        _MSG = t("cc_auth.opened_usbguard_terminal")
    elif cmd == "priv_inspect" and arg:
        STATE.set("privileges", "inspect_id", arg)
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
