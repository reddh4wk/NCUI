# -*- coding: utf-8 -*-
"""NC-UI Control Center — Updates (`panels/cc_updates.py`) [Single Backend / Dual View]"""

import os
import shlex
import time
from core import probe
from core.i18n import get_active_language
from ui.canvas import Canvas
from ui.kit import ActionBtn, Badge, Box, StateBadge, Txt

_STATE = {
    "scanning": False,
    "scanned": False,
    "last_ts": "",
    "official": [],
    "aur": [],
    "orphans": [],
    "cache_info": "unknown",
    "msg": "",
    "err": "",
}

_PKGLIST_SCRIPT = "/usr/local/sbin/nc-pkglist-save.sh"


def _feedback_cards() -> list:
    if not _STATE["msg"] and not _STATE["err"]:
        return []
    is_err = bool(_STATE["err"])
    b = Box("STATUS // ERROR" if is_err else "STATUS // OK", icon="\uf071" if is_err else "\uf058")
    b.add_row(f"  {Txt(_STATE['err'] if is_err else _STATE['msg'], role='warn' if is_err else 'vitality')}")
    return [b]


def _start_scan(cc_panel) -> None:
    if _STATE["scanning"]:
        return
    _STATE["scanning"] = True
    _STATE["err"] = ""
    _STATE["msg"] = "Scanning repositories (checkupdates / paru / pacman -Qdtq)..."
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()

    def _worker():
        off = []
        if probe.has_bin("checkupdates"):
            _, out, _ = probe.run_cmd_full(["checkupdates"], timeout=25.0)
            if out:
                off = [ln.strip() for ln in out.splitlines() if ln.strip()]

        aur = []
        if probe.has_bin("paru"):
            _, out, _ = probe.run_cmd_full(["paru", "-Qua"], timeout=25.0)
            if out:
                aur = [ln.strip() for ln in out.splitlines() if ln.strip()]

        orph = []
        if probe.has_bin("pacman"):
            _, out, _ = probe.run_cmd_full(["pacman", "-Qdtq"], timeout=8.0)
            if out:
                orph = [ln.strip() for ln in out.splitlines() if ln.strip()]

        cache_txt = "unknown"
        if os.path.isdir("/var/cache/pacman/pkg"):
            ok, out, _ = probe.run_cmd_full(["du", "-sh", "/var/cache/pacman/pkg"], timeout=6.0)
            if ok and out:
                cache_txt = out.split()[0]

        return off, aur, orph, cache_txt

    def _done(res):
        _STATE["scanning"] = False
        _STATE["scanned"] = True
        if isinstance(res, Exception):
            _STATE["err"] = str(res)
            return
        off, aur, orph, cache_txt = res
        _STATE["official"] = off
        _STATE["aur"] = aur
        _STATE["orphans"] = orph
        _STATE["cache_info"] = cache_txt
        _STATE["last_ts"] = time.strftime("%H:%M:%S")
        _STATE["msg"] = f"Scan complete at {_STATE['last_ts']}: {len(off)} Arch, {len(aur)} AUR, {len(orph)} orphans."
        if cc_panel and cc_panel.shell:
            cc_panel.shell.refresh_view()

    probe.run_async(_worker, on_done=_done)


def _add_package_lists(b: Box, cc_panel, is_it: bool) -> None:
    off, aur, orph = _STATE["official"], _STATE["aur"], _STATE["orphans"]

    def _fill_off(bx: Box):
        for ln in off[:40]:
            bx.add_row(f"  {Txt('•', role='structure')} {Txt(ln, role='focus')}")
        if not off:
            bx.add_row(f"  {Txt('System is up to date.' if not is_it else 'Sistema aggiornato.', role='vitality')}")

    def _fill_aur(bx: Box):
        for ln in aur[:40]:
            bx.add_row(f"  {Txt('•', role='structure')} {Txt(ln, role='intent')}")
        if not aur:
            bx.add_row(f"  {Txt('No pending AUR updates.' if not is_it else 'Nessun pacchetto AUR in coda.', role='schema')}")

    def _fill_orph(bx: Box):
        for ln in orph[:40]:
            bx.add_row(f"  {Txt('•', role='structure')} {Txt(ln, role='attention')}")
        if not orph:
            bx.add_row(f"  {Txt('No orphan packages.' if not is_it else 'Nessun pacchetto orfano.', role='vitality')}")

    lbl_off = f"Pacchetti Ufficiali Arch ({len(off)})" if is_it else f"Official Arch Updates ({len(off)})"
    lbl_aur = f"Pacchetti AUR / paru ({len(aur)})" if is_it else f"AUR Updates ({len(aur)})"
    lbl_orph = f"Pacchetti Orfani ({len(orph)})" if is_it else f"Orphan Packages ({len(orph)})"
    b.add_collapsible(cc_panel, "upd_off", lbl_off, _fill_off)
    b.add_collapsible(cc_panel, "upd_aur", lbl_aur, _fill_aur)
    b.add_collapsible(cc_panel, "upd_orph", lbl_orph, _fill_orph)


