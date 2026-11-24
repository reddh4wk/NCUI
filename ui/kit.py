#!/usr/bin/env python3
import json
import math
import os
import re
from core.config import THEME, color_for_pct, get_hide_unavailable_settings
from core.i18n import t
from ui.canvas import Canvas, _ATOMIC_WIDGET_RE

DEFAULT_KEY_WIDTH = 20
COMPACT_KEY_WIDTH = 12
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")


def _valid_hex(val, fallback: str) -> str:
    if isinstance(val, str):
        s = val.strip()
        if _COLOR_RE.match(s) or s.lower().startswith("rgb"):
            return s
    return fallback


def _pair(asc: dict, key: str, dl: str, dr: str) -> tuple[str, str]:
    v = asc.get(key) if isinstance(asc, dict) else None
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return (str(v[0]) if v[0] is not None else dl, str(v[1]) if v[1] is not None else dr)
    return dl, dr


# =====================================================================
# SORGENTE SEMANTICA UNICA DEI COLORI (7 Assi Semantici — Golden Standard §4)
# =====================================================================
# MEMENTO: Nessun pannello può scrivere `<span color='#...'>` manuali.
# `PALETTE` viene aggiornato in-place ad ogni refresh di `get_semantic_palette()`
# così anche i moduli che mantengono un riferimento diretto a `PALETTE` ricevono
# istantaneamente le modifiche tema senza riavvio.
_PALETTE_CACHE: dict = {"key": None, "val": None}
PALETTE: dict[str, str] = {}

_LEADING_PILL_DOTS_RE = re.compile(r"^[\u25cf\u25cb\u25c6\u25c7\u25a0\u25a1]\s*")
_LEADING_CYCLE_RE = re.compile(r"^\u21bb\s*")
_LEADING_CHEV_RE = re.compile(r"^[\u25b8\u25be\u25b6\u25bc]\s*")
_NEUTRAL_FILTER_TOKENS = frozenset({"ALL", "*", "ANY", "TUTTI", "TUTTE", "NONE"})


def get_semantic_palette() -> dict[str, str]:
    c = THEME.get("colors", {})
    th = c.get("thresholds", {})
    cache_key = (
        tuple((k, v) for k, v in c.items() if not isinstance(v, (dict, list))),
        tuple(th.items()) if isinstance(th, dict) else (),
    )
    if _PALETTE_CACHE["key"] == cache_key and _PALETTE_CACHE["val"] is not None:
        return _PALETTE_CACHE["val"]
    vh = _valid_hex
    s = vh(c.get("structure"), "#3e465c")
    sbg = vh(c.get("structure_bg"), "#141824")
    ina = vh(c.get("inactive", c.get("muted")), "#596278")
    sch = vh(c.get("schema", c.get("secondary")), "#8b9bb8")
    foc = vh(c.get("focus", c.get("primary")), "#ffffff")
    fval = vh(c.get("focus_val"), "#dce6f7")
    itn = vh(c.get("intent", c.get("accent")), "#00f0ff")
    itns = vh(c.get("intent_soft"), "#3ec8d6")
    itnbg = vh(c.get("intent_bg"), "#002b36")
    vit = vh(c.get("vitality", th.get("normal")), "#00f57a")
    vits = vh(c.get("vitality_soft"), "#3ebd7c")
    vitbg = vh(c.get("vitality_bg"), "#002915")
    att = vh(c.get("attention", th.get("warning")), "#ffe619")
    atts = vh(c.get("attention_soft"), "#c7b438")
    attbg = vh(c.get("attention_bg"), "#332b00")
    itr = vh(c.get("interrupt", th.get("alert")), "#ff2a9d")
    itrr = vh(c.get("interrupt_rest"), "#c45b8c")
    itrbg = vh(c.get("interrupt_bg"), "#36081e")
    flt = vh(c.get("fault", th.get("critical")), "#ff2442")
    flts = vh(c.get("fault_soft"), "#c9495b")
    fltbg = vh(c.get("fault_bg"), "#38060e")
    pal = {
        "structure": s, "structure_bg": sbg, "inactive": ina, "muted": ina,
        "schema": sch, "dim": sch,
        "focus": foc, "pri": foc, "focus_val": fval, "val": fval,
        "intent": itn, "sel_fg": itn, "intent_soft": itns, "sel_soft": itns, "intent_bg": itnbg, "sel_bg": itnbg,
        "vitality": vit, "ok_fg": vit, "vitality_soft": vits, "ok_soft": vits, "vitality_bg": vitbg, "ok_bg": vitbg,
        "attention": att, "dirty_fg": att, "attention_soft": atts, "dirty_soft": atts, "attention_bg": attbg, "dirty_bg": attbg,
        "interrupt": itr, "interrupt_rest": itrr, "crit_soft": itrr, "interrupt_bg": itrbg,
        "fault": flt, "crit_fg": flt, "fault_soft": flts, "fault_bg": fltbg, "crit_bg": fltbg,
    }
    PALETTE.clear()
    PALETTE.update(pal)
    _PALETTE_CACHE["key"] = cache_key
    _PALETTE_CACHE["val"] = PALETTE
    return PALETTE


get_semantic_palette()


# =====================================================================
# COSTRUTTORI SEMANTICI CENTRALIZZATI (Unica API per tutti i pannelli)
# =====================================================================
def Txt(text: str, role: str = "focus", bg: bool = False) -> str:
    """
    Formatta una stringa di sola lettura secondo i 7 Assi Semantici (Golden Standard §4.1):
      - 'focus' / 'pri'      : Bianco Puro (titoli, identificatori primari in col. 0)
      - 'focus_val' / 'val'  : Bianco Ghiaccio (TUTTI i valori di lettura neutri)
      - 'schema' / 'dim'     : Grigio Ardesia (chiavi, header, metadati, contatori)
      - 'inactive' / 'muted' : Grafite Spento (placeholder, righe spente, '-', 'N/D', hint)
      - 'structure'          : Grafite Strutturale (separatori '|')
      - 'intent' / 'sel'     : Ciano Elettrico (RISERVATO a selezione/focus/URL)
      - 'vitality' / 'ok'    : Verde Smeraldo (telemetria nominale <50%, nuova versione)
      - 'attention' / 'warn' : Giallo Cyber (warning dinamici, 50-75%, modifiche staged)
      - 'interrupt_rest'     : Rosa-Magenta a riposo
      - 'interrupt'          : Magenta pieno (75-90% carico)
      - 'fault' / 'crit'     : Cremisi (errore critico, >=90% carico)

    MEMENTO DI GUARDIA:
      1) Codici `#hex` raw passati in `role` vengono ignorati (ricadono su `focus`).
         Per i soli campioni colore letterali (§4.2) usare `SwatchTxt()` o `ColorSwatch()`.
      2) `bg=True` è bloccato sui ruoli strutturali/informativi (`focus`, `val`, `schema`,
         `inactive`, `structure`); su `intent` è consentito SOLO per la cella calendario `[GG]`.
    """
    p = get_semantic_palette()
    s_txt = str(text)
    allow_intent_bg = bg and s_txt.strip().startswith("[") and len(s_txt.strip()) <= 6
    role_map = {
        "focus":          (p["focus"], ""),
        "pri":            (p["focus"], ""),
        "focus_val":      (p["focus_val"], ""),
        "val":            (p["focus_val"], ""),
        "schema":         (p["schema"], ""),
        "dim":            (p["schema"], ""),
        "structure":      (p["structure"], ""),
        "inactive":       (p["inactive"], ""),
        "muted":          (p["inactive"], ""),
        "intent":         (p["intent"], p["intent_bg"] if allow_intent_bg else ""),
        "sel":            (p["intent"], p["intent_bg"] if allow_intent_bg else ""),
        "intent_soft":    (p["intent_soft"], ""),
        "vitality":       (p["vitality"], p["vitality_bg"] if bg else ""),
        "ok":             (p["vitality"], p["vitality_bg"] if bg else ""),
        "vitality_soft":  (p["vitality_soft"], ""),
        "ok_soft":        (p["vitality_soft"], ""),
        "attention":      (p["attention"], p["attention_bg"] if bg else ""),
        "dirty":          (p["attention"], p["attention_bg"] if bg else ""),
        "warn":           (p["attention"], p["attention_bg"] if bg else ""),
        "attention_soft": (p["attention_soft"], ""),
        "interrupt":      (p["interrupt"], p["interrupt_bg"] if bg else ""),
        "interrupt_rest": (p["interrupt_rest"], ""),
        "fault":          (p["fault"], p["fault_bg"] if bg else ""),
        "crit":           (p["fault"], p["fault_bg"] if bg else ""),
        "fault_soft":     (p["fault_soft"], ""),
    }
    fg_col, bg_col = role_map.get(role, (p["focus"], ""))
    bg_attr = f" background='{bg_col}'" if bg_col else ""
    return f"<span color='{fg_col}'{bg_attr}>{Canvas.escape(s_txt)}</span>"


def SwatchTxt(hex_color: str, show_hex: bool = True) -> str:
    """
    ECCEZIONE CONTROLLATA (Golden Standard §4.2 — Color Swatches):
    Unica primitiva autorizzata a dipingere un campione esadecimale letterale (`■ #rrggbb`)
    nei selettori colore del tema/sfondo. Se il colore è quasi nero (luminanza minima),
    usa un blocco pieno incapsulato per non renderlo invisibile sul fondo scuro del Box.
    """
    p = get_semantic_palette()
    hx = _valid_hex(hex_color, p["focus"]).lower()
    r, g, b = int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)
    if max(r, g, b) < 42:
        glyph = f"<span color='{p['schema']}' background='{hx}'>\u25a1</span>"
    else:
        glyph = f"<span color='{hx}'>\u25a0</span>"
    if not show_hex:
        return glyph
    return f"{glyph} <span color='{p['focus_val']}'>{Canvas.escape(hx)}</span>"


def RelevanceGlyph(score: int, show_pct: bool = True) -> str:
    """Indicatore graduato dal verde all'arancio per il coefficiente di pertinenza (0..100)."""
    sc = max(0, min(100, int(score)))
    if sc >= 90:
        col = "#00ff73"
    elif sc >= 80:
        col = "#66ff33"
    elif sc >= 70:
        col = "#b8ff19"
    elif sc >= 60:
        col = "#ffeb19"
    elif sc >= 50:
        col = "#ffb319"
    else:
        col = "#ff7a19"
    if not show_pct:
        return f"<span color='{col}'>\u25cf</span>"
    return f"<span color='{col}'>\u25cf {sc:2d}%</span>"


def SelectPill(label: str, uri: str, selected: bool = False, dirty: bool = False, style: str = "default", min_chars: int = 0) -> str:
    """
    Selettore persistente di opzione/modalità/toggle (Golden Standard §2.2 / §3.1):
      - `selected=True, dirty=True`  -> `[● Opzione]` in `attention` su `attention_bg`.
      - `selected=True, dirty=False` -> `[● Opzione]` in `intent` su `intent_bg`.
      - `selected=False`             -> `[○ Opzione]` in `inactive`, ZERO sfondo.
    Rimuove eventuali pallini duplicati passati per errore dentro `label`.
    """
    p = get_semantic_palette()
    asc = THEME["ascii"]
    b_l, b_r = _pair(asc, "button_brackets", "[", "]")
    dots = _pair(asc, "pill_dots", "\u25cf", "\u25cb")
    raw = _LEADING_PILL_DOTS_RE.sub("", str(label).strip())
    eff_min = min(int(min_chars), len(raw) + 8) if min_chars > 0 else 0
    if eff_min > len(raw):
        raw = raw.rjust(eff_min)
    clean = Canvas.escape(raw.replace(" ", "\u00a0"))
    if selected and dirty:
        fg, bg, brk, dot = p["attention"], p["attention_bg"], p["attention_soft"], f"{dots[0]}\u00a0"
    elif selected:
        fg, bg, brk, dot = p["intent"], p["intent_bg"], p["intent_soft"], f"{dots[0]}\u00a0"
    elif style == "secondary":
        fg, bg, brk, dot = p["schema"], "", p["structure"], f"{dots[1]}\u00a0"
    else:
        fg, bg, brk, dot = p["inactive"], "", p["structure"], f"{dots[1]}\u00a0"

    bg_attr = f" background='{bg}'" if bg else ""
    return (
        f"<a href='{Canvas.escape(uri)}'>"
        f"<span color='{brk}'{bg_attr} underline='none'>{Canvas.escape(b_l)}</span>"
        f"<span color='{fg}'{bg_attr} underline='none'>{dot}{clean}</span>"
        f"<span color='{brk}'{bg_attr} underline='none'>{Canvas.escape(b_r)}</span>"
        f"</a>"
    )


def StateBadge(is_on: bool, on_text: str, off_text: str = "OFF", on_tone: str = "ok", min_chars: int = 0) -> str:
    """
    Indicatore passivo di stato hardware/demone/link (Golden Standard §2.2 / §3.1.3):
      - `is_on=True` : `● STATO` con `vitality_bg` ('ok'), `attention_bg` ('warn') o `fault_bg` ('crit').
      - `is_on=False`: `○ STATO` in `inactive` (#596278), ZERO sfondo.
    MEMENTO DI GUARDIA:
      - `on_tone='select'`/'intent' è vietato dalla Legge di Purezza del Ciano (§4.1.5):
        uno stato passivo del sistema non è una scelta utente, quindi ricade su `'ok'` (`vitality`).
      - Non passare MAI parole di stato attivo (es. 'LIVE', 'ACTIVE') in `off_text`!
    """
    p = get_semantic_palette()
    dots = _pair(THEME["ascii"], "pill_dots", "\u25cf", "\u25cb")
    d_on, d_off = dots[0], dots[1]
    if is_on:
        raw_on = _LEADING_PILL_DOTS_RE.sub("", str(on_text).strip())
        target_w = min(max(0, int(min_chars) - 2), len(raw_on) + 6) if min_chars > 0 else 0
        if target_w > len(raw_on):
            raw_on = raw_on.ljust(target_w)
        clean_on = Canvas.escape(raw_on.replace(" ", "\u00a0"))
        if on_tone == "ok_soft":
            return f"<span color='{p['vitality_soft']}'>{d_on}\u00a0{clean_on}</span>"
        if on_tone in ("warn", "attention", "dirty"):
            fg, bg = p["attention"], p["attention_bg"]
        elif on_tone in ("crit", "fault", "danger"):
            fg, bg = p["fault"], p["fault_bg"]
        else:
            fg, bg = p["vitality"], p["vitality_bg"]
        return f"<span color='{fg}' background='{bg}'>{d_on}\u00a0{clean_on}</span>"

    raw_off = _LEADING_PILL_DOTS_RE.sub("", str(off_text).strip())
    target_w = min(max(0, int(min_chars) - 2), len(raw_off) + 6) if min_chars > 0 else 0
    if target_w > len(raw_off):
        raw_off = raw_off.ljust(target_w)
    clean_off = Canvas.escape(raw_off.replace(" ", "\u00a0"))
    return f"<span color='{p['inactive']}'>{d_off}\u00a0{clean_off}</span>"


