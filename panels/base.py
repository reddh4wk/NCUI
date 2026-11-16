#!/usr/bin/env python3
import importlib.util
import os
import traceback
import time
from core.config import CONFIG, THEME, CONFIG_DIR
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

    def request_throttled_refresh(self) -> None:
        now = time.time()
        if now - self._last_forced_refresh_ts >= 0.1:
            self._last_forced_refresh_ts = now
            if self.shell:
                self.shell.refresh_view()
                self.shell._setup_timer()

    # --- Helpers Stato Persistente ---
    def get_state(self, key: str, default=None):
        return STATE.get(self.id, key, default)

    def set_state(self, key: str, value) -> None:
        STATE.set(self.id, key, value)

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

    # --- Prompt Bar ---
    def request_prompt(
        self,
        action_id: str,
        placeholder: str,
        initial_text: str = "",
        secret: bool = None,
        title: str = None,
        required: bool = False,
    ) -> None:
        if self.shell:
            self.shell.activate_prompt(
                action_id,
                placeholder,
                initial_text,
                secret=secret,
                title=title,
                required=required,
            )

    # --- Hooks da sovrascrivere nei Pannelli ---
    def build_cards(self, canvas, force_expanded: bool = False) -> list:
        return []

    def on_link(self, uri: str) -> None:
        pass

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        pass

    def on_key_press(self, event) -> bool:
        return False

    # --- Waybar Integration ---
    def get_bar_pill(self) -> dict:
        return {"text": f"{self.icon} {self.title}", "tooltip": "", "class": "module-nc"}

    def on_bar_action(self, action: str, args: list = None) -> None:
        pass


CONTROL_CENTER_SECTIONS = [
    ("accounts",        "\uf007"),
    ("display",         "\uf108"),
    ("waybar",          "\uf0db"),
    ("auth",            "\uf577"),
    ("apps",            "\uf1b2"),
    ("personalization", "\uf53f"),
    ("network",         "\uf6ff"),
    ("notifications",   "\uf0f3"),
    ("bluetooth",       "\uf294"),
    ("sound",           "\uf028"),
    ("power",           "\uf0e7"),
    ("mouse",           "\uf8cc"),
    ("datetime",        "\uf017"),
    ("calendar",        "\uf073"),
    ("updates",         "\uf0aa"),
    ("keyboard",        "\uf11c"),
    ("system",          "\uf129"),
    ("general",         "\uf013"),
]


def load_panel(panel_id: str, shell=None, mode: str = "popup") -> BasePanel:
    pid = str(panel_id or "network").strip()
    if mode == "settings":
        from panels.control_center import ControlCenterPanel
        return ControlCenterPanel(initial_section=pid, shell=shell)

    base_mod_name = "system" if pid in ("cpu", "ram") else pid

    user_plugin = os.path.join(CONFIG_DIR, "plugins", f"{base_mod_name}.py")
    sys_plugin = os.path.join(os.path.dirname(__file__), f"{base_mod_name}.py")
    target_path = user_plugin if os.path.exists(user_plugin) else sys_plugin

    if os.path.exists(target_path):
        try:
            spec = importlib.util.spec_from_file_location(f"nc_panel_{base_mod_name}", target_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "create_panel"):
                return mod.create_panel(pid, shell)
            if hasattr(mod, "Panel"):
                return mod.Panel(shell)
        except Exception as e:
            log.error(f"Error loading panel '{pid}' from {target_path}: {e}\n{traceback.format_exc()}")

    return BasePanel(shell)
