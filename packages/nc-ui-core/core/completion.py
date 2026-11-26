#!/usr/bin/env python3
"""Finite catalogs, Tab-completion stepper, and Control Center search index."""

import json
import os
import re
import time
import zoneinfo

from core import probe
from core.config import _xdg_base
from core.i18n import _load_lang_file, get_active_language, t
from core.paths import find_package_file, find_panel_file

_CACHE: dict[str, tuple[float, object]] = {}
_CC_INDEX_CACHE: dict[str, list[dict]] = {}
_T_CALL_RE = re.compile(r"""\bt\(\s*["']([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)["']""")
_FMT_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")

_FALLBACK_XKB = [
    ("us", "English (US)"),
    ("it", "Italian"),
    ("gb", "English (UK)"),
    ("de", "German"),
    ("fr", "French"),
    ("es", "Spanish"),
    ("pt", "Portuguese"),
    ("ch", "German (Switzerland)"),
    ("jp", "Japanese"),
    ("ru", "Russian"),
]

_NOISE_KEY_SUBS = (
    ".fb_", "_msg", "_err", "_hint", "_warn", "status_", "not_installed", "error", "failed",
    "_badge", ".active", ".stopped", ".enabled", ".disabled", ".dir", ".proto", ".verdict",
    ".actions", ".edit", ".clear", ".save", ".reset", ".refresh", ".cancel", ".apply",
)


def _cached(key: str, ttl: float, loader):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and (now - hit[0]) < ttl:
        return hit[1]
    val = loader()
    _CACHE[key] = (now, val)
    return val


def rank_candidates(items: list, query: str, limit: int | None = 9) -> list[tuple[str, str]]:
    """Multi-token case-insensitive filter and deterministic match ranking."""
    norm = [
        (it, it, it.lower()) if isinstance(it, str)
        else (str(it[0]), str(it[1] if len(it) > 1 else it[0]), str(it[2] if len(it) > 2 else f"{it[0]} {it[1] if len(it) > 1 else ''}").lower())
        for it in items
    ]
    q = str(query or "").strip().lower()
    if not q:
        sliced = norm if limit is None else norm[: max(0, int(limit))]
        return [(val, lbl) for val, lbl, _blob in sliced]

    tokens = q.split()
    matched = []
    for val, lbl, blob in norm:
        if all(tok in blob for tok in tokens):
            val_l = val.lower()
            lbl_l = lbl.lower()
            score = (
                0 if val_l == q else 1,
                0 if val_l.startswith(q) else 1,
                0 if lbl_l.startswith(q) else 1,
                0 if q in val_l else 1,
                len(lbl_l),
                lbl_l,
            )
            matched.append((score, val, lbl))
    matched.sort(key=lambda row: row[0])
    out = [(val, lbl) for _, val, lbl in matched]
    return out if limit is None else out[: max(0, int(limit))]


def _format_path_hint(matches: list[str]) -> str:
    shown = [os.path.basename(m.rstrip("/")) + ("/" if m.endswith("/") else "") for m in matches[:6]]
    return "  ".join(shown) + (" …" if len(matches) > 6 else "")


