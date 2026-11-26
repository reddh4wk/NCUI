#!/usr/bin/env python3
import html
import math
import re
from core.config import BOX_STYLES, THEME, get_row_gap, is_inner_rounded_box_style, is_rounded_box_style

_PANGO_SCALE = 1024
cairo = None
Gdk = None
Pango = None
PangoCairo = None


def _ensure_pango() -> None:
    global cairo, Gdk, Pango, PangoCairo
    if Pango is not None:
        return
    import cairo as _cairo
    import gi as _gi

    _gi.require_version("Gdk", "3.0")
    _gi.require_version("Pango", "1.0")
    _gi.require_version("PangoCairo", "1.0")
    from gi.repository import Gdk as _Gdk, Pango as _Pango, PangoCairo as _PangoCairo

    cairo = _cairo
    Gdk = _Gdk
    Pango = _Pango
    PangoCairo = _PangoCairo

_TAG_OR_TOKEN_RE = re.compile(r"(<[^>]+>|&[a-zA-Z0-9#]+;|.)")
_LINK_TAG_RE = re.compile(r"</?a\b[^>]*>", re.IGNORECASE)
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_ATOMIC_WIDGET_RE = re.compile(r"(<a\b[^>]*>.*?</a>|<span\b[^>]*background=[^>]*>.*?</span>)", re.IGNORECASE | re.DOTALL)
_FALLBACK_GLYPH_RE = re.compile(r"([\u2139\u21b9\u21ba\u21bb\u229e\u229f\u2300-\u23ff\u2580-\u259f\u25b0-\u25ff\u2600-\u27bf\ue000-\uf8ff\U0001f000-\U0001ffff\U000f0000-\U000ffffd])")
_GEO_TRIANGLE_CHARS = frozenset("\u25b2\u25b4\u25b6\u25b8\u25bc\u25be\u25c0\u25c2")
_ALLOWED_TAG_RE = re.compile(r"^</?\s*(span|a|b|i|u|s|small|big|sub|sup)\b", re.IGNORECASE)
_ANY_ANGLE_TAG_RE = re.compile(r"<[^>]*>")
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_SPLIT_TAG_RE = re.compile(r"(<[^>]+>)")
_SPAN_SIZE_RE = re.compile(r"<span\b[^>]*\bsize=", re.IGNORECASE)
_SPAN_SIZE_VAL_RE = re.compile(r"\bsize=['\"]?(\d+)", re.IGNORECASE)
_FAST_MONO_PLAIN_RE = re.compile(r"^[\x20-\u024f\u2026\u2500-\u257f\u25cf\u25cb]*$")
_CTRL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# MEMENTO TIPOGRAFICO (Golden Standard §5.1):
# 1) Le ligature OpenType DEVONO restare disattivate (liga=0, clig=0, dlig=0, hlig=0) per non alterare le colonne.
# 2) I glifi Nerd Font / PUA in _FALLBACK_GLYPH_RE vengono scalati al 62% (con micro-shrink 1-cella se 1.0 < gw/space_w <= 1.25),
#    centrati verticalmente con Pango `rise` clamped sull'altezza riga, e agganciati a multipli interi di cella monospace.
_NO_LIGA_FEATURES = "liga=0, clig=0, dlig=0, hlig=0"
_NO_LIGA_SPAN_OPEN = "<span font_features='liga=0, clig=0, dlig=0, hlig=0'>"
_SANITIZE_CACHE: dict[tuple[str, float, str, bool, str], str] = {}
_GLYPH_METRICS_CACHE: dict[tuple[str, float, str, int, bool, str, bool], tuple[int, int, int]] = {}
_GLYPH_LAYOUT = None
_GLYPH_SURF = None
_GLYPH_LAYOUT_HAS_SCREEN = False
_GLYPH_NO_LIGA_ATTRS = None


