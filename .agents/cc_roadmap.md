# Control Center Implementation Roadmap

## Regole d'Oro sulle Sezioni e Bozze Esistenti
1. **VIETATO RIMUOVERE FEATURE ESISTENTI:** Tutte le funzionalità già implementate devono essere preservate integralmente (possono essere riorganizzate, rese collassabili o rifinite graficamente, ma mai eliminate).
2. **PROTOCOLLO DI BACKUP `.old` OBBLIGATORIO:**
   - Prima di modificare o ristrutturare i file esistenti di **`network`** (`cc_network.py`, `cc_net_wired.py`, `cc_net_wifi.py`, `cc_net_vpn.py`, `cc_net_wan.py`), **`waybar`** (`cc_waybar.py`), **`calendar`** (`cc_calendar.py`) o **`cc_integrations.py`**, fanne una copia di sicurezza `<nome_file>.py.old` nella stessa cartella se non esiste già.
3. **SCORPORO DA `cc_integrations.py`:**
   - Le bozze attualmente contenute in `cc_integrations.py` (`display`, `wallpaper`, `floating`) vanno rese indipendenti spostandole nei rispettivi file modulari (`cc_display.py`, `cc_personalization.py`, `cc_apps.py`).
4. **DOPPIO STILE PER OGNI SUBTAB (`[ ] std` | `[ ] cyber`):**
   - Una subtab può essere marcata come completata (`[x] std` | `[x] cyber`) solo quando entrambi gli stili (`standard` e `cyberpunk`) sono stati implementati sullo stesso backend e validati tramite screenshot con `bin/nc-shot`.

---

## Ordine di Esecuzione Sezioni (Una Sezione alla volta)

### 1. [x] `keyboard` (`panels/cc_keyboard.py`) — PRIORITÀ 1
- [x] std | [x] cyber — **Subtab `keyboard`**: Repeat rate/delay live in `~/.config/niri/config.kdl`, NumLock on boot, area di test, **Macro Manager** e **Key Capture / Event Inspector** (cattura e diagnostica di tasti speciali/multimediali non mappati out-of-the-box tramite `wev` / `libinput debug-events`).
- [x] std | [x] cyber — **Subtab `layout`**: Visualizzazione layout XKB attivi (`it`, `us`), selezione rapida di layout e varianti regionali, creazione/iniezione di layout custom e selettore lingua interfaccia NC-UI.
- [x] std | [x] cyber — **Subtab `shortcuts`**: Configuratore visuale delle scorciatoie di `niri` (`~/.config/niri/config.kdl`), inclusi i lanci rapidi di `nc-ctl` e la configurazione dei tasti di navigazione da tastiera della barra e del Control Center.

### 2. [x] `personalization` (`panels/cc_personalization.py`)
- [x] std | [x] cyber — **Subtab `theme`**: Trasparenza globale UI (preservare sincronizzazione Waybar), selettore live `box_style` (`rounded`, `sharp`, `heavy`, `double`), palette `accent`, colori soglia (`normal`, `warning`, `alert`, `critical`) e colori semantici di **selezione (`box_selected`), elezione (`box_elected`) ed evidenziazione (`box_highlight`)** in `theme.toml`. Personalizzazione di ogni icona testuale di sistema, tema e dimensione del cursore in Niri/GTK, e selezione/calibrazione dimensione del font (solo font monospace).
- [x] std | [x] cyber — **Subtab `creator`** (*Theme Creator Tool*): Definizione granulare degli elementi testuali che compongono la UI (caratteri degli angoli e bordi dei box, spaziatura, parentesi delle barre del titolo, bottoni, slider, sparkline) per creare, salvare, importare e condividere nuovi temi tramite file `.toml` dedicati (modificando forma e disposizione estetica senza alterare il contenuto dei pannelli).
- [x] std | [x] cyber — **Subtab `wallpaper`**: Rivedere la bozza presente in `cc_integrations.py` (`nc_util_desktop.sh`, slideshow, unicolor, profili e shader CMatrix ASCII/Katakana) rendendola completamente autonoma dentro `cc_personalization.py`.
- [x] std | [x] cyber — **Subtab `waybar`**: Personalizzazione completa della barra Waybar (ordine/sezioni widget `left`/`center`/`right`, larghezze percentuali dinamiche di `niri/window` e `custom/media`, opzioni pillola di rete e comando blocco schermo, partendo dal backup `.old` di `cc_waybar.py`).

### 3. [x] `display` (`panels/cc_display.py`)
- [x] std | [x] cyber — **Subtab `layout`**: Mappa spaziale interattiva multi-monitor Niri (`NiriOutputMap`), allineamento automatico, coordinate logiche e risoluzione nativa (scorporare da `cc_integrations.py`).
- [x] std | [x] cyber — **Subtab `scaling`**: Fattore di scala frazionario/intero, modalità hardware (`width x height @ Hz`), Variable Refresh Rate (**VRR** in Niri `config.kdl`), filtro luce blu / **Night Light** (`wlsunset` / `gammastep` con temperatura K e orari) e timeout **DPMS**.

