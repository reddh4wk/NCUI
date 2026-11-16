#!/usr/bin/env python3
from core.config import THEME, color_for_pct, get_hide_unavailable_settings
from ui.canvas import Canvas, _ATOMIC_WIDGET_RE

DEFAULT_KEY_WIDTH = 20
COMPACT_KEY_WIDTH = 12


# =====================================================================
# SORGENTE SEMANTICA UNICA DEI COLORI (7 Assi Semantici)
# =====================================================================
def get_semantic_palette() -> dict[str, str]:
    c = THEME.get("colors", {})
    th = c.get("thresholds", {})
    return {
        # 1. Tono Grafite — INERZIA & CONTENIMENTO
        "structure":      c.get("structure", "#43495c"),
        "structure_bg":   c.get("structure_bg", "#1a1c23"),
        "inactive":       c.get("inactive", c.get("muted", "#5f657b")),
        "muted":          c.get("inactive", c.get("muted", "#5f657b")),
        # 2. Tono Ardesia — DESCRIZIONE & SCHEMA
        "schema":         c.get("schema", c.get("secondary", "#8c9bba")),
        "dim":            c.get("schema", c.get("secondary", "#8c9bba")),
        # 3. Tono Bianco — PRESENZA & DATO OGGETTIVO
        "focus":          c.get("focus", c.get("primary", "#ffffff")),
        "pri":            c.get("focus", c.get("primary", "#ffffff")),
        "focus_val":      c.get("focus_val", "#e2eafc"),
        "val":            c.get("focus_val", "#e2eafc"),
        # 4. Tono Ciano & Blu Cobalto — INTENZIONE UTENTE (UI)
        "intent":         c.get("intent", c.get("accent", "#00f0ff")),
        "sel_fg":         c.get("intent", c.get("accent", "#00f0ff")),
        "intent_soft":    c.get("intent_soft", "#4db8c4"),
        "sel_soft":       c.get("intent_soft", "#4db8c4"),
        "intent_bg":      c.get("intent_bg", "#002a3a"),
        "sel_bg":         c.get("intent_bg", "#002a3a"),
        # 5. Tono Verde Matrix — OPERATIVITÀ SISTEMA (HW/OS)
        "vitality":       c.get("vitality", th.get("normal", "#00ff73")),
        "ok_fg":          c.get("vitality", th.get("normal", "#00ff73")),
        "vitality_soft":  c.get("vitality_soft", "#4fac73"),
        "ok_soft":        c.get("vitality_soft", "#4fac73"),
        "vitality_bg":    c.get("vitality_bg", "#002813"),
        "ok_bg":          c.get("vitality_bg", "#002813"),
        # 6. Tono Giallo Fluorescente — MUTAZIONE & ATTENZIONE
        "attention":      c.get("attention", th.get("warning", "#ffeb19")),
        "dirty_fg":       c.get("attention", th.get("warning", "#ffeb19")),
        "attention_soft": c.get("attention_soft", "#c7b848"),
        "dirty_soft":     c.get("attention_soft", "#c7b848"),
        "attention_bg":   c.get("attention_bg", "#332d00"),
        "dirty_bg":       c.get("attention_bg", "#332d00"),
        # 7a. Tono Rosa Antico -> Fucsia — INTERRUZIONE VOLONTARIA
        "interrupt":      c.get("interrupt", th.get("alert", "#ff2a9d")),
        "interrupt_rest": c.get("interrupt_rest", "#bd5b86"),
        "crit_soft":      c.get("interrupt_rest", "#bd5b86"),
        "interrupt_bg":   c.get("interrupt_bg", "#3a091d"),
        # 7b. Tono Rosso Allarme — ANOMALIA / GUASTO SUBÌTO
        "fault":          c.get("fault", th.get("critical", "#ff213f")),
        "crit_fg":        c.get("fault", th.get("critical", "#ff213f")),
        "fault_soft":     c.get("fault_soft", "#c94f5f"),
        "fault_bg":       c.get("fault_bg", "#36050b"),
        "crit_bg":        c.get("fault_bg", "#36050b"),
    }


PALETTE = get_semantic_palette()