def _glyph_metrics(ch: str, pt: int, use_dejavu: bool = False) -> tuple[int, int, int]:
    """Return `(eff_pt, letter_spacing, rise)` so fallback glyphs fit exact monospace cells and center vertically."""
    global _GLYPH_LAYOUT, _GLYPH_SURF, _GLYPH_LAYOUT_HAS_SCREEN, _GLYPH_NO_LIGA_ATTRS
    _ensure_pango()
    screen = Gdk.Screen.get_default()
    has_scr = screen is not None
    if _GLYPH_LAYOUT is None or (has_scr and not _GLYPH_LAYOUT_HAS_SCREEN):
        if has_scr:
            _GLYPH_LAYOUT = Pango.Layout.new(Gdk.pango_context_get_for_screen(screen))
            _GLYPH_SURF = None
        else:
            _GLYPH_SURF = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            _GLYPH_LAYOUT = PangoCairo.create_layout(cairo.Context(_GLYPH_SURF))
        _GLYPH_NO_LIGA_ATTRS = Pango.AttrList()
        _GLYPH_NO_LIGA_ATTRS.insert(Pango.AttrFontFeatures.new(_NO_LIGA_FEATURES))
        _GLYPH_LAYOUT.set_attributes(_GLYPH_NO_LIGA_ATTRS)
        _GLYPH_LAYOUT_HAS_SCREEN = has_scr
        _GLYPH_METRICS_CACHE.clear()
        _SANITIZE_CACHE.clear()

    typo = THEME.get("typography", {})
    fam = str(typo.get("font_family", "Courier") or "Courier")
    sz = float(typo.get("font_weight_size", typo.get("font_size", 11)) if "font_weight_size" in typo else typo.get("font_size", 11))
    wt = str(typo.get("font_weight", "bold") or "bold")
    key = (fam, sz, wt, pt, use_dejavu, ch, _GLYPH_LAYOUT_HAS_SCREEN)
    cached = _GLYPH_METRICS_CACHE.get(key)
    if cached is not None:
        return cached

    fd = Pango.FontDescription.from_string(
        f"{fam}, Font Awesome 6 Free, Font Awesome 6 Brands, Symbols Nerd Font, monospace {wt} {sz}"
    )
    _GLYPH_LAYOUT.set_font_description(fd)
    _GLYPH_LAYOUT.set_width(-1)
    _GLYPH_LAYOUT.set_attributes(_GLYPH_NO_LIGA_ATTRS)
    _GLYPH_LAYOUT.set_text(" " * 20, -1)
    space_w = max(1.0, (_GLYPH_LAYOUT.get_size()[0] / Pango.SCALE) / 20.0)

    _GLYPH_LAYOUT.set_text("0A", -1)
    ink_base, log_base = _GLYPH_LAYOUT.get_extents()
    base_bl = _GLYPH_LAYOUT.get_baseline()
    base_mid_rel = (ink_base.y + ink_base.height * 0.5) - base_bl

    is_block = "\u2580" <= ch <= "\u259f"
    fam_attr = " font_family='DejaVu Sans Mono'" if use_dejavu else ""
    eff_pt = int(pt)
    _GLYPH_LAYOUT.set_markup(f"<span{fam_attr} size='{eff_pt}'>{html.escape(ch)}</span>", -1)
    gw = _GLYPH_LAYOUT.get_size()[0] / Pango.SCALE

    if not use_dejavu and not is_block and space_w < gw <= space_w * 1.25:
        eff_pt = max(1024, int(eff_pt * (space_w - 0.15) / gw))
        _GLYPH_LAYOUT.set_markup(f"<span size='{eff_pt}'>{html.escape(ch)}</span>", -1)
        gw = _GLYPH_LAYOUT.get_size()[0] / Pango.SCALE

    target_cells = max(1, int(math.ceil((gw - 0.5) / space_w)))
    ls = max(0, int(round((target_cells * space_w - gw) * _PANGO_SCALE)))

    rise = 0
    if not is_block and gw > 0:
        ink_g, log_g = _GLYPH_LAYOUT.get_extents()
        g_bl = _GLYPH_LAYOUT.get_baseline()
        g_mid_rel = (ink_g.y + ink_g.height * 0.5) - g_bl
        max_safe_rise = max(0, (base_bl - log_base.y) - (g_bl - log_g.y))
        rise = max(0, min(int(round(g_mid_rel - base_mid_rel)), max_safe_rise))

    res = (eff_pt, ls, rise)
    if len(_GLYPH_METRICS_CACHE) > 512:
        _GLYPH_METRICS_CACHE.clear()
    _GLYPH_METRICS_CACHE[key] = res
    return res


def _safe_box_chars(raw_chars: dict) -> dict:
    fb = BOX_STYLES["rounded"]
    src = raw_chars if isinstance(raw_chars, dict) else {}
    return {k: (str(src.get(k) or "") or v)[0] for k, v in fb.items()}


def _safe_pair(seq, d_l: str, d_r: str) -> tuple[str, str]:
    if isinstance(seq, (list, tuple)) and len(seq) >= 2:
        return (str(seq[0]) if seq[0] is not None else d_l, str(seq[1]) if seq[1] is not None else d_r)
    return d_l, d_r


def rounded_rect_path(cr, x: float, y: float, w: float, h: float, r: float, *, outer: bool = False) -> None:
    eff_r = float(r) if (outer or is_inner_rounded_box_style()) else 0.0
    rad = max(0.0, min(eff_r, float(w) * 0.5, float(h) * 0.5))
    if rad <= 0.0:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi * 0.5, 0.0)
    cr.arc(x + w - rad, y + h - rad, rad, 0.0, math.pi * 0.5)
    cr.arc(x + rad, y + h - rad, rad, math.pi * 0.5, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, math.pi * 1.5)
    cr.close_path()


def stroke_styled_frame(cr, x: float, y: float, w: float, h: float, r: float = 5.0, *, outer: bool = False) -> None:
    """Stroke an already-traced or newly-traced rectangle matching active `box_style` (dashed/double/solid)."""
    bs = str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded").strip().lower()
    cr.save()
    rounded_rect_path(cr, x, y, w, h, r, outer=outer)
    if bs == "dashed":
        cr.set_dash([4.0, 2.5])
        cr.stroke()
    elif bs == "double":
        cr.stroke()
        if w > 8.0 and h > 8.0:
            rounded_rect_path(cr, x + 2.0, y + 2.0, w - 4.0, h - 4.0, max(0.0, r - 1.0), outer=outer)
            cr.stroke()
    else:
        cr.stroke()
    cr.restore()


