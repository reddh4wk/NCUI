---
description: Ciclo ricorsivo di progettazione, implementazione dual-style (standard + cyberpunk) e review visiva di una sezione del Control Center NC-UI
---

Segui rigorosamente questi passaggi in ordine per la prossima sezione da completare in `.agents/cc_roadmap.md` (o per la sezione specificata dall'utente). Prima di iniziare, leggi sempre `.agents/rules/ncui-guidelines.md` e `.agents/cc_roadmap.md`.

### STEP 0: Pre-Flight Check & Backup `.old`
1. Identifica in `.agents/cc_roadmap.md` la prima sezione con subtab non ancora completate (`[ ] std` o `[ ] cyber`).
2. Se la sezione tocca file già esistenti o bozze (`cc_integrations.py`, `cc_waybar.py`, `cc_calendar.py`, `cc_network.py`, `cc_net_*.py`, `cc_audio.py`, `cc_system.py`), verifica se esiste già una copia `<file>.py.old`. Se non esiste, creala subito (`cp <file>.py <file>.py.old`) prima di apportare qualsiasi modifica.
3. Verifica quali funzioni esistenti devono essere preservate o scorporate da `cc_integrations.py` per renderle autonome nel modulo `panels/cc_<section>.py`.

### STEP 1: Feature Parity Mapping (Primo Passaggio)
Senza cercare screenshot esterni, interroga la tua conoscenza dell'architettura UI/UX degli OS. Per ciascuna subtab:
1. **Ricostruzione Mentale:** Elenca quali impostazioni espongono nativamente GNOME 50+, KDE Plasma 6+, Windows 11, macOS e Deepin in questa categoria, e come le raggruppano logicamente.
2. **Traduzione Linux/Arch:** Mappa queste impostazioni sui comandi nativi di `Nightcity` (`niri msg`, `config.kdl`, `systemctl`, `pactl`, `nmcli`, `sbctl`, ecc.). Usa il parametro `requires=["binario"]` nei componenti di `ui/kit.py` per nascondere o segnalare le opzioni in base ai pacchetti installati.
3. **Anti-Muro di Testo:** Seleziona i 4-6 controlli primari da mostrare in chiaro. Destina i parametri avanzati a **sezioni espandibili (`add_collapsible`)**.

### STEP 2: Critica Avversaria & Design Dual-Style (Secondo Passaggio)
Rileggi criticamente la proposta dello Step 1 e rispondi a:
1. **Espansione Power-User:** *"Quali opzioni avanzate o specifiche per l'infrastruttura `Nightcity` (doppio kernel CachyOS/Hardened, UKI/Limine, LUKS2/TPM2, `nc-snapshot`, Golden Image, NAS `10.10.0.20`, `nc-nas-conf.sh`, gaming low-latency) mancano ancora?"*
2. **Architettura Single Backend / Dual View:**
   - Definisci gli URI di azione unificati (`cc_<sec>:...`) che verranno usati da entrambi gli stili.
   - **Design Vista `standard`:** Struttura tabellare/chiave-valore pulita (`add_kv` con `DEFAULT_KEY_WIDTH = 20`, `add_table`, `add_collapsible`).
   - **Design Vista `cyberpunk`:** Progetta lo schema tecnico interattivo in puro ASCII single-width (max 72 colonne interne: topologie di bus, chip pinout, patchbay, timeline di rollback) in cui i nodi stessi del disegno sono i pulsanti cliccabili (`Btn`, `Badge`, `StatusDot`, `Slider`).

### STEP 3: Ciclo Implementazione & Verifica Visiva Doppia (UNA SUBTAB ALLA VOLTA)
Procedi **una subtab alla volta** seguendo questo loop stretto:
1. **Implementazione Modulare:**
   - Scrivi in `panels/cc_<section>.py` il backend condiviso (lettura stato, validazione input, esecuzione con `probe.run_cmd_full()` + rilettura di verifica e banner feedback `✓ Applied` / `✗ Error`).
   - Implementa le due funzioni di rendering della subtab (`_build_<subtab>_standard` e `_build_<subtab>_cyberpunk`) smistate in base a `get_cc_panel_style()`.
   - Non usare MAI colori esadecimali hardcoded nei pannelli: usa sempre i token semantici di `THEME["colors"]`.
   - Collega la sezione in `panels/control_center.py` e aggiorna `locales/en.toml` e `locales/it.toml`.
2. **Verifica Sintattica:**
   - Esegui `python3 -m py_compile` su tutti i file modificati.
3. **Cattura Screenshot di Entrambi gli Stili:**
   - Genera lo screenshot in stile `standard`:
     `python3 bin/nc-shot -s <section> -t <subtab_id> -S standard -o /tmp/nc_review_<section>_<subtab_id>_std.png`
   - Genera lo screenshot in stile `cyberpunk`:
     `python3 bin/nc-shot -s <section> -t <subtab_id> -S cyberpunk -o /tmp/nc_review_<section>_<subtab_id>_cyber.png`
4. **Ispezione Visiva Critica di Entrambi gli Screenshot:**
   Visualizza entrambi i file `.png` generati e verifica:
   - **Geometria & Bordi:** Il bordo destro `│` e gli angoli `╮`/`╯` sono perfettamente allineati? Nessun titolo o schema ASCII nello stile `cyberpunk` sporge o manda a capo i bordi?
   - **Leggibilità & Densità:** La vista è ariosa e ordinata per gruppi logici, con uso corretto di `Divider`, righe vuote e sezioni collassabili (nessun muro di testo)?
   - **Ergonomia & Feedback:** Tutti i pulsanti sono atomici (non spezzati su due righe) e sono presenti gli indicatori di stato reale (`Badge`, `StatusDot`)?
5. **Rifinitura Immediata:**
   - Correggi subito qualsiasi difetto visivo o funzionale emerso dagli screenshot, rigenera i `.png` per confermare la correzione e marca `[x] std | [x] cyber` per quella subtab in `.agents/cc_roadmap.md`.

### STEP 4: Chiusura Sezione e Checkpoint
1. Quando tutte le subtab della sezione hanno superato la doppia verifica visiva (`std` + `cyber`):
   - Aggiorna `.agents/cc_roadmap.md` marcando l'intera sezione come `[x]`.
   - Riassumi in poche righe le funzionalità implementate e i path degli screenshot salvati in `/tmp/nc_review_*.png`.
2. Fermati in attesa di conferma dell'utente prima di passare alla sezione successiva (a meno che l'utente non abbia esplicitamente ordinato un'esecuzione continua su più sezioni).
