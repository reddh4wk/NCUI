#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# NC-UI Aider Pipeline (GNOME .blp/.ui -> Spec -> Architect/Diff Edit -> nc-shot)
# ==============================================================================

readonly SCRIPT_NAME="$(basename "$0")"
readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly SPEC_DIR="/tmp/aider_specs"
readonly SHOT_DIR="/tmp/nc_aider_review"

# Pastel ANSI palette
readonly C_RESET=$'\033[0m'
readonly C_BOLD=$'\033[1m'
readonly C_CYAN=$'\033[38;5;117m'
readonly C_MINT=$'\033[38;5;114m'
readonly C_WARN=$'\033[38;5;222m'
readonly C_ERR=$'\033[38;5;210m'
readonly C_DIM=$'\033[38;5;245m'

# Default Aider model triad (override via env vars if needed)
readonly AIDER_MAIN_MODEL="${AIDER_MAIN_MODEL:-gemini/gemini-4-argon}"
readonly AIDER_EDITOR_MODEL="${AIDER_EDITOR_MODEL:-gemini/gemini-4-hydrogen}"
readonly AIDER_WEAK_MODEL="${AIDER_WEAK_MODEL:-gemini/gemini-3.5-flash-lite}"
readonly AIDER_THINK_TOKENS="${AIDER_THINK_TOKENS:-8192}"
readonly AIDER_REASONING="${AIDER_REASONING:-high}"
readonly AIDER_MAP_TOKENS="${AIDER_MAP_TOKENS:-512}"

# CLI defaults
TARGET=""
PHASE="all"
AUTO_YES=0
DRY_RUN=0
CURRENT_TMP=""

# Target registry:
#   key -> "gnome_subdir|section|subtabs_csv|py_files_csv|extra_read_csv"
declare -A TARGETS=(
  ["display"]="display|display|monitors,scaling|panels/cc_engine_display.py,panels/cc_display_monitors.py,panels/cc_display_scaling.py|utils/nc_util_desktop.sh"
  ["sound"]="sound|sound|devices,output,mixer|panels/cc_engine_sound.py,panels/cc_sound_devices.py,panels/cc_sound_output.py,panels/cc_sound_input.py,panels/cc_sound_mixer.py|"
  ["network"]="network|network|interfaces,vpn_proxy,wan_routing|panels/cc_engine_network.py,panels/cc_network_interfaces.py,panels/cc_network_vpn_proxy.py,panels/cc_network_wan_routing.py|"
  ["bluetooth"]="bluetooth|network|wifi_bt|panels/cc_engine_network.py,panels/cc_network_wifi_bt.py|"
  ["keyboard"]="keyboard|input|keyboard,shortcuts|panels/cc_engine_input.py,panels/cc_input_keyboard.py,panels/cc_input_shortcuts.py|"
  ["mouse"]="mouse|input|mouse|panels/cc_engine_input.py,panels/cc_input_mouse.py|"
  ["power"]="power|power|profiles,idle,ups|panels/cc_engine_power.py,panels/cc_power_profiles.py,panels/cc_power_idle.py,panels/cc_power_ups.py|"
  ["printers"]="printers|printers|devices,discovery,queue,server|panels/cc_engine_printers.py,panels/cc_printers_devices.py,panels/cc_printers_discovery.py,panels/cc_printers_queue.py,panels/cc_printers_server.py|"
  ["apps"]="applications|apps|autostart,floating,mime|panels/cc_engine_apps.py,panels/cc_apps_autostart.py,panels/cc_apps_floating.py,panels/cc_apps_mime.py|"
  ["notifications"]="notifications|settings|notifications|panels/cc_engine_settings.py,panels/cc_settings_notifications.py|"
  ["datetime"]="system/datetime|datetime|time,calendar|panels/cc_engine_datetime.py,panels/cc_datetime_time.py,panels/cc_datetime_calendar.py|"
)

readonly TARGET_ORDER=(
  display sound network bluetooth keyboard mouse power printers apps notifications datetime
)

# ------------------------------------------------------------------------------
# Logging & Cleanup
# ------------------------------------------------------------------------------
log_info() { printf "%s[*]%s %s\n" "$C_CYAN" "$C_RESET" "$*"; }
log_ok()   { printf "%s[✓]%s %s\n" "$C_MINT" "$C_RESET" "$*"; }
log_warn() { printf "%s[!]%s %s\n" "$C_WARN" "$C_RESET" "$*" >&2; }
log_err()  { printf "%s[✗]%s %s\n" "$C_ERR"  "$C_RESET" "$*" >&2; }

cleanup() {
  if [[ -n "$CURRENT_TMP" && -f "$CURRENT_TMP" ]]; then
    rm -f "$CURRENT_TMP"
  fi
}

