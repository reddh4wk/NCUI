#!/usr/bin/env python3
import importlib.util
import os
import sys
import traceback
import time
from core.config import CONFIG, THEME, CONFIG_DIR
from core.paths import find_panel_file
from core.state import STATE
from core import log


class BasePanel:
    id = "base"
    title = "Base"
    icon = "\uf120"
    supports_expand = False
    hover_highlight = True
    default_tab = "main"
    refresh_ms = 0

    def __init__(self, shell=None):
        self.shell = shell
        self._last_forced_refresh_ts = 0.0
        self._pending_refresh_id = None

    def request_throttled_refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_forced_refresh_ts >= 0.025:
            self._last_forced_refresh_ts = now
            if self._pending_refresh_id is not None:
                from gi.repository import GLib
                GLib.source_remove(self._pending_refresh_id)
                self._pending_refresh_id = None
            if self.shell:
                self.shell.refresh_view()
                self.shell._setup_timer()
        elif self.shell and self._pending_refresh_id is None:
            from gi.repository import GLib

            def _flush():
                self._pending_refresh_id = None
                self._last_forced_refresh_ts = time.monotonic()
                if self.shell:
                    self.shell.refresh_view()
                    self.shell._setup_timer()
                return False

            self._pending_refresh_id = GLib.timeout_add(25, _flush)

    # --- Helpers Stato Persistente ---
    def get_state(self, key: str, default=None):
        return STATE.get(self.id, key, default)

    def set_state(self, key: str, value, immediate: bool = True) -> None:
        STATE.set(self.id, key, value, immediate=immediate)

    @property
    def is_expanded(self) -> bool:
        return bool(self.supports_expand and self.get_state("expanded", False))

    @is_expanded.setter
    def is_expanded(self, val: bool) -> None:
        self.set_state("expanded", bool(val))

    @property
    def active_tab(self) -> str:
        return str(self.get_state("active_tab", self.default_tab))

    @active_tab.setter
    def active_tab(self, tab_id: str) -> None:
        self.set_state("active_tab", str(tab_id))

    # --- Geometria & Comportamento Finestra ---
    def get_cols(self, force_expanded: bool = False) -> int:
        if force_expanded or self.is_expanded:
            return int(THEME["geometry"]["expanded_cols"])
        return int(THEME["geometry"]["compact_cols"])

    def get_refresh_ms(self) -> int:
        refresh_map = CONFIG.get("panels", {}).get("refresh_ms", {})
        if isinstance(refresh_map, dict) and self.id in refresh_map:
            return int(refresh_map[self.id])
        return int(getattr(self, "refresh_ms", 0))

    def is_busy(self) -> bool:
        return False

    def should_refresh_on_tick(self) -> bool:
        return True

    # --- Prompt Bar ---
    def request_prompt(
        self,
        action_id: str,
        placeholder: str,
        initial_text: str = "",
        secret: bool = None,
        title: str = None,
        required: bool = False,
        completion=None,
    ) -> None:
        if self.shell:
            self.shell.activate_prompt(
                action_id,
                placeholder,
                initial_text,
                secret=secret,
                title=title,
                required=required,
                completion=completion,
            )

    # --- Hooks da sovrascrivere nei Pannelli ---
    def build_cards(self, canvas, force_expanded: bool = False) -> list:
        return []

    def on_link(self, uri: str) -> None:
        pass

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        pass

    def on_prompt_change(self, action_id: str, text: str) -> bool:
        return False

    def on_key_press(self, event) -> bool:
        return False

    def on_key_release(self, event) -> bool:
        return False

    # --- Waybar Integration ---
    def get_bar_pill(self) -> dict:
        return {"text": f"{self.icon} {self.title}", "tooltip": "", "class": "module-nc"}

    def on_bar_action(self, action: str, args: list = None) -> None:
        pass


def load_panel(panel_id: str, shell=None, mode: str = "popup") -> BasePanel:
    if mode == "settings":
        from panels.control_center import create_panel
        return create_panel(panel_id, shell=shell)
    pid = str(panel_id or "network").strip()

    raw_name = pid[3:] if pid.startswith(("wp_", "ww_")) else pid
    base_mod_name = "wp_system" if raw_name in ("cpu", "ram", "gpu", "system") else f"wp_{raw_name}"

    target_path = find_panel_file(f"{base_mod_name}.py")

    if target_path and os.path.exists(target_path):
        try:
            spec = importlib.util.spec_from_file_location(f"nc_panel_{base_mod_name}", target_path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "create_panel"):
                return mod.create_panel(pid, shell)
            if hasattr(mod, "Panel"):
                return mod.Panel(shell)
        except Exception as e:
            log.error(f"Error loading panel '{pid}' from {target_path}: {e}\n{traceback.format_exc()}")

    return BasePanel(shell)
