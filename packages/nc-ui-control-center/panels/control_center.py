#!/usr/bin/env python3
import importlib
import importlib.util
import os
import sys
import traceback
from gi.repository import GLib
from core import log, probe
from core.config import (
    apply_waybar_transparency,
    get_ui_transparency_pct,
    set_ui_transparency_pct,
)
from core.i18n import get_active_language, list_available_languages, set_language, t
from core.privileges import (
    handle_privilege_link,
    handle_privilege_prompt,
    is_sudo_session_unlocked,
    lock_sudo_session,
)
from core.state import STATE
from panels.base import BasePanel
from ui.canvas import Canvas
from ui.kit import ActionBtn, Box, InlineFilter, SelectPill, Txt, cancel_inline_entry

_ALL_SECTION_BLUEPRINTS = {
    "sysinfo":         {"icon": "\uf129", "tabs": ["overview"]},
    "display":         {"icon": "\uf108", "tabs": ["monitors"]},
    "personalization": {"icon": "\uf53f", "tabs": ["theme", "wallpaper", "zsh", "ecosystem"]},
    "waybar":          {"icon": "\uf009", "tabs": ["layout", "options"]},
    "apps":            {"icon": "\uf1b2", "tabs": ["floating", "mime", "autostart"]},
    "input":           {"icon": "\uf11c", "tabs": ["mouse", "keyboard", "macros", "shortcuts", "controllers"]},
    "printers":        {"icon": "\uf02f", "tabs": ["devices", "queue", "discovery", "server"]},
    "sound":           {"icon": "\uf028", "tabs": ["output", "input", "mixer", "devices"]},
    "network":         {"icon": "\uf6ff", "tabs": ["interfaces", "wifi_bt", "vpn_proxy", "wan_routing"]},
    "firewall":        {"icon": "\uf3ed", "tabs": ["rules", "chains", "connections"]},
    "power":           {"icon": "\uf0e7", "tabs": ["profiles", "cpu_ram", "gpu", "ups"]},
    "datetime":        {"icon": "\uf017", "tabs": ["time", "calendar"]},
    "updates":         {"icon": "\uf0aa", "tabs": ["pacman", "aur", "flatpak", "orphans"]},
    "auth":            {"icon": "\uf023", "tabs": ["tpm", "keys", "polkit", "usbguard", "sudoers"]},
    "telemetry":       {"icon": "\uf0ad", "tabs": ["boot", "recovery", "snapshots", "vault", "backups", "disks"]},
    "settings":        {"icon": "\uf013", "tabs": ["interface", "notifications", "terminal"]},
}


def discover_section_blueprints() -> dict[str, dict]:
    """Discover installed Control Center sections and subtabs via package availability."""
    is_arch = (probe.get_os_release().get("family") == "arch") or probe.has_bin("pacman")
    out: dict[str, dict] = {}
    for sec, bp in _ALL_SECTION_BLUEPRINTS.items():
        avail_tabs = [
            sub for sub in bp["tabs"]
            if not (sec == "updates" and sub == "aur" and not is_arch)
            and importlib.util.find_spec(f"panels.cc_{sec}_{sub}") is not None
        ]
        if avail_tabs:
            out[sec] = {"icon": bp["icon"], "tabs": avail_tabs}
    return out


def get_tab_title(sec_id: str, sub_id: str) -> str:
    if sec_id == "updates" and sub_id == "pacman":
        if probe.get_os_release().get("family") != "arch" and not probe.has_bin("pacman"):
            pm = probe.get_preferred_pkg_manager()
            return t("control_center.tabs.updates.system_pkg", p0=pm.upper() if pm else t("common.system"))
    return t(f"control_center.tabs.{sec_id}.{sub_id}", sub_id.title())


SECTION_BLUEPRINTS = discover_section_blueprints()
CONTROL_CENTER_SECTIONS = [(sec, bp["icon"]) for sec, bp in SECTION_BLUEPRINTS.items()]
SECTION_TABS = {sec: bp["tabs"] for sec, bp in SECTION_BLUEPRINTS.items()}


def get_section_blueprints() -> dict[str, dict]:
    return discover_section_blueprints()


def get_control_center_sections() -> list[tuple[str, str]]:
    bps = discover_section_blueprints()
    return [(sec, bp["icon"]) for sec, bp in bps.items()]


def get_section_title(sec_id: str, blueprints: dict | None = None) -> str:
    bps = blueprints if blueprints is not None else discover_section_blueprints()
    if sec_id == "power" and "ups" not in bps.get("power", {}).get("tabs", []):
        return t("control_center.sections.power_only", "Power")
    return t(f"control_center.sections.{sec_id}", sec_id.title())


_ENGINES = [
    ("input", "handle_cc_input_link", "handle_cc_input_prompt"),
    ("printers", "handle_cc_printers_link", "handle_cc_printers_prompt"),
    ("apps", "handle_cc_apps_link", "handle_cc_apps_prompt"),
    ("sound", "handle_cc_audio_link", "handle_cc_audio_prompt"),
    ("resources", "handle_cc_resources_link", None),
    ("ups", "handle_cc_ups_link", "handle_cc_ups_prompt"),
    ("power", "handle_cc_power_link", "handle_cc_power_prompt"),
    ("boot", "handle_cc_boot_link", "handle_cc_boot_prompt"),
    ("disks", "handle_cc_disks_link", None),
    ("telemetry", "handle_cc_telemetry_link", "handle_cc_telemetry_prompt"),
    ("updates", "handle_cc_updates_link", "handle_cc_updates_prompt"),
    ("auth", "handle_cc_auth_link", None),
    ("calendar", "handle_cc_calendar_link", "handle_cc_calendar_prompt"),
    ("datetime", "handle_cc_datetime_link", "handle_cc_datetime_prompt"),
    ("notifications", "handle_cc_notifications_link", "handle_cc_notifications_prompt"),
    ("settings", "handle_cc_settings_link", "handle_cc_settings_prompt"),
    ("sysinfo", "handle_cc_sysinfo_link", None),
    ("firewall", "handle_cc_firewall_link", None),
    ("personalization", "handle_cc_personalization_link", "handle_cc_personalization_prompt"),
    ("zsh", "handle_cc_zsh_link", None),
    ("ecosystem", "handle_cc_ecosystem_link", None),
    ("display", "handle_cc_display_link", "handle_cc_display_prompt"),
    ("network", "handle_cc_network_link", "handle_cc_network_prompt"),
    ("waybar", "handle_cc_waybar_link", "handle_cc_waybar_prompt"),
]

_AVAILABLE_ENGINES: dict[str, bool] = {}


def _import_engine(sec: str):
    mod_name = f"panels.cc_engine_{sec}"
    avail = _AVAILABLE_ENGINES.get(mod_name)
    if avail is False:
        return None
    if avail is None:
        if importlib.util.find_spec(mod_name) is None:
            _AVAILABLE_ENGINES[mod_name] = False
            return None
        _AVAILABLE_ENGINES[mod_name] = True
    try:
        return importlib.import_module(mod_name)
    except ImportError:
        _AVAILABLE_ENGINES[mod_name] = False
        return None


_CC_LIVE_REFRESH_MS: dict[tuple[str, str], int] = {
    ("personalization", "wallpaper"): 2000,
    ("sound", "mixer"): 1500,
    ("sound", "output"): 2000,
    ("sound", "input"): 2000,
    ("sound", "devices"): 2000,
    ("network", "interfaces"): 2000,
    ("network", "wifi_bt"): 2500,
    ("network", "vpn_proxy"): 3000,
    ("network", "wan_routing"): 3000,
    ("printers", "queue"): 2000,
    ("printers", "devices"): 3000,
    ("printers", "server"): 3000,
    ("firewall", "connections"): 2000,
    ("power", "ups"): 2500,
    ("power", "profiles"): 3000,
    ("power", "cpu_ram"): 1500,
    ("power", "gpu"): 1500,
    ("datetime", "time"): 1000,
    ("telemetry", "disks"): 5000,
    ("sysinfo", "overview"): 5000,
    ("input", "controllers"): 2500,
    ("input", "macros"): 2000,
    ("display", "monitors"): 2500,
    ("auth", "usbguard"): 3000,
    ("auth", "sudoers"): 5000,
}


