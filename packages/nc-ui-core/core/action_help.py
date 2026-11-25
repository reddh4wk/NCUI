#!/usr/bin/env python3
"""Action Help Registry & Lazy Catalog Loader for NC-UI Hover Descriptions."""

import fnmatch
import os
import tomllib
from core.i18n import get_active_language, t
from core.paths import CORE_ROOT, find_package_file

_HELP_DIR = find_package_file("locales", "help") or os.path.join(CORE_ROOT, "locales", "help")
_HELP_CACHE: dict[str, tuple[tuple[float, float], dict[str, dict], dict[str, list[tuple[str, dict]]]]] = {}


def _file_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return -1.0


def _read_toml(path: str) -> dict[str, dict]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def _compile_maps(entries: dict[str, dict]) -> tuple[dict[str, dict], dict[str, list[tuple[str, dict]]]]:
    exact: dict[str, dict] = {}
    patterns: dict[str, list[tuple[str, dict]]] = {}
    for key, raw_val in entries.items():
        if "*" in key or "?" in key:
            ns = key.split(":", 1)[0]
            patterns.setdefault(ns, []).append((key, raw_val))
        else:
            exact[key] = raw_val

    for ns, pat_list in patterns.items():
        pat_list.sort(
            key=lambda kv: (
                -sum(1 for seg in kv[0].split(":") if seg and "*" not in seg and "?" not in seg),
                -len(kv[0]),
                kv[0],
            )
        )
    return exact, patterns


def _collect_catalog_files(dir_path: str) -> list[str]:
    if not os.path.isdir(dir_path):
        return []
    try:
        return [
            os.path.join(dir_path, fn)
            for fn in sorted(os.listdir(dir_path))
            if fn.endswith(".toml") and os.path.isfile(os.path.join(dir_path, fn))
        ]
    except OSError:
        return []


def _load_catalog(lang: str) -> tuple[dict[str, dict], dict[str, list[tuple[str, dict]]]]:
    norm = str(lang or "en").strip().lower() or "en"
    en_root = os.path.join(_HELP_DIR, "en.toml")
    loc_root = os.path.join(_HELP_DIR, f"{norm}.toml") if norm != "en" else en_root
    en_parts = _collect_catalog_files(os.path.join(_HELP_DIR, "en"))
    loc_parts = _collect_catalog_files(os.path.join(_HELP_DIR, norm)) if norm != "en" else []

    all_en = [en_root] + en_parts
    all_loc = ([loc_root] + loc_parts) if norm != "en" else []
    sig = tuple((p, _file_mtime(p)) for p in (all_en + all_loc))

    cached = _HELP_CACHE.get(norm)
    if cached is not None and cached[0] == sig:
        return cached[1], cached[2]

    merged: dict[str, dict] = {}
    for p in all_en:
        merged.update(_read_toml(p))

    if norm != "en":
        for p in all_loc:
            if os.path.isfile(p):
                loc_data = _read_toml(p)
                for k, v in loc_data.items():
                    base = dict(merged.get(k, {}))
                    base.update(v)
                    merged[k] = base

    exact, patterns = _compile_maps(merged)
    _HELP_CACHE[norm] = (sig, exact, patterns)
    return exact, patterns


def invalidate_help_cache() -> None:
    """Clear compiled hover-help lookup caches."""
    _HELP_CACHE.clear()


def _resolve_raw(uri: str, exact: dict[str, dict], patterns: dict[str, list[tuple[str, dict]]]) -> dict | None:
    hit = exact.get(uri)
    if hit is not None:
        return hit
    ns = uri.split(":", 1)[0]
    for pat, entry in patterns.get(ns, ()):
        if fnmatch.fnmatchcase(uri, pat):
            return entry
    # Fallback for wildcards without namespace prefix
    for pat, entry in patterns.get("*", ()):
        if fnmatch.fnmatchcase(uri, pat):
            return entry
    return None


def lookup_action_help(uri: str, *, mode: str = "settings", lang: str | None = None) -> dict | None:
    """
    Look up localized hover documentation for a clickable URI.

    Returns ``{"uri", "title", "short", "verbose", "diagram", "diagram_focus"}``
    or ``None`` when no description is registered. In ``popup`` mode, ``verbose``
    and ``diagram`` are omitted so Waybar popups render compact cards only.
    """
    u = str(uri or "").strip()
    if not u or u.startswith("ncui:"):
        return None

    use_lang = lang or get_active_language()
    exact, patterns = _load_catalog(use_lang)
    raw = _resolve_raw(u, exact, patterns)
    if raw is None and ":" in u:
        prefix = u.rpartition(":")[0]
        if prefix:
            raw = _resolve_raw(prefix, exact, patterns)
    if raw is None:
        return None

    short_s = str(raw.get("short") or "").strip()
    if not short_s:
        return None

    title_s = str(raw.get("title") or "").strip() or t("ui.help_title_fallback")
    if mode == "popup":
        verbose_s = ""
        diagram_s = ""
        focus_s = ""
    else:
        verbose_s = str(raw.get("verbose") or "").strip()
        diagram_s = str(raw.get("diagram") or "").strip()
        focus_s = str(raw.get("diagram_focus") or "").strip()

    return {
        "uri": u,
        "title": title_s,
        "short": short_s,
        "verbose": verbose_s,
        "diagram": diagram_s,
        "diagram_focus": focus_s,
    }


def audit_uris(uris: list[str], lang: str = "en") -> list[str]:
    """Return URIs from ``uris`` that have no matching entry in ``help_<lang>.toml``."""
    return [u for u in uris if lookup_action_help(u, mode="settings", lang=lang) is None]