def ActionBtn(
    label: str,
    uri: str,
    tone: str = "neutral",
    active: bool = False,
    brackets: bool = True,
    min_chars: int = 0,
    *,
    modal_active: bool = False,
    _kb_focus: bool = False,
) -> str:
    """
    Pulsante di comando imperativo stateless (Golden Standard §2.2 / §3.2.1 / §4.1):
      - 'neutral' / 'schema'      : Grigio Ardesia (Refresh, Copy, Edit.., Inspect, Collapse)
      - 'apply' / 'vitality'      : Verde Smeraldo (Apply, + Add.., Enable, Up, Mount, Save)
      - 'warn' / 'attention'      : Giallo Cyber (Reset, Clean Cache, Suspend, Unbind)
      - 'danger_soft' / 'interrupt': Rosa-Magenta a riposo -> Magenta in hover (Disable, Remove, [x])
      - 'muted' / 'inactive'      : Grafite Spento (Apply senza modifiche staged, stepper disabilitato)

    MEMENTO INVALICABILE (Anti-Regressione Golden Standard):
      1. `active=True` passato per errore dai pannelli viene IGNORATO per lo sfondo:
         NESSUN `ActionBtn` a riposo può avere sfondo pieno (`bg = ""`).
         Lo sfondo è ammesso SOLO su cattura modale realmente in corso (`modal_active=True`)
         o sul candidato puntato da tastiera dentro `InlinePicker` (`_kb_focus=True`).
      2. `tone='select'/'intent'/'accent'/'info'` a riposo viene ricondotto a `'neutral'` (`schema`)
         per proteggere la Legge di Purezza del Ciano (§4.1.5).
      3. Eventuali pallini `●`/`○` in `label` vengono rimossi quando `not modal_active`.
      4. `min_chars` è limitato a `len(label) + 6` per impedire parentesi cave `[       Testo       ]`.
    """
    p = get_semantic_palette()
    b_l, b_r = _pair(THEME["ascii"], "button_brackets", "[", "]") if brackets else ("", "")
    raw_lbl = str(label).strip()
    if not modal_active:
        raw_lbl = _LEADING_PILL_DOTS_RE.sub("", raw_lbl)
    elif raw_lbl and not _LEADING_PILL_DOTS_RE.match(raw_lbl):
        dots = _pair(THEME["ascii"], "pill_dots", "\u25cf", "\u25cb")
        raw_lbl = f"{dots[0]} {raw_lbl}"

    eff_min = min(int(min_chars), len(raw_lbl) + 6) if min_chars > 0 else 0
    if eff_min > len(raw_lbl):
        raw_lbl = raw_lbl.center(eff_min)
    clean = Canvas.escape(raw_lbl.replace(" ", "\u00a0"))

    bg = ""
    brk = p["structure"]

    if _kb_focus:
        fg, bg, brk = p["intent"], p["intent_bg"], p["intent_soft"]
    elif modal_active:
        if tone in ("danger_soft", "interrupt", "alert", "danger", "crit", "fault"):
            fg, bg, brk = p["interrupt"], p["interrupt_bg"], p["interrupt"]
        else:
            fg, bg, brk = p["attention"], p["attention_bg"], p["attention_soft"]
    elif tone in ("apply", "vitality", "ok"):
        fg = p["vitality"]
    elif tone in ("warn", "attention"):
        fg = p["attention"]
    elif tone in ("danger_soft", "interrupt", "alert"):
        fg = p["interrupt_rest"]
    elif tone in ("danger", "crit", "fault"):
        fg = p["fault"]
    elif tone in ("primary", "focus"):
        fg = p["focus"]
    elif tone in ("muted", "inactive"):
        fg = p["inactive"]
    else:
        # Include 'neutral', 'schema' e intercetta 'select'/'intent'/'accent'/'info' (vietati su ActionBtn)
        fg = p["schema"]

    bg_attr = f" background='{bg}'" if bg else ""
    if not brackets:
        return f"<a href='{Canvas.escape(uri)}'><span color='{fg}'{bg_attr} underline='none'>{clean}</span></a>"
    return (
        f"<a href='{Canvas.escape(uri)}'>"
        f"<span color='{brk}'{bg_attr} underline='none'>{Canvas.escape(b_l)}</span>"
        f"<span color='{fg}'{bg_attr} underline='none'>{clean}</span>"
        f"<span color='{brk}'{bg_attr} underline='none'>{Canvas.escape(b_r)}</span>"
        f"</a>"
    )


def InspectorCell(label: str, uri: str, selected: bool, is_alive: bool = True, icon: str = "") -> str:
    """
    Selettore riga Master-Detail nella colonna 0 di una tabella (Golden Standard §2.2 / §6.4):
      - Selezionata per ispezione: Ciano (`intent`) su `intent_bg` con cursore '▸'.
      - Non selezionata viva     : Bianco Puro (`focus`), ZERO sfondo.
      - Non selezionata spenta   : Grafite Spento (`inactive`), ZERO sfondo.
    """
    p = get_semantic_palette()
    ic_part = f"{icon}\u00a0" if icon else ""
    raw = _LEADING_CHEV_RE.sub("", str(label).strip())
    clean = Canvas.escape(raw.replace(" ", "\u00a0"))
    if selected:
        return (
            f"<a href='{Canvas.escape(uri)}'>"
            f"<span color='{p['intent']}' background='{p['intent_bg']}' underline='none'>"
            f"\u00a0\u25b8\u00a0{ic_part}{clean}\u00a0"
            f"</span></a>"
        )
    fg = p["focus"] if is_alive else p["inactive"]
    return (
        f"<a href='{Canvas.escape(uri)}'>"
        f"<span color='{p['inactive']}' underline='none'>\u00a0\u00a0\u00a0</span>"
        f"<span color='{fg}' underline='none'>{ic_part}{clean}</span>"
        f"</a>"
    )


def TabBar(tabs: list[tuple[str, str]], active_tab: str, uri_prefix: str = "ui:tab", min_tab_cols: int = 18, sep: str = "  ") -> str:
    """
    Costruisce una barra di navigazione a Tab larghe e ben distanziate:
      - Tab attiva  : Ciano Elettrico (#00f0ff) su fondo Blu Cobalto (#002a3a) con indicatore '●'.
      - Tab inattive: Grigio Ardesia (#8c9bba) su fondo Grafite Scuro (#1a1c23) con indicatore '○'.
    """
    p = get_semantic_palette()
    asc = THEME["ascii"]
    dots = _pair(asc, "pill_dots", "\u25cf", "\u25cb")
    caps = _pair(asc, "tab_caps", "\u258f", "\u2595")
    use_cairo_caps = (caps == ("\u258f", "\u2595"))
    cap_l = "\u00a0" if use_cairo_caps else Canvas.escape(caps[0])
    cap_r = "\u00a0" if use_cairo_caps else Canvas.escape(caps[1])
    rendered = []
    for t_id, t_lbl in tabs:
        is_act = (t_id == active_tab)
        dot = dots[0] if is_act else dots[1]
        raw_text = f"{dot} {str(t_lbl).strip()}"
        pad_total = max(2, min_tab_cols - len(raw_text))
        pad_l = max(1, pad_total // 2)
        pad_r = max(1, pad_total - pad_l)
        padded_lbl = ("\u00a0" * pad_l) + Canvas.escape(raw_text.replace(" ", "\u00a0")) + ("\u00a0" * pad_r)
        uri = Canvas.escape(f"{uri_prefix}:{t_id}")
        if is_act:
            fg, bg, brk = p["intent"], p["intent_bg"], p["intent"]
        else:
            fg, bg, brk = p["schema"], p["structure_bg"], p["structure"]
        rendered.append(
            f"<a href='{uri}'>"
            f"<span color='{brk}' background='{bg}' underline='none'>{cap_l}</span>"
            f"<span color='{fg}' background='{bg}' underline='none'>{padded_lbl}</span>"
            f"<span color='{brk}' background='{bg}' underline='none'>{cap_r}</span>"
            f"</a>"
        )
    return sep.join(rendered)


def format_tab_bar_rows(
    canvas: Canvas,
    tabs: list[tuple[str, str]],
    active_tab: str,
    uri_prefix: str = "ui:tab",
    min_tab_cols: int = 18,
) -> list[str]:
    """Disponi le tab su 1 riga centrata (preferendo ampiezza uniforme) oppure su righe bilanciate con capsule identiche."""
    if not tabs:
        return []
    budget_px = max(canvas.char_w * 24, canvas.inner_px - 2.0)
    uniform_floor = max(len(f"\u25cf {str(lbl).strip()}") for _, lbl in tabs) + 2

    def _fit_row(chunk: list[tuple[str, str]], max_mc: int, min_mc: int = 0) -> str | None:
        for sep in ("  ", " "):
            for mc in range(max_mc, min_mc - 1, -1):
                cand = TabBar(chunk, active_tab, uri_prefix=uri_prefix, min_tab_cols=mc, sep=sep)
                if canvas.measure_markup(cand) <= budget_px + 0.5:
                    return canvas.pad_cell(cand, canvas.inner_cols, align="center", truncate=False)
        return None

    single_uniform = _fit_row(tabs, max(min_tab_cols, uniform_floor), min_mc=uniform_floor)
    if single_uniform is not None:
        return [single_uniform]

    single = _fit_row(tabs, min_tab_cols)
    if single is not None:
        return [single]

    n_tabs = len(tabs)
    n_rows = 2
    while n_rows < n_tabs:
        q, r = divmod(n_tabs, n_rows)
        chunks = []
        idx = 0
        for row_i in range(n_rows):
            sz = q + (1 if row_i < r else 0)
            chunks.append(tabs[idx : idx + sz])
            idx += sz
        if all(canvas.measure_markup(TabBar(ch, active_tab, uri_prefix=uri_prefix, min_tab_cols=0, sep=" ")) <= budget_px + 0.5 for ch in chunks):
            break
        n_rows += 1
    else:
        chunks = [[it] for it in tabs]

    start_mc = max(min_tab_cols, uniform_floor)
    chosen_sep, chosen_mc = " ", 0
    found_shared = False
    for min_bound in (uniform_floor, 0):
        for sep in ("  ", " "):
            for mc in range(start_mc, min_bound - 1, -1):
                if all(
                    canvas.measure_markup(TabBar(ch, active_tab, uri_prefix=uri_prefix, min_tab_cols=mc, sep=sep)) <= budget_px + 0.5
                    for ch in chunks
                ):
                    chosen_sep, chosen_mc = sep, mc
                    found_shared = True
                    break
            if found_shared:
                break
        if found_shared:
            break

    return [
        canvas.pad_cell(
            TabBar(ch, active_tab, uri_prefix=uri_prefix, min_tab_cols=chosen_mc, sep=chosen_sep),
            canvas.inner_cols,
            align="center",
            truncate=False,
        )
        for ch in chunks
    ]


def _fold_glyph(ch: str, color: str) -> str:
    """Rende il marker collassabile all'85% (esente dal downscaling 62% PUA) in giallo `attention` ad alto contrasto."""
    fold_pt = max(6144, int(round(float(THEME["typography"].get("font_size", 11)) * 1024 * 0.85)))
    return f"<span color='{color}' underline='none' size='{fold_pt}'>{Canvas.escape(ch)}</span>"


def FoldIconBtn(is_open: bool, uri: str, label: str = "", dirty: bool = False) -> str:
    """
    Controllo collassabile standard (Golden Standard §2.2 / §4.1.2 / §4.1.7):
      - Parentesi `[...]` in `structure` (o `attention_soft` se dirty)
      - Marker pieno (`▶`/`▼`, ``/`` o `⊞`/`⊟` all'85%) sempre in giallo `attention` (mutamento vista)
      - Etichetta esplicita (`Expand`/`Collapse`, mai icona nuda) in `schema` (`#8b9bb8`), MAI `intent` e MAI sfondo.
    """
    p = get_semantic_palette()
    asc = THEME["ascii"]
    c_icons = _pair(asc, "collapsible_icons", "\u25bc", "\u25b6")
    b_l, b_r = _pair(asc, "button_brackets", "[", "]")
    icon = c_icons[0] if is_open else c_icons[1]
    fg_ic = p["attention"]
    fg_lbl = p["attention"] if dirty else p["schema"]
    brk = p["attention_soft"] if dirty else p["structure"]
    lbl_text = str(label or "").strip() or (t("ui.collapse") if is_open else t("ui.expand"))
    lbl_part = f"<span color='{fg_lbl}' underline='none'>\u00a0{Canvas.escape(lbl_text.replace(' ', chr(160)))}</span>"
    return (
        f"<a href='{Canvas.escape(uri)}'>"
        f"<span color='{brk}' underline='none'>{Canvas.escape(b_l)}</span>"
        f"{_fold_glyph(icon, fg_ic)}"
        f"{lbl_part}"
        f"<span color='{brk}' underline='none'>{Canvas.escape(b_r)}</span>"
        f"</a>"
    )


def FoldToggle(panel, sec_key: str, default: bool = False, is_it: bool = True, dirty: bool = False, uri_prefix: str = "net:toggle_sec") -> tuple[bool, str]:
    """
    MEMENTO (Golden Standard §2.2 / §4.1.2 / §4.1.7):
    Espandere o comprimere una sezione NON è una scelta di configurazione: il controllo
    usa `[▶ Expand]` / `[▼ Collapse]` con marker pieno all'85% in `attention` (giallo) ed
    etichetta in `schema`, zero sfondo e mai `intent`. Preferire `box.add_collapsible(...)` per le sottosezioni.
    """
    if uri_prefix == "ui:state":
        opened = bool(panel.get_state(sec_key, default)) or dirty
        uri = f"ui:state:{sec_key}:{'false' if opened else 'true'}"
    else:
        opened = bool(panel.get_state("open_sections", {}).get(sec_key, default)) or dirty
        uri = f"{uri_prefix}:{sec_key}"
    lbl = t("ui.collapse") if opened else t("ui.expand")
    return opened, FoldIconBtn(opened, uri, label=lbl, dirty=dirty)


# --- Alias legacy mantenuti solo per retrocompatibilità; usare i nomi canonici sopra ---
ui_pill = SelectPill
ui_act_btn = ActionBtn
ui_status_badge = StateBadge
ui_fold_toggle = FoldToggle


def Btn(label: str, action: str, style: str = "accent", active: bool = False, brackets: bool = True) -> str:
    """Wrapper legacy deprecato -> instrada su `ActionBtn` bloccando sfondi e ciano impropri."""
    style_to_tone = {
        "accent":    "apply",
        "info":      "neutral",
        "primary":   "primary",
        "secondary": "neutral",
        "muted":     "muted",
        "warn":      "warn",
        "alert":     "danger_soft",
        "crit":      "danger_soft",
    }
    return ActionBtn(label, action, tone=style_to_tone.get(style, "neutral"), brackets=brackets)


def ToggleExpandBtn(is_expanded: bool) -> str:
    """Pulsante di espansione/compressione popup Waybar: sempre `neutral` senza sfondo (§3.2.1)."""
    return ActionBtn("-" if is_expanded else "+", "ui:toggle_expand", tone="neutral")


def StateToggleBtn(label_on: str, label_off: str, state_key: str, current_val: bool, style: str = "secondary") -> str:
    next_val = "false" if current_val else "true"
    lbl = label_on if current_val else label_off
    return SelectPill(lbl, f"ui:state:{state_key}:{next_val}", selected=current_val)


def Badge(text: str, style: str = "warn", min_chars: int = 0) -> str:
    """
    Capsula passiva di allerta/verdetto operativo (Golden Standard §2.1.2 / §3.1.3 / §3.2.2):
      - `'ok'` / `'vitality'`   : capsula `vitality_bg` (es. verdetto `ACCEPT` attivo).
      - `'warn'` / `'attention'`: capsula `attention_bg` (`PREVIEW *`, `MISSING: bin`, `NOT SET`).
      - `'crit'` / `'fault'`    : capsula `fault_bg` (`OFFLINE`, `TIMEOUT`, `DROP`).

    MEMENTO INVALICABILE (Anti-Regressione Golden Standard):
      1. `Badge` NON emette MAI `intent` / `intent_bg` (Ciano): se un chiamante passa
         `style='info'`/`'select'`/`'intent'`/`'neutral'`, degrada a testo puro `schema` senza sfondo!
      2. `Badge` NON racchiude MAI il testo tra parentesi quadre `[...]` (riservate ai link cliccabili):
         `style='muted'`/`'inactive'` degrada a testo puro `inactive` senza parentesi e senza sfondo!
    """
    p = get_semantic_palette()
    raw = str(text).strip()
    if len(raw) >= 2 and raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1].strip()
    eff_min = min(int(min_chars), len(raw) + 6) if min_chars > 0 else 0
    if eff_min > len(raw):
        raw = raw.center(eff_min)
    clean = Canvas.escape(raw.replace(" ", "\u00a0"))
    if style in ("accent", "ok", "vitality"):
        return f"<span color='{p['vitality']}' background='{p['vitality_bg']}'>\u00a0{clean}\u00a0</span>"
    if style in ("warn", "attention", "dirty"):
        return f"<span color='{p['attention']}' background='{p['attention_bg']}'>\u00a0{clean}\u00a0</span>"
    if style in ("crit", "fault", "alert", "danger"):
        return f"<span color='{p['fault']}' background='{p['fault_bg']}'>\u00a0{clean}\u00a0</span>"
    if style in ("muted", "inactive"):
        return f"<span color='{p['inactive']}'>{clean}</span>"
    return f"<span color='{p['schema']}'>{clean}</span>"