# =====================================================================
# COSTRUTTORI SEMANTICI CENTRALIZZATI (Unica API per tutti i pannelli)
# =====================================================================
def Txt(text: str, role: str = "focus", bg: bool = False) -> str:
    """
    Formatta una stringa secondo il ruolo semantico dichiarato:
      - 'focus' / 'pri' : Bianco Puro (#ffffff)
      - 'val'           : Bianco Cristallo (#e2eafc)
      - 'schema' / 'dim': Grigio Ardesia (#8c9bba)
      - 'inactive'      : Grigio Grafite Spento (#5f657b)
      - 'intent' / 'sel': Ciano Elettrico (#00f0ff)
      - 'vitality'/'ok' : Verde Matrix (#00ff73)
      - 'ok_soft'       : Verde Fosforo (#4fac73)
      - 'attention'     : Giallo Fluorescente (#ffeb19)
      - 'interrupt'     : Fucsia Elettrico (#ff2a9d)
      - 'fault' / 'crit': Rosso Allarme (#ff213f)
    """
    p = get_semantic_palette()
    role_map = {
        "focus":          (p["focus"], ""),
        "pri":            (p["focus"], ""),
        "val":            (p["focus_val"], ""),
        "schema":         (p["schema"], ""),
        "dim":            (p["schema"], ""),
        "structure":      (p["structure"], p["structure_bg"] if bg else ""),
        "inactive":       (p["inactive"], ""),
        "muted":          (p["inactive"], ""),
        "intent":         (p["intent"], p["intent_bg"] if bg else ""),
        "sel":            (p["intent"], p["intent_bg"] if bg else ""),
        "intent_soft":    (p["intent_soft"], ""),
        "vitality":       (p["vitality"], p["vitality_bg"] if bg else ""),
        "ok":             (p["vitality"], p["vitality_bg"] if bg else ""),
        "ok_soft":        (p["vitality_soft"], ""),
        "attention":      (p["attention"], p["attention_bg"] if bg else ""),
        "dirty":          (p["attention"], p["attention_bg"] if bg else ""),
        "warn":           (p["attention"], p["attention_bg"] if bg else ""),
        "interrupt":      (p["interrupt"], p["interrupt_bg"] if bg else ""),
        "interrupt_rest": (p["interrupt_rest"], ""),
        "fault":          (p["fault"], p["fault_bg"] if bg else ""),
        "crit":           (p["fault"], p["fault_bg"] if bg else ""),
    }
    fg_col, bg_col = role_map.get(role, (p["focus"], ""))
    bg_attr = f" background='{bg_col}'" if bg_col else ""
    return f"<span color='{fg_col}'{bg_attr}>{Canvas.escape(str(text))}</span>"


def SelectPill(label: str, uri: str, selected: bool = False, dirty: bool = False) -> str:
    """
    Selettore di opzione/filtro:
      - Selezionato (modificato nel buffer): Giallo Fluorescente (#ffeb19) su #332d00.
      - Selezionato (salvato): Ciano Elettrico (#00f0ff) su Blu Cobalto (#002a3a).
      - Non selezionato: Grigio Grafite Spento (#5f657b), zero sfondo.
    """
    p = get_semantic_palette()
    b_l, b_r = THEME["ascii"]["button_brackets"]
    clean = Canvas.escape(str(label).strip().replace(" ", "\u00a0"))
    if selected and dirty:
        fg, bg, brk, dot = p["attention"], p["attention_bg"], p["attention_soft"], "\u25cf\u00a0"
    elif selected:
        fg, bg, brk, dot = p["intent"], p["intent_bg"], p["intent_soft"], "\u25cf\u00a0"
    else:
        fg, bg, brk, dot = p["inactive"], "", p["structure"], "\u25cb\u00a0"

    bg_attr = f" background='{bg}'" if bg else ""
    return (
        f"<a href='{Canvas.escape(uri)}'>"
        f"<span color='{brk}'{bg_attr} underline='none'>{Canvas.escape(b_l)}</span>"
        f"<span color='{fg}'{bg_attr} underline='none'>{dot}{clean}</span>"
        f"<span color='{brk}'{bg_attr} underline='none'>{Canvas.escape(b_r)}</span>"
        f"</a>"
    )


