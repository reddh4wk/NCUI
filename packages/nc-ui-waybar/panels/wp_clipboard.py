#!/usr/bin/env python3
import datetime
import io
import json
import os
import re
import subprocess
import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf
from core.config import get_cache_dir
from core.state import STATE
from core.i18n import t
from core import probe
from ui.canvas import Canvas
from ui.kit import (
    ActionBtn,
    Box,
    InlineFilter,
    SelectPill,
    Txt,
    get_active_inline_entry,
    get_inline_entry_buffer,
    start_inline_input,
)
from ui.widgets import AsciiCard
from panels.base import BasePanel
from panels.wp_capture import _screenshots_dir

_THUMB_W = 440
_THUMB_H = 96
_MEM_TEXT_CACHE: dict[str, str] | None = None
_MEM_PIXBUF_CACHE: dict[str, GdkPixbuf.Pixbuf] = {}


def _meta_file() -> str:
    env_c = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = os.path.expanduser(env_c) if env_c else os.path.expanduser("~/.cache")
    return os.path.join(base, "nc_clipboard_meta.json")


def _pins_file(for_write: bool = False) -> str:
    path = os.path.join(get_cache_dir(), "clipboard_pins.json")
    if not for_write and not os.path.exists(path) and not os.environ.get("XDG_CACHE_HOME", "").strip():
        legacy = os.path.expanduser("~/.cache/nc-ui/clipboard_pins.json")
        if os.path.exists(legacy):
            return legacy
    return path


def _cache_dir() -> str:
    rt = os.environ.get("XDG_RUNTIME_DIR", "").strip() or "/tmp"
    return os.path.join(os.path.expanduser(rt), "nc_clipboard_cache")


def _text_cache_file() -> str:
    return os.path.join(_cache_dir(), "texts.json")