def StatusDot(active: bool, label_on: str = "ON", label_off: str = "OFF") -> str:
    return StateBadge(active, label_on, label_off, on_tone="ok")


def ColorSwatch(hex_color: str, width: int = 4) -> str:
    """ECCEZIONE CONTROLLATA (§4.2): Campione colore esadecimale letterale."""
    p = get_semantic_palette()
    hx = _valid_hex(hex_color, "#000000")
    pad = "\u00a0" * max(1, int(width))
    return f"<span color='{p['structure']}' background='{hx}'>{pad}</span>"


def ProgressBar(pct: float, width: int = 15, color_by_threshold: bool = True, invert_threshold: bool = False) -> str:
    """
    Barra telemetrica passiva di sola lettura (Golden Standard §2.2 / §4.1.6):
    NON ha mai steppers `[-]`/`[+]` e NON usa mai il Ciano `intent`.
    """
    asc = THEME["ascii"]
    p = get_semantic_palette()
    pct_clamped = max(0.0, min(100.0, float(pct)))
    fill_n = int(round((pct_clamped / 100.0) * width))
    empty_n = max(0, width - fill_n)
    eff_pct = (100.0 - pct_clamped) if invert_threshold else pct_clamped
    c_fill = color_for_pct(eff_pct) if color_by_threshold else p["vitality"]
    c_empty = p["structure"]
    b_l, b_r = _pair(asc, "slider_brackets", "[", "]")
    s_fill = str(asc.get("slider_fill") or "#")[0]
    s_empty = str(asc.get("slider_empty") or "-")[0]

    return (
        f"<span color='{c_empty}'>{Canvas.escape(b_l)}</span>"
        f"<span color='{c_fill}'>{Canvas.escape(s_fill) * fill_n}</span>"
        f"<span color='{c_empty}'>{Canvas.escape(s_empty) * empty_n}</span>"
        f"<span color='{c_empty}'>{Canvas.escape(b_r)}</span>"
    )


_SLIDER_META: dict[str, dict] = {}
_SCROLL_ZONE_META: dict[str, str] = {}


def get_slider_step_uris(action_prefix: str) -> tuple[str, str] | None:
    meta = _SLIDER_META.get(action_prefix)
    if not meta:
        return None
    return meta["dec_uri"], meta["inc_uri"]


def is_slider_enabled(action_prefix: str) -> bool:
    meta = _SLIDER_META.get(action_prefix)
    return bool(meta.get("enabled", True)) if meta else True


def set_slider_meta_value(action_prefix: str, val_str: str) -> None:
    meta = _SLIDER_META.get(action_prefix)
    if not meta or meta.get("scroll_prefix"):
        return
    try:
        v = float(val_str)
    except ValueError:
        return
    lo, hi, stp, is_flt = meta["min"], meta["max"], meta["step"], meta["is_float"]
    clamped = max(lo, min(hi, v))
    meta["cur"] = clamped
    meta["dec_uri"] = f"{action_prefix}:{_fmt_slider_val(max(lo, clamped - stp), is_flt)}"
    meta["inc_uri"] = f"{action_prefix}:{_fmt_slider_val(min(hi, clamped + stp), is_flt)}"


def advance_slider_uri(action_prefix: str, direction: str) -> str | None:
    """Compute the next slider URI from live in-memory state so rapid steps never drop ticks."""
    meta = _SLIDER_META.get(action_prefix)
    if not meta or not meta.get("enabled", True):
        return None
    if meta.get("scroll_prefix"):
        return f"{meta['scroll_prefix']}:{'up' if direction == 'up' else 'down'}"
    lo, hi, stp, is_flt = meta["min"], meta["max"], meta["step"], meta["is_float"]
    delta = stp if direction == "up" else -stp
    nxt = max(lo, min(hi, meta["cur"] + delta))
    meta["cur"] = nxt
    meta["dec_uri"] = f"{action_prefix}:{_fmt_slider_val(max(lo, nxt - stp), is_flt)}"
    meta["inc_uri"] = f"{action_prefix}:{_fmt_slider_val(min(hi, nxt + stp), is_flt)}"
    return f"{action_prefix}:{_fmt_slider_val(nxt, is_flt)}"


def register_scroll_zone(uri: str, scroll_action_prefix: str) -> None:
    if uri and scroll_action_prefix:
        _SCROLL_ZONE_META[str(uri)] = str(scroll_action_prefix)


def get_scroll_zone_action(uri: str) -> str | None:
    return _SCROLL_ZONE_META.get(str(uri or ""))


def _fmt_slider_val(val: float, is_float: bool) -> str:
    if not is_float:
        return str(int(round(float(val))))
    s = f"{round(float(val), 4):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def Slider(
    value: float,
    action_prefix: str,
    min_val: float = 0.0,
    max_val: float = 100.0,
    width: int = 15,
    show_pct: bool = True,
    step_buttons: bool = True,
    step: float = 5.0,
    scroll_prefix: str = "",
    enabled: bool = True,
) -> str:
    """
    Barra di modulazione continua interattiva (Golden Standard §2.2 / §4.1.5 / §8):
      - Morfologia obbligatoria `[-] [####----] [+]` (gli steppers `[-]`/`[+]` sono sempre resi
        per `width >= 6` così uno `Slider` interattivo non è MAI confondibile con `ProgressBar`).
      - Usa `intent` (Ciano) quando `enabled=True`, e si spegne su `inactive` quando `enabled=False`
        (es. quando il toggle associato sulla stessa riga è `OFF`).
      - Registra in `_SLIDER_META` gli URI di decremento/incremento per la modulazione element-hover.
    """
    asc = THEME["ascii"]
    p = get_semantic_palette()
    lo, hi = float(min_val), float(max_val)
    span = max(1e-6, hi - lo)
    norm = max(0.0, min(1.0, (float(value) - lo) / span))
    fill_n = int(round(norm * width))
    is_float = any(not float(x).is_integer() for x in (min_val, max_val, step))

    c_fill = p["intent"] if enabled else p["inactive"]
    c_empty = p["structure"]
    b_l, b_r = _pair(asc, "slider_brackets", "[", "]")
    s_fill = str(asc.get("slider_fill") or "#")[0]
    s_empty = str(asc.get("slider_empty") or "-")[0]
    segments = []
    for i in range(1, width + 1):
        seg_val = _fmt_slider_val(lo + (i / float(width)) * span, is_float)
        ch = s_fill if i <= fill_n else s_empty
        col = c_fill if i <= fill_n else c_empty
        segments.append(
            f"<a href='{Canvas.escape(action_prefix)}:{seg_val}'><span color='{col}' underline='none'>{Canvas.escape(ch)}</span></a>"
        )

    v_dec = _fmt_slider_val(max(lo, float(value) - float(step)), is_float)
    v_inc = _fmt_slider_val(min(hi, float(value) + float(step)), is_float)
    _SLIDER_META[action_prefix] = {
        "cur": max(lo, min(hi, float(value))),
        "min": lo,
        "max": hi,
        "step": float(step),
        "is_float": is_float,
        "scroll_prefix": scroll_prefix,
        "enabled": bool(enabled),
        "dec_uri": f"{scroll_prefix}:down" if scroll_prefix else f"{action_prefix}:{v_dec}",
        "inc_uri": f"{scroll_prefix}:up" if scroll_prefix else f"{action_prefix}:{v_inc}",
    }

    bar_markup = f"<span color='{c_empty}'>{Canvas.escape(b_l)}</span>{''.join(segments)}<span color='{c_empty}'>{Canvas.escape(b_r)}</span>"
    if step_buttons or width >= 6:
        step_tone = "neutral" if enabled else "muted"
        bar_markup = f"{ActionBtn('-', f'{action_prefix}:{v_dec}', tone=step_tone)}\u00a0{bar_markup}\u00a0{ActionBtn('+', f'{action_prefix}:{v_inc}', tone=step_tone)}"
    if show_pct:
        pct_int = int(round(value)) if (not is_float and 0 <= min_val < max_val <= 200) else int(round(norm * 100.0))
        pct_col = p["focus_val"] if enabled else p["inactive"]
        bar_markup += f"\u00a0<span color='{pct_col}'>{pct_int:3d}%</span>"
    return bar_markup


def Sparkline(values: list, max_items: int = 32) -> str:
    chars = str(THEME["ascii"].get("sparkline_chars") or "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588")
    n_levels = len(chars)
    out = []
    for v in values[:max_items]:
        pct = max(0.0, min(100.0, float(v)))
        idx = min(n_levels - 1, int((pct / 100.0) * n_levels))
        col = color_for_pct(pct)
        out.append(f"<span color='{col}'>{chars[idx]}</span>")
    return "".join(out)


def CyclePill(label: str, uri: str, active: bool = False, dirty: bool = False, min_chars: int = 0) -> str:
    """
    Selettore ciclico compatto `[↻ valore]` (Golden Standard §2.2 / §3.1.1):
    MEMENTO DI GUARDIA: Se il valore corrente è un token neutro di default ('ALL', '*', 'ANY'),
    `active` viene forzato a `False` (zero sfondo ciano sul filtro non attivo).
    """
    p = get_semantic_palette()
    asc = THEME["ascii"]
    b_l, b_r = _pair(asc, "button_brackets", "[", "]")
    raw = _LEADING_CYCLE_RE.sub("", str(label).strip())
    is_eff_active = bool(active) and (raw.upper() not in _NEUTRAL_FILTER_TOKENS)
    eff_min = min(int(min_chars), len(raw) + 6) if min_chars > 0 else 0
    if eff_min > len(raw):
        raw = raw.ljust(eff_min)
    clean = Canvas.escape(raw.replace(" ", "\u00a0"))
    if dirty:
        fg, bg, brk = p["attention"], p["attention_bg"], p["attention_soft"]
    elif is_eff_active:
        fg, bg, brk = p["intent"], p["intent_bg"], p["intent_soft"]
    else:
        fg, bg, brk = p["schema"], "", p["structure"]
    bg_attr = f" background='{bg}'" if bg else ""
    return (
        f"<a href='{Canvas.escape(uri)}'>"
        f"<span color='{brk}'{bg_attr} underline='none'>{Canvas.escape(b_l)}</span>"
        f"<span color='{fg}'{bg_attr} underline='none'>\u21bb\u00a0{clean}</span>"
        f"<span color='{brk}'{bg_attr} underline='none'>{Canvas.escape(b_r)}</span>"
        f"</a>"
    )