def _scan_path_matches(cur_text: str, mode: str = "file", exts: tuple[str, ...] | None = None) -> list[str]:
    home = os.path.expanduser("~")
    raw = cur_text or "~/"
    use_tilde = raw.startswith("~")
    if raw == "~":
        parent, prefix = home, ""
    else:
        exp = os.path.expanduser(raw)
        if raw.endswith("/") or (os.path.isdir(exp) and not raw.rstrip("/").endswith(os.path.basename(exp.rstrip("/")))):
            parent, prefix = exp, ""
        else:
            parent, prefix = os.path.split(exp)
    if not parent:
        parent = "/"
    if not os.path.isdir(parent):
        return []

    try:
        with os.scandir(parent) as it:
            entries = sorted(it, key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return []

    def _collect(case_insensitive: bool) -> list[str]:
        res = []
        pref_l = prefix.lower()
        for ent in entries:
            if ent.name.startswith(".") and not prefix.startswith("."):
                continue
            if case_insensitive:
                if not ent.name.lower().startswith(pref_l):
                    continue
            elif not ent.name.startswith(prefix):
                continue
            is_d = ent.is_dir()
            if mode == "dir" and not is_d:
                continue
            if exts and not is_d and not ent.name.lower().endswith(exts):
                continue
            full = os.path.join(parent, ent.name) + ("/" if is_d else "")
            if use_tilde and (full == home or full.startswith(home + "/")):
                full = "~" + full[len(home):]
            res.append(full)
        return res

    matches = _collect(case_insensitive=False)
    if not matches and prefix:
        matches = _collect(case_insensitive=True)
    return matches


def _step_path_completion(
    cur_text: str,
    mode: str,
    exts: tuple[str, ...] | None,
    cached_candidates: list[str],
    cur_idx: int,
    reverse: bool,
) -> tuple[str, list[str], int, str]:
    if cached_candidates:
        step = -1 if reverse else 1
        new_idx = (cur_idx + step) % len(cached_candidates)
        return cached_candidates[new_idx], cached_candidates, new_idx, _format_path_hint(cached_candidates)

    matches = _scan_path_matches(cur_text, mode=mode, exts=exts)
    if not matches:
        return cur_text, [], -1, t("ui.search_no_results")
    if len(matches) == 1:
        return matches[0], [], -1, ""

    hint = _format_path_hint(matches)
    lcp = os.path.commonprefix(matches)
    if len(lcp) > len(cur_text):
        return lcp, matches, -1, hint

    new_idx = (len(matches) - 1) if reverse else 0
    return matches[new_idx], matches, new_idx, hint


def _catalog_path_bins() -> list[tuple[str, str, str]]:
    def _load() -> list[tuple[str, str, str]]:
        seen: set[str] = set()
        bins: list[str] = []
        for d in (os.environ.get("PATH") or "").split(os.pathsep):
            if not d or not os.path.isdir(d):
                continue
            try:
                with os.scandir(d) as it:
                    for ent in it:
                        nm = ent.name
                        if nm in seen or nm.startswith("."):
                            continue
                        try:
                            if ent.is_file() and os.access(ent.path, os.X_OK):
                                seen.add(nm)
                                bins.append(nm)
                        except OSError:
                            continue
            except OSError:
                continue
        bins.sort(key=str.lower)
        return [(b, b, b.lower()) for b in bins]

    return _cached("path_bins", 30.0, _load)


def _step_list_completion(
    cur_text: str,
    items: list[tuple],
    cached_candidates: list[str],
    cur_idx: int,
    reverse: bool,
) -> tuple[str, list[str], int, str]:
    if cached_candidates:
        step = -1 if reverse else 1
        new_idx = (cur_idx + step) % len(cached_candidates)
        hint = "  ".join(os.path.basename(c) or c for c in cached_candidates[:6]) + (" …" if len(cached_candidates) > 6 else "")
        return cached_candidates[new_idx], cached_candidates, new_idx, hint

    ranked = rank_candidates(items, cur_text, limit=30)
    vals = [v for v, _ in ranked]
    if not vals:
        return cur_text, [], -1, t("ui.search_no_results")
    if len(vals) == 1:
        return vals[0], [], -1, ""

    hint = "  ".join(lbl for _, lbl in ranked[:5]) + (" …" if len(ranked) > 5 else "")
    lcp = os.path.commonprefix(vals)
    if len(lcp) > len(cur_text) and lcp.lower().startswith(cur_text.strip().lower()):
        return lcp, vals, -1, hint

    new_idx = (len(vals) - 1) if reverse else 0
    return vals[new_idx], vals, new_idx, hint


def step_tab_completion(
    cur_text: str,
    provider,
    cached_candidates: list[str],
    cur_idx: int,
    reverse: bool = False,
) -> tuple[str, list[str], int, str]:
    """Unified Tab / Shift+Tab stepper for filesystem paths, shell commands, and finite catalogs."""
    if provider is None:
        return cur_text, [], -1, ""

    if provider == "path_file":
        return _step_path_completion(cur_text, "file", None, cached_candidates, cur_idx, reverse)
    if provider == "path_dir":
        return _step_path_completion(cur_text, "dir", None, cached_candidates, cur_idx, reverse)
    if isinstance(provider, tuple) and len(provider) >= 2 and provider[0] == "path_ext":
        exts = tuple(str(e).lower() for e in provider[1])
        return _step_path_completion(cur_text, "file", exts, cached_candidates, cur_idx, reverse)

    if provider == "shell_cmd":
        if cached_candidates:
            step = -1 if reverse else 1
            new_idx = (cur_idx + step) % len(cached_candidates)
            hint = "  ".join(cached_candidates[:5]) + (" …" if len(cached_candidates) > 5 else "")
            return cached_candidates[new_idx], cached_candidates, new_idx, hint
        if " " in cur_text:
            head, _, tail = cur_text.rpartition(" ")
            matches = _scan_path_matches(tail or "~/", mode="file", exts=None)
            full_cands = [f"{head} {m}" for m in matches]
            if not full_cands:
                return cur_text, [], -1, t("ui.search_no_results")
            if len(full_cands) == 1:
                return full_cands[0], [], -1, ""
            hint = _format_path_hint(matches)
            lcp = os.path.commonprefix(full_cands)
            if len(lcp) > len(cur_text):
                return lcp, full_cands, -1, hint
            new_idx = (len(full_cands) - 1) if reverse else 0
            return full_cands[new_idx], full_cands, new_idx, hint
        if cur_text.startswith(("/", "~", "./")):
            return _step_path_completion(cur_text, "file", None, cached_candidates, cur_idx, reverse)
        bins = _catalog_path_bins()
        ql = cur_text.strip().lower()
        pref_items = [it for it in bins if it[0].lower().startswith(ql)] if ql else bins[:40]
        return _step_list_completion(cur_text, pref_items or bins, [], cur_idx, reverse)

    if provider == "niri_windows":
        return _step_list_completion(cur_text, catalog_niri_windows(), cached_candidates, cur_idx, reverse)

    items = provider(cur_text) if callable(provider) else list(provider)
    return _step_list_completion(cur_text, items, cached_candidates, cur_idx, reverse)


# =====================================================================
# FINITE SYSTEM CATALOGS
# =====================================================================

def catalog_xkb_layouts(exclude: set[str] | None = None) -> list[tuple[str, str, str]]:
    def _load() -> list[tuple[str, str]]:
        out = []
        lst = "/usr/share/X11/xkb/rules/base.lst"
        if os.path.exists(lst):
            try:
                in_sec = False
                with open(lst, "r", encoding="utf-8", errors="ignore") as f:
                    for ln in f:
                        s = ln.rstrip("\n")
                        if s.startswith("! layout"):
                            in_sec = True
                            continue
                        if s.startswith("!") and in_sec:
                            break
                        if in_sec:
                            m = re.match(r"^\s+([a-zA-Z0-9_-]+)\s+(.+)$", s)
                            if m:
                                out.append((m.group(1).strip(), m.group(2).strip()))
            except OSError:
                pass
        return out or list(_FALLBACK_XKB)

    excl = {c.lower() for c in (exclude or set())}
    raw = _cached("xkb_layouts", 300.0, _load)
    return [
        (code, f"{code.upper()} \u2014 {name}", f"{code} {name}".lower())
        for code, name in raw
        if code.lower() not in excl
    ]


def catalog_timezones(exclude: str = "") -> list[tuple[str, str, str]]:
    def _load() -> list[str]:
        tzs = [
            z for z in zoneinfo.available_timezones()
            if ("/" in z and not z.startswith(("posix/", "right/", "SystemV/", "Etc/"))) or z == "UTC"
        ]
        return sorted(tzs)

    excl = str(exclude or "").strip()
    return [(z, z, z.lower()) for z in _cached("timezones", 300.0, _load) if z != excl]


def _scan_desktop_entries() -> list[dict]:
    def _load() -> list[dict]:
        xdg_data = _xdg_base("XDG_DATA_HOME", "~/.local/share")
        dirs = [
            os.path.join(xdg_data, "applications"),
            os.path.join(xdg_data, "flatpak", "exports", "share", "applications"),
            "/usr/local/share/applications",
            "/usr/share/applications",
            "/var/lib/flatpak/exports/share/applications",
        ]
        seen: set[str] = set()
        entries: list[dict] = []
        for d in dirs:
            if not os.path.isdir(d):
                continue
            try:
                files = sorted(os.listdir(d))
            except OSError:
                continue
            for fn in files:
                if not fn.endswith(".desktop") or fn in seen:
                    continue
                seen.add(fn)
                fp = os.path.join(d, fn)
                name = fn[:-8]
                mimes = ""
                wmclass = ""
                no_display = False
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        in_entry = False
                        for ln in f:
                            s = ln.strip()
                            if s.startswith("["):
                                if in_entry:
                                    break
                                in_entry = (s == "[Desktop Entry]")
                                continue
                            if not in_entry or "=" not in s:
                                continue
                            k, v = s.split("=", 1)
                            k = k.strip()
                            v = v.strip()
                            if k == "Name" and v:
                                name = v
                            elif k == "MimeType":
                                mimes = v.lower()
                            elif k == "StartupWMClass":
                                wmclass = v
                            elif k == "NoDisplay":
                                no_display = (v.lower() == "true")
                except OSError:
                    pass
                mimes_list: list[str] = []
                seen_m: set[str] = set()
                for part in mimes.split(";"):
                    pm = part.strip().lower()
                    if pm and "/" in pm and pm not in seen_m:
                        seen_m.add(pm)
                        mimes_list.append(pm)
                entries.append({
                    "id": fn,
                    "stem": fn[:-8],
                    "name": name,
                    "mimes": mimes,
                    "mimes_list": mimes_list,
                    "wmclass": wmclass,
                    "no_display": no_display,
                    "path": fp,
                })
        entries.sort(key=lambda e: (e["name"].lower(), e["id"].lower()))
        return entries

    return _cached("desktop_entries", 20.0, _load)


def invalidate_desktop_cache() -> None:
    _CACHE.pop("desktop_entries", None)


def catalog_desktop_apps(mime_hint: str = "") -> list[tuple[str, str, str]]:
    entries = _scan_desktop_entries()
    mh = str(mime_hint or "").strip().lower()
    major = mh.split("/", 1)[0] + "/" if "/" in mh else ""

    def _prio(e: dict) -> tuple[int, str]:
        ml = e.get("mimes_list") or ()
        ms = e["mimes"]
        if mh and (mh in ml or mh in ms):
            return (0, e["name"].lower())
        if major and major in ms:
            return (1, e["name"].lower())
        return (2, e["name"].lower())

    ordered = sorted(entries, key=_prio) if mh else entries
    out = []
    for e in ordered:
        lbl = e["stem"] if e["name"].lower() == e["stem"].lower() else f"{e['name']} ({e['stem']})"
        blob = f"{e['id']} {e['stem']} {e['name']} {e['mimes']}".lower()
        out.append((e["id"], lbl, blob))
    return out


def normalize_desktop_id(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    entries = _scan_desktop_entries()
    low = s.lower()
    for e in entries:
        if e["id"].lower() == low or e["stem"].lower() == low:
            return e["id"]
    return s if s.endswith(".desktop") else f"{s}.desktop"


def catalog_niri_windows() -> list[tuple[str, str, str]]:
    """List currently open Wayland/XWayland windows via niri IPC (cached 2s)."""
    def _load() -> list[tuple[str, str, str]]:
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str, str]] = []
        if probe.has_bin("niri"):
            ok, raw = probe.run_cmd(["niri", "msg", "-j", "windows"], timeout=0.9)
            if ok and raw:
                try:
                    for w in json.loads(raw):
                        aid = str(w.get("app_id") or "").strip()
                        title = str(w.get("title") or "").strip()
                        val = aid or title
                        if not val:
                            continue
                        key = (val.lower(), title.lower()[:32])
                        if key in seen:
                            continue
                        seen.add(key)
                        lbl = f"{val} — {title[:32]}" if title else val
                        out.append((val, lbl, f"{val} {title}".lower()))
                except Exception:
                    pass
        return out or catalog_window_app_ids()[:16]

    return _cached("niri_windows", 2.0, _load)


