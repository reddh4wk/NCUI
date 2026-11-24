---
trigger: always_on
description: Costituzione architetturale, di sistema (Nightcity), linguistica (i18n), deterministica e di validazione obbligatoria per qualsiasi modifica a NC-UI, con vincolo tassativo a ncui-golden_standard.md.
---

- **Archivio di Riferimento (`.gnome-panels/`):** Directory di sola lettura ignorata da Git. Non ispezionarla durante i task ordinari; leggila solo quando viene richiesta esplicitamente un'analisi comparativa su uno specifico pannello (`.gnome-panels/<sezione>/`), estraendo esclusivamente gerarchia UX ed edge-case di validazione senza importare pattern `GObject`, `gsettings`, `mutter` o `libadwaita`.

# NC-UI Architecture, System & Engineering Constitution

Questa regola è attiva in modo permanente su **qualunque prompt**. Quando scrivi, modifichi o revisioni codice in questo repository, devi agire simultaneamente come **Systems Engineer** (codice minimale, deterministico, sicuro, zero dipendenze esterne) e come **Senior TUI/UI Designer**.

> **IMPERATIVO TASSATIVO PER LA UI (OBBLIGO DI `read_file` INTEGRALE):**
> Per **qualunque creazione, modifica o revisione** di pannelli (`panels/cc_*.py`, `panels/wp_*.py`) o di moduli grafici (`ui/*.py`), **DEVI prima leggere integralmente dall'inizio alla fine `/home/damiano/Projects/ncui/.agents/rules/ncui-golden_standard.md` tramite `read_file`** (non basarti mai sulla sola anteprima iniettata nel prompt di sistema, che viene troncata oltre una certa lunghezza) e applicarlo alla lettera. Nessuna scelta di colore, evidenziazione di sfondo, parentesi/simbolo, spaziatura, tabella, campo di input o interazione mouse/tastiera può deviare dal Golden Standard.

---

## 1. Filosofia e Contesto del Sistema (`Nightcity`)
- **Target:** Power user / Systems & OT Security Engineer. Vuole il controllo granulare della CLI Linux senza digitare comandi ripetitivi.
- **Zero Bloat & Zero Dipendenze Esterne:** Nessuna integrazione social/cloud superflua, nessun pacchetto Python esterno oltre lo stack di sistema (`PyGObject` / `GTK3` / `Cairo` / `Pango` / stdlib Python), nessun riferimento o dipendenza da GNOME/`gsettings`. Ogni controllo deve corrispondere a un'azione deterministica, verificabile e reversibile.
- **Stack Sistema (`Nightcity`):**
  - **OS & Kernel:** Arch Linux con doppio kernel `linux-cachyos` e `linux-cachyos-hardened`.
  - **Boot & Sicurezza:** Limine (`limine.conf`) + UKI, Secure Boot (`sbctl`), LVM su LUKS2 con TPM2 (`systemd-cryptenroll`), privilegi granulari *least-privilege* in `/etc/sudoers.d/ncui-*` validati con `visudo -cf` (`core/privileges.py`).
  - **Resilienza & Storage:** Snapshot LVM (`nc-snapshot`), Golden Image Recovery (`duskcity`), backup configurazioni su NAS `NASUS` (`10.10.0.20` / `nc-nas-conf.sh`).
  - **Desktop & Hardware:** Compositor Wayland `niri` (`~/.config/niri/config.kdl`), `waybar`, PipeWire/WirePlumber, Ryzen 9 5950X + RTX 3090, UPS EcoFlow via NUT (`upsc`).

---

## 2. Legge Tassativa di Localizzazione (`core/i18n.py` & `locales/*.toml`)
1. **ZERO STRINGHE HARDCODED IN UI:** È severamente vietato inserire stringhe visibili all'utente (titoli di `Box`, `Divider`, `add_kv`, `add_section`, `add_table`, etichette di pulsanti, badge, messaggi di stato/errore, placeholder di input/ricerca) come letterali nel codice Python.
2. **VIETATO IL TERNARIO `get_active_language() == "it"` NEI PANNELLI:** Non scrivere mai `"Testo IT" if is_it else "Text EN"`. Usa **esclusivamente** `t("sezione.chiave")` (o `t("sezione.chiave", p0=val1, p1=val2)` per le stringhe parametriche) importato da `core.i18n`.
3. **AGGIORNAMENTO CONTESTUALE DEI DIZIONARI TOML:**
   - Ogni chiave usata in `t("...")` **deve esistere** sia in `locales/en.toml` sia in `locales/it.toml` sotto la sezione corrispondente al modulo (es. `[cc_personalization]`, `[cc_network]`, `[cc_power]`, `[common]`, `[ui]`).
   - Non passare fallback inglesi lunghi nel codice per evitare di scrivere nel TOML: popola sempre entrambi i file `.toml` nello stesso task in cui modifichi il `.py`.
