---
trigger: always_on
description: Golden Standard estetico, cromatico, tipografico, geometrico e interattivo per l'intera interfaccia NC-UI (Control Center, Waybar Popups, Plugins).
---

# NC-UI Golden Standard — Manifesto Estetico, Cromatico, Geometrico e Interattivo

Questo documento costituisce la **legge formale e definitiva di design e ingegneria dell'interfaccia** per **NC-UI**. Qualsiasi creazione, modifica o revisione di pannelli (`panels/cc_*.py`, `panels/wp_*.py`) o primitive grafiche (`ui/kit.py`, `ui/canvas.py`, `ui/widgets.py`, `ui/shell.py`, `ui/markup.py`) deve conformarsi senza eccezioni ai principi e alle matrici qui codificati.

---

## 1. Il Manifesto Teorico: I 4 Canali Visivi Ortogonali

Un'interfaccia densa per power user risulta riposante, immediata e coerente solo quando **ogni canale visivo codifica una e una sola dimensione informativa**, senza mai sovrapporsi o contraddirsi con gli altri. In NC-UI l'informazione visiva è ripartita su **4 canali ortogonali**:

| Canale Visivo | Domanda Cognitiva a cui Risponde | Meccanismo Tecnico |
| :--- | :--- | :--- |
| **1. Morfologia & Delimitatori** | *"Che cos'è questo oggetto? Posso cliccarlo, digitarci dentro, modularlo o solo leggerlo?"* | Parentesi `[...]`, calice input `▏...▕`, stepper `[-]`/`[+]`, pallini `●`/`○`, ciclico `↻`, cursore master-detail `▸`, marker espandibile `[▶ Expand]`/`[▼ Collapse]`, suffisso `..`. |
| **2. Evidenziazione di Sfondo (`*_bg`)** | *"Dove risiede in questo istante una selezione attiva, il focus tastiera o un'allerta viva?"* | Capsule Cairo retroilluminate centrate su `cap_ink_h` (`intent_bg`, `vitality_bg`, `attention_bg`, `interrupt_bg`, `fault_bg`). |
| **3. Tinta Testuale Semantica** | *"Qual è il ruolo strutturale o la conseguenza operativa di questo elemento?"* | I **7 Assi Semantici** della palette (`structure`, `schema`, `inactive`, `focus`, `intent`, `vitality`, `attention`/`interrupt`/`fault`). |
| **4. Geometria, Scala & Spazio** | *"Qual è la gerarchia tra contesto, gruppo, proprietà, valore e comando?"* | Cornici `Box` (`highlighted`), `TabBar` `1.5x`, `Divider`, colonne `add_kv` / `add_table`, margini e respiri verticali. |

> **Principio Cardine di Non-Collisione:**
> - Le parentesi **`[...]`** dichiarano l'**interattività cliccabile** (*Affordance*).
> - Lo **sfondo pieno (`*_bg`)** dichiara la **persistenza di uno stato attivo** (*Salienza*), **MAI** la semplice cliccabilità.
> - Il **colore del testo** dichiara la **semantica**, modulata dall'archetipo dell'oggetto a cui è applicato.

---

## 2. Canale 1 — Grammatica Morfologica dei Delimitatori e dei Simboli

Ogni delimitatore e prefisso simbolico possiede un significato univoco e invariante in tutto il sistema:

### 2.1 Regola Esclusiva delle Parentesi Quadre `[...]`
1. **`[...]` = Controllo Cliccabile Discreto:** Le parentesi quadre (colorate in `structure` `#43495c` a riposo) sono il marcatore universale ed esclusivo di un elemento azionabile con click o `Invio`.
2. **Divieto Assoluto di `[...]` su Testo Non Cliccabile:** È **severamente vietato** racchiudere tra parentesi quadre tag di sola lettura, categorie, scorciatoie da tastiera, codici lingua, conteggi o note di stato (es. vietati `[TXT]`, `[UTILS]`, `[ALL RULES]`, `[no u2f_keys]`, `[Mod+Shift+E]`, `[60% ≈ 57 chars]`, `[0]`). Se non è cliccabile, non può avere `[...]`.
3. **Conteggi negli Header:** I conteggi di elementi appartenenti a un `Box` o a una sezione vanno espressi tra **parentesi tonde `(N)`** direttamente nel titolo (es. `Active Keybindings (20)`, `Clipboard (78)`), mai come `[N]` né come `Badge` colorati a destra.

