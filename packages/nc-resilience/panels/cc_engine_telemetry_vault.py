#!/usr/bin/env python3
"""NC-UI Centralized NAS & Vault Engine (`panels/cc_engine_telemetry_vault.py`)."""

import os
import re
import subprocess
from core import probe
from core.i18n import t
from core.privileges import get_privileged_action
from core.resilience import (
    exec_priv_helper,
    expand_user_token,
    load_resilience_config,
    parse_vault_share,
    read_env_kv,
    resolve_sbin_helper,
    run_helper_async,
)
from core.state import STATE
from ui.kit import set_feedback, start_inline_input
from ui.markup import format_bytes_short

_FIELD_SPECS = {
    "vault_host_open": ("host", 64, r"^[a-zA-Z0-9_.:-]{0,64}$"),
    "vault_port_open": ("port", 5, r"^([0-9]{1,5})?$"),
    "vault_mp_open": ("mountpoint", 96, r"^/[a-zA-Z0-9_./{}$-]{1,96}$"),
    "vault_shares_open": ("shares", 160, r"^[a-zA-Z0-9_.,:/{}$-]{0,160}$"),
    "vault_symdir_open": ("user_symlink_dir", 64, r"^(~/[a-zA-Z0-9_./{}$-]{1,62}|/[a-zA-Z0-9_./{}$-]{1,63})?$"),
    "vault_mopts_open": ("mount_opts", 96, r"^[a-zA-Z0-9_.,=:/-]{0,96}$"),
    "vault_user_open": ("ssh_user", 32, r"^[a-zA-Z0-9_.-]{0,32}$"),
    "vault_rpath_open": ("remote_path", 80, r"^(/[a-zA-Z0-9_./-]{1,80})?$"),
    "vault_subdir_open": ("backup_root_subdir", 80, r"^[a-zA-Z0-9_./-]{1,80}$"),
    "vault_pkgdir_open": ("pkglist_subdir", 80, r"^[a-zA-Z0-9_./-]{1,80}$"),
    "vault_alias_open": ("sys_alias", 16, r"^[a-z0-9_-]{1,16}$"),
    "vault_folder_open": ("sys_folder", 48, r"^[a-zA-Z0-9_.-]{1,48}$"),
}

_ENUM_CFG_FIELDS = {
    "protocol": ("smb", "nfs", "ssh", "local"),
    "mount_visibility": ("symlink", "filemanager", "both", "none"),
    "smb_version": ("auto", "3.1.1", "3.0", "2.1", "2.0", "1.0"),
}