def catalog_window_app_ids(extra_apps: list[str] | None = None) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    items: list[tuple[str, str, str]] = []

    def _add(app_id: str, lbl: str = "", blob_extra: str = ""):
        aid = str(app_id or "").strip()
        if not aid or aid.lower() in seen:
            return
        seen.add(aid.lower())
        show = lbl or aid
        items.append((aid, show, f"{aid} {show} {blob_extra}".lower()))

    if probe.has_bin("niri"):
        ok, out = probe.run_cmd(["niri", "msg", "-j", "windows"], timeout=0.8)
        if ok and out:
            try:
                for w in json.loads(out):
                    aid = str(w.get("app_id") or "").strip()
                    title = str(w.get("title") or "").strip()
                    if aid:
                        _add(aid, f"{aid} \u2022 {title[:18]}" if title else aid, title)
            except Exception:
                pass

    for ex in (extra_apps or []):
        _add(ex)

    for e in _scan_desktop_entries():
        aid = e["wmclass"] or e["stem"]
        _add(aid, e["name"] if e["name"].lower() == aid.lower() else f"{aid} ({e['name']})", e["id"])

    return items


def catalog_mono_fonts() -> list[tuple[str, str, str]]:
    def _load() -> list[tuple[str, str, str]]:
        seen: set[str] = set()
        out: list[tuple[str, str, str]] = []
        presets = ["Courier", "JetBrainsMono Nerd Font", "FiraCode Nerd Font", "Iosevka"]
        names = list(presets)
        if probe.has_bin("fc-list"):
            ok, raw = probe.run_cmd(["fc-list", ":spacing=100", "family"], timeout=1.5)
            if ok and raw:
                for line in raw.splitlines():
                    for fam in line.split(","):
                        f_clean = fam.strip()
                        if f_clean:
                            names.append(f_clean)
        for nm in names:
            kl = nm.lower()
            if kl not in seen:
                seen.add(kl)
                out.append((nm, nm, kl))
        return out

    return _cached("mono_fonts", 60.0, _load)


