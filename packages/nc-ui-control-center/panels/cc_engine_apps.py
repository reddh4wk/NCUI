#!/usr/bin/env python3
import json
import os
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from core import probe
from core.completion import _scan_desktop_entries, invalidate_desktop_cache, normalize_desktop_id
from core.config import CONFIG, _update_toml_key_in_section
from core.i18n import t
from core.state import STATE
from core.paths import resolve_script_path
from ui.kit import FeedbackBox, start_inline_input

NIRI_CONFIG_FILE = os.path.expanduser("~/.config/niri/config.kdl")

_DEFAULT_USER_MIMEAPPS = os.path.expanduser("~/.config/mimeapps.list")
USER_MIMEAPPS = _DEFAULT_USER_MIMEAPPS
SYS_MIMEAPPS_PATHS = [
    "/etc/xdg/mimeapps.list",
    "/usr/local/share/applications/mimeapps.list",
    "/usr/share/applications/mimeapps.list",
]
MIMEINFO_CACHE_PATHS = [
    os.path.expanduser("~/.local/share/applications/mimeinfo.cache"),
    "/usr/share/applications/mimeinfo.cache",
    "/var/lib/flatpak/exports/share/applications/mimeinfo.cache",
]
MIME_GLOBS2_PATH = "/usr/share/mime/globs2"
USER_MIME_DIR = os.path.expanduser("~/.local/share/mime")
USER_MIME_XML = os.path.join(USER_MIME_DIR, "packages", "ncui-custom-extensions.xml")
USER_MIME_GLOBS2 = os.path.join(USER_MIME_DIR, "globs2")
CUSTOM_EXT_MIME_PREFIX = "text/x-ncui-ext-"
_GENERIC_MIMES = {"text/plain", "application/octet-stream"}

DEFAULT_CATEGORIES = [
    ("browser", "\uf268", ["x-scheme-handler/http", "x-scheme-handler/https", "text/html", "application/xhtml+xml"]),
    ("mail", "\uf0e0", ["x-scheme-handler/mailto", "message/rfc822"]),
    ("files", "\uf07b", ["inode/directory"]),
    ("editor", "\uf15c", ["text/plain", "text/markdown", "application/json", "text/x-shellscript", "text/x-python"]),
    ("pdf", "\uf1c1", ["application/pdf"]),
    ("image", "\uf03e", ["image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml"]),
    ("video", "\uf03d", ["video/mp4", "video/x-matroska", "video/webm", "video/quicktime"]),
    ("audio", "\uf001", ["audio/mpeg", "audio/flac", "audio/ogg", "audio/wav", "audio/mp4"]),
    ("archive", "\uf1c6", ["application/zip", "application/x-tar", "application/gzip", "application/x-7z-compressed", "application/vnd.rar"]),
]

_CANONICAL_EXTS: dict[str, list[str]] = {
    "text/html": [".html", ".htm"],
    "application/xhtml+xml": [".xhtml", ".xht"],
    "message/rfc822": [".eml"],
    "text/plain": [".txt", ".log"],
    "text/markdown": [".md"],
    "application/json": [".json"],
    "text/x-shellscript": [".sh"],
    "text/x-python": [".py"],
    "application/pdf": [".pdf"],
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    "image/webp": [".webp"],
    "image/gif": [".gif"],
    "image/svg+xml": [".svg"],
    "video/mp4": [".mp4"],
    "video/x-matroska": [".mkv"],
    "video/webm": [".webm"],
    "video/quicktime": [".mov"],
    "audio/mpeg": [".mp3"],
    "audio/flac": [".flac"],
    "audio/ogg": [".ogg"],
    "audio/wav": [".wav"],
    "audio/mp4": [".m4a"],
    "application/zip": [".zip"],
    "application/x-tar": [".tar"],
    "application/gzip": [".gz"],
    "application/x-7z-compressed": [".7z"],
    "application/vnd.rar": [".rar"],
}

_EXT_GLOB_RE = re.compile(r"^\*(\.[a-zA-Z0-9][a-zA-Z0-9._+-]*)$")
_GLOBS_CACHE: tuple[tuple[str, str], tuple[dict[str, list[str]], dict[str, str]]] | None = None


def invalidate_mime_caches() -> None:
    global _GLOBS_CACHE
    _GLOBS_CACHE = None


def _set_apps_fb(ok: bool, msg: str) -> None:
    STATE.set("apps", "cc_fb", {"ok": bool(ok), "msg": str(msg), "ts": time.time()})


def _get_apps_fb() -> dict:
    fb = STATE.get("apps", "cc_fb", {})
    if isinstance(fb, dict) and fb.get("msg") and (time.time() - float(fb.get("ts", 0))) < 15.0:
        return fb
    return {}


def _feedback_cards() -> list:
    fb = _get_apps_fb()
    if not fb:
        return []
    return [FeedbackBox(t("cc_apps.apps_systemd_status"), bool(fb["ok"]), fb["msg"])]


# --- XDG MIME & Globs2 Engine ---

