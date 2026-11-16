#!/usr/bin/env python3
from core.config import (
    get_ui_transparency_pct,
    set_ui_transparency_pct,
    apply_waybar_transparency,
)
from core.i18n import t, get_active_language, list_available_languages, set_language
from ui.canvas import Canvas
from ui.kit import (
    PALETTE,
    Box,
    Badge,
    Slider,
    Txt,
    SelectPill,
    ActionBtn,
    TabBar,
)
from panels.base import BasePanel, CONTROL_CENTER_SECTIONS
from panels.cc_calendar import (
    build_cc_calendar_cards,
    handle_cc_calendar_link,
    handle_cc_calendar_prompt,
)
from panels.cc_waybar import (
    build_cc_waybar_cards,
    handle_cc_waybar_link,
    handle_cc_waybar_prompt,
)
from panels.cc_network import (
    build_cc_network_cards,
    build_cc_bluetooth_cards,
    handle_cc_network_link,
    handle_cc_network_prompt,
)

SECTION_TABS = {
    "accounts": [
        ("profile", {"en": "User Profile", "it": "Profilo Utente"}),
        ("groups", {"en": "Groups & Sudo", "it": "Gruppi & Sudo"}),
        ("sessions", {"en": "Active Sessions", "it": "Sessioni Attive"}),
    ],
    "display": [
        ("layout", {"en": "Monitor Map", "it": "Mappa Monitor"}),
        ("scaling", {"en": "Scaling & Modes", "it": "Scala & Modalit\u00e0"}),
    ],
    "waybar": [
        ("layout", {"en": "Bar Layout & Widgets", "it": "Layout & Widget Barra"}),
        ("options", {"en": "Widget Options", "it": "Opzioni Widget"}),
    ],
    "auth": [
        ("tpm", {"en": "TPM2 & LUKS", "it": "TPM2 & LUKS"}),
        ("keys", {"en": "SSH & GPG Keys", "it": "Chiavi SSH & GPG"}),
        ("polkit", {"en": "Polkit & PAM", "it": "Polkit & PAM"}),
    ],
    "apps": [
        ("floating", {"en": "Niri Floating Rules", "it": "Regole Floating Niri"}),
        ("mime", {"en": "Default Apps", "it": "App Predefinite"}),
        ("autostart", {"en": "Autostart", "it": "Avvio Automatico"}),
    ],
    "personalization": [
        ("theme", {"en": "Theme & Colors", "it": "Tema & Colori"}),
        ("creator", {"en": "Theme Creator", "it": "Creatore Temi"}),
        ("wallpaper", {"en": "Desktop & Matrix", "it": "Sfondi & Matrix"}),
        ("waybar", {"en": "Waybar Bar", "it": "Barra Waybar"}),
    ],
    "network": [
        ("wired", {"en": "Interfaces & Profiles", "it": "Interfacce & Profili"}),
        ("wireless", {"en": "Wi-Fi & Bluetooth", "it": "Wi-Fi & Bluetooth"}),
        ("vpn", {"en": "VPN & Proxy", "it": "VPN & Proxy"}),
        ("watchdog", {"en": "WAN & Routing", "it": "WAN & Routing"}),
    ],
    "notifications": [
        ("system", {"en": "System Notifications", "it": "Notifiche di Sistema"}),
        ("apps", {"en": "Per-App Rules", "it": "Regole Applicazioni"}),
    ],
    "bluetooth": [
        ("paired", {"en": "Bluetooth Devices & Scanner", "it": "Dispositivi Bluetooth & Scanner"}),
    ],
    "sound": [
        ("output", {"en": "Output", "it": "Uscita"}),
        ("input", {"en": "Input", "it": "Ingresso"}),
        ("mixer", {"en": "Application Mixer", "it": "Mixer Applicazioni"}),
        ("devices", {"en": "Hardware Profiles", "it": "Profili Hardware"}),
    ],
    "power": [
        ("general", {"en": "Power Profiles", "it": "Profili Energetici"}),
        ("ups", {"en": "EcoFlow / NUT UPS", "it": "UPS EcoFlow / NUT"}),
        ("wakeup", {"en": "Idle & Lock", "it": "Standby & Blocco"}),
    ],
    "mouse": [
        ("general", {"en": "General", "it": "Generale"}),
        ("pointer", {"en": "Pointer & Accel", "it": "Puntatore & Accel"}),
    ],
    "datetime": [
        ("time", {"en": "Time & NTP", "it": "Ora & NTP"}),
        ("timezone", {"en": "Timezone List", "it": "Fusi Orari"}),
        ("region", {"en": "Region & Format", "it": "Regione & Formato"}),
    ],
    "calendar": [
        ("mode", {"en": "Mode & Local DB", "it": "Modalit\u00e0 & DB Locale"}),
        ("google", {"en": "Google OAuth2 & Sync", "it": "Google OAuth2 & Sync"}),
        ("io", {"en": "Import / Export (.ics / .db)", "it": "Importa / Esporta (.ics / .db)"}),
    ],
    "updates": [
        ("packages", {"en": "Arch & AUR Updates", "it": "Aggiornamenti Arch & AUR"}),
    ],
    "keyboard": [
        ("keyboard", {"en": "Keyboard", "it": "Tastiera"}),
        ("layout", {"en": "Layout & Language", "it": "Layout & Lingua"}),
        ("shortcuts", {"en": "Niri Shortcuts", "it": "Scorciatoie Niri"}),
    ],
    "system": [
        ("about", {"en": "About This PC", "it": "Info sul Sistema"}),
        ("power_tools", {"en": "NC Power Tools", "it": "NC Power Tools"}),
        ("resources", {"en": "CPU, RAM & Procs", "it": "CPU, RAM & Proc."}),
        ("storage", {"en": "Disks & NASUS", "it": "Dischi & NASUS"}),
        ("backup", {"en": "Snapshots & Backup", "it": "Snapshot & Backup"}),
    ],
    "general": [
        ("boot", {"en": "Boot Menu (Limine)", "it": "Menu di Avvio (Limine)"}),
        ("ncui", {"en": "NC-UI Behavior", "it": "Comportamento NC-UI"}),
    ],
}

