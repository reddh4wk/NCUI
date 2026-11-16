# NC-UI Architecture, System Context & Design Guidelines

## 1. Filosofia e Target ("Lazy Power User" / Cyberpunk / Netrunner / ASCII Monospace Lover)
- **Pubblico:** Power user / Systems & OT Security Engineer. Vuole il controllo granulare e profondo della CLI Linux senza dover digitare lunghi comandi ripetitivi.
- **Zero Bloat:** Nessuna integrazione social, nessun cloud invasivo, nessun wrapper opaco. Ogni pulsante o toggle deve corrispondere a un'azione deterministica, verificabile e reversibile sul sistema reale.
- **Contesto Sistema (`Nightcity`):**
  - **OS & Kernel:** Arch Linux con doppio kernel `linux-cachyos` e `linux-cachyos-hardened`.
  - **Boot & Crittografia:** Bootloader Limine + UKI (Unified Kernel Images), Secure Boot (`sbctl`), LVM su LUKS2 con sblocco TPM2 (`systemd-cryptenroll`).
  - **Resilienza & Backup:** Sistema di snapshot LVM pre-update (`nc-snapshot`), Golden Image Recovery ISO/partizione dedicata (`duskcity`), e sincronizzazione configurazioni su NAS (`10.10.0.20` / `nas.damiano.sarl`) tramite `nc-nas-conf.sh`.
  - **Desktop & Gaming:** Compositor Wayland `niri`, barra `waybar`, dual-boot / profilo gaming ad alte prestazioni (Ryzen 9 5950X + RTX 3090), audio PipeWire/WirePlumber, periferiche esterne (DAC USB, microfono QuadCast, multi-monitor).

---

## 2. Benchmark Funzionale (Cosa estrarre dagli altri OS e come sintetizzarlo)
Quando progetti una sezione, analizza sempre le funzionalità e la disposizione nei pannelli di:
- **KDE Plasma 6+:** Granularità tecnica estrema, regole per-finestra/per-app, profili energetici e audio pro, diagnostica dettagliata dei dispositivi.
- **GNOME 50+:** Ordine gerarchico, raggruppamento logico pulito, assenza di rumore visivo nei controlli primari.
- **Windows 11 / PowerToys:** Utility rapide da power-user (Key Remapper, Awake/Idle override, Gestione Avvio/Servizi, diagnostica di rete e storage).
- **macOS:** Immediatezza dello stato attivo (indicatori chiari di cosa è connesso/eletto) e coerenza dei toggle.
- **Deepin:** Eleganza della separazione in blocchi visivi distinti nel pannello laterale destro e accesso rapido ai preset.

### Protocollo di Sintesi per `Nightcity` (Come trarre le conclusioni)
A fronte dell'analisi dei 5 OS, applica sempre questo filtro in 3 passaggi per decidere cosa implementare:
1. **Intersezione Core (Controlli Primari):** Prendi le impostazioni presenti in tutti gli OS (es. volume, layout tastiera, scala monitor, timeout schermo) e mettile nel primo `Box` della subtab, immediatamente accessibili con 1 click.
2. **Estensione Power-User (KDE / PowerToys + Arch Linux):** Aggiungi i controlli tecnici avanzati che un utente esperto cerca sempre (es. quantum PipeWire, parametri `sysctl`, mount opzioni NAS, regole KDL di Niri, priorità autoconnect, servizi `systemd`). Per non appesantire la vista, raggruppali in card dedicate o dentro **sezioni collassabili (`add_collapsible`)**.
3. **Integrazione Infrastruttura `Nightcity` (Tailored System):** Aggiungi sempre ciò che i DE consumer non hanno ma che governa l'architettura reale della macchina:
   - Gestione ad alto livello del bootloader **Limine** (`limine.conf`) e verifica **UKI / Secure Boot (`sbctl`)**.
   - Ispezione e gestione **TPM2 (`systemd-cryptenroll`)** e slot **LUKS2**.
   - Creazione/rollback **snapshot LVM (`nc-snapshot`)**, stato **Golden Image**, e sincronizzazione backup su **NASUS (`10.10.0.20` / `nc-nas-conf.sh`)**.
   - Parametri custom degli script di sistema in `/usr/local/sbin/nc-*`.

---

## 3. Architettura Dual-Style: `standard` vs `cyberpunk` (Single Backend / Dual View)
Il Control Center supporta due stili di pannelli selezionabili dall'utente nella sezione `settings`:
1. **Stile `standard`:** Layout razionale, pulito e strutturato tramite `Box`, `Divider`, `add_kv`, `add_table` e `add_collapsible`. Massima leggibilità tabellare.
2. **Stile `cyberpunk`:** Layout artistico/funzionale ispirato a terminali Netrunner / Matrix / Schemi Hardware. Usa diagrammi ASCII, topologie di bus, chip pinout e matrici di routing in cui **i nodi stessi del disegno ASCII sono pulsanti interattivi (`Btn`, `Badge`, `Slider`, `StatusDot`)** perfettamente funzionanti.

