# -*- coding: utf-8 -*-
"""NC-UI Control Center — Auth, Security & Keys (`panels/cc_auth.py`) [Single Backend / Dual View]"""

import glob
import os
import shlex
from core import probe
from core.i18n import get_active_language
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, StateBadge, Txt

_MSG = ""
_ERR = ""
_USBGUARD_HELPER = "/usr/local/sbin/nc-usbguard-helper.sh"


def _feedback_cards() -> list:
    if not _MSG and not _ERR:
        return []
    b = Box("AUTH // ERROR" if _ERR else "AUTH // STATUS", icon="\uf071" if _ERR else "\uf058")
    b.add_row(f"  {Txt(_ERR if _ERR else _MSG, role='warn' if _ERR else 'vitality')}")
    return [b]


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


def _build_tpm_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    d = _probe_tpm_luks()
    cards = _feedback_cards()
    b = Box(
        "UEFI Secure Boot, UKI & TPM2 / LUKS2",
        icon="\uf084",
        actions=[StateBadge(d["tpm"], "TPM2 PRESENT", "NO TPM2"), StateBadge(d["sb_enabled"], "SECURE BOOT", "SB OFF")],
        elected=True,
    )
    b.add_row(" ")
    b.add_kv("Chip TPM 2.0", StateBadge(d["tpm"], "DETECTED (/dev/tpmrm0)", "NOT DETECTED"))
    b.add_kv("sbctl Verify UKI", ActionBtn("Verify UKI Signatures", "cc_auth:sbctl_verify", tone="apply"), requires=["sbctl"])
    b.add_kv("EFI UKI Images", Txt(f"{len(d['ukis'])} found" if d["ukis"] else "Needs elevated /efi read", role="val"))

    if d["sb_lines"]:
        def _fill_sb(bx: Box):
            for ln in d["sb_lines"]:
                bx.add_row(f"  {Txt(ln, role='schema')}")
        b.add_collapsible(cc_panel, "auth_sbctl", "sbctl status details", _fill_sb)

    luks_label = ", ".join(f"{dev} ({sz})" for dev, sz in d["luks_devs"]) or "None detected (or hidden)"
    b.add_kv(
        "LUKS2 Containers",
        Txt(luks_label, role="focus"),
        right_markup=f"{ActionBtn('Inspect Slots (Term)', 'cc_auth:luks_term', tone='select')} {ActionBtn('TPM2 Enroll (Term)', 'cc_auth:tpm_enroll', tone='warn')}",
        requires=["cryptsetup"],
    )
    b.add_row(" ")
    cards.append(b)
    return cards



def _build_keys_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    d = _probe_keys()
    cards = _feedback_cards()

    ssh_box = Box(
        f"SSH Public Keys & ssh-agent ({len(d['pubs'])} files)",
        icon="\uf084",
        actions=[ActionBtn("ssh-add (Term)", "cc_auth:ssh_add", tone="apply")],
        elected=True,
    )
    ssh_box.add_row(" ")
    ssh_box.add_kv("ssh-agent", Txt(f"{len(d['agent'])} loaded identities" if d["agent"] else "0 loaded / socket idle", role="val"))
    for pk in d["pubs"][:8]:
        fn = pk["name"]
        ssh_box.add_row(
            f"  {Badge(pk['type'][:11], style='info')} {Txt(fn, role='focus')}  {Txt(pk['comment'][:28], role='schema')}",
            right_markup=ActionBtn("Copy .pub", f"cc_auth:copy_pub:{fn}", tone="select"),
        )
    if not d["pubs"]:
        ssh_box.add_row(f"  {Txt('No ~/.ssh/*.pub files found.', role='inactive')}")
    if d["agent"]:
        def _fill_agent(bx: Box):
            for ln in d["agent"]:
                bx.add_row(f"  {Txt(ln, role='schema')}")
        ssh_box.add_collapsible(cc_panel, "auth_ssh_agent", f"Active ssh-agent keys ({len(d['agent'])})", _fill_agent)
    ssh_box.add_row(" ")
    cards.append(ssh_box)

    gpg_box = Box("GnuPG Secret Keyring", icon="\uf023")
    gpg_box.add_row(" ")
    if d["gpg"]:
        for ln in d["gpg"][:6]:
            gpg_box.add_row(f"  {Txt(ln, role='val')}", requires=["gpg"])
    else:
        gpg_box.add_row(f"  {Txt('No GPG secret keys in ~/.gnupg', role='schema')}", requires=["gpg"])
    gpg_box.add_row(" ")
    cards.append(gpg_box)
    return cards


def build_cc_auth_cards(cc_panel, canvas: Canvas, active_sub: str = "tpm") -> list:
    is_it = (get_active_language() == "it")
    if active_sub == "keys":
        return _build_keys_standard(cc_panel, canvas, is_it)
    if active_sub == "polkit":
        return _build_polkit_standard(cc_panel, canvas, is_it)
    return _build_tpm_standard(cc_panel, canvas, is_it)


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
        _MSG = "Opened sbctl verification in terminal."
    elif cmd == "luks_term":
        d = _probe_tpm_luks()
        dev = d["luks_devs"][0][0] if d["luks_devs"] else "/dev/nvme0n1p3"
        probe.open_terminal(["bash", "-c", f"sudo cryptsetup luksDump {shlex.quote(dev)}; echo; read -p 'Press Enter to close...'"])
        _MSG = f"Opened cryptsetup luksDump for {dev}."
    elif cmd == "tpm_enroll":
        d = _probe_tpm_luks()
        dev = d["luks_devs"][0][0] if d["luks_devs"] else "/dev/nvme0n1p3"
        probe.open_terminal(["bash", "-c", f"echo 'Inspect or wipe/enroll TPM2 slots:'; sudo systemd-cryptenroll {shlex.quote(dev)} --tpm2-device=list; echo; read -p 'Press Enter to close...'"])
        _MSG = "Opened systemd-cryptenroll inspector in terminal."
    elif cmd == "copy_pub" and arg:
        p = os.path.expanduser(f"~/.ssh/{os.path.basename(arg)}")
        if os.path.isfile(p):
            probe.copy_clipboard(open(p, "r", encoding="utf-8", errors="ignore").read().strip())
            _MSG = f"Copied {arg} to clipboard."
        else:
            _ERR = f"Not found: {arg}"
    elif cmd == "ssh_add":
        probe.open_terminal(["bash", "-c", "ssh-add; ssh-add -l; echo; read -p 'Press Enter to close...'"])
        _MSG = "Opened ssh-add terminal."
    elif cmd == "sudo_term":
        probe.open_terminal(["bash", "-c", "sudo -l; echo; sudo visudo -c; echo; read -p 'Press Enter to close...'"])
        _MSG = "Opened sudoers verification in terminal."
    elif cmd == "usbguard_term":
        cmd_s = f"sudo {shlex.quote(_USBGUARD_HELPER)}" if os.path.exists(_USBGUARD_HELPER) else "sudo usbguard list-devices"
        probe.open_terminal(["bash", "-c", f"{cmd_s}; echo; read -p 'Press Enter to close...'"])
        _MSG = "Opened USBGuard terminal."
    else:
        return False

    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