### 4. [x] `apps` (`panels/cc_apps.py`)
- [x] std | [x] cyber — **Subtab `floating`**: Gestore regole finestre floating di Niri (`window-rule` in `~/.config/niri/config.kdl` e picker finestre attive), reso autonomo da `cc_integrations.py`.
- [x] std | [x] cyber — **Subtab `mime`**: Gestore associazioni `xdg-mime` / `xdg-settings` per Browser Web, Emulatore Terminale, File Manager, Editor di Testo/Codice, Lettore PDF, Visualizzatore Immagini e Player Audio/Video.
- [x] std | [x] cyber — **Subtab `autostart`**: Gestione voci `spawn-at-startup` in `~/.config/niri/config.kdl`, gestione servizi utente (`systemctl --user`), e ispettore in sola lettura dei servizi di sistema attivi/falliti (`systemctl list-units --type=service`).

### 5. [x] `sound` (`panels/cc_audio.py`)
- [x] std | [x] cyber — **Subtab `output`**: Periferiche di uscita PipeWire (DAC USB, HDMI, Bluetooth), elezione sink predefinito, volume, mute, test canali e over-amplification.
- [x] std | [x] cyber — **Subtab `input`**: Ingressi microfonici (es. HyperX QuadCast S), elezione source predefinito, guadagno, mute e stato loopback/filtro rumore.
- [x] std | [x] cyber — **Subtab `mixer`**: Controllo individuale dei flussi applicativi attivi (`sink-inputs`) e parametri runtime PipeWire (`pw-metadata` quantum / sample-rate per bassa latenza gaming o Hi-Res audio).
- [x] std | [x] cyber — **Subtab `devices`**: Profili hardware delle schede audio (`pactl list cards`, commutazione profili Analog Stereo / Digital IEC958 / Pro Audio / Codec Bluetooth A2DP/HSP).

### 6. [x] `power` (`panels/cc_power.py`)
- [x] std | [x] cyber — **Subtab `general`**: CPU frequency governor (`cpupower` / `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`), Energy Performance Preference (EPP), boost clock toggle (CPB) e telemetria consumi/temperature CPU/GPU.
- [x] std | [x] cyber — **Subtab `ups`**: Telemetria e configurazione **NUT Server** (`upsc`): configurazione IP/nome dell'UPS (es. `ecoflow@10.10.0.20`), visualizzazione carico in Watt, percentuale batteria, autonomia residua, tensione IN/OUT, soglie di allarme, configurazione graceful shutdown e notifiche di sistema personalizzate.
- [x] std | [x] cyber — **Subtab `wakeup`**: Timeout di inattività, spegnimento schermo DPMS e blocco automatico sessione (`swayidle` / `hypridle`) integrati col comando di screen lock.

### 7. [x] `system` (`panels/cc_system.py`)
- [x] std | [x] cyber — **Subtab `about`**: Riepilogo hardware e sistema operativo (`Nightcity`), stato doppio kernel (`linux-cachyos` / `linux-cachyos-hardened`), versione bootloader Limine e uptime.
- [x] std | [x] cyber — **Subtab `power_tools`**: Pannello di controllo, configurazione parametri ed esecuzione assistita degli script custom `nc-*` presenti in `/usr/local/sbin/` (o nella cartella di progetto `ncscripts`).
- [x] std | [x] cyber — **Subtab `resources`**: Telemetria estesa 960px per CPU (per-core sparklines, frequenze, loadavg) e RAM/Swap, gestore processi top (ordinamento CPU/RAM, ricerca, `SIGTERM`/`SIGKILL`), e tuning parametri `sysctl` (`vm.swappiness`, ZRAM, ` dirty_ratio`).
- [x] std | [x] cyber — **Subtab `storage`**: Struttura dischi e partizioni (`lsblk`), volumi logici LVM (`vgs`/`lvs`), salute NVMe SMART (`smartctl`), e stato di mount/spazio disponibile del NAS `NASUS` (`10.10.0.20`).
- [x] std | [x] cyber — **Subtab `backup`**: Stato degli snapshot LVM (`nc-snapshot`) e spazio CoW disponibile; verifica dello stato della Recovery (`duskcity`) e delle **Golden Image** (richiede verifica preliminare che il NAS sia montato); monitoraggio e trigger dei backup configurazioni a Torre di Hanoi tramite `nc-nas-conf.sh` (richiede NAS montato).

### 8. [x] `updates` (`panels/cc_updates.py`)
- [x] std | [x] cyber — **Subtab `packages`**: Report generabile manualmente sullo stato degli aggiornamenti di sistema: pacchetti ufficiali Arch (`checkupdates`), pacchetti AUR (`paru -Qua`), pacchetti orfani (`pacman -Qdtq`), pulizia cache (`paccache`) e lancio aggiornamento protetto in terminale.

