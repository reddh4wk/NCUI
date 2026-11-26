#!/usr/bin/env python3
"""NC-UI Printers — URI & Prompt Action Dispatchers."""

import concurrent.futures
import csv
import datetime
import getpass
import grp
import io
import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse

from core import log, probe
from core.completion import rank_candidates
from core.config import get_cache_dir
from core.i18n import t
from core.paths import find_bin
from core.state import STATE
from ui.kit import ActionBtn, Badge, Box, InlineField, StateBadge, Txt, feedback_cards as _kit_feedback_cards, get_active_inline_entry, set_feedback, start_inline_input


def handle_cc_printers_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_prn:"):
        return False

    rest = uri[len("cc_prn:") :]
    act, _, tail = rest.partition(":")
    arg1, _, arg2 = tail.partition(":")
    shell = getattr(cc_panel, "shell", None)

    def _refresh() -> bool:
        if shell:
            shell.refresh_view()
        return True

    def _open_flt(entry_id: str, max_len: int, initial: str, setter) -> bool:
        start_inline_input(
            entry_id,
            shell=shell,
            trigger_uri=uri,
            initial_text=initial,
            max_len=max_len,
            strip_leading_hash=False,
            uppercase=False,
            on_change=setter,
            on_commit=setter,
        )
        return True

    if act not in ("ask_confirm", "delete_prn", "purge_queue", "class_delete"):
        _core().set_pending_confirm(cc_panel, "")

    if act == "refresh":
        _core().invalidate_printers_cache(keep_drivers=False)
        return _refresh()

    if act == "ask_confirm" and tail:
        _core().set_pending_confirm(cc_panel, tail)
        return _refresh()

    if act == "cancel_confirm":
        _core().set_pending_confirm(cc_panel, "")
        return _refresh()

    if act == "select_prn" and tail:
        cc_panel.set_state("prn_selected", tail)
        _core().update_drv_edit_state(printer="")
        return _refresh()

    if act == "dev_filter_open":
        return _open_flt(
            "prn_dev_filter_in",
            40,
            str(cc_panel.get_state("prn_dev_filter", "") or ""),
            lambda v: cc_panel.set_state("prn_dev_filter", v),
        )

    if act == "dev_filter_clear":
        cc_panel.set_state("prn_dev_filter", "")
        return _refresh()

    if act == "select_job" and tail:
        cc_panel.set_state("prn_selected_job", tail)
        return _refresh()

    if act == "select_class" and tail:
        cc_panel.set_state("prn_selected_class", tail)
        return _refresh()

    if act == "goto_subtab" and tail:
        cc_panel.set_subtab(tail)
        return _refresh()

    # --- Services, Privileges & Web UI ---
    if act == "webui":
        if not _core().get_cups_daemon_state()["scheduler_up"]:
            _core()._set_fb(False, t("cc_printers.err_spooler_down"))
            return _refresh()
        if not probe.has_bin("xdg-open"):
            _core()._set_fb(False, t("cc_printers.err_missing_bin", p0="xdg-open"))
            return _refresh()
        probe.spawn_bg(["xdg-open", "http://localhost:631"])
        _core()._set_fb(True, t("cc_printers.fb_opened_webui"))
        return _refresh()

    if act == "svc" and arg1 in ("start", "stop", "restart", "enable", "disable"):
        unit = arg2 or "cups.service"
        if unit not in ("cups.service", "cups.socket", "avahi-daemon.service"):
            _core()._set_fb(False, t("cc_printers.err_invalid_unit", p0=unit))
            return _refresh()

        def _after_svc():
            if unit == "cups.service" and arg1 in ("start", "restart"):
                lpstat_bin = _core()._cups_bin("lpstat")
                if lpstat_bin:
                    for _ in range(5):
                        s_ok, s_out, _ = _core()._run_c([lpstat_bin, "-r"], timeout=0.6)
                        if s_ok and "is running" in (s_out or "").lower():
                            break
                        time.sleep(0.2)
            _core().invalidate_printers_cache()

        _core()._exec_admin(
            cc_panel,
            ["systemctl", arg1, unit],
            t("cc_printers.fb_svc_done", p0=unit, p1=arg1),
            try_direct=False,
            after_fn=_after_svc,
        )
        return True

    if act == "clear_logs":
        err_log = "/var/log/cups/error_log"
        if not os.path.isfile(err_log):
            _core()._set_fb(False, t("cc_printers.err_no_log_file", p0=err_log))
            return _refresh()
        _core()._exec_admin(
            cc_panel,
            ["truncate", "-s", "0", err_log],
            t("cc_printers.fb_logs_cleared"),
            try_direct=False,
        )
        return True

    if act == "dismiss_logs_view":
        _core().dismiss_cups_logs_view()
        return _refresh()

    # --- Printer Lifecycle, In-Place Edit & Cloning ---
    if act == "set_default" and tail:
        if probe.has_bin("lpoptions"):
            _core()._run_c(["lpoptions", "-d", tail], timeout=1.5)
        _core()._exec_admin(cc_panel, ["lpadmin", "-d", tail], t("cc_printers.fb_default_set", p0=tail))
        return True

    if act == "enable_prn" and tail:
        _core()._exec_admin(cc_panel, ["cupsenable", tail], t("cc_printers.fb_prn_resumed", p0=tail))
        return True

    if act == "pause_prn" and tail:
        _core()._exec_admin(cc_panel, ["cupsdisable", tail], t("cc_printers.fb_prn_paused", p0=tail))
        return True

    if act == "accept_prn" and arg1:
        is_on = (arg2 == "1")
        bin_p = "cupsaccept" if is_on else "cupsreject"
        msg = t("cc_printers.fb_accept_on", p0=arg1) if is_on else t("cc_printers.fb_accept_off", p0=arg1)
        _core()._exec_admin(cc_panel, [bin_p, arg1], msg)
        return True

    if act == "share_prn" and arg1:
        val = "true" if arg2 == "1" else "false"
        _core()._exec_admin(
            cc_panel,
            ["lpadmin", "-p", arg1, "-o", f"printer-is-shared={val}"],
            t("cc_printers.fb_share_updated", p0=arg1, p1=val),
        )
        return True

    if act == "err_policy" and arg1 and arg2 in ("retry-job", "abort-job", "stop-printer"):
        _core()._exec_admin(
            cc_panel,
            ["lpadmin", "-p", arg1, "-o", f"printer-error-policy={arg2}"],
            t("cc_printers.fb_policy_set", p0=arg1, p1=arg2),
        )
        return True

    if act == "delete_prn" and tail:
        _core().set_pending_confirm(cc_panel, "")
        _core()._exec_admin(cc_panel, ["lpadmin", "-x", tail], t("cc_printers.fb_prn_deleted", p0=tail))
        return True

    if act == "restart_prn" and tail and _core()._QUEUE_NAME_RE.match(tail):
        _core()._exec_admin_batch(
            cc_panel,
            [["cupsenable", tail], ["cupsaccept", tail]],
            t("cc_printers.fb_prn_restarted", p0=tail),
        )
        return True

    if act == "open_addr" and tail:
        snap = _core().get_printers_snapshot()
        prn = next((p for p in snap["printers"] if p["name"] == tail), None)
        u = str(prn["uri"] if prn else "").strip()
        parsed = urlparse(u) if "://" in u else None
        host = (parsed.hostname or "").strip() if parsed else ""
        if not host:
            _core()._set_fb(False, t("cc_printers.err_no_prn_addr"))
        elif not probe.has_bin("xdg-open"):
            _core()._set_fb(False, t("cc_printers.err_missing_bin", p0="xdg-open"))
        else:
            scheme = "https" if (parsed and parsed.scheme in ("ipps", "https")) else "http"
            web_url = f"{scheme}://{host}"
            probe.spawn_bg(["xdg-open", web_url])
            _core()._set_fb(True, t("cc_printers.fb_opened_prn_web", p0=web_url))
        return _refresh()

    if act == "edit_uri" and tail and _core()._QUEUE_NAME_RE.match(tail):
        snap = _core().get_printers_snapshot()
        prn = next((p for p in snap["printers"] if p["name"] == tail), None)
        cur_u = prn["uri"] if prn else "ipp://"

        def _commit_uri(v: str, p_name=tail):
            nv = v.strip()
            _core()._exec_admin(cc_panel, ["lpadmin", "-p", p_name, "-E", "-v", nv], t("cc_printers.fb_uri_updated", p0=p_name))

        start_inline_input(
            f"prn_edit_uri_{tail}",
            shell=shell,
            trigger_uri=uri,
            initial_text=cur_u,
            max_len=128,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda v: (("://" in v.strip() and len(v.strip()) >= 6), t("cc_printers.err_bad_uri")),
            on_commit=_commit_uri,
        )
        return True

    if act == "rename_prn" and tail and _core()._QUEUE_NAME_RE.match(tail):
        snap = _core().get_printers_snapshot()
        existing = {p["name"] for p in snap["printers"]}

        def _val_rename(v: str, old_name=tail, names=existing):
            nv = v.strip()
            if not _core()._QUEUE_NAME_RE.match(nv):
                return (False, t("cc_printers.err_bad_queue_name"))
            if nv != old_name and nv in names:
                return (False, t("cc_printers.err_queue_exists", p0=nv))
            return (True, "")

        def _commit_rename(v: str, old_name=tail):
            new_name = v.strip()
            if not new_name or new_name == old_name:
                return
            cur_snap = _core().get_printers_snapshot()
            src = next((p for p in cur_snap["printers"] if p["name"] == old_name), None)
            if not src or not src["uri"]:
                _core()._set_fb(False, t("cc_printers.err_cmd_failed"))
                _refresh()
                return
            src_ppd = f"/etc/cups/ppd/{old_name}.ppd"
            drv_args = ["-P", src_ppd] if os.path.isfile(src_ppd) else (["-m", "raw"] if src["proto"] == "LOCAL" else ["-m", "everywhere"])
            add_cmd = ["lpadmin", "-p", new_name, "-E", "-v", src["uri"]] + drv_args
            if src["description"]:
                add_cmd += ["-D", src["description"]]
            if src["location"]:
                add_cmd += ["-L", src["location"]]
            add_cmd += [
                "-o",
                f"printer-is-shared={'true' if src['shared'] else 'false'}",
                "-o",
                f"printer-error-policy={src['error_policy'] or 'retry-job'}",
            ]
            cmds = [add_cmd]
            if src["accepting"]:
                cmds.append(["cupsaccept", new_name])
            else:
                cmds.append(["cupsreject", new_name])
            if not src["enabled"]:
                cmds.append(["cupsdisable", new_name])
            if _core().get_print_jobs("not-completed", printer=old_name):
                cmds.append(["lpmove", old_name, new_name])
            if src["is_default"]:
                cmds.append(["lpadmin", "-d", new_name])
            cmds.append(["lpadmin", "-x", old_name])

            def _after_rename():
                if src["is_default"] and probe.has_bin("lpoptions"):
                    _core()._run_c(["lpoptions", "-d", new_name], timeout=1.5)
                cc_panel.set_state("prn_selected", new_name)

            _core()._exec_admin_batch(
                cc_panel,
                cmds,
                t("cc_printers.fb_prn_renamed", p0=old_name, p1=new_name),
                after_fn=_after_rename,
            )

        start_inline_input(
            f"prn_edit_name_{tail}",
            shell=shell,
            trigger_uri=uri,
            initial_text=tail,
            max_len=64,
            strip_leading_hash=False,
            uppercase=False,
            validator=_val_rename,
            on_commit=_commit_rename,
        )
        return True

    if act == "clone_prompt" and tail:
        cc_panel.request_prompt(
            f"cc_prn_clone|{tail}",
            t("cc_printers.prompt_clone_name", p0=tail),
            f"{tail}_Profile",
            secret=False,
            title=t("cc_printers.prompt_clone_title"),
            required=True,
        )
        return True

    if act == "drv_edit_mode" and arg1 in ("everywhere", "db", "raw", "ppd"):
        sel_p = str(cc_panel.get_state("prn_selected", "") or "")
        kw = {"mode": arg1}
        if sel_p:
            kw["printer"] = sel_p
        if arg1 == "everywhere":
            kw["drv_id"] = "everywhere"
        elif arg1 == "raw":
            kw["drv_id"] = "raw"
        elif arg1 == "db" and _core().get_drv_edit_state()["drv_id"] in ("everywhere", "raw"):
            kw["drv_id"] = "drv:///sample.drv/generic.ppd"
        _core().update_drv_edit_state(**kw)
        return _refresh()

    if act == "drv_edit_search":
        de = _core().get_drv_edit_state()
        sel_p = str(cc_panel.get_state("prn_selected", "") or "")
        if sel_p and not de["printer"]:
            _core().update_drv_edit_state(printer=sel_p)
        return _open_flt("prn_drv_edit_in", 48, de["query"], lambda v: _core().update_drv_edit_state(query=v))

    if act == "drv_edit_clear":
        _core().update_drv_edit_state(query="")
        return _refresh()

    if act == "drv_edit_pick" and tail:
        sel_p = str(cc_panel.get_state("prn_selected", "") or "")
        kw = {"mode": "db", "drv_id": tail}
        if sel_p:
            kw["printer"] = sel_p
        _core().update_drv_edit_state(**kw)
        return _refresh()

    if act == "drv_edit_ppd":
        de = _core().get_drv_edit_state()
        sel_p = str(cc_panel.get_state("prn_selected", "") or "")
        if sel_p and not de["printer"]:
            _core().update_drv_edit_state(printer=sel_p)
        cc_panel.request_prompt(
            "cc_prn_edit_ppd",
            t("cc_printers.prompt_ppd_path"),
            de["ppd_path"] or "/usr/share/cups/model/",
            secret=False,
            title=t("cc_printers.prompt_ppd_title"),
            required=True,
            completion=("path_ext", _core()._PPD_EXTS),
        )
        return True

    if act == "drv_edit_apply" and tail:
        de = _core().get_drv_edit_state()
        if de["mode"] == "ppd":
            v_ok, v_msg = _disc().validate_ppd_file(de["ppd_path"])
            if not v_ok:
                _core()._set_fb(False, v_msg)
                return _refresh()
            cmd = ["lpadmin", "-p", tail, "-E", "-P", os.path.expanduser(de["ppd_path"])]
        else:
            model = "everywhere" if de["mode"] == "everywhere" else ("raw" if de["mode"] == "raw" else de["drv_id"])
            cmd = ["lpadmin", "-p", tail, "-E", "-m", model]
        _core()._exec_admin(
            cc_panel,
            cmd,
            t("cc_printers.fb_drv_updated", p0=tail),
            after_fn=lambda: _core().update_drv_edit_state(printer=""),
        )
        return True

    if act == "test_page" and tail:
        _core().print_test_page(cc_panel, tail)
        return True

    if act == "identify_prn" and tail:
        _core().identify_printer(cc_panel, tail)
        return True

    if act == "maint" and arg1 and arg2:
        cmd_map = {
            "clean": ("Clean all", t("cc_printers.maint_clean")),
            "selftest": ("PrintSelfTestPage", t("cc_printers.maint_selftest")),
            "levels": ("ReportLevels", t("cc_printers.maint_levels")),
        }
        if arg2 in cmd_map:
            c_str, lbl = cmd_map[arg2]
            _core().send_cups_maintenance_cmd(cc_panel, arg1, c_str, lbl)
        return True

    # --- Quick Print Job Composer ---
    if act == "print_file" and tail:
        cc_panel.set_state("prn_sec_qprint", True)
        qp = _core().get_quick_print_draft()
        cc_panel.request_prompt(
            f"cc_prn_file|{tail}",
            t("cc_printers.prompt_print_file", p0=tail),
            qp["file"] or "~/",
            secret=False,
            title=t("cc_printers.prompt_print_file_title"),
            required=True,
            completion=("path_ext", _core()._PRINTABLE_EXTS),
        )
        return True

    if act == "qp_pick_file":
        qp = _core().get_quick_print_draft()
        cc_panel.request_prompt(
            "cc_prn_qp_setfile",
            t("cc_printers.prompt_qp_file"),
            qp["file"] or "~/",
            secret=False,
            title=t("cc_printers.prompt_print_file_title"),
            required=True,
            completion=("path_ext", _core()._PRINTABLE_EXTS),
        )
        return True

    if act == "qp_copies" and tail:
        if tail.isdigit():
            _core().update_quick_print_draft(copies=int(tail))
            if shell:
                from panels.base import BasePanel
                if isinstance(getattr(shell, "panel", None), BasePanel):
                    shell.panel.request_throttled_refresh()
                else:
                    shell.refresh_view()
        return True

    if act == "qp_copies_edit":
        qp = _core().get_quick_print_draft()
        start_inline_input(
            "prn_qp_copies_in",
            shell=shell,
            trigger_uri=uri,
            initial_text=str(qp["copies"]),
            max_len=2,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda v: (v.strip().isdigit() and 1 <= int(v.strip()) <= 99, t("cc_printers.err_bad_copies")),
            on_commit=lambda v: _core().update_quick_print_draft(copies=int(v.strip())),
        )
        return True

    if act == "qp_pages_all":
        _core().update_quick_print_draft(pages="")
        return _refresh()

    if act == "qp_pages_edit":
        qp = _core().get_quick_print_draft()
        start_inline_input(
            "prn_qp_pages_in",
            shell=shell,
            trigger_uri=uri,
            initial_text=qp["pages"],
            max_len=24,
            strip_leading_hash=False,
            uppercase=False,
            validator=lambda v: (not v.strip() or bool(_core()._PAGE_RANGE_RE.match(v.strip())), t("cc_printers.err_bad_pages")),
            on_commit=lambda v: _core().update_quick_print_draft(pages=v.strip()),
        )
        return True

    if act == "qp_fit" and tail in ("0", "1"):
        _core().update_quick_print_draft(fit=(tail == "1"))
        return _refresh()

    if act == "qp_sides" and tail in ("default", "one-sided", "two-sided-long-edge", "two-sided-short-edge"):
        _core().update_quick_print_draft(sides=tail)
        return _refresh()

    if act == "qp_color" and tail in ("default", "color", "monochrome"):
        _core().update_quick_print_draft(color=tail)
        return _refresh()

    if act == "qp_submit" and tail:
        _core().submit_quick_print(cc_panel, tail)
        return True

    # --- Inline Edit Description / Location ---
    if act in ("edit_desc", "edit_loc") and tail:
        snap = _core().get_printers_snapshot()
        prn = next((p for p in snap["printers"] if p["name"] == tail), None)
        cur_val = (prn["description"] if act == "edit_desc" else prn["location"]) if prn else ""
        flag = "-D" if act == "edit_desc" else "-L"
        eid = f"prn_{act}_{tail}"

        def _commit_meta(val: str, p_name=tail, fl=flag):
            _core()._exec_admin(cc_panel, ["lpadmin", "-p", p_name, fl, val.strip()], t("cc_printers.fb_meta_saved", p0=p_name))

        start_inline_input(
            eid,
            shell=shell,
            trigger_uri=uri,
            initial_text=cur_val,
            max_len=64,
            strip_leading_hash=False,
            uppercase=False,
            on_commit=_commit_meta,
        )
        return True

    # --- Printing Preferences & PPD / IPP System + User Sync ---
    if act == "ppd_grp" and tail:
        cc_panel.set_state("prn_ppd_group", tail)
        return _refresh()

    if act == "cycle_opt" and arg1 and arg2:
        opt_k = arg2.strip()
        if not _core()._QUEUE_NAME_RE.match(arg1) or not _core()._OPT_TOKEN_RE.match(opt_k):
            _core()._set_fb(False, t("cc_printers.err_cmd_failed"))
            return _refresh()
        opts = _core().get_printer_options(arg1)
        meta = opts["ppd"].get(opt_k)
        if not meta or not meta.get("choices"):
            return True
        ch_list = meta["choices"]
        cur = meta.get("current", ch_list[0])
        nxt = ch_list[(ch_list.index(cur) + 1) % len(ch_list)] if cur in ch_list else ch_list[0]
        if not _core()._OPT_TOKEN_RE.match(nxt):
            _core()._set_fb(False, t("cc_printers.err_cmd_failed"))
            return _refresh()
        if probe.has_bin("lpoptions"):
            _core()._run_c(["lpoptions", "-p", arg1, "-o", f"{opt_k}={nxt}"], timeout=1.5)
        _core()._exec_admin(
            cc_panel,
            ["lpadmin", "-p", arg1, "-o", f"{opt_k}-default={nxt}"],
            t("cc_printers.fb_opt_saved", p0=opt_k, p1=_core().humanize_ppd_choice(opt_k, nxt)),
        )
        return True

    if act == "set_opt" and arg1 and "=" in arg2:
        opt_k, opt_v = arg2.split("=", 1)
        if not _core()._QUEUE_NAME_RE.match(arg1) or not _core()._OPT_TOKEN_RE.match(opt_k) or not _core()._OPT_TOKEN_RE.match(opt_v):
            _core()._set_fb(False, t("cc_printers.err_cmd_failed"))
            return _refresh()
        if opt_k in _core()._LPADMIN_DEFAULT_KEYS or not opt_k.startswith("job-"):
            if probe.has_bin("lpoptions"):
                _core()._run_c(["lpoptions", "-p", arg1, "-o", f"{opt_k}={opt_v}"], timeout=1.5)
            _core()._exec_admin(
                cc_panel,
                ["lpadmin", "-p", arg1, "-o", f"{opt_k}-default={opt_v}"],
                t("cc_printers.fb_opt_saved", p0=opt_k, p1=_core().humanize_ppd_choice(opt_k, opt_v)),
            )
        else:
            if not probe.has_bin("lpoptions"):
                _core()._set_fb(False, t("cc_printers.err_missing_bin", p0="lpoptions"))
                return _refresh()
            ok_o, out_o, err_o = _core()._run_c(["lpoptions", "-p", arg1, "-o", f"{opt_k}={opt_v}"], timeout=1.5)
            _core().invalidate_printers_cache()
            _core()._set_fb(
                ok_o,
                t("cc_printers.fb_opt_saved", p0=opt_k, p1=_core().humanize_ppd_choice(opt_k, opt_v))
                if ok_o
                else (err_o or out_o or t("cc_printers.err_cmd_failed"))[:110],
            )
            return _refresh()
        return True

    if act == "reset_user_opts" and tail:
        if not probe.has_bin("lpoptions"):
            _core()._set_fb(False, t("cc_printers.err_missing_bin", p0="lpoptions"))
            return _refresh()
        ok_r, out_r, err_r = _core()._run_c(["lpoptions", "-x", tail], timeout=1.5)
        _core().invalidate_printers_cache()
        _core()._set_fb(
            ok_r,
            t("cc_printers.fb_user_opts_reset", p0=tail)
            if ok_r
            else (err_r or out_r or t("cc_printers.err_cmd_failed"))[:110],
        )
        return _refresh()

    # --- Spooler Queue & Jobs ---
    if act == "q_mode" and tail in ("not-completed", "completed"):
        cc_panel.set_state("prn_q_mode", tail)
        return _refresh()

    if act == "q_cycle_prn":
        snap = _core().get_printers_snapshot()
        names = [""] + [p["name"] for p in snap["printers"]]
        cur_p = str(cc_panel.get_state("prn_q_printer", "") or "")
        nxt = names[(names.index(cur_p) + 1) % len(names)] if cur_p in names else ""
        cc_panel.set_state("prn_q_printer", nxt)
        return _refresh()

    if act == "q_filter_open":
        return _open_flt(
            "prn_q_filter_in",
            40,
            str(cc_panel.get_state("prn_q_filter", "") or ""),
            lambda v: cc_panel.set_state("prn_q_filter", v),
        )

    if act == "q_filter_clear":
        cc_panel.set_state("prn_q_filter", "")
        return _refresh()

    if act == "job_cancel" and tail:
        _core()._exec_admin(cc_panel, ["cancel", tail], t("cc_printers.fb_job_cancelled", p0=tail))
        return True

    if act == "job_hold" and arg1:
        mode = "hold" if arg2 == "1" else "resume"
        _core()._exec_admin(cc_panel, ["lp", "-i", arg1, "-H", mode], t("cc_printers.fb_job_hold_state", p0=arg1, p1=mode))
        return True

    if act == "job_reprint" and tail:
        _core()._exec_admin(cc_panel, ["lp", "-i", tail, "-H", "restart"], t("cc_printers.fb_job_reprinted", p0=tail))
        return True

    if act == "job_prio" and arg1 and arg2.isdigit():
        _core()._exec_admin(cc_panel, ["lp", "-i", arg1, "-q", arg2], t("cc_printers.fb_job_prio_set", p0=arg1, p1=arg2))
        return True

    if act == "job_move" and arg1 and arg2:
        _core()._exec_admin(cc_panel, ["lpmove", arg1, arg2], t("cc_printers.fb_job_moved", p0=arg1, p1=arg2))
        return True

    if act == "job_auth" and tail and _core()._JOB_ID_RE.match(tail):
        cc_panel.request_prompt(
            f"cc_prn_job_auth_user|{tail}",
            t("cc_printers.prompt_job_auth_user", p0=tail),
            _core()._current_user(),
            secret=False,
            title=t("cc_printers.prompt_job_auth_user_title", p0=tail),
            required=True,
        )
        return True

    if act == "smb_auth_prompt":
        d = _core().get_add_draft()
        host_hint = d["host"] or (urlparse(d["uri"]).hostname or "") if "://" in d["uri"] else d["host"]
        cc_panel.request_prompt(
            f"cc_prn_smb_user|{host_hint or 'SMB'}",
            t("cc_printers.prompt_smb_user", p0=host_hint or "SMB"),
            _core()._current_user(),
            secret=False,
            title=t("cc_printers.prompt_smb_user_title"),
            required=True,
        )
        return True

    if act == "purge_queue":
        _core().set_pending_confirm(cc_panel, "")
        cmd = ["cancel", "-a"] + ([tail] if tail and tail != "ALL" else [])
        _core()._exec_admin(cc_panel, cmd, t("cc_printers.fb_queue_purged", p0=tail or t("cc_printers.q_all")))
        return True

    # --- Discovery, Add Printer Wizard, Virtual PDF & Classes ---
    if act == "scan_all":
        _disc().trigger_tcp_lan_scan(cc_panel)
        _disc().trigger_device_discovery(cc_panel)
        return _refresh()

    if act == "wiz_open_manual":
        _core().update_add_draft(name="", desc="", loc="", proto="socket", tcp_port=9100, host="", uri="", drv_mode="auto", drv_query="", ppd_path="", selected_pkg="")
        cc_panel.set_state("prn_pkg_confirm", "")
        cc_panel.set_state("prn_wiz_target", "manual")
        _disc().warm_cups_drivers_async(cc_panel)
        return _refresh()

    if act == "wiz_close":
        cc_panel.set_state("prn_wiz_target", "")
        cc_panel.set_state("prn_pkg_confirm", "")
        return _refresh()

    if act == "open_installed" and tail:
        cc_panel.set_state("prn_selected", tail)
        cc_panel.set_state("prn_wiz_target", "")
        cc_panel.set_state("prn_pkg_confirm", "")
        cc_panel.set_subtab("devices")
        return _refresh()

    if act == "wiz_open_disc" and tail.isdigit():
        devs = _disc().get_discovered_devices()
        idx = int(tail)
        if 0 <= idx < len(devs):
            dv = devs[idx]
            nd = _disc().apply_disc_device_to_draft(dv)
            cc_panel.set_state("prn_disc_sel", nd["uri"])
            cc_panel.set_state("prn_wiz_target", f"disc:{idx}")
            cc_panel.set_state("prn_pkg_confirm", "")
            _disc().warm_cups_drivers_async(cc_panel)
            _disc().trigger_smart_probe_async(cc_panel, nd["host"], nd["uri"], dv["label"])
            _disc().trigger_auto_driver_search_async(cc_panel, dv["label"], nd["uri"])
        return _refresh()

    if act == "tcp_subnet_pick" and tail:
        ok_s, norm, _ = _disc().parse_tcp_scan_targets(tail)
        if ok_s:
            _disc().trigger_tcp_lan_scan(cc_panel, norm)
        return _refresh()

    if act == "tcp_subnet_custom":
        cur_sub = _disc().get_tcp_scan_state()["active_subnet"]

        def _val_sub(v: str):
            s = v.strip()
            if not s:
                return (True, "")
            ok_s, _, _ = _disc().parse_tcp_scan_targets(s)
            return (ok_s, "" if ok_s else t("cc_printers.err_bad_subnet"))

        def _commit_sub(v: str):
            s = v.strip()
            if not s:
                STATE.set("printers", "tcp_scan_subnet", _disc().get_local_ipv4_subnets()[0])
                return
            ok_s, norm, _ = _disc().parse_tcp_scan_targets(s)
            if ok_s:
                _disc().trigger_tcp_lan_scan(cc_panel, norm)

        start_inline_input(
            "prn_tcp_subnet_in",
            shell=shell,
            trigger_uri=uri,
            initial_text=cur_sub,
            max_len=36,
            strip_leading_hash=False,
            uppercase=False,
            validator=_val_sub,
            on_commit=_commit_sub,
        )
        return True

    if act == "wiz_open_tcp" and tail.isdigit():
        devs = _disc().get_tcp_scan_state()["devices"]
        idx = int(tail)
        if 0 <= idx < len(devs):
            dv = devs[idx]
            cc_panel.set_state("prn_tcp_sel_idx", idx)
            cc_panel.set_state("prn_wiz_target", f"tcp:{idx}")
            cc_panel.set_state("prn_pkg_confirm", "")
            nd = _disc().apply_tcp_device_to_draft(dv)
            cc_panel.set_state("prn_disc_sel", nd["uri"])
            _disc().warm_cups_drivers_async(cc_panel)
            _disc().trigger_smart_probe_async(cc_panel, dv["ip"], nd["uri"], dv["label"], dv.get("open_ports"))
            _disc().trigger_auto_driver_search_async(cc_panel, dv["label"], nd["uri"])
        return _refresh()

    if act == "create_pdf_queue":
        ppd_cand = "/usr/share/cups/model/CUPS-PDF_opt.ppd"
        model_args = ["-P", ppd_cand] if os.path.isfile(ppd_cand) else ["-m", "drv:///sample.drv/generic.ppd"]
        cmd = ["lpadmin", "-p", "Print_to_PDF", "-E", "-v", "cups-pdf:/", "-D", "Virtual PDF Printer"] + model_args
        _core()._exec_admin(cc_panel, cmd, t("cc_printers.fb_pdf_created"))
        return True

    if act == "draft_tcp_port" and tail.isdigit():
        p_val = int(tail)
        if p_val in (9100, 631, 515):
            d = _core().get_add_draft()
            kw = {"proto": "socket", "tcp_port": p_val}
            if d["host"]:
                kw["uri"] = _disc().build_tcp_uri(d["host"], p_val)
            if p_val == 631 and d["drv_mode"] == "db" and d["drv_id"] == "everywhere":
                kw["drv_mode"] = "everywhere"
            _core().update_add_draft(**kw)
            if kw.get("uri"):
                cc_panel.set_state("prn_disc_sel", kw["uri"])
        return _refresh()

    if act == "draft_proto" and tail:
        d = _core().get_add_draft()
        if tail == "socket":
            tcp_devs = _disc().get_tcp_scan_state()["devices"]
            if not d["host"] and tcp_devs:
                nd = _disc().apply_tcp_device_to_draft(tcp_devs[0])
                cc_panel.set_state("prn_disc_sel", nd["uri"])
            else:
                new_uri = _disc().build_tcp_uri(d["host"], d["tcp_port"]) if d["host"] else ""
                _core().update_add_draft(proto="socket", uri=new_uri)
        else:
            tpl = next((tp for pid, _l, tp in _core()._PROTO_PRESETS if pid == tail), "ipp://{host}:631/ipp/print")
            if tail == "usb":
                new_uri = "usb://"
            elif d["host"] and "{host}" in tpl:
                new_uri = tpl.format(host=d["host"])
            else:
                new_uri = ""
            _core().update_add_draft(proto=tail, uri=new_uri)
        return _refresh()

    if act == "draft_field" and tail in ("name", "desc", "loc", "host", "uri"):
        d = _core().get_add_draft()
        eid = f"prn_draft_{tail}"

        def _validate_draft(v: str, fld=tail):
            s = v.strip()
            if fld == "name":
                return (bool(_core()._QUEUE_NAME_RE.match(s)), t("cc_printers.err_bad_queue_name"))
            if fld == "uri":
                return (("://" in s and len(s) >= 6), t("cc_printers.err_bad_uri"))
            return (True, "")

        def _commit_draft(v: str, fld=tail):
            s = v.strip()
            cur = _core().get_add_draft()
            kw = {fld: s}
            if fld == "host" and s:
                if cur["proto"] == "socket":
                    kw["uri"] = _disc().build_tcp_uri(s, cur["tcp_port"])
                else:
                    tpl = next((tp for pid, _l, tp in _core()._PROTO_PRESETS if pid == cur["proto"]), "")
                    if "{host}" in tpl:
                        kw["uri"] = tpl.format(host=s)
            _core().update_add_draft(**kw)

        start_inline_input(
            eid,
            shell=shell,
            trigger_uri=uri,
            initial_text=d[tail],
            max_len=96 if tail == "uri" else 48,
            strip_leading_hash=False,
            uppercase=False,
            validator=_validate_draft,
            on_commit=_commit_draft,
        )
        return True

    if act == "draft_drv_mode" and tail in ("auto", "everywhere", "db", "raw", "ppd"):
        kw = {"drv_mode": tail, "selected_pkg": ""}
        if tail == "everywhere":
            kw["drv_id"] = "everywhere"
        elif tail == "raw":
            kw["drv_id"] = "raw"
        elif tail == "db":
            cur_d = _core().get_add_draft()
            _disc().warm_cups_drivers_async(cc_panel)
            _disc().trigger_auto_driver_search_async(cc_panel, cur_d.get("desc") or cur_d.get("name") or "", cur_d.get("uri", ""))
            cc_panel.set_state("prn_drv_scroll", 0)
            if cur_d["drv_id"] in ("everywhere", "raw", "auto", ""):
                kw["drv_id"] = "drv:///cupsfilters.drv/pwgrast.ppd"
        _core().update_add_draft(**kw)
        return _refresh()

    if act == "drv_search_open":
        d = _core().get_add_draft()

        def _on_q(v: str):
            cc_panel.set_state("prn_drv_scroll", 0)
            _core().update_add_draft(drv_query=v)

        return _open_flt("prn_drv_picker", 48, d["drv_query"], _on_q)

    if act == "drv_search_clear":
        cc_panel.set_state("prn_drv_scroll", 0)
        _core().update_add_draft(drv_query="")
        return _refresh()

    if act == "drv_box_scroll" and tail:
        cur_d = _core().get_add_draft()
        cur_sm = _disc().get_smart_probe(cur_d.get("host") or cur_d.get("uri") or "")
        cur_off = int(cc_panel.get_state("prn_drv_scroll", 0) or 0)
        delta = -1 if tail == "up" else (1 if tail == "down" else (-999 if tail == "top" else 999))
        cc_panel.set_state("prn_drv_scroll", max(0, cur_off + delta))
        _disc().get_auto_found_drivers(cc_panel, cur_d, cur_sm)
        return _refresh()

    if act == "drv_pkg_rescan":
        cur_d = _core().get_add_draft()
        cur_sm = _disc().get_smart_probe(cur_d.get("host") or cur_d.get("uri") or "")
        m_txt = str(cur_sm.get("model") or cur_d.get("desc") or cur_d.get("name") or "")
        _disc().trigger_auto_driver_search_async(cc_panel, m_txt, cur_d.get("uri", ""), force=True)
        return _refresh()

    if act == "pick_drv" and tail:
        if tail.startswith("ppd:"):
            _core().update_add_draft(drv_mode="ppd", ppd_path=tail[4:], selected_pkg="")
        else:
            _core().update_add_draft(drv_mode="db", drv_id=tail, selected_pkg="")
        return _refresh()

    if act == "pick_pkg_drv" and tail:
        cur_d = _core().get_add_draft()
        cur_sm = _disc().get_smart_probe(cur_d.get("host") or cur_d.get("uri") or "")
        _core()._CACHE.pop("drivers_list", None)
        drvs = _disc()._load_drivers_blocking()
        _core()._CACHE["drivers_list"] = (time.monotonic(), drvs)
        sig = _disc()._parse_printer_signature(f"{cur_sm.get('model') or cur_d.get('desc')} {tail}")
        matched = _disc()._match_vendor_driver(f"{tail} {sig['raw_model']}", drvs)
        if matched:
            _core().update_add_draft(drv_mode="db", drv_id=matched[0], selected_pkg=tail)
            _core()._set_fb(True, t("cc_printers.fb_pkg_drv_selected", p0=tail, p1=matched[1][:42]))
        else:
            _core().update_add_draft(drv_mode="auto", selected_pkg="")
            _core()._set_fb(True, t("cc_printers.fb_pkg_installed_ready", p0=tail))
        return _refresh()

    if act == "cancel_pkg_confirm":
        cc_panel.set_state("prn_pkg_confirm", "")
        return _refresh()

    if act in ("install_drv_pkg", "install_drv_pkg_force") and tail:
        pkg = tail.strip()
        cached_pkgs = {
            c.get("pkg")
            for _ts, pkg_list in _disc()._AUTO_DRV_PKG_CACHE.values()
            for c in (pkg_list or ())
            if isinstance(c, dict) and c.get("pkg")
        }
        if not _core()._PKG_NAME_RE.match(pkg) or pkg not in cached_pkgs:
            _core()._set_fb(False, t("cc_printers.err_invalid_pkg", p0=pkg))
            return _refresh()
        if act == "install_drv_pkg":
            auto_items = _disc().get_auto_found_drivers(cc_panel, _core().get_add_draft()).get("items", ())
            matched_it = next((c for c in auto_items if c.get("pkg") == pkg), None)
            if matched_it and int(matched_it.get("score", 0)) < 90 and cc_panel.get_state("prn_pkg_confirm", "") != pkg:
                cc_panel.set_state("prn_pkg_confirm", pkg)
                return _refresh()
        cc_panel.set_state("prn_pkg_confirm", "")
        pm = probe.get_preferred_pkg_manager()
        if not pm:
            _core()._set_fb(False, t("cc_printers.err_no_pkg_manager"))
            return _refresh()
        qpkg = shlex.quote(pkg)
        if pm in ("paru", "yay", "pikaur", "trizen"):
            cmd_line = f"{pm} -S --needed {qpkg}"
        elif pm == "pacman":
            cmd_line = f"sudo pacman -S --needed {qpkg}"
        elif pm in ("apt", "apt-get", "nala", "dnf", "dnf5", "yum", "zypper"):
            cmd_line = f"sudo {pm} install {qpkg}"
        elif pm == "apk":
            cmd_line = f"sudo apk add {qpkg}"
        elif pm == "xbps-query":
            cmd_line = f"sudo xbps-install -S {qpkg}"
        else:
            _core()._set_fb(False, t("cc_printers.err_no_pkg_manager"))
            return _refresh()
        if not probe.open_terminal(["bash", "-c", f"echo '=== NC-UI Printer Driver Install ({pm}) ==='; {cmd_line}; echo; read -p 'Press Enter to close...'"]):
            _core()._set_fb(False, t("cc_printers.err_no_terminal"))
            return _refresh()
        _disc()._watch_pkg_install_async(cc_panel, pkg)
        _core()._set_fb(True, t("cc_printers.fb_pkg_install_launched", p0=pkg, p1=pm))
        return _refresh()

    if act == "browse_ppd":
        d = _core().get_add_draft()
        cc_panel.request_prompt(
            "cc_prn_ppd",
            t("cc_printers.prompt_ppd_path"),
            d["ppd_path"] or "/usr/share/cups/model/",
            secret=False,
            title=t("cc_printers.prompt_ppd_title"),
            required=True,
            completion=("path_ext", _core()._PPD_EXTS),
        )
        return True

    if act == "draft_toggle" and tail in ("make_default", "shared"):
        d = _core().get_add_draft()
        _core().update_add_draft(**{tail: not d[tail]})
        return _refresh()

    if act == "draft_reset":
        _core().reset_add_draft()
        cc_panel.set_state("prn_disc_sel", "")
        return _refresh()

    if act == "probe_uri":
        target = tail or _core().get_add_draft()["uri"] or _core().get_add_draft()["host"]
        ok, msg = _disc().probe_printer_host(target)
        _core()._set_fb(ok, msg)
        return _refresh()

    if act in ("install_printer", "install_printer_test"):
        _core()._start_install_flow(cc_panel, print_test=(act == "install_printer_test"))
        return True

    # --- Classes / Pools (Master-Detail + Toggle Membership) ---
    if act == "class_new_prompt":
        snap = _core().get_printers_snapshot()
        if not snap["printers"]:
            _core()._set_fb(False, t("cc_printers.no_printers_hint"))
            return _refresh()
        first_p = snap["default"] or snap["printers"][0]["name"]
        cc_panel.request_prompt(
            f"cc_prn_cls|{first_p}",
            t("cc_printers.prompt_new_class", p0=first_p),
            "Office_Pool",
            secret=False,
            title=t("cc_printers.prompt_class_title"),
            required=True,
        )
        return True

    if act == "class_toggle_member" and arg1 and arg2:
        classes = _core().get_printer_classes()
        cl = next((c for c in classes if c["name"] == arg1), None)
        in_cls = bool(cl and arg2 in cl["members"])
        flag = "-r" if in_cls else "-c"
        _core()._exec_admin(cc_panel, ["lpadmin", "-p", arg2, flag, arg1], t("cc_printers.fb_class_updated", p0=arg1))
        return True

    if act == "class_delete" and tail:
        _core().set_pending_confirm(cc_panel, "")
        _core()._exec_admin(cc_panel, ["lpadmin", "-x", tail], t("cc_printers.fb_class_deleted", p0=tail))
        return True

    # --- Global CUPS Server Policies (`cupsctl`) ---
    if act == "cupsctl" and arg1 in ("share-printers", "remote-any", "remote-admin", "user-cancel-any", "debug-logging"):
        if not _core().get_cups_daemon_state()["scheduler_up"]:
            _core()._set_fb(False, t("cc_printers.err_spooler_down"))
            return _refresh()
        flag = f"--{arg1}" if arg2 == "1" else f"--no-{arg1}"
        _core()._exec_admin(cc_panel, ["cupsctl", flag], t("cc_printers.fb_cupsctl_saved", p0=flag))
        return True

    return False


