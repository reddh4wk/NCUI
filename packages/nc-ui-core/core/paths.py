#!/usr/bin/env python3
"""Centralized path resolution and PEP 420 multi-package bootstrapper for NC-UI."""

import os
import shutil
import subprocess
import sys

_PACKAGE_MARKERS = ("core", "ui", "panels", "bin", "config", "locales", "libexec", "utils", "assets")


def _find_monorepo_root(start_file: str | None = None) -> str:
    seeds = []
    if start_file:
        seeds.append(os.path.dirname(os.path.realpath(start_file)))
    seeds.append(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    for seed in seeds:
        cur = os.path.realpath(seed)
        while cur and cur != os.path.dirname(cur):
            if os.path.isdir(os.path.join(cur, "packages", "nc-ui-core")):
                return cur
            cur = os.path.dirname(cur)
    return ""


REPO_ROOT = _find_monorepo_root(__file__)

if REPO_ROOT:
    CORE_ROOT = os.path.join(REPO_ROOT, "packages", "nc-ui-core")
else:
    _here_pkg = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    if os.path.isdir(os.path.join(_here_pkg, "locales")):
        CORE_ROOT = _here_pkg
    elif os.path.isdir("/usr/share/ncui/locales"):
        CORE_ROOT = "/usr/share/ncui"
    else:
        CORE_ROOT = _here_pkg


def _is_package_root(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    return any(os.path.isdir(os.path.join(path, d)) for d in _PACKAGE_MARKERS)


def iter_package_roots() -> list[str]:
    """Return all active NC-UI package roots (monorepo packages/ or installed system dirs)."""
    roots: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        if not p or not os.path.isdir(p):
            return
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            roots.append(rp)

    _add(CORE_ROOT)
    if REPO_ROOT:
        pkgs_dir = os.path.join(REPO_ROOT, "packages")
        if os.path.isdir(pkgs_dir):
            for name in sorted(os.listdir(pkgs_dir)):
                p = os.path.join(pkgs_dir, name)
                if not os.path.isdir(p) or name.startswith("."):
                    continue
                if _is_package_root(p):
                    _add(p)
                else:
                    for sub in sorted(os.listdir(p)):
                        sp = os.path.join(p, sub)
                        if not sub.startswith(".") and _is_package_root(sp):
                            _add(sp)
        _add(REPO_ROOT)

    for sys_dir in ("/usr/lib/ncui", "/usr/share/ncui", "/usr/local/lib/nc-ui"):
        _add(sys_dir)
    return roots


def bootstrap_sys_path(start_file: str | None = None) -> str:
    """Ensure all NC-UI package roots are on sys.path (PEP 420 namespace packages)."""
    global REPO_ROOT, CORE_ROOT
    if not REPO_ROOT and start_file:
        found = _find_monorepo_root(start_file)
        if found:
            REPO_ROOT = found
            CORE_ROOT = os.path.join(REPO_ROOT, "packages", "nc-ui-core")

    roots = iter_package_roots()
    for p in reversed(roots):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    return CORE_ROOT


def find_package_file(*rel_parts: str) -> str:
    """Find the first existing file/dir matching rel_parts across NC-UI package roots and system dirs."""
    rel = os.path.join(*rel_parts)
    candidates: list[str] = [os.path.join(r, rel) for r in iter_package_roots()]
    candidates.extend([
        os.path.join("/usr/share/ncui", rel),
        os.path.join("/usr/lib/ncui", rel),
        os.path.join("/etc/ncui", rel),
    ])
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return ""


def find_all_package_files(*rel_parts: str) -> list[str]:
    """Return all unique existing realpaths matching rel_parts across package roots."""
    rel = os.path.join(*rel_parts)
    seen: set[str] = set()
    out: list[str] = []
    for root in iter_package_roots():
        cand = os.path.join(root, rel)
        if os.path.exists(cand):
            rp = os.path.realpath(cand)
            if rp not in seen:
                seen.add(rp)
                out.append(rp)
    return out


def iter_user_plugin_dirs() -> list[str]:
    """Return user plugin directories outside/alongside config in priority order."""
    return [
        os.path.expanduser("~/.local/share/nc-ui/plugins"),
        os.path.expanduser("~/.config/nc-ui/plugins"),
    ]


def find_panel_file(filename: str) -> str:
    """Locate a panel module file across user plugins, /usr/lib/ncui/panels, and packages/."""
    for d in iter_user_plugin_dirs():
        user_plugin = os.path.join(d, filename)
        if os.path.isfile(user_plugin):
            return user_plugin
    hit = find_package_file("panels", filename)
    return hit if (hit and os.path.isfile(hit)) else ""


def find_bin(name: str, fallback_aliases: tuple[str, ...] = ()) -> str:
    """Locate an executable binary or script across PATH, /usr/bin, /usr/local/bin, and packages/*/bin."""
    names = (name,) + tuple(a for a in fallback_aliases if a)
    for n in names:
        for d in ("/usr/bin", "/usr/local/bin"):
            cand = os.path.join(d, n)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        for root in iter_package_roots():
            for sub in ("bin", "utils"):
                cand = os.path.join(root, sub, n)
                if os.path.isfile(cand) and os.access(cand, os.X_OK):
                    return cand
        w = shutil.which(n)
        if w and os.path.isfile(w) and os.access(w, os.X_OK):
            return w
    return ""


def find_libexec_helper(name: str) -> str:
    """Locate a privileged root helper source or installed binary across sbin/libexec/packages."""
    for root in iter_package_roots():
        cand = os.path.join(root, "libexec", "ncui", name)
        if os.path.isfile(cand):
            return cand
    for d in ("/usr/lib/ncui/sbin", "/usr/lib/ncui/libexec"):
        cand = os.path.join(d, name)
        if os.path.isfile(cand):
            return cand
    return ""


def resolve_script_path(script_name: str = "nc_util_desktop.sh", fallback_alias: str = "") -> str:
    """Locate an NC-UI utility script or renamed binary across packages and system paths."""
    names = [script_name] + ([fallback_alias] if fallback_alias else [])
    for n in names:
        for root in iter_package_roots():
            for sub in ("utils", "bin", ""):
                cand = os.path.join(root, sub, n) if sub else os.path.join(root, n)
                if os.path.isfile(cand):
                    return cand
        for sys_cand in (
            f"/usr/share/ncui/utils/{n}",
            f"/usr/lib/ncui/utils/{n}",
            f"/usr/local/lib/nc-ui/utils/{n}",
            f"/usr/local/lib/nc-ui/{n}",
            f"/usr/bin/{n}",
            f"/usr/local/bin/{n}",
            shutil.which(n) or "",
        ):
            if sys_cand and os.path.isfile(sys_cand):
                return sys_cand
    return script_name


def run_desktop_cli(args: list) -> None:
    """Execute `nc_util_desktop.sh` detached in the background."""
    script = resolve_script_path()
    try:
        subprocess.Popen(
            ["bash", script] + [str(a) for a in args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass
