#!/usr/bin/env python3
import glob
import json
import os
import re
import shlex
import tempfile
import time
import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from core.config import (
    THEME,
    VALID_CAPTURE_ACTIONS,
    get_capture_action,
    get_waybar_config_dir,
    set_capture_action,
    strip_jsonc_comments,
)
from core.paths import find_all_package_files
from core.i18n import t
from core import probe, log
from ui.canvas import Canvas
from ui.kit import Box, InlineField, Txt, SelectPill, ActionBtn, start_inline_input
from ui.widgets import AsciiCard
from panels.base import BasePanel


def _screenshots_dir() -> str:
    pic = os.environ.get("XDG_PICTURES_DIR", "").strip()
    if not pic:
        cfg_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
        ud = os.path.join(os.path.expanduser(cfg_home) if cfg_home else os.path.expanduser("~/.config"), "user-dirs.dirs")
        if os.path.isfile(ud):
            try:
                with open(ud, "r", encoding="utf-8", errors="ignore") as f:
                    for ln in f:
                        m = re.match(r'^\s*XDG_PICTURES_DIR\s*=\s*"?([^"\n]+)"?', ln)
                        if m:
                            pic = os.path.expandvars(m.group(1).strip())
                            break
            except OSError:
                pass
    base = os.path.expanduser(pic) if pic else os.path.expanduser("~/Pictures")
    return os.path.join(base, "Screenshots")


def _ram_state_file() -> str:
    rt = os.environ.get("XDG_RUNTIME_DIR", "").strip() or "/tmp"
    return os.path.join(os.path.expanduser(rt), "nc_capture_session.json")

_CAPTURE_ACTION_KEYS = [
    ("clipboard",       "\uf0ea  ", "act_clipboard"),
    ("satty",           "\uf1fc  ", "act_satty"),
    ("save",            "\uf0c7  ", "act_save"),
    ("clipboard_satty", "",        "act_clip_satty"),
]

MODE_ICONS = {
    "area":   "\uf125",
    "window": "\uf2d0",
    "screen": "\uf108",
}