def _render_editable_slice(
    buf: str,
    cursor_pos: int,
    placeholder: str,
    avail_w: int,
    blink_on: bool,
    s_cur: str,
    fg_col: str,
    cur_col: str,
    ph_col: str,
    bg: str,
) -> str:
    """Renderizza `buf` a larghezza fissa `avail_w` con cursore Cairo a larghezza zero (zero shift al blink)."""
    w = max(4, int(avail_w))
    s = str(buf or "")
    bg_attr = f" background='{bg}'" if bg else ""

    if not s:
        ph_s = str(placeholder or "")
        shown = ph_s if len(ph_s) <= w else (ph_s[: max(1, w - 1)] + "\u2026")
        full = shown + ("\u00a0" * max(0, w - len(shown)))
        col, c_idx, right_edge = ph_col, 0, False
    else:
        pos = max(0, min(len(s), int(cursor_pos)))
        col = fg_col
        if pos == len(s):
            if len(s) < w:
                full = s + ("\u00a0" * (w - len(s)))
                c_idx, right_edge = len(s), False
            else:
                full = s if len(s) == w else ("\u2026" + s[-(w - 1) :])
                c_idx, right_edge = w - 1, True
        else:
            if len(s) <= w:
                vis = s
                c_idx = pos
            else:
                half = w // 2
                start = max(0, min(len(s) - (w - 1), pos - half))
                has_l = start > 0
                body_cap = w - (1 if has_l else 0)
                sub = s[start : start + body_cap]
                vis = ("\u2026" + sub) if has_l else sub
                c_idx = max(0, min(len(vis) - 1, (pos - start) + (1 if has_l else 0)))
            full = vis + ("\u00a0" * max(0, w - len(vis)))
            right_edge = False

    def _esc(txt: str) -> str:
        return Canvas.escape(txt.replace(" ", "\u00a0"))

    if not blink_on:
        return f"<span color='{col}'{bg_attr} underline='none'>{_esc(full)}</span>"

    pre = full[:c_idx]
    at_ch = full[c_idx]
    post = full[c_idx + 1 :]
    cur_attr = f" strikethrough='false' strikethrough_color='{cur_col}'" if right_edge else f" underline_color='{cur_col}'"
    parts = []
    if pre:
        parts.append(f"<span color='{col}'{bg_attr} underline='none'>{_esc(pre)}</span>")
    parts.append(f"<span color='{col}'{bg_attr} underline='none'{cur_attr}>{_esc(at_ch)}</span>")
    if post:
        parts.append(f"<span color='{col}'{bg_attr} underline='none'>{_esc(post)}</span>")
    return "".join(parts)


def InlineFilter(
    entry_id: str,
    query: str,
    placeholder: str,
    trigger_uri: str,
    clear_uri: str = "",
    width: int = 30,
    compact: bool = False,
    active: bool = False,
) -> str:
    """
    Casella di filtro/ricerca inline a larghezza stabile:
      - Focus attivo: cornice e fondo `intent` con cursore lampeggiante da tema.
      - Query attiva non a fuoco: cornice `intent_soft` su `structure_bg`, testo `focus_val`, ZERO cursore + `[x]`.
      - Vuota non a fuoco: cornice `structure` su `structure_bg`, placeholder `inactive`.
    """
    p = get_semantic_palette()
    asc = THEME["ascii"]
    s_pref = "\uf002" if compact else str(asc.get("search_prefix") or "\uf002 >").strip()
    s_l, s_r = _pair(asc, "search_brackets", "\u258f", "\u2595")
    s_cur = str(asc.get("search_cursor") or "\u2502")[0]

    is_focused = bool((entry_id and get_active_inline_entry() == entry_id) or active)
    q_str = get_inline_entry_buffer() if (entry_id and get_active_inline_entry() == entry_id) else str(query or "")
    has_err = bool(entry_id and is_focused and get_inline_entry_error(entry_id))

    if has_err:
        bg, brk, pref_col, cur_col = p["fault_bg"], p["fault"], p["fault"], p["fault"]
    elif is_focused:
        bg, brk, pref_col, cur_col = p["intent_bg"], p["intent"], p["intent"], p["intent"]
    elif q_str:
        bg, brk, pref_col, cur_col = p["structure_bg"], p["intent_soft"], p["intent"], p["intent"]
    else:
        bg, brk, pref_col, cur_col = p["structure_bg"], p["structure"], p["schema"], p["intent"]

    pref_txt = f" {s_pref} "
    inner_w = max(len(pref_txt) + 4, int(width))
    avail_body = max(4, inner_w - len(pref_txt))
    head_p = Canvas.escape(pref_txt.replace(" ", "\u00a0"))

    if is_focused:
        blink = _INLINE_BLINK_ON if (entry_id and get_active_inline_entry() == entry_id) else True
        cpos = get_inline_cursor_pos() if (entry_id and get_active_inline_entry() == entry_id) else len(q_str)
        body_markup = _render_editable_slice(
            q_str,
            cpos,
            placeholder,
            avail_body,
            blink,
            s_cur,
            p["focus"],
            cur_col,
            p["inactive"],
            bg,
        )
    elif q_str:
        shown = q_str if len(q_str) <= avail_body else (q_str[: max(1, avail_body - 1)] + "\u2026")
        pad_s = "\u00a0" * max(0, avail_body - len(shown))
        body_markup = f"<span color='{p['focus_val']}' background='{bg}' underline='none'>{Canvas.escape(shown.replace(' ', '\u00a0'))}{pad_s}</span>"
    else:
        ph_s = str(placeholder or "")
        shown = ph_s if len(ph_s) <= avail_body else (ph_s[: max(1, avail_body - 1)] + "\u2026")
        pad_s = "\u00a0" * max(0, avail_body - len(shown))
        body_markup = f"<span color='{p['inactive']}' background='{bg}' underline='none'>{Canvas.escape(shown.replace(' ', '\u00a0'))}{pad_s}</span>"

    box_markup = (
        f"<a href='{Canvas.escape(trigger_uri)}'>"
        f"<span color='{brk}' background='{bg}' underline='none'>{Canvas.escape(s_l)}</span>"
        f"<span color='{pref_col}' background='{bg}' underline='none'>{head_p}</span>"
        f"{body_markup}"
        f"<span color='{brk}' background='{bg}' underline='none'>{Canvas.escape(s_r)}</span>"
        f"</a>"
    )
    if q_str and clear_uri and not compact:
        box_markup += "\u00a0" + ActionBtn("x", clear_uri, tone="danger_soft")
    return box_markup


def SearchBox(
    query: str,
    placeholder: str,
    trigger_uri: str,
    clear_uri: str = "",
    active: bool = False,
    width: int = 34,
    entry_id: str = "",
) -> str:
    """Alias standardizzato verso `InlineFilter` (mantiene firma retrocompatibile)."""
    return InlineFilter(
        entry_id=entry_id,
        query=query,
        placeholder=placeholder,
        trigger_uri=trigger_uri,
        clear_uri=clear_uri,
        width=width,
        compact=False,
        active=active,
    )


_INLINE_ACTIVE_ID: str = ""
_INLINE_ACTIVE_URI: str = ""
_INLINE_BUFFER: str = ""
_INLINE_CURSOR_POS: int = 0
_INLINE_CANDIDATES: list[tuple[str, str]] = []
_INLINE_CAND_OWNER: str = ""
_INLINE_TAB_CACHE: list[str] = []
_INLINE_CAND_IDX: int = -1
_INLINE_ERRORS: dict[str, str] = {}
_INLINE_BLINK_ON: bool = True
_INLINE_TIMER_ID: int | None = None
_INLINE_REFRESH_ID: int | None = None
_INLINE_REFRESH_SHELL = None
_INLINE_SHELL = None
_INLINE_HANDLERS: dict[str, dict] = {}
_INLINE_MOCK_LOADED: bool = False


def _ensure_inline_mock() -> None:
    global _INLINE_ACTIVE_ID, _INLINE_ACTIVE_URI, _INLINE_BUFFER, _INLINE_CURSOR_POS
    global _INLINE_CAND_IDX, _INLINE_ERRORS, _INLINE_MOCK_LOADED
    if _INLINE_MOCK_LOADED:
        return
    _INLINE_MOCK_LOADED = True
    raw = os.environ.get("NC_INLINE_MOCK_JSON", "")
    if raw:
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                _INLINE_ACTIVE_ID = str(d.get("active_id", "") or "")
                _INLINE_ACTIVE_URI = str(d.get("active_uri", "") or "")
                _INLINE_BUFFER = str(d.get("buffer", "") or "")
                _INLINE_CURSOR_POS = int(d.get("cursor_pos", len(_INLINE_BUFFER)))
                _INLINE_CAND_IDX = int(d.get("cand_idx", -1))
                errs = d.get("errors", {})
                if isinstance(errs, dict):
                    _INLINE_ERRORS = {str(k): str(v) for k, v in errs.items()}
        except Exception:
            pass


def get_active_inline_entry() -> str:
    _ensure_inline_mock()
    return _INLINE_ACTIVE_ID


def get_active_inline_uri() -> str:
    _ensure_inline_mock()
    return _INLINE_ACTIVE_URI


def get_inline_entry_buffer() -> str:
    _ensure_inline_mock()
    return _INLINE_BUFFER


def get_inline_cursor_pos() -> int:
    _ensure_inline_mock()
    return _INLINE_CURSOR_POS


def get_inline_candidate_index(entry_id: str = "") -> int:
    _ensure_inline_mock()
    if entry_id and _INLINE_CAND_OWNER and _INLINE_CAND_OWNER != entry_id:
        return -1
    return _INLINE_CAND_IDX


def set_inline_candidates(entry_id: str, candidates: list[tuple[str, str]]) -> None:
    global _INLINE_CANDIDATES, _INLINE_CAND_OWNER, _INLINE_CAND_IDX
    _INLINE_CAND_OWNER = str(entry_id or "")
    _INLINE_CANDIDATES = list(candidates or [])
    if _INLINE_CAND_IDX >= len(_INLINE_CANDIDATES):
        _INLINE_CAND_IDX = len(_INLINE_CANDIDATES) - 1 if _INLINE_CANDIDATES else -1


def get_inline_entry_error(entry_id: str) -> str:
    _ensure_inline_mock()
    return str(_INLINE_ERRORS.get(entry_id, "") or "")


def set_inline_entry_error(entry_id: str, msg: str) -> None:
    _ensure_inline_mock()
    if msg:
        _INLINE_ERRORS[entry_id] = str(msg)
    else:
        _INLINE_ERRORS.pop(entry_id, None)


def _stop_inline_blink_timer() -> None:
    global _INLINE_TIMER_ID
    if _INLINE_TIMER_ID is not None:
        try:
            from gi.repository import GLib
            GLib.source_remove(_INLINE_TIMER_ID)
        except Exception:
            pass
        _INLINE_TIMER_ID = None


def _stop_inline_refresh() -> None:
    global _INLINE_REFRESH_ID, _INLINE_REFRESH_SHELL
    if _INLINE_REFRESH_ID is not None:
        try:
            from gi.repository import GLib
            GLib.source_remove(_INLINE_REFRESH_ID)
        except Exception:
            pass
        _INLINE_REFRESH_ID = None
    _INLINE_REFRESH_SHELL = None


def _flush_inline_refresh() -> bool:
    global _INLINE_REFRESH_ID, _INLINE_REFRESH_SHELL
    sh = _INLINE_REFRESH_SHELL
    _INLINE_REFRESH_ID = None
    _INLINE_REFRESH_SHELL = None
    if sh is not None and _INLINE_ACTIVE_ID:
        sh.refresh_view()
    return False


def _schedule_inline_refresh(shell) -> None:
    global _INLINE_REFRESH_ID, _INLINE_REFRESH_SHELL
    _INLINE_REFRESH_SHELL = shell
    if _INLINE_REFRESH_ID is None:
        try:
            from gi.repository import GLib
            _INLINE_REFRESH_ID = GLib.idle_add(_flush_inline_refresh, priority=GLib.PRIORITY_HIGH_IDLE)
        except Exception:
            _INLINE_REFRESH_ID = None
            if shell is not None:
                shell.refresh_view()


def _on_inline_blink_tick() -> bool:
    global _INLINE_BLINK_ON, _INLINE_TIMER_ID
    if not _INLINE_ACTIVE_ID:
        _INLINE_TIMER_ID = None
        return False
    _INLINE_BLINK_ON = not _INLINE_BLINK_ON
    if _INLINE_SHELL is not None:
        _INLINE_SHELL.refresh_view()
    return True


def _clear_inline_state() -> None:
    global _INLINE_ACTIVE_ID, _INLINE_ACTIVE_URI, _INLINE_BUFFER, _INLINE_CURSOR_POS
    global _INLINE_CANDIDATES, _INLINE_CAND_OWNER, _INLINE_TAB_CACHE, _INLINE_CAND_IDX, _INLINE_BLINK_ON
    _INLINE_ACTIVE_ID = ""
    _INLINE_ACTIVE_URI = ""
    _INLINE_BUFFER = ""
    _INLINE_CURSOR_POS = 0
    _INLINE_CANDIDATES = []
    _INLINE_CAND_OWNER = ""
    _INLINE_TAB_CACHE = []
    _INLINE_CAND_IDX = -1
    _INLINE_BLINK_ON = True
    _stop_inline_blink_timer()
    _stop_inline_refresh()


def cancel_inline_entry(refresh: bool = False) -> bool:
    _ensure_inline_mock()
    entry_id = _INLINE_ACTIVE_ID
    if not entry_id:
        return False
    meta = _INLINE_HANDLERS.get(entry_id, {})
    _clear_inline_state()
    if _INLINE_SHELL is not None and hasattr(_INLINE_SHELL, "release_inline_gtk_entry"):
        _INLINE_SHELL.release_inline_gtk_entry()
    on_cancel = meta.get("on_cancel")
    if callable(on_cancel):
        on_cancel()
    if refresh and _INLINE_SHELL is not None:
        _INLINE_SHELL.refresh_view()
    return True