def _load_text_cache() -> dict[str, str]:
    global _MEM_TEXT_CACHE
    if _MEM_TEXT_CACHE is not None:
        return _MEM_TEXT_CACHE
    tcf = _text_cache_file()
    if os.path.exists(tcf):
        try:
            with open(tcf, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _MEM_TEXT_CACHE = {str(k): str(v) for k, v in data.items()}
                    return _MEM_TEXT_CACHE
        except Exception:
            pass
    _MEM_TEXT_CACHE = {}
    return _MEM_TEXT_CACHE


def _save_text_cache(cache: dict[str, str]) -> None:
    cdir = _cache_dir()
    try:
        os.makedirs(cdir, exist_ok=True)
        tmp = os.path.join(cdir, ".texts.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, _text_cache_file())
    except Exception:
        pass


def _get_image_thumb_pixbuf(item_id: str, line_item: str, preview: str) -> GdkPixbuf.Pixbuf | None:
    cached = _MEM_PIXBUF_CACHE.get(item_id)
    if cached is not None:
        return cached
    cdir = _cache_dir()
    thumb_p = os.path.join(cdir, f"thumb_{item_id}.png")
    if os.path.exists(thumb_p):
        try:
            surf = cairo.ImageSurface.create_from_png(thumb_p)
            pb = Gdk.pixbuf_get_from_surface(surf, 0, 0, surf.get_width(), surf.get_height())
            if pb:
                _MEM_PIXBUF_CACHE[item_id] = pb
                return pb
        except Exception:
            pass
    try:
        p1 = subprocess.Popen(["cliphist", "decode"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        raw_bytes, _ = p1.communicate(input=line_item.encode("utf-8"), timeout=2.0)
        if not raw_bytes:
            return None
        if " png " in preview.lower() or raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            try:
                src = cairo.ImageSurface.create_from_png(io.BytesIO(raw_bytes))
                sw, sh = src.get_width(), src.get_height()
                if sw > 0 and sh > 0:
                    dst = cairo.ImageSurface(cairo.FORMAT_ARGB32, _THUMB_W, _THUMB_H)
                    cr = cairo.Context(dst)
                    scale = max(_THUMB_W / float(sw), _THUMB_H / float(sh))
                    cr.scale(scale, scale)
                    cr.set_source_surface(src, (_THUMB_W / scale - sw) * 0.5, (_THUMB_H / scale - sh) * 0.5)
                    cr.paint()
                    os.makedirs(cdir, exist_ok=True)
                    tmp_png = os.path.join(cdir, f".thumb_{item_id}.png.tmp")
                    dst.write_to_png(tmp_png)
                    os.replace(tmp_png, thumb_p)
                    pb = Gdk.pixbuf_get_from_surface(dst, 0, 0, _THUMB_W, _THUMB_H)
                    if pb:
                        _MEM_PIXBUF_CACHE[item_id] = pb
                        return pb
            except Exception:
                pass
        loader = GdkPixbuf.PixbufLoader()
        loader.write(raw_bytes)
        loader.close()
        pb = loader.get_pixbuf()
        if pb:
            _MEM_PIXBUF_CACHE[item_id] = pb
            return pb
    except Exception:
        pass
    return None


def _detect_image_ext(raw_bytes: bytes, match_line: str) -> str:
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
        return "webp"
    if raw_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if raw_bytes.startswith(b"BM"):
        return "bmp"
    low = match_line.lower()
    if " jpg " in low or " jpeg " in low:
        return "jpg"
    return "webp" if " webp " in low else "png"


def _save_clipboard_image(item_id: str) -> tuple[bool, str]:
    ok, out = probe.run_cmd(["cliphist", "list"], timeout=1.5)
    if not ok or not out:
        return False, ""
    match_line = next((l for l in out.splitlines() if l.startswith(f"{item_id}\t")), None)
    if not match_line:
        return False, ""
    try:
        p1 = subprocess.Popen(["cliphist", "decode"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        raw_bytes, _ = p1.communicate(input=match_line.encode("utf-8"), timeout=2.5)
        if not raw_bytes:
            return False, ""
        ext = _detect_image_ext(raw_bytes, match_line)
        s_dir = _screenshots_dir()
        os.makedirs(s_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        fname = f"clipboard_{item_id}_{stamp}.{ext}"
        dest_path = os.path.join(s_dir, fname)
        tmp_path = os.path.join(s_dir, f".{fname}.tmp")
        with open(tmp_path, "wb") as f:
            f.write(raw_bytes)
        os.replace(tmp_path, dest_path)
        if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
            return True, os.path.join(os.path.basename(s_dir), fname)
    except Exception:
        pass
    return False, ""


def _load_pins() -> list[str]:
    pf = _pins_file(for_write=False)
    if not os.path.exists(pf):
        return []
    try:
        with open(pf, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [str(x) for x in data if str(x).strip()] if isinstance(data, list) else []
    except Exception:
        return []


def _save_pins(pins: list[str]) -> None:
    pf = _pins_file(for_write=True)
    try:
        os.makedirs(os.path.dirname(pf), exist_ok=True)
        with open(pf, "w", encoding="utf-8") as f:
            json.dump(pins, f, indent=2)
    except Exception:
        pass


def is_image_capture_enabled() -> bool:
    return bool(STATE.get("clipboard", "capture_images", True))


def sync_image_watcher(enable: bool) -> None:
    if not probe.has_bin("wl-paste") or not probe.has_bin("cliphist"):
        return
    probe.run_cmd(["pkill", "-f", "^wl-paste --watch cliphist store$"], timeout=1.0)
    ok_t, out_t = probe.run_cmd(["pgrep", "-f", "wl-paste.*--type text.*cliphist"], timeout=1.0)
    if not ok_t or not out_t.strip():
        probe.spawn_bg(["wl-paste", "--type", "text", "--watch", "cliphist", "store"])
    if enable:
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
    if s.startswith(("sudo ", "git ", "paru ", "pacman ", "apt ", "dnf ", "zypper ", "flatpak ", "systemctl ", "docker ", "tar ", "ssh ")):
        return "CMD", "accent"
    return "TXT", "secondary"


class Panel(BasePanel):
    id = "clipboard"
    icon = "\uf0ea"
    hover_highlight = True
    refresh_ms = 1500

    def __init__(self, shell=None):
        self.title = t("clipboard.title") if shell is not None else self.id
        super().__init__(shell)
        self.search_query = ""
        self.last_saved: tuple[str, bool, str] | None = None
        self._last_clip_sig = None
        if os.environ.get("NC_UI_OFFSCREEN") != "1":
            probe.run_async(lambda: sync_image_watcher(is_image_capture_enabled()))

    def _compute_clip_sig(self) -> tuple | None:
        if not probe.has_bin("cliphist"):
            return None
        img_enabled = is_image_capture_enabled()
        ok, out = probe.run_cmd(["cliphist", "list"], timeout=1.5)
        lines = [l for l in out.splitlines() if l.strip()] if ok else []
        if not img_enabled:
            lines = [l for l in lines if "[[ binary data" not in l]
        return (tuple(lines[:30]), len(lines), tuple(_load_pins()), img_enabled)

    def should_refresh_on_tick(self) -> bool:
        sig = self._compute_clip_sig()
        changed = self._last_clip_sig is not None and sig != self._last_clip_sig
        self._last_clip_sig = sig
        return changed

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        if not probe.has_bin("cliphist"):
            b = Box(t("clipboard.title"), icon="\uf0ea")
            b.add_row(" ")
            b.add_row(f"  {Txt(t('clipboard.not_installed'), role='inactive')}")
            b.add_row(" ")
            return [b]

        img_enabled = is_image_capture_enabled()
        ok, out = probe.run_cmd(["cliphist", "list"], timeout=2.0)
        all_lines = [l for l in out.splitlines() if l.strip()] if ok else []

        if not img_enabled:
            raw_lines = [l for l in all_lines if "[[ binary data" not in l]
            bin_lines = [l for l in all_lines if "[[ binary data" in l]
            if bin_lines and os.environ.get("NC_UI_OFFSCREEN") != "1":
                def _purge_bin(lines_to_del=bin_lines):
                    for bl in lines_to_del:
                        probe.run_cmd(["cliphist", "delete"], input_data=bl.encode("utf-8"), timeout=1.0)
                probe.run_async(_purge_bin)
        else:
            raw_lines = all_lines

        pins = _load_pins()
        self._last_clip_sig = (tuple(raw_lines[:30]), len(raw_lines), tuple(pins), img_enabled)
        live_q = get_inline_entry_buffer() if get_active_inline_entry() == "wp_clip_flt" else self.search_query

        # --- HERO BAR: Contatore, Toggle Cattura Immagini, Ricerca Live & Wipe ---
        hdr_acts = [ActionBtn(t("clipboard.wipe"), "clip:wipe", tone="danger_soft")] if raw_lines else []

        hdr_box = Box(
            t("clipboard.clipboard", p0=len(raw_lines)),
            icon="\uf0ea",
            actions=hdr_acts,
            highlighted=True,
        )
        img_btn_lbl = (
            f"\uf03e {t('clipboard.images_on')}"
            if img_enabled
            else f"\uf03e {t('clipboard.images_off')}"
        )
        img_toggle_btn = SelectPill(img_btn_lbl, "clip:toggle_images", selected=img_enabled)
        hdr_box.add_row(" ")
        hdr_box.add_row(
            f"  {Txt(t('clipboard.pinned'), role='schema')} {Txt(str(len(pins)), role='val')}   "
            f"{Txt(t('clipboard.items'), role='schema')} {Txt(str(len(raw_lines)), role='val')}",
            right_markup=img_toggle_btn,
        )
        hdr_box.add_row(
            f"  {InlineFilter('wp_clip_flt', live_q, t('clipboard.filter_clipboard'), 'clip:search', clear_uri='clip:clear_search' if live_q else '', width=26, compact=False, active=False)}"
        )
        hdr_box.add_row(" ")
        cards = [hdr_box]

        wrap_w = max(8, canvas.inner_cols - 3)
        q_low = live_q.lower().strip()

        # --- SEZIONE PINNED ---
        if pins:
            shown_pins = [(idx, p) for idx, p in enumerate(pins) if not q_low or q_low in p.lower()]
            if shown_pins:
                for p_idx, p_txt in shown_pins:
                    kind, _b_style = _classify_text(p_txt)
                    p_box = Box(
                        t("clipboard.pin", p0=p_idx + 1),
                        icon="\uf005",
                        actions=[
                            Txt(kind, role="schema"),
                            ActionBtn(t("clipboard.unpin"), f"clip:unpin:{p_idx}", tone="warn"),
                        ],
                        highlighted=False,
                    )
                    p_box.add_row(" ")
                    for wl in self._format_preview_lines(p_txt, canvas, wrap_w):
                        p_box.add_row(f"  {Txt(wl, role='focus')}")
                    p_box.add_row(" ")
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
                hdr_box.add_section()
                hdr_box.add_row(f"  {Txt(t("clipboard.empty"), role='inactive')}")
                hdr_box.add_row(" ")
            return cards

        meta_data = {}
        mf = _meta_file()
        if os.path.exists(mf):
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
            except Exception:
                pass

        txt_cache = _load_text_cache()
        dirty_txt = False
        pins_set = {p.strip() for p in pins}

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
                pixbuf = _get_image_thumb_pixbuf(item_id, line_item, preview)
                if pixbuf:
                    is_just_saved = bool(self.last_saved and self.last_saved[0] == str(item_id) and self.last_saved[1])
                    save_lbl = t("clipboard.saved_btn") if is_just_saved else t("clipboard.save_to_file")
                    img_box = Box(
                        ts,
                        icon="\uf03e",
                        actions=[
                            ActionBtn(save_lbl, f"clip:save:{item_id}", tone="apply"),
                            Txt(t("clipboard.img"), role="schema"),
                            ActionBtn("x", f"clip:del:{item_id}", tone="danger_soft"),
                        ],
                        highlighted=False,
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

            if item_id in txt_cache:
                raw_text = txt_cache[item_id]
            else:
                ok_d, dec_txt = probe.run_cmd(["cliphist", "decode"], input_data=line_item.encode("utf-8"), timeout=1.5)
                raw_text = (dec_txt if ok_d else preview).replace("\t", "    ")
                if ok_d:
                    txt_cache[item_id] = raw_text
                    dirty_txt = True
            if q_low and q_low not in raw_text.lower():
                continue

            kind, _b_style = _classify_text(raw_text)
            is_pinned = raw_text.strip() in pins_set
            pin_btn = SelectPill("\uf005", f"clip:pin:{item_id}", selected=is_pinned)
            del_btn = ActionBtn("x", f"clip:del:{item_id}", tone="danger_soft")

            t_box = Box(ts, icon="\uf0ea", actions=[Txt(kind, role="schema"), pin_btn, del_btn], highlighted=False)
            t_box.add_row(" ")
            for wl in self._format_preview_lines(raw_text, canvas, wrap_w):
                t_box.add_row(f"  {Txt(wl, role='focus')}")
            t_box.add_row(" ")
            cards.append(AsciiCard(
                t_box,
                canvas,
                on_link=self.shell.dispatch_uri if self.shell else None,
                on_row_click=(lambda item=line_item: self._copy_item(item)),
            ))

        if dirty_txt:
            _save_text_cache(txt_cache)
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
            copy_cmd = ["wl-copy"]
            if "[[ binary data" in line_item:
                low = line_item.lower()
                if " jpg " in low or " jpeg " in low:
                    copy_cmd = ["wl-copy", "--type", "image/jpeg"]
                elif " webp " in low:
                    copy_cmd = ["wl-copy", "--type", "image/webp"]
                else:
                    copy_cmd = ["wl-copy", "--type", "image/png"]
            p2 = subprocess.Popen(copy_cmd, stdin=subprocess.PIPE)
            p2.communicate(input=decoded, timeout=1.5)
        except Exception:
            pass
        if self.shell:
            self.shell.dispatch_uri("ui:close")

    def on_link(self, uri: str) -> None:
        if uri == "clip:toggle_images":
            new_val = not is_image_capture_enabled()
            STATE.set("clipboard", "capture_images", new_val)
            sync_image_watcher(new_val)
            self.request_throttled_refresh()
            return
        if uri == "clip:search":
            start_inline_input(
                "wp_clip_flt",
                shell=self.shell,
                trigger_uri=uri,
                initial_text=self.search_query,
                max_len=40,
                on_change=lambda s: setattr(self, "search_query", str(s or "")),
                on_commit=lambda s: setattr(self, "search_query", str(s or "").strip()),
            )
            return
        if uri == "clip:clear_search":
            self.search_query = ""
            self.request_throttled_refresh()
            return
        if uri == "clip:wipe":
            probe.run_cmd(["cliphist", "wipe"], timeout=2.0)
            _MEM_PIXBUF_CACHE.clear()
            if _MEM_TEXT_CACHE is not None:
                _MEM_TEXT_CACHE.clear()
            cdir = _cache_dir()
            if os.path.isdir(cdir):
                for fn in os.listdir(cdir):
                    try:
                        os.remove(os.path.join(cdir, fn))
                    except OSError:
                        pass
            self.request_throttled_refresh()
            return
        if uri.startswith("clip:save:"):
            target_id = uri.split(":", 2)[2]
            ok_s, fname = _save_clipboard_image(target_id)
            self.last_saved = (str(target_id), ok_s, fname)
            if self.shell:
                msg = t("clipboard.saved_msg", p0=fname) if ok_s else t("clipboard.save_err_msg", p0=target_id)
                self.shell.show_toast(msg, level=("ok" if ok_s else "err"), title=t("clipboard.title"))
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
                        _MEM_PIXBUF_CACHE.pop(target_id, None)
                        txt_c = _load_text_cache()
                        if target_id in txt_c:
                            txt_c.pop(target_id, None)
                            _save_text_cache(txt_c)
                        thumb_p = os.path.join(_cache_dir(), f"thumb_{target_id}.png")
                        if os.path.exists(thumb_p):
                            try:
                                os.remove(thumb_p)
                            except OSError:
                                pass
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