_SECTION_ICONS = dict(CONTROL_CENTER_SECTIONS)
SECTION_BLUEPRINTS = {
    k: {"icon": _SECTION_ICONS.get(k, "\uf013"), "tabs": v}
    for k, v in SECTION_TABS.items()
}


def _loc_str(entry, lang: str) -> str:
    if isinstance(entry, dict):
        return entry.get(lang) or entry.get("en") or ""
    return str(entry)


class ControlCenterPanel(BasePanel):
    id = "control_center"
    title = "Control Center"
    icon = "\uf013"
    supports_expand = True
    hover_highlight = True
    default_tab = "main"

    def __init__(self, initial_section: str = "system", shell=None):
        super().__init__(shell)
        sec_map = {
            "cpu": "system",
            "ram": "system",
            "storage": "system",
            "audio": "sound",
            "settings": "system",
        }
        norm_sec = sec_map.get(initial_section, initial_section)
        if norm_sec not in SECTION_BLUEPRINTS:
            norm_sec = self.get_state("active_section", "system")
            if norm_sec not in SECTION_BLUEPRINTS:
                norm_sec = "system"
        self.section = norm_sec
        self.set_state("active_section", self.section)

    def set_section(self, sec_id: str) -> None:
        if sec_id in SECTION_BLUEPRINTS:
            self.section = sec_id
            self.set_state("active_section", sec_id)

    def get_subtab(self) -> str:
        bp = SECTION_BLUEPRINTS.get(self.section, {})
        tabs = bp.get("tabs", [])
        default_sub = tabs[0][0] if tabs else "main"
        saved = self.get_state(f"subtab_{self.section}", default_sub)
        valid_ids = [tid for tid, _ in tabs]
        return saved if saved in valid_ids else default_sub

    def set_subtab(self, sub_id: str) -> None:
        self.set_state(f"subtab_{self.section}", sub_id)

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        cur_lang = get_active_language()
        avail_langs = list_available_languages()

        lang_btns = [
            SelectPill(lg.upper(), f"cc:lang:{lg}", selected=(lg == cur_lang))
            for lg in avail_langs
        ]

        bp = SECTION_BLUEPRINTS.get(self.section, SECTION_BLUEPRINTS["system"])
        sec_title = t(f"control_center.sections.{self.section}", self.section.title())
        sec_icon = bp["icon"]
        tabs = bp["tabs"]
        active_sub = self.get_subtab()
        active_sub_dict = next((lbl for tid, lbl in tabs if tid == active_sub), tabs[0][1])
        active_sub_label = _loc_str(active_sub_dict, cur_lang)

        cards = []

        # --- 1. TOP HEADER & TAB BAR (Cornice Bianco Puro in risalto, Tab larghe Ciano/Cobalto) ---
        search_prompt_uri = f"ui:prompt:cc_search|{t('common.search_placeholder')}:"
        hdr_actions = lang_btns + [
            ActionBtn(f"\uf002 {t('control_center.search_btn')}", search_prompt_uri, tone="neutral"),
            ActionBtn("\uf00d", "ui:close", tone="danger_soft"),
        ]
        hdr_box = Box(
            f"{sec_title.upper()}",
            icon=sec_icon,
            actions=hdr_actions,
            highlighted=True,
        )

        localized_tabs = [(tid, _loc_str(tlabel_dict, cur_lang)) for tid, tlabel_dict in tabs]
        n_tabs = max(1, len(localized_tabs))
        avail_tab_cols = max(14, (canvas.inner_cols - 4 - (n_tabs - 1) * 2) // n_tabs - 2)
        min_tab_w = min(26, max(16, avail_tab_cols))

        hdr_box.add_row(" ")
        hdr_box.add_row("  " + TabBar(localized_tabs, active_sub, uri_prefix="cc:subtab", min_tab_cols=min_tab_w))
        hdr_box.add_row(" ")
        cards.append(hdr_box)

        # --- SEZIONE DEDICATA: KEYBOARD ---
        if self.section == "keyboard":
            from panels.cc_keyboard import build_cc_keyboard_cards
            cards.extend(build_cc_keyboard_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: SOUND (cc_audio) ---
        if self.section == "sound":
            from panels.cc_audio import build_cc_audio_cards
            cards.extend(build_cc_audio_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: POWER (cc_power) ---
        if self.section == "power":
            from panels.cc_power import build_cc_power_cards
            cards.extend(build_cc_power_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: SYSTEM (cc_system) ---
        if self.section == "system":
            from panels.cc_system import build_cc_system_cards
            cards.extend(build_cc_system_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: UPDATES (cc_updates) ---
        if self.section == "updates":
            from panels.cc_updates import build_cc_updates_cards
            cards.extend(build_cc_updates_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: AUTH (cc_auth) ---
        if self.section == "auth":
            from panels.cc_auth import build_cc_auth_cards
            cards.extend(build_cc_auth_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: NETWORK ---
        if self.section == "network":
            cards.extend(build_cc_network_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: BLUETOOTH ---
        if self.section == "bluetooth":
            cards.extend(build_cc_bluetooth_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: CALENDAR ---
        if self.section == "calendar":
            cards.extend(build_cc_calendar_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: DISPLAY (cc_display) ---
        if self.section == "display":
            from panels.cc_display import build_cc_display_cards
            cards.extend(build_cc_display_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: WAYBAR WIDGETS ---
        if self.section == "waybar":
            cards.extend(build_cc_waybar_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: PERSONALIZATION ---
        if self.section == "personalization":
            from panels.cc_personalization import build_cc_personalization_cards
            cards.extend(build_cc_personalization_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: APPS (cc_apps) ---
        if self.section == "apps":
            from panels.cc_apps import build_cc_apps_cards
            cards.extend(build_cc_apps_cards(self, canvas, active_sub))
            return cards

        # --- SEZIONE DEDICATA: PERSONALIZATION -> THEME & COLORS (Trasparenza Live + [Apply]) ---
        if self.section == "personalization" and active_sub == "theme":
            trans_pct = get_ui_transparency_pct()
            alpha_val = (100 - trans_pct) / 100.0
            tr_title = "Trasparenza Globale UI (Live)" if cur_lang == "it" else "Global UI Transparency (Live)"
            tr_lbl = "Trasparenza:" if cur_lang == "it" else "Transparency:"
            pr_lbl = "Preset Rapidi:" if cur_lang == "it" else "Quick Presets:"
            apply_bar_btn = ActionBtn(
                "\uf00c Applica alla Barra" if cur_lang == "it" else "\uf00c Apply to Waybar",
                "cc:trans_apply",
                tone="apply",
                active=True,
            )

            tr_box = Box(
                tr_title,
                icon="\uf042",
                actions=[Badge(f"alpha={alpha_val:.2f}", style="info"), apply_bar_btn],
                elected=False,
                scroll_action="cc:trans_scroll",
            )
            tr_slider = Slider(
                trans_pct,
                "cc:trans",
                min_val=0,
                max_val=95,
                width=28,
                show_pct=True,
                step_buttons=True,
                step=5,
            )
            tr_box.add_row(f"  {Txt(f'{tr_lbl:<15}', role='schema')} {tr_slider}")

            preset_btns = "  ".join(
                SelectPill(f"{p}%", f"cc:trans:{p}", selected=(abs(trans_pct - p) <= 2))
                for p in (0, 15, 22, 35, 50, 75, 90)
            )
            tr_box.add_row(f"  {Txt(f'{pr_lbl:<15}', role='schema')} {preset_btns}")
            note_txt = (
                "  Anteprima live attiva su NC-UI. Premi [Applica alla Barra] per allineare la trasparenza di Waybar."
                if cur_lang == "it"
                else "  Live preview active on NC-UI. Click [Apply to Waybar] to sync the Waybar bar transparency."
            )
            tr_box.add_row(Txt(note_txt, role="schema"))
            cards.append(tr_box)

        # --- HERO WIREFRAME / BLUEPRINT VISUAL CARD ---
        hero_box = Box(
            f"{sec_title}  \u203a  {active_sub_label}",
            icon="\uf542",
            actions=[Badge(t("control_center.blueprint_badge"), style="info")],
            elected=False,
        )
        hero_box.add_row(Txt(t("control_center.skeleton_notice"), role="schema"))
        cards.append(hero_box)

        return cards

    def on_prompt_submit(self, action_id: str, text: str) -> None:
        from panels.cc_integrations import handle_cc_integrations_prompt
        from panels.cc_keyboard import handle_cc_keyboard_prompt
        from panels.cc_personalization import handle_cc_personalization_prompt
        from panels.cc_display import handle_cc_display_prompt
        from panels.cc_apps import handle_cc_apps_prompt
        from panels.cc_power import handle_cc_power_prompt
        from panels.cc_system import handle_cc_system_prompt
        if handle_cc_keyboard_prompt(self, action_id, text):
            return
        if handle_cc_display_prompt(self, action_id, text):
            return
        if handle_cc_apps_prompt(self, action_id, text):
            return
        if handle_cc_power_prompt(self, action_id, text):
            return
        if handle_cc_system_prompt(self, action_id, text):
            return
        if handle_cc_personalization_prompt(self, action_id, text):
            return
        if handle_cc_waybar_prompt(self, action_id, text):
            return
        if handle_cc_network_prompt(self, action_id, text):
            return
        if handle_cc_calendar_prompt(self, action_id, text):
            return
        if handle_cc_integrations_prompt(self, action_id, text):
            return
        if action_id == "cc_search" and text:
            q = text.strip().lower()
            for sec_id, _ in CONTROL_CENTER_SECTIONS:
                lbl = t(f"control_center.sections.{sec_id}", sec_id).lower()
                if q in sec_id.lower() or q in lbl:
                    self.set_section(sec_id)
                    break

    def on_link(self, uri: str) -> None:
        from panels.cc_integrations import handle_cc_integrations_link
        from panels.cc_keyboard import handle_cc_keyboard_link
        from panels.cc_personalization import handle_cc_personalization_link
        from panels.cc_display import handle_cc_display_link
        from panels.cc_apps import handle_cc_apps_link
        from panels.cc_audio import handle_cc_audio_link
        from panels.cc_power import handle_cc_power_link
        from panels.cc_system import handle_cc_system_link
        from panels.cc_updates import handle_cc_updates_link
        from panels.cc_auth import handle_cc_auth_link
        if handle_cc_keyboard_link(self, uri):
            return
        if handle_cc_apps_link(self, uri):
            return
        if handle_cc_audio_link(self, uri):
            return
        if handle_cc_power_link(self, uri):
            return
        if handle_cc_system_link(self, uri):
            return
        if handle_cc_updates_link(self, uri):
            return
        if handle_cc_auth_link(self, uri):
            return
        if self.section == "display" and handle_cc_display_link(self, uri):
            return
        if handle_cc_personalization_link(self, uri):
            return
        if handle_cc_display_link(self, uri):
            return
        if handle_cc_network_link(self, uri):
            return
        if handle_cc_calendar_link(self, uri):
            return
        if handle_cc_waybar_link(self, uri):
            return
        if handle_cc_integrations_link(self, uri):
            return
        if uri == "cc:trans_apply":
            if self.shell:
                self.shell.bump_leave_guard(1.5)
            apply_waybar_transparency()
            return
        if uri.startswith("cc:section:"):
            sec_id = uri.split(":", 2)[2]
            self.set_section(sec_id)
            if self.shell:
                self.shell.refresh_view()
        elif uri.startswith("cc:subtab:"):
            sub_id = uri.split(":", 2)[2]
            self.set_subtab(sub_id)
            if self.shell:
                self.shell.refresh_view()
        elif uri.startswith("cc:lang:"):
            lang_code = uri.split(":", 2)[2]
            set_language(lang_code)
            if self.shell:
                self.shell.refresh_view()
        elif uri.startswith("cc:trans:"):
            try:
                val = int(round(float(uri.split(":", 2)[2])))
                set_ui_transparency_pct(val, sync_waybar=False)
                if self.shell:
                    self.shell._apply_css()
                    self.shell.refresh_view()
            except Exception:
                pass
        elif uri.startswith("cc:trans_scroll:"):
            direction = uri.rsplit(":", 1)[1]
            cur = get_ui_transparency_pct()
            step = 5 if direction == "up" else -5
            set_ui_transparency_pct(cur + step, sync_waybar=False)
            if self.shell:
                self.shell._apply_css()
                self.shell.refresh_view()


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return ControlCenterPanel(initial_section=panel_id, shell=shell)