def start_inline_input(
    entry_id: str,
    shell=None,
    trigger_uri: str = "",
    initial_text: str = "",
    max_len: int = 64,
    validator=None,
    on_change=None,
    on_commit=None,
    on_cancel=None,
    on_invalid=None,
    completion=None,
    is_picker: bool = False,
    strip_leading_hash: bool = False,
    uppercase: bool = False,
) -> None:
    global _INLINE_ACTIVE_ID, _INLINE_ACTIVE_URI, _INLINE_BUFFER, _INLINE_CURSOR_POS
    global _INLINE_CANDIDATES, _INLINE_CAND_OWNER, _INLINE_TAB_CACHE, _INLINE_CAND_IDX
    global _INLINE_BLINK_ON, _INLINE_TIMER_ID, _INLINE_SHELL
    _ensure_inline_mock()

    prev_id = _INLINE_ACTIVE_ID
    if prev_id and prev_id != entry_id:
        prev_cancel = _INLINE_HANDLERS.get(prev_id, {}).get("on_cancel")
        if callable(prev_cancel):
            prev_cancel()

    ml = max(1, int(max_len))
    init_s = str(initial_text or "")
    if strip_leading_hash and init_s.startswith("#"):
        init_s = init_s[1:]
    if uppercase:
        init_s = init_s.upper()
    init_s = init_s[:ml]

    _INLINE_ACTIVE_ID = str(entry_id)
    _INLINE_ACTIVE_URI = str(trigger_uri or "")
    _INLINE_BUFFER = init_s
    _INLINE_CURSOR_POS = len(init_s)
    _INLINE_CANDIDATES = []
    _INLINE_CAND_OWNER = ""
    _INLINE_TAB_CACHE = []
    _INLINE_CAND_IDX = -1
    set_inline_entry_error(entry_id, "")
    _INLINE_HANDLERS[entry_id] = {
        "max_len": ml,
        "initial_text": init_s,
        "validator": validator,
        "on_change": on_change,
        "on_commit": on_commit,
        "on_cancel": on_cancel,
        "on_invalid": on_invalid,
        "completion": completion,
        "is_picker": bool(is_picker),
        "strip_leading_hash": bool(strip_leading_hash),
        "uppercase": bool(uppercase),
    }
    _INLINE_BLINK_ON = True
    if shell is not None:
        _INLINE_SHELL = shell
    target_shell = shell or _INLINE_SHELL
    if target_shell is not None:
        if hasattr(target_shell, "prompt_bar") and target_shell.prompt_bar.get_visible():
            target_shell.hide_prompt()
        if hasattr(target_shell, "sync_inline_gtk_entry"):
            target_shell.sync_inline_gtk_entry(init_s)
    _stop_inline_blink_timer()
    if os.environ.get("NC_UI_OFFSCREEN") != "1":
        try:
            from gi.repository import GLib
            _INLINE_TIMER_ID = GLib.timeout_add(500, _on_inline_blink_tick)
        except Exception:
            _INLINE_TIMER_ID = None
    if target_shell is not None:
        target_shell.refresh_view()


def start_inline_entry(
    entry_id: str,
    shell=None,
    max_len: int = 6,
    validator=None,
    on_commit=None,
    on_invalid=None,
    strip_leading_hash: bool = True,
    uppercase: bool = True,
    initial_text: str = "",
    trigger_uri: str = "",
) -> None:
    """Wrapper retrocompatibile su `start_inline_input` (commit solo con Invio)."""
    start_inline_input(
        entry_id=entry_id,
        shell=shell,
        trigger_uri=trigger_uri,
        initial_text=initial_text,
        max_len=max_len,
        validator=validator,
        on_commit=on_commit,
        on_invalid=on_invalid,
        strip_leading_hash=strip_leading_hash,
        uppercase=uppercase,
    )


def sync_inline_from_gtk_entry(raw_text: str, cursor_pos: int, shell=None) -> None:
    global _INLINE_BUFFER, _INLINE_CURSOR_POS, _INLINE_TAB_CACHE, _INLINE_CAND_IDX, _INLINE_BLINK_ON
    entry_id = _INLINE_ACTIVE_ID
    if not entry_id:
        return
    meta = _INLINE_HANDLERS.get(entry_id, {})
    ml = int(meta.get("max_len", 64))
    txt = str(raw_text or "")
    pos = max(0, int(cursor_pos))
    if meta.get("strip_leading_hash") and txt.startswith("#"):
        txt = txt[1:]
        pos = max(0, pos - 1)
    if meta.get("uppercase"):
        txt = txt.upper()
    if len(txt) > ml:
        txt = txt[:ml]
        pos = min(pos, ml)

    new_pos = min(pos, len(txt))
    text_changed = (txt != _INLINE_BUFFER)
    cursor_changed = (new_pos != _INLINE_CURSOR_POS)
    target_shell = shell or _INLINE_SHELL
    if not text_changed and not cursor_changed:
        if txt != raw_text and target_shell is not None and hasattr(target_shell, "sync_inline_gtk_entry"):
            target_shell.sync_inline_gtk_entry(txt, cursor_pos=new_pos, grab_focus=False)
        return

    _INLINE_BUFFER = txt
    _INLINE_CURSOR_POS = new_pos
    _INLINE_BLINK_ON = True
    set_inline_entry_error(entry_id, "")

    if txt != raw_text and target_shell is not None and hasattr(target_shell, "sync_inline_gtk_entry"):
        target_shell.sync_inline_gtk_entry(txt, cursor_pos=_INLINE_CURSOR_POS, grab_focus=False)

    if text_changed:
        _INLINE_TAB_CACHE = []
        _INLINE_CAND_IDX = -1
        on_change = meta.get("on_change")
        if callable(on_change):
            on_change(txt)

    if target_shell is not None:
        if os.environ.get("NC_UI_OFFSCREEN") == "1":
            target_shell.refresh_view()
        else:
            _schedule_inline_refresh(target_shell)


def commit_active_inline_entry(shell=None) -> bool:
    entry_id = _INLINE_ACTIVE_ID
    if not entry_id:
        return False
    meta = _INLINE_HANDLERS.get(entry_id, {})
    raw_val = _INLINE_BUFFER
    target_shell = shell or _INLINE_SHELL
    cands = list(_INLINE_CANDIDATES) if _INLINE_CAND_OWNER == entry_id else []
    cidx = _INLINE_CAND_IDX

    if meta.get("is_picker") and cands:
        eff_i = cidx if (0 <= cidx < len(cands)) else 0
        _lbl, picked_target = cands[eff_i]
        _clear_inline_state()
        if target_shell is not None and hasattr(target_shell, "release_inline_gtk_entry"):
            target_shell.release_inline_gtk_entry()
        if target_shell is not None and picked_target:
            target_shell.dispatch_uri(picked_target)
            return True
        on_commit = meta.get("on_commit")
        if callable(on_commit):
            on_commit(picked_target)
        if target_shell is not None:
            target_shell.refresh_view()
        return True

    validator = meta.get("validator")
    err_msg = ""
    if callable(validator):
        vres = validator(raw_val)
        if isinstance(vres, tuple):
            is_valid = bool(vres[0])
            err_msg = str(vres[1] or "") if len(vres) > 1 else ""
        else:
            is_valid = bool(vres)
    else:
        is_valid = True

    if not is_valid:
        if err_msg:
            set_inline_entry_error(entry_id, err_msg)
        on_invalid = meta.get("on_invalid")
        if callable(on_invalid):
            on_invalid(raw_val)
        if target_shell is not None:
            target_shell.refresh_view()
        return True

    _clear_inline_state()
    set_inline_entry_error(entry_id, "")
    if target_shell is not None and hasattr(target_shell, "release_inline_gtk_entry"):
        target_shell.release_inline_gtk_entry()
    on_commit = meta.get("on_commit")
    if callable(on_commit):
        on_commit(raw_val)
    if target_shell is not None:
        target_shell.refresh_view()
    return True


def step_inline_completion(reverse: bool = False, delta_arrow: int | None = None, shell=None) -> bool:
    global _INLINE_BUFFER, _INLINE_CURSOR_POS, _INLINE_TAB_CACHE, _INLINE_CAND_IDX, _INLINE_BLINK_ON
    entry_id = _INLINE_ACTIVE_ID
    if not entry_id:
        return False
    meta = _INLINE_HANDLERS.get(entry_id, {})
    target_shell = shell or _INLINE_SHELL

    if meta.get("is_picker"):
        cands = _INLINE_CANDIDATES if _INLINE_CAND_OWNER == entry_id else []
        if not cands:
            return True
        step = delta_arrow if delta_arrow is not None else (-1 if reverse else 1)
        if _INLINE_CAND_IDX < 0:
            _INLINE_CAND_IDX = (len(cands) - 1) if step < 0 else (1 if len(cands) > 1 and delta_arrow is None else 0)
        else:
            _INLINE_CAND_IDX = (_INLINE_CAND_IDX + step) % len(cands)
        _INLINE_BLINK_ON = True
        if target_shell is not None:
            target_shell.refresh_view()
        return True

    if delta_arrow is not None:
        return False

    provider = meta.get("completion")
    if provider is None:
        return True

    from core.completion import step_tab_completion

    new_txt, new_cache, new_idx, _hint = step_tab_completion(
        _INLINE_BUFFER,
        provider,
        _INLINE_TAB_CACHE,
        _INLINE_CAND_IDX,
        reverse=reverse,
    )
    ml = int(meta.get("max_len", 64))
    new_txt = new_txt[:ml]
    _INLINE_TAB_CACHE = new_cache
    _INLINE_CAND_IDX = new_idx
    if new_txt != _INLINE_BUFFER:
        _INLINE_BUFFER = new_txt
        _INLINE_CURSOR_POS = len(new_txt)
        _INLINE_BLINK_ON = True
        if target_shell is not None and hasattr(target_shell, "sync_inline_gtk_entry"):
            target_shell.sync_inline_gtk_entry(new_txt, cursor_pos=_INLINE_CURSOR_POS, grab_focus=False)
        on_change = meta.get("on_change")
        if callable(on_change):
            on_change(new_txt)
    if target_shell is not None:
        target_shell.refresh_view()
    return True


def handle_inline_entry_key_press(event, shell=None) -> bool:
    """Fallback per test offscreen / eventi tastiera instradati fuori dal widget Entry."""
    _ensure_inline_mock()
    if not _INLINE_ACTIVE_ID:
        return False
    from gi.repository import Gdk
    kv = event.keyval
    state = event.state
    is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
    target_shell = shell or _INLINE_SHELL

    if kv == Gdk.KEY_Escape:
        cancel_inline_entry(refresh=True)
        return True
    if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
        return commit_active_inline_entry(shell=target_shell)
    if kv in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab, Gdk.KEY_ISO_Left_Tab):
        rev = is_shift or (kv == Gdk.KEY_ISO_Left_Tab)
        return step_inline_completion(reverse=rev, shell=target_shell)
    if kv in (Gdk.KEY_Up, Gdk.KEY_KP_Up, Gdk.KEY_Down, Gdk.KEY_KP_Down):
        return step_inline_completion(delta_arrow=(-1 if kv in (Gdk.KEY_Up, Gdk.KEY_KP_Up) else 1), shell=target_shell)
    return False


def InlineField(
    entry_id: str,
    uri: str,
    value: str = "",
    placeholder: str = "",
    empty_label: str = "",
    width: int = 12,
    icon: str = "\u270e",
    tone: str = "neutral",
    active: bool = False,
    dirty: bool = False,
    is_hex_color: bool = False,
    selected: bool = False,
    min_chars: int = 0,
) -> str:
    """
    Campo scalare inline standardizzato:
      - A riposo: bottone `[✎ valore]` (o pillola `[● #RRGGBB]` per HEX selezionato).
      - A fuoco: box editabile `▕ valore│ ▏` in `intent` (o `fault` su errore) con commit su Invio.
    """
    p = get_semantic_palette()
    asc = THEME["ascii"]
    b_l, b_r = _pair(asc, "button_brackets", "[", "]")

    if get_active_inline_entry() == entry_id:
        s_l, s_r = _pair(asc, "search_brackets", "\u258f", "\u2595")
        s_cur = str(asc.get("search_cursor") or "\u2502")[0]
        has_err = bool(get_inline_entry_error(entry_id))
        bg = p["fault_bg"] if has_err else p["intent_bg"]
        brk = p["fault"] if has_err else p["intent"]
        cur_col = p["fault"] if has_err else p["intent"]
        eff_w = max(6, int(width))
        body_markup = _render_editable_slice(
            get_inline_entry_buffer(),
            get_inline_cursor_pos(),
            placeholder,
            eff_w,
            _INLINE_BLINK_ON,
            s_cur,
            p["focus"],
            cur_col,
            p["inactive"],
            bg,
        )
        return (
            f"<a href='{Canvas.escape(uri)}'>"
            f"<span color='{brk}' background='{bg}' underline='none'>{Canvas.escape(s_l)}</span>"
            f"{body_markup}"
            f"<span color='{brk}' background='{bg}' underline='none'>{Canvas.escape(s_r)}</span>"
            f"</a>"
        )

    if selected and value:
        val_str = str(value).strip()
        if is_hex_color:
            val_str = ("#" + val_str.lstrip("#")).upper()
        # MEMENTO (§3.1.1): Quando un InlineField rappresenta il valore custom attivo tra
        # SelectPill preset, usa SEMPRE il ruolo cromatico della selezione (`intent` su `intent_bg`,
        # o `attention` su `attention_bg` se dirty), evitando contrasto illeggibile con tinte scure/rosse.
        fg = p["attention"] if dirty else p["intent"]
        dots = _pair(asc, "pill_dots", "\u25cf", "\u25cb")
        brk = p["attention_soft"] if dirty else p["intent_soft"]
        bg = p["attention_bg"] if dirty else p["intent_bg"]
        clean = Canvas.escape(val_str.replace(" ", "\u00a0"))
        return (
            f"<a href='{Canvas.escape(uri)}'>"
            f"<span color='{brk}' background='{bg}' underline='none'>{Canvas.escape(b_l)}</span>"
            f"<span color='{fg}' background='{bg}' underline='none'>{dots[0]}\u00a0{clean}</span>"
            f"<span color='{brk}' background='{bg}' underline='none'>{Canvas.escape(b_r)}</span>"
            f"</a>"
        )

    disp = str(value).strip() or str(empty_label).strip()
    lbl = f"{icon} {disp}".strip() if icon else disp
    # MEMENTO (§3.1.1 / §3.2.1): Un InlineField non selezionato come preset attivo è un semplice
    # comando di apertura input -> ZERO sfondo (`active=False`) e ZERO ciano (`tone='select'` -> `'neutral'`).
    rest_tone = "warn" if dirty else ("neutral" if tone in ("select", "intent", "accent", "info") else tone)
    return ActionBtn(
        lbl,
        uri,
        tone=rest_tone,
        min_chars=min_chars,
    )


