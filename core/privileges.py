#!/usr/bin/env python3
"""NC-UI Centralized Privileged Actions & Sudoers Registry (`core/privileges.py`)."""

import atexit
import getpass
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from core import probe
from core.config import CONFIG_DIR
from core.i18n import t
from core.state import STATE
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, FeedbackBox, Txt

SUDOERS_DIR = "/etc/sudoers.d"
SUDOERS_STATE_FILE = os.path.join(CONFIG_DIR, "sudoers_state.json")

_SUDO_L_CACHE = {"ts": 0.0, "text": "", "ok": False}
_SUDO_SESSION = {"unlocked": False, "expires_at": 0.0, "timeout_sec": 300.0}
_SUDO_ALIVE_CACHE = {"ts": 0.0, "ok": False}


def _detect_sudo_timeout_sec() -> float:
    ok, txt = _get_sudo_list_cached()
    if ok and txt:
        m = re.search(r"timestamp_timeout\s*=\s*([0-9]+(?:\.[0-9]+)?)", txt)
        if m:
            try:
                mins = float(m.group(1))
                if mins > 0:
                    return max(30.0, mins * 60.0)
            except ValueError:
                pass
    return 300.0


def lock_sudo_session(drop_ticket: bool = True) -> None:
    """Lock the in-memory batch privilege session and invalidate this process's sudo ticket."""
    _SUDO_SESSION["unlocked"] = False
    _SUDO_SESSION["expires_at"] = 0.0
    _SUDO_ALIVE_CACHE["ts"] = 0.0
    _SUDO_ALIVE_CACHE["ok"] = False
    if drop_ticket and probe.has_bin("sudo"):
        probe.run_cmd(["sudo", "-k"], timeout=1.0)


atexit.register(lock_sudo_session)


def unlock_sudo_session(password: str) -> tuple[bool, str]:
    """Verify sudo password once and unlock batch privilege operations until timeout/panel exit."""
    pwd = str(password or "")
    if not pwd:
        return False, t("privileges.err_auth_cancelled")
    try:
        res = subprocess.run(
            ["sudo", "-k", "-S", "-v", "-p", ""],
            input=f"{pwd}\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=8.0,
        )
    except Exception as e:
        lock_sudo_session(drop_ticket=False)
        return False, str(e)
    if res.returncode != 0:
        lock_sudo_session(drop_ticket=False)
        out_s = (res.stdout or "").strip()
        return False, out_s or t("privileges.err_auth_cancelled")

    t_sec = _detect_sudo_timeout_sec()
    now = time.monotonic()
    _SUDO_SESSION["unlocked"] = True
    _SUDO_SESSION["timeout_sec"] = t_sec
    _SUDO_SESSION["expires_at"] = now + t_sec
    _SUDO_ALIVE_CACHE["ts"] = now
    _SUDO_ALIVE_CACHE["ok"] = True
    mins = max(1, int(round(t_sec / 60.0)))
    return True, t("privileges.ok_session_unlocked", mins=mins)


def is_sudo_session_unlocked() -> bool:
    """Return True only while unlocked in memory, within timeout, and sudo ticket is still alive."""
    if not _SUDO_SESSION["unlocked"]:
        return False
    now = time.monotonic()
    if now >= _SUDO_SESSION["expires_at"]:
        lock_sudo_session(drop_ticket=False)
        return False
    if (now - _SUDO_ALIVE_CACHE["ts"]) >= 2.0:
        ok, _ = probe.run_cmd(["sudo", "-n", "true"], timeout=0.8)
        _SUDO_ALIVE_CACHE["ts"] = now
        _SUDO_ALIVE_CACHE["ok"] = bool(ok)
        if not ok:
            lock_sudo_session(drop_ticket=False)
            return False
    return True


def get_sudo_session_remaining_sec() -> int:
    if not _SUDO_SESSION["unlocked"]:
        return 0
    return max(0, int(round(_SUDO_SESSION["expires_at"] - time.monotonic())))