class Canvas:
    def __init__(self, total_cols: int = None):
        _ensure_pango()
        geom = THEME["geometry"]
        cols = total_cols if total_cols is not None else geom["compact_cols"]

        screen = Gdk.Screen.get_default()
        if screen is not None:
            pctx = Gdk.pango_context_get_for_screen(screen)
            self.layout = Pango.Layout.new(pctx)
        else:
            self.surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            self.layout = PangoCairo.create_layout(cairo.Context(self.surf))

        self._no_liga_attrs = Pango.AttrList()
        self._no_liga_attrs.insert(Pango.AttrFontFeatures.new(_NO_LIGA_FEATURES))
        self._mt_cache: dict[str, float] = {}
        self._mm_cache: dict[str, float] = {}
        self._lh_cache: dict[str, int] = {}
        self._metrics_sig = None
        self.total_cols = max(6, int(cols))
        self.inner_cols = max(1, self.total_cols - 4)
        self.sync_theme()
        self.set_cols(cols)

    def sync_theme(self) -> bool:
        """
        Ricalcola istantaneamente le metriche Pango del Canvas se font, dimensione, peso
        o carattere orizzontale della cornice (`h`) sono cambiati a caldo in `THEME`.
        """
        typo = THEME["typography"]
        chars = _safe_box_chars(THEME["ascii"].get("chars", {}))
        ff = str(typo.get("font_family", "Courier") or "Courier")
        fw = str(typo.get("font_weight", "bold") or "bold")
        fs = float(typo.get("font_size", 11))
        h_char = chars.get("h", "\u2500")
        sig = (ff, fw, fs, h_char)

        self.chars = chars
        self.title_brackets = _safe_pair(THEME["ascii"].get("title_brackets"), "[ ", " ]")
        if self._metrics_sig == sig:
            return False

        self._metrics_sig = sig
        self.font_family = ff
        self.font_weight = fw
        self.font_size = fs
        desc = Pango.FontDescription.from_string(
            f"{self.font_family}, Font Awesome 6 Free, Font Awesome 6 Brands, Symbols Nerd Font, monospace {self.font_weight} {self.font_size}"
        )
        self.layout.set_font_description(desc)
        self._mt_cache.clear()
        self._mm_cache.clear()
        self._lh_cache.clear()
        self._std_row_h_px = 0
        self._h_bar_rel_baseline = None

        self.char_w = max(1.0, self.measure_text(h_char * 20) / 20.0)
        self.space_w = max(1.0, self.measure_text(" " * 20) / 20.0)
        self.layout.set_attributes(self._no_liga_attrs)
        self.layout.set_text("[A0]", -1)
        ink_r, log_r = self.layout.get_pixel_extents()
        base_px = self.layout.get_baseline() / Pango.SCALE
        self.base_row_h_px = max(12, int(log_r.height))
        self.char_h = self.base_row_h_px
        self.cap_mid_from_baseline = (ink_r.y + ink_r.height * 0.5) - base_px
        self.cap_ink_h = float(max(8, ink_r.height))
        self.base_pango_size = self.font_size * Pango.SCALE
        self.fallback_glyph_pt = int(round(self.base_pango_size * 0.62))
        self._is_mono = abs(self.char_w - self.space_w) < 0.01
        return True

    def set_cols(self, total_cols: int) -> None:
        self.sync_theme()
        self.total_cols = max(6, int(total_cols))
        self.inner_cols = max(1, self.total_cols - 4)
        ch = self.chars
        new_anchor_v = ch.get("v", "\u2502")
        if getattr(self, "_anchor_v", None) != new_anchor_v:
            self._mm_cache.clear()
        self._anchor_v = new_anchor_v
        self._anchor_px = self.measure_text(self._anchor_v * 2)
        n_dashes = max(1, self.total_cols - 2)
        self.total_px = self.measure_text(f"{ch['bl']}{ch['h'] * n_dashes}{ch['br']}")
        borders_px = self.measure_text(f"{ch['v']}  {ch['v']}")
        self.inner_px = max(self.char_w, self.total_px - borders_px)

    @staticmethod
    def escape(text) -> str:
        if text is None:
            return ""
        s = _CTRL_XML_RE.sub("", str(text))
        if "<" not in s and ">" not in s:
            return s.replace("&", "&amp;")
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @staticmethod
    def sanitize_markup(markup_str: str) -> str:
        if not markup_str:
            return ""
        s = _CTRL_XML_RE.sub("", str(markup_str))
        typo = THEME.get("typography", {})
        font_fam = str(typo.get("font_family", "monospace"))
        font_sz = float(typo.get("font_size", 11))
        font_wt = str(typo.get("font_weight", "normal"))
        cache_key = (font_fam, font_sz, font_wt, bool(Gdk is not None and Gdk.Screen.get_default() is not None), s)
        cached = _SANITIZE_CACHE.get(cache_key)
        if cached is not None:
            return cached

        def _repl(m):
            tag = m.group(0)
            if _ALLOWED_TAG_RE.match(tag):
                return tag
            return tag.replace("<", "&lt;").replace(">", "&gt;")

        s_safe = _ANY_ANGLE_TAG_RE.sub(_repl, s) if "<" in s else s
        if "&" in s_safe:
            s_safe = _BARE_AMP_RE.sub("&amp;", s_safe)
        if _FALLBACK_GLYPH_RE.search(s_safe):
            pt = int(round(font_sz * _PANGO_SCALE * 0.62))
            parts = _SPLIT_TAG_RE.split(s_safe)
            for idx in range(0, len(parts), 2):
                seg = parts[idx]
                if not seg or not _FALLBACK_GLYPH_RE.search(seg):
                    continue
                prev_tag = parts[idx - 1] if idx > 0 else ""
                has_size = bool(prev_tag and _SPAN_SIZE_RE.search(prev_tag))
                has_fam = bool(prev_tag and "font_family=" in prev_tag.lower())
                has_ls = bool(prev_tag and "letter_spacing=" in prev_tag.lower())
                has_rise = bool(prev_tag and "rise=" in prev_tag.lower())
                m_sz = _SPAN_SIZE_VAL_RE.search(prev_tag) if has_size else None
                parsed_pt = int(m_sz.group(1)) if m_sz else pt

                def _g_repl(m, _has_size=has_size, _has_fam=has_fam, _has_ls=has_ls, _has_rise=has_rise, _ppt=parsed_pt):
                    g = m.group(1)
                    use_dv = (g in _GEO_TRIANGLE_CHARS)
                    if _has_size:
                        if _has_ls or ("\u2580" <= g <= "\u259f"):
                            return g
                        _eff_pt, ls, rise = _glyph_metrics(g, _ppt, use_dejavu=use_dv)
                        attrs = ""
                        if use_dv and not _has_fam:
                            attrs += " font_family='DejaVu Sans Mono'"
                        if ls > 0:
                            attrs += f" letter_spacing='{ls}'"
                        if rise > 0 and not _has_rise:
                            attrs += f" rise='{rise}'"
                        return f"<span{attrs}>{g}</span>" if attrs else g
                    eff_pt, ls, rise = _glyph_metrics(g, pt, use_dejavu=use_dv)
                    fam_attr = " font_family='DejaVu Sans Mono'" if use_dv else ""
                    ls_attr = f" letter_spacing='{ls}'" if ls > 0 else ""
                    rise_attr = f" rise='{rise}'" if rise > 0 else ""
                    return f"<span{fam_attr} size='{eff_pt}'{ls_attr}{rise_attr}>{g}</span>"

                parts[idx] = _FALLBACK_GLYPH_RE.sub(_g_repl, seg)
            s_safe = "".join(parts)

        if len(_SANITIZE_CACHE) > 8192:
            _SANITIZE_CACHE.clear()
        _SANITIZE_CACHE[cache_key] = s_safe
        return s_safe

    @staticmethod
    def strip_tags(markup_str: str) -> str:
        if not markup_str:
            return ""
        s = str(markup_str)
        if "<" not in s and "&" not in s:
            return s
        return html.unescape(_ANY_TAG_RE.sub("", s))

    def measure_text(self, text_str: str) -> float:
        s = str(text_str or "")
        if not s:
            return 0.0
        cached = self._mt_cache.get(s)
        if cached is not None:
            return cached
        self.layout.set_width(-1)
        self.layout.set_attributes(self._no_liga_attrs)
        self.layout.set_text(s, -1)
        res = self.layout.get_size()[0] / Pango.SCALE
        if len(self._mt_cache) > 4096:
            self._mt_cache.clear()
        self._mt_cache[s] = res
        return res

    def measure_markup(self, markup_str: str) -> float:
        if not markup_str:
            return 0.0
        cached = self._mm_cache.get(markup_str)
        if cached is not None:
            return cached
        s = self.sanitize_markup(markup_str)
        if self._is_mono and "size=" not in s and "letter_spacing=" not in s and "\n" not in s and not _FALLBACK_GLYPH_RE.search(s):
            plain = self.strip_tags(s)
            if _FAST_MONO_PLAIN_RE.match(plain):
                res = len(plain) * self.char_w
                if len(self._mm_cache) > 8192:
                    self._mm_cache.clear()
                self._mm_cache[markup_str] = res
                return res
        pango_only = _LINK_TAG_RE.sub("", s)
        v = getattr(self, "_anchor_v", "\u2502")
        anc = getattr(self, "_anchor_px", 0.0)
        self.layout.set_width(-1)
        try:
            self.layout.set_markup(f"{_NO_LIGA_SPAN_OPEN}{v}{pango_only}{v}</span>", -1)
            res = max(0.0, (self.layout.get_size()[0] / Pango.SCALE) - anc)
        except Exception:
            res = self.measure_text(self.strip_tags(s))
        if len(self._mm_cache) > 8192:
            self._mm_cache.clear()
        self._mm_cache[markup_str] = res
        return res

    def measure_line_height(self, markup_str: str) -> int:
        if not markup_str or "size=" not in markup_str:
            cached_plain = self._lh_cache.get("__plain__")
            if cached_plain is not None:
                return cached_plain
        else:
            cached = self._lh_cache.get(markup_str)
            if cached is not None:
                return cached
        s = self.sanitize_markup(markup_str or " ")
        pango_only = _LINK_TAG_RE.sub("", s)
        self.layout.set_width(-1)
        try:
            self.layout.set_markup(f"{_NO_LIGA_SPAN_OPEN}{pango_only}</span>", -1)
        except Exception:
            self.layout.set_attributes(self._no_liga_attrs)
            self.layout.set_text(self.strip_tags(s) or " ", -1)
        line = self.layout.get_line(0)
        if line is not None:
            _, logical_rect = line.get_extents()
            res = int(logical_rect.height)
        else:
            res = int(self.layout.get_size()[1])
        if len(self._lh_cache) > 4096:
            self._lh_cache.clear()
        if not markup_str or "size=" not in markup_str:
            self._lh_cache["__plain__"] = res
        else:
            self._lh_cache[markup_str] = res
        return res

    def measure_cols(self, markup_str: str) -> int:
        px = self.measure_markup(markup_str)
        if px <= 0.0:
            return 0
        return int(math.ceil((px - 0.5) / self.char_w))

    def _micro_space(self, rem_px: float) -> str:
        if rem_px < 0.45:
            return ""
        pt_size = max(64, int(round((rem_px / self.space_w) * self.base_pango_size)))
        return f"<span size='{pt_size}'> </span>"

    def _int_spaces_for_px(self, gap_px: float) -> str:
        """Restituisce solo spazi standard interi senza _micro_space (zero deriva da hinting Pango)."""
        if gap_px <= 0.2:
            return ""
        n = max(0, int((gap_px + 0.1) // self.space_w))
        while n > 0 and self.measure_text(" " * n) > gap_px + 0.1:
            n -= 1
        return " " * n

    def _exact_spaces_for_px(self, gap_px: float, min_spaces: int = 0) -> tuple[str, float]:
        """Restituisce spazi esatti al pixel tramite letter_spacing sull'ultimo spazio (altezza riga invariante)."""
        n_sp = max(min_spaces, int((gap_px + 0.1) // self.space_w))
        while n_sp > min_spaces and (n_sp * self.space_w) > gap_px + 0.15:
            n_sp -= 1
        base_adv = n_sp * self.space_w
        rem_px = gap_px - base_adv
        if n_sp >= 1 and 0.45 <= rem_px < self.space_w:
            ls = int(round(rem_px * _PANGO_SCALE))
            return ((" " * (n_sp - 1)) + f"<span letter_spacing='{ls}'> </span>", base_adv + rem_px)
        return (" " * n_sp, base_adv)

    def _space_fill(self, gap_px: float, fill_char: str = " ") -> str:
        if gap_px <= 0.0:
            return ""
        if fill_char == " ":
            return self._int_spaces_for_px(gap_px)
        c_w = self.char_w
        n_chars = max(0, int(gap_px // c_w))
        used = self.measure_text(fill_char * n_chars) if n_chars > 0 else 0.0
        while n_chars > 0 and used > gap_px + 0.2:
            n_chars -= 1
            used = self.measure_text(fill_char * n_chars)
        while self.measure_text(fill_char * (n_chars + 1)) <= gap_px + 0.15:
            n_chars += 1
            used = self.measure_text(fill_char * n_chars)
        rem_px = max(0.0, gap_px - used)
        return self._micro_space(rem_px) + (fill_char * n_chars)

    def _fit_line(
        self,
        left_markup: str,
        right_markup: str,
        fill_char: str = " ",
        c_fill: str = None,
        min_chars: int = 0,
        fill_alpha: str = "",
    ) -> str:
        base_px = self.measure_markup(left_markup) + self.measure_markup(right_markup)
        gap_px = max(0.0, self.total_px - base_px)
        unit_w = self.space_w if fill_char == " " else self.measure_text(fill_char)
        n_chars = max(min_chars, int(gap_px // unit_w))

        def _wrap_fill(body: str) -> str:
            if not body:
                return ""
            return f"<span color='{c_fill}'{fill_alpha}>{body}</span>" if c_fill else body

        while n_chars > min_chars and (base_px + n_chars * unit_w) > self.total_px + 0.15:
            n_chars -= 1
        while (base_px + (n_chars + 1) * unit_w) <= self.total_px + 0.10:
            n_chars += 1
        cur_line = left_markup + _wrap_fill(fill_char * n_chars) + right_markup
        rem_px = max(0.0, self.total_px - (base_px + n_chars * unit_w))
        sub = self._micro_space(rem_px)
        if sub:
            test_with_sub = left_markup + _wrap_fill((sub + (fill_char * n_chars)) if fill_char != " " else ((fill_char * n_chars) + sub)) + right_markup
            if self.measure_markup(test_with_sub) <= self.total_px + 0.25:
                return test_with_sub
        return cur_line

    @staticmethod
    def _tag_name(tag: str) -> str:
        if not tag:
            return "span"
        m = re.match(r"</?\s*([a-zA-Z0-9_:-]+)", tag)
        name = m.group(1).lower() if m else "span"
        if name not in ("span", "a", "b", "i", "u", "s", "small", "big", "sub", "sup"):
            return "span"
        return name

    def truncate_markup(self, markup_str: str, max_px: float) -> str:
        markup_str = self.sanitize_markup(markup_str)
        if max_px <= 0.0:
            return ""
        if self.measure_markup(markup_str) <= max_px + 0.3:
            return markup_str
        ell_px = self.measure_text("\u2026")
        target_px = max(0.0, max_px - ell_px)
        tokens = _TAG_OR_TOKEN_RE.findall(markup_str)
        open_tags, out = [], []
        for tok in tokens:
            if tok.startswith("<"):
                if tok.startswith("</"):
                    if open_tags:
                        open_tags.pop()
                    out.append(tok)
                elif tok.endswith("/>"):
                    out.append(tok)
                else:
                    open_tags.append(tok)
                    out.append(tok)
            else:
                closing = "".join(f"</{self._tag_name(t)}>" for t in reversed(open_tags))
                candidate = "".join(out) + tok + closing
                if self.measure_markup(candidate) > target_px and any(not x.startswith("<") for x in out):
                    break
                out.append(tok)
        closing = "".join(f"</{self._tag_name(t)}>" for t in reversed(open_tags))
        return "".join(out) + "\u2026" + closing

    def wrap_markup(self, markup_str: str, max_px: float) -> list[str]:
        markup_str = self.sanitize_markup(markup_str)
        if not markup_str:
            return [""]
        max_px = max(self.char_w * 4, max_px)
        raw_lines = markup_str.split("\n")
        if len(raw_lines) == 1 and self.measure_markup(markup_str) <= max_px + 0.5:
            return [markup_str]
        wrapped_lines, carry_open_tags = [], []
        for raw_line in raw_lines:
            line_input = "".join(carry_open_tags) + raw_line
            tokens = _TAG_OR_TOKEN_RE.findall(line_input)
            open_tags, cur_tokens, last_space_state = [], [], None
            for tok in tokens:
                if tok.startswith("<"):
                    if tok.startswith("</"):
                        if open_tags:
                            open_tags.pop()
                        cur_tokens.append(tok)
                    elif tok.endswith("/>"):
                        cur_tokens.append(tok)
                    else:
                        open_tags.append(tok)
                        cur_tokens.append(tok)
                else:
                    if tok == " ":
                        last_space_state = (len(cur_tokens), list(open_tags))
                    closing = "".join(f"</{self._tag_name(t)}>" for t in reversed(open_tags))
                    candidate = "".join(cur_tokens) + tok + closing
                    has_visible = any(not x.startswith("<") for x in cur_tokens)
                    inside_atom = any(t.lower().startswith("<a") or "background=" in t.lower() for t in open_tags)
                    can_break = (last_space_state is not None and last_space_state[0] > 0) or not inside_atom
                    if has_visible and self.measure_markup(candidate) > max_px + 0.5 and can_break:
                        if last_space_state is not None and last_space_state[0] > 0:
                            cut_idx, tags_at_space = last_space_state
                            line_tokens = cur_tokens[:cut_idx]
                            rem_tokens = cur_tokens[cut_idx + 1:]
                            close_str = "".join(f"</{self._tag_name(t)}>" for t in reversed(tags_at_space))
                            wrapped_lines.append("".join(line_tokens) + close_str)
                            cur_tokens = list(tags_at_space) + rem_tokens + [tok]
                            last_space_state = None
                        else:
                            close_str = "".join(f"</{self._tag_name(t)}>" for t in reversed(open_tags))
                            wrapped_lines.append("".join(cur_tokens) + close_str)
                            cur_tokens = list(open_tags) + [tok]
                            last_space_state = None
                    else:
                        cur_tokens.append(tok)
            closing = "".join(f"</{self._tag_name(t)}>" for t in reversed(open_tags))
            wrapped_lines.append("".join(cur_tokens) + closing)
            carry_open_tags = list(open_tags)
        return wrapped_lines or [""]

    def pad_cell(self, markup_str: str, target_cols: int, align: str = "left", truncate: bool = True) -> str:
        markup_str = self.sanitize_markup(markup_str)
        target_px = max(0.0, target_cols * self.char_w)
        cur_px = self.measure_markup(markup_str)
        if truncate and cur_px > target_px + 0.4:
            markup_str = self.truncate_markup(markup_str, target_px)
            cur_px = self.measure_markup(markup_str)
        gap_px = max(0.0, target_px - cur_px)
        if align == "right":
            sp, _ = self._exact_spaces_for_px(gap_px)
            return sp + markup_str
        if align == "center":
            left_px = gap_px / 2.0
            left_sp, left_adv = self._exact_spaces_for_px(left_px)
            rem_px = max(0.0, gap_px - left_adv)
            right_sp, _ = self._exact_spaces_for_px(rem_px)
            return left_sp + markup_str + right_sp
        sp, _ = self._exact_spaces_for_px(gap_px)
        return markup_str + sp

    def format_table_row(
        self,
        cells: list,
        col_widths: list[int],
        aligns: list[str],
        truncate: bool = True,
        left_margin: str = "  ",
        sep_cols: int = 2,
    ) -> str:
        """
        Allinea una riga multi-colonna calcolando la coordinata X cumulativa di ogni colonna,
        con spanning automatico su celle successive vuote e protezione anti-troncamento per widget atomici.
        """
        n_cols = len(col_widths)
        if n_cols == 0:
            return left_margin

        out_str = left_margin
        base_x_px = self.measure_markup(left_margin)
        cur_px = base_x_px
        has_non_margin = bool(left_margin.strip())
        col_starts, col_ends = [], []
        cum = 0
        for idx in range(n_cols):
            w_c = max(1, int(col_widths[idx]))
            col_starts.append(base_x_px + cum * self.char_w)
            col_ends.append(base_x_px + (cum + w_c) * self.char_w)
            cum += w_c + (sep_cols if idx < n_cols - 1 else 0)

        for idx in range(n_cols):
            raw_cell = self.sanitize_markup(str(cells[idx] if idx < len(cells) else ""))
            if not self.strip_tags(raw_cell).strip():
                continue
            align = aligns[idx] if idx < len(aligns) else "left"
            col_start_px = col_starts[idx]
            eff_end_px = col_ends[idx]
            for nxt in range(idx + 1, n_cols):
                nxt_raw = str(cells[nxt] if nxt < len(cells) else "")
                if not self.strip_tags(nxt_raw).strip():
                    eff_end_px = col_ends[nxt]
                else:
                    break

            col_budget_px = max(self.char_w, eff_end_px - col_start_px)
            cell_px = self.measure_markup(raw_cell)
            has_atom = bool(_ATOMIC_WIDGET_RE.search(raw_cell))
            if truncate and not has_atom and cell_px > col_budget_px + 0.3:
                raw_cell = self.truncate_markup(raw_cell, col_budget_px)
                cell_px = self.measure_markup(raw_cell)

            if idx > 0 or cur_px < col_start_px - 0.1:
                if align == "right":
                    target_start_px = max(col_start_px, eff_end_px - cell_px)
                elif align == "center":
                    target_start_px = max(col_start_px, col_start_px + max(0.0, (eff_end_px - col_start_px - cell_px) / 2.0))
                else:
                    target_start_px = col_start_px
                min_sp = 1 if idx > 0 and has_non_margin else 0
                needed_px = max(self.space_w * min_sp, target_start_px - cur_px)
                sp_markup, adv_px = self._exact_spaces_for_px(needed_px, min_spaces=min_sp)
                out_str += sp_markup
                cur_px += adv_px
            else:
                avail_in_col = max(0.0, eff_end_px - cur_px)
                if align == "right":
                    sp, adv_px = self._exact_spaces_for_px(max(0.0, avail_in_col - cell_px))
                    out_str += sp
                    cur_px += adv_px
                elif align == "center":
                    sp, adv_px = self._exact_spaces_for_px(max(0.0, (avail_in_col - cell_px) / 2.0))
                    out_str += sp
                    cur_px += adv_px

            out_str += raw_cell
            cur_px += cell_px
            has_non_margin = True

        if cur_px > self.inner_px + 0.3 and not _ATOMIC_WIDGET_RE.search(out_str):
            out_str = self.truncate_markup(out_str, self.inner_px)
        return out_str

    def compute_split_widths(self, n_cols: int, ratios: list = None, min_widths: list = None) -> list[int]:
        if n_cols <= 0:
            return []
        avail = max(n_cols, self.inner_cols - (n_cols - 1) * 3)
        if ratios and len(ratios) == n_cols and sum(ratios) > 0:
            tot_r = float(sum(ratios))
            widths = [max(4, int(round((r / tot_r) * avail))) for r in ratios]
        else:
            w = max(4, avail // n_cols)
            widths = [w] * n_cols
        if min_widths and len(min_widths) == n_cols:
            widths = [max(w, mw) for w, mw in zip(widths, min_widths)]
        diff = avail - sum(widths)
        widths[-1] = max(2, widths[-1] + diff)
        return widths

    def split_junction_line(self, col_widths: list[int], kind: str = "top", c_box: str = None) -> str:
        c_box = c_box or THEME["colors"]["structure"]
        ch = self.chars
        j_char = ch["mt"] if kind == "top" else (ch["mb"] if kind == "bot" else ch["mc"])
        segs = [ch["h"] * (w + 2) for w in col_widths]
        body = j_char.join(segs)
        l_ch = ch["ml"]
        r_ch = ch["mr"]
        return f"<span color='{c_box}'>{l_ch}{body}{r_ch}</span>"

    def split_row(self, cells: list[str], col_widths: list[int], c_box: str = None) -> str:
        c_box = c_box or THEME["colors"]["structure"]
        ch = self.chars
        sep = f" <span color='{c_box}'>{ch['v']}</span> "
        padded = [self.pad_cell(str(c), w, align="left", truncate=True) for c, w in zip(cells, col_widths)]
        return self.row(sep.join(padded), c_box=c_box)

    def truncate_plain(self, text: str, max_cols: int) -> str:
        s = str(text or "")
        return s if len(s) <= max_cols else s[: max(1, max_cols - 1)] + "\u2026"

    def top(
        self,
        title_markup: str,
        right_markup: str = "",
        c_box: str = None,
        c_txt: str = None,
        c_brk: str = None,
        *,
        tl_ch: str = None,
        tr_ch: str = None,
    ) -> str:
        c_box = c_box or THEME["colors"]["box_inactive"]
        c_txt = c_txt or THEME["colors"]["focus"]
        c_brk = c_brk or c_box
        ch = self.chars
        b_alpha = " alpha='1%'" if str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded") == "dashed" else ""
        tl = tl_ch or ch["tl"]
        tr = tr_ch or ch["tr"]
        if not title_markup and not right_markup:
            return f"<span color='{c_box}'{b_alpha}>{tl}{ch['h'] * max(1, self.total_cols - 2)}{tr}</span>"
        b_l, b_r = self.title_brackets
        if title_markup:
            left_part = (
                f"<span color='{c_box}'{b_alpha}>{tl}{ch['h']} </span>"
                f"<span color='{c_brk}'>{self.escape(b_l)}</span>"
                f"<span color='{c_txt}'>{self.sanitize_markup(title_markup)}</span>"
                f"<span color='{c_brk}'>{self.escape(b_r)}</span>"
                f"<span color='{c_box}'{b_alpha}> </span>"
            )
        else:
            left_part = f"<span color='{c_box}'{b_alpha}>{tl}{ch['h']}</span>"
        right_part = (
            f"<span color='{c_box}'{b_alpha}> </span>{self.sanitize_markup(right_markup)}<span color='{c_box}'{b_alpha}> {ch['h']}{tr}</span>"
            if right_markup
            else f"<span color='{c_box}'{b_alpha}>{tr}</span>"
        )
        return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_box, min_chars=1, fill_alpha=b_alpha)

    def mid(self, label_markup: str = "", right_markup: str = "", c_box: str = None) -> str:
        c_box = c_box or THEME["colors"]["box_inactive"]
        ch = self.chars
        b_alpha = " alpha='1%'" if str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded") == "dashed" else ""
        left_part = f"<span color='{c_box}'{b_alpha}>{ch['ml']}{ch['h']} </span>{self.sanitize_markup(label_markup)}<span color='{c_box}'{b_alpha}> </span>" if label_markup else f"<span color='{c_box}'{b_alpha}>{ch['ml']}</span>"
        right_part = f"<span color='{c_box}'{b_alpha}> </span>{self.sanitize_markup(right_markup)}<span color='{c_box}'{b_alpha}> {ch['h']}{ch['mr']}</span>" if right_markup else f"<span color='{c_box}'{b_alpha}>{ch['mr']}</span>"
        return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_box, min_chars=1, fill_alpha=b_alpha)

    def divider_line(self, title_markup: str = "", c_line: str = None, c_txt: str = None, align: str = "left") -> str:
        c_line = c_line or THEME["colors"]["structure"]
        c_txt = c_txt or THEME["colors"]["schema"]
        ch = self.chars
        b_alpha = " alpha='1%'" if str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded") == "dashed" else ""
        if not title_markup:
            return f"<span color='{c_line}'{b_alpha}>{ch['h'] * max(1, self.total_cols)}</span>"
        b_l, b_r = self.title_brackets
        title_block = f"<span color='{c_line}'>{self.escape(b_l)}</span><span color='{c_txt}'>{self.sanitize_markup(title_markup)}</span><span color='{c_line}'>{self.escape(b_r)}</span>"
        if align == "left":
            left_part = f"<span color='{c_line}'{b_alpha}>{ch['h'] * 3} </span>{title_block}<span color='{c_line}'{b_alpha}> </span>"
            right_part = f"<span color='{c_line}'{b_alpha}>{ch['h']}</span>"
            return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_line, min_chars=1, fill_alpha=b_alpha)
        elif align == "right":
            left_part = f"<span color='{c_line}'{b_alpha}>{ch['h']}</span>"
            right_part = f"<span color='{c_line}'{b_alpha}> </span>{title_block}<span color='{c_line}'{b_alpha}> {ch['h'] * 3}</span>"
            return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_line, min_chars=1, fill_alpha=b_alpha)
        else:
            base_px = self.measure_markup(f"<span color='{c_line}'> </span>{title_block}<span color='{c_line}'> </span>")
            gap_px = max(0.0, self.total_px - base_px)
            left_dashes = self._space_fill(gap_px / 2.0, ch["h"])
            left_part = f"<span color='{c_line}'{b_alpha}>{left_dashes} </span>{title_block}<span color='{c_line}'{b_alpha}> </span>"
            return self._fit_line(left_part, "", fill_char=ch["h"], c_fill=c_line, min_chars=0, fill_alpha=b_alpha)

    def row(self, content_markup: str, right_markup: str = "", c_box: str = None, c_txt: str = None, min_height_pango: int = 0) -> str:
        c_box = c_box or THEME["colors"]["box_inactive"]
        c_txt = c_txt or THEME["colors"]["focus"]
        ch = self.chars
        bs = str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded")
        v_alpha = " alpha='1%'" if bs in ("dashed", "double") else ""
        content_markup, right_markup = self.sanitize_markup(content_markup), self.sanitize_markup(right_markup)
        left_border = f"<span color='{c_box}'{v_alpha}>{ch['v']}</span> "
        right_border = f" <span color='{c_box}'{v_alpha}>{ch['v']}</span>"
        borders_px = self.measure_markup(left_border + right_border)

        if right_markup:
            max_right_px = max(self.char_w * 4, self.total_px - borders_px - (self.char_w * 4))
            if self.measure_markup(right_markup) > max_right_px:
                right_markup = self.truncate_markup(right_markup, max_right_px)
            r_px = self.measure_markup(right_markup)
            max_left_px = max(0.0, self.total_px - borders_px - r_px - self.space_w)
            if self.measure_markup(content_markup) > max_left_px + 0.3:
                content_markup = self.truncate_markup(content_markup, max_left_px)
            left_full = f"{left_border}<span color='{c_txt}'>{content_markup}</span>"
            line_out = self._fit_line(left_full, f"{right_markup}{right_border}", fill_char=" ", min_chars=1)
        else:
            max_content_px = max(self.char_w, self.total_px - borders_px)
            if self.measure_markup(content_markup) > max_content_px + 0.3:
                content_markup = self.truncate_markup(content_markup, max_content_px)
            line_out = self._fit_line(f"{left_border}<span color='{c_txt}'>{content_markup}</span>", right_border, fill_char=" ", min_chars=0)

        target_h_pango = max(min_height_pango, int(round((self.base_row_h_px + get_row_gap()) * Pango.SCALE)))
        return self.ensure_min_line_height(line_out, target_h_pango)

    def ensure_min_line_height(self, line_out: str, target_h_pango: int) -> str:
        """Append zero-width baseline strut if `line_out` is shorter than `target_h_pango`."""
        if target_h_pango > 0:
            cur_h = self.measure_line_height(line_out)
            if cur_h < target_h_pango:
                base_h = max(1, int(round(self.base_row_h_px * Pango.SCALE)))
                target_pt = int(round(self.base_pango_size * (target_h_pango / float(base_h))))
                cand = f"{line_out}<span size='{target_pt}'>\u200b</span>"
                for _ in range(4):
                    ch = self.measure_line_height(cand)
                    if ch <= target_h_pango or target_pt <= 1024:
                        break
                    target_pt = max(1024, int(round(target_pt * (target_h_pango / float(ch)))) - 128)
                    cand = f"{line_out}<span size='{target_pt}'>\u200b</span>"
                return cand
        return line_out

    def bot(
        self,
        label_markup: str = "",
        right_markup: str = "",
        c_box: str = None,
        *,
        bl_ch: str = None,
        br_ch: str = None,
    ) -> str:
        c_box = c_box or THEME["colors"]["box_inactive"]
        ch = self.chars
        b_alpha = " alpha='1%'" if str(THEME.get("ascii", {}).get("box_style", "rounded") or "rounded") == "dashed" else ""
        bl = bl_ch or ch["bl"]
        br = br_ch or ch["br"]
        if not label_markup and not right_markup:
            return f"<span color='{c_box}'{b_alpha}>{bl}{ch['h'] * max(1, self.total_cols - 2)}{br}</span>"
        left_part = f"<span color='{c_box}'{b_alpha}>{bl}{ch['h']} </span>{self.sanitize_markup(label_markup)}<span color='{c_box}'{b_alpha}> </span>" if label_markup else f"<span color='{c_box}'{b_alpha}>{bl}</span>"
        right_part = f"<span color='{c_box}'{b_alpha}> </span>{self.sanitize_markup(right_markup)}<span color='{c_box}'{b_alpha}> {ch['h']}{br}</span>" if right_markup else f"<span color='{c_box}'{b_alpha}>{br}</span>"
        return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_box, min_chars=1, fill_alpha=b_alpha)