def handle_cc_printers_prompt(cc_panel, action_id: str, text: str) -> bool:
    if not action_id.startswith("cc_prn_"):
        return False
    val = str(text or "").strip()

    if action_id == "cc_prn_sudo":
        cmds = _core()._PENDING_SUDO.get("cmds") or []
        ok_msg = str(_core()._PENDING_SUDO.get("ok_msg", t("common.ok")))
        after_fn = _core()._PENDING_SUDO.get("after_fn")
        _core()._PENDING_SUDO.clear()
        if not cmds or not val:
            return True
        ok = True
        out = ""
        for cmd in cmds:
            try:
                res = subprocess.run(
                    ["sudo", "-k", "-S", "-p", ""] + list(cmd),
                    input=f"{val}\n",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=12.0,
                )
                ok = res.returncode == 0
                out = (res.stdout or "").strip()
            except Exception as e:
                ok, out = False, str(e)
            if not ok:
                break
        _core().invalidate_printers_cache()
        if ok and callable(after_fn):
            after_fn()
        _core()._set_fb(ok, ok_msg if ok else (out or t("cc_printers.err_cmd_failed"))[:110])
        return True

    if action_id.startswith("cc_prn_job_auth_user|"):
        jid = action_id.split("|", 1)[1]
        if not _core()._JOB_ID_RE.match(jid) or not val:
            return True
        cc_panel.request_prompt(
            f"cc_prn_job_auth_pass|{jid}|{val}",
            t("cc_printers.prompt_job_auth_pass", p0=val, p1=jid),
            "",
            secret=True,
            title=t("cc_printers.prompt_job_auth_pass_title", p0=jid),
            required=True,
        )
        return True

    if action_id.startswith("cc_prn_job_auth_pass|"):
        parts = action_id.split("|", 2)
        if len(parts) != 3 or not val:
            return True
        jid, auth_user = parts[1], parts[2]
        if not _core()._JOB_ID_RE.match(jid):
            return True
        _core()._exec_admin(
            cc_panel,
            ["lp", "-i", jid, "-H", "resume", "-o", f"auth-info={auth_user},{val}"],
            t("cc_printers.fb_job_authenticated", p0=jid),
        )
        return True

    if action_id.startswith("cc_prn_smb_user|"):
        host_hint = action_id.split("|", 1)[1]
        if not val:
            return True
        cc_panel.request_prompt(
            f"cc_prn_smb_pass|{host_hint}|{val}",
            t("cc_printers.prompt_smb_pass", p0=val, p1=host_hint),
            "",
            secret=True,
            title=t("cc_printers.prompt_smb_pass_title"),
            required=True,
        )
        return True

    if action_id.startswith("cc_prn_smb_pass|"):
        parts = action_id.split("|", 2)
        if len(parts) != 3 or not val:
            return True
        host_hint, smb_user = parts[1], parts[2]
        d = _core().get_add_draft()
        if host_hint and host_hint != "SMB" and probe.has_bin("smbclient"):
            cache_dir = get_cache_dir()
            os.makedirs(cache_dir, exist_ok=True)
            auth_fd, auth_path = tempfile.mkstemp(prefix="ncui_smb_", dir=cache_dir)
            shares: list[str] = []
            try:
                os.fchmod(auth_fd, 0o600)
                with os.fdopen(auth_fd, "w", encoding="utf-8") as af:
                    if "\\" in smb_user:
                        dom, usr = smb_user.split("\\", 1)
                        af.write(f"username={usr}\npassword={val}\ndomain={dom}\n")
                    else:
                        af.write(f"username={smb_user}\npassword={val}\n")
                ok_l, out_l, err_l = _core()._run_c(["smbclient", "-L", host_hint, "-A", auth_path, "-g"], timeout=3.5)
                if ok_l and out_l:
                    for ln in out_l.splitlines():
                        cols = ln.split("|")
                        if len(cols) >= 2 and cols[0].strip().lower() == "printer":
                            shares.append(cols[1].strip())
            finally:
                try:
                    os.unlink(auth_path)
                except OSError:
                    pass
            if shares:
                first_u = f"smb://{smb_user}:{val}@{host_hint}/{shares[0]}"
                _core().update_add_draft(proto="smb", host=host_hint, uri=first_u, name=_disc()._sanitized_queue_name(shares[0], f"SMB_{host_hint}"))
                _core()._set_fb(True, t("cc_printers.fb_smb_shares_found", p0=host_hint, p1=len(shares)))
                return True
        if d["uri"].startswith("smb://"):
            raw_rest = d["uri"][len("smb://") :].split("@", 1)[-1]
            _core().update_add_draft(uri=f"smb://{smb_user}:{val}@{raw_rest}")
        else:
            _core().update_add_draft(proto="smb", uri=f"smb://{smb_user}:{val}@{host_hint}/Printer")
        _core()._set_fb(True, t("cc_printers.fb_smb_auth_saved", p0=host_hint))
        return True

    if action_id == "cc_prn_sudo_install":
        info = dict(_core()._PENDING_INSTALL_SUDO)
        _core()._PENDING_INSTALL_SUDO.clear()
        if not info or not val:
            return True
        _core()._start_install_flow(
            cc_panel,
            print_test=bool(info.get("print_test", False)),
            sudo_pass=val,
            draft_override=info.get("d"),
            smart_override=info.get("smart"),
        )
        return True

    if action_id.startswith("cc_prn_file|"):
        prn = action_id.split("|", 1)[1]
        exp = os.path.expanduser(val)
        if not os.path.isfile(exp):
            _core()._set_fb(False, t("cc_printers.err_file_not_found", p0=val))
            return True
        _core().update_quick_print_draft(file=val)
        if getattr(cc_panel, "active_subtab", "") == "queue":
            _core().submit_quick_print(cc_panel, prn, file_override=val)
        else:
            cc_panel.set_state("prn_sec_qprint", True)
            _core()._set_fb(True, t("cc_printers.fb_qp_file_ready", p0=os.path.basename(exp)))
        return True

    if action_id == "cc_prn_qp_setfile":
        exp = os.path.expanduser(val)
        if not os.path.isfile(exp):
            _core()._set_fb(False, t("cc_printers.err_file_not_found", p0=val))
            return True
        _core().update_quick_print_draft(file=val)
        _core()._set_fb(True, t("cc_printers.fb_qp_file_ready", p0=os.path.basename(exp)))
        return True

    if action_id.startswith("cc_prn_clone|"):
        src_prn = action_id.split("|", 1)[1]
        if not _core()._QUEUE_NAME_RE.match(val):
            _core()._set_fb(False, t("cc_printers.err_bad_queue_name"))
            return True
        snap = _core().get_printers_snapshot()
        src = next((p for p in snap["printers"] if p["name"] == src_prn), None)
        if not src or not src["uri"]:
            _core()._set_fb(False, t("cc_printers.err_cmd_failed"))
            return True
        src_ppd = f"/etc/cups/ppd/{src_prn}.ppd"
        drv_args = ["-P", src_ppd] if os.path.isfile(src_ppd) else ["-m", "everywhere"]
        cmd = ["lpadmin", "-p", val, "-E", "-v", src["uri"]] + drv_args
        if src["description"]:
            cmd += ["-D", f"{src['description']} (Clone)"]
        if src["location"]:
            cmd += ["-L", src["location"]]
        _core()._exec_admin(
            cc_panel,
            cmd,
            t("cc_printers.fb_prn_cloned", p0=src_prn, p1=val),
            after_fn=lambda: cc_panel.set_state("prn_selected", val),
        )
        return True

    if action_id == "cc_prn_ppd":
        v_ok, v_msg = _disc().validate_ppd_file(val)
        _core().update_add_draft(drv_mode="ppd", ppd_path=val)
        _core()._set_fb(v_ok, v_msg)
        return True

    if action_id == "cc_prn_edit_ppd":
        v_ok, v_msg = _disc().validate_ppd_file(val)
        _core().update_drv_edit_state(mode="ppd", ppd_path=val)
        _core()._set_fb(v_ok, v_msg)
        return True

    if action_id.startswith("cc_prn_cls|"):
        prn = action_id.split("|", 1)[1]
        if not _core()._QUEUE_NAME_RE.match(val):
            _core()._set_fb(False, t("cc_printers.err_bad_queue_name"))
            return True
        _core()._exec_admin(
            cc_panel,
            ["lpadmin", "-p", prn, "-c", val],
            t("cc_printers.fb_class_updated", p0=val),
            after_fn=lambda: cc_panel.set_state("prn_selected_class", val),
        )
        return True

    return False

def _core():
    import panels.cc_engine_printers as m
    return m

def _disc():
    import panels.cc_engine_printers_discovery as m
    return m