def _read_ram_state() -> dict:
    rf = _ram_state_file()
    if not os.path.exists(rf):
        return {}
    try:
        with open(rf, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_ram_state(data: dict) -> None:
    rf = _ram_state_file()
    rdir = os.path.dirname(rf)
    try:
        os.makedirs(rdir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=rdir, prefix=".nc_cap_ram_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, rf)
    except Exception as e:
        log.error(f"_write_ram_state error: {e}")


def get_active_capture_action() -> str:
    """
    Restituisce l'azione eletta nella sessione corrente (in RAM).
    Se il PC è appena stato avviato e non c'è ancora uno stato in RAM,
    inizializza la sessione in RAM con l'azione di default da config.toml.
    """
    ram = _read_ram_state()
    act = str(ram.get("elected_action", "")).strip().lower()
    if act in VALID_CAPTURE_ACTIONS:
        return act
    default_act = get_capture_action()
    ram["elected_action"] = default_act
    _write_ram_state(ram)
    return default_act


def set_active_capture_action(action: str) -> str:
    """Imposta l'azione eletta esclusivamente in RAM fino a nuova scelta o riavvio."""
    clean = str(action or "").strip().lower()
    if clean not in VALID_CAPTURE_ACTIONS:
        clean = get_capture_action()
    ram = _read_ram_state()
    ram["elected_action"] = clean
    _write_ram_state(ram)
    return clean


def is_copy_last_delay_enabled() -> bool:
    """Restituisce True se il checkbox di copia del delay dall'ultima selezione è attivo."""
    ram = _read_ram_state()
    return bool(ram.get("copy_last_delay", False))


def toggle_copy_last_delay() -> bool:
    """Inverte in RAM l'opzione di copia del delay dall'ultima selezione."""
    ram = _read_ram_state()
    new_val = not bool(ram.get("copy_last_delay", False))
    ram["copy_last_delay"] = new_val
    _write_ram_state(ram)
    return new_val


def _invalidate_last_selection(ram: dict) -> None:
    ram.pop("last_selection", None)
    if str(ram.get("selected_mode", "")).strip().lower() == "last":
        ram["selected_mode"] = "area"
    _write_ram_state(ram)


def _get_niri_json(subcmd: str):
    ok, out = probe.run_cmd(["niri", "msg", "-j", subcmd], timeout=0.6)
    if ok and out:
        try:
            return json.loads(out)
        except Exception:
            pass
    return None


def get_last_selection() -> dict | None:
    """
    Restituisce l'ultima selezione di cattura effettuata dall'avvio del PC (in RAM):
    {"mode": "area"|"window"|"screen", "geom": "X,Y WxH", "output": "DP-1", "win_id": "12", "delay": 0}
    oppure None se non è ancora stato fatto alcuno screenshot o se il target non è più disponibile.
    """
    ram = _read_ram_state()
    sel = ram.get("last_selection")
    if not isinstance(sel, dict):
        return None
    mode = str(sel.get("mode", "")).strip()
    geom = str(sel.get("geom", "")).strip()
    output = str(sel.get("output", "")).strip()
    win_id = str(sel.get("win_id", "")).strip()
    try:
        saved_delay = max(0, int(sel.get("delay", 0)))
    except Exception:
        saved_delay = 0

    if mode == "area":
        m = re.match(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s+(\d+)x(\d+)\s*$", geom)
        if not m:
            _invalidate_last_selection(ram)
            return None
        gx, gy, gw, gh = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if gw <= 0 or gh <= 0:
            _invalidate_last_selection(ram)
            return None
        outs = _get_niri_json("outputs")
        if isinstance(outs, dict):
            rects = [v["logical"] for v in outs.values() if isinstance(v, dict) and isinstance(v.get("logical"), dict)]
            if rects:
                cx, cy = gx + (gw / 2.0), gy + (gh / 2.0)
                if not any(
                    r.get("x", 0) <= cx <= r.get("x", 0) + r.get("width", 0)
                    and r.get("y", 0) <= cy <= r.get("y", 0) + r.get("height", 0)
                    for r in rects
                ):
                    _invalidate_last_selection(ram)
                    return None
        return {"mode": "area", "geom": geom, "output": "", "win_id": "", "delay": saved_delay}

    if mode == "window":
        if not win_id:
            _invalidate_last_selection(ram)
            return None
        wins = _get_niri_json("windows")
        if isinstance(wins, list) and not any(str(w.get("id", "")) == win_id for w in wins if isinstance(w, dict)):
            _invalidate_last_selection(ram)
            return None
        return {"mode": "window", "geom": "", "output": "", "win_id": win_id, "delay": saved_delay}

    if mode == "screen":
        if not output:
            _invalidate_last_selection(ram)
            return None
        outs = _get_niri_json("outputs")
        if isinstance(outs, dict):
            active_outs = {k: v for k, v in outs.items() if isinstance(v, dict) and v.get("logical")}
            if output not in active_outs:
                _invalidate_last_selection(ram)
                return None
        return {"mode": "screen", "geom": "", "output": output, "win_id": "", "delay": saved_delay}

    return None


def get_active_capture_mode() -> str:
    last_sel = get_last_selection()
    ram = _read_ram_state()
    mode = str(ram.get("selected_mode", "")).strip().lower()
    if mode == "last":
        if last_sel is not None:
            return "last"
        ram["selected_mode"] = "area"
        _write_ram_state(ram)
        return "area"
    if mode in ("area", "window", "screen"):
        return mode
    fallback = last_sel["mode"] if last_sel is not None else "area"
    ram["selected_mode"] = fallback
    _write_ram_state(ram)
    return fallback


def set_active_capture_mode(mode: str) -> str:
    clean = str(mode or "").strip().lower()
    if clean not in ("area", "window", "screen", "last"):
        clean = "area"
    if clean == "last" and get_last_selection() is None:
        clean = "area"
    ram = _read_ram_state()
    ram["selected_mode"] = clean
    _write_ram_state(ram)
    return clean


def _get_focused_output_name() -> str:
    data = _get_niri_json("focused-output")
    if isinstance(data, dict) and data.get("name"):
        return str(data["name"]).strip()
    outs = _get_niri_json("outputs")
    if isinstance(outs, dict):
        for k, v in outs.items():
            if isinstance(v, dict) and v.get("logical"):
                return str(k).strip()
    return ""


def is_screenshot_plugin_active() -> bool:
    """Return True if Waybar has the custom/screenshot plugin enabled."""
    for path in (
        [os.path.join(get_waybar_config_dir(), "config.jsonc")]
        + find_all_package_files("config", "waybar", "config.jsonc")
    ):
        if not os.path.exists(path):
            continue
        try:
            raw = open(path, "r", encoding="utf-8").read()
            d = json.loads(strip_jsonc_comments(raw))
            if isinstance(d, dict):
                mods = (
                    list(d.get("modules-left") or [])
                    + list(d.get("modules-center") or [])
                    + list(d.get("modules-right") or [])
                )
                return "custom/screenshot" in mods
        except Exception:
            continue
    return False


def get_last_screenshot() -> str:
    s_dir = _screenshots_dir()
    if not os.path.exists(s_dir):
        return ""
    files = glob.glob(os.path.join(s_dir, "*.png")) + glob.glob(os.path.join(s_dir, "*.jpg"))
    if not files:
        return ""
    return max(files, key=os.path.getctime)


class Panel(BasePanel):
    id = "capture"
    icon = "\uf030"

    def __init__(self, shell=None):
        self.title = t("capture.title") if shell is not None else self.id
        super().__init__(shell)

    def get_bar_pill(self) -> dict:
        text = Txt(f"\uf030  {t('capture.pill_label')}", role="focus")
        return {"text": text, "tooltip": "", "class": "module-nc"}

    def on_bar_action(self, action: str, args: list = None) -> None:
        """
        Gestisce il click destro sull'icona Waybar (`nc-ctl action capture repeat`)
        e l'azione rapida `nc-screenshot`:
        se il plugin `custom/screenshot` è attivo, usa modalità, delay e action del pannello;
        altrimenti esegue uno screenshot istantaneo in modalità Area (`clipboard_satty`).
        """
        if action in ("repeat", "quick", "nc-screenshot", ""):
            if is_screenshot_plugin_active():
                mode = get_active_capture_mode()
                act_mode = get_active_capture_action()
                self._trigger_capture(mode, delay=None, act_mode=act_mode, close_ui=False)
            else:
                self._trigger_capture("area", delay=0, act_mode="clipboard_satty", close_ui=False)

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        target_cols = int(THEME["geometry"]["compact_cols"])
        canvas.set_cols(target_cols)

        cards = []

        # --- BOX 1: Quick Capture Mode ---
        box_mode = Box(
            t("capture.quick_mode_title"),
            icon="\uf030",
            highlighted=False,
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        last_sel = get_last_selection()
        active_mode = get_active_capture_mode()

        b_area = ActionBtn(
            f"\uf125 {t('capture.mode_area')}",
            "cap:trigger:area",
            tone="apply",
        )
        b_win = ActionBtn(
            f"\uf2d0 {t('capture.mode_window')}",
            "cap:trigger:window",
            tone="apply",
        )
        b_scr = ActionBtn(
            f"\uf108 {t('capture.mode_screen')}",
            "cap:trigger:screen",
            tone="apply",
        )
        row_btns = f"{b_area}   {b_win}   {b_scr}"

        # Calcola il margine sinistro esatto della riga sopra per allineare l'ultima selezione
        target_px = max(0.0, canvas.inner_cols * canvas.char_w)
        row1_px = canvas.measure_markup(row_btns)
        left_pad = canvas._space_fill(max(0.0, (target_px - row1_px) / 2.0), " ")

        last_lbl = t("capture.mode_last")
        if last_sel is not None:
            mode_ic = MODE_ICONS.get(last_sel["mode"], "\uf125")
            b_last = ActionBtn(
                f"{last_lbl} ({mode_ic})",
                "cap:trigger:last",
                tone="apply",
            )
        else:
            b_last = Txt(last_lbl.replace(" ", "\u00a0"), role="inactive")

        copy_delay = is_copy_last_delay_enabled()
        chk_delay = SelectPill(t("capture.copy_delay_chk"), "cap:toggle_copy_delay", selected=copy_delay)
        right_pad = left_pad if canvas.measure_markup(f"{left_pad}{b_last}  {chk_delay}{left_pad}") <= canvas.inner_px else ""

        box_mode.add_row(" ")
        box_mode.add_row(canvas.pad_cell(row_btns, canvas.inner_cols, align="center", truncate=False))
        box_mode.add_row(" ")
        box_mode.add_row(f"{left_pad}{b_last}", right_markup=f"{chk_delay}{right_pad}")
        box_mode.add_row(" ")
        cards.append(box_mode)

        # --- BOX 2: Shared Parameters (Delay con icona orologio e respiro verticale) ---
        box_params = Box(
            t("capture.shared_params_title"),
            icon="\uf013",
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        delay = int(self.get_state("delay", 0))
        cur_action = get_active_capture_action()
        def_action = get_capture_action()

        b_minus = ActionBtn("-", "cap:delay:dec", tone="neutral")
        b_plus = ActionBtn("+", "cap:delay:inc", tone="neutral")
        b_val = InlineField(
            "wp_cap_delay",
            "cap:delay_inline",
            value=f"{delay}s" if delay > 0 else "",
            empty_label="0s..",
            placeholder="0..9999",
            width=6,
            icon="\u270e",
            tone="neutral",
            selected=False,
        )

        box_params.add_row(" ")
        box_params.add_row(
            f"  {Txt('\uf017', role='schema')}  {Txt(t('capture.delay_label'), role='schema')}",
            right_markup=f"{b_minus} {b_val} {b_plus}",
        )
        box_params.add_row(" ")
        cards.append(box_params)

        # --- BOX 3: Action (Titolo nella cornice + Tabella centrata verticalmente ed equidistante) ---
        action_hdr_title = t("capture.action_label").rstrip(":")
        box_action = Box(
            action_hdr_title,
            icon="\uf0ae",
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        box_action.add_row(" ")

        def_lbl = t("capture.default")
        action_rows = []
        for act_key, act_ic, t_key in _CAPTURE_ACTION_KEYS:
            is_elected = (act_key == cur_action)
            is_default = (act_key == def_action)
            opt_link = SelectPill(f"{act_ic}{t(f'capture.{t_key}')}", f"cap:action:{act_key}", selected=is_elected)
            def_btn = SelectPill(def_lbl, f"cap:default:{act_key}", selected=is_default)
            action_rows.append([opt_link, def_btn])

        box_action.add_table(
            headers=[],
            widths=["flex", max(12, len(def_lbl) + 5)],
            rows=action_rows,
            aligns=["left", "right"],
            uniform_row_height=True,
            show_headers=False,
        )
        box_action.add_row(" ")
        cards.append(box_action)

        # --- BOX 4: Last Saved Capture + Cropped Preview ---
        last_file = get_last_screenshot()
        if last_file:
            fname = os.path.basename(last_file)
            fname_trunc = canvas.truncate_plain(fname, 22)
            ftime = time.strftime("%H:%M", time.localtime(os.path.getctime(last_file)))
            pixbuf = None
            if last_file.lower().endswith(".png"):
                try:
                    surf = cairo.ImageSurface.create_from_png(last_file)
                    pixbuf = Gdk.pixbuf_get_from_surface(surf, 0, 0, surf.get_width(), surf.get_height())
                except Exception:
                    pixbuf = None
            if pixbuf is None:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(last_file)
                except Exception:
                    pixbuf = None
            if pixbuf:
                prev_box = Box(
                    f"{fname_trunc} ({ftime})",
                    icon="\uf03e",
                    fixed_width=True,
                    min_cols=target_cols,
                    max_cols=target_cols,
                )
                for _ in range(10):
                    prev_box.add_row(" ")
                cards.append(
                    AsciiCard(
                        prev_box,
                        canvas,
                        on_row_click=(lambda fp=last_file: self.on_link(f"cap:annotate:{fp}")),
                        pixbuf=pixbuf,
                    )
                )
            act_box = Box(
                t("capture.last_capture_title"),
                icon="\uf15b",
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            act_box.add_row(" ")
            if not pixbuf:
                act_box.add_row(f"  {Txt(fname_trunc, role='focus')} {Txt(f'({ftime})', role='schema')}")
            b_ann = ActionBtn(f"\uf1fc {t('capture.annotate')}", f"cap:annotate:{last_file}", tone="neutral")
            b_cp = ActionBtn(f"\uf0c5 {t('common.copy')}", f"cap:copy:{last_file}", tone="neutral")
            b_dir = ActionBtn(f"\uf07c {t('capture.folder')}", "cap:open_dir", tone="neutral")
            act_box.add_row(canvas.pad_cell(f"{b_ann}   {b_cp}   {b_dir}", canvas.inner_cols, align="center", truncate=False))
            act_box.add_row(" ")
            cards.append(act_box)
        else:
            box_last = Box(
                t("capture.last_capture_title"),
                icon="\uf03e",
                fixed_width=True,
                min_cols=target_cols,
                max_cols=target_cols,
            )
            for _ in range(2):
                box_last.add_row(" ")
            s_disp = _screenshots_dir().replace(os.path.expanduser("~"), "~", 1)
            box_last.add_row(f"  {Txt(canvas.truncate_plain(t('capture.no_captures', p0=s_disp), target_cols - 6), role='inactive')}")
            for _ in range(2):
                box_last.add_row(" ")
            cards.append(box_last)

        return cards

    def _build_grim_action_pipeline(self, target_expr: str, act_mode: str, filepath: str) -> str:
        t_part = f" {target_expr}" if target_expr else ""
        q_fp = shlex.quote(filepath)
        if act_mode == "clipboard":
            return f"grim{t_part} - | wl-copy --type image/png"
        if act_mode == "satty":
            return f"grim{t_part} - | satty --filename - --output-filename {q_fp} --copy-command 'wl-copy --type image/png'"
        if act_mode == "save":
            return f"grim{t_part} {q_fp}"
        return (
            f"grim{t_part} {q_fp} && "
            f"wl-copy --type image/png < {q_fp} && "
            f"satty --filename {q_fp} --output-filename {q_fp} --copy-command 'wl-copy --type image/png'"
        )

    def _build_window_capture_script(
        self,
        wait_sec: float,
        used_delay: int,
        act_mode: str,
        filepath: str,
        save_py_snippet: str,
        fixed_win_id: str = "",
        update_last_sel: bool = True,
    ) -> str:
        """
        Cattura la finestra tramite `niri msg action screenshot-window` (come in utils/screenshot.sh),
        attende la scrittura asincrona del frame su disco, rifila il margine trasparente con `magick`
        ed esegue l'azione richiesta (`clipboard`, `satty`, `save`, `clipboard_satty`).
        """
        q_fp = shlex.quote(filepath)
        if act_mode == "clipboard":
            post_cmd = 'wl-copy --type image/png < "$TMP_WIN"; sleep 0.15; rm -f "$TMP_WIN"'
        elif act_mode == "satty":
            post_cmd = f'satty --filename "$TMP_WIN" --output-filename {q_fp} --copy-command "wl-copy --type image/png"; rm -f "$TMP_WIN"'
        elif act_mode == "save":
            post_cmd = f'mv -f "$TMP_WIN" {q_fp}'
        else:
            post_cmd = (
                f'mv -f "$TMP_WIN" {q_fp} && '
                f"wl-copy --type image/png < {q_fp} && "
                f"satty --filename {q_fp} --output-filename {q_fp} --copy-command 'wl-copy --type image/png'"
            )

        if fixed_win_id and str(fixed_win_id).isdigit():
            niri_shot_cmd = (
                f'WIN_ID="{fixed_win_id}"; '
                f'niri msg action screenshot-window --id "$WIN_ID" --write-to-disk true --path "$TMP_WIN" >/dev/null 2>&1 || {{ rm -f "$TMP_WIN"; exit 1; }}; '
            )
        else:
            niri_shot_cmd = (
                'WIN_JSON="$(niri msg -j focused-window 2>/dev/null)" || exit 1; '
                '[ -z "$WIN_JSON" ] || [ "$WIN_JSON" = "null" ] && exit 1; '
                'niri msg action screenshot-window --write-to-disk true --path "$TMP_WIN" >/dev/null 2>&1 || { rm -f "$TMP_WIN"; exit 1; }; '
            )

        save_step = (
            f'NC_CAP_MODE=window NC_CAP_GEOM="" NC_CAP_OUT="" NC_CAP_WIN_JSON="$WIN_JSON" NC_CAP_DELAY="{int(used_delay)}" python3 -c "{save_py_snippet}"; '
            if update_last_sel
            else ""
        )

        return (
            f"sleep {wait_sec}; "
            'TMP_WIN="$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/nc-win-shot-XXXXXX.png")"; '
            f"{niri_shot_cmd}"
            'WAITED=0; '
            'while [ ! -s "$TMP_WIN" ] && [ "$WAITED" -lt 40 ]; do sleep 0.05; WAITED=$((WAITED + 1)); done; '
            '[ -s "$TMP_WIN" ] || { rm -f "$TMP_WIN"; exit 1; }; '
            'sleep 0.05; '
            'if command -v magick >/dev/null 2>&1; then '
            '  CROP_BOX="$(magick "$TMP_WIN" -alpha extract -threshold 50% -format "%@" info: 2>/dev/null || true)"; '
            '  if [[ "$CROP_BOX" =~ ^[1-9][0-9]*x[1-9][0-9]*\\+[0-9]+\\+[0-9]+$ ]]; then '
            '    TMP_CROP="${TMP_WIN}.crop.png"; '
            '    if magick "$TMP_WIN" -crop "$CROP_BOX" +repage "$TMP_CROP" 2>/dev/null && [ -s "$TMP_CROP" ]; then '
            '      mv -f "$TMP_CROP" "$TMP_WIN"; '
            '    else '
            '      rm -f "$TMP_CROP"; '
            '    fi; '
            '  fi; '
            'fi; '
            f"{save_step}"
            f"{post_cmd}"
        )

    def _trigger_capture(self, mode: str, delay: int = None, act_mode: str = None, close_ui: bool = True) -> None:
        if mode == "last":
            last_sel = get_last_selection()
            if last_sel is None:
                return
            set_active_capture_mode("last")
            if is_copy_last_delay_enabled():
                delay = max(0, int(last_sel.get("delay", 0)))
            elif delay is None:
                delay = int(self.get_state("delay", 0))
        else:
            set_active_capture_mode(mode)
            if delay is None:
                delay = int(self.get_state("delay", 0))

        if act_mode is None:
            act_mode = get_active_capture_action()

        eff_mode = last_sel["mode"] if mode == "last" else mode
        needed = ["niri"] if eff_mode == "window" else (["grim", "slurp"] if eff_mode == "area" else ["grim"])
        if act_mode in ("clipboard", "clipboard_satty"):
            needed.append("wl-copy")
        if act_mode in ("satty", "clipboard_satty"):
            needed.append("satty")
        missing = [b for b in dict.fromkeys(needed) if not probe.has_bin(b)]
        if missing:
            if self.shell:
                self.shell.show_toast(t("ui.missing", p0=", ".join(missing)), level="err", title=t("capture.title"))
            return

        s_dir = _screenshots_dir()
        os.makedirs(s_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(s_dir, f"Screenshot_{ts}.png")
        wait_sec = delay if delay > 0 else (0.20 if close_ui else 0.05)

        ram_file = _ram_state_file()
        ram_dir = os.path.dirname(ram_file)
        save_py_snippet = (
            "import json, os, tempfile; "
            f"p = {ram_file!r}; "
            "d = json.load(open(p)) if os.path.exists(p) else {}; "
            "wj = json.loads(os.environ.get('NC_CAP_WIN_JSON', '') or 'null') if os.environ.get('NC_CAP_WIN_JSON') else None; "
            "wid = str(wj.get('id', '')) if isinstance(wj, dict) else os.environ.get('NC_CAP_WIN_ID', ''); "
            "m = os.environ.get('NC_CAP_MODE', 'area'); "
            "d['selected_mode'] = m; "
            "d['last_selection'] = {"
            "'mode': m, "
            "'geom': os.environ.get('NC_CAP_GEOM', ''), "
            "'output': os.environ.get('NC_CAP_OUT', ''), "
            "'win_id': wid, "
            "'delay': int(os.environ.get('NC_CAP_DELAY', '0') or 0)"
            "}; "
            f"fd, tp = tempfile.mkstemp(dir={ram_dir!r}, prefix='.nc_cap_ram_'); "
            "os.write(fd, json.dumps(d).encode('utf-8')); os.close(fd); os.replace(tp, p)"
        )

        if mode == "last":
            last_mode = last_sel["mode"]
            if last_mode == "area":
                target_expr = f"-g {shlex.quote(last_sel['geom'])}"
                pipeline = self._build_grim_action_pipeline(target_expr, act_mode, filepath)
                cmd = f"sleep {wait_sec}; {pipeline}"
            elif last_mode == "window":
                cmd = self._build_window_capture_script(
                    wait_sec=wait_sec,
                    used_delay=delay,
                    act_mode=act_mode,
                    filepath=filepath,
                    save_py_snippet=save_py_snippet,
                    fixed_win_id=last_sel.get("win_id", ""),
                    update_last_sel=False,
                )
            else:
                out_val = last_sel.get("output", "")
                target_expr = f"-o {shlex.quote(out_val)}" if out_val else ""
                pipeline = self._build_grim_action_pipeline(target_expr, act_mode, filepath)
                cmd = f"sleep {wait_sec}; {pipeline}"
        elif mode == "area":
            pipeline = self._build_grim_action_pipeline('"-g" "$GEOM"', act_mode, filepath)
            cmd = (
                f"sleep {wait_sec}; "
                f'GEOM="$(slurp 2>/dev/null)" || exit 0; '
                f'[ -z "$GEOM" ] && exit 0; '
                f'NC_CAP_MODE=area NC_CAP_GEOM="$GEOM" NC_CAP_OUT="" NC_CAP_WIN_ID="" NC_CAP_DELAY="{int(delay)}" python3 -c "{save_py_snippet}"; '
                f"{pipeline}"
            )
        elif mode == "window":
            cmd = self._build_window_capture_script(
                wait_sec=wait_sec,
                used_delay=delay,
                act_mode=act_mode,
                filepath=filepath,
                save_py_snippet=save_py_snippet,
                update_last_sel=True,
            )
        else:
            focused_out = _get_focused_output_name()
            q_out = shlex.quote(focused_out)
            target_expr = f"-o {q_out}" if focused_out else ""
            pipeline = self._build_grim_action_pipeline(target_expr, act_mode, filepath)
            cmd = (
                f"sleep {wait_sec}; "
                f'NC_CAP_MODE=screen NC_CAP_GEOM="" NC_CAP_OUT={q_out} NC_CAP_WIN_ID="" NC_CAP_DELAY="{int(delay)}" python3 -c "{save_py_snippet}"; '
                f"{pipeline}"
            )

        probe.spawn_bg(["bash", "-c", cmd])
        if close_ui and self.shell:
            self.shell.dispatch_uri("ui:close")

    def on_link(self, uri: str) -> None:
        if uri == "cap:delay:inc":
            d = int(self.get_state("delay", 0))
            self.set_state("delay", min(9999, d + 1))
            if self.shell:
                self.shell.refresh_view()
        elif uri == "cap:delay:dec":
            d = int(self.get_state("delay", 0))
            self.set_state("delay", max(0, d - 1))
            if self.shell:
                self.shell.refresh_view()
        elif uri == "cap:delay_inline":
            start_inline_input(
                "wp_cap_delay",
                shell=self.shell,
                trigger_uri=uri,
                initial_text=str(self.get_state("delay", 0)),
                max_len=4,
                validator=lambda s: s.strip().isdigit() and 0 <= int(s.strip()) <= 9999,
                on_commit=lambda s: self.set_state("delay", max(0, min(9999, int(s.strip())))),
            )
            return
        elif uri == "cap:toggle_copy_delay":
            toggle_copy_last_delay()
            if self.shell:
                self.shell.refresh_view()
        elif uri.startswith("cap:action:"):
            act_val = uri.split(":", 2)[2]
            set_active_capture_action(act_val)
            if self.shell:
                self.shell.refresh_view()
        elif uri.startswith("cap:default:"):
            act_val = uri.split(":", 2)[2]
            set_capture_action(act_val)
            if self.shell:
                self.shell.refresh_view()
        elif uri.startswith("cap:trigger:"):
            mode = uri.split(":", 2)[2]
            self._trigger_capture(mode, close_ui=True)
        elif uri.startswith("cap:annotate:"):
            if not probe.has_bin("satty"):
                if self.shell:
                    self.shell.show_toast(t("ui.missing", p0="satty"), level="err", title=t("capture.title"))
                return
            fp = uri.split(":", 2)[2]
            probe.spawn_bg(["satty", "--filename", fp, "--copy-command", "wl-copy --type image/png"])
            if self.shell:
                self.shell.dispatch_uri("ui:close")
        elif uri.startswith("cap:copy:"):
            if not probe.has_bin("wl-copy"):
                if self.shell:
                    self.shell.show_toast(t("ui.missing", p0="wl-copy"), level="err", title=t("capture.title"))
                return
            fp = uri.split(":", 2)[2]
            mime = "image/jpeg" if fp.lower().endswith((".jpg", ".jpeg")) else "image/png"
            probe.spawn_bg(["bash", "-c", f"wl-copy --type {mime} < {shlex.quote(fp)}"])
            if self.shell:
                self.shell.dispatch_uri("ui:close")
        elif uri == "cap:open_dir":
            s_dir = _screenshots_dir()
            os.makedirs(s_dir, exist_ok=True)
            probe.spawn_bg(["xdg-open", s_dir])
            if self.shell:
                self.shell.dispatch_uri("ui:close")


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return Panel(shell)