def InlineEntry(
    entry_id: str,
    uri: str,
    label: str = "Custom",
    placeholder: str = "custom",
    max_len: int = 6,
    active_value: str = "",
    selected: bool = False,
    dirty: bool = False,
    is_hex_color: bool = True,
) -> str:
    """Wrapper retrocompatibile su `InlineField`."""
    return InlineField(
        entry_id=entry_id,
        uri=uri,
        value=active_value if (selected and active_value) else "",
        placeholder=placeholder,
        empty_label=label,
        width=max(8, int(max_len) + 2),
        icon="",
        tone="neutral",
        active=False,
        dirty=dirty,
        is_hex_color=is_hex_color,
        selected=selected,
    )


class Divider:
    is_divider = True

    def __init__(self, title: str = "", icon: str = "", align: str = "right"):
        self.title = title
        self.icon = icon
        self.align = align or "right"
        self.elected = False
        self.highlighted = False
        self.selected = False
        self.scroll_action = None
        self._forced_cols = None

    def compute_required_cols(self, canvas: Canvas, base_cols: int = None) -> int:
        base = base_cols if base_cols is not None else canvas.total_cols
        if not self.title:
            return base
        full_title = f"{self.icon}  {Canvas.escape(self.title)}" if self.icon else Canvas.escape(self.title)
        return max(base, canvas.measure_cols(full_title) + 12)

    def render(self, canvas: Canvas, forced_cols: int = None) -> str:
        prev_cols = canvas.total_cols
        try:
            p = get_semantic_palette()
            canvas.set_cols(forced_cols or self._forced_cols or self.compute_required_cols(canvas, prev_cols))
            full_title = f"{self.icon}  {Canvas.escape(self.title)}" if self.icon else Canvas.escape(self.title)
            return canvas.divider_line(full_title, c_line=p["structure"], c_txt=p["schema"], align=self.align)
        finally:
            canvas.set_cols(prev_cols)