on_interrupt() {
  echo ""
  cleanup
  log_warn "Esecuzione interrotta dall'utente. File temporanei rimossi."
  exit 130
}

trap cleanup EXIT
trap on_interrupt INT TERM

# ------------------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------------------
show_help() {
  cat <<EOF
${C_BOLD}${C_CYAN}${SCRIPT_NAME}${C_RESET} — Pipeline Aider a 3 fasi (GNOME .blp/.ui -> Spec -> Edit -> nc-shot)

${C_BOLD}USO:${C_RESET}
  ./.agents/scripts/${SCRIPT_NAME} -t <target|all> [opzioni]
  ./.agents/scripts/${SCRIPT_NAME} -l

${C_BOLD}OPZIONI:${C_RESET}
  ${C_MINT}-t, --target <nome>${C_RESET}   Target da processare (usa -l per la lista, oppure 'all')
  ${C_MINT}-p, --phase <fase>${C_RESET}    Fase da eseguire: ${C_CYAN}a${C_RESET} (solo spec), ${C_CYAN}b${C_RESET} (solo edit),
                        ${C_CYAN}c${C_RESET} (solo nc-shot), ${C_CYAN}all${C_RESET} (default: all)
  ${C_MINT}-y, --yes${C_RESET}             Modalità non interattiva (salta la pausa tra Fase A e Fase B)
  ${C_MINT}-l, --list${C_RESET}            Elenca tutti i target mappati e i relativi file
  ${C_MINT}-d, --dry-run${C_RESET}         Mostra file e comandi senza invocare aider o nc-shot
  ${C_MINT}-h, --help${C_RESET}            Mostra questo aiuto

${C_BOLD}ESEMPI:${C_RESET}
  ${C_DIM}# Ciclo completo interattivo sul pannello display${C_RESET}
  ./.agents/scripts/${SCRIPT_NAME} -t display

  ${C_DIM}# Genera solo la specifica (Fase A) per sound senza toccare codice${C_RESET}
  ./.agents/scripts/${SCRIPT_NAME} -t sound -p a

  ${C_DIM}# Applica una specifica già revisionata in /tmp/aider_specs/display.md${C_RESET}
  ./.agents/scripts/${SCRIPT_NAME} -t display -p b

  ${C_DIM}# Dry-run su tutti i target mappati${C_RESET}
  ./.agents/scripts/${SCRIPT_NAME} -t all --dry-run
EOF
}

list_targets() {
  printf "%s%-15s %-18s %-24s %s%s\n" "$C_BOLD$C_CYAN" "TARGET" "GNOME DIR" "SUBTABS" "PANELS" "$C_RESET"
  printf "%s%s%s\n" "$C_DIM" "--------------------------------------------------------------------------------" "$C_RESET"
  for key in "${TARGET_ORDER[@]}"; do
    IFS='|' read -r gdir _ subtabs pyfiles _ <<< "${TARGETS[$key]}"
    printf "%s%-15s%s %-18s %-24s %s%s%s\n" "$C_MINT" "$key" "$C_RESET" "$gdir" "$subtabs" "$C_DIM" "$pyfiles" "$C_RESET"
  done
}

# ------------------------------------------------------------------------------
# Pre-flight Checks
# ------------------------------------------------------------------------------
preflight() {
  cd "$ROOT_DIR"

  for bin in aider python3 git; do
    if ! command -v "$bin" >/dev/null 2>&1; then
      log_err "Dipendenza mancante nel PATH: ${bin}"
      exit 1
    fi
  done

  if [[ ! -d "panels" || ! -d ".gnome-panels" ]]; then
    log_err "Root progetto non valida (${ROOT_DIR}): mancano panels/ o .gnome-panels/."
    exit 1
  fi

  if [[ "$PHASE" == "b" || "$PHASE" == "all" ]]; then
    local branch
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
    if [[ "$branch" == "main" || "$branch" == "master" ]]; then
      log_warn "Sei sul branch '${branch}'. Consigliato creare un branch dedicato (es. git checkout -b feat/aider-gnome-pass)."
    fi
    if [[ -n "$(git status --porcelain -- panels/ locales/)" ]]; then
      log_warn "Working tree non pulito su panels/ o locales/. I diff di Aider potrebbero mescolarsi a modifiche non committate."
    fi
  fi

  mkdir -p "$SPEC_DIR" "$SHOT_DIR"
}