def _build_standard(cc_panel, canvas: Canvas, is_it: bool) -> list:
    cards = _feedback_cards()
    scan_lbl = "Scanning..." if _STATE["scanning"] else ("Avvia Scansione" if is_it else "Scan Updates")
    actions = [
        StateBadge(_STATE["scanning"], "SCANNING", "IDLE" if not _STATE["scanned"] else f"AT {_STATE['last_ts']}"),
        ActionBtn(scan_lbl, "cc_upd:scan", tone="apply", active=not _STATE["scanning"]),
        ActionBtn("Aggiorna (Term)" if is_it else "Upgrade (Term)", "cc_upd:upgrade_term", tone="select"),
    ]
    b = Box("Aggiornamenti Sistema — Arch Linux & AUR" if is_it else "System Updates — Arch Linux & AUR", icon="\uf187", actions=actions, elected=True)
    b.add_row(" ")
    if not _STATE["scanned"]:
        hint = "Scansione manuale on-demand (checkupdates, paru -Qua, orfani) per evitare richieste di rete automatiche." if is_it else "On-demand manual scan (checkupdates, paru -Qua, orphans) so the panel stays fast and non-blocking."
        b.add_row(f"  {Txt(hint, role='schema')}")
    else:
        b.add_kv("Arch Official", Txt(str(len(_STATE["official"])), role="vitality" if not _STATE["official"] else "attention"))
        b.add_kv("AUR (paru)", Txt(str(len(_STATE["aur"])), role="intent"))
        b.add_kv("Orphans (-Qdtq)", Txt(str(len(_STATE["orphans"])), role="focus"), right_markup=ActionBtn("Remove Orphans", "cc_upd:rm_orphans", tone="warn") if _STATE["orphans"] else "")
    b.add_kv(
        "Pacman Cache",
        Txt(_STATE["cache_info"], role="val"),
        right_markup=ActionBtn("Clean Cache (paccache -rk2)", "cc_upd:paccache", tone="warn"),
        requires=["paccache"],
    )
    b.add_kv(
        "Package Snapshot",
        Txt(_PKGLIST_SCRIPT, role="schema"),
        right_markup=ActionBtn("Save PkgList", "cc_upd:save_pkglist", tone="apply"),
        requires=[_PKGLIST_SCRIPT],
    )
    _add_package_lists(b, cc_panel, is_it)
    b.add_row(" ")
    cards.append(b)
    return cards


def build_cc_updates_cards(cc_panel, canvas: Canvas, active_sub: str = "packages") -> list:
    is_it = (get_active_language() == "it")
    return _build_standard(cc_panel, canvas, is_it)


def handle_cc_updates_link(cc_panel, uri: str) -> bool:
    if not uri.startswith("cc_upd:"):
        return False
    _STATE["msg"] = ""
    _STATE["err"] = ""
    cmd = uri.split(":", 1)[1]
    if cmd == "scan":
        _start_scan(cc_panel)
        return True
    if cmd == "upgrade_term":
        tool = "paru -Syu" if probe.has_bin("paru") else "sudo pacman -Syu"
        probe.open_terminal(["bash", "-c", f"echo '=== Nightcity Protected System Upgrade ==='; {tool}; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = f"Opened protected terminal session ({tool})."
    elif cmd == "paccache":
        probe.open_terminal(["bash", "-c", "sudo paccache -rk2; echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = "Launched paccache -rk2 in terminal."
    elif cmd == "rm_orphans":
        probe.open_terminal(["bash", "-c", "sudo pacman -Rns $(pacman -Qdtq); echo; read -p 'Press Enter to close...'"])
        _STATE["msg"] = "Launched orphan cleanup in terminal."
    elif cmd == "save_pkglist":
        if os.path.exists(_PKGLIST_SCRIPT):
            probe.open_terminal(["bash", "-c", f"sudo {shlex.quote(_PKGLIST_SCRIPT)}; echo; read -p 'Press Enter to close...'"])
            _STATE["msg"] = f"Launched {_PKGLIST_SCRIPT} in terminal."
        else:
            _STATE["err"] = f"Missing {_PKGLIST_SCRIPT}"
    else:
        return False
    if cc_panel and cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True