class Box:
    is_divider = False

    def __init__(
        self,
        title: str,
        icon: str = "",
        actions: list = None,
        elected: bool = False,
        highlighted: bool = False,
        selected: bool = False,
        scroll_action: str = None,
        title_is_markup: bool = False,
        width_mode: str = "auto",
        fixed_width: bool = False,
        min_cols: int = None,
        max_cols: int = 160,
        wrap: bool = True,
        open_top: bool = False,
        open_bottom: bool = False,
        is_overlay: bool = False,
    ):
        self.title = title
        self.icon = icon
        self.actions = [a for a in (actions or []) if a]
        self.elected = bool(elected)
        self.highlighted = bool(highlighted)
        self.selected = bool(selected)
        self.scroll_action = scroll_action
        self.title_is_markup = title_is_markup
        self.width_mode = "fixed" if fixed_width else width_mode
        self.min_cols = min_cols
        self.max_cols = max_cols
        self.wrap = wrap
        self.open_top = bool(open_top)
        self.open_bottom = bool(open_bottom)
        self.is_overlay = bool(is_overlay)
        self.fill_window = False
        self.min_height_px = 0
        self._forced_cols = None
        self._render_total_px = 0.0
        self._top_protrusions = []
        self._top_protrusion_bytes = []
        self.bottom_label = ""
        self.bottom_actions = []
        self.bottom_right_actions = []
        self._bot_protrusions = []
        self._bot_protrusion_bytes = []
        self.side_emblem = None
        self._ops = []

    def set_bottom_bar(self, label: str = "", actions: list = None, right_actions: list = None):
        """Imposta un'etichetta e/o azioni cliccabili sul bordo inferiore del Box."""
        self.bottom_label = str(label or "")
        self.bottom_actions = [a for a in (actions or []) if a]
        self.bottom_right_actions = [a for a in (right_actions or []) if a]
        return self

    def set_side_emblem(self, icon: str, label: str = "", role: str = "intent", max_rows: float = 6.0):
        ic = str(icon or "").strip()
        if not ic:
            self.side_emblem = None
        else:
            self.side_emblem = {
                "icon": ic,
                "label": str(label or "").strip(),
                "role": str(role or "intent"),
                "max_rows": float(max_rows),
            }
        return self

    def _check_deps(self, requires: list) -> tuple[bool, str]:
        if not requires:
            return True, ""
        from core import probe
        missing = [b for b in requires if not probe.has_bin(b)]
        if not missing:
            return True, ""
        if get_hide_unavailable_settings():
            return False, ""
        return True, Badge(t("ui.missing", p0=missing[0]), style="warn")

    def add_row(self, left_markup: str, right_markup: str = "", requires: list = None):
        ok, badge = self._check_deps(requires)
        if not ok:
            return self
        rm = str(right_markup or "")
        if badge:
            rm = f"{rm}  {badge}" if rm else badge
        self._ops.append(("row", str(left_markup or ""), rm, False))
        return self

    def add_kv(self, key: str, value_markup: str, key_width: int = None, right_markup: str = "", icon: str = "", requires: list = None):
        ok, badge = self._check_deps(requires)
        if not ok:
            return self
        rm = str(right_markup or "")
        if badge:
            rm = f"{rm}  {badge}" if rm else badge
        self._ops.append((
            "kv",
            str(key or ""),
            str(value_markup or ""),
            key_width,
            rm,
            str(icon or ""),
        ))
        return self

    def add_section(self, label: str = "", actions: list = None, label_is_markup: bool = False, requires: list = None):
        ok, badge = self._check_deps(requires)
        if not ok:
            return self
        clean_acts = [a for a in (actions or []) if a]
        if badge:
            clean_acts.append(badge)
        # MEMENTO GOLDEN STANDARD (§5.3.1): Vietato inserire una sezione statica come prima
        # operazione di un Box già dotato di titolo sul bordo superiore (creerebbe un doppio header).
        if not self._ops and not self.open_top and self.title and not label_is_markup and not clean_acts:
            return self
        self._ops.append(("section", str(label or ""), clean_acts, label_is_markup))
        return self

    def add_collapsible(self, panel, state_key: str, label: str, build_fn, actions: list = None, label_is_markup: bool = False, requires: list = None, summary: str = ""):
        ok, badge = self._check_deps(requires)
        if not ok:
            return self

        p = get_semantic_palette()
        is_open = bool(panel.get_state(state_key, False))
        toggle_uri = f"ui:state:{state_key}:{'false' if is_open else 'true'}"

        lbl_m = label if label_is_markup else f"<span color='{p['schema']}'>{Canvas.escape(label)}</span>"
        all_acts = list(actions or [])
        if badge:
            all_acts.append(badge)
        # MEMENTO (§2.2 / §4.1.2 / §4.1.7): Controllo espandibile standard allineato a destra
        # `[▶ Expand]` / `[▼ Collapse]` con icona 85% gialla (`attention`), zero sfondo e MAI Ciano `intent`!
        all_acts.append(FoldIconBtn(is_open, toggle_uri))

        self.add_section(lbl_m, actions=all_acts, label_is_markup=True)
        if is_open:
            build_fn(self)
        elif summary:
            self.add_row(f"  {Txt(summary, role='schema')}")
        return self

    def add_tabs(self, active_tab: str, tabs: list, uri_prefix: str = "ui:tab", min_tab_cols: int = 18):
        self._ops.append(("tabs", active_tab, list(tabs or []), uri_prefix, int(min_tab_cols)))
        return self

    def add_split(self, *columns: list, ratios: list = None, junctions: bool = False):
        self._ops.append(("split", list(columns), ratios, junctions))
        return self

    def add_table(
        self,
        headers: list,
        widths: list,
        rows: list,
        aligns: list = None,
        uniform_row_height: bool = True,
        show_headers: bool = True,
        requires: list = None,
    ):
        ok, _badge = self._check_deps(requires)
        if not ok:
            return self
        self._ops.append((
            "table",
            list(headers or []),
            list(widths or []),
            list(rows or []),
            aligns,
            bool(uniform_row_height),
            bool(show_headers),
        ))
        return self

    def add_text_block(self, plain_text: str, color: str = ""):
        self._ops.append(("text_block", str(plain_text or ""), color))
        return self

    def add_search_box(
        self,
        label: str,
        query: str,
        placeholder: str,
        trigger_uri: str,
        results: list,
        clear_uri: str = "",
        active: bool = False,
        max_pills_per_row: int = 3,
        right_markup: str = "",
        width: int = 34,
        entry_id: str = "",
        show_empty: bool = True,
    ):
        is_focused = bool((entry_id and get_active_inline_entry() == entry_id) or active)
        res_list = list(results or [])
        if entry_id and get_active_inline_entry() == entry_id:
            set_inline_candidates(entry_id, res_list)
        eff_right = right_markup
        if is_focused and not eff_right and show_empty:
            eff_right = Txt(t("ui.inline_picker_hint"), role="inactive")

        self.add_kv(
            label,
            InlineFilter(
                entry_id=entry_id,
                query=query,
                placeholder=placeholder,
                trigger_uri=trigger_uri,
                clear_uri=clear_uri,
                width=width,
                active=is_focused,
            ),
            right_markup=eff_right,
        )
        if res_list:
            cand_idx = get_inline_candidate_index(entry_id)
            eff_cand = cand_idx if (0 <= cand_idx < len(res_list)) else (0 if is_focused else -1)
            step = max(1, max_pills_per_row)
            for i in range(0, len(res_list), step):
                chunk = res_list[i : i + step]
                row_pills = " ".join(
                    ActionBtn(
                        f"\u25b8 + {lbl}" if (is_focused and (i + off) == eff_cand) else f"+ {lbl}",
                        uri,
                        tone="apply",
                        _kb_focus=(is_focused and (i + off) == eff_cand),
                    )
                    for off, (lbl, uri) in enumerate(chunk)
                )
                self.add_kv("", row_pills)
        elif show_empty and (str(query or "").strip() or is_focused):
            self.add_kv("", Txt(t("ui.search_no_results"), role="inactive"))
        return self

    add_inline_picker = add_search_box

    def _effective_mode(self) -> str:
        if self.width_mode in ("fixed", "dynamic"):
            return self.width_mode
        for op in self._ops:
            kind = op[0]
            if kind in ("section", "split", "table", "kv", "tabs"):
                return "dynamic"
            if kind == "row":
                _, _, right_m, is_struct = op
                if is_struct or right_m:
                    return "dynamic"
        return "fixed"

    @staticmethod
    def _resolve_table_geometry(canvas: Canvas, headers: list, widths: list, rows: list, avail_inner_cols: int, expand: bool = True) -> list[int]:
        n_cols = len(headers) if headers else (max((len(r) for r in rows), default=0))
        if n_cols == 0:
            return []

        norm_widths = (list(widths) + ["flex"] * max(0, n_cols - len(widths)))[:n_cols]
        col_content_max = [1] * n_cols
        col_atomic_min = [1] * n_cols

        for i in range(n_cols):
            if i < len(headers) and headers[i]:
                h_str = str(headers[i])
                h_m = h_str if ("<" in h_str and ">" in h_str) else Canvas.escape(h_str)
                col_content_max[i] = max(1, canvas.measure_cols(h_m))

        for r in rows:
            non_empty = [j for j in range(min(n_cols, len(r))) if canvas.strip_tags(str(r[j])).strip()]
            for c_idx in range(min(n_cols, len(r))):
                cell_s = str(r[c_idx])
                if not canvas.strip_tags(cell_s).strip():
                    continue
                spans_trailing = (c_idx < n_cols - 1) and all(j <= c_idx for j in non_empty)
                cw = canvas.measure_cols(cell_s)
                if not spans_trailing and cw > col_content_max[c_idx]:
                    col_content_max[c_idx] = cw
                atoms = [canvas.measure_cols(m.group(0)) for m in _ATOMIC_WIDGET_RE.finditer(cell_s)]
                if atoms:
                    am = max(atoms)
                    if (len(atoms) == 1 and cw == am) or cw <= 26:
                        col_atomic_min[c_idx] = max(col_atomic_min[c_idx], cw)
                    else:
                        col_atomic_min[c_idx] = max(col_atomic_min[c_idx], am)

        sep_spaces = (n_cols - 1) * 2
        avail = max(n_cols, avail_inner_cols - sep_spaces)
        resolved = [max(col_content_max[i], col_atomic_min[i]) for i in range(n_cols)]
        tot = sum(resolved)

        if tot <= avail:
            extra = avail - tot
            for i, w in enumerate(norm_widths):
                if isinstance(w, int) and w > resolved[i] and extra > 0:
                    add = min(extra, int(w) - resolved[i])
                    resolved[i] += add
                    extra -= add
            if extra > 0:
                flex_indices = [i for i, w in enumerate(norm_widths) if w == "flex"]
                if flex_indices:
                    per_flex = extra // len(flex_indices)
                    for idx in flex_indices:
                        resolved[idx] += per_flex
                    resolved[flex_indices[-1]] += extra - (per_flex * len(flex_indices))
                else:
                    resolved[-1] += extra
        else:
            overflow = tot - avail
            wrappable = [i for i in range(n_cols) if resolved[i] > max(col_atomic_min[i], 20)]
            for idx in sorted(wrappable, key=lambda i: resolved[i], reverse=True):
                if overflow <= 0:
                    break
                floor_w = max(col_atomic_min[idx], 16)
                take = min(overflow, max(0, resolved[idx] - floor_w))
                resolved[idx] -= take
                overflow -= take
            flex_indices = [i for i, w in enumerate(norm_widths) if w == "flex"]
            shrink_order = flex_indices + [i for i in range(n_cols - 1, -1, -1) if i not in flex_indices]
            for idx in shrink_order:
                if overflow <= 0:
                    break
                floor_w = max(col_atomic_min[idx], 4)
                take = min(overflow, max(0, resolved[idx] - floor_w))
                resolved[idx] -= take
                overflow -= take
        return resolved

    def _resolve_palette(self) -> tuple[str, str, str, str]:
        """
        Restituisce `(c_border, c_title, c_brackets, c_section_header)`:
          - Cornice Bianca (`#ffffff`) SOLO se `highlighted=True` (Header/Tab Box) o `selected=True` (hover mouse).
          - Cornice Grafite (`#43495c`) per tutti gli altri box.
          - I titoli di `add_section` sono sempre in Grigio Ardesia (`#8c9bba`), mai verdi.
        """
        p = get_semantic_palette()
        c_focus = p["focus"]
        c_struct = p["structure"]
        c_schema = p["schema"]

        if self.highlighted or self.selected:
            return c_focus, c_focus, c_schema, c_schema

        return c_struct, c_focus, c_struct, c_schema

    @staticmethod
    def _partition_border_actions(canvas: Canvas, title_markup: str, actions: list, max_total_cols: int, overhead_cols: int = 7) -> tuple[list, list]:
        if not actions:
            return [], []
        title_c = canvas.measure_cols(title_markup)
        avail_for_acts = max_total_cols - title_c - overhead_cols
        if avail_for_acts <= 2:
            return [], list(actions)

        all_c = sum(canvas.measure_cols(a) for a in actions) + max(0, len(actions) - 1)
        if all_c <= avail_for_acts:
            return list(actions), []

        # Regola All-or-Nothing per ActionBtn (<a ...>): i badge non-link restano sul bordo, tutti i bottoni vanno sotto
        border_acts = []
        overflow_acts = []
        used_c = 0
        for act in actions:
            is_btn = act.lstrip().lower().startswith("<a ")
            if is_btn:
                overflow_acts.append(act)
                continue
            act_c = canvas.measure_cols(act) + (1 if border_acts else 0)
            if used_c + act_c <= avail_for_acts:
                border_acts.append(act)
                used_c += act_c
            else:
                overflow_acts.append(act)
        return border_acts, overflow_acts

    def _format_bottom_bar(self, c_sec_hdr: str, canvas: Canvas = None, max_total_cols: int = 0) -> tuple[str, str]:
        bl_parts = []
        raw_lbl = self.bottom_label
        acts_m = " ".join(self.bottom_actions)
        bot_right = " ".join(self.bottom_right_actions)
        if raw_lbl and canvas is not None and max_total_cols > 0:
            acts_cols = (canvas.measure_cols(acts_m) + 1) if acts_m else 0
            right_cols = (canvas.measure_cols(bot_right) + 3) if bot_right else 0
            avail = max_total_cols - acts_cols - right_cols - 7
            if avail > 3 and canvas.measure_cols(Canvas.escape(raw_lbl)) > avail:
                raw_lbl = canvas.truncate_plain(raw_lbl, avail)
        if raw_lbl:
            bl_parts.append(f"<span color='{c_sec_hdr}'>{Canvas.escape(raw_lbl)}</span>")
        if acts_m:
            bl_parts.append(acts_m)
        return " ".join(bl_parts), bot_right

    def compute_required_cols(self, canvas: Canvas, base_cols: int = None) -> int:
        base = base_cols if base_cols is not None else canvas.total_cols
        mode = self._effective_mode()
        min_total = self.min_cols if self.min_cols is not None else base

        if mode == "fixed":
            return min(min_total, self.max_cols)

        title_str = self.title if self.title_is_markup else Canvas.escape(self.title)
        full_title = f"{self.icon}  {title_str}" if self.icon else title_str
        right_hdr = " ".join(self.actions) if self.actions else ""

        hdr_inner = canvas.measure_cols(full_title) + (canvas.measure_cols(right_hdr) + 3 if right_hdr else 0) + 6
        req_total = max(min_total, hdr_inner + 4)
        req_inner = req_total - 4
        _, _, _, c_sec_hdr = self._resolve_palette()
        cfg_kw = max(14, min(32, int(THEME["geometry"].get("default_key_width", DEFAULT_KEY_WIDTH))))
        base_kw = COMPACT_KEY_WIDTH if base <= 50 else cfg_kw
        kv_sep = str(THEME["ascii"].get("kv_separator", ":"))[:2]
        box_kw = max(
            [base_kw]
            + [
                max(len(f"{op[5]} {op[1]}{kv_sep}" if op[5] else f"{op[1]}{kv_sep}"), int(op[3] or 0))
                for op in self._ops
                if op[0] == "kv" and op[1]
            ]
        )

        emb_res = 22 if self.side_emblem else 0
        for op_idx, op in enumerate(self._ops):
            kind = op[0]
            res_cols = emb_res if (emb_res and op_idx < 7) else 0
            if kind == "row":
                _, left_m, right_m, _ = op
                for line in left_m.split("\n"):
                    l_cols = canvas.measure_cols(line)
                    r_cols = (canvas.measure_cols(right_m) + 2) if right_m else 0
                    req_inner = max(req_inner, l_cols + r_cols + (res_cols if line.strip() else 0))

            elif kind == "kv":
                _, key, val_m, kw, right_m, icon = op
                eff_kw = max(kw, box_kw) if kw is not None else box_kw
                l_cols = 2 + eff_kw + 1 + canvas.measure_cols(val_m)
                r_cols = (canvas.measure_cols(right_m) + 2) if right_m else 0
                req_inner = max(req_inner, l_cols + r_cols + res_cols)

            elif kind == "tabs":
                _, act_t, tabs_l, upref, min_tc = op
                t_rows = format_tab_bar_rows(canvas, tabs_l, act_t, uri_prefix=upref, min_tab_cols=min_tc)
                for tr in t_rows:
                    req_inner = max(req_inner, canvas.measure_cols(tr))

            elif kind == "section":
                _, lbl, acts, is_mk = op
                lbl_m = lbl if is_mk else (f"<span color='{c_sec_hdr}'>{Canvas.escape(lbl)}</span>" if lbl else "")
                right_m = " ".join(acts) if acts else ""
                sec_cols = canvas.measure_cols(lbl_m) + (canvas.measure_cols(right_m) + 3 if right_m else 0) + 3
                req_inner = max(req_inner, sec_cols)

            elif kind == "split":
                _, cols_list, _ratios, _ = op
                n_cols = len(cols_list)
                if n_cols == 0:
                    continue
                col_mins = [
                    max((canvas.measure_cols(cell) for cell in col), default=1)
                    for col in cols_list
                ]
                sep_overhead = (n_cols - 1) * 3
                req_inner = max(req_inner, sum(col_mins) + sep_overhead)

            elif kind == "table":
                headers, widths, rows = op[1], op[2], op[3]
                n_cols = len(headers) if headers else (max((len(r) for r in rows), default=0))
                if n_cols == 0:
                    continue
                resolved = self._resolve_table_geometry(canvas, headers, widths, rows, req_inner - 2, expand=True)
                sep_spaces = (n_cols - 1) * 2
                req_inner = max(req_inner, sum(resolved) + sep_spaces + 2)

        if self.bottom_label or self.bottom_actions or self.bottom_right_actions:
            bot_left, bot_right = self._format_bottom_bar(c_sec_hdr)
            bot_cols = canvas.measure_cols(bot_left) + (canvas.measure_cols(bot_right) + 3 if bot_right else 0) + 4
            req_inner = max(req_inner, bot_cols)

        return min(self.max_cols, max(req_total, req_inner + 4))

    def render(self, canvas: Canvas, forced_cols: int = None) -> str:
        prev_cols = canvas.total_cols
        try:
            target_cols = forced_cols or self._forced_cols or self.compute_required_cols(canvas, prev_cols)
            canvas.set_cols(target_cols)

            p = get_semantic_palette()
            c_pri = p["focus"]
            c_sec = p["schema"]
            c_brd, c_title, c_brk, c_sec_hdr = self._resolve_palette()

            raw_title = self.title
            title_str = raw_title if self.title_is_markup else Canvas.escape(raw_title)
            full_title = f"{self.icon}  {title_str}" if self.icon else title_str

            border_acts, overflow_acts = self._partition_border_actions(
                canvas, full_title, self.actions, target_cols, overhead_cols=8
            )
            right_hdr = " ".join(border_acts) if border_acts else ""

            b_l, b_r = canvas.title_brackets
            brk_cols = canvas.measure_cols(b_l) + canvas.measure_cols(b_r)
            fixed_hdr_cols = (9 if right_hdr else 5) + brk_cols
            max_title_cols = target_cols - (canvas.measure_cols(right_hdr) if right_hdr else 0) - fixed_hdr_cols
            cur_plain_title = raw_title
            if not self.title_is_markup and canvas.measure_cols(full_title) > max_title_cols:
                ic_len = (canvas.measure_cols(self.icon) + 2) if self.icon else 0
                cur_plain_title = canvas.truncate_plain(raw_title, max(4, max_title_cols - ic_len))
                full_title = f"{self.icon}  {Canvas.escape(cur_plain_title)}" if self.icon else Canvas.escape(cur_plain_title)

            out_lines = []
            self._render_total_px = float(canvas.total_px)
            self._top_protrusions = []
            self._top_protrusion_bytes = []
            if not self.open_top:
                top_line = canvas.top(full_title, right_markup=right_hdr, c_box=c_brd, c_txt=c_title, c_brk=c_brk)
                if not self.title_is_markup and raw_title and canvas.measure_markup(top_line) > canvas.total_px + 0.5:
                    lim = min(len(raw_title), len(cur_plain_title)) - 1
                    while lim >= 4 and canvas.measure_markup(top_line) > canvas.total_px + 0.5:
                        cur_plain_title = canvas.truncate_plain(raw_title, lim)
                        full_title = f"{self.icon}  {Canvas.escape(cur_plain_title)}" if self.icon else Canvas.escape(cur_plain_title)
                        top_line = canvas.top(full_title, right_markup=right_hdr, c_box=c_brd, c_txt=c_title, c_brk=c_brk)
                        lim -= 1
                out_lines.append(top_line)
                ch = canvas.chars
                top_plain = Canvas.strip_tags(top_line)
                top_bytes = top_plain.encode("utf-8")
                if full_title:
                    b_l, b_r = canvas.title_brackets
                    left_lead = f"<span color='{c_brd}'>{ch['tl']}{ch['h']} </span>"
                    title_blk = (
                        f"<span color='{c_brk}'>{canvas.escape(b_l)}</span>"
                        f"<span color='{c_title}'>{canvas.sanitize_markup(full_title)}</span>"
                        f"<span color='{c_brk}'>{canvas.escape(b_r)}</span>"
                    )
                    x_t0 = canvas.measure_markup(left_lead)
                    x_t1 = canvas.measure_markup(left_lead + title_blk)
                    if x_t1 > x_t0:
                        self._top_protrusions.append((x_t0, x_t1))
                    lead_p = f"{ch['tl']}{ch['h']} "
                    title_p = f"{b_l}{Canvas.strip_tags(full_title)}{b_r}"
                    bt0 = len(lead_p.encode("utf-8"))
                    bt1 = bt0 + len(title_p.encode("utf-8"))
                    if bt1 > bt0 and bt1 <= len(top_bytes):
                        self._top_protrusion_bytes.append((bt0, bt1))
                if right_hdr:
                    right_tail = f"<span color='{c_brd}'> {ch['h']}{ch['tr']}</span>"
                    line_w = canvas.measure_markup(top_line)
                    tail_w = canvas.measure_markup(right_tail)
                    rhdr_w = canvas.measure_markup(canvas.sanitize_markup(right_hdr))
                    x_r1 = max(0.0, line_w - tail_w)
                    x_r0 = max(0.0, x_r1 - rhdr_w)
                    if x_r1 > x_r0:
                        self._top_protrusions.append((x_r0, x_r1))
                    tail_p = f" {ch['h']}{ch['tr']}"
                    rhdr_p = Canvas.strip_tags(right_hdr)
                    br1 = max(0, len(top_bytes) - len(tail_p.encode("utf-8")))
                    br0 = max(0, br1 - len(rhdr_p.encode("utf-8")))
                    if br1 > br0:
                        self._top_protrusion_bytes.append((br0, br1))

            if overflow_acts:
                tb_right = " ".join(overflow_acts)
                if canvas.measure_markup(tb_right) <= canvas.inner_px - canvas.space_w * 3:
                    out_lines.append(canvas.row(" ", right_markup=tb_right, c_box=c_brd, c_txt=c_pri))
                else:
                    for w_tb in canvas.wrap_markup(tb_right, canvas.inner_px - canvas.space_w * 2):
                        out_lines.append(canvas.row(" ", right_markup=w_tb.strip(), c_box=c_brd, c_txt=c_pri))
                out_lines.append(canvas.mid("", c_box=c_brd))

            is_dynamic = (self._effective_mode() == "dynamic")
            cfg_kw = max(14, min(32, int(THEME["geometry"].get("default_key_width", DEFAULT_KEY_WIDTH))))
            base_kw = COMPACT_KEY_WIDTH if target_cols <= 50 else cfg_kw
            kv_sep = str(THEME["ascii"].get("kv_separator", ":"))[:2]
            t_rule = str(THEME["ascii"].get("table_rule") or "\u2500")[0]
            default_kw = max(
                [base_kw]
                + [
                    max(len(f"{op[5]} {op[1]}{kv_sep}" if op[5] else f"{op[1]}{kv_sep}"), int(op[3] or 0))
                    for op in self._ops
                    if op[0] == "kv" and op[1]
                ]
            )

            def _is_blank_op(op) -> bool:
                return op[0] == "row" and not Canvas.strip_tags(op[1]).strip() and not Canvas.strip_tags(op[2]).strip()

            # MEMENTO SPAZIATURA INTERNA (Golden Standard §5.3.1):
            # Ogni Box strutturato mantiene 1 riga vuota di respiro sotto il bordo superiore
            # e sopra il bordo inferiore (anche nei popup Waybar <= 64 colonne!).
            # Fanno eccezione solo le micro-card compatte di lista (`<= 2` righe semplici in popup stretti,
            # es. singole voci di `wp_clipboard` / `wp_notifications`) o `compact=True`.
            is_micro_card = bool(getattr(self, "compact", False)) or (
                target_cols <= 64
                and len(self._ops) <= 2
                and all(op[0] in ("row", "text_block") for op in self._ops)
            )

            if self._ops and not is_micro_card and not self.open_top and not _is_blank_op(self._ops[0]) and self._ops[0][0] != "tabs":
                out_lines.append(canvas.row(" ", c_box=c_brd, c_txt=c_pri))

            for op_idx, op in enumerate(self._ops):
                kind = op[0]

                if kind == "row":
                    _, left_m, right_m, _ = op
                    stripped_plain = Canvas.strip_tags(left_m)
                    lead_spaces = len(stripped_plain) - len(stripped_plain.lstrip(" "))
                    indent_str = " " * min(lead_spaces, 6) if stripped_plain.strip() else ""

                    if right_m:
                        r_px = canvas.measure_markup(right_m)
                        avail_px = max(canvas.char_w * 6, canvas.inner_px - r_px - canvas.space_w)
                    else:
                        avail_px = canvas.inner_px

                    if self.wrap and ("\n" in left_m or canvas.measure_markup(left_m) > avail_px + 0.5):
                        wrapped = canvas.wrap_markup(left_m, avail_px)
                        if right_m and canvas.measure_markup(wrapped[0]) > avail_px + 0.5:
                            for idx, w_line in enumerate(canvas.wrap_markup(left_m, canvas.inner_px)):
                                if idx > 0 and indent_str and not Canvas.strip_tags(w_line).startswith(" "):
                                    w_line = indent_str + w_line
                                out_lines.append(canvas.row(w_line, c_box=c_brd, c_txt=c_pri))
                            out_lines.append(canvas.row("  ", right_markup=right_m, c_box=c_brd, c_txt=c_pri))
                        else:
                            for idx, w_line in enumerate(wrapped):
                                if idx > 0 and indent_str and not Canvas.strip_tags(w_line).startswith(" "):
                                    w_line = indent_str + w_line
                                r_part = right_m if idx == 0 else ""
                                out_lines.append(canvas.row(w_line, right_markup=r_part, c_box=c_brd, c_txt=c_pri))
                    else:
                        out_lines.append(canvas.row(left_m, right_markup=right_m, c_box=c_brd, c_txt=c_pri))

                elif kind == "kv":
                    _, key, val_m, kw, right_m, icon = op
                    eff_kw = max(kw, default_kw) if kw is not None else default_kw
                    prefix = f"{icon} " if icon else ""
                    k_raw = f"{prefix}{key}{kv_sep}" if key else ""
                    k_padded = k_raw.ljust(eff_kw)
                    key_prefix = f"  <span color='{c_sec}'>{Canvas.escape(k_padded)}</span> "
                    hang_prefix = "  " + (" " * eff_kw) + " "
                    prefix_px = canvas.measure_markup(key_prefix)

                    r_px = canvas.measure_markup(right_m) if right_m else 0.0
                    emb_px = (canvas.char_w * 14.0) if (self.side_emblem and op_idx < 7) else 0.0
                    avail_val_px = canvas.inner_px - prefix_px - emb_px - (r_px + canvas.space_w * 2 if right_m else 0.0)

                    right_overflow_below = False
                    if right_m and avail_val_px < (canvas.char_w * 14):
                        right_overflow_below = True
                        avail_val_px = max(canvas.char_w * 10, canvas.inner_px - prefix_px)

                    if self.wrap and ("\n" in val_m or canvas.measure_markup(val_m) > avail_val_px + 1.0):
                        val_lines = canvas.wrap_markup(val_m, max(canvas.char_w * 8, avail_val_px))
                    else:
                        val_lines = [val_m]

                    for idx, vl in enumerate(val_lines):
                        left_part = (key_prefix + vl) if idx == 0 else (hang_prefix + vl)
                        r_part = right_m if (idx == 0 and not right_overflow_below) else ""
                        out_lines.append(canvas.row(left_part, right_markup=r_part, c_box=c_brd, c_txt=c_pri))

                    if right_overflow_below and right_m:
                        out_lines.append(canvas.row(hang_prefix + right_m, c_box=c_brd, c_txt=c_pri))

                elif kind == "text_block":
                    _, plain_text, color = op
                    col_hex = p.get(color, THEME["colors"].get(color, color)) if color else c_pri
                    escaped = f"  <span color='{col_hex}'>{Canvas.escape(plain_text)}</span>"
                    for idx, w_line in enumerate(canvas.wrap_markup(escaped, canvas.inner_px - canvas.space_w * 2)):
                        if idx > 0 and not Canvas.strip_tags(w_line).startswith(" "):
                            w_line = "  " + w_line
                        out_lines.append(canvas.row(w_line, c_box=c_brd, c_txt=c_pri))

                elif kind == "section":
                    _, lbl, acts, is_mk = op
                    if op_idx > 0 and not _is_blank_op(self._ops[op_idx - 1]) and self._ops[op_idx - 1][0] != "section":
                        out_lines.append(canvas.row(" ", c_box=c_brd, c_txt=c_pri))
                    lbl_m = lbl if is_mk else (f"<span color='{c_sec_hdr}'>{Canvas.escape(lbl)}</span>" if lbl else "")
                    sec_border_acts, sec_overflow_acts = self._partition_border_actions(
                        canvas, lbl_m, acts, target_cols, overhead_cols=9
                    )
                    right_m = " ".join(sec_border_acts) if sec_border_acts else ""
                    out_lines.append(canvas.mid(lbl_m, right_markup=right_m, c_box=c_brd))
                    if sec_overflow_acts:
                        tb_s = "  " + "  ".join(sec_overflow_acts)
                        for w_tb in canvas.wrap_markup(tb_s, canvas.inner_px):
                            line_tb = w_tb if w_tb.startswith("  ") else ("  " + w_tb.lstrip())
                            out_lines.append(canvas.row(line_tb, c_box=c_brd, c_txt=c_pri))
                    if op_idx + 1 < len(self._ops) and not _is_blank_op(self._ops[op_idx + 1]) and self._ops[op_idx + 1][0] != "section":
                        out_lines.append(canvas.row(" ", c_box=c_brd, c_txt=c_pri))

                elif kind == "tabs":
                    _, act_t, tabs_l, upref, min_tc = op
                    t_rows = format_tab_bar_rows(canvas, tabs_l, act_t, uri_prefix=upref, min_tab_cols=min_tc)
                    out_lines.append(canvas.row(" ", c_box=c_brd, c_txt=c_pri))
                    for t_idx, tr in enumerate(t_rows):
                        if t_idx > 0:
                            out_lines.append(canvas.row(" ", c_box=c_brd, c_txt=c_pri))
                        out_lines.append(canvas.row(tr, c_box=c_brd, c_txt=c_pri))
                    out_lines.append(canvas.row(" ", c_box=c_brd, c_txt=c_pri))

                elif kind == "split":
                    _, cols_list, ratios, junctions = op
                    n_cols = len(cols_list)
                    if n_cols == 0:
                        continue
                    col_mins = [
                        max((canvas.measure_cols(cell) for cell in col), default=1)
                        for col in cols_list
                    ]
                    col_widths = canvas.compute_split_widths(n_cols, ratios, min_widths=col_mins)
                    if junctions:
                        out_lines.append(canvas.split_junction_line(col_widths, kind="top", c_box=c_brd))
                    max_rows = max((len(c) for c in cols_list), default=0)
                    for r_idx in range(max_rows):
                        row_cells = [
                            cols_list[c_idx][r_idx] if r_idx < len(cols_list[c_idx]) else ""
                            for c_idx in range(n_cols)
                        ]
                        out_lines.append(canvas.split_row(row_cells, col_widths, c_box=c_brd))
                    if junctions:
                        out_lines.append(canvas.split_junction_line(col_widths, kind="bot", c_box=c_brd))

                elif kind == "table":
                    headers, widths, rows = op[1], op[2], op[3]
                    aligns_raw = op[4] if len(op) > 4 else None
                    uniform_h = op[5] if len(op) > 5 else True
                    show_hdr = op[6] if len(op) > 6 else True
                    n_cols = len(headers) if headers else (max((len(r) for r in rows), default=0))
                    if n_cols == 0:
                        continue
                    aligns = list(aligns_raw or (["left"] * n_cols)) + ["left"] * max(0, n_cols - len(aligns_raw or []))
                    table_inner_cols = max(n_cols * 2, canvas.inner_cols - 2)
                    resolved_w = self._resolve_table_geometry(canvas, headers, widths, rows, table_inner_cols, expand=True)

                    if show_hdr and headers:
                        hdr_formatted = []
                        for h in headers:
                            h_str = str(h)
                            if "<" in h_str and ">" in h_str:
                                hdr_formatted.append(h_str)
                            else:
                                hdr_formatted.append(f"<span color='{c_sec}'>{Canvas.escape(h_str)}</span>")
                        hdr_line = canvas.format_table_row(hdr_formatted, resolved_w, aligns, truncate=not is_dynamic)
                        out_lines.append(canvas.row(hdr_line, c_box=c_brd))
                        out_lines.append(canvas.row("  " + f"<span color='{p['structure']}'>{t_rule * table_inner_cols}</span>", c_box=c_brd))

                    rendered_rows = []
                    for r in rows:
                        norm_r = [str(r[i]) if i < len(r) else "" for i in range(n_cols)]
                        sub_cols = []
                        for c_idx in range(n_cols):
                            cell_s = norm_r[c_idx]
                            eff_w = resolved_w[c_idx]
                            for nxt in range(c_idx + 1, n_cols):
                                if not canvas.strip_tags(norm_r[nxt]).strip():
                                    eff_w += resolved_w[nxt] + 2
                                else:
                                    break
                            budget_px = eff_w * canvas.char_w
                            if self.wrap and _ATOMIC_WIDGET_RE.search(cell_s) and canvas.measure_markup(cell_s) > budget_px + 0.5:
                                sub_cols.append(canvas.wrap_markup(cell_s, budget_px))
                            else:
                                sub_cols.append([cell_s])
                        n_sub = max((len(sc) for sc in sub_cols), default=1)
                        for s_i in range(n_sub):
                            sub_r = [sc[s_i] if s_i < len(sc) else "" for sc in sub_cols]
                            row_str = canvas.format_table_row(sub_r, resolved_w, aligns, truncate=True)
                            rendered_rows.append(row_str)

                    max_row_h = max((canvas.measure_line_height(rs) for rs in rendered_rows), default=0) if uniform_h else 0
                    for row_str in rendered_rows:
                        out_lines.append(canvas.row(row_str, c_box=c_brd, min_height_pango=max_row_h))

            if self._ops and not is_micro_card and not self.open_bottom and not _is_blank_op(self._ops[-1]) and self._ops[-1][0] != "tabs":
                out_lines.append(canvas.row(" ", c_box=c_brd, c_txt=c_pri))

            if self.min_height_px > 0 and not self.open_bottom:
                from gi.repository import Pango
                std_row_h = getattr(canvas, "_std_row_h_px", 0)
                if std_row_h <= 0:
                    sample_r = canvas.row(" ", c_box=c_brd, c_txt=c_pri)
                    std_row_h = max(12, int(round(canvas.measure_line_height(sample_r) / Pango.SCALE)))
                    canvas._std_row_h_px = std_row_h
                base_h = int(canvas.base_row_h_px)
                cur_h = base_h + sum(std_row_h if "\u200b" in ln else base_h for ln in out_lines)
                blank_row = canvas.row(" ", c_box=c_brd, c_txt=c_pri)
                while cur_h + std_row_h <= self.min_height_px:
                    out_lines.append(blank_row)
                    cur_h += std_row_h

            self._bot_protrusions = []
            self._bot_protrusion_bytes = []
            if not self.open_bottom:
                bot_left, bot_right = self._format_bottom_bar(c_sec_hdr, canvas=canvas, max_total_cols=target_cols)
                bot_line = canvas.bot(label_markup=bot_left, right_markup=bot_right, c_box=c_brd)
                out_lines.append(bot_line)
                if bot_left or bot_right:
                    ch = canvas.chars
                    bot_plain = Canvas.strip_tags(bot_line)
                    bot_bytes = bot_plain.encode("utf-8")
                    if bot_left:
                        lead_m = f"<span color='{c_brd}'>{ch['bl']}{ch['h']} </span>"
                        lbl_m = canvas.sanitize_markup(bot_left)
                        xb0 = canvas.measure_markup(lead_m)
                        xb1 = canvas.measure_markup(lead_m + lbl_m)
                        if xb1 > xb0:
                            self._bot_protrusions.append((xb0, xb1))
                        lead_p = f"{ch['bl']}{ch['h']} "
                        lbl_p = Canvas.strip_tags(bot_left)
                        bb0 = len(lead_p.encode("utf-8"))
                        bb1 = bb0 + len(lbl_p.encode("utf-8"))
                        if bb1 > bb0 and bb1 <= len(bot_bytes):
                            self._bot_protrusion_bytes.append((bb0, bb1))
                    if bot_right:
                        tail_m = f"<span color='{c_brd}'> {ch['h']}{ch['br']}</span>"
                        bline_w = canvas.measure_markup(bot_line)
                        tail_w = canvas.measure_markup(tail_m)
                        rbot_w = canvas.measure_markup(canvas.sanitize_markup(bot_right))
                        xr1 = max(0.0, bline_w - tail_w)
                        xr0 = max(0.0, xr1 - rbot_w)
                        if xr1 > xr0:
                            self._bot_protrusions.append((xr0, xr1))
                        tail_p = f" {ch['h']}{ch['br']}"
                        rbot_p = Canvas.strip_tags(bot_right)
                        bbr1 = max(0, len(bot_bytes) - len(tail_p.encode("utf-8")))
                        bbr0 = max(0, bbr1 - len(rbot_p.encode("utf-8")))
                        if bbr1 > bbr0:
                            self._bot_protrusion_bytes.append((bbr0, bbr1))
            return "\n".join(out_lines)
        finally:
            canvas.set_cols(prev_cols)


def FeedbackBox(title: str, ok: bool, msg: str) -> Box:
    """Card uniforme Golden Standard per feedback esito operazioni negli engine."""
    b = Box(
        title,
        icon="\uf00c" if ok else "\uf071",
        actions=[StateBadge(True, t("common.ok")) if ok else Badge(t("common.err"), style="crit")],
        highlighted=False,
    )
    b._toast_meta = {"title": str(title), "ok": bool(ok), "msg": str(msg)}
    b.add_kv(t("common.status"), Txt(str(msg), role="val" if ok else "fault"))
    return b


def sync_boxes_width(items: list, canvas: Canvas, base_cols: int = None) -> int:
    base = base_cols if base_cols is not None else canvas.total_cols
    models = []
    for it in (items or []):
        if isinstance(it, (Box, Divider)):
            models.append(it)
        else:
            bm = getattr(it, "box_model", None)
            if isinstance(bm, (Box, Divider)):
                models.append(bm)
    if not models:
        return base
    shared_cols = max(m.compute_required_cols(canvas, base) for m in models)
    for m in models:
        m._forced_cols = shared_cols
    return shared_cols
