#!/usr/bin/env python3
import py_compile, re, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ERRORS = []

def flatten_toml(d, prefix=""):
    out = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= flatten_toml(v, full)
        else:
            out.add(full)
    return out

en_keys = flatten_toml(tomllib.loads((ROOT / "locales/en.toml").read_text(encoding="utf-8")))
it_keys = flatten_toml(tomllib.loads((ROOT / "locales/it.toml").read_text(encoding="utf-8")))

t_re = re.compile(r"""\bt\(\s*["']([a-zA-Z0-9_.-]+)["']""")
raw_span_re = re.compile(r"""<span\s+color=['"]#[0-9a-fA-F]{3,8}['"]""")
ternary_lang_re = re.compile(r"""\bif\s+is_it\s+else\b""")

for py in sorted((ROOT / "panels").glob("cc_*.py")):
    try:
        py_compile.compile(str(py), doraise=True)
    except py_compile.PyCompileError as e:
        ERRORS.append(f"[SYNTAX] {e}")
        continue

    txt = py.read_text(encoding="utf-8")
    for m in t_re.finditer(txt):
        key = m.group(1)
        if key not in en_keys:
            ERRORS.append(f"[i18n EN missing] {py.name}: t('{key}')")
        if key not in it_keys:
            ERRORS.append(f"[i18n IT missing] {py.name}: t('{key}')")
    if raw_span_re.search(txt):
        ERRORS.append(f"[GOLDEN STANDARD] {py.name}: trovato <span color='#...'> hardcoded")
    if ternary_lang_re.search(txt):
        ERRORS.append(f"[i18n] {py.name}: trovato ternario 'if is_it else' proibito")

if ERRORS:
    print("\n".join(ERRORS[:40]))
    sys.exit(1)
print("OK: py_compile + i18n (en/it) + Golden Standard static checks passed.")