### 2.2 Semantica Tassativa dei Prefissi e Suffissi Interni
| Morfologia | Costruttore `ui/kit.py` | Significato Operativo Esclusivo |
| :--- | :--- | :--- |
| **`[● Opzione]`** / **`[○ Opzione]`** | `SelectPill(...)` | **Scelta persistente** (radio, toggle `ON`/`OFF`, filtro vista, modalità). `●` = opzione attualmente attiva; `○` = opzione disponibile ma inattiva. |
| **`[↻ Valore]`** | `CyclePill(...)` | **Selettore ciclico compatto** su insieme finito chiuso (es. filtri di colonna tabellare `[↻ ALL]` $\rightarrow$ `[↻ TCP]`). Ogni click avanza all'opzione successiva. |
| **`[✎ Etichetta..]`** / **`[+ Nome..]`** | `InlineField(...)` / `ActionBtn` $\rightarrow$ Prompt | **Apertura inserimento testuale.** Il doppio punto finale **`..`** è obbligatorio e riservato **esclusivamente** ai controlli che aprono una digitazione (inline o `NcPromptBar`). I comandi immediati non usano **mai** `..`. |
| **`[Comando]`** / **`[Icona Comando]`** | `ActionBtn(...)` | **Comando imperativo stateless** (esegue un'azione immediata, avvia/ferma un servizio, apre un tool esterno o applica modifiche). **Vietato** inserire `●` o `○` in un `ActionBtn` a riposo. |
| **`▏ 🔍 > testo ▕`** | `InlineFilter(...)` | **Casella di filtro o ricerca testuale inline** a larghezza fissa stabile. |
| **`[-] [####----] [+]`** | `Slider(...)` | **Barra di modulazione continua interattiva** (azionabile con click su `[-]`/`[+]` e con rotella/frecce in hover sull'elemento). |
| **`[####--------]`** *(senza `[-]`/`[+]`)* | `ProgressBar(...)` | **Indicatore telemetrico passivo di sola lettura** (CPU, RAM, disco, batteria). L'assenza di `[-]`/`[+]` lo distingue istantaneamente da uno `Slider`. |
| **`▏ ● Scheda ▕`** *(altezza `1.5x`)* | `box.add_tabs(...)` | **Navigazione tra sottoschede (`TabBar`)** di una sezione del Control Center. |
| **`▸ Icona Nome`** *(senza `[...]`)* | `InspectorCell(...)` | **Selettore riga Master-Detail** nella prima colonna di una tabella per ispezionare l'oggetto nel pannello sottostante. |
| **`[▶ Expand]`** / **`[▼ Collapse]`** *(a destra)* | `add_collapsible` / `FoldToggle` / `FoldIconBtn` | **Rivelazione progressiva inequivocabile (Espandi/Comprimi):** collocato all'estremità destra sul bordo superiore del `Box` o della barra `add_section` (scendendo su toolbar solo se la riga sfora). Le parentesi `[...]` usano `structure`, il glifo pieno (`▶`/`▼`, ``/`` o `⊞`/`⊟` all'**`85%`** di scala — mai micro-triangoli `▸`/`▾` né icone nude prive di testo) è sempre **Giallo (`attention`)** per segnalare il mutamento di vista, l'etichetta (`Expand`/`Collapse`) resta in `schema`, zero sfondo. Non altera mai stati di sistema. |
| **`● STATO`** / **`○ STATO`** *(senza `[...]`)* | `StateBadge(...)` | **Stato passivo di sola lettura** di un demone, link o hardware. **Divieto semantico:** non usare mai il pallino vuoto `○` davanti a parole che indicano uno stato attivo/valido (vietati `○ LIVE`, `○ ACCEPT POLICY`, `○ IDLE ACTIVE`, `○ RIGHT-HAND`). `○` indica esclusivamente spento, assente o standby. |

---

## 3. Canale 2 — La Legge Aurea dell'Evidenziazione di Sfondo (`*_bg`)

L'evidenziazione di sfondo è la risorsa attentiva più potente dell'interfaccia. Per preservarne il valore segnaletico vale una regola assoluta:

> **LEGGE AUREA DELL'EVIDENZIAZIONE:**
> Uno sfondo pieno colorato (`*_bg`) **NON significa MAI "questo è un bottone"** e **NON decora MAI etichette, contatori o categorie statiche**.
> Lo sfondo pieno codifica **ESCLUSIVAMENTE la persistenza di uno stato attivo in corso**: una selezione attiva dell'utente, il focus attivo della tastiera, una modifica *staged* non salvata o una condizione operativa viva/critica del sistema.

### 3.1 Elementi che DEVONO avere Sfondo Pieno (`*_bg`)
1. **Selezione Utente & Navigazione Attiva (`intent_bg` `#002a3a`, o `attention_bg` `#332d00` se `dirty=True`):**
   - Scheda attiva in `TabBar` (altezza maggiorata **`1.5x`** con end-caps verticali `2px` in `#00f0ff`; le schede inattive usano il fondo neutro incassato `structure_bg` `#1a1c23`).
   - `SelectPill(selected=True)` (`[● Scelta]`). Le opzioni non selezionate (`selected=False`) non hanno **mai** sfondo.
   - `CyclePill(active=True)` **solo** quando esprime un filtro attivo o uno stato non-default (es. `[↻ TCP]`); sul valore neutro di riposo (`[↻ ALL]`) non ha sfondo.
   - `InspectorCell(selected=True)` nella riga attualmente ispezionata di una tabella Master-Detail.
   - Cella del giorno selezionato nel calendario (`[24]`, confinata ai soli caratteri della cella senza sbordare sullo spazio della colonna).
   - `InlineField` a riposo **unicamente** quando mostra e rappresenta il **valore custom attualmente attivo** in alternativa a una serie di `SelectPill` preset sulla stessa riga (comportandosi come preset selezionato).
2. **Focus Attivo di Input o Navigazione Tastiera:**
   - `InlineFilter` e `InlineField` **mentre detengono il focus di digitazione** (`intent_bg` `#002a3a`, oppure `fault_bg` `#36050b` in caso di errore di validazione live); quando non sono a fuoco usano il solo fondo neutro incassato `structure_bg` `#1a1c23`.
   - Bersaglio attivo del cursore di **Navigazione Spaziale 2D da tastiera** (capsula inversa `#00f0ff`).
3. **Stati Operativi Vivi, Modifiche *Staged* e Allerte Reali (`StateBadge` / `Badge` / Cattura Modale):**
   - `StateBadge(is_on=True)` (`● ENABLED`, `● ACTIVE`, `● ONLINE`, `● MEMBER` $\rightarrow$ `vitality_bg` `#002813`). Quando `is_on=False` (`○ OFF`, `○ STANDBY`), **zero sfondo**.
   - `Badge` semantici **solo** per condizioni operative reali che richiedono colpo d'occhio immediato:
     - `vitality_bg` (`#002813`): verdetto `ACCEPT` su regola firewall **abilitata**.
     - `attention_bg` (`#332d00`): `PREVIEW *` / `● UNSAVED` (modifiche in attesa di conferma), `● NOT SET`, `● NOT AUTHENTICATED`, `● UNINITIALIZED`, `MISSING: <bin>`.
     - `fault_bg` (`#36050b`): `OFFLINE`, `● TIMEOUT`, gateway assente, verdetto `DROP`/`REJECT` su regola firewall **abilitata**.
   - **Cattura Modale o Streaming Attivamente Armato:** Un controllo che intercetta temporaneamente l'input o segue un flusso live (es. `[● Capturing... Esc]` o `[● REC Stop]`) riceve sfondo (`attention_bg` o `interrupt_bg`) **esclusivamente mentre la cattura/registrazione è realmente in corso**. A riposo è un normale `ActionBtn` senza sfondo e senza `●`.

### 3.2 Elementi che NON DEVONO MAI avere Sfondo a Riposo (`bg=""`)
1. **Tutti i Pulsanti di Comando (`ActionBtn`) a riposo (`active=False` obbligatorio):**
   - Qualsiasi comando imperativo — inclusi `[✓ Apply]`, `[✓ Apply Waybar]`, `[+ Add..]`, `[+ Create..]`, `[+ Import..]`, `[📁 Browse..]`, `[Sync & Scan]`, `[Refresh]`, `[↻ Check WAN]`, `[▼ Collapse]`, `[Authorize]`, `[Inspect]`, `[Disable]`, `[Remove]`, `[x]` — **non ha MAI sfondo pieno a riposo**. La cliccabilità è già espressa da `[...]`; l'importanza e la natura dell'azione sono espresse dal colore del testo.
2. **Tutti i Metadati, Contatori, Formule, Categorie, Ambiti e Nomi App Statici:**
   - Elementi informativi di sola lettura (es. `60 FPS · 16.6 ms`, `Q:auto · R:auto`, `Scale 1x`, `L752-781/781`, `SBIN`, `UTILS`, `SYSTEM`, `TXT`, `IMG`, `PATH`, `STEP-BY-STEP`, nome dell'applicazione nelle righe di notifica) **non devono MAI usare `Badge` con sfondo**. Vanno resi come testo semplice `Txt(..., role="schema")` (senza `[...]` e senza sfondo).

### 3.3 Rendering Fisico Cairo delle Evidenziazioni (`ui/widgets.py`)
- Nessun attributo `background='...'` viene lasciato a Pango: `_extract_and_strip_bg_spans()` intercetta gli span e `_draw_bg_spans_on_layout()` dipinge in Cairo rettangoli arrotondati centrati sull'altezza maiuscole (`cap_ink_h`).
- **Invarianza geometrica:** Le evidenziazioni standard (`1.0x`) hanno altezza `min(cap_ink_h + 2px, line_h - 2px)`: non scendono mai sotto la baseline e non si toccano mai tra righe adiacenti. Le sole `TabBar` usano il moltiplicatore `1.5x` circondato da righe vuote di respiro.

---

## 4. Canale 3 — I 7 Assi Cromatici × Archetipi Oggetto (Legge del Colore Testuale)

È vietato scrivere tag `<span color='#...'>` manuali nei pannelli: tutto passa da `get_semantic_palette()` tramite i costruttori di `ui/kit.py`. Il colore testuale dipende dall'intersezione tra **Asse Semantico** e **Archetipo dell'Oggetto**:

### 4.1 Regole Tassative per Ciascuno dei 7 Assi

1. **Asse 1 — `structure` (`#43495c` Grafite Strutturale, `structure_bg` `#1a1c23`):**
   - **Uso:** Cornici di tutti i `Box` standard (`highlighted=False`), linee di `Divider` e `add_section`, righe `table_rule`, separatori verticali `│` / `|`, parentesi `[` `]` di tutti i controlli a riposo, porzione vuota `----` di `Slider` e `ProgressBar`, fondo incassato di `TabBar` inattive e campi `InlineFilter`/`InlineField` non a fuoco.

2. **Asse 2 — `schema` (`#8c9bba` Grigio Ardesia):**
   - **Uso:**
     - Chiavi descrittive in `add_kv`, header di colonna in `add_table`, titoli di `Divider` e `add_section`.
     - **Tutti i controlli collassabili (`FoldIconBtn`, `FoldToggle`, `add_collapsible`, sempre `[▶ Expand]` / `[▼ Collapse]` allineati a destra):** espandere o comprimere una sezione indica un mutamento di vista e non una scelta di configurazione: le parentesi `[...]` usano `structure` (`#43495c`), il solo glifo pieno (`▶`/`▼`, ``/``, `⊞`/`⊟` all'`85%` di scala — mai micro-triangoli `▸`/`▾` né icone nude senza testo) è colorato sempre in **Giallo `attention` (`#ffeb19`)**, l'etichetta (`Expand`/`Collapse`) e il titolo sezione restano in `schema` (`#8c9bba`) con **zero sfondo** (mai Ciano `intent`). In hover il controllo passa a `focus`.
     - Metadati secondari di sola lettura (indici riga `01.`, repository, tipi `TXT`/`IMG`/`SBIN`, metriche non selettive in header come `60 FPS · 16.6 ms`).
     - Pulsanti **`ActionBtn(tone="neutral")`**: azioni di ispezione, refresh, copia, apertura editor/TUI, navigazione secondaria, import/export generico, espansione/compressione (`[Refresh]`, `[↻]`, `[Copy]`, `[✎ Edit..]`, `[Custom..]`, `[Inspect]`, `[TUI]`, `[▶ Expand]` / `[▼ Collapse]`).

3. **Asse 3 — `inactive` (`#5f657b` Grafite Spento):**
   - **Uso:** `SelectPill(selected=False)` (`[○ Opzione]`), `StateBadge(False)` (`○ OFF`, `○ STANDBY`), placeholder di ricerca, righe/regole disabilitate, valori assenti o non applicabili (`-`, `N/D`, `unset` — **mai gialli!**), traccia di uno `Slider` quando il relativo toggle sulla stessa riga è `OFF`, pulsanti `[✓ Apply]` quando non vi è alcuna modifica *staged* da salvare, micro-suggerimenti statici a singola riga.

4. **Asse 4 — `focus` (`#ffffff` Bianco Puro) & `focus_val` (`#e2eafc` Bianco Ghiaccio):**
   - **Uso di `focus` (`#ffffff`):** Titoli dei `Box`, cornice dell'Header Box (`highlighted=True`) e degli overlay (`is_overlay=True`), identificatore primario nella prima colonna delle tabelle (nome pacchetto, interfaccia, processo, combinazione tasti `Mod+Shift+E`), testo e parentesi di qualsiasi elemento interattivo sotto puntatore mouse (`hover`).
   - **Uso di `focus_val` (`#e2eafc`):** **Tutti i valori attuali di sola lettura** in `add_kv` e `add_table` privi di connotazione di salute/errore: hostname, kernel, UID/GID, path (`/usr/bin/zsh`, `/etc/...`), IP/CIDR (`10.10.0.0/24`), fuso orario (`Europe/Rome`), orologio e formati (`{:%H:%M}`), protocolli (`TCP`, `UDP`, `CUBIC`), dimensioni (`97.20 MiB`, `245M`), durate (`40 ms`, `+45 ms`), percentuali numeriche accanto agli `Slider` (`16%`, `+0.00`).
   - **Divieto di Ridondanza Valore + Pillola:** Se una riga `add_kv` espone già lo stato tramite `SelectPill` (es. `[● ghostty] [○ kitty]` o `[○ ON] [● OFF]`), **non ripetere** lo stesso valore in testo libero a sinistra delle pillole. Mostra il valore testuale solo se è un valore custom non rappresentato da alcuna pillola preset.

5. **Asse 5 — `intent` (`#00f0ff` Ciano Elettrico, `intent_bg` `#002a3a`):**
   - **LEGGE DI PUREZZA DEL CIANO:** Il Ciano significa **esclusivamente**: *"Dove mi trovo, quale opzione ho scelto tra alternative, o quale controllo continuo/testuale sto governando"*. Dove non esiste scelta, navigazione attiva o modulazione utente, **il Ciano è tassativamente vietato**.
   - **Unici usi ammessi:**
     1. Icona attiva nella Sidebar del Control Center e scheda attiva nella `TabBar` (`1.5x`).
     2. `SelectPill(selected=True)`, `CyclePill(active=True)` non-default, giorno selezionato nel calendario, `InlineField` che rappresenta un valore custom attivo tra preset, monitor selezionato in una mappa spaziale interattiva.
     3. `InspectorCell(selected=True)` per l'elemento in ispezione Master-Detail.
     4. Porzione piena `####` degli **`Slider` interattivi** abilitati (e illuminazione dell'intero `Slider` in hover).
     5. Cornice, prefisso e cursore di `InlineFilter`, `InlineField` e `NcPromptBar` quando attivi/filtrati, cursore di focus tastiera 2D e URL cliccabili (`https://...`).
   - **Divieti assoluti:** Vietato su qualsiasi `ActionBtn` a riposo, vietato sui titoli e pulsanti `add_collapsible`/`FoldToggle` (`[▶ Expand]` / `[▼ Collapse]`), vietato su `ProgressBar` telemetriche di sola lettura, vietato su valori statici (path, IP, RSS, dimensioni file, keysym, indici riga) e vietato su `Badge` di conteggio/categoria/app.

6. **Asse 6 — `vitality` (`#00ff73` Verde Smeraldo, `vitality_bg` `#002813`):**
   - **Su Indicatori di Stato / Telemetria:** Indica salute operativa attiva (`StateBadge(True)` con `vitality_bg`, verdetto `ACCEPT` attivo con `vitality_bg`, `ProgressBar` e metriche dinamiche nella fascia nominale `< 50%` carico / `< 65°C`, nuova versione disponibile in Updates). Non colorare mai in verde dati anagrafici statici (UID, fuso orario, orologio).
   - **Su Comandi Costruttivi (`ActionBtn(tone="apply")`, SEMPRE senza sfondo):** Riservato alle azioni che **attivano, creano, montano, installano, autorizzano o salvano/applicano** (`[✓ Apply]`, `[+ Add..]`, `[+ Create..]`, `[Enable]`, `[Turn On]`, `[Up]`, `[Mount]`, `[Authorize]`, `[Update]`, `[Upgrade]`, `[Save]`, `[Sync & Scan]`, `[Run]`).

7. **Asse 7 — `attention` (`#ffeb19` Giallo), `interrupt` (`#bd5b86` $\rightarrow$ `#ff2a9d` Magenta), `fault` (`#ff213f` Rosso):**
   - **`attention` (`#ffeb19`, `attention_bg` `#332d00`):**
     - *Stato / Dati:* Modifiche *staged* non ancora salvate (`SelectPill(..., dirty=True)` e badge `PREVIEW *` con `attention_bg`), configurazioni mancanti non bloccanti (`● NOT SET`, `MISSING: <bin>`), avvisi contestuali dinamici (es. aggiornamenti/orfani pendenti, NAS smontato), metriche tiepide (`50–75%` carico, `65–80°C`), giorno odierno nel calendario.
     - *Controlli Collassabili (`FoldIconBtn` / `FoldToggle` / `add_collapsible`):* il solo glifo pieno `▶`/`▼` (`85%`) adotta `attention` davanti all'etichetta `Expand`/`Collapse` in `schema` per rendere evidente il mutamento di vista.
     - *Comandi di Cautela / Reset (`ActionBtn(tone="warn")`, senza sfondo):* Azioni reversibili di ripristino, pulizia cache, sospensione o disassociazione (`[↺ Reset]`, `[Clean Cache]`, `[Suspend]`, `[Sleep Now]`, `[TERM]`, `[Unbind]`).
   - **`interrupt` (`#bd5b86` a riposo $\rightarrow$ `#ff2a9d` in hover, `interrupt_bg` `#2b091c`):**
     - *Unificazione dei Comandi di Arresto / Disabilitazione / Rimozione (`ActionBtn(tone="danger_soft")`, senza sfondo a riposo):* **Tutti** i pulsanti che fermano un'interfaccia/servizio, disabilitano una voce attiva, chiudono un pannello o rimuovono elementi usano uniformemente `danger_soft`: `[Disable]`, `[Down]`, `[Turn Off]`, `[Remove]`, `[Delete]`, `[Clear]`, `[Wipe]`, `[KILL]`, `[Log Out]`, `[Reboot]`, `[Power Off]`, `[x]`. Su telemetria dinamica copre la fascia pre-critica (`75–90%` carico).
   - **`fault` (`#ff213f`, `fault_bg` `#36050b`):**
     - *Errore Critico / Sicurezza:* `OFFLINE`, `● TIMEOUT`, assenza di default gateway, verdetto `DROP`/`REJECT` attivo (coerente in ogni vista: `DROP` non può mai essere verde), validazione input fallita (`fault_bg`), metriche in emergenza (`>= 90%` carico/disco, `>= 85°C`, `< 15%` carica UPS).

### 4.2 Eccezione Controllata: Campioni Colore Letterali (*Color Swatches*)
L'unico caso in tutta l'interfaccia in cui è consentito dipingere un glifo con un codice esadecimale letterale non mediato dai ruoli semantici è l'anteprima esplicita di un colore configurabile dall'utente (es. quadratino `■ #43495c` nella tabella della palette in `cc_personalization_theme`, `Base Surface Color` o `Shader Color`) e il box dimostrativo di calibrazione del tema (*Theme Sandbox Preview*).

### 4.3 Matrice Sinottica `Archetipo × Stato × Morfologia × Colore × Sfondo`

| Archetipo Oggetto | Stato | Morfologia | Colore Testo | Sfondo (`*_bg`) |
| :--- | :--- | :--- | :--- | :--- |
| **Sidebar Icon** | Attiva / Inattiva | `Icona` | `intent` / `schema` | Nessuno |
| **Subtab (`TabBar`)** | Attiva / Inattiva | `▏ ● Nome ▕` / `▏ ○ Nome ▕` | `intent` / `schema` | `intent_bg` (`1.5x`) / `structure_bg` |
| **`SelectPill`** | Scelta / Staged / Inattiva | `[● Voce]` / `[● Voce]` / `[○ Voce]` | `intent` / `attention` / `inactive` | `intent_bg` / `attention_bg` / **Nessuno** |
| **`CyclePill`** | Filtro attivo / Default | `[↻ TCP]` / `[↻ ALL]` | `intent` / `schema` | `intent_bg` / **Nessuno** |
| **`InspectorCell`** | Ispezionata / Non sel. | `▸ Nome` / `  Nome` | `intent` / `focus` | `intent_bg` / **Nessuno** |
| **`ActionBtn` (Neutro)** | Riposo / Hover | `[Az.]` o `[✎ Az..]` | `schema` / `focus` | **MAI a riposo** |
| **`ActionBtn` (Costruttivo)** | Attivo / Nulla da salvare | `[✓ Apply]`, `[+ Add..]` | `vitality` / `inactive` | **MAI a riposo** |
| **`ActionBtn` (Cautela)** | Riposo / Hover | `[↺ Reset]`, `[Clean]` | `attention` / `focus` | **MAI a riposo** |
| **`ActionBtn` (Stop/Remove)** | Riposo / Hover | `[Disable]`, `[Remove]`, `[x]` | `interrupt_rest` / `interrupt` | **MAI a riposo** |
| **Cattura Modale** | A riposo / Armata live | `[Capture]` / `[● Capturing..]` | `schema` / `attention` o `interrupt` | **Nessuno** / `attention_bg` o `interrupt_bg` |
| **`InlineFilter` / `Field`** | Vuoto / Con filtro / Focus / Err | `▏ 🔍 > ... ▕` | `inactive` / `focus_val` / `focus` / `fault` | `structure_bg` / `structure_bg` / `intent_bg` / `fault_bg` |
| **`Slider` Interattivo** | Abilitato / Toggle OFF | `[-] [####----] [+]` | `intent` (`#`) / `inactive` | **Nessuno** *(hover illumina l'elemento)* |
| **`ProgressBar` Passiva** | `<50%` / `<75%` / `<90%` / `>=90%` | `[####--------]` | `vitality` / `attention` / `interrupt` / `fault` | **Nessuno** |
| **`StateBadge`** | ON sano / OFF o Standby | `● ACTIVE` / `○ OFF` | `vitality` / `inactive` | `vitality_bg` / **Nessuno** |
| **`Badge` Allerta/Fault** | Warn / Critico | `PREVIEW *` / `OFFLINE` | `attention` / `fault` | `attention_bg` / `fault_bg` |
| **Metadato / Contatore** | Sola lettura | `alpha=0.85`, `TXT` *(zero `[]`)* | `schema` | **MAI** |
| **Valore Config/Tabella** | Sola lettura | Testo puro *(zero `[]`)* | `focus_val` (o `focus` in col. 1) | **MAI** |
| **`add_collapsible` / `FoldToggle`** | Chiusa / Espansa | `[▶ Expand]` / `[▼ Collapse]` *(dx, glifo `85%`)* | `attention` (`▶`/`▼`) + `schema` (`Expand`/`Collapse`) | **MAI** |

---

## 5. Canale 4 — Tipografia, Cornici, Opacità e Spaziature (`Canvas` & `Box`)

### 5.1 Griglia Tipografica Monospace & Glifi (`ui/canvas.py`)
1. **Disattivazione Ligature OpenType:** Tutte le superfici Pango/CSS forzano `liga=0, clig=0, dlig=0, hlig=0` per impedire che sequenze come `->`, `!=` o `--` alterino la larghezza di cella.
2. **Scaling Automatico Glifi Nerd Font / PUA (`62%`):** `Canvas.sanitize_markup()` ridimensiona automaticamente al `62%` (`fallback_glyph_pt`) le icone PUA e i simboli geometrici fuori metrica per evitare che allarghino la cella o spacchino l'altezza riga costante.
3. **Allineamento Sub-Pixel Senza Deriva Hinting:** Le righe tabellari usano solo spazi interi (`_int_spaces_for_px`); `_micro_space` è confinato alla chiusura del trattino finale `─` dei bordi superiore/intermedio/inferiore dei `Box` per combaciare al pixel con `total_px`, mentre `AsciiCard._on_draw_box_bg()` traccia in Cairo i montanti verticali continui.

### 5.2 Gerarchia delle Cornici (`highlighted`) e Opacità a Doppio Strato
1. **Control Center (`mode="settings"`):**
   - **Unicamente** l'Header Box fisso in alto (titolo sezione, lingua, ricerca globale, `TabBar`) e gli overlay flottanti (`is_overlay=True`) usano `highlighted=True` (cornice Bianca `#ffffff`).
   - **Tutti** i `Box` di contenuto sottostanti devono avere `highlighted=False` (cornice Grafite `#43495c`).
2. **Popup Waybar (`mode="popup"`):**
   - Nei popup a singolo `Box` o composti da sezioni paritetiche (es. `wp_network`, `wp_audio`, `wp_calendar`, `wp_display`, `wp_kb_layout`, `wp_ups`, `wp_session`, `wp_capture`, `wp_printers`), **tutti** i `Box` usano `highlighted=False` per evitare un doppio bordo bianco abbagliante a ridosso della cornice esterna della finestra.
   - `highlighted=True` è ammesso nei popup **solo** su un Header Box di filtro/riepilogo che governa una lista sottostante di card figlie (`wp_clipboard`, `wp_notifications`, `wp_system`).
3. **Hover sulla Cornice del `Box`:** Il cambio di bordo in bianco al passaggio del mouse (`hover_enable=True`) è attivo **solo** sulle card interamente cliccabili (`on_row_click`, es. singola voce clipboard) o dotate di `scroll_action` di viewport; sui normali `Box` contenitori è disabilitato (`hover_enable=False`).
4. **Doppio Livello di Opacità Disaccoppiato (`core/config.py`):**
   - `Window Opacity` governa il fondale della finestra; `NC-Box Opacity` e `Base Surface Color` governano il riempimento Cairo interno agli `nc-box` (incluse le protrusioni sul titolo e sulle azioni del bordo superiore); gli overlay flottanti (`is_overlay=True`) forzano opacità `>= 0.97` con ombra portata.
   - La modifica live di opacità e colore aggiorna istantaneamente NC-UI ma **non riavvia Waybar** (`sync_waybar=False` di default): la sincronizzazione con Waybar avviene solo su click esplicito (`[✓ Apply Waybar]` / `[Apply Bar]`) per evitare flicker della barra durante la calibrazione.

### 5.3 Spaziature Interne ai `Box`, tra Righe e tra `Box`
1. **Margine Sinistro Unificato (`2 spazi`) e Respiro Verticale (`1 riga vuota`):**
   - Ogni contenuto interno (`add_kv`, `add_table`, `add_text_block`, `add_row`) inizia a `2 spazi` dal bordo sinistro `│`.
   - Ogni `Box` (sia nel Control Center sia nei popup Waybar) mantiene **1 riga vuota di respiro** subito sotto il bordo superiore e subito sopra il bordo inferiore (salvo quando contiene una `TabBar` che gestisce già il proprio padding simmetrico).
   - **Divieto di `add_section()` come prima operazione di un `Box`:** Il titolo sul bordo superiore del `Box` denomina già il primo blocco; inserire una linea `── Sezione ──` attaccata subito sotto il titolo superiore crea una doppia riga collassata ed è vietato.
2. **Partizione *All-or-Nothing* delle Azioni sul Bordo Superiore:**
   - Sul bordo superiore di un `Box` risiedono a sinistra `[ Icona  Titolo (N) ]` e a destra eventuali `StateBadge` + `ActionBtn` essenziali. Se lo spazio orizzontale non basta a contenere tutti gli `ActionBtn`, tutti i pulsanti scendono insieme in una riga toolbar dedicata, lasciando sul bordo i soli indicatori non cliccabili.
3. **Spaziatura tra `Box` (`card_spacing`) e Uso Corretto di `Divider`:**
   - Tutti i `Box` e `Divider` di una vista sincronizzano la larghezza tramite `sync_boxes_width()` e sono separati verticalmente da `card_spacing`.
   - Qualsiasi widget grafico Cairo custom (`CustomWidgetCard`, mappe monitor, anteprime shader/wallpaper, emblemi `set_side_emblem`) deve vivere incorniciato dentro un `Box` della stessa larghezza esatta di `content_canvas.total_px` (mai fluttuante nel vuoto).
   - **Legge di `Divider` (Zero Doppi Titoli):** `Divider(t("..."), icon="...")` (allineato a destra per bilanciare i titoli dei `Box` a sinistra) serve **unicamente** a separare macro-capitoli composti da più `Box` o cambi di dominio netto. È **vietato** anteporre un `Divider` al singolo `Box` ripetendone lo stesso titolo o concetto; nei popup compatti (`<= 60` colonne) i `Divider` sopra i `Box` sono sempre omessi.
4. **Centratura e Bilanciamento delle `TabBar`:**
   - Le sottoschede sono sempre centrate tramite `box.add_tabs(...)`. Quando superano la larghezza di una riga, si spezzano in righe numericamente bilanciate (es. `3 + 2` per 5 schede, mai `4 + 1`) separate da una riga vuota.

---

## 6. Allineamento Verticale, Tabelle, Master-Detail e Anti-Affollamento

1. **Allineamento Chiave-Valore (`add_kv`):**
   - Usare sempre `box.add_kv(t("..."), val_markup, right_markup=..., requires=[...])` lasciando `key_width=None` (usa `DEFAULT_KEY_WIDTH = 20`, o `COMPACT_KEY_WIDTH = 14` nei popup stretti, espandendosi automaticamente sulla chiave più lunga dello stesso `Box`).
2. **Obbligo di `add_table` per Elenchi Multi-Campo:**
   - Qualsiasi elenco con 2 o più elementi per riga deve usare `box.add_table(headers=[...], widths=[...], rows=[...], aligns=[...], show_headers=True/False)`. È **vietato** incolonnare campi con spazi manuali dentro `add_row()`.
   - `Canvas.format_table_row()` protegge i controlli atomici (`_ATOMIC_WIDGET_RE`) dal troncamento, calcola lo spanning automatico sulle celle vuote adiacenti e uniforma l'altezza di tutte le righe (`uniform_row_height=True`).
3. **Uniformità e Misura Stretta di `min_chars` in Colonna:**
   - Pulsanti o badge allineati nella stessa colonna usano lo stesso `min_chars` (e uno slot vuoto di pari larghezza quando un pulsante secondario è omesso su una riga), ma **`min_chars` deve corrispondere esattamente alla lunghezza massima reale tra le etichette di quello slot** (es. `max(len("Enable"), len("Disable")) = 7`). È **vietato** passare `min_chars` sovradimensionati (es. `24` o `30`) che generano parentesi cave `[       Testo       ]`: la larghezza della colonna si governa con `widths` e `aligns` di `add_table`.
4. **Pattern Universale Master-Detail (`InspectorCell`):**
   - Quando una tabella permette di selezionare una riga per ispezionarne/modificarne i dettagli in un `Box` sottostante, la selezione avviene **sempre** rendendo la prima colonna con `InspectorCell(nome, uri, selected=is_sel, icon=...)` (`▸ Nome` su `intent_bg`), senza aggiungere pulsanti `[Inspect]` ridondanti nella colonna `ACTIONS`.
5. **Anti-Affollamento, Dipendenze e Flusso *Staged*:**
   - **Massimo 5–6 righe `add_kv` visibili per singolo `Box`:** oltre tale soglia, suddividere in `Box` distinti o usare `box.add_collapsible(...)`.
   - **Zero Muri di Testo:** Vietati blocchi informativi multi-riga nei form; i warning (`Txt(..., role="warn")`) compaiono **solo dinamicamente** quando la configurazione attiva comporta effettivamente quel rischio o blocco.
   - **Flusso *Staged*:** Quando le modifiche non sono applicate in tempo reale ma preparate in un buffer, lo stato in header/footer mostra `PREVIEW *` (`attention` + `attention_bg`), le pillole alterate passano `dirty=True`, e in fondo al `Box` compare una sezione di conferma con `[↺ Reset]` (`tone="warn"`) e `[✓ Apply]` (`tone="apply"` se `is_dirty`, `inactive` se già sincronizzato — sempre senza sfondo).

---

## 7. Standardizzazione dell'Input Testuale, Ricerca e Completamento

Tutti gli inserimenti di testo in NC-UI sono ricondotti a **5 archetipi deterministici** (`ui/kit.py`, `ui/widgets.py`, `core/completion.py`), senza soluzioni ibride o ambigue:

| Archetipo | Quando Usarlo | Posizione & Comportamento |
| :--- | :--- | :--- |
| **1. `CyclePill`** | Scelta rapida fra `<= 6` opzioni discrete in spazi stretti (celle filtro tabella). | In-place `[↻ Valore]`, click ciclico immediato, zero digitazione. |
| **2. `InlineFilter`** | Filtrare live una tabella, griglia o lista visibile subito sotto. | In-place `▏ 🔍 > ... ▕` accanto/sopra la lista filtrata + pulsante `[x]` di reset quando valorizzato. |
| **3. `InlinePicker`** (`add_search_box`) | Cercare e aggiungere/selezionare un elemento da un catalogo finito indicizzabile (es. timezone, layout XKB, app). | In-place `InlineFilter` + righe di pillole candidate sottostanti navigabili con `Tab` / `↑` / `↓` e confermabili con `Invio` o click. |
| **4. `InlineField`** | Inserire o modificare un valore scalare breve (IP/CIDR, porta, metrica, nome profilo/bridge, step custom, esadecimale `#Hex`). | Si presenta a riposo come `[✎ Val..]` o `[+ Nome..]`; al click si espande *in-place* in un campo editabile con validazione live (`fault_bg` su formato invalido). |
| **5. `NcPromptBar`** | **Riservata esclusivamente** a: (a) password/segreti (con toggle visibilità), (b) path filesystem, URL o comandi shell lunghi, (c) flussi wizard multi-step. | Barra inferiore dedicata con cornice `intent` (`#00f0ff`), badge `[Tab ↹]` quando è attivo un catalogo/completamento path (`step_tab_completion`) e `[x]` di chiusura. |

### 7.1 Leggi Universali di Digitazione e Ricerca Globale
1. **Conferma Universale Esclusiva su `Invio` (`Enter`):** Qualsiasi inserimento testuale (`InlineFilter`, `InlinePicker`, `InlineField`, `NcPromptBar`) conferma l'azione **esclusivamente** alla pressione di `Invio` e annulla con `Esc`. È vietato qualsiasi auto-commit basato sul raggiungimento del numero massimo di caratteri.
2. **Autocompletamento Deterministico (`core/completion.py`):** Ogni qualvolta il dominio di input è finito o esplorabile (path su disco, interfacce, timezone, layout XKB, app `.desktop`, porte/protocolli), il campo deve agganciare un catalogo di completamento ciclabile con `Tab` / `Shift+Tab`.
3. **Ricerca Globale Flottante (`Ctrl+F`):** La scorciatoia `Ctrl+F` apre in qualunque momento (dal Control Center o promuovendo un popup Waybar) la ricerca globale nella barra di intestazione, proiettando sopra il pannello un `Gtk.Overlay` (`is_overlay=True`) ancorato alla casella di ricerca con i risultati in tempo reale estratti dai dizionari localizzati `locales/*.toml`, navigabili con `↑`/`↓`/`Tab` e `Invio`.

---

## 8. Paradigma di Modulazione Element-Hover e Navigazione Spaziale 2D

1. **Aggancio Esclusivo al Singolo Elemento in Hover (`ui/widgets.py` & `ui/shell.py`):**
   - È **vietato** agganciare azioni di modulazione (rotella del mouse o frecce `←`/`→`) all'intero contenitore `Box`, poiché un singolo `Box` può ospitare più `Slider` o controlli.
   - L'hit-testing Pango risolve in tempo reale al pixel il singolo controllo sotto il puntatore (incluso l'intero span continuo `[-] [####----] [+]` di ogni `Slider` intero o float registrato in `_SLIDER_META`).
   - **Comportamento deterministico:** Quando il puntatore è in hover su uno `Slider`, l'intero `Slider` si illumina e sia la **rotella del mouse** sia le **frecce `←`/`→`** modulano unicamente quello `Slider`. Non appena il puntatore esce dallo `Slider` (pur restando dentro lo stesso `Box`), la rotella torna immediatamente a scorrere verticalmente la pagina.
2. **Rate-Limiting e Igiene degli Eventi:**
   - Tutte le modulazioni a step (rotella mouse, touchpad `SMOOTH` e ripetizione tasti) passano attraverso `allow_step_input()` (intervallo minimo `95ms` su flusso continuo, soppressione dei duplicati `SMOOTH` post-click discreto) e tutti i `Gtk.Label` interattivi disabilitano il menu contestuale nativo GTK sui link (`_disable_label_context_menu`).
3. **Mappa Standard di Navigazione Tastiera 2D:**
   - `Frecce` / `Tab`: navigazione spaziale 2D tra i controlli interattivi visibili; `Invio` / `Spazio` attiva il controllo focalizzato (o modula con `←`/`→` uno `Slider` focalizzato quando il mouse è fermo).
   - `Shift + ←/→`: cicla tra le `TabBar` della sezione corrente nel Control Center, oppure esegue *Bar Surfing* tra i popup dei moduli Waybar.
   - `Shift + ↑/↓` (`PgUp`/`PgDn`): cambia sezione nella Sidebar del Control Center, oppure espande/comprime un popup Waybar che lo supporta.
   - `Ctrl + F`: apre e focalizza la Ricerca Globale del Control Center.
   - `Esc`: annulla nell'ordine un input inline/prompt attivo, chiude l'overlay di ricerca globale, oppure chiude la finestra NC-UI.