def catalog_cursor_themes() -> list[tuple[str, str, str]]:
    def _load() -> list[tuple[str, str, str]]:
        xdg_data = _xdg_base("XDG_DATA_HOME", "~/.local/share")
        roots = [
            os.path.expanduser("~/.icons"),
            os.path.join(xdg_data, "icons"),
            "/usr/share/icons",
        ]
        found: list[str] = []
        seen: set[str] = set()
        if find_package_file("assets", "cursors", "ncui-cursor"):
            found.append("NCUI Cursor")
            seen.add("NCUI Cursor")
        for r in roots:
            if not os.path.isdir(r):
                continue
            try:
                names = sorted(os.listdir(r))
            except OSError:
                continue
            for nm in names:
                if nm in ("default", "hicolor", "NCUI-Cursor") or nm.startswith("NCUI-Cursor-") or nm in seen:
                    continue
                cdir = os.path.join(r, nm, "cursors")
                try:
                    if os.path.isdir(cdir) and os.listdir(cdir):
                        seen.add(nm)
                        found.append(nm)
                except OSError:
                    continue
        if "Adwaita" not in seen:
            found.append("Adwaita")
        return [(n, n, n.lower()) for n in found]

    return _cached("cursor_themes", 20.0, _load)


def catalog_net_ifaces(include_fw_tokens: bool = False) -> list[tuple[str, str, str]]:
    ifaces: list[str] = []
    if os.path.isdir("/sys/class/net"):
        try:
            ifaces = sorted(os.listdir("/sys/class/net"), key=lambda n: (1 if n == "lo" else 0, n))
        except OSError:
            ifaces = []
    if include_fw_tokens:
        out = [
            ("$LAN_NETWORK", "$LAN_NETWORK", "$lan_network lan subnet"),
            ("any", "any", "any *"),
        ]
        for n in ifaces:
            tok = f"if:{n}"
            out.append((tok, tok, f"{tok} {n}".lower()))
        return out
    return [(n, n, n.lower()) for n in ifaces]