class ControlCenterPanel(BasePanel):
    id = "control_center"
    title = "Control Center"
    icon = "\uf013"
    supports_expand = True
    hover_highlight = True
    default_tab = "main"

    def __init__(self, initial_section: str = "", shell=None):
        super().__init__(shell)
        STATE.set("keyboard", "capture_mode", "")
        STATE.set("keyboard", "capture_preview", "")
        bps = discover_section_blueprints()
        default_sec = "sysinfo" if "sysinfo" in bps else next(iter(bps), "sysinfo")
        self.section = default_sec
        self._ready = False
        self._last_tick_sig = None
        init_sec = str(initial_section or "").strip()
        if init_sec:
            self.set_section(init_sec)
        else:
            saved = self.get_state("active_section", default_sec)
            self.set_section(saved if saved in bps else default_sec)
        self._ready = True
        if os.environ.get("NC_UI_OFFSCREEN") != "1":
            GLib.timeout_add(150, self._prewarm_updates)

    def get_refresh_ms(self) -> int:
        return int(_CC_LIVE_REFRESH_MS.get((self.section, self.get_subtab()), 0))

    @staticmethod
    def _get_active_tui() -> str:
        mod = sys.modules.get("panels.cc_engine_resources")
        return mod.get_active_tui() if (mod is not None and hasattr(mod, "get_active_tui")) else ""

    def is_busy(self) -> bool:
        if (
            STATE.get("keyboard", "capture_mode", "")
            or STATE.get("desktop", "yazi_open", False)
            or STATE.get("privileges", "pending_action", "")
            or self._get_active_tui()
        ):
            return True
        if self.section == "sound":
            from panels.cc_engine_sound import is_sound_busy
            if is_sound_busy():
                return True
        return False

    def _compute_gated_tick_sig(self, sec: str, sub: str, force: bool = True):
        if (sec, sub) == ("input", "controllers"):
            from panels.cc_input_controllers import discover_controllers
            try:
                from core.pad_mapper_core import get_mapper_runtime_sig
                mapper_sig = get_mapper_runtime_sig()
            except ImportError:
                mapper_sig = None
            devs = discover_controllers(force=force)
            return (
                tuple((d.get("id"), d.get("evdev"), d.get("connected"), d.get("battery")) for d in devs),
                mapper_sig,
            )
        if (sec, sub) == ("display", "monitors"):
            from panels.cc_engine_display import get_niri_outputs_data
            outs = get_niri_outputs_data() or {}
            return tuple(
                sorted(
                    (
                        k,
                        v.get("current_mode"),
                        (v.get("logical") or {}).get("x"),
                        (v.get("logical") or {}).get("y"),
                        (v.get("logical") or {}).get("scale"),
                        (v.get("logical") or {}).get("transform"),
                    )
                    for k, v in outs.items()
                )
            )
        if (sec, sub) == ("input", "macros"):
            from panels.cc_engine_input import _is_macro_engine_running
            return bool(_is_macro_engine_running())
        if (sec, sub) == ("power", "profiles"):
            from panels.cc_engine_power import get_power_profiles_tick_sig
            return get_power_profiles_tick_sig()
        return None

    def should_refresh_on_tick(self) -> bool:
        sec, sub = self.section, self.get_subtab()
        if (sec, sub) == ("personalization", "wallpaper"):
            from core.config import is_ncmatrix_available
            wp_mode = str(STATE.get("desktop", "wp_mode", "scene") or "scene")
            return wp_mode in ("ncmatrix", "cmatrix") and is_ncmatrix_available()
        if (sec, sub) == ("power", "cpu_ram"):
            return not bool(self.get_state("tui_btop_open", True))
        if (sec, sub) == ("power", "gpu"):
            return not bool(self.get_state("tui_nvtop_open", True))
        if (sec, sub) == ("settings", "terminal"):
            return False
        if (sec, sub) in (("input", "controllers"), ("display", "monitors"), ("input", "macros"), ("power", "profiles")):
            sig = self._compute_gated_tick_sig(sec, sub, force=True)
            changed = self._last_tick_sig is not None and sig != self._last_tick_sig
            self._last_tick_sig = sig
            return changed
        return True

    def _prewarm_updates(self) -> bool:
        try:
            from panels.cc_engine_updates import ensure_updates_loaded
            ensure_updates_loaded(self)
        except Exception as e:
            log.debug(f"Updates prewarm skipped: {e}")
        return False

    def _clear_capture(self) -> None:
        if STATE.get("keyboard", "capture_mode", ""):
            STATE.set("keyboard", "capture_mode", "")
            STATE.set("keyboard", "capture_preview", "")
            if self.shell and hasattr(self.shell, "set_key_capture_grab"):
                self.shell.set_key_capture_grab(False)

    def _clear_yazi(self) -> None:
        if STATE.get("desktop", "yazi_open", False):
            from panels.cc_engine_personalization import _close_embedded_yazi
            _close_embedded_yazi(cancelled=True)

    def _clear_resource_tuis(self, tui_id: str | None = None) -> None:
        from panels.cc_engine_resources import close_embedded_tui, close_embedded_tuis
        if tui_id:
            close_embedded_tui(tui_id)
        else:
            close_embedded_tuis()

    def _clear_active_tui_focus(self) -> None:
        if self._get_active_tui():
            from panels.cc_engine_resources import set_active_tui
            set_active_tui("", shell=self.shell)

    def set_section(self, sec_id: str) -> None:
        self._clear_capture()
        self._clear_yazi()
        if getattr(self, "_ready", False):
            self._clear_active_tui_focus()
        bps = discover_section_blueprints()
        if sec_id not in bps:
            raise ValueError(f"Unknown Control Center section: {sec_id!r}")
        if getattr(self, "_ready", False) and sec_id != self.section:
            if self.section == "power":
                self._clear_resource_tuis("btop")
                self._clear_resource_tuis("nvtop")
            elif self.section == "personalization":
                from panels.cc_engine_personalization import reset_ncmatrix_cpu_telemetry
                reset_ncmatrix_cpu_telemetry()
            if is_sudo_session_unlocked():
                lock_sudo_session(drop_ticket=True)
            STATE.set("privileges", "pending_action", "")
            cancel_inline_entry(refresh=False)
        self.section = sec_id
        self.set_state("active_section", sec_id)
        self._last_tick_sig = None
        if getattr(self, "_ready", False) and self.shell:
            self.shell._setup_timer()

    def get_subtab(self) -> str:
        bps = discover_section_blueprints()
        bp = bps.get(self.section, {})
        tabs = bp.get("tabs", [])
        default_sub = tabs[0] if tabs else "main"
        saved = self.get_state(f"subtab_{self.section}", default_sub)
        return saved if (not tabs or saved in tabs) else default_sub

    def set_subtab(self, sub_id: str) -> None:
        self._clear_capture()
        self._clear_yazi()
        if getattr(self, "_ready", False):
            self._clear_active_tui_focus()
        if self.section == "telemetry" and sub_id in ("resources", "power_tools", "cpu_ram_procs"):
            self.set_section("power")
            self.set_subtab("cpu_ram")
            return
        if self.section == "power":
            if sub_id == "idle":
                sub_id = "profiles"
            if sub_id != "cpu_ram":
                self._clear_resource_tuis("btop")
            if sub_id != "gpu":
                self._clear_resource_tuis("nvtop")
        if self.section == "updates" and sub_id == "packages":
            sub_id = "pacman"
        elif self.section == "display" and sub_id == "scaling":
            sub_id = "monitors"
        elif self.section == "settings" and sub_id == "app_rules":
            sub_id = "notifications"
        elif self.section == "telemetry":
            if sub_id == "backup":
                sub_id = "snapshots"
            elif sub_id == "storage":
                sub_id = "disks"
        bps = discover_section_blueprints()
        tabs = bps.get(self.section, {}).get("tabs", [])
        if tabs and sub_id not in tabs:
            raise ValueError(f"Unknown subtab {sub_id!r} for section {self.section!r}")
        if getattr(self, "_ready", False) and sub_id != self.get_subtab():
            if self.section == "personalization" and sub_id != "wallpaper":
                from panels.cc_engine_personalization import reset_ncmatrix_cpu_telemetry
                reset_ncmatrix_cpu_telemetry()
            if is_sudo_session_unlocked():
                lock_sudo_session(drop_ticket=True)
            STATE.set("privileges", "pending_action", "")
            cancel_inline_entry(refresh=False)
        self.set_state(f"subtab_{self.section}", sub_id)
        self._last_tick_sig = None
        if getattr(self, "_ready", False) and self.shell:
            self.shell._setup_timer()

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        cur_lang = get_active_language()
        avail_langs = list_available_languages()
        lang_btns = [
            SelectPill(lg.upper(), f"cc:lang:{lg}", selected=(lg == cur_lang))
            for lg in avail_langs
        ]

        bps = discover_section_blueprints()
        bp = bps.get(self.section) or next(iter(bps.values()), {"icon": "\uf013", "tabs": []})
        sec_title = get_section_title(self.section, blueprints=bps)
        tabs = bp["tabs"]
        active_sub = self.get_subtab()

        hdr_actions = lang_btns + [
            InlineFilter("__cc_global_search__", "", t("control_center.search_btn"), "cc:global_search_open", width=20, compact=True),
            ActionBtn("\uf00d", "ui:close", tone="danger_soft"),
        ]
        hdr_box = Box(
            f"{sec_title.upper()}",
            icon=bp["icon"],
            actions=hdr_actions,
            highlighted=True,
        )

        localized_tabs = [(tid, get_tab_title(self.section, tid)) for tid in tabs]
        n_tabs = max(1, len(localized_tabs))
        avail_tab_cols = max(14, (canvas.inner_cols - 4 - (n_tabs - 1) * 2) // n_tabs - 2)
        min_tab_w = min(26, max(14, avail_tab_cols))

        hdr_box.add_tabs(active_sub, localized_tabs, uri_prefix="cc:subtab", min_tab_cols=min_tab_w)
        self._current_hdr_box = hdr_box

        sub_mod_name = f"panels.cc_{self.section}_{active_sub}"
        eng_mod_name = f"panels.cc_engine_{self.section}"
        try:
            try:
                mod = importlib.import_module(sub_mod_name)
            except ImportError:
                if eng_mod_name in sys.modules:
                    importlib.reload(sys.modules[eng_mod_name])
                if sub_mod_name in sys.modules:
                    importlib.reload(sys.modules[sub_mod_name])
                mod = importlib.import_module(sub_mod_name)
            cards = [hdr_box] + mod.build_cards(self, canvas)
            from ui.embedded_tui import build_panel_terminal_card

            term_card = build_panel_terminal_card(self, canvas, cards[1:])
            if term_card is not None:
                cards.append(term_card)
            if (self.section, active_sub) in (("input", "controllers"), ("display", "monitors"), ("input", "macros"), ("power", "profiles")):
                self._last_tick_sig = self._compute_gated_tick_sig(self.section, active_sub, force=False)
            return cards
        except Exception as e:
            log.error(f"ControlCenter render error in {self.section}/{active_sub}: {e}\n{traceback.format_exc()}")
            err_box = Box(t("control_center.render_error", p0=f"{self.section}.{active_sub}"), icon="\uf071", highlighted=False)
            err_box.add_row(f"  {Txt(str(e), role='fault')}")
            return [hdr_box, err_box]

    def on_prompt_change(self, action_id: str, text: str) -> bool:
        if action_id.startswith("cc_kbd_"):
            from panels.cc_engine_input import handle_cc_input_prompt_change
            return handle_cc_input_prompt_change(self, action_id, text)
        if action_id.startswith("cc_dt_"):
            from panels.cc_engine_datetime import handle_cc_datetime_prompt_change
            return handle_cc_datetime_prompt_change(self, action_id, text)
        return False

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        if handle_privilege_prompt(self, action_id, text):
            return
        for sec, _lnk_fn, prm_fn in _ENGINES:
            if prm_fn:
                mod = _import_engine(sec)
                if mod is not None and hasattr(mod, prm_fn):
                    if getattr(mod, prm_fn)(self, action_id, text):
                        return
        if action_id == "cc_search" and text:
            q = text.strip().lower()
            bps = discover_section_blueprints()
            for sec_id in bps:
                lbl = get_section_title(sec_id, blueprints=bps).lower()
                if q in sec_id.lower() or q in lbl:
                    self.set_section(sec_id)
                    break

    def on_link(self, uri: str) -> bool:
        if not isinstance(uri, str) or not uri:
            return False
        if uri == "cc_term:close":
            from ui.embedded_tui import close_panel_terminal

            close_panel_terminal(self)
            return True
        if handle_privilege_link(self, uri):
            return True
        for sec, lnk_fn, _prm_fn in _ENGINES:
            mod = _import_engine(sec)
            if mod is not None and hasattr(mod, lnk_fn):
                if getattr(mod, lnk_fn)(self, uri):
                    return True
        if uri == "cc:trans_apply":
            if self.shell:
                self.shell.bump_leave_guard(1.5)
            apply_waybar_transparency()
            return True
        if uri.startswith("cc:section:"):
            rest = uri[len("cc:section:"):].strip()
            sec, _, sub = rest.partition(":")
            sec = sec.strip()
            sub = sub.strip()
            if not sec or sec not in discover_section_blueprints():
                return False
            self.set_section(sec)
            if sub:
                self.set_subtab(sub)
            if self.shell:
                self.shell.refresh_view()
            return True
        elif uri.startswith("cc:subtab:"):
            sub = uri.split(":", 2)[2].strip()
            if not sub:
                return False
            self.set_subtab(sub)
            if self.shell:
                self.shell.refresh_view()
            return True
        elif uri.startswith("cc:lang:"):
            lang = uri.split(":", 2)[2].strip()
            if not lang:
                return False
            set_language(lang)
            if self.shell:
                self.shell.refresh_view()
            return True
        elif uri.startswith("cc:trans:"):
            try:
                val = int(round(float(uri.split(":", 2)[2])))
                set_ui_transparency_pct(val, sync_waybar=False)
                if self.shell:
                    self.shell._apply_css()
                self.request_throttled_refresh()
                return True
            except Exception:
                return False
        elif uri.startswith("cc:trans_scroll:"):
            cur = get_ui_transparency_pct()
            step = 5 if uri.rsplit(":", 1)[1] == "up" else -5
            set_ui_transparency_pct(cur + step, sync_waybar=False)
            if self.shell:
                self.shell._apply_css()
            self.request_throttled_refresh()
            return True
        return False

    def on_key_press(self, event) -> bool:
        if STATE.get("desktop", "yazi_open", False):
            from panels.cc_engine_personalization import handle_cc_personalization_key_press
            if handle_cc_personalization_key_press(self, event):
                return True
        if self._get_active_tui() or (self.section == "power" and self.get_subtab() in ("cpu_ram", "gpu")):
            from panels.cc_engine_resources import handle_cc_resources_key_press
            if handle_cc_resources_key_press(self, event):
                return True
        if self.section == "input":
            from panels.cc_engine_input import handle_cc_input_key_press
            return handle_cc_input_key_press(self, event)
        if self.section == "personalization":
            from panels.cc_engine_personalization import handle_cc_personalization_key_press
            return handle_cc_personalization_key_press(self, event)
        return False

    def on_key_release(self, event) -> bool:
        if self.section == "input":
            from panels.cc_engine_input import handle_cc_input_key_release
            return handle_cc_input_key_release(self, event)
        return False


def create_panel(panel_id: str = "", shell=None) -> BasePanel:
    return ControlCenterPanel(initial_section=str(panel_id or "").strip(), shell=shell)