def StateBadge(is_on: bool, on_text: str, off_text: str = "OFF", on_tone: str = "ok") -> str:
    """
    Badge semantico dello stato di un componente:
      - 'ok'      : Verde Matrix (#00ff73) su #002813 (es. UP, ACTIVE, DEFAULT, FTTH).
      - 'ok_soft' : Verde Fosforo (#4fac73) senza sfondo.
      - 'warn'    : Giallo Fluorescente (#ffeb19) su #332d00 (es. Failover, Unsaved, Open AP).
      - 'crit'    : Rosso Allarme (#ff213f) su #36050b (es. TIMEOUT, ERROR).
      - 'select'  : Ciano Elettrico (#00f0ff) su #002a3a.
      - is_on=False: Grigio Grafite Spento (#5f657b) senza sfondo (es. DOWN, STANDBY).
    """
    p = get_semantic_palette()
    if is_on:
        clean_on = Canvas.escape(str(on_text).strip().replace(" ", "\u00a0"))
        if on_tone == "ok_soft":
            return f"<span color='{p['vitality_soft']}'>\u25cf\u00a0{clean_on}</span>"
        if on_tone == "select":
            fg, bg = p["intent"], p["intent_bg"]
        elif on_tone in ("warn", "attention", "dirty"):
            fg, bg = p["attention"], p["attention_bg"]
        elif on_tone in ("crit", "fault", "danger"):
            fg, bg = p["fault"], p["fault_bg"]
        else:
            fg, bg = p["vitality"], p["vitality_bg"]
        return f"<span color='{fg}' background='{bg}'>\u00a0\u25cf\u00a0{clean_on}\u00a0</span>"

    clean_off = Canvas.escape(str(off_text).strip().replace(" ", "\u00a0"))
    return f"<span color='{p['inactive']}'>\u25cb\u00a0{clean_off}</span>"