# =====================================================================
# GLOBAL CONTROL CENTER SEARCH INDEX (TOML + PANEL SCANNER)
# =====================================================================

def _lookup_dict(d: dict, key: str) -> str:
    cur = d
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    return str(cur) if isinstance(cur, (str, int, float)) else ""


def _clean_index_label(raw: str) -> str:
    s = _FMT_PLACEHOLDER_RE.sub("", str(raw or ""))
    s = " ".join(s.split()).strip().rstrip(":").strip()
    return s


def _build_cc_search_index(lang: str) -> list[dict]:
    try:
        from panels.control_center import get_section_blueprints, get_tab_title
        blueprints = get_section_blueprints()
    except ImportError:
        get_tab_title = None
        try:
            from panels.control_center import SECTION_BLUEPRINTS as blueprints
        except ImportError:
            return []

    en_dict = _load_lang_file("en")
    act_dict = en_dict if lang == "en" else _load_lang_file(lang)
    entries: list[dict] = []

    for sec_id, bp in blueprints.items():
        icon = str(bp.get("icon", "\uf013"))
        tabs = list(bp.get("tabs", []))
        sec_key = "power_only" if (sec_id == "power" and "ups" not in tabs) else sec_id
        sec_act = _lookup_dict(act_dict, f"control_center.sections.{sec_key}") or _lookup_dict(en_dict, f"control_center.sections.{sec_key}") or sec_id.title()
        sec_en = _lookup_dict(en_dict, f"control_center.sections.{sec_key}") or sec_id.title()

        for sub_id in tabs:
            sub_act = (get_tab_title(sec_id, sub_id) if callable(get_tab_title) else "") or _lookup_dict(act_dict, f"control_center.tabs.{sec_id}.{sub_id}") or _lookup_dict(en_dict, f"control_center.tabs.{sec_id}.{sub_id}") or sub_id.title()
            sub_en = _lookup_dict(en_dict, f"control_center.tabs.{sec_id}.{sub_id}") or sub_id.title()
            entries.append({
                "section": sec_id,
                "subtab": sub_id,
                "icon": icon,
                "sec_title": sec_act,
                "sub_title": sub_act,
                "label": sub_act,
                "kind": "subtab",
                "blob": f"{sec_id} {sub_id} {sec_act} {sec_en} {sub_act} {sub_en}".lower(),
                "label_en": sub_en.lower(),
            })

            seen_lbls = {sub_act.lower()}
            py_path = find_panel_file(f"cc_{sec_id}_{sub_id}.py")
            if py_path and os.path.isfile(py_path):
                try:
                    src = open(py_path, "r", encoding="utf-8", errors="ignore").read()
                except OSError:
                    src = ""
                for t_key in _T_CALL_RE.findall(src):
                    if t_key.startswith(("ui.", "common.", "control_center.")):
                        continue
                    kl = t_key.lower()
                    if any(ns in kl for ns in _NOISE_KEY_SUBS):
                        continue
                    lbl_act = _clean_index_label(_lookup_dict(act_dict, t_key))
                    lbl_en = _clean_index_label(_lookup_dict(en_dict, t_key))
                    if not lbl_act:
                        continue
                    if len(lbl_act) < 4 or len(lbl_act) > 54:
                        continue
                    if lbl_act.lower() in seen_lbls:
                        continue
                    seen_lbls.add(lbl_act.lower())
                    key_tail = t_key.split(".", 1)[-1].replace("_", " ")
                    entries.append({
                        "section": sec_id,
                        "subtab": sub_id,
                        "icon": icon,
                        "sec_title": sec_act,
                        "sub_title": sub_act,
                        "label": lbl_act,
                        "kind": "setting",
                        "blob": f"{sec_id} {sub_id} {sec_act} {sec_en} {sub_act} {sub_en} {lbl_act} {lbl_en} {key_tail}".lower(),
                        "label_en": lbl_en.lower(),
                    })

    return entries


