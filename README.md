# NC-UI: Contesto Architetturale e Linee Guida per lo Sviluppo

## 1. Natura del Progetto & Stack Tecnologico
   - **Cos'è:** NC-UI (`nc-ui`) è un framework TUI/GUI ibrido ad alte prestazioni per Wayland, integrato come overlay nativo (tramite `GtkLayerShell`) e sintonizzato con Waybar.
   - **Linguaggio e Librerie:** Python 3, GTK3 (`gi.repository`), Pango per il markup testuale avanzato.
   - **Ambiente:** Arch Linux, workstation headless/Wayland (Niri compositor), terminal-first workflow.

## 2. Principi Architetturali Fondamentali (DA RISPETTARE RIGOROSAMENTE)
a. **Asincronicità e Non-Blocking I/O:** 
   - Il Main Thread grafico (che esegue il loop GTK) **non deve mai** blocchi o chiamate di I/O sincrone pesanti (`subprocess.run`, D-Bus bloccanti, richieste di rete) durante il rendering o i cicli di refresh.
   - Usare pattern *State-First* con cache in memoria e worker thread in background (`threading.Lock`, `GLib.idle_add`) per raccogliere le telemetrie di sistema.
b. **Semantica Cromatica e Stati Ortogonali dei Box (`Box`):**
   - *Elezione* (`elected=True`):** Stato persistente di dominio (es. periferica audio predefinita). Verde fisso (`box_elected`).
   - *Evidenziazione* (`highlighted=True`):** Rango strutturale / Hero card di telemetria aggregata. Ambra tenue (`box_highlight`).
   - *Selezione* (`selected=True`):** Puntamento attentivo transitorio gestito dal mouse in hover.
   - *Nota*: La selezione sui box si attiva solo se nel pannello ci sono `> 1` elementi selezionabili (Regola del Conteggio). Il `text-shadow` dei box non deve mai applicarsi all'intero blocco di testo interno, ma solo ai link attivi (`link:hover`).
c. **Robustezza degli Script e delle Eccezioni:**
   - Ogni script o modulo deve anticipare e gestire eccezioni, timeout di sottoprocessi e interruzioni inaspettate.
   - Nelle interazioni di testo (es. prompt bar), sanitizzare ed effettuare sempre l'escape rigoroso (`Canvas.escape()`) di qualsiasi input o stringa grezza per evitare crash del parser XML/Pango.

## 3. Regole Operative per il Codice
   - **Zero Dipendenze Esterne Superflue:** Privilegiare librerie standard Python (es. `sqlite3` per la persistenza, parser ICS custom) rispetto a pacchetti esterni fragili.
   - **Pulizia dei Warning:** Evitare deprecation warning di PyGObject (es. usare `GLibUnix.signal_add` dove appropriato) e mantenere la compilazione pulita.

## 4. Cosa vuole essere il progetto
   - NC-UI vuole essere una interfaccia grafica prestante, ordinata, leggera e configurabile basata su ASCII. Grazie al mantenimento ordinato di classi e l'applicazione di standard trasversalmente al codice e ai pannelli l'interfaccia mantiene un'anima coerente e piacevole. 
   - NC-UI vuole essere facilmente editabile e ampliabile. Definire standard, curare i file di configurazione, facilitare la creazione di elementi simili a quelli esistenti e integrarle nuove sezioni facendo sforzi minimi è un atto di rispetto verso chi userà il progetto in futuro e vi metterà mano.
   - NC-UI vuole essere sicuro e robusto. Presentare il minor numero di punti di rottura possibili, evitando dipendenze di pacchetti sconosciuti o poco supportati.

