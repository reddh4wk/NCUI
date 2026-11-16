#!/usr/bin/env python3
import html
import math
import re
import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Pango, PangoCairo
from core.config import THEME

_TAG_OR_TOKEN_RE = re.compile(r"(<[^>]+>|&[a-zA-Z0-9#]+;|.)")
_LINK_TAG_RE = re.compile(r"</?a\b[^>]*>", re.IGNORECASE)
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_ATOMIC_WIDGET_RE = re.compile(r"(<a\b[^>]*>.*?</a>|<span\b[^>]*background=[^>]*>.*?</span>)", re.IGNORECASE | re.DOTALL)


class Canvas:
    def __init__(self, total_cols: int = None):
        typo = THEME["typography"]
        geom = THEME["geometry"]
        self.font_family = typo["font_family"]
        self.font_weight = typo["font_weight"]
        self.font_size = float(typo["font_size"])
        self.chars = THEME["ascii"]["chars"]
        self.title_brackets = THEME["ascii"]["title_brackets"]
        cols = total_cols if total_cols is not None else geom["compact_cols"]

        desc = Pango.FontDescription.from_string(
            f"{self.font_family}, Font Awesome 6 Free, Font Awesome 6 Brands, Symbols Nerd Font, monospace {self.font_weight} {self.font_size}"
        )
        screen = Gdk.Screen.get_default()
        if screen is not None:
            pctx = Gdk.pango_context_get_for_screen(screen)
            self.layout = Pango.Layout.new(pctx)
        else:
            self.surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            self.layout = PangoCairo.create_layout(cairo.Context(self.surf))

        self.layout.set_font_description(desc)
        h_char = self.chars.get("h", "\u2500")
        self.char_w = max(1.0, self.measure_text(h_char * 20) / 20.0)
        self.space_w = max(1.0, self.measure_text(" " * 20) / 20.0)
        self.char_h = self.layout.get_pixel_size()[1] or 16
        self.base_pango_size = self.font_size * Pango.SCALE
        self._anchor_v = self.chars.get("v", "\u2502")
        self._anchor_px = self.measure_text(self._anchor_v * 2)
        self.set_cols(cols)

    def set_cols(self, total_cols: int) -> None:
        self.total_cols = max(6, int(total_cols))
        self.inner_cols = max(1, self.total_cols - 4)
        ch = self.chars
        n_dashes = max(1, self.total_cols - 2)
        self.total_px = self.measure_text(f"{ch['bl']}{ch['h'] * n_dashes}{ch['br']}")
        borders_px = self.measure_text(f"{ch['v']}  {ch['v']}")
        self.inner_px = max(self.char_w, self.total_px - borders_px)

    @staticmethod
    def escape(text) -> str:
        if text is None:
            return ""
        s = str(text)
        if "<" not in s and ">" not in s:
            return s.replace("&", "&amp;")
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @staticmethod
    def sanitize_markup(markup_str: str) -> str:
        if not markup_str:
            return ""
        s = str(markup_str)

        def _repl(m):
            tag = m.group(0)
            if re.match(r"^</?\s*(span|a|b|i|u|s|small|big|sub|sup)\b", tag, re.IGNORECASE):
                return tag
            return tag.replace("<", "&lt;").replace(">", "&gt;")

        s_safe = re.sub(r"<[^>]*>", _repl, s)
        return _BARE_AMP_RE.sub("&amp;", s_safe)

    @staticmethod
    def strip_tags(markup_str: str) -> str:
        if not markup_str:
            return ""
        return html.unescape(re.sub(r"<[^>]+>", "", str(markup_str)))

    def measure_text(self, text_str: str) -> float:
        self.layout.set_width(-1)
        self.layout.set_attributes(None)
        self.layout.set_text(str(text_str or ""), -1)
        return self.layout.get_size()[0] / Pango.SCALE

    def measure_markup(self, markup_str: str) -> float:
        if not markup_str:
            return 0.0
        s = self.sanitize_markup(markup_str)
        pango_only = _LINK_TAG_RE.sub("", s)
        v = getattr(self, "_anchor_v", "\u2502")
        anc = getattr(self, "_anchor_px", 0.0)
        self.layout.set_width(-1)
        try:
            self.layout.set_markup(f"{v}{pango_only}{v}", -1)
            return max(0.0, (self.layout.get_size()[0] / Pango.SCALE) - anc)
        except Exception:
            return self.measure_text(self.strip_tags(s))

    def measure_line_height(self, markup_str: str) -> int:
        s = self.sanitize_markup(markup_str or " ")
        pango_only = _LINK_TAG_RE.sub("", s)
        self.layout.set_width(-1)
        try:
            self.layout.set_markup(pango_only, -1)
        except Exception:
            self.layout.set_attributes(None)
            self.layout.set_text(self.strip_tags(s) or " ", -1)
        line = self.layout.get_line(0)
        if line is not None:
            _, logical_rect = line.get_extents()
            return int(logical_rect.height)
        return int(self.layout.get_size()[1])

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

    def _fit_line(self, left_markup: str, right_markup: str, fill_char: str = " ", c_fill: str = None, min_chars: int = 0) -> str:
        base_px = self.measure_markup(left_markup + right_markup)
        gap_px = max(0.0, self.total_px - base_px)
        unit_w = self.space_w if fill_char == " " else self.char_w
        n_chars = max(min_chars, int(gap_px // unit_w))

        def _wrap_fill(body: str) -> str:
            if not body:
                return ""
            return f"<span color='{c_fill}'>{body}</span>" if c_fill else body

        while n_chars > min_chars:
            cand = left_markup + _wrap_fill(fill_char * n_chars) + right_markup
            if self.measure_markup(cand) <= self.total_px + 0.15:
                break
            n_chars -= 1
        while True:
            cand_next = left_markup + _wrap_fill(fill_char * (n_chars + 1)) + right_markup
            if self.measure_markup(cand_next) <= self.total_px + 0.10:
                n_chars += 1
            else:
                break
        cur_line = left_markup + _wrap_fill(fill_char * n_chars) + right_markup
        rem_px = max(0.0, self.total_px - self.measure_markup(cur_line))
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
            return self._int_spaces_for_px(gap_px) + markup_str
        if align == "center":
            left_px = gap_px / 2.0
            left_sp = self._int_spaces_for_px(left_px)
            rem_px = max(0.0, gap_px - self.measure_text(left_sp))
            return left_sp + markup_str + self._int_spaces_for_px(rem_px)
        return markup_str + self._int_spaces_for_px(gap_px)

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

            cur_px = self.measure_markup(out_str)
            if idx > 0 or cur_px < col_start_px - 0.1:
                if align == "right":
                    target_start_px = max(col_start_px, eff_end_px - cell_px)
                elif align == "center":
                    target_start_px = max(col_start_px, col_start_px + max(0.0, (eff_end_px - col_start_px - cell_px) / 2.0))
                else:
                    target_start_px = col_start_px
                min_sp = 1 if idx > 0 and self.strip_tags(out_str).strip() else 0
                needed_px = max(self.space_w * min_sp, target_start_px - cur_px)
                n_sp = max(min_sp, int((needed_px + 0.1) // self.space_w))
                limit_px = eff_end_px if align != "left" else col_start_px
                while n_sp > min_sp and self.measure_markup(out_str + (" " * n_sp) + (raw_cell if align != "left" else "")) > limit_px + 0.15:
                    n_sp -= 1
                out_str += " " * n_sp
            else:
                avail_in_col = max(0.0, eff_end_px - cur_px)
                if align == "right":
                    out_str += self._int_spaces_for_px(max(0.0, avail_in_col - cell_px))
                elif align == "center":
                    out_str += self._int_spaces_for_px(max(0.0, (avail_in_col - cell_px) / 2.0))

            out_str += raw_cell

        if self.measure_markup(out_str) > self.inner_px + 0.3 and not _ATOMIC_WIDGET_RE.search(out_str):
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

    def top(self, title_markup: str, right_markup: str = "", c_box: str = None, c_txt: str = None, c_brk: str = None) -> str:
        c_box = c_box or THEME["colors"]["box_inactive"]
        c_txt = c_txt or THEME["colors"]["focus"]
        c_brk = c_brk or c_box
        ch = self.chars
        if not title_markup and not right_markup:
            return f"<span color='{c_box}'>{ch['tl']}{ch['h'] * max(1, self.total_cols - 2)}{ch['tr']}</span>"
        b_l, b_r = self.title_brackets
        if title_markup:
            left_part = (
                f"<span color='{c_box}'>{ch['tl']}{ch['h']} </span>"
                f"<span color='{c_brk}'>{self.escape(b_l)}</span>"
                f"<span color='{c_txt}'>{self.sanitize_markup(title_markup)}</span>"
                f"<span color='{c_brk}'>{self.escape(b_r)}</span>"
                f"<span color='{c_box}'> </span>"
            )
        else:
            left_part = f"<span color='{c_box}'>{ch['tl']}{ch['h']}</span>"
        right_part = (
            f"<span color='{c_box}'> </span>{self.sanitize_markup(right_markup)}<span color='{c_box}'> {ch['h']}{ch['tr']}</span>"
            if right_markup
            else f"<span color='{c_box}'>{ch['tr']}</span>"
        )
        return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_box, min_chars=1)

    def mid(self, label_markup: str = "", right_markup: str = "", c_box: str = None) -> str:
        c_box = c_box or THEME["colors"]["box_inactive"]
        ch = self.chars
        left_part = f"<span color='{c_box}'>{ch['ml']}{ch['h']} </span>{self.sanitize_markup(label_markup)}<span color='{c_box}'> </span>" if label_markup else f"<span color='{c_box}'>{ch['ml']}</span>"
        right_part = f"<span color='{c_box}'> </span>{self.sanitize_markup(right_markup)}<span color='{c_box}'> {ch['h']}{ch['mr']}</span>" if right_markup else f"<span color='{c_box}'>{ch['mr']}</span>"
        return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_box, min_chars=1)

    def divider_line(self, title_markup: str = "", c_line: str = None, c_txt: str = None, align: str = "left") -> str:
        c_line = c_line or THEME["colors"]["structure"]
        c_txt = c_txt or THEME["colors"]["schema"]
        ch = self.chars
        if not title_markup:
            return f"<span color='{c_line}'>{ch['h'] * max(1, self.total_cols)}</span>"
        b_l, b_r = self.title_brackets
        title_block = f"<span color='{c_line}'>{self.escape(b_l)}</span><span color='{c_txt}'>{self.sanitize_markup(title_markup)}</span><span color='{c_line}'>{self.escape(b_r)}</span>"
        if align == "left":
            left_part = f"<span color='{c_line}'>{ch['h'] * 3} </span>{title_block}<span color='{c_line}'> </span>"
            right_part = f"<span color='{c_line}'>{ch['h']}</span>"
            return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_line, min_chars=1)
        elif align == "right":
            left_part = f"<span color='{c_line}'>{ch['h']}</span>"
            right_part = f"<span color='{c_line}'> </span>{title_block}<span color='{c_line}'> {ch['h'] * 3}</span>"
            return self._fit_line(left_part, right_part, fill_char=ch["h"], c_fill=c_line, min_chars=1)
        else:
            base_px = self.measure_markup(f"<span color='{c_line}'> </span>{title_block}<span color='{c_line}'> </span>")
            gap_px = max(0.0, self.total_px - base_px)
            left_dashes = self._space_fill(gap_px / 2.0, ch["h"])
            left_part = f"<span color='{c_line}'>{left_dashes} </span>{title_block}<span color='{c_line}'> </span>"
            return self._fit_line(left_part, "", fill_char=ch["h"], c_fill=c_line, min_chars=0)

    def row(self, content_markup: str, right_markup: str = "", c_box: str = None, c_txt: str = None, min_height_pango: int = 0) -> str:
        c_box = c_box or THEME["colors"]["box_inactive"]
        c_txt = c_txt or THEME["colors"]["focus"]
        ch = self.chars
        content_markup, right_markup = self.sanitize_markup(content_markup), self.sanitize_markup(right_markup)
        left_border = f"<span color='{c_box}'>{ch['v']}</span> "
        right_border = f" <span color='{c_box}'>{ch['v']}</span>"
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

        if min_height_pango > 0:
            cur_h = self.measure_line_height(line_out)
            if cur_h < min_height_pango:
                base_h = max(1, self.measure_line_height(" "))
                target_pt = int(round(self.base_pango_size * (min_height_pango / float(base_h))))
                line_out += f"<span size='{target_pt}'>\u200b</span>"
        return line_out

    def bot(self, c_box: str = None) -> str:
        c_box = c_box or THEME["colors"]["box_inactive"]
        ch = self.chars
        return f"<span color='{c_box}'>{ch['bl']}{ch['h'] * max(1, self.total_cols - 2)}{ch['br']}</span>"