def _read_mountinfo_map() -> dict[str, str]:
    """Return `{mountpoint: fstype}` parsed from `/proc/self/mountinfo` (zero fork)."""
    out: dict[str, str] = {}
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                if " - " not in ln:
                    continue
                left, right = ln.split(" - ", 1)
                lparts = left.split()
                rparts = right.split()
                if len(lparts) >= 5 and rparts:
                    mp = lparts[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
                    out[mp] = rparts[0].lower()
    except Exception:
        pass
    return out


def _detect_trust_state(hw_uuid: str) -> str:
    live_uuid = ""
    try:
        if os.path.isfile("/sys/class/dmi/id/product_uuid"):
            with open("/sys/class/dmi/id/product_uuid", "r", encoding="utf-8", errors="ignore") as f:
                live_uuid = f.read().strip().lower()
    except Exception:
        pass
    cfg_uuid = str(hw_uuid or "").strip().lower()
    if cfg_uuid and live_uuid and cfg_uuid != live_uuid:
        return "UNTRUSTED"
    origin_env = read_env_kv(os.environ.get("NC_RECOVERY_ORIGIN_FILE", "/etc/ncui/recovery-origin.env"))
    if os.path.isdir("/run/recovery-ram/upper") or origin_env.get("NC_RECOVERY_IMAGE") == "1":
        return "RECOVERY"
    return "TRUSTED"


def _stat_usage(path: str) -> tuple[int, int, str]:
    try:
        st = os.statvfs(path)
        tot = int(st.f_blocks) * int(st.f_frsize)
        avail = int(st.f_bavail) * int(st.f_frsize)
        if tot <= 0:
            return 0, 0, "-"
        used = max(0, tot - int(st.f_bfree) * int(st.f_frsize))
        _ = avail
        return used, tot, f"{format_bytes_short(used)}/{format_bytes_short(tot)}"
    except OSError:
        return 0, 0, "-"


def probe_vault_status() -> dict:
    """Zero-fork probe of configured `[vault]` storage mount/readiness across SMB/NFS/SSH/Local."""
    res_cfg = load_resilience_config()
    vcfg = res_cfg["vault"]
    icfg = res_cfg["identity"]
    proto = str(vcfg.get("protocol") or "smb").lower()
    host = str(vcfg.get("host") or "")
    mp = expand_user_token(str(vcfg.get("mountpoint") or "/nas")).rstrip("/") or "/nas"
    vis = str(vcfg.get("mount_visibility") or "symlink").lower()
    sym_raw = str(vcfg.get("user_symlink_dir") or "")
    sym_dir = expand_user_token(sym_raw).rstrip("/") if sym_raw else ""
    shares_cfg = [str(s) for s in (vcfg.get("shares") or []) if str(s).strip()]

    mounted_map = _read_mountinfo_map()
    base = mp
    active: list[str] = []
    share_rows: list[dict] = []

    if proto in ("smb", "nfs"):
        nas_hits: list[str] = []
        run_hits: list[str] = []
        for idx, spec in enumerate(shares_cfg):
            rem, tgt_mp = parse_vault_share(spec, mp)
            lbl = os.path.basename(tgt_mp) or rem.rsplit("/", 1)[-1] or spec
            def_mp = os.path.join(mp, rem.rstrip("/").rsplit("/", 1)[-1] or lbl)
            run_mp = f"/run/nas/{lbl}"
            in_tgt = tgt_mp in mounted_map
            in_run = run_mp in mounted_map
            is_m = in_tgt or in_run
            act_mp = run_mp if (in_run and not in_tgt) else tgt_mp
            fs_t = mounted_map.get(act_mp, "")
            used_b, tot_b, usage_s = _stat_usage(act_mp) if is_m else (0, 0, "-")
            sym_p = os.path.join(sym_dir, lbl) if (sym_dir and vis in ("symlink", "both")) else ""
            sym_ok = bool(sym_p and os.path.islink(sym_p))
            if in_tgt:
                nas_hits.append(lbl)
            elif in_run:
                run_hits.append(lbl)
            share_rows.append({
                "idx": idx,
                "raw_spec": spec,
                "remote": rem,
                "target_mp": act_mp,
                "default_mp": def_mp,
                "label": lbl,
                "is_custom_mp": (":/" in spec),
                "mounted": is_m,
                "fs_type": fs_t,
                "used_bytes": used_b,
                "total_bytes": tot_b,
                "usage_str": usage_s,
                "symlink_path": sym_p,
                "symlink_ok": sym_ok,
            })
        if run_hits and not nas_hits:
            base = "/run/nas"
            active = run_hits
        else:
            active = nas_hits
        mounted = bool(active)
        cnt = len(active)
        tot = max(1, len(shares_cfg))
    elif proto == "ssh":
        is_fused = mp in mounted_map
        is_staged = os.path.isfile(os.path.join(mp, ".ncui_ssh_mounted"))
        mounted = bool(is_fused or is_staged)
        cnt = 1 if mounted else 0
        tot = 1
        active = ["sshfs" if is_fused else "rsync"] if mounted else []
    else:
        mounted = os.path.isdir(mp)
        cnt = 1 if mounted else 0
        tot = 1
        active = ["local"] if mounted else []

    creds_path = str(vcfg.get("smb_creds_file") or "/etc/samba/nas-cred")
    if creds_path.lower() == "guest":
        creds_mode = "guest"
    elif os.path.isfile(creds_path):
        creds_mode = "saved"
    else:
        creds_mode = "missing"

    priv_v = get_privileged_action("vault_mount")
    priv_h = get_privileged_action("hanoi_sync")
    backup_sub = str(vcfg.get("backup_root_subdir") or "ncui-vault").strip("/")
    pkg_sub = str(vcfg.get("pkglist_subdir") or f"{backup_sub}/pkglists").strip("/")
    return {
        "mounted": mounted,
        "count": cnt,
        "total": tot,
        "base": base,
        "shares": active,
        "shares_cfg": shares_cfg,
        "share_rows": share_rows,
        "protocol": proto,
        "host": host,
        "port": str(vcfg.get("port") or ""),
        "mount_visibility": vis,
        "user_symlink_dir": sym_raw,
        "smb_version": str(vcfg.get("smb_version") or "auto"),
        "mount_opts": str(vcfg.get("mount_opts") or ""),
        "ssh_user": str(vcfg.get("ssh_user") or ""),
        "remote_path": str(vcfg.get("remote_path") or ""),
        "backup_root": os.path.join(base, backup_sub),
        "pkglist_dir": os.path.join(base, pkg_sub),
        "backup_root_subdir": backup_sub,
        "pkglist_subdir": pkg_sub,
        "smb_creds_file": creds_path,
        "creds_mode": creds_mode,
        "trust_state": _detect_trust_state(icfg.get("hw_uuid", "")),
        "sys_alias": str(icfg.get("sys_alias") or ""),
        "sys_folder": str(icfg.get("sys_folder") or ""),
        "priv_vault_ok": bool(priv_v and priv_v.check_installed()),
        "priv_hanoi_ok": bool(priv_h and priv_h.check_installed()),
    }


def is_vault_mounted() -> bool:
    return probe_vault_status()["mounted"]


def collect_vault_page_data() -> dict:
    res_cfg = load_resilience_config()
    nas = probe_vault_status()
    rows = nas["share_rows"]
    sel_idx = int(STATE.get("system", "vault_sel_share_idx", 0) or 0)
    if not rows:
        sel_idx = -1
    elif sel_idx < 0 or sel_idx >= len(rows):
        sel_idx = 0
        STATE.set("system", "vault_sel_share_idx", 0)
    return {
        "nas": nas,
        "vault_cfg": res_cfg["vault"],
        "ident_cfg": res_cfg["identity"],
        "vault_busy": str(STATE.get("system", "vault_busy", "") or ""),
        "selected_share_idx": sel_idx,
        "confirm_rm_idx": int(STATE.get("system", "vault_confirm_rm_idx", -1)),
        "has_xdg_open": probe.has_bin("xdg-open"),
    }


def prompt_smb_user(cc_panel) -> None:
    nas = probe_vault_status()
    host = nas.get("host") or "NAS"
    cc_panel.request_prompt(
        "cc_sys_smb_user",
        t("cc_system.prompt_smb_user", p0=host),
        nas.get("ssh_user") or "",
        title=t("cc_system.prompt_smb_user_title"),
        required=True,
    )


def commit_vault_config(cc_panel, key: str, val: str, ok_msg: str | None = None) -> bool:
    opened, rc, out = exec_priv_helper(
        cc_panel, "nc-vault", "vault_mount", ["config-stdin"], stdin_data=f"{key}={val}\n", timeout=8.0
    )
    if not opened:
        if rc == 0:
            set_feedback("system", True, ok_msg or t("cc_system.vault_fb_cfg_saved", p0=f"{key}={val or '-'}"))
        else:
            set_feedback("system", False, t("cc_system.vault_fb_err", p0=(out or "error")[:140]))
    return rc == 0 and not opened


def save_shares_list(cc_panel, shares: list[str], ok_msg: str | None = None) -> bool:
    csv_val = ",".join(str(s).strip() for s in shares if str(s).strip())
    return commit_vault_config(cc_panel, "shares", csv_val, ok_msg=ok_msg)


def _clear_hanoi_caches() -> None:
    STATE.set("system", "hanoi_list_items", None)
    STATE.set("system", "hanoi_hist_items", None)
    STATE.set("system", "pkglist_items", None)
    STATE.set("system", "hanoi_versions_map", {})


def trigger_vault_mount_action(cc_panel, action: str, on_mounted_cb=None) -> None:
    busy_lbl = {"mount": "MOUNT", "remount": "REMOUNT", "umount": "UMOUNT", "ssh-push": "PUSH"}.get(action, "VAULT")
    tmo = 25.0 if action == "umount" else 30.0

    def _on_done(opened: bool, rc: int, out: str):
        if opened:
            return
        if action in ("mount", "remount") and (rc == 10 or "CREDENTIALS_REQUIRED" in out):
            prompt_smb_user(cc_panel)
            return
        if rc == 0:
            _clear_hanoi_caches()
            if action == "umount":
                set_feedback("system", True, t("cc_system.vault_fb_unmounted"))
            elif action == "ssh-push":
                set_feedback("system", True, t("cc_system.vault_fb_ssh_pushed"))
            else:
                set_feedback("system", True, t("cc_system.vault_fb_mounted", p0=probe_vault_status()["base"]))
                if callable(on_mounted_cb):
                    on_mounted_cb()
        else:
            msg_s = (out.splitlines()[-1] if out else "error")[:140]
            set_feedback("system", False, t("cc_system.vault_fb_err", p0=msg_s))

    run_helper_async(cc_panel, "vault_busy", busy_lbl, "nc-vault", "vault_mount", [action], timeout=tmo, on_done=_on_done)


def _valid_vault_field(key: str, pattern: str, val: str) -> bool:
    s = val.strip()
    if ".." in s or not re.match(pattern, s):
        return False
    if key == "port" and s:
        return s.isdigit() and 1 <= int(s) <= 65535
    return True


def _open_folder_detached(path: str) -> None:
    if not path or not os.path.isdir(path) or not probe.has_bin("xdg-open"):
        set_feedback("system", False, t("cc_system.vault_fb_open_err", p0=path or "-"))
        return
    try:
        subprocess.Popen(
            ["xdg-open", path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        set_feedback("system", False, t("cc_system.vault_fb_open_err", p0=str(e)[:100]))


def handle_vault_link(cc_panel, uri: str, on_mounted_cb=None) -> bool:
    if not uri.startswith("cc_sys:"):
        return False
    act = uri[len("cc_sys:"):]

    if act == "mount_nas":
        trigger_vault_mount_action(cc_panel, "mount", on_mounted_cb=on_mounted_cb)
        return True
    if act == "nas_remount":
        trigger_vault_mount_action(cc_panel, "remount", on_mounted_cb=on_mounted_cb)
        return True
    if act == "nas_umount":
        trigger_vault_mount_action(cc_panel, "umount")
        return True
    if act == "vault_ssh_push":
        trigger_vault_mount_action(cc_panel, "ssh-push")
        return True
    if act == "vault_goto_tab":
        if hasattr(cc_panel, "set_section") and getattr(cc_panel, "section", "") != "telemetry":
            cc_panel.set_section("telemetry")
        if hasattr(cc_panel, "set_subtab"):
            cc_panel.set_subtab("vault")
        return True
    if act == "vault_creds_auth":
        prompt_smb_user(cc_panel)
        return True
    if act.startswith("vault_creds_mode:"):
        mode = act.split(":", 1)[1]
        if mode in ("guest", "file"):
            target = "guest" if mode == "guest" else "/etc/samba/nas-cred"
            commit_vault_config(cc_panel, "smb_creds_file", target)
        return True
    if act.startswith("vault_cfg:") and ":" in act[len("vault_cfg:"):]:
        k, v = act[len("vault_cfg:"):].split(":", 1)
        if k in _ENUM_CFG_FIELDS and v in _ENUM_CFG_FIELDS[k]:
            commit_vault_config(cc_panel, k, v)
        return True
    if act in _FIELD_SPECS:
        rcfg = load_resilience_config()
        vcfg, icfg = rcfg["vault"], rcfg["identity"]
        cfg_k, ml, pat = _FIELD_SPECS[act]
        if cfg_k == "shares":
            init_v = ",".join(str(s) for s in (vcfg.get("shares") or []))
        elif cfg_k in ("sys_alias", "sys_folder"):
            init_v = icfg.get(cfg_k, "")
        else:
            init_v = vcfg.get(cfg_k, "")

        def _commit_field(new_val: str, key=cfg_k, pattern=pat):
            val_s = new_val.strip()
            if not _valid_vault_field(key, pattern, val_s):
                set_feedback("system", False, t("cc_system.vault_fb_err", p0=f"{key}={val_s}"))
                return
            commit_vault_config(cc_panel, key, val_s)

        start_inline_input(
            act[:-5],
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=str(init_v),
            max_len=ml,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda s, k=cfg_k, p=pat: _valid_vault_field(k, p, s),
            on_commit=_commit_field,
        )
        return True
    if act == "vault_share_add":
        cc_panel.request_prompt(
            "cc_sys_vault_share_add",
            t("cc_system.vault_prompt_share_lbl"),
            "",
            title=t("cc_system.vault_prompt_share_title"),
            required=True,
        )
        return True
    if act.startswith("vault_share_sel:"):
        try:
            idx = int(act.split(":", 1)[1])
        except ValueError:
            idx = 0
        STATE.set("system", "vault_sel_share_idx", idx)
        STATE.set("system", "vault_confirm_rm_idx", -1)
        return True
    if act.startswith("vault_share_edit_mp:"):
        try:
            idx = int(act.split(":", 1)[1])
        except ValueError:
            return True
        nas = probe_vault_status()
        rows = nas["share_rows"]
        if not (0 <= idx < len(rows)):
            return True
        row = rows[idx]
        cur_mp_raw = row["raw_spec"].split(":/", 1)[1] if ":/" in row["raw_spec"] else row["target_mp"]
        init_mp = ("/" + cur_mp_raw) if (":/" in row["raw_spec"]) else row["target_mp"]
        pat = r"^(/[a-zA-Z0-9_./{}$-]{1,96})?$"

        def _commit_mp(new_val: str, i=idx, rem=row["remote"], def_mp=row["default_mp"]):
            val_s = new_val.strip().rstrip("/")
            if val_s and (".." in val_s or not re.match(pat, val_s)):
                set_feedback("system", False, t("cc_system.vault_fb_err", p0=val_s))
                return
            shares = list(probe_vault_status()["shares_cfg"])
            if not (0 <= i < len(shares)):
                return
            new_spec = rem if (not val_s or val_s == def_mp) else f"{rem}:{val_s}"
            shares[i] = new_spec
            if save_shares_list(cc_panel, shares, ok_msg=t("cc_system.vault_fb_share_updated", p0=rem, p1=val_s or def_mp)):
                if cc_panel.shell:
                    cc_panel.shell.refresh_view()

        start_inline_input(
            "vault_share_mp",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=init_mp,
            max_len=96,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda s, p=pat: ".." not in s and bool(re.match(p, s.strip())),
            on_commit=_commit_mp,
        )
        return True
    if act.startswith("vault_share_reset_mp:"):
        try:
            idx = int(act.split(":", 1)[1])
        except ValueError:
            return True
        nas = probe_vault_status()
        shares = list(nas["shares_cfg"])
        rows = nas["share_rows"]
        if 0 <= idx < len(shares):
            rem = rows[idx]["remote"]
            shares[idx] = rem
            save_shares_list(cc_panel, shares, ok_msg=t("cc_system.vault_fb_share_updated", p0=rem, p1=rows[idx]["default_mp"]))
        return True
    if act.startswith("vault_share_rm_ask:"):
        try:
            STATE.set("system", "vault_confirm_rm_idx", int(act.split(":", 1)[1]))
        except ValueError:
            pass
        return True
    if act == "vault_share_rm_cancel":
        STATE.set("system", "vault_confirm_rm_idx", -1)
        return True
    if act.startswith("vault_share_rm_do:"):
        try:
            idx = int(act.split(":", 1)[1])
        except ValueError:
            return True
        STATE.set("system", "vault_confirm_rm_idx", -1)
        shares = list(probe_vault_status()["shares_cfg"])
        if 0 <= idx < len(shares):
            removed = shares.pop(idx)
            if save_shares_list(cc_panel, shares, ok_msg=t("cc_system.vault_fb_share_removed", p0=removed)):
                STATE.set("system", "vault_sel_share_idx", max(0, min(idx, len(shares) - 1)) if shares else -1)
        return True
    if act == "vault_open_base":
        _open_folder_detached(probe_vault_status()["base"])
        return True
    if act.startswith("vault_open_fm:"):
        try:
            idx = int(act.split(":", 1)[1])
        except ValueError:
            return True
        rows = probe_vault_status()["share_rows"]
        if 0 <= idx < len(rows):
            _open_folder_detached(rows[idx]["target_mp"])
        return True
    return False


def handle_vault_prompt(cc_panel, action_id: str, text: str, on_mounted_cb=None) -> bool:
    if action_id == "cc_sys_smb_user":
        smb_u = str(text or "").strip()
        if not smb_u:
            return True
        nas = probe_vault_status()
        host = nas.get("host") or "NAS"
        creds_f = nas.get("smb_creds_file") or "/etc/samba/nas-cred"
        cc_panel.request_prompt(
            f"cc_sys_smb_pass|{smb_u}",
            t("cc_system.prompt_smb_pass", p0=smb_u, p1=host, p2=creds_f),
            "",
            secret=True,
            title=t("cc_system.prompt_smb_pass_title"),
            required=True,
        )
        return True
    if action_id.startswith("cc_sys_smb_pass|"):
        smb_u = action_id.split("|", 1)[1]
        smb_p = str(text or "")
        if not smb_u or not smb_p:
            return True

        def _on_creds(opened: bool, rc: int, out: str):
            if opened:
                return
            if rc == 0:
                _clear_hanoi_caches()
                set_feedback("system", True, t("cc_system.vault_fb_creds_ok"))
                if callable(on_mounted_cb):
                    on_mounted_cb()
            else:
                set_feedback("system", False, t("cc_system.vault_fb_err", p0=(out.splitlines()[-1] if out else "auth failed")[:140]))

        run_helper_async(
            cc_panel,
            "vault_busy",
            "AUTH",
            "nc-vault",
            "vault_mount",
            ["creds-stdin"],
            stdin_data=f"{smb_u}\n{smb_p}\n1\n",
            timeout=25.0,
            on_done=_on_creds,
        )
        return True
    if action_id == "cc_sys_vault_share_add":
        spec = str(text or "").strip()
        if not spec or ".." in spec or not re.match(r"^[a-zA-Z0-9_.:/{}$-]{1,128}$", spec):
            set_feedback("system", False, t("cc_system.vault_fb_err", p0=spec or "empty"))
            return True
        shares = list(probe_vault_status()["shares_cfg"])
        if spec not in shares:
            shares.append(spec)
        if save_shares_list(cc_panel, shares, ok_msg=t("cc_system.vault_fb_share_added", p0=spec)):
            STATE.set("system", "vault_sel_share_idx", len(shares) - 1)
        return True
    return False
