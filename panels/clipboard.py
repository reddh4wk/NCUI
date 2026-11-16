#!/usr/bin/env python3
import json
import os
import re
import subprocess
import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
from core.state import STATE
from core.i18n import t, get_active_language
from core import probe
from ui.canvas import Canvas
from ui.kit import Box, Badge, Divider, Txt, SelectPill, ActionBtn
from ui.widgets import AsciiCard
from panels.base import BasePanel

META_FILE = os.path.expanduser("~/.cache/nc_clipboard_meta.json")
PINS_FILE = os.path.expanduser("~/.cache/nc-ui/clipboard_pins.json")


def _load_pins() -> list[str]:
    if not os.path.exists(PINS_FILE):
        return []
    try:
        with open(PINS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [str(x) for x in data if str(x).strip()] if isinstance(data, list) else []
    except Exception:
        return []


def _save_pins(pins: list[str]) -> None:
    try:
        os.makedirs(os.path.dirname(PINS_FILE), exist_ok=True)
        with open(PINS_FILE, "w", encoding="utf-8") as f:
            json.dump(pins, f, indent=2)
    except Exception:
        pass


def is_image_capture_enabled() -> bool:
    return bool(STATE.get("clipboard", "capture_images", True))


def sync_image_watcher(enable: bool) -> None:
    if not probe.has_bin("wl-paste") or not probe.has_bin("cliphist"):
        return
    if enable:
        ok, out = probe.run_cmd(["pgrep", "-f", "wl-paste.*cliphist"], timeout=1.0)
        if not ok or not out.strip():
            probe.spawn_bg(["wl-paste", "--watch", "cliphist", "store"])
        else:
            # Assicura che anche il watcher esplicito per le immagini sia attivo
            ok_i, out_i = probe.run_cmd(["pgrep", "-f", "wl-paste.*--type image.*cliphist"], timeout=1.0)
            if not ok_i or not out_i.strip():
                probe.spawn_bg(["wl-paste", "--type", "image", "--watch", "cliphist", "store"])
    else:
        probe.run_cmd(["pkill", "-f", "wl-paste.*--type image.*cliphist"], timeout=1.0)


def _classify_text(txt: str) -> tuple[str, str]:
    s = txt.strip()
    if re.match(r"^https?://\S+$", s, re.IGNORECASE):
        return "URL", "info"
    if re.match(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$", s):
        return "HEX", "warn"
    if s.startswith(("/", "~/")) and "\n" not in s and len(s) < 180:
        return "PATH", "accent"
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        return "JSON", "info"
    if s.startswith(("sudo ", "git ", "paru ", "pacman ", "systemctl ", "docker ", "tar ", "ssh ")):
        return "CMD", "accent"
    return "TXT", "secondary"


class Panel(BasePanel):
    id = "clipboard"
    icon = "\uf0ea"
    hover_highlight = True

    def __init__(self, shell=None):
        self.title = t("clipboard.title", "Clipboard")
        super().__init__(shell)
        self.search_query = ""
        sync_image_watcher(is_image_capture_enabled())

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        is_it = (get_active_language() == "it")

        if not probe.has_bin("cliphist"):
            b = Box(t("clipboard.title"), icon="\uf0ea")
            b.add_row(Txt(t("clipboard.not_installed"), role="inactive"))
            return [b]

        img_enabled = is_image_capture_enabled()
        ok, out = probe.run_cmd(["cliphist", "list"], timeout=2.0)
        all_lines = [l for l in out.splitlines() if l.strip()] if ok else []

        if not img_enabled:
            # Se la cattura immagini è disattivata, rimuove/filtra i payload binari
            raw_lines = []
            for l in all_lines:
                if "[[ binary data" in l:
                    probe.run_cmd(["cliphist", "delete"], input_data=l.encode("utf-8"), timeout=1.0)
                else:
                    raw_lines.append(l)
        else:
            raw_lines = all_lines

        pins = _load_pins()

        # --- HERO BAR: Contatore, Toggle Cattura Immagini, Ricerca Live & Wipe ---
        search_lbl = f"\uf002 {self.search_query[:10]}" if self.search_query else ("\uf002 Cerca" if is_it else "\uf002 Search")
        hdr_acts = [
            ActionBtn(search_lbl, "clip:search", tone="select" if self.search_query else "neutral", active=bool(self.search_query)),
        ]
        if self.search_query:
            hdr_acts.append(ActionBtn("\uf00d", "clip:clear_search", tone="warn"))
        if raw_lines:
            hdr_acts.append(ActionBtn("Wipe" if not is_it else "Svuota", "clip:wipe", tone="danger_soft"))

        hdr_box = Box(
            f"Clipboard ({len(raw_lines)})",
            icon="\uf0ea",
            actions=hdr_acts,
            highlighted=True,
        )
        img_btn_lbl = (
            f"\uf03e {'Immagini: ON' if is_it else 'Images: ON'}"
            if img_enabled
            else f"\uf03e {'Immagini: OFF' if is_it else 'Images: OFF'}"
        )
        img_toggle_btn = SelectPill(img_btn_lbl, "clip:toggle_images", selected=img_enabled)
        hdr_box.add_row(
            f"  {Txt('Fissati:' if is_it else 'Pinned:', role='schema')} {Txt(str(len(pins)), role='intent')}   "
            f"{Txt('Elementi:' if is_it else 'Items:', role='schema')} {Txt(str(len(raw_lines)), role='focus')}",
            right_markup=img_toggle_btn,
        )
        cards = [hdr_box]

        wrap_w = max(8, canvas.inner_cols - 2)
        q_low = self.search_query.lower().strip()

        # --- SEZIONE PINNED ---
        if pins:
            shown_pins = [(idx, p) for idx, p in enumerate(pins) if not q_low or q_low in p.lower()]
            if shown_pins:
                cards.append(Divider("Pinned Items" if not is_it else "Elementi Fissati", icon="\uf005", align="right"))
                for p_idx, p_txt in shown_pins:
                    kind, b_style = _classify_text(p_txt)
                    p_box = Box(
                        f"Pin #{p_idx + 1}",
                        icon="\uf005",
                        actions=[
                            Badge(kind, style=b_style),
                            ActionBtn("Unpin", f"clip:unpin:{p_idx}", tone="warn"),
                        ],
                        elected=True,
                    )
                    for wl in self._format_preview_lines(p_txt, canvas, wrap_w):
                        p_box.add_row(Txt(f" {wl}", role="focus"))
                    cards.append(
                        AsciiCard(
                            p_box,
                            canvas,
                            on_link=self.shell.dispatch_uri if self.shell else None,
                            on_row_click=(lambda txt=p_txt: self._copy_plain_and_close(txt)),
                        )
                    )

        if not raw_lines:
            if not pins:
                b = Box(t("clipboard.title"), icon="\uf0ea")
                b.add_row(Txt(t("clipboard.empty"), role="inactive"))
                cards.append(b)
            return cards

        meta_data = {}
        if os.path.exists(META_FILE):
            try:
                with open(META_FILE, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
            except Exception:
                pass

        for line_item in raw_lines[:22]:
            parts = line_item.split("\t", 1)
            if len(parts) != 2:
                continue
            item_id, preview = parts
            if q_low and q_low not in preview.lower():
                continue
            ts = meta_data.get(str(item_id), f"#{item_id}")

            if "[[ binary data" in preview:
                if not img_enabled:
                    continue
                try:
                    p1 = subprocess.Popen(["cliphist", "decode"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                    raw_bytes, _ = p1.communicate(input=line_item.encode("utf-8"), timeout=2.0)
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(raw_bytes)
                    loader.close()
                    pixbuf = loader.get_pixbuf()
                    if pixbuf:
                        img_box = Box(
                            ts,
                            icon="\uf03e",
                            actions=[
                                Badge("IMG", style="info"),
                                ActionBtn("x", f"clip:del:{item_id}", tone="danger_soft"),
                            ],
                        )
                        for _ in range(6):
                            img_box.add_row(" ")
                        cards.append(AsciiCard(
                            img_box,
                            canvas,
                            on_link=self.shell.dispatch_uri if self.shell else None,
                            on_row_click=(lambda item=line_item: self._copy_item(item)),
                            pixbuf=pixbuf,
                        ))
                        continue
                except Exception:
                    pass

            ok_d, dec_txt = probe.run_cmd(["cliphist", "decode"], input_data=line_item.encode("utf-8"), timeout=1.5)
            raw_text = (dec_txt if ok_d else preview).replace("\t", "    ")
            if q_low and q_low not in raw_text.lower():
                continue

            kind, b_style = _classify_text(raw_text)
            is_pinned = raw_text.strip() in [p.strip() for p in pins]
            pin_btn = ActionBtn("\uf005", f"clip:pin:{item_id}", tone="select" if is_pinned else "neutral", active=is_pinned)
            del_btn = ActionBtn("x", f"clip:del:{item_id}", tone="danger_soft")

            t_box = Box(ts, icon="\uf0ea", actions=[Badge(kind, style=b_style), pin_btn, del_btn])
            for wl in self._format_preview_lines(raw_text, canvas, wrap_w):
                t_box.add_row(Txt(f" {wl}", role="focus"))
            cards.append(AsciiCard(
                t_box,
                canvas,
                on_link=self.shell.dispatch_uri if self.shell else None,
                on_row_click=(lambda item=line_item: self._copy_item(item)),
            ))
        return cards

    def _format_preview_lines(self, raw_text: str, canvas: Canvas, wrap_w: int) -> list[str]:
        lines_to_show = []
        for para in raw_text.splitlines():
            if len(lines_to_show) >= 3:
                break
            if canvas.measure_markup(Canvas.escape(para)) > (wrap_w * canvas.char_w):
                truncated = canvas.truncate_plain(raw_text, wrap_w * 3)
                for chunk_line in truncated.splitlines():
                    lines_to_show.append(chunk_line)
                    if len(lines_to_show) >= 3:
                        break
            else:
                lines_to_show.append(para)
        if not lines_to_show:
            lines_to_show = [canvas.truncate_plain(raw_text, wrap_w)]
        return lines_to_show[:3]

    def _copy_plain_and_close(self, text: str) -> None:
        probe.copy_clipboard(text)
        if self.shell:
            self.shell.dispatch_uri("ui:close")

    def _copy_item(self, line_item: str) -> None:
        try:
            p1 = subprocess.Popen(["cliphist", "decode"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            decoded, _ = p1.communicate(input=line_item.encode("utf-8"), timeout=1.5)
            p2 = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
            p2.communicate(input=decoded, timeout=1.5)
        except Exception:
            pass
        if self.shell:
            self.shell.dispatch_uri("ui:close")

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        if action_id == "clip_search":
            self.search_query = text.strip()

    def on_link(self, uri: str) -> None:
        if uri == "clip:toggle_images":
            new_val = not is_image_capture_enabled()
            STATE.set("clipboard", "capture_images", new_val)
            sync_image_watcher(new_val)
            self.request_throttled_refresh()
            return
        if uri == "clip:search":
            self.request_prompt("clip_search", "Filter clipboard...", self.search_query, title="Search Clipboard")
            return
        if uri == "clip:clear_search":
            self.search_query = ""
            self.request_throttled_refresh()
            return
        if uri == "clip:wipe":
            probe.run_cmd(["cliphist", "wipe"], timeout=2.0)
            self.request_throttled_refresh()
            return
        if uri.startswith("clip:unpin:"):
            try:
                idx = int(uri.split(":", 2)[2])
                pins = _load_pins()
                if 0 <= idx < len(pins):
                    pins.pop(idx)
                    _save_pins(pins)
            except Exception:
                pass
            self.request_throttled_refresh()
            return
        if uri.startswith("clip:pin:") or uri.startswith("clip:del:"):
            act = uri.split(":", 2)[1]
            target_id = uri.split(":", 2)[2]
            ok, out = probe.run_cmd(["cliphist", "list"], timeout=1.5)
            if ok and out:
                match_line = next((l for l in out.splitlines() if l.startswith(f"{target_id}\t")), None)
                if match_line:
                    if act == "del":
                        probe.run_cmd(["cliphist", "delete"], input_data=match_line.encode("utf-8"), timeout=1.5)
                    elif act == "pin":
                        ok_d, dec_txt = probe.run_cmd(["cliphist", "decode"], input_data=match_line.encode("utf-8"), timeout=1.5)
                        if ok_d and dec_txt.strip():
                            pins = _load_pins()
                            val = dec_txt.strip()
                            if val in pins:
                                pins.remove(val)
                            else:
                                pins.insert(0, val)
                            _save_pins(pins)
            self.request_throttled_refresh()