def _read_globs2_file(path: str, raw_by_mime: dict, ext_best: dict, weight_boost: int = 0) -> None:
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                parts = s.split(":", 2)
                if len(parts) != 3:
                    continue
                try:
                    w = int(parts[0]) + weight_boost
                except ValueError:
                    continue
                mime = parts[1].strip().lower()
                m = _EXT_GLOB_RE.match(parts[2].strip())
                if not mime or not m:
                    continue
                ext = m.group(1).lower()
                raw_by_mime.setdefault(mime, []).append((w, ext))
                cur_best = ext_best.get(ext)
                if cur_best is None or w > cur_best[0]:
                    ext_best[ext] = (w, mime)
    except OSError:
        pass


def load_mime_globs() -> tuple[dict[str, list[str]], dict[str, str]]:
    global _GLOBS_CACHE
    cache_id = (MIME_GLOBS2_PATH, USER_MIME_GLOBS2)
    if _GLOBS_CACHE is not None and _GLOBS_CACHE[0] == cache_id:
        return _GLOBS_CACHE[1]

    raw_by_mime: dict[str, list[tuple[int, str]]] = {}
    ext_best: dict[str, tuple[int, str]] = {}
    _read_globs2_file(MIME_GLOBS2_PATH, raw_by_mime, ext_best, weight_boost=0)
    _read_globs2_file(USER_MIME_GLOBS2, raw_by_mime, ext_best, weight_boost=1000)

    mime_to_exts: dict[str, list[str]] = {}
    for mime, pairs in raw_by_mime.items():
        canon = _CANONICAL_EXTS.get(mime, [])
        ordered = list(canon)
        seen = set(ordered)
        for _w, ext in sorted(pairs, key=lambda x: (-x[0], len(x[1]), x[1])):
            if ext not in seen:
                seen.add(ext)
                ordered.append(ext)
        mime_to_exts[mime] = ordered
    for mime, canon in _CANONICAL_EXTS.items():
        if mime not in mime_to_exts:
            mime_to_exts[mime] = list(canon)

    ext_to_mime = {ext: m for ext, (_w, m) in ext_best.items()}
    _GLOBS_CACHE = (cache_id, (mime_to_exts, ext_to_mime))
    return mime_to_exts, ext_to_mime


def mime_display_ext(mime: str, mime_to_exts: dict[str, list[str]]) -> str:
    if mime.startswith("x-scheme-handler/"):
        scheme = mime.split("/", 1)[1]
        return "mailto:" if scheme == "mailto" else f"{scheme}://"
    if mime == "inode/directory":
        return t("cc_apps.ext_folder")
    exts = mime_to_exts.get(mime) or []
    if exts:
        return ", ".join(exts[:2])
    return mime.split("/", 1)[-1]