def build_sudo_padlock_actions() -> list[str]:
    """Return top-border padlock status/action controls for batch sudoers editing."""
    if is_sudo_session_unlocked():
        rem = get_sudo_session_remaining_sec()
        rem_s = f"{max(1, (rem + 30) // 60)}m" if rem >= 60 else f"{rem}s"
        return [
            Badge(f"\uf09c {t('privileges.badge_unlocked', rem=rem_s)}", style="warn"),
            ActionBtn(f"\uf023 {t('privileges.btn_lock_session')}", "priv:lock", tone="danger_soft"),
        ]
    return [ActionBtn(f"\uf023 {t('privileges.btn_unlock_session')}", "priv:unlock", tone="warn")]

_POLKIT_AGENT_TOKENS = (
    "soteria",
    "polkit-gnome",
    "polkit-kde",
    "lxqt-policykit",
    "mate-polkit",
    "xfce-polkit",
    "hyprpolkitagent",
    "pantheon-agent-polkit",
    "polkit-dumb-agent",
    "polkit-mate",
)


def has_polkit_agent() -> bool:
    """Return True if a graphical Polkit authentication agent is active in /proc."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return False
    for pid in entries:
        if not pid.isdigit():
            continue
        try:
            comm = open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="ignore").read().strip().lower()
            cmdline = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
        except OSError:
            continue
        blob = f"{comm} {cmdline}"
        if any(tok in blob for tok in _POLKIT_AGENT_TOKENS):
            return True
    return False


def _current_user() -> str:
    u = getpass.getuser() or os.environ.get("USER", "root")
    return u if re.match(r"^[a-zA-Z_][a-zA-Z0-9_.-]*$", u) else "root"


def _read_sudoers_state() -> dict:
    if not os.path.exists(SUDOERS_STATE_FILE):
        return {}
    try:
        data = json.load(open(SUDOERS_STATE_FILE, "r", encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_sudoers_state(action_id: str, installed: bool) -> None:
    st = _read_sudoers_state()
    st[action_id] = bool(installed)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SUDOERS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
    except Exception:
        pass


def _get_sudo_list_cached() -> tuple[bool, str]:
    now = time.monotonic()
    if now - _SUDO_L_CACHE["ts"] < 2.0:
        return _SUDO_L_CACHE["ok"], _SUDO_L_CACHE["text"]
    ok, out, _ = probe.run_cmd_full(["sudo", "-n", "-l"], timeout=1.0)
    _SUDO_L_CACHE["ts"] = now
    _SUDO_L_CACHE["ok"] = bool(ok)
    _SUDO_L_CACHE["text"] = out or ""
    return _SUDO_L_CACHE["ok"], _SUDO_L_CACHE["text"]


def invalidate_priv_cache() -> None:
    _SUDO_L_CACHE["ts"] = 0.0


class PrivilegedAction:
    def __init__(
        self,
        action_id: str,
        rules: list[str],
        exec_cmd: list[str],
        extra_cmds: list[list[str]] | None = None,
    ):
        self.id = action_id
        self.rules = list(rules)
        self.exec_cmd = list(exec_cmd)
        self.extra_cmds = [list(c) for c in (extra_cmds or [])]

    @property
    def filename(self) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", self.id)
        return os.path.join(SUDOERS_DIR, f"ncui-{safe}")

    def title(self, is_it: bool = False) -> str:
        return t(f"privileges.{self.id}.title")

    def description(self, is_it: bool = False) -> str:
        return t(f"privileges.{self.id}.desc")

    def warning(self, is_it: bool = False) -> str:
        return t(f"privileges.{self.id}.warn")

    def sudoers_content(self, user: str = None) -> str:
        u = user or _current_user()
        rule_str = ", ".join(self.rules)
        return (
            f"# Generated by NC-UI Privilege Framework — Least Privilege Policy\n"
            f"# Action ID: {self.id} ({self.title()})\n"
            f"Defaults:{u} !requiretty\n"
            f"{u} ALL=(root) NOPASSWD: {rule_str}\n"
        )

    def check_installed(self) -> bool:
        try:
            if os.path.exists(self.filename):
                return True
        except Exception:
            pass
        ok, txt = _get_sudo_list_cached()
        if ok and self.rules and self.rules[0] in txt:
            return True
        return bool(_read_sudoers_state().get(self.id, False))


PRIVILEGED_ACTIONS: dict[str, PrivilegedAction] = {
    a.id: a
    for a in (
        PrivilegedAction(
            "uinput_unlock",
            ["/usr/bin/chmod 0666 /dev/uinput"],
            ["/usr/bin/chmod", "0666", "/dev/uinput"],
        ),
        PrivilegedAction(
            "nftables_apply",
            ["/usr/bin/nft -f /etc/nftables.conf"],
            ["/usr/bin/nft", "-f", "/etc/nftables.conf"],
        ),
        PrivilegedAction(
            "nftables_service",
            ["/usr/bin/systemctl restart nftables.service", "/usr/bin/systemctl start nftables.service"],
            ["/usr/bin/systemctl", "restart", "nftables.service"],
        ),
        PrivilegedAction(
            "sbctl_verify",
            ["/usr/bin/sbctl status", "/usr/bin/sbctl verify"],
            ["/usr/bin/sbctl", "status"],
        ),
        PrivilegedAction(
            "usbguard_inspect",
            ["/usr/bin/usbguard list-devices"],
            ["/usr/bin/usbguard", "list-devices"],
        ),
        PrivilegedAction(
            "cups_service",
            [
                "/usr/bin/systemctl start cups.service",
                "/usr/bin/systemctl stop cups.service",
                "/usr/bin/systemctl restart cups.service",
                "/usr/bin/systemctl enable cups.service",
                "/usr/bin/systemctl disable cups.service",
                "/usr/bin/systemctl start avahi-daemon.service",
                "/usr/bin/systemctl restart avahi-daemon.service",
            ],
            ["/usr/bin/systemctl", "start", "cups.service"],
        ),
        PrivilegedAction(
            "cups_sysgroup",
            [
                f"/usr/bin/gpasswd -a {_current_user()} sys",
                f"/usr/bin/gpasswd -a {_current_user()} lp",
            ],
            ["/usr/bin/gpasswd", "-a", _current_user(), "sys"],
            extra_cmds=[["/usr/bin/gpasswd", "-a", _current_user(), "lp"]],
        ),
        PrivilegedAction(
            "power_cpu_tune",
            [
                "/usr/bin/cpupower frequency-set -g performance",
                "/usr/bin/cpupower frequency-set -g schedutil",
                "/usr/bin/cpupower frequency-set -g powersave",
                "/usr/bin/cpupower frequency-set -g ondemand",
                "/usr/bin/cpupower frequency-set -g conservative",
                "/usr/bin/cpupower set -e default",
                "/usr/bin/cpupower set -e performance",
                "/usr/bin/cpupower set -e balance_performance",
                "/usr/bin/cpupower set -e balance_power",
                "/usr/bin/cpupower set -e power",
                "/usr/bin/tee /sys/devices/system/cpu/cpufreq/boost",
            ],
            ["/usr/bin/cpupower", "frequency-set", "-g", "powersave"],
        ),
        PrivilegedAction(
            "power_logind",
            [
                "/usr/bin/mkdir -p /etc/systemd/logind.conf.d",
                "/usr/bin/tee /etc/systemd/logind.conf.d/ncui.conf",
            ],
            ["/usr/bin/mkdir", "-p", "/etc/systemd/logind.conf.d"],
        ),
        PrivilegedAction(
            "gaming_mode",
            [
                "/usr/local/sbin/nc-gaming.sh on",
                "/usr/local/sbin/nc-gaming.sh off",
                "/usr/local/sbin/nc-gaming.sh status",
            ],
            ["/usr/local/sbin/nc-gaming.sh", "status"],
        ),
        PrivilegedAction(
            "power_fan_profile",
            [
                "/usr/local/sbin/nc-fan-profile.sh silent",
                "/usr/local/sbin/nc-fan-profile.sh balanced",
                "/usr/local/sbin/nc-fan-profile.sh cool",
                "/usr/local/sbin/nc-fan-profile.sh apply-stdin",
                "/usr/local/sbin/nc-fan-profile.sh status",
            ],
            ["/usr/local/sbin/nc-fan-profile.sh", "status"],
        ),
    )
}


def get_privileged_action(action_id: str) -> PrivilegedAction | None:
    return PRIVILEGED_ACTIONS.get(action_id)


def list_privileged_actions() -> list[PrivilegedAction]:
    return list(PRIVILEGED_ACTIONS.values())


def _set_priv_fb(ok: bool, msg: str) -> None:
    STATE.set("privileges", "fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_priv_fb() -> dict:
    fb = STATE.get("privileges", "fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def validate_sudoers_text(content: str) -> tuple[bool, str]:
    fd, tmp_path = tempfile.mkstemp(prefix=".ncui_sudo_", dir="/tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        res = subprocess.run(["/usr/bin/visudo", "-cf", tmp_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3.0)
        out = (res.stdout or "").strip()
        return res.returncode == 0, out
    except Exception as e:
        return False, str(e)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass


def _run_as_root(script: str, password: str = None, use_session: bool = False) -> tuple[bool, str]:
    try:
        if password is not None:
            res = subprocess.run(
                ["sudo", "-k", "-S", "-p", "", "bash", "-c", script],
                input=f"{password}\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=12.0,
            )
            return res.returncode == 0, (res.stdout or "").strip()
        if use_session and is_sudo_session_unlocked():
            res = subprocess.run(
                ["sudo", "-n", "bash", "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=12.0,
            )
            out_s = (res.stdout or "").strip()
            if res.returncode == 0:
                _SUDO_SESSION["expires_at"] = time.monotonic() + _SUDO_SESSION["timeout_sec"]
                return True, out_s
            low_s = out_s.lower()
            if any(tok in low_s for tok in ("password is required", "no tty", "not allowed")):
                lock_sudo_session(drop_ticket=False)
            return False, out_s
        res = subprocess.run(
            ["pkexec", "bash", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=20.0,
        )
        return res.returncode == 0, (res.stdout or "").strip()
    except Exception as e:
        return False, str(e)


def authorize_action(action_id: str, password: str = None, run_after: bool = True, use_session: bool = False) -> tuple[bool, str]:
    act = get_privileged_action(action_id)
    if not act:
        return False, t("privileges.err_unknown_priv_action", action_id=action_id)

    content = act.sudoers_content()
    v_ok, v_out = validate_sudoers_text(content)
    if not v_ok:
        return False, t("privileges.err_visudo_failed", out=v_out)

    fd, tmp_path = tempfile.mkstemp(prefix=".ncui_sudoers_", dir="/tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        q_tmp = shlex.quote(tmp_path)
        q_dst = shlex.quote(act.filename)
        script = (
            f"/usr/bin/visudo -cf {q_tmp} && "
            f"/usr/bin/install -o root -g root -m 0440 {q_tmp} {q_dst} && "
            f"/usr/bin/visudo -cf {q_dst}"
        )
        ok, msg = _run_as_root(script, password=password, use_session=use_session)
        if not ok:
            return False, msg or t("privileges.err_auth_cancelled")
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass

    invalidate_priv_cache()
    _write_sudoers_state(action_id, True)
    if run_after:
        ex_ok, ex_msg = execute_privileged(action_id)
        if not ex_ok:
            return True, t("privileges.ok_installed_exec_note", filename=act.filename, note=ex_msg)
    return True, t("privileges.ok_installed", filename=act.filename)


def revoke_action(action_id: str, password: str = None, use_session: bool = False) -> tuple[bool, str]:
    act = get_privileged_action(action_id)
    if not act:
        return False, t("privileges.err_unknown_action", action_id=action_id)
    script = f"/usr/bin/rm -f {shlex.quote(act.filename)}"
    ok, msg = _run_as_root(script, password=password, use_session=use_session)
    if not ok and password is None and not use_session:
        return False, msg or t("privileges.err_revoke_cancelled")
    if not ok:
        return False, msg or t("privileges.err_revoke_failed")
    invalidate_priv_cache()
    _write_sudoers_state(action_id, False)
    return True, t("privileges.ok_revoked", filename=act.filename)


def execute_privileged(action_id: str, password: str = None) -> tuple[bool, str]:
    act = get_privileged_action(action_id)
    if not act:
        return False, t("privileges.err_unknown_action", action_id=action_id)
    outputs: list[str] = []
    try:
        for cmd in [act.exec_cmd] + act.extra_cmds:
            if password is not None:
                res = subprocess.run(
                    ["sudo", "-k", "-S", "-p", ""] + cmd,
                    input=f"{password}\n",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=10.0,
                )
            else:
                res = subprocess.run(
                    ["sudo", "-n"] + cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=10.0,
                )
            out_s = (res.stdout or "").strip()
            if out_s:
                outputs.append(out_s)
            if res.returncode != 0:
                return False, "\n".join(outputs) or t("privileges.ok_executed")
        return True, "\n".join(outputs) or t("privileges.ok_executed")
    except Exception as e:
        return False, str(e)


def request_privilege_modal(cc_panel, action_id: str) -> None:
    if action_id in PRIVILEGED_ACTIONS:
        STATE.set("privileges", "pending_action", action_id)
        STATE.set("privileges", "auth_mode", "grant")


def build_privilege_modal_card(cc_panel, canvas: Canvas, is_it: bool = False) -> list:
    cards = []
    fb = _get_priv_fb()
    if fb:
        cards.append(FeedbackBox(t("privileges.status_title"), bool(fb["ok"]), fb["msg"]))

    aid = str(STATE.get("privileges", "pending_action", "") or "")
    act = get_privileged_action(aid)
    if not act:
        return cards

    mode = str(STATE.get("privileges", "auth_mode", "grant") or "grant")
    title = (
        t("privileges.modal_revoke_title", id=act.id)
        if mode == "revoke"
        else t("privileges.modal_grant_title", id=act.id)
    )
    bx = Box(
        title,
        icon="\uf023",
        actions=[
            Txt("0440 root:root", role="schema"),
            ActionBtn(t("privileges.close_btn"), "priv:cancel", tone="danger_soft"),
        ],
        highlighted=False,
    )
    bx.add_row(" ")
    bx.add_kv(t("privileges.lbl_action"), Txt(act.title(), role="focus"))
    bx.add_kv(t("privileges.lbl_target"), Txt(act.filename, role="val"))
    bx.add_kv(t("privileges.lbl_seccheck"), Txt(t("privileges.seccheck_val"), role="val"))
    bx.add_kv(t("privileges.lbl_notice"), Txt(act.warning(), role="attention"))
    bx.add_section(t("privileges.sec_preview"))
    polkit_ok = has_polkit_agent()
    if mode == "revoke":
        bx.add_row(f"  {Txt(t('privileges.rm_comment', filename=act.filename), role='interrupt')}")
        bx.add_row(" ")
        rev_btns = [ActionBtn(t("privileges.btn_revoke_pwd"), f"priv:prompt_rev:{act.id}", tone="danger_soft")]
        if polkit_ok:
            rev_btns.append(ActionBtn(t("privileges.btn_revoke_polkit"), f"priv:pkexec_rev:{act.id}", tone="warn"))
        rev_btns.append(ActionBtn(t("common.cancel"), "priv:cancel", tone="neutral"))
        bx.add_row("  " + "  ".join(rev_btns))
    else:
        for ln in act.sudoers_content().splitlines():
            bx.add_row(f"  {Txt(canvas.truncate_plain(ln, canvas.inner_cols - 6), role='val' if not ln.startswith('#') else 'schema')}")
        bx.add_row(" ")
        grant_btns = [ActionBtn(t("privileges.btn_grant_pwd"), f"priv:prompt_auth:{act.id}", tone="apply")]
        if polkit_ok:
            grant_btns.append(ActionBtn(t("privileges.btn_grant_polkit"), f"priv:pkexec_auth:{act.id}", tone="apply"))
        grant_btns.extend([
            ActionBtn(t("privileges.btn_run_once"), f"priv:prompt_once:{act.id}", tone="warn"),
            ActionBtn(t("privileges.btn_auth_tab"), "priv:goto_auth", tone="neutral"),
        ])
        bx.add_row("  " + "  ".join(grant_btns))
    bx.add_row(" ")
    cards.append(bx)
    return cards


def handle_privilege_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("priv:"):
        return False
    parts = uri.split(":", 2)
    cmd = parts[1]
    aid = parts[2] if len(parts) > 2 else ""

    if cmd == "cancel":
        STATE.set("privileges", "pending_action", "")
    elif cmd == "unlock":
        cc_panel.request_prompt(
            "priv_unlock|session",
            t("privileges.prompt_unlock"),
            "",
            secret=True,
            title=t("privileges.prompt_unlock_title"),
            required=True,
        )
        return True
    elif cmd == "lock":
        lock_sudo_session(drop_ticket=True)
        _set_priv_fb(True, t("privileges.ok_session_locked"))
    elif cmd == "request" and aid in PRIVILEGED_ACTIONS:
        if is_sudo_session_unlocked():
            ok, msg = authorize_action(aid, use_session=True, run_after=True)
            _set_priv_fb(ok, msg)
            STATE.set("privileges", "pending_action", "")
            if not ok and not is_sudo_session_unlocked():
                request_privilege_modal(cc_panel, aid)
        else:
            STATE.set("privileges", "pending_action", aid)
            STATE.set("privileges", "auth_mode", "grant")
    elif cmd == "revoke" and aid in PRIVILEGED_ACTIONS:
        if is_sudo_session_unlocked():
            ok, msg = revoke_action(aid, use_session=True)
            _set_priv_fb(ok, msg)
            STATE.set("privileges", "pending_action", "")
            if not ok and not is_sudo_session_unlocked():
                STATE.set("privileges", "pending_action", aid)
                STATE.set("privileges", "auth_mode", "revoke")
        else:
            STATE.set("privileges", "pending_action", aid)
            STATE.set("privileges", "auth_mode", "revoke")
    elif cmd == "goto_auth":
        STATE.set("privileges", "pending_action", "")
        if hasattr(cc_panel, "set_section"):
            cc_panel.set_section("auth")
            cc_panel.set_subtab("sudoers")
    elif cmd == "pkexec_auth" and aid in PRIVILEGED_ACTIONS:
        ok, msg = authorize_action(aid, password=None, run_after=True)
        _set_priv_fb(ok, msg)
        if ok:
            STATE.set("privileges", "pending_action", "")
    elif cmd == "pkexec_rev" and aid in PRIVILEGED_ACTIONS:
        ok, msg = revoke_action(aid, password=None)
        _set_priv_fb(ok, msg)
        if ok:
            STATE.set("privileges", "pending_action", "")
    elif cmd == "prompt_auth" and aid in PRIVILEGED_ACTIONS:
        cc_panel.request_prompt(
            f"priv_auth|{aid}",
            t("privileges.prompt_auth", filename=PRIVILEGED_ACTIONS[aid].filename),
            "",
            secret=True,
            title=t("privileges.prompt_auth_title"),
            required=True,
        )
        return True
    elif cmd == "prompt_once" and aid in PRIVILEGED_ACTIONS:
        cc_panel.request_prompt(
            f"priv_once|{aid}",
            t("privileges.prompt_once", aid=aid),
            "",
            secret=True,
            title=t("privileges.prompt_once_title"),
            required=True,
        )
        return True
    elif cmd == "prompt_rev" and aid in PRIVILEGED_ACTIONS:
        cc_panel.request_prompt(
            f"priv_rev|{aid}",
            t("privileges.prompt_rev", filename=PRIVILEGED_ACTIONS[aid].filename),
            "",
            secret=True,
            title=t("privileges.prompt_rev_title"),
            required=True,
        )
        return True
    elif cmd == "exec" and aid in PRIVILEGED_ACTIONS:
        act = get_privileged_action(aid)
        if act and act.check_installed():
            ok, msg = execute_privileged(aid)
            _set_priv_fb(ok, msg)
        else:
            request_privilege_modal(cc_panel, aid)
    else:
        return False

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_privilege_prompt(cc_panel, action_id: str, text: str) -> bool:
    if not action_id.startswith("priv_"):
        return False
    pwd = str(text or "")
    if "|" not in action_id or not pwd:
        return False
    kind, aid = action_id.split("|", 1)
    if kind == "priv_unlock":
        ok, msg = unlock_sudo_session(pwd)
        _set_priv_fb(ok, msg)
        if ok:
            STATE.set("privileges", "pending_action", "")
        return True
    if kind == "priv_auth":
        ok, msg = authorize_action(aid, password=pwd, run_after=True)
        _set_priv_fb(ok, msg)
        if ok:
            STATE.set("privileges", "pending_action", "")
    elif kind == "priv_once":
        ok, msg = execute_privileged(aid, password=pwd)
        _set_priv_fb(ok, msg)
        if ok:
            STATE.set("privileges", "pending_action", "")
    elif kind == "priv_rev":
        ok, msg = revoke_action(aid, password=pwd)
        _set_priv_fb(ok, msg)
        if ok:
            STATE.set("privileges", "pending_action", "")
    return True