def search_control_center(query: str, limit: int = 10) -> list[dict]:
    """Search Control Center sections, subtabs, and indexed setting labels in real time."""
    lang = get_active_language()
    idx = _CC_INDEX_CACHE.get(lang)
    if idx is None:
        idx = _build_cc_search_index(lang)
        _CC_INDEX_CACHE[lang] = idx

    q = str(query or "").strip().lower()
    if not q:
        seen_sec: set[str] = set()
        defaults = []
        for it in idx:
            if it["kind"] == "subtab" and it["section"] not in seen_sec:
                seen_sec.add(it["section"])
                defaults.append(it)
                if len(defaults) >= limit:
                    break
        return defaults

    tokens = q.split()
    scored = []
    for pos, it in enumerate(idx):
        if not all(tok in it["blob"] for tok in tokens):
            continue
        lbl_l = it["label"].lower()
        sub_l = it["sub_title"].lower()
        sec_l = it["sec_title"].lower()
        kind_rank = 0 if it["kind"] == "subtab" else 1
        exact_rank = (
            0 if (lbl_l == q or sub_l == q or sec_l == q or it["section"] == q or it["subtab"] == q)
            else (1 if (lbl_l.startswith(q) or sub_l.startswith(q) or sec_l.startswith(q)) else 2)
        )
        direct_lbl_rank = 0 if (q in lbl_l or q in it.get("label_en", "")) else 1
        scored.append(((kind_rank, exact_rank, direct_lbl_rank, len(lbl_l), pos), it))

    scored.sort(key=lambda row: row[0])
    out = []
    seen_targets: set[tuple[str, str, str]] = set()
    for _, it in scored:
        sig = (it["section"], it["subtab"], it["label"].lower())
        if sig in seen_targets:
            continue
        seen_targets.add(sig)
        out.append(it)
        if len(out) >= limit:
            break
    return out