### Regola d'Oro d'Implementazione (Zero Duplicazione Logica)
In ogni file `panels/cc_<section>.py`:
- **Il Backend è UNICO:** La raccolta dati (`_collect_*`), la validazione degli input, la verifica post-azione (`probe.run_cmd_full`) e gli handler `handle_cc_<section>_link()` / `handle_cc_<section>_prompt()` vengono scritti **una sola volta**.
- **Gli URI sono IDENTICI:** I pulsanti nello stile `cyberpunk` devono emettere gli stessi identici `href` URI dello stile `standard` (es. `cc_kbd:rate:50`, `cc_pwr:gov:performance`).
- **Smistamento della Vista:** La funzione `build_cc_<section>_cards(cc_panel, canvas, active_sub)` legge `get_cc_panel_style()` (che restituisce `"standard"` o `"cyberpunk"`) e delega il solo assemblaggio grafico dei `Box` a `_build_<subtab>_standard(...)` oppure `_build_<subtab>_cyberpunk(...)`.

---

## 4. Ricchezza senza "Muri di Testo" (Progressive Disclosure & Condizionalità)
Per offrire decine di impostazioni mantenendo l'interfaccia ariosa e leggibile:
1. **Sezioni Espandibili / Collassabili (`box.add_collapsible`):**
   - Quando una card contiene parametri avanzati, diagnostica verbosa o liste lunghe (es. dettagli moduli kernel, tutti i profili secondari, flag avanzati), racchiudili in `box.add_collapsible(panel, "adv_key", "Impostazioni Avanzate", build_fn)`.
   - Di default la sezione mostra solo una riga compatta con il pulsante `[▸ Mostra ...]`; cliccandolo si espande in-place.
2. **Visibilità Condizionale dei Pacchetti (`check_capability` & `hide_unavailable_settings`):**
   - Se una funzione dipende da un pacchetto opzionale non installato (es. `wlsunset`, `cpupower`, `smartctl`, `gcalcli`, `upsc`), verifica la presenza del binario con `probe.has_bin("...")`.
   - Se l'opzione globale `hide_unavailable_settings` è attiva, ometti silenziosamente i controlli non supportati; se è disattivata, mostra la riga con un `Badge("MISSING: <pkg>", style="warn")` senza far fallire il pannello.

---

## 5. Leggi Geometriche, Cromatiche ed Estetiche Obbligatorie (`ui/kit.py`)
Tutta l'interfaccia è costruita esclusivamente con caratteri testuali monospace usando i componenti di `ui/kit.py`:
- **Contenitori e Struttura:** `Box`, `Divider` (allineato a destra di default), `box.add_section()`, `box.add_collapsible()`.
- **Dati e Griglie:** `box.add_kv()`, `box.add_table(..., show_headers=True/False)`.
- **Controlli Interattivi:** `Btn`, `StateToggleBtn`, `ToggleExpandBtn`, `Slider(..., step_buttons=True)`.
- **Indicatori Semantici:** `Badge`, `StatusDot`, `ProgressBar`, `Sparkline`.

### Regole Inviolabili:
1. **Coerenza Cromatica Centralizzata (VIETATO L'USO DI HEX HARDCODED):**
   - Non scrivere mai colori esadecimali (`#...`) direttamente nei pannelli. Usa esclusivamente i token semantici di `THEME["colors"]` (`primary`, `secondary`, `muted`, `accent`, `info`, `thresholds.warning`, `thresholds.critical`) e gli stili standard di `Btn`/`Badge` (`"accent"`, `"primary"`, `"secondary"`, `"info"`, `"warn"`, `"crit"`).
2. **Zero Sfondamento Orizzontale (Max 960px / ~74-78 col interne):**
   - Non mettere mai più di 1-2 azioni corte nel parametro `actions=[...]` di `Box()`.
   - Negli schemi ASCII dello stile `cyberpunk`, ogni riga deve avere una larghezza visiva massima di **72 colonne** (misurata senza tag Pango) e usare solo caratteri *single-width* (box-drawing `╭─╮│╰╯╔═╗║╚╝`, blocchi `█▓▒░`, frecce `◄►▲▼`), evitando emoji multipunto che alterano l'allineamento verticale dei bordi `│`.
3. **Griglia Chiave-Valore Costante:**
   - Usa sempre `box.add_kv(key, value_markup, right_markup=...)` lasciando il `key_width` di default (`DEFAULT_KEY_WIDTH = 20`) per mantenere l'asse verticale dei valori allineato lungo tutta la pagina.
4. **Vietato l'allineamento manuale con spazi in `add_row()`:**
   - Per qualsiasi elenco a più colonne (dispositivi, regole, scorciatoie, processi, partizioni, snapshot) nello stile `standard`, usa **sempre** `box.add_table(headers=[...], widths=[...], rows=[...], show_headers=True/False)`.
5. **Respiro Visivo:**
   - Massimo 5-6 coppie `add_kv` visibili per singolo `Box` (usa `add_collapsible` per il resto o spezza in più `Box` separati da `Divider("Titolo Gruppo")`).
   - Inserisci sempre `box.add_row(" ")` come prima e ultima riga di ogni `Box`.
6. **Verifica Reale Post-Azione:**
   - Ogni modifica di sistema deve usare `probe.run_cmd_full()`, verificare il codice di uscita e rileggere lo stato reale dal sistema, mostrando un feedback visivo (`✓ Applied` o banner d'errore con `stderr`).
7. **Bilinguismo Completo (`en.toml` / `it.toml`):**
   - Rispetta sempre `get_active_language()` o `t()` per tutte le etichette.
8. **Modularità dei File:**
   - Ogni sezione del Control Center deve vivere nel proprio file `panels/cc_<section>.py` ed essere richiamata da `panels/control_center.py`.
