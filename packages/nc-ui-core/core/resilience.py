#!/usr/bin/env python3
"""NC-UI Resilience Config, Hardware Auto-Discovery & Root Helper Sync (`core/resilience.py`)."""

import getpass
import hashlib
import os
import platform
import re
import shlex
import socket
import subprocess
import threading
import tomllib
from core.paths import CORE_ROOT, find_libexec_helper, find_package_file


def expand_user_token(path: str) -> str:
    """Expand `~/`, `$USER`, and `{user}` placeholders deterministically for the active user."""
    s = str(path or "").strip()
    if not s:
        return ""
    user = getpass.getuser() or os.environ.get("USER", "root")
    s = s.replace("$USER", user).replace("{user}", user)
    if s == "~" or s.startswith("~/"):
        s = os.path.expanduser(s)
    return s


def parse_vault_share(spec: str, base_mp: str) -> tuple[str, str]:
    """Parse `<remote>` or `<remote>:</local/mountpoint>` into `(remote_spec, target_mountpoint)`."""
    raw = str(spec or "").strip()
    if ":/" in raw:
        rem, loc = raw.split(":/", 1)
        tgt = expand_user_token("/" + loc).rstrip("/") or "/"
        return rem.strip(), tgt
    name = raw.rstrip("/").rsplit("/", 1)[-1]
    base = expand_user_token(base_mp).rstrip("/") or "/nas"
    return raw, os.path.join(base, name) if name else base

RESILIENCE_CONF_PATH = "/etc/ncui/resilience.toml"
RESILIENCE_EXAMPLE_PATH = find_package_file("config", "ncui", "resilience.example.toml") or os.path.join(
    CORE_ROOT, "config", "ncui", "resilience.example.toml"
)
LIBEXEC_SRC_DIR = os.path.join(CORE_ROOT, "libexec", "ncui")
SBIN_DST_DIR = "/usr/lib/ncui/sbin"
BUNDLED_HELPERS = (
    "nc-fan-profile",
    "nc-usbguard",
    "nc-posture",
    "nc-snap",
    "nc-vault",
    "nc-hanoi",
    "nc-rescue",
    "nc-chroot",
    "nc-golden",
    "nc-disks",
)

DEFAULT_POSTURE_CFG: dict = {
    "ntsync": True,
    "proc_hidepid": True,
    "ptrace_relax": True,
    "vm_tuning": True,
    "suspend_audit": True,
    "nvidia_persistence": True,
    "cpu_governor": "performance",
}