def parse_extension_spec(raw: str) -> tuple[str, str, str]:
    s = str(raw or "").strip().lower()
    if not s:
        return "", "", ""
    if s.startswith("*."):
        s = s[1:]
    if "/" in s:
        if re.match(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$", s):
            return "mime", s, s
        return "", "", ""
    if s.endswith("://") or s.endswith(":"):
        scheme = s.split(":", 1)[0]
        if re.match(r"^[a-z][a-z0-9+.-]{0,31}$", scheme):
            return "scheme", f"{scheme}://", f"x-scheme-handler/{scheme}"
        return "", "", ""
    ext = s if s.startswith(".") else f".{s}"
    if not re.match(r"^\.[a-z0-9][a-z0-9._+-]{0,23}$", ext):
        return "", "", ""
    _m2e, e2m = load_mime_globs()
    existing = e2m.get(ext, "")
    if existing and existing not in _GENERIC_MIMES:
        return "ext_existing", ext, existing
    slug = re.sub(r"[^a-z0-9]+", "-", ext.lstrip(".")).strip("-")
    if not slug:
        return "", "", ""
    return "ext_custom", ext, f"{CUSTOM_EXT_MIME_PREFIX}{slug}"


def _load_custom_ext_map() -> dict[str, str]:
    res: dict[str, str] = {}
    if os.path.exists(USER_MIME_XML):
        try:
            root = ET.parse(USER_MIME_XML).getroot()
            for mt in root:
                if not mt.tag.endswith("mime-type"):
                    continue
                mtype = str(mt.attrib.get("type") or "").strip().lower()
                if not mtype:
                    continue
                for ch in mt:
                    if ch.tag.endswith("glob"):
                        pat = str(ch.attrib.get("pattern") or "").strip().lower()
                        if pat.startswith("*."):
                            res[mtype] = pat[1:]
                            break
        except Exception:
            pass
    return res


def _save_custom_ext_map(mapping: dict[str, str]) -> tuple[bool, str]:
    if not mapping:
        if os.path.exists(USER_MIME_XML):
            try:
                os.unlink(USER_MIME_XML)
            except OSError as e:
                return False, str(e)
    else:
        pkg_dir = os.path.dirname(os.path.abspath(USER_MIME_XML))
        os.makedirs(pkg_dir, exist_ok=True)
        ns = "http://www.freedesktop.org/standards/shared-mime-info"
        ET.register_namespace("", ns)
        root = ET.Element(f"{{{ns}}}mime-info")
        for mtype in sorted(mapping):
            ext = mapping[mtype]
            mt_el = ET.SubElement(root, f"{{{ns}}}mime-type", {"type": mtype})
            cmt = ET.SubElement(mt_el, f"{{{ns}}}comment")
            cmt.text = f"NC-UI custom {ext} association"
            ET.SubElement(mt_el, f"{{{ns}}}sub-class-of", {"type": "text/plain"})
            ET.SubElement(mt_el, f"{{{ns}}}glob", {"weight": "90", "pattern": f"*{ext}"})
        if hasattr(ET, "indent"):
            ET.indent(root, space="  ")
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile("wb", dir=pkg_dir, prefix=".ncui_mime_", delete=False) as tf:
                tmp_path = tf.name
                ET.ElementTree(root).write(tf, encoding="utf-8", xml_declaration=True)
            os.replace(tmp_path, USER_MIME_XML)
        except OSError as e:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return False, str(e)

    if os.path.isdir(USER_MIME_DIR) and probe.has_bin("update-mime-database"):
        ok, _o, err = probe.run_cmd_full(["update-mime-database", USER_MIME_DIR], timeout=5.0)
        if not ok:
            invalidate_mime_caches()
            return False, err or "update-mime-database failed"

    invalidate_mime_caches()
    if mapping:
        m2e, _e2m = load_mime_globs()
        missing = [m for m, ext in mapping.items() if ext not in m2e.get(m, [])]
        if missing:
            return False, t("cc_apps.fb_mime_verify_fail", p0=USER_MIME_GLOBS2, p1=", ".join(missing[:3]))
    return True, ""


def register_extension_mime(raw_spec: str) -> tuple[bool, str, str, str]:
    kind, disp_tok, mime = parse_extension_spec(raw_spec)
    if not kind:
        return False, "", "", t("cc_apps.fb_invalid_ext", p0=raw_spec)
    if kind == "ext_custom":
        cur = _load_custom_ext_map()
        cur[mime] = disp_tok
        ok, err = _save_custom_ext_map(cur)
        if not ok:
            return False, "", "", err
    return True, mime, disp_tok, ""


def assign_extension_to_app(raw_spec: str, desktop_id: str) -> tuple[bool, str]:
    desk = normalize_desktop_id(desktop_id)
    if not desk:
        return False, t("cc_apps.fb_no_desktop", p0=desktop_id)
    ok_reg, mime, disp_tok, err_reg = register_extension_mime(raw_spec)
    if not ok_reg:
        return False, err_reg
    ok_set, err_set = set_mime_default(desk, [mime], label=disp_tok)
    if not ok_set:
        return False, err_set
    apps_by_id = {e["id"]: e for e in _scan_desktop_entries()}
    return True, t("cc_apps.fb_ext_assigned", p0=disp_tok, p1=app_display_name(desk, apps_by_id), p2=mime)


def _parse_ini_sections(path: str) -> dict[str, list[tuple[str, str]]]:
    sections: dict[str, list[tuple[str, str]]] = {}
    if not path or not os.path.exists(path):
        return sections
    cur_sec = ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith(("#", ";")):
                    continue
                if s.startswith("[") and s.endswith("]"):
                    cur_sec = s[1:-1].strip()
                    sections.setdefault(cur_sec, [])
                    continue
                if cur_sec and "=" in s:
                    k, v = s.split("=", 1)
                    sections.setdefault(cur_sec, []).append((k.strip().lower(), v.strip()))
    except OSError:
        pass
    return sections


def _split_desktop_list(raw: str) -> list[str]:
    return [p.strip() for p in str(raw or "").split(";") if p.strip()]


def app_display_name(desktop_id: str, apps_by_id: dict[str, dict]) -> str:
    did = str(desktop_id or "").strip()
    if not did:
        return ""
    ent = apps_by_id.get(did)
    if ent and ent.get("name"):
        return str(ent["name"])
    return did[:-8] if did.endswith(".desktop") else did


def read_xdg_mime_state() -> dict:
    entries = _scan_desktop_entries()
    apps_by_id = {e["id"]: e for e in entries}
    apps_with_mimes = [e for e in entries if e.get("mimes_list")]

    user_sec = _parse_ini_sections(USER_MIMEAPPS)
    user_defaults: dict[str, str] = {}
    for k, v in user_sec.get("Default Applications", []):
        vals = _split_desktop_list(v)
        if vals:
            user_defaults[k] = vals[0]

    user_added: dict[str, list[str]] = {}
    for k, v in user_sec.get("Added Associations", []):
        user_added[k] = _split_desktop_list(v)

    user_removed: dict[str, set[str]] = {}
    for k, v in user_sec.get("Removed Associations", []):
        user_removed[k] = set(_split_desktop_list(v))

    sys_defaults: dict[str, list[str]] = {}
    for sp in SYS_MIMEAPPS_PATHS:
        sec = _parse_ini_sections(sp)
        for k, v in sec.get("Default Applications", []):
            if k not in sys_defaults:
                sys_defaults[k] = _split_desktop_list(v)

    cache_handlers: dict[str, list[str]] = {}
    for cp in MIMEINFO_CACHE_PATHS:
        sec = _parse_ini_sections(cp)
        for k, v in sec.get("MIME Cache", []):
            lst = cache_handlers.setdefault(k, [])
            for did in _split_desktop_list(v):
                if did not in lst:
                    lst.append(did)

    handlers_by_mime: dict[str, list[str]] = {}

    def _add_handler(mime: str, did: str) -> None:
        if did not in apps_by_id or did in user_removed.get(mime, set()):
            return
        lst = handlers_by_mime.setdefault(mime, [])
        if did not in lst:
            lst.append(did)

    for ent in apps_with_mimes:
        for m in ent["mimes_list"]:
            _add_handler(m, ent["id"])
    for m, lst in cache_handlers.items():
        for did in lst:
            _add_handler(m, did)
    for m, lst in user_added.items():
        for did in lst:
            _add_handler(m, did)

    for m, lst in handlers_by_mime.items():
        lst.sort(key=lambda d: (app_display_name(d, apps_by_id).lower(), d.lower()))

    def resolve(mime: str) -> tuple[str, str]:
        m = mime.lower()
        if m in user_defaults:
            cand_list = []
            for k, v in user_sec.get("Default Applications", []):
                if k == m:
                    cand_list = _split_desktop_list(v)
            for did in cand_list:
                if did in apps_by_id:
                    return did, "user"
            if user_defaults[m]:
                return user_defaults[m], "user"
        for did in sys_defaults.get(m, []):
            if did in apps_by_id:
                return did, "system"
        rem = user_removed.get(m, set())
        for did in user_added.get(m, []):
            if did in apps_by_id and did not in rem:
                return did, "system"
        for did in cache_handlers.get(m, []):
            if did in apps_by_id and did not in rem:
                return did, "system"
        cands = handlers_by_mime.get(m, [])
        if cands:
            return cands[0], "system"
        return "", "unset"

    return {
        "apps_by_id": apps_by_id,
        "apps_with_mimes": apps_with_mimes,
        "custom_mimes": _load_custom_ext_map(),
        "user_defaults": user_defaults,
        "handlers_by_mime": handlers_by_mime,
        "resolve": resolve,
        "override_count": len(user_defaults),
    }


def _rewrite_user_mimeapps(mutator) -> tuple[bool, str]:
    existing_lines: list[str] = []
    if os.path.exists(USER_MIMEAPPS):
        try:
            with open(USER_MIMEAPPS, "r", encoding="utf-8", errors="ignore") as f:
                existing_lines = f.read().splitlines()
        except OSError as e:
            return False, str(e)

    other_sections: list[tuple[str, list[str]]] = []
    defaults: dict[str, str] = {}
    cur_sec = ""
    cur_body: list[str] = []

    def _flush() -> None:
        if not cur_sec or cur_sec == "Default Applications":
            return
        while cur_body and not cur_body[-1].strip():
            cur_body.pop()
        other_sections.append((cur_sec, list(cur_body)))

    for ln in existing_lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            _flush()
            cur_sec = s[1:-1].strip()
            cur_body = []
        elif cur_sec == "Default Applications":
            if s and not s.startswith(("#", ";")) and "=" in s:
                k, v = s.split("=", 1)
                defaults[k.strip().lower()] = v.strip()
        elif cur_sec:
            cur_body.append(ln)
    _flush()

    mutator(defaults)

    out_lines: list[str] = ["[Default Applications]"]
    for k in sorted(defaults):
        if defaults[k]:
            out_lines.append(f"{k}={defaults[k]}")
    for sec_name, body in other_sections:
        out_lines.append("")
        out_lines.append(f"[{sec_name}]")
        out_lines.extend(body)

    parent = os.path.dirname(os.path.abspath(USER_MIMEAPPS))
    os.makedirs(parent, exist_ok=True)
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", dir=parent, prefix=".mimeapps_", delete=False, encoding="utf-8") as tf:
            tmp_path = tf.name
            tf.write("\n".join(out_lines) + "\n")
        os.replace(tmp_path, USER_MIMEAPPS)
        return True, ""
    except OSError as e:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False, str(e)


def set_mime_default(desktop_id: str, mimes: list[str], label: str = "") -> tuple[bool, str]:
    desk = normalize_desktop_id(desktop_id)
    if not desk:
        return False, t("cc_apps.fb_no_desktop", p0=desktop_id)
    clean_mimes: list[str] = []
    seen: set[str] = set()
    for m in mimes:
        pm = str(m or "").strip().lower()
        if pm and "/" in pm and pm not in seen:
            seen.add(pm)
            clean_mimes.append(pm)
    if not clean_mimes:
        return False, "No MIME types specified"

    if os.path.abspath(USER_MIMEAPPS) == os.path.abspath(_DEFAULT_USER_MIMEAPPS) and probe.has_bin("xdg-mime"):
        ok, _out, err = probe.run_cmd_full(["xdg-mime", "default", desk, *clean_mimes], timeout=3.0)
        if not ok:
            return False, err or "xdg-mime failed"
    else:
        ok, err = _rewrite_user_mimeapps(lambda d: d.update({m: desk for m in clean_mimes}))
        if not ok:
            return False, err

    sec = _parse_ini_sections(USER_MIMEAPPS)
    actual = {k: _split_desktop_list(v)[0] for k, v in sec.get("Default Applications", []) if _split_desktop_list(v)}
    mismatched = [m for m in clean_mimes if actual.get(m) != desk]
    invalidate_mime_caches()
    if mismatched:
        return False, t("cc_apps.fb_mime_verify_fail", p0=desk, p1=", ".join(mismatched[:3]))

    apps_by_id = {e["id"]: e for e in _scan_desktop_entries()}
    disp = app_display_name(desk, apps_by_id)
    if len(clean_mimes) == 1:
        return True, t("cc_apps.fb_mime_single", p0=clean_mimes[0], p1=disp)
    return True, t("cc_apps.fb_mime_set", p0=label or disp, p1=len(clean_mimes), p2=disp)


def reset_mime_defaults(mimes: list[str]) -> tuple[bool, str]:
    targets = {str(m or "").strip().lower() for m in mimes if str(m or "").strip()}
    if not targets:
        return False, "No MIME types specified"
    removed_n = [0]

    def _mut(d: dict[str, str]) -> None:
        for k in list(d.keys()):
            if k in targets:
                d.pop(k, None)
                removed_n[0] += 1

    ok, err = _rewrite_user_mimeapps(_mut)
    if not ok:
        invalidate_mime_caches()
        return False, err

    custom_targets = {m for m in targets if m.startswith(CUSTOM_EXT_MIME_PREFIX)}
    removed_exts: list[str] = []
    if custom_targets:
        cur_custom = _load_custom_ext_map()
        if any(m in cur_custom for m in custom_targets):
            for m in sorted(custom_targets):
                if m in cur_custom:
                    removed_exts.append(cur_custom.pop(m))
            ok_xml, err_xml = _save_custom_ext_map(cur_custom)
            if not ok_xml:
                invalidate_mime_caches()
                return False, err_xml
    invalidate_mime_caches()

    sec = _parse_ini_sections(USER_MIMEAPPS)
    remaining = {k for k, _v in sec.get("Default Applications", []) if k in targets}
    if remaining:
        return False, t("cc_apps.fb_mime_verify_fail", p0=USER_MIMEAPPS, p1=", ".join(sorted(remaining)[:3]))
    if removed_exts and removed_n[0] <= len(removed_exts):
        return True, t("cc_apps.fb_ext_removed", p0=", ".join(removed_exts))
    return True, t("cc_apps.fb_mime_reset", p0=removed_n[0])


def reset_all_mime_defaults() -> tuple[bool, str]:
    if os.path.exists(USER_MIMEAPPS):
        try:
            shutil.copy2(USER_MIMEAPPS, USER_MIMEAPPS + ".bak")
        except OSError as e:
            return False, str(e)
    ok, err = _rewrite_user_mimeapps(lambda d: d.clear())
    if ok and os.path.exists(USER_MIME_XML):
        _save_custom_ext_map({})
    invalidate_mime_caches()
    if not ok:
        return False, err
    sec = _parse_ini_sections(USER_MIMEAPPS)
    if sec.get("Default Applications"):
        return False, t("cc_apps.fb_mime_verify_fail", p0=USER_MIMEAPPS, p1="Default Applications")
    return True, t("cc_apps.fb_mime_reset_all")


# --- Niri & Systemd Helpers (Preserved for cc_apps_floating & cc_apps_autostart) ---

def get_niri_floating_rules() -> list[str]:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return []
    try:
        content = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read()
        pat = re.compile(r'window-rule\s*\{\s*match\s+app-id="([^"]+)"\s+open-floating\s+true\s*\}', re.MULTILINE)
        return sorted(set(m.group(1).replace("\\.", ".") for m in pat.finditer(content)))
    except Exception:
        return []


def get_niri_active_windows() -> list[dict]:
    ok, out = probe.run_cmd(["niri", "msg", "-j", "windows"], timeout=1.2)
    if not ok or not out:
        return []
    try:
        seen, res = set(), []
        for w in json.loads(out):
            aid = str(w.get("app_id") or "").strip()
            if aid and aid not in seen:
                seen.add(aid)
                res.append({"app_id": aid, "title": str(w.get("title") or "")})
        return sorted(res, key=lambda x: x["app_id"].lower())
    except Exception:
        return []


def toggle_niri_floating_rule(app_id: str) -> tuple[bool, str]:
    clean_id = str(app_id or "").strip()
    if not clean_id:
        return False, "Empty app-id"
    esc_app = clean_id.replace(".", "\\.")
    os.makedirs(os.path.dirname(NIRI_CONFIG_FILE), exist_ok=True)
    content = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read() if os.path.exists(NIRI_CONFIG_FILE) else ""
    rule_re = re.compile(r'\n*window-rule\s*\{\s*match\s+app-id="' + re.escape(esc_app) + r'"\s+open-floating\s+true\s*\}', re.MULTILINE)
    removed = bool(rule_re.search(content))
    if removed:
        new_content = rule_re.sub("", content)
    else:
        new_content = content.rstrip() + f'\n\nwindow-rule {{\n    match app-id="{esc_app}"\n    open-floating true\n}}\n'
    try:
        open(NIRI_CONFIG_FILE, "w", encoding="utf-8").write(new_content)
        return True, f"{'Removed' if removed else 'Added'} floating rule for {clean_id}"
    except Exception as e:
        return False, str(e)


def _read_niri_spawns() -> list[dict]:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return []
    items = []
    for idx, line in enumerate(open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read().splitlines()):
        m = re.match(r"^\s*(//\s*)?spawn-at-startup\s+(.+?)\s*$", line)
        if m:
            disabled = bool(m.group(1))
            raw_cmd = m.group(2).strip()
            items.append({"idx": idx, "disabled": disabled, "cmd": raw_cmd})
    return items


def _toggle_niri_spawn(line_idx: int, remove: bool = False) -> tuple[bool, str]:
    if not os.path.exists(NIRI_CONFIG_FILE):
        return False, "config.kdl missing"
    lines = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read().splitlines()
    if not (0 <= line_idx < len(lines)):
        return False, "Invalid spawn line"
    if remove:
        lines.pop(line_idx)
    else:
        ln = lines[line_idx]
        if ln.lstrip().startswith("//"):
            lines[line_idx] = re.sub(r"^(\s*)//\s*", r"\1", ln, count=1)
        else:
            lines[line_idx] = "// " + ln.lstrip()
    open(NIRI_CONFIG_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return True, "Updated spawn-at-startup in config.kdl"


def _add_niri_spawn(cmd_args: str) -> tuple[bool, str]:
    tokens = [tok.strip() for tok in cmd_args.strip().split() if tok.strip()]
    if not tokens:
        return False, "Empty command"
    quoted = " ".join(f'"{tok}"' for tok in tokens)
    txt = open(NIRI_CONFIG_FILE, "r", encoding="utf-8").read() if os.path.exists(NIRI_CONFIG_FILE) else ""
    open(NIRI_CONFIG_FILE, "w", encoding="utf-8").write(txt.rstrip() + f"\nspawn-at-startup {quoted}\n")
    return True, f"Added spawn-at-startup {quoted}"


def _read_systemd_services(user: bool = True) -> list[dict]:
    cmd = ["systemctl"] + (["--user"] if user else []) + ["list-units", "--type=service", "--no-pager", "--no-legend", "--plain"]
    ok, out = probe.run_cmd(cmd, timeout=1.5)
    services = []
    if ok and out:
        for line in out.splitlines()[:24]:
            parts = line.split(None, 4)
            if len(parts) >= 4:
                services.append({
                    "unit": parts[0],
                    "active": parts[2],
                    "sub": parts[3],
                    "desc": parts[4] if len(parts) > 4 else "",
                })
    return services


# --- URI & Prompt Dispatchers ---

def _cat_mimes(cid: str) -> list[str]:
    return next((mimes for c_id, _ic, mimes in DEFAULT_CATEGORIES if c_id == cid), [])


def handle_cc_apps_link(cc_panel, uri: str) -> bool:
    if uri.startswith("cc_flt:"):
        act = uri[len("cc_flt:"):]
        if act == "open_tui":
            probe.open_terminal(["bash", resolve_script_path("nc_util_floater.sh")])
            return True
        if act == "pick_open":
            cur_q = str(STATE.get("apps", "flt_query", "") or "")

            def _commit_raw_flt(txt: str) -> None:
                val = str(txt or "").strip()
                STATE.set("apps", "flt_query", "")
                if val and val not in get_niri_floating_rules():
                    ok, msg = toggle_niri_floating_rule(val)
                    _set_apps_fb(ok, msg)

            start_inline_input(
                entry_id="cc_flt_picker",
                shell=cc_panel.shell,
                trigger_uri=uri,
                initial_text=cur_q,
                max_len=40,
                is_picker=True,
                on_change=lambda txt: STATE.set("apps", "flt_query", str(txt or "")),
                on_cancel=lambda: STATE.set("apps", "flt_query", ""),
                on_commit=_commit_raw_flt,
            )
            return True
        if act == "pick_clear":
            STATE.set("apps", "flt_query", "")
            return True
        if act.startswith("add_app:"):
            aid = act.split(":", 1)[1]
            STATE.set("apps", "flt_query", "")
            if aid and aid not in get_niri_floating_rules():
                ok, msg = toggle_niri_floating_rule(aid)
                _set_apps_fb(ok, msg)
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True
        if act.startswith("toggle:"):
            ok, msg = toggle_niri_floating_rule(act.split(":", 1)[1])
            _set_apps_fb(ok, msg)
            if cc_panel.shell:
                cc_panel.shell.refresh_view()
            return True

    if not uri.startswith("cc_apps:"):
        return False
    act = uri[len("cc_apps:"):]

    if act == "refresh":
        invalidate_desktop_cache()
        invalidate_mime_caches()
    elif act.startswith("view:"):
        mode = act.split(":", 1)[1]
        if mode in ("category", "app", "type"):
            STATE.set("apps", "mime_view", mode)
            STATE.set("apps", "mime_other_open", "")
            STATE.set("apps", "mime_add_ext_target", "")
    elif act == "qfind_open":
        cur_q = str(STATE.get("apps", "mime_qfind", "") or "")

        def _on_qfind(txt: str) -> None:
            q = str(txt or "")
            STATE.set("apps", "mime_qfind", q)
            if q.strip():
                STATE.set("apps", "mime_view", "type")

        start_inline_input(
            entry_id="apps_qfind",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=48,
            on_change=_on_qfind,
            on_commit=lambda txt: _on_qfind(str(txt or "").strip()),
        )
        return True
    elif act == "qfind_clear":
        STATE.set("apps", "mime_qfind", "")
    elif act.startswith("cat_sel:"):
        STATE.set("apps", "mime_cat_sel", act.split(":", 1)[1])
        STATE.set("apps", "mime_other_open", "")
    elif act.startswith("cat_apply:"):
        _, cid, desk = act.split(":", 2)
        mimes = _cat_mimes(cid)
        ok, msg = set_mime_default(desk, mimes, label=t(f"cc_apps.cat_{cid}"))
        STATE.set("apps", "mime_other_open", "")
        _set_apps_fb(ok, msg)
    elif act.startswith("cat_reset:"):
        cid = act.split(":", 1)[1]
        ok, msg = reset_mime_defaults(_cat_mimes(cid))
        _set_apps_fb(ok, msg)
    elif act.startswith("app_sel:"):
        STATE.set("apps", "mime_app_sel", act.split(":", 1)[1])
        STATE.set("apps", "mime_app_subflt", "all")
        STATE.set("apps", "mime_other_open", "")
        STATE.set("apps", "mime_add_ext_target", "")
    elif act == "app_flt_open":
        cur_q = str(STATE.get("apps", "mime_app_flt", "") or "")
        start_inline_input(
            entry_id="apps_app_flt",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=40,
            on_change=lambda txt: STATE.set("apps", "mime_app_flt", str(txt or "")),
            on_commit=lambda txt: STATE.set("apps", "mime_app_flt", str(txt or "").strip()),
        )
        return True
    elif act == "app_flt_clear":
        STATE.set("apps", "mime_app_flt", "")
    elif act.startswith("app_subflt:"):
        sub = act.split(":", 1)[1]
        if sub in ("all", "owned", "other"):
            STATE.set("apps", "mime_app_subflt", sub)
    elif act.startswith("app_set_all:"):
        desk = act.split(":", 1)[1]
        st = read_xdg_mime_state()
        ent = st["apps_by_id"].get(desk)
        if ent and ent.get("mimes_list"):
            ok, msg = set_mime_default(desk, ent["mimes_list"], label=ent["name"])
            _set_apps_fb(ok, msg)
    elif act.startswith("app_clear:"):
        desk = act.split(":", 1)[1]
        st = read_xdg_mime_state()
        owned = [m for m, d in st["user_defaults"].items() if d == desk]
        if owned:
            ok, msg = reset_mime_defaults(owned)
            _set_apps_fb(ok, msg)
    elif act.startswith("add_ext_open:"):
        target = act.split(":", 1)[1]
        STATE.set("apps", "mime_add_ext_target", target)

        def _commit_ext(txt: str, tgt: str = target) -> None:
            STATE.set("apps", "mime_add_ext_target", "")
            raw = str(txt or "").strip()
            if not raw:
                return
            if tgt.startswith("app|"):
                desk = tgt.split("|", 1)[1]
                ok, msg = assign_extension_to_app(raw, desk)
                _set_apps_fb(ok, msg)
            else:
                ok_reg, mime, disp_tok, err = register_extension_mime(raw)
                if not ok_reg:
                    _set_apps_fb(False, err)
                    return
                STATE.set("apps", "mime_view", "type")
                STATE.set("apps", "mime_qfind", "")
                STATE.set("apps", "mime_type_sel", mime)
                STATE.set("apps", "mime_other_open", f"mime|{mime}")
                _set_apps_fb(True, t("cc_apps.fb_ext_registered", p0=disp_tok, p1=mime))

        start_inline_input(
            entry_id="apps_add_ext",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text="",
            max_len=36,
            on_cancel=lambda: STATE.set("apps", "mime_add_ext_target", ""),
            on_commit=_commit_ext,
        )
        return True
    elif act.startswith("register_ext:"):
        raw = act.split(":", 1)[1]
        ok_reg, mime, disp_tok, err = register_extension_mime(raw)
        if not ok_reg:
            _set_apps_fb(False, err)
        else:
            STATE.set("apps", "mime_add_ext_target", "")
            STATE.set("apps", "mime_view", "type")
            STATE.set("apps", "mime_qfind", "")
            STATE.set("apps", "mime_type_sel", mime)
            STATE.set("apps", "mime_other_open", f"mime|{mime}")
            _set_apps_fb(True, t("cc_apps.fb_ext_registered", p0=disp_tok, p1=mime))
    elif act.startswith("type_sel:"):
        STATE.set("apps", "mime_type_sel", act.split(":", 1)[1])
        STATE.set("apps", "mime_other_open", "")
    elif act == "type_kind_cycle":
        seq = ("ALL", "FILES", "LINKS", "OVERRIDES")
        cur = str(STATE.get("apps", "mime_type_kind", "ALL")).upper()
        nxt = seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]
        STATE.set("apps", "mime_type_kind", nxt)
    elif act.startswith("mime_set:"):
        _, mime, desktop = act.split(":", 2)
        STATE.set("apps", "mime_other_open", "")
        STATE.set("apps", "mime_other_query", "")
        ok, msg = set_mime_default(desktop, [mime])
        _set_apps_fb(ok, msg)
    elif act.startswith("mime_reset:"):
        mime = act.split(":", 1)[1]
        ok, msg = reset_mime_defaults([mime])
        _set_apps_fb(ok, msg)
    elif act.startswith("pick_other:"):
        target = act.split(":", 1)[1]
        STATE.set("apps", "mime_other_open", target)
        cur_q = str(STATE.get("apps", "mime_other_query", "") or "")

        def _commit_other(txt: str, tgt: str = target) -> None:
            STATE.set("apps", "mime_other_open", "")
            STATE.set("apps", "mime_other_query", "")
            desk = normalize_desktop_id(txt)
            if not desk:
                return
            if tgt.startswith("cat|"):
                cid = tgt.split("|", 1)[1]
                ok, msg = set_mime_default(desk, _cat_mimes(cid), label=t(f"cc_apps.cat_{cid}"))
            elif tgt.startswith("mime|"):
                mime = tgt.split("|", 1)[1]
                ok, msg = set_mime_default(desk, [mime])
            else:
                return
            _set_apps_fb(ok, msg)

        def _cancel_other() -> None:
            STATE.set("apps", "mime_other_open", "")
            STATE.set("apps", "mime_other_query", "")

        start_inline_input(
            entry_id="apps_pick_other",
            shell=cc_panel.shell,
            trigger_uri=uri,
            initial_text=cur_q,
            max_len=48,
            is_picker=True,
            on_change=lambda txt: STATE.set("apps", "mime_other_query", str(txt or "")),
            on_cancel=_cancel_other,
            on_commit=_commit_other,
        )
        return True
    elif act == "pick_other_clear":
        STATE.set("apps", "mime_other_query", "")
    elif act == "reset_all":
        ok, msg = reset_all_mime_defaults()
        _set_apps_fb(ok, msg)
    elif act.startswith("set_term:"):
        term = act.split(":", 1)[1]
        CONFIG.setdefault("general", {})["terminal"] = term
        _update_toml_key_in_section("general", "terminal", f'"{term}"')
        _set_apps_fb(True, t("cc_apps.default_nc_ui_terminal_set_to", p0=term))
    elif act.startswith("set_pkg_mgr:"):
        pm = act.split(":", 1)[1]
        CONFIG.setdefault("general", {})["pkg_manager"] = pm
        _update_toml_key_in_section("general", "pkg_manager", f'"{pm}"')
        _set_apps_fb(True, t("cc_apps.default_pkg_mgr_set_to", p0=pm))
    elif act == "prompt_spawn":
        cc_panel.request_prompt("cc_apps_spawn", t("cc_apps.command_args_to_spawn_at_startup_e"), "", title=t("cc_apps.spawn_at_startup"), required=True, completion="shell_cmd")
        return True
    elif act.startswith("toggle_spawn:"):
        ok, msg = _toggle_niri_spawn(int(act.split(":", 1)[1]), remove=False)
        _set_apps_fb(ok, msg)
    elif act.startswith("del_spawn:"):
        ok, msg = _toggle_niri_spawn(int(act.split(":", 1)[1]), remove=True)
        _set_apps_fb(ok, msg)
    elif act.startswith("svc_user:"):
        _, op, unit = act.split(":", 2)
        ok, _o, err = probe.run_cmd_full(["systemctl", "--user", op, unit], timeout=3.0)
        _set_apps_fb(ok, f"systemctl --user {op} {unit}" if ok else (err or "Service action failed"))

    if cc_panel.shell:
        cc_panel.shell.refresh_view()
    return True


def handle_cc_apps_prompt(cc_panel, action_id: str, text: str) -> bool:
    val = str(text or "").strip()
    if not val:
        return False
    if action_id == "cc_flt_add":
        ok, msg = toggle_niri_floating_rule(val)
        _set_apps_fb(ok, msg)
        return True
    if action_id == "cc_apps_spawn":
        ok, msg = _add_niri_spawn(val)
        _set_apps_fb(ok, msg)
        return True
    return False
