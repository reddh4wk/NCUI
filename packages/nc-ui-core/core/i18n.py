#!/usr/bin/env python3
import os
from core.config import CONFIG, CONFIG_DIR, ROOT_DIR, _load_toml, _deep_merge, _update_toml_key_in_section
from core.paths import CORE_ROOT, find_all_package_files
from core.state import STATE

SYS_LOCALES_DIR = os.path.join(CORE_ROOT, "locales")
USER_LOCALES_DIR = os.path.join(CONFIG_DIR, "locales")

_ACTIVE_LANG: str | None = None
_DICT_CACHE = {}


def _candidate_dirs() -> list[str]:
    seen = set()
    out = []
    for d in (
        [SYS_LOCALES_DIR]
        + find_all_package_files("locales")
        + ["/usr/share/ncui/locales", USER_LOCALES_DIR]
    ):
        rp = os.path.realpath(d)
        if rp not in seen:
            seen.add(rp)
            out.append(d)
    return out


def _load_lang_file(lang: str) -> dict:
    base = {}
    for d in _candidate_dirs():
        p = os.path.join(d, f"{lang}.toml")
        if os.path.exists(p):
            loaded = _load_toml(p)
            if loaded:
                base = _deep_merge(base, loaded)
    return base


def _resolve_configured_language() -> str:
    st_lang = STATE.get("general", "language", None)
    if st_lang and isinstance(st_lang, str):
        return st_lang.strip().lower()
    return str(CONFIG.get("general", {}).get("language", "en")).strip().lower()


def reload_locale(lang: str = None) -> None:
    global _ACTIVE_LANG, _DICT_CACHE
    _ACTIVE_LANG = str(lang or _resolve_configured_language()).strip().lower()
    en_base = _load_lang_file("en")
    if _ACTIVE_LANG != "en":
        target = _load_lang_file(_ACTIVE_LANG)
        _DICT_CACHE = _deep_merge(en_base, target)
    else:
        _DICT_CACHE = en_base


def set_language(lang: str) -> None:
    clean_lang = str(lang or "en").strip().lower()
    if clean_lang not in list_available_languages():
        return
    STATE.set("general", "language", clean_lang)
    CONFIG.setdefault("general", {})["language"] = clean_lang
    _update_toml_key_in_section("general", "language", f'"{clean_lang}"')
    reload_locale(clean_lang)


def get_active_language() -> str:
    global _ACTIVE_LANG
    if _ACTIVE_LANG is None:
        _ACTIVE_LANG = _resolve_configured_language()
    return _ACTIVE_LANG


def list_available_languages() -> list[str]:
    langs = {"en", "it"}
    for d in _candidate_dirs():
        if os.path.exists(d):
            for fname in os.listdir(d):
                if fname.endswith(".toml"):
                    langs.add(fname[:-5])
    return sorted(langs)


def _resolve_key(key: str):
    if not _DICT_CACHE:
        reload_locale()
    cur = _DICT_CACHE
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def t(key: str, default: str = None, **kwargs) -> str:
    val = _resolve_key(key)
    if val is None or not isinstance(val, (str, int, float)):
        res = default if default is not None else key
    else:
        res = str(val)
    if kwargs:
        try:
            res = res.format(**kwargs)
        except Exception:
            pass
    return res


def t_list(key: str, default: list = None) -> list:
    val = _resolve_key(key)
    if isinstance(val, list):
        return val
    return list(default) if default is not None else []