4. **Uniche Eccezioni:** Icone Nerd Font (`\uf028`), ID tecnici interni/URI, path di sistema (`/etc/...`), nomi di binari CLI e log di debug (`log.debug()`).

---

## 3. Architettura Backend, Privilegi e Verifica Post-Azione
- **Separazione Engine / View:** La raccolta stato, il parsing di configurazioni e l'esecuzione di azioni di sistema risiedono nei moduli engine dedicati (`panels/cc_engine_*.py`, `core/*`), mantenendo i builder dei pannelli (`panels/cc_*.py`, `panels/wp_*.py`) snelli, dichiarativi e privi di duplicazioni logiche tra Control Center e popup Waybar.
- **Esecuzione Deterministica e Verifica di Sistema:** Ogni modifica di stato sul sistema deve passare per `probe.run_cmd_full()` (o helper equivalenti in `core/probe.py`), verificare il codice d'uscita e lo stato reale post-azione sul sistema operativo, e restituire un feedback esplicito e veritiero all'utente.
- **Sicurezza dei Privilegi:** Qualsiasi operazione amministrativa deve appoggiarsi al registro centralizzato in `core/privileges.py` (regole `/etc/sudoers.d/ncui-*` senza wildcard, permessi `0440 root:root`, sempre validate con `visudo -cf` prima dell'installazione) o a sessioni terminale autenticate esplicite.

---

## 4. Vincolo Imperativo allo Standard Estetico e Funzionale UI
Tutta la progettazione visiva, cromatica, simbolica, spaziale e interattiva di NC-UI è governata integralmente da **`.agents/rules/ncui-golden_standard.md`**.
Chiunque codifichi un nuovo pannello o ne modifichi uno esistente **ha l'obbligo tassativo** di attenersi a `.agents/rules/ncui-golden_standard.md` per:
- la grammatica morfologica dei delimitatori (`[...]`, `..`, `●`/`○`, `↻`, `▸`/`▾`) e dei costruttori di `ui/kit.py`;
- la **Legge Aurea dell'Evidenziazione di Sfondo (`*_bg`)** e la matrice `Archetipo Oggetto × 7 Assi Semantici`;
- la gerarchia delle cornici (`highlighted`), le spaziature di `Box`, righe, `Divider`, `TabBar` (`1.5x`) e l'incolonnamento tramite `add_kv` / `add_table`;
- i 5 archetipi standard di inserimento testuale/ricerca, l'autocompletamento (`core/completion.py`), il commit esclusivo su `Invio` e la Ricerca Globale (`Ctrl+F`);
- il paradigma di puntamento e modulazione sul singolo elemento in hover (`Slider` e controlli) e la navigazione spaziale 2D.

---

## 5. Gate di Validazione Obbligatorio (Prima di Concludere Qualsiasi Task)
Non dichiarare MAI completato un task su NC-UI senza aver eseguito realmente questi **4 controlli**:
1. **Audit `i18n`:** Verifica che nel codice toccato non ci siano stringhe UI letterali fuori da `t()` e che ogni nuova chiave sia presente sia in `locales/en.toml` sia in `locales/it.toml`.
2. **Audit `ncui-golden_standard.md`:** Verifica che nessun `ActionBtn` a riposo abbia sfondo attivo, che il Ciano (`intent`) sia usato solo per selezione/focus/modulazione attiva, che nessun testo statico usi parentesi `[...]`, e che spaziature e tabelle rispettino la griglia.
3. **Compilazione:** Esegui `python3 -m py_compile` su tutti i file `.py` modificati.
4. **Ispezione Visiva Reale (`bin/nc-shot`):** Genera lo screenshot reale con `python3 bin/nc-shot -s <section> -t <subtab> -o /tmp/nc_review_<section>_<subtab>.png` (o `-m popup -s <panel>` per i popup Waybar), apri l'immagine generata e ispezionala criticamente da UI Designer verificando contrasto, gerarchia visiva, allineamenti e assenza di bordi spezzati o ridondanze.
