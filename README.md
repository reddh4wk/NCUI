<div align="center">

```text
╭─────────────────────────────────────────────────────────────────────────────╮
│                                                                             │
│   ███╗   ██╗ ██████╗      ██╗   ██╗██╗                                      │
│   ████╗  ██║██╔════╝      ██║   ██║██║    MONOSPACE PRECISION               │
│   ██╔██╗ ██║██║     █████╗██║   ██║██║    WAYLAND LAYER-SHELL               │
│   ██║╚██╗██║██║     ╚════╝██║   ██║██║    ZERO BLOAT ENGINE                 │
│   ██║ ╚████║╚██████╗      ╚██████╔╝██║                                      │
│   ╚═╝  ╚═══╝ ╚═════╝       ╚═════╝ ╚═╝    [● ONLINE]  [✓ PARAMETRIC]        │
│                                                                             │
╰─────────────────────────────────────────────────────────────────────────────╯
```

### *An interface for everyone. Engineered for the few.*

[![Platform](https://img.shields.io/badge/Wayland-niri_%2F_Layer--Shell-00f0ff?style=for-the-badge&labelColor=0b0c10&logo=wayland&logoColor=00f0ff)](#-architecture--control-center)
[![Renderer](https://img.shields.io/badge/Renderer-GTK3_%2B_Cairo_%2B_Pango-00ff73?style=for-the-badge&labelColor=0b0c10)](#-the-four-orthogonal-channels)
[![Zero Bloat](https://img.shields.io/badge/Python_Deps-Zero_External_Packages-ffeb19?style=for-the-badge&labelColor=0b0c10&logo=python&logoColor=ffeb19)](#-quick-start)
[![Shader Engine](https://img.shields.io/badge/nc--matrix-C11_%2B_OpenGL_%2F_EGL-ff2a9d?style=for-the-badge&labelColor=0b0c10)](#-nc-matrix-native-wallpaper-engine)
[![i18n](https://img.shields.io/badge/Locales-EN_%7C_IT_Hot--Reload-8c9bba?style=for-the-badge&labelColor=0b0c10)](#-configuration--i18n)

<br/>

> *"Terminal-grade density doesn't have to sacrifice ergonomics — and modern GUIs don't have to cost 800 MB of Chromium RAM."*

</div>

---

**NC-UI** is a high-density, keyboard-first **Desktop Control Center** and **Waybar Layer-Shell Popup Suite** crafted in Python, C11, Cairo, and Pango.  
It bridges the gap between raw Linux CLI control and tactile visual feedback — giving power users deterministic telemetry, sub-pixel monospace geometry, hardware-accelerated wallpaper shaders, and zero third-party Python or GNOME dependencies.

---

## 🧭 Philosophy: Built for Clarity Under Pressure

> *"Most interfaces shout with color everywhere until nothing stands out. NC-UI whispers in graphite — so when Cyan, Emerald, or Crimson lights up, your eye lands on the truth in 50 milliseconds."*

NC-UI is governed by an internal visual constitution ([`.agents/rules/ncui-golden_standard.md`](.agents/rules/ncui-golden_standard.md)) built on **Four Orthogonal Visual Channels**, ensuring every pixel answers a single question without visual noise:

| Visual Channel | Cognitive Question | Technical Mechanism |
| :--- | :--- | :--- |
| **1. Morphology & Delimiters** | *"What is this? Can I click, toggle, cycle, slide, or type in it?"* | `[...]` strictly marks clickable controls; `[● Opt]` / `[○ Opt]` = state pills; `[↻ Val]` = cycle pill; `[✎ ..]` = text prompt; `[-] [####----] [+]` = interactive slider; `[####----]` = read-only meter; `● STATE` (no brackets) = passive badge. |
| **2. Cairo Background Glow (`*_bg`)** | *"Where is an active selection, focus cursor, or live state right now?"* | Rounded pill highlights rendered in Cairo centered on cap-height (`cap_ink_h`). Reserved **exclusively** for persistent active state (`selected`, keyboard focus, `dirty` staged preview, live alerts) — **never** on idle buttons. |
| **3. 7 Semantic Color Axes** | *"What does this element mean operationally?"* | Every glyph and label resolves through 7 semantic theme axes (never hardcoded hex colors in UI panels). |
| **4. Sub-Pixel Box Geometry** | *"What is the hierarchy between context, keys, values, and tables?"* | Pango OpenType ligatures disabled (`liga=0`), Nerd Font/PUA auto-scaling, and continuous Cairo vector borders. |

### 🎨 The 7-Axis Semantic Palette

| Axis | Swatch | Role in the UI |
| :--- | :---: | :--- |
| **`structure`** | ![#43495c](https://img.shields.io/badge/%2343495c-Graphite_Frame-43495c?style=flat-square) | Box frames, section dividers, idle brackets `[...]`, empty meter tracks |
| **`schema`** | ![#8c9bba](https://img.shields.io/badge/%238c9bba-Slate_Metadata-8c9bba?style=flat-square) | Key-value labels, table column headers, neutral action buttons |
| **`inactive`** | ![#5f657b](https://img.shields.io/badge/%235f657b-Dimmed_State-5f657b?style=flat-square) | Unselected options (`○`), `OFF` / `STANDBY` badges, unset placeholders |
| **`focus` / `focus_val`** | ![#ffffff](https://img.shields.io/badge/%23ffffff-Pure_Focus-ffffff?style=flat-square) | Card titles, hovered controls, primary entity names & literal values |
| **`intent`** | ![#00f0ff](https://img.shields.io/badge/%2300f0ff-Electric_Cyan-00f0ff?style=flat-square) | Active tab/selection, cursor focus, interactive sliders & live URI links |
| **`vitality`** | ![#00ff73](https://img.shields.io/badge/%2300ff73-Emerald_Vitality-00ff73?style=flat-square) | Healthy OS/network state (`● ONLINE`, `ACCEPT`), constructive `[✓ Apply]` / `[+ Install]` actions |
| **`attention` / `interrupt` / `fault`** | ![#ffeb19](https://img.shields.io/badge/%23ffeb19-Warn-ffeb19?style=flat-square) ![#ff2a9d](https://img.shields.io/badge/%23ff2a9d-Interrupt-ff2a9d?style=flat-square) ![#ff213f](https://img.shields.io/badge/%23ff213f-Fault-ff213f?style=flat-square) | Staged previews & warnings (Amber), stop/remove/reset actions (Magenta), critical faults & `DROP` verdicts (Crimson) |

---

## 🧩 Control Center & Waybar Popups

### 🖥️ Control Center — **16 Domains × 51 Subtabs**

Launched as an independent floating layer-shell window (`nc-ctl settings`), cleanly split into declarative view builders (`panels/cc_*_*.py`) and deterministic system engines (`panels/cc_engine_*.py`):

| Icon | Section | Subtabs & Capabilities |
| :---: | :--- | :--- |
| ℹ️ | **System Information** | `overview` — OS/kernel telemetry, hardware inventory, session & runtime summary |
| 🖥️ | **Display** | `monitors`, `scaling` — Multi-monitor topology, modes, refresh rates, transforms & fractional scaling |
| 🎨 | **Personalizations** | `theme`, `wallpaper` — Live theme sandbox, dual-layer transparency, 7-axis palette editor, Waybar sync, multi-monitor wallpaper scenes, slideshows, videos & `nc-matrix` shader controls |
| ⊞ | **Waybar Plug-Ins** | `layout`, `options` — Live module placement across `left`/`center`/`right` bar zones, spacing & per-module behavior |
| 📦 | **Applications** | `floating`, `mime`, `autostart` — Compositor floating window rules, default apps/package-manager/MIME handlers & autostart entries |
| ⌨️ | **Input Devices** | `mouse`, `keyboard`, `macros`, `shortcuts`, `controllers` — Pointer accel/scroll, XKB layouts, `/dev/uinput` hardware macro editor, compositor keybinding explorer & controllers |
| 🖨️ | **Printers** | `devices`, `queue`, `discovery`, `server` — Automated TCP/IP & USB printer discovery, Smart Auto driver synthesis, AUR/distro package scoring (`paru`/`yay`/`pacman`/`apt`/`dnf`/`zypper`), CUPS pools & log controls |
| 🔊 | **Sound** | `output`, `input`, `mixer`, `devices` — PipeWire/WirePlumber sinks, sources, per-stream volumes & hardware profiles |
| 🌐 | **Network** | `interfaces`, `wifi_bt`, `vpn_proxy`, `wan_routing` — Wired interfaces, Wi-Fi/Bluetooth management, VPN/proxy toggles, DNS/routing & watchdog targets |
| 🛡️ | **Firewall (`nftables`)** | `rules`, `chains`, `connections` — Interactive multi-column rule composer, chain policies, dry-run validation & active flow inspection |
| ⚡ | **Power & UPS** | `profiles`, `ups`, `idle` — Power profiles, NUT (`upsc`) battery/load monitoring & Wayland idle/DPMS timers |
| 🕒 | **Date & Time** | `time`, `calendar` — NTP sync, timezone picker with live completion & interactive calendar |
| ⬆️ | **Updates** | `pacman`, `aur`, `flatpak`, `orphans` — Unified system update scanner, AUR & Flatpak updates & orphan package cleanup |
| 🔒 | **Authorization & Security** | `tpm`, `keys`, `polkit`, `sudoers` — TPM2 LUKS2 enrollment, SSH/signing keys, Polkit inspection & zero-wildcard `visudo`-verified `/etc/sudoers.d/ncui-*` privilege manager |
| 🔧 | **Telemetry & Power Tools** | `power_tools`, `resources`, `storage`, `backup`, `boot` — Embedded TUI launchers, live resource telemetry, disks/mounts, backup workflows & bootloader/kernel inspector |
| ⚙️ | **UI Settings** | `interface`, `notifications`, `app_rules` — Side placement, dimensions, language switcher, notification DND/history & compositor rules |

### ⚡ Tactile Waybar Popups — **13 Anchorable Micro-HUDs**

Click any Waybar module powered by `bin/nc-bar-agent` to toggle a focused layer-shell popup (`panels/wp_*.py`):

> 🔊 `wp_audio` · 📅 `wp_calendar` · 📸 `wp_capture` · 📋 `wp_clipboard` · 🖥️ `wp_display` · ⌨️ `wp_kb_layout` · 🎵 `wp_media` · 📡 `wp_network` · 🔔 `wp_notifications` · 🖨️ `wp_printers` · ⏻ `wp_session` · 📊 `wp_system` (CPU/RAM) · 🔋 `wp_ups`

---

## 🎮 Interaction Physics

NC-UI never guesses what you wanted to scroll or edit:

- **Element-Hover Modulation:** Move your pointer over any `Slider` (`[-] [####----] [+]`) — the entire widget illuminates, and both the **scroll wheel** and **`←`/`→`** adjust its value with rate-limited precision. Move off the slider, and scrolling immediately returns to the page.
- **2D Spatial Keyboard Navigation:** Navigate smoothly between visible controls with `Arrows` or `Tab`, activate with `Enter`/`Space`, and confirm inline fields deterministically with `Enter` (`Esc` cancels).
- **Tab Completion Everywhere (`core/completion.py`):** Press `Tab` / `Shift+Tab` inside path, interface, timezone, XKB layout, or application inputs for instant candidate cycling.
- **Global Floating Search (`Ctrl+F`):** Jump to any setting across all 51 subtabs in any active locale (`EN` / `IT`) without touching the mouse.
- **Bar Surfing (`Shift + ←/→`):** Cycle subtabs inside Control Center or surf directly across neighboring Waybar popups.

---

## 🧬 `nc-matrix`: Native C11 / OpenGL Wallpaper Engine

Built by the split [`PKGBUILD`](PKGBUILD) as the `nc-matrix` package (sources in [`packages/nc-wallpapers/nc-matrix/`](packages/nc-wallpapers/nc-matrix/)), **nc-matrix** renders customizable digital rain in TrueColor 24-bit TTYs or directly on Wayland outputs via `gtk-layer-shell`, `OpenGL/EGL` shaders, and pre-baked Pango/Cairo glyph atlases (`ASCII`, `Katakana JP`, `Binary`, `Hexadecimal`), with automatic software fallback.

> Installs `/usr/bin/nc-matrix` (`nc-matrix` package; links against `gtk+-3.0`, `gtk-layer-shell-0`, `pangocairo`, `epoxy`, `ncursesw`, `gio-unix-2.0`, `wayland-client`, `wayland-egl`, `egl`).

---

## 🚀 Quick Start

### Compatibility & Roadmap
NC-UI is currently built and tested for **Arch Linux** (`makepkg` split packages) running on **Wayland** with **`niri`** and **`waybar`**, integrating natively with **PipeWire/WirePlumber**, **NetworkManager**, **`nftables`**, **CUPS**, **NUT (`upsc`)**, **Limine**, and **Ghostty** (optional domain backends stay modular).

> In future releases I plan to broaden NC-UI's compatibility across more Linux distributions, Wayland compositors, status bars, and system backends.

### Dependencies
NC-UI requires **zero external PyPI packages** — only standard OS system libraries:
- Python 3.11+ with `PyGObject` (`gi`)
- `gtk3`, `gtk-layer-shell`, `cairo`, `pango`
- Wayland compositor (`niri`) + `waybar`
- Optional domain backends: `pipewire` / `wireplumber`, `nftables`, `cups`, `networkmanager`, `nut`, `pacman` / `paru` / `apt` / `dnf` / `zypper`

### Clone, Install & First-Run Setup (`nc-ctl`)
```bash
git clone https://github.com/reddh4wk/NCUI.git && cd NCUI

# Build & install all 15 split packages (nc-matrix included) and clean build artifacts
makepkg -sic

# Or: pick individual packages from the 'ncui' pacman group (Enter = all)
./install.sh
```

Once installed, **`nc-ctl`** (`/usr/bin/nc-ctl`) acts as the unified CLI entrypoint for provisioning, launching Control Center / Waybar popups, triggering quick actions, and restoring desktop wallpapers:

```bash
# First-run user provisioning (run as your normal user, never root):
#   • Seeds default ~/.config/ncui/{config,theme}.toml
#   • Generates ~/.config/niri/ncui.kdl and links it in config.kdl (validated via niri)
#   • Installs synchronized Waybar config.jsonc & style.css
#   • Automatically backs up any overwritten user files (reversible anytime)
nc-ctl setup

# Inspect setup state or roll back cleanly to pre-NC-UI configs
nc-ctl setup --status
nc-ctl setup --dry-run
nc-ctl setup --restore

# Open Control Center or jump straight to a section & subtab
nc-ctl settings
nc-ctl settings personalization theme
nc-ctl settings printers discovery

# Toggle Waybar popups or trigger quick bar actions
nc-ctl popup network
nc-ctl popup capture
nc-ctl action audio vol-up
```

### Visual Verification (`nc-shot`)
Inspect any section, subtab, popup, or locale headlessly via Cairo/Broadway offscreen rendering:
```bash
./dev/nc-shot -l en -s firewall -t rules -o /tmp/firewall.png
./dev/nc-shot -l it -m popup -s audio -e -o /tmp/audio_popup.png
```

---

## 📂 Repository Structure

```text
ncui/
├── assets/
│   └── screenshots/       # High-DPI showcase captures rendered with nc-shot
├── bin/                   # nc-ctl, nc-bar-agent, nc-shot, macro & wallpaper daemons
├── config/
│   ├── ncui/              # Runtime config.toml, theme.toml, rule schemas
│   └── waybar/            # Waybar config.jsonc & synchronized style.css
├── core/                  # Completion, i18n, privilege registry, probing & state
├── locales/               # Hot-reload EN & IT translation catalogs (TOML)
├── panels/
│   ├── control_center.py  # Modular Control Center coordinator (16 sections)
│   ├── cc_engine_*.py     # System inspection & async action engines
│   ├── cc_*_*.py          # Declarative Control Center subtab views (51 tabs)
│   └── wp_*.py            # Waybar layer-shell micro-popups (13 modules)
├── ui/                    # Sub-pixel Canvas, Box/Widget primitives & ShellWindow
└── utils/
    ├── nc-matrix/         # Standalone C11 + OpenGL/EGL Wayland & TTY digital rain package
    └── *.sh               # Desktop, floater, notification & screenshot utilities
```

---

## 🎯 Visual Showcase

<div align="center">

<table>
  <tr>
    <td width="50%" align="center">
      <b>🖥️ Display</b><br/>
      <img src="assets/screenshots/cc_display_monitors.png" alt="NC-UI Display Topology, Monitors, Scaling and Modes" />
    </td>
    <td width="50%" align="center">
      <b>🎨 Theme &amp; Colors</b><br/>
      <img src="assets/screenshots/cc_personalization_theme.png" alt="NC-UI Theme Sandbox and 7-Axis Semantic Color Studio" />
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>🧬 Wallpaper</b><br/>
      <img src="assets/screenshots/cc_personalization_wallpaper.png" alt="NC-UI Multi-Monitor Wallpaper Map and Native nc-matrix Engine" />
    </td>
    <td width="50%" align="center">
      <b>🎛️ Waybar Layout Configurator</b><br/>
      <img src="assets/screenshots/cc_waybar_layout.png" alt="NC-UI Waybar Layout Configurator" />
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>📦 Default Apps</b><br/>
      <img src="assets/screenshots/cc_apps_mime.png" alt="NC-UI Default Apps and MIME Handlers" />
    </td>
    <td width="50%" align="center">
      <b>⌨️ Macro Studio</b><br/>
      <img src="assets/screenshots/cc_input_macros.png" alt="NC-UI Hardware Macro Studio" />
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>🎮 Gaming Controllers</b><br/>
      <img src="assets/screenshots/cc_input_controllers.png" alt="NC-UI Gaming Controllers Calibration and Mapping" />
    </td>
    <td width="50%" align="center">
      <b>🖨️ Devices &amp; Prefs</b><br/>
      <img src="assets/screenshots/cc_printers_devices.png" alt="NC-UI Printer Devices and Preferences" />
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>🌐 Interface &amp; Profiles</b><br/>
      <img src="assets/screenshots/cc_network_interfaces.png" alt="NC-UI Network Interfaces and Connection Profiles" />
    </td>
    <td width="50%" align="center">
      <b>🛡️ Active Flows</b><br/>
      <img src="assets/screenshots/cc_firewall_connections.png" alt="NC-UI Firewall Active Flows" />
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>⚡ Power Profiles</b><br/>
      <img src="assets/screenshots/cc_power_profiles.png" alt="NC-UI Power Profiles and Thermal Telemetry" />
    </td>
    <td width="50%" align="center">
      <b>🔋 UPS &amp; NUT</b><br/>
      <img src="assets/screenshots/cc_power_ups.png" alt="NC-UI UPS and NUT Battery Monitoring" />
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>🔒 Sudoers &amp; Privs</b><br/>
      <img src="assets/screenshots/cc_auth_sudoers.png" alt="NC-UI Least-Privilege Sudoers Manager" />
    </td>
    <td width="50%" align="center">
      <b>🔧 Bootloader</b><br/>
      <img src="assets/screenshots/cc_telemetry_boot.png" alt="NC-UI Bootloader and Kernel Telemetry" />
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center">
      <b>🔔 Notifications &amp; Rules</b><br/>
      <img src="assets/screenshots/cc_settings_notifications.png" width="50%" alt="NC-UI Notification Center Rules and DND Settings" />
    </td>
  </tr>
</table>

<br/>

<table>
  <tr>
    <td width="50%" align="center">
      <b>📸 Quick Capture Mode</b><br/>
      <img src="assets/screenshots/wp_capture_studio.png" width="380" alt="NC-UI Quick Capture Mode Waybar Popup" />
    </td>
    <td width="50%" align="center">
      <b>🔔 Notification Center</b><br/>
      <img src="assets/screenshots/wp_notifications.png" width="380" alt="NC-UI Notification Center Waybar Popup" />
    </td>
  </tr>
</table>

<sub>▲ **Waybar Layer-Shell Popups (`wp_capture` &amp; `wp_notifications`):** anchored directly beneath the bar with instant capture targets, live alert management, and keyboard/wheel focus.</sub>

</div>

---

<div align="center">

<sub>Crafted in monospace · Driven by determinism · <b>NC-UI</b></sub>

</div>