### 9. [x] `auth` (`panels/cc_auth.py`)
- [x] std | [x] cyber — **Subtab `tpm`**: Stato UEFI Secure Boot (`sbctl status`, verifica firma immagini UKI), stato slot LUKS2 (`cryptsetup luksDump`) e binding con i registri PCR del chip TPM2 (`systemd-cryptenroll`).
- [x] std | [x] cyber — **Subtab `keys`**: Identità SSH caricate in `ssh-agent` (`ssh-add -l`), chiavi pubbliche in `~/.ssh/*.pub` con copia rapida, e stato chiavi GPG (`gpg --list-secret-keys`).
- [x] std | [x] cyber — **Subtab `polkit`**: Stato dell'agente di autenticazione Polkit in esecuzione, verifica configurazione `sudoers` / gruppo `wheel` e regole PAM/FIDO2.

### 10. [x] `datetime` (`panels/cc_datetime.py`)
- [x] std | [x] cyber — **Subtab `time`**: Stato sincronizzazione `systemd-timesyncd` (`timedatectl status`), toggle NTP, server pool NTP e stato orologio hardware RTC.
- [x] std | [x] cyber — **Subtab `timezone`**: Fuso orario attivo (es. `Europe/Rome`) e selettore rapido per aree geografiche.
- [x] std | [x] cyber — **Subtab `region`**: Impostazioni `locale.conf` (`LANG`, `LC_TIME`, formato 24h) e formato data nella barra.

### 11. [x] `notifications` (`panels/cc_notifications.py`)
- [x] std | [x] cyber — **Subtab `system`**: Modalità Non Disturbare (DND), programmazione oraria silenziosa, limite massimo notifiche in cronologia, test invio notifica (`nc_util_notify.sh`) e pulizia archivio.
- [x] std | [x] cyber — **Subtab `apps`**: Regole di filtro/soppressione notifiche per singola applicazione e livello di urgenza.

### 12. [x] `mouse` (`panels/cc_mouse.py`)
- [x] std | [x] cyber — **Subtab `general`**: Configurazione mouse in `~/.config/niri/config.kdl` (left-handed mode, natural scroll, middle-click emulation, scroll factor).
- [x] std | [x] cyber — **Subtab `pointer`**: Velocità puntatore (`accel-speed` `-1.0 .. +1.0`), profilo di accelerazione (`adaptive` vs `flat`) e impostazioni touchpad (`tap`, `dwt`, `natural-scroll`).

### 13. [x] `accounts` (`panels/cc_accounts.py`)
- [x] std | [x] cyber — **Subtab `profile`**: Identità utente (`UID`/`GID`, Home, Login Shell `zsh`/`bash`, cambio shell e cambio password in terminale).
- [x] std | [x] cyber — **Subtab `groups`**: Appartenenza ai gruppi di sistema critici (`wheel`, `docker`, `libvirt`, `wireshark`, `video`, `input`, `uucp`) con toggle di aggiunta/rimozione.
- [x] std | [x] cyber — **Subtab `sessions`**: Sessioni attive (`loginctl list-sessions`), stato seat/TTY e terminazione sessioni appese.

### 14. [~] `calendar` (`panels/cc_calendar.py`)
- [ ] std | [ ] cyber — **Subtab `mode`, `google`, `io`**: Fare backup `.old` di `cc_calendar.py`, preservare l'intero backend SQLite/gcalcli/ICS esistente e aggiungere la variante visuale `cyberpunk` accanto a quella `standard`.

### 15. [~] `network` & `bluetooth` (`panels/cc_network.py`, `cc_net_*.py`)
- [ ] std | [ ] cyber — **Subtab `wired`, `wireless`, `vpn`, `watchdog` + sezione `bluetooth`**: Fare backup `.old` dei 5 file attuali, preservare integralmente le validazioni e verifiche `nmcli`+Kernel e aggiungere la variante visuale `cyberpunk` accanto a quella `standard`.

### 16. [ ] `settings` (`panels/cc_settings.py` — sostituisce `general`)
- [ ] std | [ ] cyber — **Subtab `interface`**:
  - **Selettore Stile Pannelli Control Center:** Commutazione globale immediata tra set di pannelli **`standard`** e **`cyberpunk`**.
  - **Filtro Pacchetti Non Installati (`hide_unavailable_settings`):** Toggle per nascondere automaticamente o mostrare (con badge `[MISSING]`) le impostazioni nei vari pannelli i cui pacchetti sottostanti non sono installati sul sistema corrente.
  - Lato di ancoraggio del Control Center (`left` / `right`), lingua interfaccia (`en` / `it`), terminale preferito e livello di log (`/tmp/nc-ui.log`).
- [ ] std | [ ] cyber — **Subtab `boot`**: Gestione del menu di avvio **Limine** (`/boot/limine.conf` o `/efi/limine/limine.conf`), selezione entry predefinita (`linux-cachyos`, `linux-cachyos-hardened`, `Golden Image Recovery`, `Windows`), timeout di boot e stato UKI.