def _read_env_kv(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                s = ln.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _detect_storage_layout() -> dict:
    """Inspect `/proc/self/mounts` (world-readable) for root fstype and LVM VG/LV layout."""
    layout = {
        "root_fstype": "",
        "vg_name": "",
        "root_lv": "",
        "has_var_lv": False,
    }
    try:
        with open("/proc/self/mounts", "r", encoding="utf-8", errors="ignore") as f:
            entries = [ln.split() for ln in f if ln.strip()]
    except Exception:
        return layout

    def _parse_lv(src: str) -> tuple[str, str]:
        if src.startswith("/dev/mapper/") and "-" in src[len("/dev/mapper/"):]:
            raw = src[len("/dev/mapper/"):]
            m = re.match(r"^((?:[^-]|--)+)-((?:[^-]|--)+)$", raw)
            if m:
                return m.group(1).replace("--", "-"), m.group(2).replace("--", "-")
        if src.startswith("/dev/") and src.count("/") == 3:
            segs = src.split("/")
            if segs[2] not in ("mapper", "disk", "by-uuid", "by-label", "by-partuuid"):
                return segs[2], segs[3]
        return "", ""

    for parts in entries:
        if len(parts) < 3:
            continue
        src, mp, fstype = parts[0], parts[1], parts[2]
        if mp == "/":
            layout["root_fstype"] = fstype.lower()
            vg, lv = _parse_lv(src)
            if vg and lv and not lv.startswith("snap_"):
                layout["vg_name"] = vg
                layout["root_lv"] = lv
            elif lv.startswith("snap_"):
                layout["vg_name"] = vg
        elif mp == "/var":
            vg, lv = _parse_lv(src)
            if vg and (lv == "var" or lv.endswith("_var")):
                layout["has_var_lv"] = True
    return layout


def _expand_uki_vars(val: str, esp: str) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    uki_dir = f"{esp}/EFI/Linux"
    return s.replace("${UKI_DIR}", uki_dir).replace("${ESP_MOUNT}", esp)


def _detect_vault_mounts(default_mp: str = "/nas", sys_folder: str = "") -> dict:
    """Inspect `/proc/self/mountinfo` (zero fork, zero network) for active SMB/NFS/SSHFS vault mounts."""
    det = {
        "protocol": "smb",
        "host": "",
        "mountpoint": (default_mp or "/nas").rstrip("/") or "/nas",
        "ssh_user": "",
        "remote_path": "",
        "shares": [],
        "backup_root_subdir": "",
        "pkglist_subdir": "",
    }
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln for ln in f if ln.strip()]
    except Exception:
        lines = []

    mp_prefix = det["mountpoint"]
    smb_shares: list[str] = []
    nfs_shares: list[str] = []
    for ln in lines:
        if " - " not in ln:
            continue
        left, right = ln.split(" - ", 1)
        lparts = left.split()
        rparts = right.split()
        if len(lparts) < 5 or len(rparts) < 2:
            continue
        mp = lparts[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
        if not (mp == mp_prefix or mp.startswith(mp_prefix + "/") or mp == "/run/nas" or mp.startswith("/run/nas/")):
            continue
        fstype = rparts[0].lower()
        src = rparts[1]
        opts = rparts[2] if len(rparts) > 2 else ""
        if fstype == "cifs" and src.startswith("//"):
            rest = src[2:].split("/", 1)
            if len(rest) == 2:
                det["protocol"] = "smb"
                det["host"] = rest[0]
                if rest[1] not in smb_shares:
                    smb_shares.append(rest[1])
                if not det["ssh_user"] and "username=" in opts:
                    for opt in opts.split(","):
                        if opt.startswith("username="):
                            det["ssh_user"] = opt.split("=", 1)[1]
                            break
        elif fstype in ("nfs", "nfs4") and ":" in src:
            h, exp = src.split(":", 1)
            det["protocol"] = "nfs"
            det["host"] = h
            sh = exp.strip("/").split("/")[-1] or mp.rsplit("/", 1)[-1]
            if sh and sh not in nfs_shares:
                nfs_shares.append(sh)
        elif fstype == "fuse.sshfs" and ":" in src:
            det["protocol"] = "ssh"
            hp, rpath = src.split(":", 1)
            if "@" in hp:
                det["ssh_user"], det["host"] = hp.split("@", 1)
            else:
                det["host"] = hp
            det["remote_path"] = rpath

    if smb_shares:
        det["shares"] = sorted(smb_shares)
    elif nfs_shares:
        det["shares"] = sorted(nfs_shares)

    if "personal_folder" in det["shares"] or os.path.isdir(os.path.join(mp_prefix, "personal_folder")):
        det["backup_root_subdir"] = "personal_folder/Software/Linux"
        host_pkg = os.path.join(mp_prefix, "personal_folder", "Software", "Linux", sys_folder, "pkglists") if sys_folder else ""
        legacy_pkg = os.path.join(mp_prefix, "personal_folder", "Software", "Linux", "arch", "pkglists")
        if sys_folder and (os.path.isdir(host_pkg) or not os.path.isdir(legacy_pkg)):
            det["pkglist_subdir"] = f"personal_folder/Software/Linux/{sys_folder}/pkglists"
        else:
            det["pkglist_subdir"] = "personal_folder/Software/Linux/arch/pkglists"
    elif det["shares"] or det["host"]:
        det["backup_root_subdir"] = "ncui-vault"
        det["pkglist_subdir"] = "ncui-vault/pkglists"
    return det


def _seed_defaults() -> dict:
    host = socket.gethostname().split(".", 1)[0].strip() or "linux"
    hw_uuid = ""
    try:
        if os.path.isfile("/sys/class/dmi/id/product_uuid"):
            with open("/sys/class/dmi/id/product_uuid", "r", encoding="utf-8", errors="ignore") as f:
                hw_uuid = f.read().strip().lower()
    except Exception:
        pass

    try:
        os_id = str(platform.freedesktop_os_release().get("ID", "linux") or "linux").strip().lower()
    except OSError:
        os_id = "linux"

    nc_prof = _read_env_kv("/etc/nc.profile")
    snap_conf = _read_env_kv("/etc/ncui/snap.conf")
    st_layout = _detect_storage_layout()
    esp = snap_conf.get("ESP_MOUNT", "").rstrip("/") or (
        "/efi" if os.path.isdir("/efi/EFI/Linux")
        else ("/boot/efi" if os.path.isdir("/boot/efi/EFI") else "/boot")
    )
    if not esp:
        esp = "/boot"

    vg_name = snap_conf.get("VG_NAME", "") or st_layout["vg_name"] or "vg0"
    root_lv = st_layout["root_lv"] or "root"
    has_var = st_layout["has_var_lv"] or os.path.exists(f"/dev/{vg_name}/var")
    backend = "btrfs" if (st_layout["root_fstype"] == "btrfs" and not st_layout["vg_name"]) else "lvm_uki"

    auto_uki_base = os.path.basename(_expand_uki_vars(snap_conf.get("AUTO_UKI", ""), esp))
    if auto_uki_base.endswith("-auto.efi"):
        uki_prefix = auto_uki_base[:-9]
    else:
        uki_prefix = host.lower() or "linux"

    primary_uki = _expand_uki_vars(snap_conf.get("PRIMARY_UKI", ""), esp)
    hook_path = "/etc/pacman.d/hooks/00-nc-snapshot.hook"
    hook_default = os.path.isfile(hook_path) if os.path.isdir("/etc/pacman.d/hooks") else True
    sys_folder = nc_prof.get("NC_SYS_FOLDER", f"{host}-{os_id}")
    v_det = _detect_vault_mounts(nc_prof.get("NC_NAS_MOUNTPOINT", "/nas"), sys_folder=sys_folder)

    dr_env = _read_env_kv("/var/lib/ncui/dr-status.env")
    uki_names = {u.strip() for u in dr_env.get("UKI_NAMES", "").split(",") if u.strip()}
    host_rescue = f"{host.lower()}-rescue.efi"
    if host_rescue in uki_names or os.path.isfile(f"{esp}/EFI/Linux/{host_rescue}"):
        rec_uki = host_rescue
    else:
        rec_uki = next((u for u in sorted(uki_names) if u.endswith("-rescue.efi")), "nc-rescue.efi")

    b_root_sub = str(v_det["backup_root_subdir"] or "").strip("/")
    b_root_abs = os.path.join(v_det["mountpoint"], b_root_sub) if b_root_sub else v_det["mountpoint"]
    golden_subdir = f"{sys_folder}/images" if os.path.isdir(os.path.join(b_root_abs, sys_folder, "images")) else "images"

    return {
        "identity": {
            "sys_alias": nc_prof.get("NC_SYS_ALIAS", host[:8].lower()),
            "hostname": nc_prof.get("NC_SYS_HOSTNAME", host),
            "hw_uuid": (nc_prof.get("NC_SYS_UUID", "") or hw_uuid).lower(),
            "sys_folder": sys_folder,
        },
        "posture": dict(DEFAULT_POSTURE_CFG),
        "snapshots": {
            "backend": backend,
            "vg_name": vg_name,
            "root_lv": root_lv,
            "var_lv": "var" if has_var else "",
            "home_lv": "",
            "esp_mount": esp,
            "uki_prefix": uki_prefix,
            "primary_uki": primary_uki,
            "snap_root_size": snap_conf.get("SNAP_ROOT_SIZE", "15G"),
            "snap_var_size": snap_conf.get("SNAP_VAR_SIZE", "5G"),
            "snap_home_size": snap_conf.get("SNAP_HOME_SIZE", "15G"),
            "btrfs_subvol": "@",
            "pacman_hook_auto": hook_default,
        },
        "vault": {
            "protocol": v_det["protocol"],
            "host": v_det["host"],
            "mountpoint": v_det["mountpoint"],
            "mount_visibility": "symlink",
            "user_symlink_dir": "~/NAS",
            "ssh_user": v_det["ssh_user"],
            "remote_path": v_det["remote_path"],
            "smb_creds_file": "/etc/samba/nas-cred",
            "smb_version": "auto",
            "port": "",
            "mount_opts": "",
            "shares": list(v_det["shares"]),
            "backup_root_subdir": v_det["backup_root_subdir"],
            "sync_paths": [],
            "pkglist_subdir": v_det["pkglist_subdir"],
        },
        "recovery": {
            "uki_name": rec_uki,
            "sign_sbctl": True,
            "golden_subdir": golden_subdir,
        },
    }


def load_resilience_config() -> dict:
    """Load `/etc/ncui/resilience.toml` merged cleanly over hardware-seeded defaults."""
    seeds = _seed_defaults()
    cfg = {sec: dict(tbl) for sec, tbl in seeds.items()}
    if os.path.isfile(RESILIENCE_CONF_PATH):
        try:
            with open(RESILIENCE_CONF_PATH, "rb") as f:
                raw = tomllib.load(f)
            if isinstance(raw, dict):
                for sec, tbl in raw.items():
                    if isinstance(tbl, dict) and sec in cfg and isinstance(cfg[sec], dict):
                        for k, v in tbl.items():
                            if k in cfg[sec]:
                                cfg[sec][k] = v
        except Exception:
            pass
    gov = str(cfg["posture"].get("cpu_governor") or "performance").strip().lower()
    if gov not in ("performance", "schedutil", "powersave"):
        gov = "performance"
    cfg["posture"]["cpu_governor"] = gov
    for bk in ("ntsync", "proc_hidepid", "ptrace_relax", "vm_tuning", "suspend_audit", "nvidia_persistence"):
        cfg["posture"][bk] = bool(cfg["posture"].get(bk, True))

    sn = cfg["snapshots"]
    be = str(sn.get("backend") or "lvm_uki").strip().lower()
    if be not in ("lvm_uki", "btrfs"):
        be = "lvm_uki"
    sn["backend"] = be
    esp = str(sn.get("esp_mount") or "/boot").strip().rstrip("/") or "/boot"
    sn["esp_mount"] = esp
    sn["primary_uki"] = _expand_uki_vars(sn.get("primary_uki", ""), esp)
    if not str(sn.get("uki_prefix") or "").strip():
        sn["uki_prefix"] = str(cfg["identity"].get("hostname") or "linux").strip().lower()
    if not str(sn.get("btrfs_subvol") or "").strip():
        sn["btrfs_subvol"] = "@"
    sn["pacman_hook_auto"] = bool(sn.get("pacman_hook_auto", True))

    vl = cfg["vault"]
    proto = str(vl.get("protocol") or "smb").strip().lower()
    if proto not in ("smb", "nfs", "ssh", "local"):
        proto = "smb"
    vl["protocol"] = proto
    vl["mountpoint"] = expand_user_token(str(vl.get("mountpoint") or "/nas")).rstrip("/") or "/nas"
    vis = str(vl.get("mount_visibility") or "symlink").strip().lower()
    if vis not in ("symlink", "filemanager", "both", "none"):
        vis = "symlink"
    vl["mount_visibility"] = vis
    vl["user_symlink_dir"] = str(vl.get("user_symlink_dir") if vl.get("user_symlink_dir") is not None else "~/NAS").strip()
    smb_ver = str(vl.get("smb_version") or "auto").strip().lower()
    if smb_ver not in ("auto", "3.1.1", "3.0", "2.1", "2.0", "1.0"):
        smb_ver = "auto"
    vl["smb_version"] = smb_ver
    port_s = str(vl.get("port") or "").strip()
    vl["port"] = port_s if (port_s.isdigit() and 1 <= int(port_s) <= 65535) else ""
    vl["mount_opts"] = str(vl.get("mount_opts") or "").strip()
    for fill_k in ("host", "shares", "backup_root_subdir", "pkglist_subdir"):
        if not vl.get(fill_k) and seeds["vault"].get(fill_k):
            vl[fill_k] = seeds["vault"][fill_k]
    if not isinstance(vl.get("shares"), list):
        vl["shares"] = []
    if not vl.get("backup_root_subdir"):
        vl["backup_root_subdir"] = "ncui-vault"
    if not vl.get("pkglist_subdir"):
        vl["pkglist_subdir"] = f"{str(vl['backup_root_subdir']).rstrip('/')}/pkglists"

    rc = cfg["recovery"]
    rc["sign_sbctl"] = bool(rc.get("sign_sbctl", True))
    if not str(rc.get("golden_subdir") or "").strip():
        rc["golden_subdir"] = seeds["recovery"]["golden_subdir"]
    uki_n = str(rc.get("uki_name") or "").strip()
    if not uki_n or (
        uki_n == "nc-rescue.efi"
        and seeds["recovery"]["uki_name"] != "nc-rescue.efi"
        and not os.path.isfile("/var/lib/ncui/rescue-status.env")
    ):
        rc["uki_name"] = seeds["recovery"]["uki_name"]
    return cfg


def _toml_val(val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, list):
        items = ", ".join(f'"{str(x).replace(chr(34), chr(92) + chr(34))}"' for x in val)
        return f"[{items}]"
    s = str(val or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def render_resilience_toml(cfg: dict) -> str:
    """Serialize resilience config dictionary deterministically as TOML."""
    lines = [
        "# NC-UI Resilience, Posture & Disaster Recovery Configuration",
        "",
    ]
    for sec in ("identity", "posture", "snapshots", "vault", "recovery"):
        tbl = cfg.get(sec)
        if not isinstance(tbl, dict):
            continue
        lines.append(f"[{sec}]")
        for k, v in tbl.items():
            lines.append(f"{k} = {_toml_val(v)}")
        lines.append("")
    return "\n".join(lines)


def file_sha256(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _is_root_exec(path: str) -> bool:
    try:
        st = os.stat(path)
        return os.path.isfile(path) and st.st_uid == 0 and bool(st.st_mode & 0o111)
    except Exception:
        return False


def probe_bundled_helpers() -> list[dict]:
    """Return install & SHA256 sync status for every first-party helper in `libexec/ncui`."""
    out: list[dict] = []
    for name in BUNDLED_HELPERS:
        src = find_libexec_helper(name) or os.path.join(LIBEXEC_SRC_DIR, name)
        dst = os.path.join(SBIN_DST_DIR, name)
        src_sha = file_sha256(src)
        dst_sha = file_sha256(dst)
        root_owned = _is_root_exec(dst)
        synced = bool(src_sha and dst_sha and src_sha == dst_sha and root_owned)
        out.append({
            "name": name,
            "src": src,
            "dst": dst,
            "src_sha": src_sha,
            "dst_sha": dst_sha,
            "src_short": src_sha[:10] if src_sha else "-",
            "dst_short": dst_sha[:10] if dst_sha else "-",
            "root_owned": root_owned,
            "synced": synced,
        })
    return out


def resolve_sbin_helper(name: str, legacy_fallback: str = "") -> str:
    """Resolve installed helper `/usr/lib/ncui/sbin/<name>`, delegating fallback to `core.paths.find_libexec_helper`."""
    sbin = os.path.join(SBIN_DST_DIR, name)
    return sbin if os.path.isfile(sbin) else (find_libexec_helper(name) or sbin)


read_env_kv = _read_env_kv


def exec_priv_helper(
    cc_panel,
    helper_name: str,
    action_id: str,
    args: list[str],
    stdin_data: str = "",
    timeout: float = 15.0,
) -> tuple[bool, int, str]:
    """Run bundled `/usr/lib/ncui/sbin/<helper_name>` via sudo -n or pkexec; return `(opened_modal, rc, output)`."""
    from core import probe
    from core.privileges import get_privileged_action, has_polkit_agent, request_privilege_modal

    helper = resolve_sbin_helper(helper_name)
    priv = get_privileged_action(action_id)
    if priv and priv.check_installed() and os.path.isfile(helper):
        res = subprocess.run(
            ["sudo", "-n", helper] + args,
            input=(stdin_data if stdin_data else None),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        out = (res.stdout or "").strip()
        if res.returncode != 0 and any(tok in out.lower() for tok in ("password is required", "not allowed", "no tty present")):
            if cc_panel is not None:
                request_privilege_modal(cc_panel, action_id)
            return True, res.returncode, out
        return False, res.returncode, out
    if has_polkit_agent() and probe.has_bin("pkexec") and os.path.isfile(helper):
        res = subprocess.run(
            ["pkexec", helper] + args,
            input=(stdin_data if stdin_data else None),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=max(timeout, 25.0),
        )
        return False, res.returncode, (res.stdout or "").strip()
    if cc_panel is not None:
        request_privilege_modal(cc_panel, action_id)
    return True, 1, ""


def run_helper_async(
    cc_panel,
    state_key: str,
    busy_tag: str,
    helper_name: str,
    action_id: str,
    args: list[str],
    stdin_data: str = "",
    timeout: float = 90.0,
    on_done=None,
) -> bool:
    """Generic background runner for bundled root helpers with GLib.idle_add refresh."""
    from core import probe
    from core.privileges import get_privileged_action, has_polkit_agent, request_privilege_modal
    from core.state import STATE

    if STATE.get("system", state_key, ""):
        return True
    helper = resolve_sbin_helper(helper_name)
    priv = get_privileged_action(action_id)
    if not (priv and priv.check_installed() and os.path.isfile(helper)):
        if not (has_polkit_agent() and probe.has_bin("pkexec") and os.path.isfile(helper)):
            request_privilege_modal(cc_panel, action_id)
            return False

    STATE.set("system", state_key, busy_tag)

    def _worker():
        opened_modal = False
        rc = 1
        out = ""
        try:
            opened_modal, rc, out = exec_priv_helper(
                cc_panel, helper_name, action_id, args, stdin_data=stdin_data, timeout=timeout
            )
        except Exception as e:
            out = str(e)

        def _finish():
            STATE.set("system", state_key, "")
            if callable(on_done):
                on_done(opened_modal, rc, out)
            if cc_panel and cc_panel.shell:
                cc_panel.shell.refresh_view()
            return False

        try:
            from gi.repository import GLib
            GLib.idle_add(_finish)
        except Exception:
            _finish()

    threading.Thread(target=_worker, daemon=True).start()
    return True


def build_install_helpers_root_script(names: list[str] | None = None) -> str:
    """Build root shell snippet installing bundled helpers (`0755 root:root`) and seeding TOML config."""
    targets = list(names) if names else list(BUNDLED_HELPERS)
    cmds = [
        f"/usr/bin/install -d -o root -g root -m 0755 {shlex.quote(SBIN_DST_DIR)} /etc/ncui",
    ]
    for name in targets:
        if name not in BUNDLED_HELPERS:
            continue
        src = find_libexec_helper(name)
        dst = os.path.join(SBIN_DST_DIR, name)
        if src and os.path.isfile(src):
            cmds.append(f"/usr/bin/install -o root -g root -m 0755 {shlex.quote(src)} {shlex.quote(dst)}")
    if not os.path.isfile(RESILIENCE_CONF_PATH):
        toml_body = render_resilience_toml(load_resilience_config())
        cmds.append(
            f"if [ ! -f {shlex.quote(RESILIENCE_CONF_PATH)} ]; then "
            f"umask 0133 && printf '%s\\n' {shlex.quote(toml_body)} > {shlex.quote(RESILIENCE_CONF_PATH)} && "
            f"/usr/bin/chown root:root {shlex.quote(RESILIENCE_CONF_PATH)} && "
            f"/usr/bin/chmod 0644 {shlex.quote(RESILIENCE_CONF_PATH)}; fi"
        )
    return " && ".join(cmds)