def ActionBtn(label: str, uri: str, tone: str = "neutral", active: bool = False, brackets: bool = True) -> str:
    """
    Pulsante operativo con parentesi in Tono Grafite (#43495c) e testo semantico:
      - 'neutral'             : Grigio Ardesia (#8c9bba) per Edit, Copy, Clone, Rename.
      - 'select' / 'intent'   : Ciano Elettrico (#00f0ff) (con fondo Blu Cobalto se active=True).
      - 'apply' / 'vitality'  : Verde Matrix (#00ff73) per Up, Conn, Apply & Verify.
      - 'warn' / 'attention'  : Giallo Fluorescente (#ffeb19) per Reset, Confirm.
      - 'danger_soft'         : Rosa Antico (#bd5b86) a riposo per Down, Disc, Off, [x], Power Off;
                                diventa Fucsia Elettrico (#ff2a9d) se active=True.
      - 'danger' / 'fault'    : Fucsia Elettrico (#ff2a9d) / Rosso Allarme (#ff213f).
    """
    p = get_semantic_palette()
    b_l, b_r = THEME["ascii"]["button_brackets"] if brackets else ("", "")
    clean = Canvas.escape(str(label).strip().replace(" ", "\u00a0"))
    bg = ""
    brk = p["structure"]

    if tone in ("select", "intent", "accent", "info"):
        if active:
            fg, bg, brk = p["intent"], p["intent_bg"], p["intent_soft"]
        else:
            fg = p["intent"] if tone in ("accent", "info") else p["schema"]
    elif tone in ("apply", "vitality", "ok"):
        if active:
            fg, bg, brk = p["vitality"], p["vitality_bg"], p["vitality_soft"]
        else:
            fg = p["vitality"]
    elif tone in ("warn", "attention"):
        if active:
            fg, bg, brk = p["attention"], p["attention_bg"], p["attention_soft"]
        else:
            fg = p["attention"]
    elif tone in ("danger_soft", "interrupt", "alert"):
        if active:
            fg, bg, brk = p["interrupt"], p["interrupt_bg"], p["interrupt"]
        else:
            fg = p["interrupt_rest"]
    elif tone in ("danger", "crit", "fault"):
        if active:
            fg, bg, brk = p["fault"], p["fault_bg"], p["fault_soft"]
        else:
            fg = p["interrupt"]
    elif tone in ("primary", "focus"):
        fg = p["focus"]
    elif tone == "muted":
        fg = p["inactive"]
    else:
        fg = p["focus_val"] if active else p["schema"]

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
    Cella Master-Detail di prima colonna (es. selezione interfaccia o profilo):
      - Selezionata per ispezione: Ciano Elettrico (#00f0ff) su Blu Cobalto (#002a3a) con '▸'.
      - Non selezionata ma viva  : Bianco Puro (#ffffff).
      - Non selezionata e spenta : Grigio Grafite Spento (#5f657b).
    """
    p = get_semantic_palette()
    ic_part = f"{icon}\u00a0" if icon else ""
    clean = Canvas.escape(str(label).strip().replace(" ", "\u00a0"))
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


def TabBar(tabs: list[tuple[str, str]], active_tab: str, uri_prefix: str = "ui:tab", min_tab_cols: int = 18) -> str:
    """
    Costruisce una barra di navigazione a Tab larghe e ben distanziate:
      - Tab attiva  : Ciano Elettrico (#00f0ff) su fondo Blu Cobalto (#002a3a) con indicatore '●'.
      - Tab inattive: Grigio Ardesia (#8c9bba) su fondo Grafite Scuro (#1a1c23) con indicatore '○'.
    """
    p = get_semantic_palette()
    rendered = []
    for t_id, t_lbl in tabs:
        is_act = (t_id == active_tab)
        dot = "\u25cf" if is_act else "\u25cb"
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
            f"<span color='{brk}' background='{bg}' underline='none'>\u258f</span>"
            f"<span color='{fg}' background='{bg}' underline='none'>{padded_lbl}</span>"
            f"<span color='{brk}' background='{bg}' underline='none'>\u2595</span>"
            f"</a>"
        )
    return "  ".join(rendered)


def FoldToggle(panel, sec_key: str, default: bool = False, is_it: bool = True, dirty: bool = False) -> tuple[bool, str]:
    opened = bool(panel.get_state("open_sections", {}).get(sec_key, default)) or dirty
    uri = f"net:toggle_sec:{sec_key}"
    if opened:
        lbl = "\u25be Riduci" if is_it else "\u25be Collapse"
        return True, ActionBtn(lbl, uri, tone="select", active=True)
    lbl = "\u25b8 Espandi" if is_it else "\u25b8 Expand"
    return False, ActionBtn(lbl, uri, tone="warn" if dirty else "neutral", active=dirty)


# --- Alias per piena compatibilità con tutti i moduli esistenti ---
ui_pill = SelectPill
ui_act_btn = ActionBtn
ui_status_badge = StateBadge
ui_fold_toggle = FoldToggle


def Btn(label: str, action: str, style: str = "accent", active: bool = False, brackets: bool = True) -> str:
    style_to_tone = {
        "accent":    "select" if active else "apply",
        "info":      "select",
        "primary":   "primary",
        "secondary": "select" if active else "neutral",
        "muted":     "select" if active else "muted",
        "warn":      "warn",
        "alert":     "danger_soft",
        "crit":      "danger_soft",
    }
    tone = style_to_tone.get(style, "neutral")
    return ActionBtn(label, action, tone=tone, active=active, brackets=brackets)


def ToggleExpandBtn(is_expanded: bool) -> str:
    return ActionBtn("-" if is_expanded else "+", "ui:toggle_expand", tone="select", active=is_expanded)


def StateToggleBtn(label_on: str, label_off: str, state_key: str, current_val: bool, style: str = "secondary") -> str:
    next_val = "false" if current_val else "true"
    lbl = label_on if current_val else label_off
    return SelectPill(lbl, f"ui:state:{state_key}:{next_val}", selected=current_val)


def Badge(text: str, style: str = "accent") -> str:
    p = get_semantic_palette()
    clean = Canvas.escape(str(text).strip().replace(" ", "\u00a0"))
    if style in ("accent", "ok", "vitality"):
        return f"<span color='{p['vitality']}' background='{p['vitality_bg']}'>\u00a0{clean}\u00a0</span>"
    if style in ("info", "select", "intent"):
        return f"<span color='{p['intent']}' background='{p['intent_bg']}'>\u00a0{clean}\u00a0</span>"
    if style in ("warn", "attention"):
        return f"<span color='{p['attention']}' background='{p['attention_bg']}'>\u00a0{clean}\u00a0</span>"
    if style in ("crit", "fault", "alert"):
        return f"<span color='{p['fault']}' background='{p['fault_bg']}'>\u00a0{clean}\u00a0</span>"
    if style == "muted":
        return f"<span color='{p['inactive']}'>[{clean}]</span>"
    return f"<span color='{p['schema']}'>[{clean}]</span>"


def StatusDot(active: bool, label_on: str = "ON", label_off: str = "OFF") -> str:
    return StateBadge(active, label_on, label_off, on_tone="ok")


def ProgressBar(pct: float, width: int = 15, color_by_threshold: bool = True) -> str:
    asc = THEME["ascii"]
    p = get_semantic_palette()
    pct_clamped = max(0.0, min(100.0, float(pct)))
    fill_n = int(round((pct_clamped / 100.0) * width))
    empty_n = max(0, width - fill_n)
    c_fill = color_for_pct(pct_clamped) if color_by_threshold else p["vitality"]
    c_empty = p["structure"]
    b_l, b_r = asc["slider_brackets"]

    return (
        f"<span color='{c_empty}'>{Canvas.escape(b_l)}</span>"
        f"<span color='{c_fill}'>{Canvas.escape(asc['slider_fill']) * fill_n}</span>"
        f"<span color='{c_empty}'>{Canvas.escape(asc['slider_empty']) * empty_n}</span>"
        f"<span color='{c_empty}'>{Canvas.escape(b_r)}</span>"
    )


def Slider(
    value: float,
    action_prefix: str,
    min_val: float = 0.0,
    max_val: float = 100.0,
    width: int = 15,
    show_pct: bool = True,
    step_buttons: bool = False,
    step: float = 5.0,
) -> str:
    """Slider di input utente: usa sempre il Tono Ciano Elettrico ('intent')."""
    asc = THEME["ascii"]
    p = get_semantic_palette()
    span = max(1e-6, float(max_val - min_val))
    norm = max(0.0, min(1.0, (float(value) - min_val) / span))
    fill_n = int(round(norm * width))

    c_fill = p["intent"]
    c_empty = p["structure"]
    b_l, b_r = asc["slider_brackets"]
    segments = []
    for i in range(1, width + 1):
        seg_val = int(round(min_val + (i / float(width)) * span))
        ch = asc["slider_fill"] if i <= fill_n else asc["slider_empty"]
        col = c_fill if i <= fill_n else c_empty
        segments.append(
            f"<a href='{Canvas.escape(action_prefix)}:{seg_val}'><span color='{col}' underline='none'>{Canvas.escape(ch)}</span></a>"
        )

    bar_markup = f"<span color='{c_empty}'>{Canvas.escape(b_l)}</span>{''.join(segments)}<span color='{c_empty}'>{Canvas.escape(b_r)}</span>"
    if step_buttons:
        v_dec = max(int(min_val), int(round(value - step)))
        v_inc = min(int(max_val), int(round(value + step)))
        bar_markup = f"{ActionBtn('-', f'{action_prefix}:{v_dec}', tone='neutral')}\u00a0{bar_markup}\u00a0{ActionBtn('+', f'{action_prefix}:{v_inc}', tone='neutral')}"
    if show_pct:
        pct_int = int(round(norm * 100.0))
        bar_markup += f"\u00a0<span color='{p['focus']}'>{pct_int:3d}%</span>"
    return bar_markup


def Sparkline(values: list, max_items: int = 32) -> str:
    chars = THEME["ascii"]["sparkline_chars"]
    n_levels = len(chars)
    out = []
    for v in values[:max_items]:
        pct = max(0.0, min(100.0, float(v)))
        idx = min(n_levels - 1, int((pct / 100.0) * n_levels))
        col = color_for_pct(pct)
        out.append(f"<span color='{col}'>{chars[idx]}</span>")
    return "".join(out)


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
        self._forced_cols = None
        self._ops = []

    def _check_deps(self, requires: list) -> tuple[bool, str]:
        if not requires:
            return True, ""
        from core import probe
        missing = [b for b in requires if not probe.has_bin(b)]
        if not missing:
            return True, ""
        if get_hide_unavailable_settings():
            return False, ""
        return True, Badge(f"MISSING: {missing[0]}", style="warn")

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
        self._ops.append(("section", str(label or ""), clean_acts, label_is_markup))
        return self

    def add_collapsible(self, panel, state_key: str, label: str, build_fn, actions: list = None, label_is_markup: bool = False, requires: list = None):
        ok, badge = self._check_deps(requires)
        if not ok:
            return self

        p = get_semantic_palette()
        is_open = bool(panel.get_state(state_key, False))
        icon = "\u25be" if is_open else "\u25b8"
        toggle_uri = f"ui:state:{state_key}:{'false' if is_open else 'true'}"

        lbl_m = label if label_is_markup else Canvas.escape(label)
        c_tit = p["intent"] if is_open else p["schema"]
        anchor = f"<a href='{Canvas.escape(toggle_uri)}'><span color='{c_tit}' underline='none'>{icon} {lbl_m}</span></a>"

        all_acts = list(actions or [])
        if badge:
            all_acts.append(badge)

        self.add_section(anchor, actions=all_acts, label_is_markup=True)
        if is_open:
            build_fn(self)
        return self

    def add_tabs(self, active_tab: str, tabs: list, uri_prefix: str = "ui:tab", min_tab_cols: int = 18):
        self._ops.append(("row", " ", "", False))
        self._ops.append(("row", "  " + TabBar(tabs, active_tab, uri_prefix=uri_prefix, min_tab_cols=min_tab_cols), "", True))
        self._ops.append(("row", " ", "", False))
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

    def _effective_mode(self) -> str:
        if self.width_mode in ("fixed", "dynamic"):
            return self.width_mode
        for op in self._ops:
            kind = op[0]
            if kind in ("section", "split", "table", "kv"):
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
    def _partition_border_actions(canvas: Canvas, title_markup: str, actions: list, max_total_cols: int, overhead_cols: int = 10) -> tuple[list, list]:
        if not actions:
            return [], []
        title_c = canvas.measure_cols(title_markup)
        avail_for_acts = max_total_cols - title_c - overhead_cols
        if avail_for_acts <= 2:
            return [], list(actions)

        border_acts = []
        overflow_acts = []
        used_c = 0
        for idx, act in enumerate(actions):
            act_c = canvas.measure_cols(act) + (1 if border_acts else 0)
            if not overflow_acts and (used_c + act_c <= avail_for_acts):
                border_acts.append(act)
                used_c += act_c
            else:
                overflow_acts.extend(actions[idx:])
                break
        return border_acts, overflow_acts

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

        for op in self._ops:
            kind = op[0]
            if kind == "row":
                _, left_m, right_m, _ = op
                for line in left_m.split("\n"):
                    l_cols = canvas.measure_cols(line)
                    r_cols = (canvas.measure_cols(right_m) + 2) if right_m else 0
                    req_inner = max(req_inner, l_cols + r_cols)

            elif kind == "kv":
                _, key, val_m, kw, right_m, icon = op
                eff_kw = kw if kw is not None else (COMPACT_KEY_WIDTH if base <= 50 else DEFAULT_KEY_WIDTH)
                l_cols = 2 + eff_kw + 1 + canvas.measure_cols(val_m)
                r_cols = (canvas.measure_cols(right_m) + 2) if right_m else 0
                req_inner = max(req_inner, l_cols + r_cols)

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
                canvas, full_title, self.actions, target_cols, overhead_cols=13
            )
            right_hdr = " ".join(border_acts) if border_acts else ""

            max_title_cols = target_cols - (canvas.measure_cols(right_hdr) + 4 if right_hdr else 0) - 9
            if not self.title_is_markup and canvas.measure_cols(full_title) > max_title_cols:
                ic_len = (canvas.measure_cols(self.icon) + 2) if self.icon else 0
                trunc_t = canvas.truncate_plain(raw_title, max(8, max_title_cols - ic_len))
                full_title = f"{self.icon}  {Canvas.escape(trunc_t)}" if self.icon else Canvas.escape(trunc_t)

            out_lines = [canvas.top(full_title, right_markup=right_hdr, c_box=c_brd, c_txt=c_title, c_brk=c_brk)]

            if overflow_acts:
                tb_str = "  " + "  ".join(overflow_acts)
                for w_tb in canvas.wrap_markup(tb_str, canvas.inner_px):
                    line_tb = w_tb if w_tb.startswith("  ") else ("  " + w_tb.lstrip())
                    out_lines.append(canvas.row(line_tb, c_box=c_brd, c_txt=c_pri))
                out_lines.append(canvas.mid("", c_box=c_brd))

            is_dynamic = (self._effective_mode() == "dynamic")
            default_kw = COMPACT_KEY_WIDTH if target_cols <= 50 else DEFAULT_KEY_WIDTH

            for op in self._ops:
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
                    eff_kw = kw if kw is not None else default_kw
                    prefix = f"{icon} " if icon else ""
                    k_raw = f"{prefix}{key}:" if key else ""
                    k_padded = k_raw.ljust(eff_kw)
                    key_prefix = f"  <span color='{c_sec}'>{Canvas.escape(k_padded)}</span> "
                    hang_prefix = "  " + (" " * eff_kw) + " "
                    prefix_px = canvas.measure_markup(key_prefix)

                    r_px = canvas.measure_markup(right_m) if right_m else 0.0
                    avail_val_px = canvas.inner_px - prefix_px - (r_px + canvas.space_w * 2 if right_m else 0.0)

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
                        out_lines.append(canvas.row("  " + f"<span color='{p['structure']}'>{'\u2500' * table_inner_cols}</span>", c_box=c_brd))

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

            out_lines.append(canvas.bot(c_box=c_brd))
            return "\n".join(out_lines)
        finally:
            canvas.set_cols(prev_cols)


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
