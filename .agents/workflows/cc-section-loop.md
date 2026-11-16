---
description: Ciclo ricorsivo autonomo (batch unattended) di progettazione, implementazione dual-style (standard + cyberpunk), review visiva e checkpoint git per tutte le sezioni del Control Center NC-UI
---

Esegui questo workflow in ciclo continuo su **tutte** le sezioni non ancora completate (`[ ]` o `[~]`) in `.agents/cc_roadmap.md`, procedendo dalla prima all'ultima senza fermarti a chiedere conferma tra una sezione e l'altra. Prima di iniziare, leggi `.agents/rules/ncui-guidelines.md` e `.agents/cc_roadmap.md`, e crea la directory `/tmp/nc_review_batch`.

### STEP 0: Pre-Flight Check & Backup `.old`
1. Identifica in `.agents/cc_roadmap.md` la prima sezione con subtab non ancora completate (`[ ] std` o `[ ] cyber`).
2. Se la sezione tocca file già esistenti o bozze (`cc_integrations.py`, `cc_waybar.py`, `cc_calendar.py`, `cc_network.py`, `cc_net_*.py`, `cc_audio.py`, `cc_system.py`), verifica se esiste già una copia `<file>.py.old`. Se non esiste, creala subito prima di apportare qualsiasi modifica.

### STEP 1: Feature Parity Mapping (Primo Passaggio)
Senza cercare screenshot esterni, interroga la tua conoscenza dell'architettura UI/UX degli OS. Per ciascuna subtab della sezione corrente:
1. **Ricostruzione Mentale:** Elenca quali impostazioni espongono nativamente GNOME 50+, KDE Plasma 6+, Windows 11, macOS e Deepin in questa categoria, e come le raggruppano logicamente.
2. **Traduzione Linux/Arch:** Mappa queste impostazioni sui comandi/file nativi di `Nightcity` (`niri msg`, `config.kdl`, `systemctl`, `pactl`, `nmcli`, `sbctl`, `systemd-cryptenroll`, `smartctl`, `limine.conf`, script `/usr/local/sbin/nc-*`). Usa il parametro `requires=["binario"]` nei componenti di `ui/kit.py` per nascondere o segnalare le opzioni in base ai pacchetti installati.
3. **Anti-Muro di Testo:** Seleziona i 4-6 controlli primari da mostrare in chiaro. Destina i parametri avanzati o le liste lunghe a **sezioni espandibili (`add_collapsible`)**.

### STEP 2: Critica Avversaria & Design Dual-Style (Secondo Passaggio)
1. **Espansione Power-User:** Aggiungi impostazioni hardware e infrastrutturali specifiche per `Nightcity` (doppio kernel CachyOS/Hardened, UKI/Limine, LUKS2/TPM2, `nc-snapshot`, Golden Image, NAS `10.10.0.20`, `nc-nas-conf.sh`, gaming low-latency).
2. **Architettura Single Backend / Dual View:**
   - Progetta un solo backend di telemetria e dispatcher (`on_link` / `on_prompt_submit`).
   - **Vista `standard`:** Struttura pulita (`add_kv` con `DEFAULT_KEY_WIDTH = 20`, `add_table`, `add_collapsible`).
   - **Vista `cyberpunk`:** Diagrammi ASCII single-width (max 72 colonne interne, zero emoji multipunto) dove i nodi del disegno (chip, partizioni, bus, patchbay) sono gli interruttori cliccabili che emettono gli stessi identici URI della vista `standard`.

### STEP 3: Ciclo Implementazione & Verifica Visiva Doppia (UNA SUBTAB ALLA VOLTA)
Per **ciascuna subtab** della sezione corrente:
1. **Implementazione Modulare:**
   - Scrivi in `panels/cc_<section>.py` il backend condiviso (lettura stato, validazione input, esecuzione con `probe.run_cmd_full()` + rilettura di verifica e banner feedback `✓ Applied` / `✗ Error`) e le due funzioni di rendering (`_build_<subtab>_standard` e `_build_<subtab>_cyberpunk`) smistate tramite `get_cc_panel_style()`.
   - Non usare MAI colori esadecimali hardcoded nei pannelli: usa sempre i token semantici di `THEME["colors"]`.
   - Collega la sezione in `panels/control_center.py` e aggiungi eventuali chiavi mancanti in `locales/en.toml` e `locales/it.toml`.
2. **Verifica Sintattica:** Esegui `python3 -m py_compile` sui file modificati.
3. **Cattura Screenshot di Entrambi gli Stili:**
   - `python3 bin/nc-shot -s <section> -t <subtab> -S standard -o /tmp/nc_review_batch/<section>_<subtab>_std.png`
   - `python3 bin/nc-shot -s <section> -t <subtab> -S cyberpunk -o /tmp/nc_review_batch/<section>_<subtab>_cyber.png`
4. **Ispezione Visiva Critica & Rifinitura (Max 2 iterazioni per subtab):**
   - Apri entrambi i `.png` e verifica l'assenza di muri di testo, di bordi `│` disallineati, o di pulsanti troncati/spezzati.
   - Se rilevi difetti, correggili nel codice e rigenera i `.png` (massimo 2 passaggi di fix per subtab; se persiste un dettaglio minore, annotalo in `/tmp/nc_review_batch/NOTES.md` senza bloccare il batch).
   - Marca `[x] std | [x] cyber` per quella subtab in `.agents/cc_roadmap.md`.

### STEP 4: Commit Locale Atomico & Avanzamento Automatico
1. Quando tutte le subtab della sezione corrente sono completate:
   - Marca l'intera sezione come `[x]` in `.agents/cc_roadmap.md`.
   - Verifica che l'intero progetto compili senza errori: `python3 -m py_compile panels/*.py core/*.py ui/*.py`.
   - Esegui un commit locale di checkpoint (NON fare `git push`):
     `git add -A && git commit -m "feat(cc): implement <section> (standard + cyberpunk)"`
2. **Loop Continuo:** Torna immediatamente allo **STEP 0** per la sezione successiva di `.agents/cc_roadmap.md`. Fermati solo quando tutte le 16 sezioni della roadmap sono marcate `[x]`, stampando il report finale completo.