# ------------------------------------------------------------------------------
# Execution per Target
# ------------------------------------------------------------------------------
run_target() {
  local key="$1"
  local entry="${TARGETS[$key]}"
  IFS='|' read -r gdir section subtabs_csv pyfiles_csv extra_read_csv <<< "$entry"

  local gnome_path=".gnome-panels/${gdir}"
  if [[ ! -d "$gnome_path" ]]; then
    if [[ "$gdir" == system/* && -d ".gnome-panels/system" ]]; then
      gnome_path=".gnome-panels/system"
    else
      log_err "[${key}] Directory GNOME non trovata: ${gnome_path}"
      return 1
    fi
  fi

  local spec_file="${SPEC_DIR}/${key}.md"
  local spec_tmp="${spec_file}.tmp"

  IFS=',' read -r -a py_files <<< "$pyfiles_csv"
  IFS=',' read -r -a subtabs <<< "$subtabs_csv"
  local extra_reads=()
  if [[ -n "$extra_read_csv" ]]; then
    IFS=',' read -r -a extra_reads <<< "$extra_read_csv"
  fi

  local valid_py=()
  for f in "${py_files[@]}"; do
    if [[ -f "$f" ]]; then
      valid_py+=("$f")
    else
      log_warn "[${key}] File non trovato (saltato): ${f}"
    fi
  done
  if [[ ${#valid_py[@]} -eq 0 ]]; then
    log_err "[${key}] Nessun file Python valido trovato."
    return 1
  fi

  local aider_base=(
    aider
    --model "$AIDER_MAIN_MODEL"
    --architect
    --editor-model "$AIDER_EDITOR_MODEL"
    --weak-model "$AIDER_WEAK_MODEL"
    --thinking-tokens "$AIDER_THINK_TOKENS"
    --reasoning-effort "$AIDER_REASONING"
    --dark-mode
    --no-check-model-accepts-settings
    --map-tokens "$AIDER_MAP_TOKENS"
  )

  printf "\n%s======================================================================%s\n" "$C_DIM" "$C_RESET"
  log_info "${C_BOLD}Target: ${key}${C_RESET} (GNOME: ${gnome_path} | Section: ${section})"

  # --------------------------------------------------------------------------
  # FASE A: Analisi Read-Only (.blp / .ui -> Spec)
  # --------------------------------------------------------------------------
  if [[ "$PHASE" == "a" || "$PHASE" == "all" ]]; then
    mapfile -t ui_files < <(find "$gnome_path" -type f \( -name '*.blp' -o -name '*.ui' \) | sort)
    if [[ ${#ui_files[@]} -eq 0 ]]; then
      log_warn "[${key}] Nessun file .blp/.ui trovato in ${gnome_path}, passo ai .c principali."
      mapfile -t ui_files < <(find "$gnome_path" -maxdepth 2 -type f -name '*.c' | sort | head -n 6)
    fi

    local read_args=()
    for f in "${ui_files[@]}"; do read_args+=(--read "$f"); done
    for f in "${valid_py[@]}"; do read_args+=(--read "$f"); done
    for f in "${extra_reads[@]}"; do
      [[ -f "$f" ]] && read_args+=(--read "$f")
    done

    local prompt_a="Confronta i file .blp/.ui di GNOME in ${gnome_path} con i file NC-UI (${valid_py[*]}). Ignora GTK4, libadwaita, GObject, gsettings e mutter. Produci una specifica compatta ma esaustiva (max 100 righe), solo bullet point ad alta densità coadiuvati da riferimenti reali (file:riga) per provare ogni punto. Evita di citare interi blocchi di codice. Focus: 1) miglioramenti di gerarchia UX e violazioni di ncui-golden_standard.md, 2) edge-case, guardie di sicurezza e validazioni mancanti, 3) traduzione nativa Arch/Niri con ui/kit.py e core/probe.py."

    log_info "[FASE A] Analisi read-only (${#ui_files[@]} file GNOME, ${#valid_py[@]} file NC-UI) -> ${spec_file}"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf "%sDRY-RUN FASE A:%s %s --chat-mode ask --yes-always --no-auto-commits %s\n" \
        "$C_DIM" "$C_RESET" "${aider_base[*]}" "${read_args[*]}"
    else
      CURRENT_TMP="$spec_tmp"
      "${aider_base[@]}" \
        --chat-mode ask \
        --yes-always \
        --no-auto-commits \
        "${read_args[@]}" \
        --message "$prompt_a" \
        --no-stream < /dev/null | tee "$spec_tmp"

      if [[ ! -s "$spec_tmp" ]]; then
        rm -f "$spec_tmp"
        CURRENT_TMP=""
        log_err "[${key}] Fase A ha prodotto una specifica vuota."
        return 1
      fi
      mv "$spec_tmp" "$spec_file"
      CURRENT_TMP=""
      log_ok "Specifica salvata in ${spec_file}"
    fi
  fi

  # Pausa di revisione tra Fase A e Fase B
  if [[ "$PHASE" == "all" && "$AUTO_YES" -eq 0 && "$DRY_RUN" -eq 0 ]]; then
    printf "%s[?]%s Revisiona %s%s%s. Premi %sINVIO%s per avviare Fase B, oppure %ss%s per saltare questo target: " \
      "$C_WARN" "$C_RESET" "$C_CYAN" "$spec_file" "$C_RESET" "$C_BOLD" "$C_RESET" "$C_BOLD" "$C_RESET"
    local reply=""
    read -r reply
    if [[ "$reply" == "s" || "$reply" == "S" ]]; then
      log_warn "[${key}] Fase B e C saltate su richiesta."
      return 0
    fi
  fi

  # --------------------------------------------------------------------------
  # FASE B: Editing Pulito (Spec read-only + NC-UI & locales in edit)
  # --------------------------------------------------------------------------
  if [[ "$PHASE" == "b" || "$PHASE" == "all" ]]; then
    if [[ "$DRY_RUN" -eq 0 && ! -s "$spec_file" ]]; then
      log_err "[${key}] Specifica non trovata o vuota: ${spec_file}. Esegui prima -p a."
      return 1
    fi

    local extra_read_b=()
    for f in "${extra_reads[@]}"; do
      [[ -f "$f" ]] && extra_read_b+=(--read "$f")
    done

    local prompt_b="Applica i miglioramenti descritti nella specifica ${spec_file} sui file editabili (${valid_py[*]}). Rispetta tassativamente ncui-golden_standard.md, ncui-guidelines.md e ponytail.md. Aggiorna sia locales/en.toml sia locales/it.toml per ogni nuova chiave t()."

    log_info "[FASE B] Editing Architect/Diff su ${valid_py[*]} + locales/{en,it}.toml"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf "%sDRY-RUN FASE B:%s %s --yes-always --read %s %s %s locales/en.toml locales/it.toml\n" \
        "$C_DIM" "$C_RESET" "${aider_base[*]}" "$spec_file" "${extra_read_b[*]}" "${valid_py[*]}"
    else
      "${aider_base[@]}" \
        --yes-always \
        --read "$spec_file" \
        "${extra_read_b[@]}" \
        "${valid_py[@]}" locales/en.toml locales/it.toml \
        --message "$prompt_b"
      log_ok "[${key}] Fase B completata."
    fi
  fi

  # --------------------------------------------------------------------------
  # FASE C: Screenshot di verifica (bin/nc-shot)
  # --------------------------------------------------------------------------
  if [[ "$PHASE" == "c" || "$PHASE" == "all" ]]; then
    if [[ ! -f "bin/nc-shot" ]]; then
      log_warn "bin/nc-shot non trovato, salto Fase C."
      return 0
    fi

    for st in "${subtabs[@]}"; do
      local shot_file="${SHOT_DIR}/${section}_${st}.png"
      log_info "[FASE C] Screenshot: -s ${section} -t ${st} -> ${shot_file}"
      if [[ "$DRY_RUN" -eq 1 ]]; then
        printf "%sDRY-RUN FASE C:%s python3 bin/nc-shot -s %s -t %s -o %s\n" \
          "$C_DIM" "$C_RESET" "$section" "$st" "$shot_file"
      else
        if python3 bin/nc-shot -s "$section" -t "$st" -o "$shot_file"; then
          log_ok "Salvato: ${shot_file}"
        else
          log_warn "nc-shot fallito per ${section}/${st} (verifica nome subtab)."
        fi
      fi
    done
  fi
}

# ------------------------------------------------------------------------------
# Argument Parsing
# ------------------------------------------------------------------------------
if [[ $# -eq 0 ]]; then
  show_help
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    -l|--list)
      list_targets
      exit 0
      ;;
    -t|--target)
      [[ $# -lt 2 ]] && { log_err "Opzione $1 richiede un argomento."; exit 1; }
      TARGET="$2"
      shift 2
      ;;
    -p|--phase)
      [[ $# -lt 2 ]] && { log_err "Opzione $1 richiede un argomento (a|b|c|all)."; exit 1; }
      PHASE="${2,,}"
      if [[ ! "$PHASE" =~ ^(a|b|c|all)$ ]]; then
        log_err "Fase non valida: '${PHASE}'. Valori ammessi: a, b, c, all."
        exit 1
      fi
      shift 2
      ;;
    -y|--yes)
      AUTO_YES=1
      shift
      ;;
    -d|--dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      log_err "Opzione sconosciuta: $1 (usa -h per l'aiuto)"
      exit 1
      ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  log_err "Devi specificare un target con -t <nome|all> (usa -l per la lista)."
  exit 1
fi

preflight

if [[ "$TARGET" == "all" ]]; then
  for key in "${TARGET_ORDER[@]}"; do
    run_target "$key" || log_warn "Target '${key}' terminato con errori, continuo col successivo."
  done
else
  if [[ -z "${TARGETS[$TARGET]+x}" ]]; then
    log_err "Target sconosciuto: '${TARGET}'. Usa -l per vedere i target validi."
    exit 1
  fi
  run_target "$TARGET"
fi
