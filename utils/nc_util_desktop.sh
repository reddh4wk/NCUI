#!/usr/bin/env bash
# ==============================================================================
# nc-desktop.sh — Gestore Sfondi, Display & Matrix (ASCII/JP) per Niri Wayland
# ==============================================================================
# Architettura modulare:
#   1. Bootstrap, Drop Privilegi & Auto-discovery Socket (Wayland / Niri)
#   2. Palette Cyberpunk Pastello & Gestione Stato / Processi per Monitor
#   3. Controllo Hardware Display Niri (Risoluzione, Refresh Rate, Max Mode, Scale)
#   4. Motori Grafici Background (awww Immagine/Unicolor/Random, Slideshow, NCMatrix JP)
#   5. Motore TUI Reattivo Real-Time (SIGWINCH, Redraw Zero-Flicker, Tab Completion)
#   6. Parser Unificato CLI & Helper
# ==============================================================================

set -uo pipefail

readonly VERSION="3.1.0"
readonly SCRIPT_NAME="nc-desktop"

# ==============================================================================
# 1. BOOTSTRAP & SICUREZZA PRIVILEGI
# ==============================================================================
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        TARGET_UID="$(id -u "${SUDO_USER}")"
        export XDG_RUNTIME_DIR="/run/user/${TARGET_UID}"
        exec sudo -u "${SUDO_USER}" \
            XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" \
            WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" \
            NIRI_SOCKET="${NIRI_SOCKET:-}" \
            "$0" "$@"
    else
        printf "\033[38;2;243;139;168m[!] Errore:\033[0m %s deve essere eseguito come utente, non come root.\n" "${SCRIPT_NAME}" >&2
        exit 1
    fi
fi

# --- Palette Cyberpunk Pastello (TrueColor ANSI) ------------------------------
readonly C_RESET=$'\033[0m'
readonly C_BOLD=$'\033[1m'
readonly C_DIM=$'\033[2m'
readonly C_LAVENDER=$'\033[38;2;180;190;254m'
readonly C_MAUVE=$'\033[38;2;203;166;247m'
readonly C_BLUE=$'\033[38;2;137;180;250m'
readonly C_SKY=$'\033[38;2;137;220;235m'
readonly C_TEAL=$'\033[38;2;148;226;213m'
readonly C_GREEN=$'\033[38;2;166;227;161m'
readonly C_PEACH=$'\033[38;2;250;179;135m'
readonly C_YELLOW=$'\033[38;2;249;226;175m'
readonly C_RED=$'\033[38;2;243;139;168m'
readonly C_TEXT=$'\033[38;2;205;214;244m'
readonly C_SUBTEXT=$'\033[38;2;166;173;200m'
readonly C_MUTED=$'\033[38;2;108;112;134m'

# --- Directory Operativa Utente -----------------------------------------------
source /usr/local/lib/nc-ui/nc_log.sh
readonly BASE_DIR="${HOME}/.local/nc-desktop"
readonly WALL_DIR="${BASE_DIR}/wallpapers"
readonly FAV_DIR="${WALL_DIR}/favorites"
readonly STATE_DIR="${BASE_DIR}/state"
readonly PID_DIR="${BASE_DIR}/pids"
readonly PROF_DIR="${BASE_DIR}/profiles"
readonly LOG_DIR="${BASE_DIR}/logs"
readonly HIST_FILE="${BASE_DIR}/history"

mkdir -p "${WALL_DIR}" "${FAV_DIR}" "${STATE_DIR}" "${PID_DIR}" "${PROF_DIR}" "${LOG_DIR}"

# --- Logging Standard ---------------------------------------------------------
msg_info() { 
    nc_log_info "$*"
    printf "${C_SKY}  [i]${C_RESET} ${C_TEXT}%s${C_RESET}\n" "$*"
}
msg_ok() { 
    nc_log_info "$*"
    printf "${C_GREEN}  [✓]${C_RESET} ${C_TEXT}%s${C_RESET}\n" "$*"
}
msg_warn() { 
    nc_log_warning "$*"
    printf "${C_YELLOW}  [!]${C_RESET} ${C_TEXT}%s${C_RESET}\n" "$*" >&2
}
msg_err() { 
    nc_log_error "$*"
    printf "${C_RED}  [✗]${C_RESET} ${C_TEXT}%s${C_RESET}\n" "$*" >&2
}

# --- Auto-discovery Socket Wayland e Niri -------------------------------------
ensure_wayland_env() {
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
        local wl_sock
        wl_sock="$(find "${XDG_RUNTIME_DIR}" -maxdepth 1 -type s -name 'wayland-*' ! -name '*.lock' 2>/dev/null | head -n1 || true)"
        [[ -n "${wl_sock}" ]] && export WAYLAND_DISPLAY="$(basename "${wl_sock}")"
    fi
    if [[ -z "${NIRI_SOCKET:-}" ]]; then
        local niri_sock
        niri_sock="$(find "${XDG_RUNTIME_DIR}" -maxdepth 1 -type s -name 'niri*.sock' 2>/dev/null | head -n1 || true)"
        [[ -n "${niri_sock}" ]] && export NIRI_SOCKET="${niri_sock}"
    fi
}

check_core_deps() {
    local missing=()
    for cmd in niri awww awww-daemon jq python3; do
        command -v "${cmd}" >/dev/null 2>&1 || missing+=("${cmd}")
    done
    if (( ${#missing[@]} > 0 )); then
        msg_err "Dipendenze mancanti: ${missing[*]}"
        exit 1
    fi
}

ensure_awww_daemon() {
    if ! awww query >/dev/null 2>&1; then
        nohup awww-daemon >/dev/null 2>&1 &
        disown || true
        local retries=25
        while (( retries > 0 )); do
            awww query >/dev/null 2>&1 && return 0
            sleep 0.1
            ((retries--))
        done
        msg_err "Impossibile inizializzare awww-daemon."
        return 1
    fi
    return 0
}

# ==============================================================================
# 2. QUERY HARDWARE NIRI, RISOLUZIONI E VALIDAZIONE
# ==============================================================================
get_ordered_outputs_json() {
    niri msg -j outputs 2>/dev/null | jq -c '
        to_entries
        | map(.value)
        | map(select(.logical != null))
        | sort_by(.logical.x, .logical.y, .name)
        | to_entries
        | map(.value + {id: (.key + 1)})
    '
}

resolve_outputs() {
    local target="${1:-all}"
    local json
    json="$(get_ordered_outputs_json)"
    if [[ -z "${json}" || "${json}" == "[]" ]]; then
        msg_err "Nessuno schermo attivo rilevato da Niri."
        return 1
    fi

    if [[ "${target}" == "all" || "${target}" == "ALL" || "${target}" == "*" ]]; then
        jq -r '.[].name' <<< "${json}"
        return 0
    fi

    local item resolved out_list=()
    IFS=',' read -ra items <<< "${target}"
    for item in "${items[@]}"; do
        if [[ "${item}" =~ ^[0-9]+$ ]]; then
            resolved="$(jq -r --argjson id "${item}" '.[] | select(.id == $id) | .name' <<< "${json}")"
        else
            resolved="$(jq -r --arg name "${item}" '.[] | select((.name | ascii_downcase) == ($name | ascii_downcase)) | .name' <<< "${json}")"
        fi
        if [[ -z "${resolved}" ]]; then
            msg_err "Schermo '${item}' inesistente. Usa ID (1, 2), porta (DP-1, DP-3) o 'all'."
            return 1
        fi
        out_list+=("${resolved}")
    done
    printf "%s\n" "${out_list[@]}"
    return 0
}

get_max_mode_for_output() {
    local out="${1}"
    niri msg -j outputs 2>/dev/null | jq -r --arg out "${out}" '
        .[$out].modes
        | sort_by(.width * .height, .width, .refresh_rate)
        | last
        | "\(.width)x\(.height)@\(if .refresh_rate > 1000 then (.refresh_rate / 1000.0) else .refresh_rate end)"
    '
}

cmd_set_resolution() {
    local out="${1}" mode_spec="${2}"
    local actual_mode="${mode_spec}"

    if [[ "${mode_spec,,}" == "max" || "${mode_spec,,}" == "best" || "${mode_spec,,}" == "native" ]]; then
        actual_mode="$(get_max_mode_for_output "${out}")"
        if [[ -z "${actual_mode}" || "${actual_mode}" == "null" ]]; then
            msg_err "Impossibile determinare la risoluzione massima per ${out}."
            return 1
        fi
    elif [[ ! "${mode_spec}" =~ ^[0-9]+x[0-9]+(@[0-9]+(\.[0-9]+)?)?$ ]]; then
        msg_err "Formato risoluzione '${mode_spec}' non valido. Usa WxH, WxH@Hz oppure 'max'."
        return 1
    fi

    if niri msg output "${out}" mode "${actual_mode}" >/dev/null 2>&1; then
        msg_ok "Schermo ${out}: risoluzione impostata a ${actual_mode}"
        return 0
    else
        msg_err "Schermo ${out}: modalità '${actual_mode}' rifiutata dal monitor. Usa 'modes ${out}' per l'elenco."
        return 1
    fi
}

cmd_set_scale() {
    local out="${1}" scale_val="${2}"
    if [[ ! "${scale_val}" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        msg_err "Fattore di scala '${scale_val}' non valido (es. 1, 1.25, 1.5)."
        return 1
    fi
    if niri msg output "${out}" scale "${scale_val}" >/dev/null 2>&1; then
        msg_ok "Schermo ${out}: scala impostata a ${scale_val}x"
        return 0
    else
        msg_err "Impossibile applicare la scala ${scale_val} su ${out}."
        return 1
    fi
}

cmd_set_position() {
    local out="${1}" pos_spec="${2}"
    if [[ "${pos_spec,,}" == "auto" ]]; then
        niri msg output "${out}" position auto >/dev/null 2>&1 && {
            msg_ok "Schermo ${out}: posizionamento impostato su automatico."
            return 0
        }
    elif [[ "${pos_spec}" =~ ^(-?[0-9]+)[,x:](-?[0-9]+)$ ]]; then
        local px="${BASH_REMATCH[1]}" py="${BASH_REMATCH[2]}"
        if niri msg output "${out}" position set "${px}" "${py}" >/dev/null 2>&1; then
            msg_ok "Schermo ${out}: posizione logica impostata a (${px}, ${py})."
            return 0
        fi
    fi
    msg_err "Posizione '${pos_spec}' non valida. Usa X,Y (es. 0,0 o 2560,0) oppure 'auto'."
    return 1
}

cmd_list_modes() {
    local target_out="${1:-all}"
    local outs out
    outs="$(resolve_outputs "${target_out}")" || return 1
    echo
    while IFS= read -r out; do
        [[ -z "${out}" ]] && continue
        printf "  ${C_BOLD}${C_MAUVE}Modalità supportate da ${C_SKY}%s${C_RESET}:\n" "${out}"
        niri msg -j outputs 2>/dev/null | jq -r --arg out "${out}" '
            .[$out] as $o
            | $o.modes
            | to_entries
            | map({
                idx: .key,
                w: .value.width,
                h: .value.height,
                hz: (if .value.refresh_rate > 1000 then (.value.refresh_rate / 1000.0) else .value.refresh_rate end),
                pref: .value.is_preferred,
                cur: (.key == $o.current_mode)
            })
            | sort_by(-(.w * .h), -.hz)
            | .[]
            | "    \(if .cur then "\u001b[38;2;166;227;161m● ATTUALE \u001b[0m" else "          " end)\u001b[38;2;205;214;244m\(.w)x\(.h)@\(.hz | tostring | .[0:6])Hz\u001b[0m \(if .pref then "\u001b[38;2;250;179;135m[Nativa/Preferred]\u001b[0m" else "" end)"
        '
        echo
    done <<< "${outs}"
}

# --- Validazione Parametri Sfondi ---------------------------------------------
parse_interval_seconds() {
    local raw="${1}"
    if [[ "${raw}" =~ ^([0-9]+)[sS]?$ ]]; then
        echo "${BASH_REMATCH[1]}"
    elif [[ "${raw}" =~ ^([0-9]+)[mM]$ ]]; then
        echo "$(( BASH_REMATCH[1] * 60 ))"
    elif [[ "${raw}" =~ ^([0-9]+)[hH]$ ]]; then
        echo "$(( BASH_REMATCH[1] * 3600 ))"
    else
        msg_err "Intervallo '${raw}' non valido (usa es. 30s, 5m, 1h)."
        return 1
    fi
}

map_fit_to_awww() {
    local mode="${1,,}"
    case "${mode}" in
        fill|crop)      echo "crop" ;;
        fit|contain)    echo "fit" ;;
        stretch|scale)  echo "stretch" ;;
        center|no|none) echo "no" ;;
        *)
            msg_err "Fit '${1}' non valido. Valori ammessi: fill, fit, stretch, center."
            return 1
            ;;
    esac
}

validate_transition() {
    local trans="${1,,}"
    case "${trans}" in
        simple|fade|left|right|top|bottom|wipe|wave|grow|center|any|outer|random|none)
            echo "${trans}"
            ;;
        *)
            msg_err "Transizione '${1}' non valida (wipe, wave, grow, outer, fade, simple, random, none)."
            return 1
            ;;
    esac
}

validate_ncmatrix_color() {
    local raw="${1}"
    local col="${raw,,}"
    if [[ "${col}" =~ ^#?[0-9a-f]{6}$ ]]; then
        echo "#${col#\#}"
        return 0
    fi
    case "${col}" in
        green|cyan|blue|red|magenta|yellow|white|black|rainbow)
            echo "${col}"
            ;;
        *)
            msg_err "Colore '${1}' non valido (green, cyan, yellow, white, rainbow, #RRGGBB)."
            return 1
            ;;
    esac
}

resolve_solid_color() {
    local raw="${1}"
    local cleaned="${raw#\#}"
    cleaned="${cleaned#0x}"
    cleaned="${cleaned#0X}"

    case "${cleaned,,}" in
        white)                echo "ffffff" ; return 0 ;;
        black)                echo "000000" ; return 0 ;;
        red)                  echo "ff0000" ; return 0 ;;
        green|lime)           echo "00ff00" ; return 0 ;;
        blue)                 echo "0000ff" ; return 0 ;;
        yellow)               echo "ffff00" ; return 0 ;;
        cyan|aqua)            echo "00ffff" ; return 0 ;;
        magenta|fuchsia)      echo "ff00ff" ; return 0 ;;
        gray|grey)            echo "808080" ; return 0 ;;
        darkgray|darkgrey)    echo "2a2a2e" ; return 0 ;;
        lightgray|lightgrey|silver) echo "c0c0c0" ; return 0 ;;
        orange)               echo "ff8800" ; return 0 ;;
        purple|violet)        echo "800080" ; return 0 ;;
        pink)                 echo "ffc0cb" ; return 0 ;;
        brown)                echo "8b4513" ; return 0 ;;
        navy)                 echo "000080" ; return 0 ;;
        teal)                 echo "008080" ; return 0 ;;
        olive)                echo "808000" ; return 0 ;;
        maroon)               echo "800000" ; return 0 ;;
        indigo)               echo "4b0082" ; return 0 ;;
        coral)                echo "ff7f50" ; return 0 ;;
        gold)                 echo "ffd700" ; return 0 ;;
        cyber)                echo "0d0e15" ; return 0 ;;
        anthracite|dark)      echo "18181a" ; return 0 ;;
        mocha|catppuccin)     echo "1e1e2e" ; return 0 ;;
        mantle)               echo "181825" ; return 0 ;;
        crust)                echo "11111b" ; return 0 ;;
        lavender)             echo "b4befe" ; return 0 ;;
        mauve)                echo "cba6f7" ; return 0 ;;
        peach)                echo "fab387" ; return 0 ;;
        sky)                  echo "89dceb" ; return 0 ;;
        sapphire)             echo "74c7ec" ; return 0 ;;
        rosewater)            echo "f5e0dc" ; return 0 ;;
        flamingo)             echo "f2cdcd" ; return 0 ;;
    esac

    if [[ "${cleaned}" =~ ^([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$ ]]; then
        local r="${BASH_REMATCH[1]}" g="${BASH_REMATCH[2]}" b="${BASH_REMATCH[3]}"
        echo "${r,,}${r,,}${g,,}${g,,}${b,,}${b,,}"
        return 0
    fi

    if [[ "${cleaned}" =~ ^[0-9a-fA-F]{6}$ ]]; then
        echo "${cleaned,,}"
        return 0
    fi

    msg_err "Colore '${1}' non valido. Usa un nome (es. white, black, red, cyber) o un hex (es. #ffffff)."
    return 1
}

resolve_image_path() {
    local input="${1/#\~/$HOME}"
    if [[ -f "${input}" ]]; then
        realpath "${input}"
        return 0
    elif [[ -f "${WALL_DIR}/${input}" ]]; then
        realpath "${WALL_DIR}/${input}"
        return 0
    fi
    msg_err "Immagine '${1}' non trovata (cercata anche in ~/.local/nc-desktop/wallpapers)."
    return 1
}

resolve_dir_path() {
    local input="${1/#\~/$HOME}"
    if [[ -d "${input}" ]]; then
        realpath "${input}"
        return 0
    elif [[ -d "${WALL_DIR}/${input}" ]]; then
        realpath "${WALL_DIR}/${input}"
        return 0
    fi
    msg_err "Cartella '${1}' non trovata (cercata anche in ~/.local/nc-desktop/wallpapers)."
    return 1
}

# ==============================================================================
# 3. GESTIONE ATOMICA PROCESSI E STATO PER MONITOR
# ==============================================================================
kill_pidfile() {
    local pidfile="${1}"
    [[ ! -f "${pidfile}" ]] && return 0
    local pid
    pid="$(cat "${pidfile}" 2>/dev/null || true)"
    if [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null; then
        pkill -TERM -P "${pid}" 2>/dev/null || true
        kill -TERM "${pid}" 2>/dev/null || true
        local wait_ticks=15
        while (( wait_ticks > 0 )) && kill -0 "${pid}" 2>/dev/null; do
            sleep 0.04
            ((wait_ticks--))
        done
        if kill -0 "${pid}" 2>/dev/null; then
            pkill -KILL -P "${pid}" 2>/dev/null || true
            kill -KILL "${pid}" 2>/dev/null || true
        fi
    fi
    rm -f "${pidfile}"
}

collect_output_daemon_pids() {
    local out="${1}"
    local -A seen=()
    local pf pid
    for pf in \
        "${PID_DIR}/slideshow_${out}.pid" \
        "${PID_DIR}/ncmatrix_${out}.pid" \
        "${PID_DIR}/cmatrix_${out}.pid" \
        "${PID_DIR}/video_${out}.pid" \
        "${PID_DIR}/solid_${out}.pid"; do
        if [[ -f "${pf}" ]]; then
            pid="$(cat "${pf}" 2>/dev/null || true)"
            if [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null; then
                seen["${pid}"]=1
            fi
        fi
    done

    local pat
    for pat in \
        "^([^ ]*/)?ncmatrix .*--output[= ]${out}( |\$)" \
        "^([^ ]*/)?swaybg .*-o[= ]${out}( |\$)" \
        "^([^ ]*/)?(python[^ ]* |env python[^ ]* )?([^ ]*/)?nc-video-wallpaper.*--output[= ]${out}( |\$)" \
        "^([^ ]*/)?ffplay .*-window_title nc-vidwp-audio-${out}( |\$)" \
        "^([^ ]*/)?pacat .*nc-vidwp-audio-${out}( |\$)" \
        "^([^ ]*/)?mpvpaper .*( |^)${out}( |\$)"; do
        while IFS= read -r pid; do
            [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] && seen["${pid}"]=1
        done < <(pgrep -f "${pat}" 2>/dev/null || true)
    done

    for pid in "${!seen[@]}"; do
        printf "%s\n" "${pid}"
    done
}

kill_collected_pids() {
    local exclude_pid="${1:-0}"
    shift || true
    local pids=("$@")
    (( ${#pids[@]} == 0 )) && return 0

    local p alive=()
    for p in "${pids[@]}"; do
        [[ -z "${p}" || "${p}" == "${exclude_pid}" || "${p}" == "$$" ]] && continue
        if kill -0 "${p}" 2>/dev/null; then
            pkill -TERM -P "${p}" 2>/dev/null || true
            kill -TERM "${p}" 2>/dev/null || true
            alive+=("${p}")
        fi
    done

    (( ${#alive[@]} == 0 )) && return 0
    sleep 0.15
    for p in "${alive[@]}"; do
        if kill -0 "${p}" 2>/dev/null; then
            pkill -KILL -P "${p}" 2>/dev/null || true
            kill -KILL "${p}" 2>/dev/null || true
        fi
    done
}

cleanup_output_daemons() {
    local out="${1}"
    local exclude_pid="${2:-0}"
    local old_pids=()
    mapfile -t old_pids < <(collect_output_daemon_pids "${out}")
    rm -f "${PID_DIR}/pause_${out}.flag" "/tmp/nc_mpvpaper_${out}.sock"
    rm -f \
        "${PID_DIR}/slideshow_${out}.pid" \
        "${PID_DIR}/ncmatrix_${out}.pid" \
        "${PID_DIR}/cmatrix_${out}.pid" \
        "${PID_DIR}/video_${out}.pid" \
        "${PID_DIR}/solid_${out}.pid"
    if (( ${#old_pids[@]} > 0 )); then
        kill_collected_pids "${exclude_pid}" "${old_pids[@]}"
    fi
}

save_output_state() {
    local out="${1}" mode="${2}" target="${3}" fit="${4}" trans="${5}" interval="${6}" color="${7}" speed="${8}" bgcolor="${9}" shuffle="${10}" japanese="${11:-false}" align="${12:-center}"
    jq -n \
        --arg output "${out}" \
        --arg mode "${mode}" \
        --arg target "${target}" \
        --arg fit "${fit}" \
        --arg align "${align}" \
        --arg transition "${trans}" \
        --arg interval "${interval}" \
        --arg color "${color}" \
        --arg speed "${speed}" \
        --arg bgcolor "${bgcolor}" \
        --arg shuffle "${shuffle}" \
        --arg japanese "${japanese}" \
        --arg updated_at "$(date '+%Y-%m-%d %H:%M:%S')" \
        '{
            output: $output,
            mode: $mode,
            target: $target,
            fit: $fit,
            align: $align,
            transition: $transition,
            interval: $interval,
            color: $color,
            speed: $speed,
            bgcolor: $bgcolor,
            shuffle: $shuffle,
            japanese: $japanese,
            updated_at: $updated_at
        }' > "${STATE_DIR}/${out}.json"
}

# ==============================================================================
# 4. MOTORI GRAFICI: IMMAGINI, UNICOLOR, RANDOM, SLIDESHOW, NCMATRIX (ASCII/JP)
# ==============================================================================
apply_single_image() {
    local out="${1}" img="${2}" fit="${3}" trans="${4}" bgcolor="${5:-18181a}" align="${6:-center}"
    local awww_resize
    awww_resize="$(map_fit_to_awww "${fit}")" || return 1

    local actual_trans="${trans}"
    if [[ "${trans}" == "random" ]]; then
        local pool=("wipe" "wave" "grow" "outer" "fade" "simple" "left" "right")
        actual_trans="${pool[$(( RANDOM % ${#pool[@]} ))]}"
    fi

    local extra_args=()
    if [[ "${awww_resize}" == "crop" ]]; then
        case "${align,,}" in
            top|bottom|center) extra_args+=(--crop-gravity "${align,,}") ;;
            *)                 extra_args+=(--crop-gravity "center") ;;
        esac
    fi
    case "${actual_trans}" in
        wipe|wave)
            extra_args+=(--transition-angle 30 --transition-step 90 --transition-duration 1.4 --transition-fps 120)
            ;;
        grow|outer)
            extra_args+=(--transition-pos "0.5,0.5" --transition-step 90 --transition-duration 1.4 --transition-fps 120)
            ;;
        none)
            actual_trans="simple"
            extra_args+=(--transition-step 255 --transition-duration 0)
            ;;
        *)
            extra_args+=(--transition-step 90 --transition-duration 1.2 --transition-fps 120)
            ;;
    esac

    awww img "${img}" \
        --outputs "${out}" \
        --resize "${awww_resize}" \
        --fill-color "${bgcolor#\#}" \
        --transition-type "${actual_trans}" \
        "${extra_args[@]}" >/dev/null 2>&1
}

cmd_set_image() {
    local out="${1}" img="${2}" fit="${3}" trans="${4}" bgcolor="${5}" align="${6:-center}"
    ensure_awww_daemon || return 1
    local old_pids=()
    mapfile -t old_pids < <(collect_output_daemon_pids "${out}")
    apply_single_image "${out}" "${img}" "${fit}" "${trans}" "${bgcolor}" "${align}" || return 1
    save_output_state "${out}" "image" "${img}" "${fit}" "${trans}" "" "" "" "${bgcolor}" "" "false" "${align}"
    if (( ${#old_pids[@]} > 0 )); then
        sleep 0.25
        rm -f "${PID_DIR}/pause_${out}.flag" "/tmp/nc_mpvpaper_${out}.sock" \
            "${PID_DIR}/slideshow_${out}.pid" "${PID_DIR}/ncmatrix_${out}.pid" \
            "${PID_DIR}/cmatrix_${out}.pid" "${PID_DIR}/video_${out}.pid" \
            "${PID_DIR}/solid_${out}.pid"
        kill_collected_pids 0 "${old_pids[@]}"
    fi
    msg_ok "Schermo ${out}: applicata '$(basename "${img}")' [fit: ${fit}, trans: ${trans}]"
}

cmd_set_solid() {
    local out="${1}" color_spec="${2}"
    local hex
    hex="$(resolve_solid_color "${color_spec}")" || return 1

    local upper_hex="#${hex^^}"
    local display_label="${upper_hex}"
    local old_pids=()
    mapfile -t old_pids < <(collect_output_daemon_pids "${out}")

    if command -v swaybg >/dev/null 2>&1; then
        nohup swaybg -o "${out}" -c "${upper_hex}" -m solid_color >/dev/null 2>&1 &
        local new_pid=$!
        disown "${new_pid}" 2>/dev/null || true
        sleep 0.30
        if ! kill -0 "${new_pid}" 2>/dev/null; then
            msg_err "Schermo ${out}: swaybg terminato inaspettatamente."
            return 1
        fi
        printf "%s\n" "${new_pid}" > "${PID_DIR}/solid_${out}.pid"
        rm -f "${PID_DIR}/pause_${out}.flag" "/tmp/nc_mpvpaper_${out}.sock" \
            "${PID_DIR}/slideshow_${out}.pid" "${PID_DIR}/ncmatrix_${out}.pid" \
            "${PID_DIR}/cmatrix_${out}.pid" "${PID_DIR}/video_${out}.pid"
        kill_collected_pids "${new_pid}" "${old_pids[@]}"
    else
        ensure_awww_daemon || return 1
        if ! awww clear --outputs "${out}" "${hex}" >/dev/null 2>&1; then
            msg_err "Schermo ${out}: impossibile applicare il colore uniforme ${display_label}."
            return 1
        fi
        if (( ${#old_pids[@]} > 0 )); then
            sleep 0.25
            rm -f "${PID_DIR}/pause_${out}.flag" "/tmp/nc_mpvpaper_${out}.sock" \
                "${PID_DIR}/slideshow_${out}.pid" "${PID_DIR}/ncmatrix_${out}.pid" \
                "${PID_DIR}/cmatrix_${out}.pid" "${PID_DIR}/video_${out}.pid" \
                "${PID_DIR}/solid_${out}.pid"
            kill_collected_pids 0 "${old_pids[@]}"
        fi
    fi

    save_output_state "${out}" "solid" "${upper_hex}" "" "" "" "" "" "${hex}" "" "false"
    msg_ok "Schermo ${out}: applicato sfondo uniforme ${display_label}"
}

cmd_set_random() {
    local out="${1}" dir_input="${2:-${WALL_DIR}}" fit="${3:-fill}" trans="${4:-random}" bgcolor="${5:-18181a}"
    local dir
    dir="$(resolve_dir_path "${dir_input}")" || return 1
    local chosen
    chosen="$(find -L "${dir}" -maxdepth 3 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' -o -iname '*.gif' \) 2>/dev/null | shuf -n 1)"
    if [[ -z "${chosen}" ]]; then
        msg_err "Nessuna immagine trovata in '${dir}' per la selezione casuale."
        return 1
    fi
    cmd_set_image "${out}" "${chosen}" "${fit}" "${trans}" "${bgcolor}"
}

cmd_fav_wallpaper() {
    local out="${1}"
    local sf="${STATE_DIR}/${out}.json"
    if [[ ! -f "${sf}" ]]; then
        msg_err "Nessuno stato registrato per lo schermo ${out}."
        return 1
    fi
    local mode target cur_img src_file=""
    mode="$(jq -r '.mode // empty' "${sf}")"
    target="$(jq -r '.target // empty' "${sf}")"
    cur_img="$(jq -r '.current_image // empty' "${sf}")"

    if [[ "${mode}" == "image" && -f "${target}" ]]; then
        src_file="${target}"
    elif [[ "${mode}" == "slideshow" && -n "${cur_img}" ]]; then
        src_file="$(find -L "${target}" -maxdepth 2 -type f -name "${cur_img}" 2>/dev/null | head -n1 || true)"
    fi

    if [[ -z "${src_file}" || ! -f "${src_file}" ]]; then
        msg_err "Nessuna immagine attiva salvabile nei preferiti su ${out}."
        return 1
    fi
    cp -f "${src_file}" "${FAV_DIR}/"
    msg_ok "Schermo ${out}: '$(basename "${src_file}")' salvata in ~/.local/nc-desktop/wallpapers/favorites/"
}

cmd_swap_outputs() {
    local id1="${1}" id2="${2}"
    local out1 out2
    out1="$(resolve_outputs "${id1}" | head -n1)" || return 1
    out2="$(resolve_outputs "${id2}" | head -n1)" || return 1
    if [[ "${out1}" == "${out2}" ]]; then
        msg_err "Specifica due schermi diversi per effettuare lo scambio."
        return 1
    fi
    local f1="${STATE_DIR}/${out1}.json"
    local f2="${STATE_DIR}/${out2}.json"
    if [[ ! -f "${f1}" && ! -f "${f2}" ]]; then
        msg_err "Nessuno stato salvato sugli schermi ${out1} e ${out2} da scambiare."
        return 1
    fi
    local tmp_dir
    tmp_dir="$(mktemp -d)"
    [[ -f "${f1}" ]] && jq --arg o "${out2}" '.output = $o' "${f1}" > "${tmp_dir}/${out2}.json"
    [[ -f "${f2}" ]] && jq --arg o "${out1}" '.output = $o' "${f2}" > "${tmp_dir}/${out1}.json"
    restore_state_from_dir "${tmp_dir}"
    rm -rf "${tmp_dir}"
    msg_ok "Sfondi scambiati con successo tra ${out1} e ${out2}."
}

run_slideshow_worker() {
    local out="${1}" dir="${2}" fit="${3}" trans="${4}" interval_sec="${5}" bgcolor="${6}" shuffle="${7}"
    ensure_wayland_env
    ensure_awww_daemon || exit 1

    trap ':' USR1
    trap 'exit 0' TERM INT

    while true; do
        local files=()
        if [[ "${shuffle}" == "true" ]]; then
            mapfile -d '' -t files < <(find -L "${dir}" -maxdepth 2 -type f \( \
                -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
                -o -iname '*.webp' -o -iname '*.gif' -o -iname '*.bmp' \
            \) -print0 2>/dev/null | shuf -z)
        else
            mapfile -d '' -t files < <(find -L "${dir}" -maxdepth 2 -type f \( \
                -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
                -o -iname '*.webp' -o -iname '*.gif' -o -iname '*.bmp' \
            \) -print0 2>/dev/null | sort -z)
        fi

        if (( ${#files[@]} == 0 )); then
            sleep 10
            continue
        fi

        local img
        for img in "${files[@]}"; do
            while [[ -f "${PID_DIR}/pause_${out}.flag" ]]; do
                sleep 1
            done
            apply_single_image "${out}" "${img}" "${fit}" "${trans}" "${bgcolor}" || true
            if [[ -f "${STATE_DIR}/${out}.json" ]]; then
                local tmp_state
                tmp_state="$(jq --arg cur "$(basename "${img}")" '. + {current_image: $cur}' "${STATE_DIR}/${out}.json" 2>/dev/null || true)"
                [[ -n "${tmp_state}" ]] && printf "%s\n" "${tmp_state}" > "${STATE_DIR}/${out}.json"
            fi
            sleep "${interval_sec}" &
            local sleep_pid=$!
            wait "${sleep_pid}" 2>/dev/null || true
        done
    done
}

cmd_set_slideshow() {
    local out="${1}" dir="${2}" fit="${3}" trans="${4}" interval_raw="${5}" bgcolor="${6}" shuffle="${7}"
    local interval_sec
    interval_sec="$(parse_interval_seconds "${interval_raw}")" || return 1
    if (( interval_sec < 2 )); then
        msg_err "L'intervallo minimo consentito per la slideshow è 2 secondi."
        return 1
    fi

    local count
    count="$(find -L "${dir}" -maxdepth 2 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' -o -iname '*.gif' -o -iname '*.bmp' \) 2>/dev/null | wc -l)"
    if (( count == 0 )); then
        msg_err "Nessuna immagine valida trovata nella cartella '${dir}'."
        return 1
    fi

    ensure_awww_daemon || return 1
    local old_pids=()
    mapfile -t old_pids < <(collect_output_daemon_pids "${out}")
    save_output_state "${out}" "slideshow" "${dir}" "${fit}" "${trans}" "${interval_raw}" "" "" "${bgcolor}" "${shuffle}" "false"

    nohup "$0" --internal-worker "${out}" "${dir}" "${fit}" "${trans}" "${interval_sec}" "${bgcolor}" "${shuffle}" \
        >> "${LOG_DIR}/slideshow_${out}.log" 2>&1 &
    local worker_pid=$!
    disown "${worker_pid}" 2>/dev/null || true
    printf "%s\n" "${worker_pid}" > "${PID_DIR}/slideshow_${out}.pid"
    if (( ${#old_pids[@]} > 0 )); then
        sleep 0.30
        rm -f "${PID_DIR}/pause_${out}.flag" "/tmp/nc_mpvpaper_${out}.sock" \
            "${PID_DIR}/ncmatrix_${out}.pid" "${PID_DIR}/cmatrix_${out}.pid" \
            "${PID_DIR}/video_${out}.pid" "${PID_DIR}/solid_${out}.pid"
        kill_collected_pids "${worker_pid}" "${old_pids[@]}"
    fi

    msg_ok "Schermo ${out}: slideshow avviata da '$(basename "${dir}")' (${count} img, ogni ${interval_raw})"
}

cmd_set_ncmatrix() {
    local out="${1}" color="${2}" speed="${3}" japanese="${4:-false}" font_size="${5:-9}"
    local charset="ascii"
    [[ "${japanese}" == "true" ]] && charset="katakana"

    local ncmatrix_bin script_dir
    script_dir="$(dirname "$(readlink -f "$0")")"
    ncmatrix_bin="${script_dir}/../bin/ncmatrix"
    if [[ ! -x "${ncmatrix_bin}" ]]; then
        msg_err "'${ncmatrix_bin}' non trovato o non eseguibile (esegui: make -C utils/ncmatrix)."
        return 1
    fi

    cat > "/tmp/ncmatrix_${out}.conf" <<EOF
charset=${charset}
color=${color}
speed=${speed}
font_size=${font_size}
bold=2
async=1
fade=1
EOF

    local pidfile="${PID_DIR}/ncmatrix_${out}.pid"
    local running_pid=""
    if [[ -f "${pidfile}" ]]; then
        local cand
        cand="$(cat "${pidfile}" 2>/dev/null || true)"
        if [[ -n "${cand}" && "${cand}" =~ ^[0-9]+$ ]] && kill -0 "${cand}" 2>/dev/null; then
            if tr '\0' ' ' < "/proc/${cand}/cmdline" 2>/dev/null | grep -q "ncmatrix"; then
                running_pid="${cand}"
            fi
        fi
    fi

    if [[ -n "${running_pid}" ]]; then
        save_output_state "${out}" "ncmatrix" "ncmatrix" "" "" "" "${color}" "${speed}" "" "" "${japanese}"
        kill -USR1 "${running_pid}" 2>/dev/null || true
    else
        local old_pids=()
        mapfile -t old_pids < <(collect_output_daemon_pids "${out}")
        rm -f "/tmp/nc_mpvpaper_${out}.sock"
        save_output_state "${out}" "ncmatrix" "ncmatrix" "" "" "" "${color}" "${speed}" "" "" "${japanese}"
        nohup "${ncmatrix_bin}" \
            --wallpaper \
            --output "${out}" \
            --charset "${charset}" \
            --color "${color}" \
            --speed "${speed}" \
            --font-size "${font_size}" \
            --pid-file "${pidfile}" \
            --ipc-socket "/tmp/nc_mpvpaper_${out}.sock" \
            -a -b \
            >> "${LOG_DIR}/ncmatrix_${out}.log" 2>&1 &
        local new_pid=$!
        disown "${new_pid}" 2>/dev/null || true
        if (( ${#old_pids[@]} > 0 )); then
            sleep 0.35
            kill_collected_pids "${new_pid}" "${old_pids[@]}"
        fi
    fi

    local jp_label=""
    [[ "${japanese}" == "true" ]] && jp_label=" [Katakana JP]"
    msg_ok "Schermo ${out}: ncmatrix${jp_label} attivato [colore: ${color}, speed: ${speed}, ${font_size}pt]"
}

cmd_control_slideshow() {
    local action="${1}" target_out="${2:-all}"
    local outs out
    outs="$(resolve_outputs "${target_out}")" || return 1

    while IFS= read -r out; do
        [[ -z "${out}" ]] && continue
        local pidfile="${PID_DIR}/slideshow_${out}.pid"
        case "${action}" in
            next)
                if [[ -f "${pidfile}" ]] && kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
                    local wpid
                    wpid="$(cat "${pidfile}")"
                    pkill -TERM -P "${wpid}" sleep 2>/dev/null || kill -USR1 "${wpid}" 2>/dev/null || true
                    msg_ok "Schermo ${out}: avanzamento immediato alla prossima immagine."
                else
                    msg_err "Schermo ${out}: nessuna slideshow attiva."
                    return 1
                fi
                ;;
            pause)
                if [[ -f "${pidfile}" ]] && kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
                    touch "${PID_DIR}/pause_${out}.flag"
                    msg_ok "Schermo ${out}: slideshow messa in pausa."
                else
                    msg_err "Schermo ${out}: nessuna slideshow attiva."
                    return 1
                fi
                ;;
            resume)
                if [[ -f "${PID_DIR}/pause_${out}.flag" ]]; then
                    rm -f "${PID_DIR}/pause_${out}.flag"
                    msg_ok "Schermo ${out}: slideshow ripresa."
                else
                    msg_err "Schermo ${out}: la slideshow non era in pausa."
                    return 1
                fi
                ;;
            stop)
                cleanup_output_daemons "${out}"
                msg_ok "Schermo ${out}: processi background (slideshow/ncmatrix) terminati."
                ;;
        esac
    done <<< "${outs}"
}

restore_state_from_dir() {
    local src_dir="${1}"
    ensure_wayland_env
    ensure_awww_daemon || return 1
    local sf
    shopt -s nullglob
    for sf in "${src_dir}"/*.json; do
        local out mode target fit align trans interval color speed bgcolor shuffle japanese
        out="$(jq -r '.output // empty' "${sf}")"
        mode="$(jq -r '.mode // empty' "${sf}")"
        target="$(jq -r '.target // empty' "${sf}")"
        fit="$(jq -r '.fit // "fill"' "${sf}")"
        align="$(jq -r '.align // "center"' "${sf}")"
        trans="$(jq -r '.transition // "wipe"' "${sf}")"
        interval="$(jq -r '.interval // "5m"' "${sf}")"
        color="$(jq -r '.color // "green"' "${sf}")"
        speed="$(jq -r '.speed // "4"' "${sf}")"
        bgcolor="$(jq -r '.bgcolor // "18181a"' "${sf}")"
        shuffle="$(jq -r '.shuffle // "true"' "${sf}")"
        japanese="$(jq -r '.japanese // "false"' "${sf}")"

        [[ -z "${out}" ]] && continue
        resolve_outputs "${out}" >/dev/null 2>&1 || continue

        case "${mode}" in
            image)     [[ -f "${target}" ]] && cmd_set_image "${out}" "${target}" "${fit}" "${trans}" "${bgcolor}" "${align}" ;;
            solid)     cmd_set_solid "${out}" "${bgcolor}" ;;
            slideshow) [[ -d "${target}" ]] && cmd_set_slideshow "${out}" "${target}" "${fit}" "${trans}" "${interval}" "${bgcolor}" "${shuffle}" ;;
            ncmatrix|cmatrix) cmd_set_ncmatrix "${out}" "${color}" "${speed}" "${japanese}" ;;
        esac
    done
    shopt -u nullglob
}

cmd_save_profile() {
    local name="${1:-}"
    if [[ -z "${name}" || ! "${name}" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        msg_err "Nome profilo non valido (usa solo lettere, numeri, - e _)."
        return 1
    fi
    local pdir="${PROF_DIR}/${name}"
    mkdir -p "${pdir}"
    rm -f "${pdir}"/*.json
    if ! cp -f "${STATE_DIR}"/*.json "${pdir}/" 2>/dev/null; then
        msg_err "Nessuno stato attivo da salvare."
        return 1
    fi
    msg_ok "Profilo '${name}' salvato con successo."
}

cmd_load_profile() {
    local name="${1:-}"
    local pdir="${PROF_DIR}/${name}"
    if [[ -z "${name}" || ! -d "${pdir}" ]]; then
        msg_err "Profilo '${name}' non trovato in ~/.local/nc-desktop/profiles."
        return 1
    fi
    restore_state_from_dir "${pdir}" || return 1
    msg_ok "Profilo '${name}' caricato con successo."
}

cmd_list_wallpapers() {
    printf "\n${C_BOLD}${C_MAUVE}  Archivio Sfondi Locale:${C_RESET} ${C_DIM}%s${C_RESET}\n" "${WALL_DIR}"
    printf "  ${C_MUTED}────────────────────────────────────────────────────────────────────────────────${C_RESET}\n"
    local dirs files
    mapfile -t dirs < <(find -L "${WALL_DIR}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
    mapfile -t files < <(find -L "${WALL_DIR}" -maxdepth 2 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' -o -iname '*.gif' \) 2>/dev/null | sort)

    if (( ${#dirs[@]} == 0 && ${#files[@]} == 0 )); then
        printf "  ${C_YELLOW}[!]${C_RESET} ${C_TEXT}La cartella '${WALL_DIR}' è attualmente vuota.${C_RESET}\n"
        printf "  ${C_SKY}[i]${C_RESET} ${C_SUBTEXT}Puoi copiarci immagini/cartelle o usare qualsiasi percorso sul disco.${C_RESET}\n\n"
        return 0
    fi

    if (( ${#dirs[@]} > 0 )); then
        printf "  ${C_BOLD}${C_SKY}Cartelle (usabili con 'slide <id> <nome>' o '-d <nome>'):${C_RESET}\n"
        local d count
        for d in "${dirs[@]}"; do
            count="$(find -L "${d}" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' -o -iname '*.gif' \) | wc -l)"
            printf "    ${C_PEACH}📁 %-28s${C_RESET} ${C_SUBTEXT}(%s immagini)${C_RESET}\n" "$(basename "${d}")" "${count}"
        done
        echo
    fi

    if (( ${#files[@]} > 0 )); then
        printf "  ${C_BOLD}${C_SKY}Immagini (usabili con 'img <id> <nome>' o '-i <nome>'):${C_RESET}\n"
        local f rel
        for f in "${files[@]}"; do
            rel="${f#${WALL_DIR}/}"
            printf "    ${C_GREEN}🖼  %s${C_RESET}\n" "${rel}"
        done
        echo
    fi
}

# ==============================================================================
# 5. MOTORE TUI REAL-TIME DINAMICO & RENDERER SPAZIALE 2D (PYTHON EMBEDDED)
# ==============================================================================
# Si adatta istantaneamente (SIGWINCH + 25fps polling) al ridimensionamento
# della finestra in Niri senza perdere l'input parziale nella Command Box.
run_tui_or_map() {
    local mode="${1:-tui}" # "tui" oppure "map"
    ensure_wayland_env
    python3 - "$0" "${mode}" "${STATE_DIR}" "${PID_DIR}" "${WALL_DIR}" "${PROF_DIR}" "${HIST_FILE}" "${VERSION}" << 'PYTUI'
import sys, os, json, math, shutil, signal, select, subprocess, time, termios, tty, shlex, glob

SCRIPT_PATH, RUN_MODE, STATE_DIR, PID_DIR, WALL_DIR, PROF_DIR, HIST_FILE, VERSION = sys.argv[1:9]

# Palette Cyberpunk Pastello (TrueColor ANSI)
RESET    = "\033[0m"
BOLD     = "\033[1m"
DIM      = "\033[2m"
LAVENDER = "\033[38;2;180;190;254m"
MAUVE    = "\033[38;2;203;166;247m"
BLUE     = "\033[38;2;137;180;250m"
SKY      = "\033[38;2;137;220;235m"
TEAL     = "\033[38;2;148;226;213m"
GREEN    = "\033[38;2;166;227;161m"
PEACH    = "\033[38;2;250;179;135m"
YELLOW   = "\033[38;2;249;226;175m"
RED      = "\033[38;2;243;139;168m"
TEXT     = "\033[38;2;205;214;244m"
SUBTEXT  = "\033[38;2;166;173;200m"
MUTED    = "\033[38;2;108;112;134m"

PALETTE = [SKY, MAUVE, GREEN, PEACH, YELLOW, RED]

def pid_alive(pid_path):
    if not os.path.isfile(pid_path):
        return False
    try:
        with open(pid_path, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def query_system_state():
    try:
        raw_out = subprocess.check_output(["niri", "msg", "-j", "outputs"], stderr=subprocess.DEVNULL).decode("utf-8", "replace")
        data = json.loads(raw_out)
    except Exception:
        data = {}

    outputs = []
    for k, v in data.items():
        if v and v.get("logical") is not None:
            outputs.append(v)
    outputs.sort(key=lambda o: (o["logical"]["x"], o["logical"]["y"], o.get("name", "")))
    for idx, o in enumerate(outputs):
        o["id"] = idx + 1

    try:
        awww_raw = subprocess.check_output(["awww", "query"], stderr=subprocess.DEVNULL).decode("utf-8", "replace")
    except Exception:
        awww_raw = ""

    awww_map = {}
    for line in awww_raw.splitlines():
        line = line.strip().lstrip(":").strip()
        if ":" in line and "currently displaying:" in line:
            out_name = line.split(":", 1)[0].strip()
            disp = line.split("currently displaying:", 1)[1].strip()
            awww_map[out_name] = disp

    return outputs, awww_map

def extract_res_info(o):
    modes = o.get("modes") or []
    cur_idx = o.get("current_mode")
    cur_w = o["logical"]["width"]
    cur_h = o["logical"]["height"]
    cur_hz = 0.0

    if cur_idx is not None and 0 <= cur_idx < len(modes):
        cm = modes[cur_idx]
        cur_w = cm.get("width", cur_w)
        cur_h = cm.get("height", cur_h)
        rr = cm.get("refresh_rate", 0)
        cur_hz = rr / 1000.0 if rr > 1000 else float(rr)

    max_w, max_h, max_hz = cur_w, cur_h, cur_hz
    if modes:
        best = max(modes, key=lambda m: (m.get("width", 0) * m.get("height", 0), m.get("width", 0), m.get("refresh_rate", 0)))
        max_w = best.get("width", cur_w)
        max_h = best.get("height", cur_h)
        br = best.get("refresh_rate", 0)
        max_hz = br / 1000.0 if br > 1000 else float(br)

    cur_str = f"{cur_w}x{cur_h}@{cur_hz:.0f}Hz" if cur_hz > 0 else f"{cur_w}x{cur_h}"
    max_str = f"{max_w}x{max_h}@{max_hz:.0f}Hz" if max_hz > 0 else f"{max_w}x{max_h}"
    return cur_str, max_str

def get_screen_status(name, awww_map):
    state_file = os.path.join(STATE_DIR, f"{name}.json")
    cm_alive = pid_alive(os.path.join(PID_DIR, f"ncmatrix_{name}.pid")) or pid_alive(os.path.join(PID_DIR, f"cmatrix_{name}.pid"))
    ss_alive = pid_alive(os.path.join(PID_DIR, f"slideshow_{name}.pid"))
    paused   = os.path.isfile(os.path.join(PID_DIR, f"pause_{name}.flag"))

    if os.path.isfile(state_file):
        try:
            with open(state_file, "r") as f:
                st = json.load(f)
            mode = st.get("mode", "")
            if mode in ("ncmatrix", "cmatrix") and cm_alive:
                jp = " JP" if st.get("japanese") == "true" else ""
                col = st.get("color", "green")
                spd = st.get("speed", "4")
                return f"NCMatrix{jp} ({col})", f"NCMatrix{jp} [{col}, v:{spd}]"
            elif mode == "slideshow" and ss_alive:
                folder = os.path.basename(st.get("target", "").rstrip("/"))
                cur = st.get("current_image", "")
                p_str = " [PAUSA]" if paused else ""
                sub = f"Slide: {folder}/ ({st.get('interval','5m')}){p_str}"
                detail = f"{sub} -> {cur}" if cur else sub
                return sub, detail
            elif mode == "solid":
                hx = st.get("target", "#18181a")
                return f"Unicolor: {hx}", f"Sfondo uniforme: {hx}"
            elif mode == "image":
                img_name = os.path.basename(st.get("target", ""))
                fit = st.get("fit", "fill")
                return f"Img: {img_name} [{fit}]", f"Img: {img_name} [{fit}]"
        except Exception:
            pass

    raw = awww_map.get(name, "Nessuno sfondo")
    if "image:" in raw:
        p = raw.split("image:", 1)[1].strip()
        return f"Img: {os.path.basename(p)}", f"Img: {os.path.basename(p)}"
    return raw, raw

def build_frame(term_cols, term_rows, outputs, awww_map, input_buf="", cursor_pos=0, status_msg="", status_is_err=False, interactive=True):
    # Larghezza utile dinamica adattata in tempo reale alla finestra
    ui_w = max(54, term_cols - 4)
    inner_ui_w = ui_w - 2
    lines_out = []

    def box_top(title="", color=MAUVE):
        if title:
            t_str = f"─ {title} "
            rem = max(0, inner_ui_w - len(t_str))
            return f"  {BOLD}{color}╭{t_str}{'─' * rem}╮{RESET}"
        return f"  {BOLD}{color}╭{'─' * inner_ui_w}╮{RESET}"

    def box_bot(color=MAUVE):
        return f"  {BOLD}{color}╰{'─' * inner_ui_w}╯{RESET}"

    def h_sep():
        return f"  {MUTED}{'─' * ui_w}{RESET}"

    # Header principale
    lines_out.append(box_top("", MAUVE))
    hdr_left = f"  NC-DESKTOP v{VERSION} — TOPOLOGIA DISPLAY & SFONDI (Niri Wayland)"
    hdr_right = f"[{term_cols}x{term_rows}]  "
    pad_len = max(1, inner_ui_w - len(hdr_left) - len(hdr_right))
    if len(hdr_left) + len(hdr_right) > inner_ui_w:
        hdr_line = hdr_left[:inner_ui_w].ljust(inner_ui_w)
        lines_out.append(f"  {BOLD}{MAUVE}│{RESET}{BOLD}{TEXT}{hdr_line}{RESET}{BOLD}{MAUVE}│{RESET}")
    else:
        lines_out.append(f"  {BOLD}{MAUVE}│{RESET}{BOLD}{TEXT}{hdr_left}{RESET}{' ' * pad_len}{MUTED}{hdr_right}{RESET}{BOLD}{MAUVE}│{RESET}")
    lines_out.append(box_bot(MAUVE))

    if not outputs:
        lines_out.append(f"  {RED}[✗] Nessuno schermo attivo rilevato da Niri.{RESET}")
        return lines_out, (1, 1)

    # Calcolo geometria spaziale 2D adattiva (sia in larghezza sia in altezza)
    min_x = min(o["logical"]["x"] for o in outputs)
    min_y = min(o["logical"]["y"] for o in outputs)
    max_x = max(o["logical"]["x"] + o["logical"]["width"] for o in outputs)
    max_y = max(o["logical"]["y"] + o["logical"]["height"] for o in outputs)

    total_w = max(1, max_x - min_x)
    total_h = max(1, max_y - min_y)

    # Riserva righe per tabella stato, legenda e command box in modalità interattiva
    reserved_rows = (15 + len(outputs)) if interactive else (6 + len(outputs))
    avail_map_h = max(7, term_rows - reserved_rows)

    max_canvas_w = max(48, ui_w - 4)
    scale_x = max_canvas_w / float(total_w)
    scale_y = min(scale_x * 0.42, avail_map_h / float(max(1, total_h)))

    min_box_w = max(24, min(34, max_canvas_w // max(1, len(outputs))))
    min_box_h = 7 if avail_map_h >= 8 else 6

    boxes = []
    for idx, o in enumerate(outputs):
        lx = o["logical"]["x"] - min_x
        ly = o["logical"]["y"] - min_y
        lw = o["logical"]["width"]
        lh = o["logical"]["height"]

        bx = int(round(lx * scale_x))
        by = int(round(ly * scale_y))
        bw = max(min_box_w, int(round(lw * scale_x)))
        bh = max(min_box_h, int(round(lh * scale_y)))

        cur_res, max_res = extract_res_info(o)
        short_st, full_st = get_screen_status(o["name"], awww_map)

        boxes.append({
            "id": o["id"],
            "name": o["name"],
            "model": o.get("model", "Display"),
            "lx": o["logical"]["x"],
            "ly": o["logical"]["y"],
            "lw": lw,
            "lh": lh,
            "scale": o["logical"].get("scale", 1.0),
            "cur_res": cur_res,
            "max_res": max_res,
            "short_st": short_st,
            "full_st": full_st,
            "bx": bx, "by": by, "bw": bw, "bh": bh,
            "color": PALETTE[idx % len(PALETTE)]
        })

    # Prevenzione collisioni tra rettangoli
    boxes.sort(key=lambda b: (b["by"], b["bx"]))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            ox = (a["bx"] < b["bx"] + b["bw"]) and (b["bx"] < a["bx"] + a["bw"])
            oy = (a["by"] < b["by"] + b["bh"]) and (b["by"] < a["by"] + a["bh"])
            if ox and oy:
                if (b["lx"] - a["lx"]) >= (b["ly"] - a["ly"]):
                    b["bx"] = a["bx"] + a["bw"] + 1
                else:
                    b["by"] = a["by"] + a["bh"]

    # Se i box superano la larghezza disponibile del terminale, riscala proporzionalmente
    actual_span_w = max(b["bx"] + b["bw"] for b in boxes)
    if actual_span_w > max_canvas_w and actual_span_w > 0:
        shrink = max_canvas_w / float(actual_span_w)
        for b in boxes:
            b["bx"] = int(b["bx"] * shrink)
            b["bw"] = max(22, int(b["bw"] * shrink))
        for i in range(len(boxes) - 1):
            if boxes[i]["by"] == boxes[i+1]["by"] and boxes[i]["bx"] + boxes[i]["bw"] > boxes[i+1]["bx"]:
                boxes[i+1]["bx"] = boxes[i]["bx"] + boxes[i]["bw"] + 1

    canvas_w = max(b["bx"] + b["bw"] for b in boxes) + 1
    canvas_h = max(b["by"] + b["bh"] for b in boxes)

    grid_char  = [[" " for _ in range(canvas_w)] for _ in range(canvas_h)]
    grid_color = [[""  for _ in range(canvas_w)] for _ in range(canvas_h)]

    def put_text(r, c, text, col, max_len):
        text = text[:max_len]
        for k, ch in enumerate(text):
            if 0 <= r < canvas_h and 0 <= c + k < canvas_w:
                grid_char[r][c + k] = ch
                grid_color[r][c + k] = col

    for b in boxes:
        x, y, w, h = b["bx"], b["by"], b["bw"], b["bh"]
        col = b["color"]

        grid_char[y][x] = "╭";         grid_color[y][x] = BOLD + col
        grid_char[y][x+w-1] = "╮";     grid_color[y][x+w-1] = BOLD + col
        grid_char[y+h-1][x] = "╰";     grid_color[y+h-1][x] = BOLD + col
        grid_char[y+h-1][x+w-1] = "╯"; grid_color[y+h-1][x+w-1] = BOLD + col

        for cx in range(x + 1, x + w - 1):
            grid_char[y][cx] = "─";     grid_color[y][cx] = BOLD + col
            grid_char[y+h-1][cx] = "─"; grid_color[y+h-1][cx] = BOLD + col
        for cy in range(y + 1, y + h - 1):
            grid_char[cy][x] = "│";     grid_color[cy][x] = BOLD + col
            grid_char[cy][x+w-1] = "│"; grid_color[cy][x+w-1] = BOLD + col

        inner_w = max(1, w - 4)
        gcd_v = math.gcd(int(b["lw"]), int(b["lh"]))
        ar_w, ar_h = int(b["lw"]) // gcd_v, int(b["lh"]) // gcd_v
        ar_map = {(64, 27): "21:9", (32, 9): "32:9", (16, 9): "16:9", (16, 10): "16:10"}
        ar_str = ar_map.get((ar_w, ar_h), f"{ar_w}:{ar_h}")

        # Contenuto del box schermo con Risoluzione Attuale + Risoluzione Massima suggerita appena sotto
        box_lines = [
            (f"[{b['id']}] {b['name']} ({ar_str}) {b['model']}", BOLD + col),
            (f"Ora: {b['cur_res']} ({b['lx']},{b['ly']})", TEXT),
            (f"Max: {b['max_res']} [s:{b['scale']:g}x]", PEACH),
            (f"{b['short_st']}", col),
        ]

        start_row = y + max(1, (h - len(box_lines)) // 2)
        for idx_l, (txt, lcol) in enumerate(box_lines):
            row = start_row + idx_l
            if row >= y + h - 1:
                break
            trimmed = txt[:inner_w]
            pad_left = x + 2 + max(0, (inner_w - len(trimmed)) // 2)
            put_text(row, pad_left, trimmed, lcol, inner_w)

    # Centratura orizzontale della mappa rispetto alla larghezza corrente della finestra
    left_margin = max(2, (ui_w - canvas_w) // 2 + 2)
    margin_str = " " * left_margin
    for r in range(canvas_h):
        row_buf = [margin_str]
        for c in range(canvas_w):
            ch = grid_char[r][c]
            col = grid_color[r][c]
            row_buf.append(f"{col}{ch}{RESET}" if col else ch)
        lines_out.append("".join(row_buf).rstrip())

    # Riepilogo tabellare dinamico
    lines_out.append(h_sep())
    for b in sorted(boxes, key=lambda item: item["id"]):
        col = b["color"]
        left_info = f" [{b['id']}] {b['name']:<5} │ {b['cur_res']:<15} (Max: {b['max_res']}) │ "
        avail_st = max(10, ui_w - len(left_info) - 2)
        st_trim = b["full_st"][:avail_st]
        lines_out.append(
            f"  {BOLD}{col}[{b['id']}] {b['name']:<5}{RESET} {MUTED}│{RESET} "
            f"{TEXT}{b['cur_res']:<14}{RESET} {DIM}{PEACH}(Max: {b['max_res']}){RESET} {MUTED}│{RESET} "
            f"{col}{st_trim}{RESET}"
        )
    lines_out.append(h_sep())

    if not interactive:
        return lines_out, (1, 1)

    # Legenda Comandi adattiva alla larghezza della finestra
    lines_out.append(box_top("LEGENDA COMANDI", SKY))
    legend_rows = [
        (f" {GREEN}img{RESET}      {PEACH}<id|all> <file>{RESET} {SUBTEXT}[fit] [trans]{RESET} {MUTED}│{RESET} {GREEN}res{RESET}   {PEACH}<id> <WxH@Hz|max>{RESET} {SUBTEXT}(cambia risoluz.){RESET}",
         f" img      <id|all> <file> [fit] [trans] │ res   <id> <WxH@Hz|max> (cambia risoluz.)"),
        (f" {GREEN}slide{RESET}    {PEACH}<id|all> <dir>{RESET}  {SUBTEXT}[tempo] [fit]{RESET} {MUTED}│{RESET} {GREEN}scale{RESET} {PEACH}<id> <1|1.25|1.5>{RESET} {MUTED}│{RESET} {GREEN}modes{RESET} {PEACH}<id>{RESET}",
         f" slide    <id|all> <dir>  [tempo] [fit] │ scale <id> <1|1.25|1.5> │ modes <id>"),
        (f" {GREEN}matrix{RESET}   {PEACH}<id|all>{RESET} {SUBTEXT}[col] [0-9] [jp]{RESET}     {MUTED}│{RESET} {GREEN}rand{RESET}  {PEACH}[id] [dir]{RESET} {MUTED}│{RESET} {GREEN}unicolor{RESET} {PEACH}<id> <col|hex>{RESET}",
         f" matrix   <id|all> [col] [0-9] [jp]     │ rand  [id] [dir] │ unicolor <id> <col|hex>"),
        (f" {GREEN}swap{RESET} {PEACH}<1> <2>{RESET} │ {GREEN}fav{RESET} {PEACH}<id>{RESET} │ {GREEN}next/pause/resume/stop{RESET} {SUBTEXT}[id]{RESET} │ {GREEN}list{RESET} │ {GREEN}save/load{RESET} {PEACH}<p>{RESET} │ {RED}q{RESET}",
         f" swap <1> <2> │ fav <id> │ next/pause/resume/stop [id] │ list │ save/load <p> │ q"),
    ]
    for colored_txt, plain_txt in legend_rows:
        if len(plain_txt) <= inner_ui_w:
            pad = " " * (inner_ui_w - len(plain_txt))
            lines_out.append(f"  {BOLD}{SKY}│{RESET}{colored_txt}{pad}{BOLD}{SKY}│{RESET}")
        else:
            trimmed_plain = plain_txt[:inner_ui_w]
            lines_out.append(f"  {BOLD}{SKY}│{RESET}{TEXT}{trimmed_plain.ljust(inner_ui_w)}{RESET}{BOLD}{SKY}│{RESET}")
    lines_out.append(box_bot(SKY))

    # Command Box dinamica
    lines_out.append(box_top("COMANDO", LAVENDER))
    prompt_prefix = " ❯ "
    max_inp_vis = max(10, inner_ui_w - len(prompt_prefix) - 1)

    # Scorrimento orizzontale se il comando digitato è più lungo della finestra
    scroll_off = 0
    if cursor_pos >= max_inp_vis:
        scroll_off = cursor_pos - max_inp_vis + 1
    vis_inp = input_buf[scroll_off:scroll_off + max_inp_vis]
    pad_inp = " " * max(0, inner_ui_w - len(prompt_prefix) - len(vis_inp))

    cmd_row_idx = len(lines_out) + 1 # Riga 1-based nel terminale
    cmd_col_idx = 2 + 1 + len(prompt_prefix) + (cursor_pos - scroll_off) + 1

    lines_out.append(f"  {BOLD}{LAVENDER}│{RESET}{BOLD}{PEACH}{prompt_prefix}{RESET}{TEXT}{vis_inp}{RESET}{pad_inp}{BOLD}{LAVENDER}│{RESET}")
    lines_out.append(box_bot(LAVENDER))

    # Barra di stato / Flash errore appena sotto la Command Box
    if status_msg:
        if status_is_err:
            msg_trim = status_msg[:max(20, ui_w - 14)]
            lines_out.append(f"  {BOLD}{RED}[✗] ERRORE:{RESET} {RED}{msg_trim}{RESET}")
        else:
            msg_trim = status_msg[:max(20, ui_w - 6)]
            lines_out.append(f"  {BOLD}{GREEN}[✓]{RESET} {TEXT}{msg_trim}{RESET}")
    else:
        hint = "Digita un comando (TAB completa path/comandi, ↑/↓ cronologia, 'q' esce) — Resize finestra Real-Time attivo"
        lines_out.append(f"  {MUTED}{hint[:ui_w]}{RESET}")

    return lines_out, (cmd_row_idx, cmd_col_idx)

# Modalità sola stampa mappa (-m / --map)
if RUN_MODE == "map":
    cols, rows = shutil.get_terminal_size((100, 34))
    outputs, awww_map = query_system_state()
    frame, _ = build_frame(cols, rows, outputs, awww_map, interactive=False)
    print("\n" + "\n".join(frame) + "\n")
    sys.exit(0)

# --- Loop Interattivo TUI con SIGWINCH Real-Time ------------------------------
resize_pending = False
def on_sigwinch(signum, frame):
    global resize_pending
    resize_pending = True

signal.signal(signal.SIGWINCH, on_sigwinch)

def load_history():
    if not os.path.isfile(HIST_FILE):
        return []
    try:
        with open(HIST_FILE, "r") as f:
            return [ln.strip() for ln in f if ln.strip()][-200:]
    except Exception:
        return []

def save_history(cmd_str, hist):
    if not cmd_str.strip():
        return
    if not hist or hist[-1] != cmd_str:
        hist.append(cmd_str)
    try:
        with open(HIST_FILE, "w") as f:
            for item in hist[-200:]:
                f.write(item + "\n")
    except Exception:
        pass

COMMANDS = [
    "img", "slide", "matrix", "res", "scale", "pos", "modes",
    "rand", "unicolor", "solid", "swap", "fav", "next", "pause", "resume",
    "stop", "list", "save", "load", "restore", "help", "q"
]

def tab_complete(buf, pos):
    head = buf[:pos]
    tail = buf[pos:]
    tokens = head.split(" ")
    cur_tok = tokens[-1]

    if len(tokens) <= 1:
        matches = [c + " " for c in COMMANDS if c.startswith(cur_tok.lower())]
    else:
        expanded = os.path.expanduser(cur_tok)
        if "/" in cur_tok or cur_tok.startswith("~") or cur_tok.startswith("."):
            candidates = glob.glob(expanded + "*")
            matches = []
            for c in sorted(candidates):
                suffix = "/" if os.path.isdir(c) else " "
                if cur_tok.startswith("~"):
                    home = os.path.expanduser("~")
                    if c.startswith(home):
                        c = "~" + c[len(home):]
                matches.append(c + suffix)
        else:
            # Cerca dentro ~/.local/nc-desktop/wallpapers o parole chiave
            wall_candidates = glob.glob(os.path.join(WALL_DIR, cur_tok + "*"))
            matches = []
            for c in sorted(wall_candidates):
                rel = os.path.relpath(c, WALL_DIR)
                matches.append(rel + ("/" if os.path.isdir(c) else " "))
            keywords = [
                "all", "max", "fill", "fit", "stretch", "center",
                "wipe", "wave", "grow", "fade", "random", "jp",
                "white", "black", "red", "green", "blue", "yellow",
                "cyan", "magenta", "gray", "orange", "purple", "pink",
                "teal", "cyber", "anthracite", "dark", "mocha",
                "lavender", "mauve", "peach", "sky"
            ]
            for kw in keywords:
                if kw.startswith(cur_tok.lower()):
                    matches.append(kw + " ")

    if len(matches) == 1:
        tokens[-1] = matches[0]
        new_head = " ".join(tokens)
        return new_head + tail, len(new_head)
    elif len(matches) > 1:
        common = os.path.commonprefix(matches)
        if len(common) > len(cur_tok):
            tokens[-1] = common
            new_head = " ".join(tokens)
            return new_head + tail, len(new_head)
    return buf, pos

def translate_box_command(line):
    try:
        args = shlex.split(line)
    except ValueError:
        return None, "Virgolette non bilanciate nel comando."

    if not args:
        return [], None

    cmd = args[0]
    if cmd.startswith("-"):
        return args, None

    c_low = cmd.lower()
    if c_low in ("q", "quit", "exit"):
        return ["__EXIT__"], None
    elif c_low in ("list", "ls"):
        return ["__PAGER__", "-l"], None
    elif c_low in ("help", "?"):
        return ["__PAGER__", "-h"], None
    elif c_low == "modes":
        sc = args[1] if len(args) > 1 else "all"
        return ["__PAGER__", "-o", sc, "--modes"], None
    elif c_low in ("img", "image"):
        if len(args) < 3:
            return None, "Sintassi: img <id|all> <file> [fill|fit|stretch|center] [transizione]"
        sc, im = args[1], args[2]
        ft = args[3] if len(args) > 3 else "fill"
        tr = args[4] if len(args) > 4 else "wipe"
        return ["-o", sc, "-i", im, "-f", ft, "-t", tr], None
    elif c_low in ("slide", "slideshow", "dir"):
        if len(args) < 3:
            return None, "Sintassi: slide <id|all> <cartella> [intervallo] [fit] [transizione]"
        sc, dr = args[1], args[2]
        iv = args[3] if len(args) > 3 else "5m"
        ft = args[4] if len(args) > 4 else "fill"
        tr = args[5] if len(args) > 5 else "random"
        return ["-o", sc, "-d", dr, "-n", iv, "-f", ft, "-t", tr], None
    elif c_low in ("matrix", "ncmatrix", "cmatrix"):
        if len(args) < 2:
            return None, "Sintassi: matrix <id|all> [colore] [velocità 0-9] [jp]"
        sc = args[1]
        col, spd, jp_flag = "green", "4", []
        for a in args[2:]:
            al = a.lower()
            if al in ("jp", "jap", "japanese", "-j", "--japanese"):
                jp_flag = ["-j"]
            elif len(al) == 1 and al.isdigit():
                spd = al
            else:
                col = al
        return ["-o", sc, "-c", "-C", col, "-u", spd] + jp_flag, None
    elif c_low in ("res", "resolution", "mode"):
        if len(args) < 3:
            return None, "Sintassi: res <id|porta> <WxH[@Hz]|max> (es. res 1 max oppure res 1 2560x1440@144)"
        return ["-o", args[1], "-r", args[2]], None
    elif c_low == "scale":
        if len(args) < 3:
            return None, "Sintassi: scale <id|porta> <fattore> (es. scale 1 1.25)"
        return ["-o", args[1], "--scale", args[2]], None
    elif c_low == "pos":
        if len(args) < 3:
            return None, "Sintassi: pos <id|porta> <X,Y|auto> (es. pos 2 2560,0)"
        return ["-o", args[1], "--pos", args[2]], None
    elif c_low in ("rand", "random"):
        sc = args[1] if len(args) > 1 else "all"
        dr = args[2] if len(args) > 2 else WALL_DIR
        return ["-o", sc, "--random", dr], None
    elif c_low in ("unicolor", "solid"):
        if len(args) < 3:
            return None, f"Sintassi: {c_low} <id|all> <colore|hex> (es. {c_low} all white oppure {c_low} 1 #18181a)"
        return ["-o", args[1], "--unicolor", args[2]], None
    elif c_low == "swap":
        if len(args) < 3:
            return None, "Sintassi: swap <schermo1> <schermo2> (es. swap 1 2)"
        return ["--swap", args[1], args[2]], None
    elif c_low == "fav":
        sc = args[1] if len(args) > 1 else "1"
        return ["-o", sc, "--fav"], None
    elif c_low in ("next", "pause", "resume", "stop"):
        sc = args[1] if len(args) > 1 else "all"
        return ["-o", sc, f"--{c_low}"], None
    elif c_low == "save":
        if len(args) < 2:
            return None, "Sintassi: save <nome_profilo>"
        return ["--save-profile", args[1]], None
    elif c_low == "load":
        if len(args) < 2:
            return None, "Sintassi: load <nome_profilo>"
        return ["--load-profile", args[1]], None
    elif c_low == "restore":
        return ["--restore"], None
    else:
        return None, f"Comando sconosciuto: '{cmd}'. Consulta la legenda o digita 'help'."

def strip_ansi(text):
    import re
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text).strip()

def run_interactive_loop():
    global resize_pending
    fd = os.open("/dev/tty", os.O_RDWR); sys.stdin = open(fd, "r", closefd=False)
    old_tc = termios.tcgetattr(fd)

    history = load_history()
    hist_idx = len(history)
    input_buf = ""
    cursor_pos = 0

    status_msg = ""
    status_is_err = False
    error_expire = 0.0

    last_cols, last_rows = shutil.get_terminal_size((100, 34))
    outputs, awww_map = query_system_state()
    last_state_poll = time.time()
    need_redraw = True

    sys.stdout.write("\033[?1049h\033[2J\033[H")
    sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        while True:
            now = time.time()
            if status_is_err and error_expire > 0 and now >= error_expire:
                status_msg = ""
                status_is_err = False
                error_expire = 0.0
                need_redraw = True

            cur_cols, cur_rows = shutil.get_terminal_size((100, 34))
            if resize_pending or (cur_cols, cur_rows) != (last_cols, last_rows):
                resize_pending = False
                last_cols, last_rows = cur_cols, cur_rows
                outputs, awww_map = query_system_state()
                need_redraw = True

            if now - last_state_poll >= 1.5:
                last_state_poll = now
                new_outputs, new_awww = query_system_state()
                if new_outputs != outputs or new_awww != awww_map:
                    outputs, awww_map = new_outputs, new_awww
                need_redraw = True

            if need_redraw:
                need_redraw = False
                frame_lines, (c_row, c_col) = build_frame(
                    last_cols, last_rows, outputs, awww_map,
                    input_buf, cursor_pos, status_msg, status_is_err, interactive=True
                )
                out_str = "\033[?25l\033[H"
                for ln in frame_lines[:last_rows]:
                    out_str += ln + "\033[K\r\n"
                out_str += "\033[J"
                target_r = min(last_rows, max(1, c_row))
                target_c = min(last_cols, max(1, c_col))
                out_str += f"\033[{target_r};{target_c}H\033[?25h"
                sys.stdout.write(out_str)
                sys.stdout.flush()

            rlist, _, _ = select.select([fd], [], [], 0.04)
            if not rlist:
                continue

            ch = os.read(fd, 1)
            if not ch:
                break

            # Sequenze Escape (Frecce, Home, End, Canc)
            if ch == b'\x1b':
                r2, _, _ = select.select([fd], [], [], 0.02)
                if r2:
                    seq = os.read(fd, 8)
                    if seq == b'[A': # Freccia Su
                        if history and hist_idx > 0:
                            hist_idx -= 1
                            input_buf = history[hist_idx]
                            cursor_pos = len(input_buf)
                            need_redraw = True
                    elif seq == b'[B': # Freccia Giù
                        if hist_idx < len(history) - 1:
                            hist_idx += 1
                            input_buf = history[hist_idx]
                            cursor_pos = len(input_buf)
                            need_redraw = True
                        elif hist_idx == len(history) - 1:
                            hist_idx = len(history)
                            input_buf = ""
                            cursor_pos = 0
                            need_redraw = True
                    elif seq == b'[C': # Freccia Destra
                        if cursor_pos < len(input_buf):
                            cursor_pos += 1
                            need_redraw = True
                    elif seq == b'[D': # Freccia Sinistra
                        if cursor_pos > 0:
                            cursor_pos -= 1
                            need_redraw = True
                    elif seq in (b'[H', b'[1~', b'OH'): # Home
                        cursor_pos = 0
                        need_redraw = True
                    elif seq in (b'[F', b'[4~', b'OF'): # End
                        cursor_pos = len(input_buf)
                        need_redraw = True
                    elif seq == b'[3~': # Delete
                        if cursor_pos < len(input_buf):
                            input_buf = input_buf[:cursor_pos] + input_buf[cursor_pos+1:]
                            need_redraw = True
                continue

            if ch in (b'\x03', b'\x04'): # Ctrl+C / Ctrl+D
                break
            elif ch == b'\x0c': # Ctrl+L (Forza redraw)
                sys.stdout.write("\033[2J\033[H")
                need_redraw = True
            elif ch == b'\x15': # Ctrl+U (Pulisci riga)
                input_buf = ""
                cursor_pos = 0
                need_redraw = True
            elif ch == b'\x17': # Ctrl+W (Cancella parola precedente)
                left = input_buf[:cursor_pos].rstrip(" ")
                idx_sp = left.rfind(" ")
                new_left = left[:idx_sp+1] if idx_sp != -1 else ""
                input_buf = new_left + input_buf[cursor_pos:]
                cursor_pos = len(new_left)
                need_redraw = True
            elif ch == b'\t': # TAB Completion
                input_buf, cursor_pos = tab_complete(input_buf, cursor_pos)
                need_redraw = True
            elif ch in (b'\x7f', b'\x08'): # Backspace
                if cursor_pos > 0:
                    input_buf = input_buf[:cursor_pos-1] + input_buf[cursor_pos:]
                    cursor_pos -= 1
                    need_redraw = True
            elif ch in (b'\r', b'\n'): # Invio: esegue il comando
                cmd_line = input_buf.strip()
                input_buf = ""
                cursor_pos = 0
                need_redraw = True

                if not cmd_line:
                    continue

                save_history(cmd_line, history)
                hist_idx = len(history)

                cli_args, err_parse = translate_box_command(cmd_line)
                if err_parse:
                    status_msg = err_parse
                    status_is_err = True
                    error_expire = time.time() + 3.2
                    continue

                if not cli_args:
                    continue
                if cli_args[0] == "__EXIT__":
                    break

                if cli_args[0] == "__PAGER__":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_tc)
                    sys.stdout.write("\033[2J\033[H")
                    sys.stdout.flush()
                    subprocess.call([SCRIPT_PATH] + cli_args[1:])
                    sys.stdout.write(f"\n  {MUTED}Premi [INVIO] per tornare alla dashboard...{RESET}")
                    sys.stdout.flush()
                    try:
                        sys.stdin.readline()
                    except Exception:
                        pass
                    tty.setcbreak(fd)
                    sys.stdout.write("\033[2J\033[H")
                    outputs, awww_map = query_system_state()
                    continue

                # Esecuzione comando CLI catturando output ed errori
                proc = subprocess.run([SCRIPT_PATH] + cli_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out_text = strip_ansi(proc.stdout.decode("utf-8", "replace"))
                err_text = strip_ansi(proc.stderr.decode("utf-8", "replace"))

                outputs, awww_map = query_system_state()
                last_state_poll = time.time()

                if proc.returncode != 0:
                    msg = err_text if err_text else (out_text if out_text else "Errore durante l'esecuzione del comando.")
                    msg = msg.replace("[✗]", "").replace("[!]", "").strip()
                    status_msg = msg
                    status_is_err = True
                    error_expire = time.time() + 3.2
                else:
                    msg = out_text.splitlines()[-1] if out_text else "Comando applicato con successo."
                    msg = msg.replace("[✓]", "").strip()
                    status_msg = msg
                    status_is_err = False
                    error_expire = 0.0
            else:
                # Caratteri UTF-8 multi-byte
                b_arr = bytearray(ch)
                if ch[0] >= 0xC0:
                    while True:
                        r_utf, _, _ = select.select([fd], [], [], 0.005)
                        if not r_utf:
                            break
                        nxt = os.read(fd, 1)
                        if not nxt:
                            break
                        b_arr.extend(nxt)
                        try:
                            b_arr.decode("utf-8")
                            break
                        except UnicodeDecodeError:
                            if len(b_arr) >= 4:
                                break
                try:
                    char_str = b_arr.decode("utf-8")
                    if char_str.isprintable():
                        input_buf = input_buf[:cursor_pos] + char_str + input_buf[cursor_pos:]
                        cursor_pos += len(char_str)
                        need_redraw = True
                except Exception:
                    pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_tc)
        sys.stdout.write("\033[?1049l\033[?25h")
        sys.stdout.flush()

run_interactive_loop()
PYTUI
}

# ==============================================================================
# 6. HELPER & PARSER UNIFICATO CLI
# ==============================================================================
show_help() {
    cat << EOF

${C_BOLD}${C_MAUVE}╭──────────────────────────────────────────────────────────────────────────────╮${C_RESET}
${C_BOLD}${C_MAUVE}│${C_RESET}  ${C_BOLD}${C_LAVENDER}NC-DESKTOP${C_RESET} ${C_MUTED}v${VERSION}${C_RESET} — ${C_TEXT}Gestore Sfondi, Display & Matrix per Niri Wayland${C_RESET}       ${C_BOLD}${C_MAUVE}│${C_RESET}
${C_BOLD}${C_MAUVE}╰──────────────────────────────────────────────────────────────────────────────╯${C_RESET}

  ${C_BOLD}${C_SKY}UTILIZZO:${C_RESET}
    ${C_TEXT}${SCRIPT_NAME}${C_RESET}                              ${C_SUBTEXT}Avvia la Dashboard TUI Reattiva Real-Time (default)${C_RESET}
    ${C_TEXT}${SCRIPT_NAME}${C_RESET} ${C_SUBTEXT}[OPZIONI CLI]${C_RESET}                ${C_SUBTEXT}Esegue un comando diretto da terminale o script${C_RESET}
    ${C_TEXT}${SCRIPT_NAME}${C_RESET} ${C_GREEN}unicolor${C_RESET} ${C_PEACH}<id|all> <col|hex>${C_RESET}  ${C_SUBTEXT}Applica subito un colore uniforme (es. unicolor all white)${C_RESET}

  ${C_BOLD}${C_SKY}INTERFACCIA E STATO:${C_RESET}
    ${C_GREEN}-I, --interactive${C_RESET}          Avvia la dashboard interattiva con resize real-time
    ${C_GREEN}-m, --map${C_RESET}                  Stampa la mappa 2D degli schermi (con Max Res) ed esce
    ${C_GREEN}-l, --list${C_RESET}                 Elenca immagini e cartelle in ${C_DIM}~/.local/nc-desktop/wallpapers${C_RESET}
    ${C_GREEN}-h, --help${C_RESET}                 Mostra questa guida e termina

  ${C_BOLD}${C_SKY}SELEZIONE E CONTROLLO DISPLAY (NIRI):${C_RESET}
    ${C_GREEN}-o, --output${C_RESET} ${C_PEACH}<id|nome|all>${C_RESET}  Schermo target: numero (${C_PEACH}1${C_RESET}, ${C_PEACH}2${C_RESET}), porta (${C_PEACH}DP-1${C_RESET}, ${C_PEACH}DP-3${C_RESET}) o ${C_PEACH}all${C_RESET}
    ${C_GREEN}-r, --res${C_RESET} ${C_PEACH}<WxH[@Hz]|max>${C_RESET}    Cambia risoluzione/refresh rate o imposta il massimo (${C_PEACH}max${C_RESET})
    ${C_GREEN}    --scale${C_RESET} ${C_PEACH}<fattore>${C_RESET}       Cambia il fattore di scala dello schermo (es. ${C_PEACH}1${C_RESET}, ${C_PEACH}1.25${C_RESET}, ${C_PEACH}1.5${C_RESET})
    ${C_GREEN}    --pos${C_RESET} ${C_PEACH}<X,Y|auto>${C_RESET}        Sposta le coordinate logiche dello schermo in Niri
    ${C_GREEN}    --modes${C_RESET}                Elenca tutte le risoluzioni e frequenze supportate dal monitor

  ${C_BOLD}${C_SKY}MODALITÀ SFONDO:${C_RESET}
    ${C_GREEN}-i, --image${C_RESET} ${C_PEACH}<file>${C_RESET}          Imposta un'immagine statica
    ${C_GREEN}-d, --dir${C_RESET} ${C_PEACH}<cartella>${C_RESET}        Avvia una slideshow indipendente dalla cartella indicata
    ${C_GREEN}-c, --ncmatrix${C_RESET}             Imposta ${C_TEAL}ncmatrix${C_RESET} in background nativo Wayland sullo schermo
    ${C_GREEN}    --random${C_RESET} ${C_PEACH}[cartella]${C_RESET}     Applica subito uno sfondo casuale
    ${C_GREEN}    --unicolor${C_RESET} ${C_PEACH}<col|hex>${C_RESET}    Applica un colore uniforme (es. ${C_PEACH}white${C_RESET}, ${C_PEACH}black${C_RESET}, ${C_PEACH}#18181a${C_RESET}, ${C_PEACH}cyber${C_RESET})
    ${C_GREEN}    --solid${C_RESET} ${C_PEACH}<col|hex>${C_RESET}       Alias di ${C_GREEN}--unicolor${C_RESET}
    ${C_GREEN}    --swap${C_RESET} ${C_PEACH}<id1> <id2>${C_RESET}      Scambia istantaneamente gli sfondi tra due monitor
    ${C_GREEN}    --fav${C_RESET}                  Salva lo sfondo corrente dello schermo nella cartella ${C_PEACH}favorites/${C_RESET}

  ${C_BOLD}${C_SKY}OPZIONI IMMAGINE, SLIDESHOW E NCMATRIX:${C_RESET}
    ${C_GREEN}-f, --fit${C_RESET} ${C_PEACH}<modo>${C_RESET}            Riempimento: ${C_PEACH}fill${C_RESET} (crop), ${C_PEACH}fit${C_RESET}, ${C_PEACH}stretch${C_RESET}, ${C_PEACH}center${C_RESET} ${C_MUTED}[Default: fill]${C_RESET}
    ${C_GREEN}-t, --transition${C_RESET} ${C_PEACH}<tipo>${C_RESET}     Transizione: ${C_PEACH}wipe${C_RESET}, ${C_PEACH}wave${C_RESET}, ${C_PEACH}grow${C_RESET}, ${C_PEACH}outer${C_RESET}, ${C_PEACH}fade${C_RESET}, ${C_PEACH}simple${C_RESET}, ${C_PEACH}random${C_RESET}, ${C_PEACH}none${C_RESET}
    ${C_GREEN}-n, --interval${C_RESET} ${C_PEACH}<tempo>${C_RESET}      Intervallo slideshow (${C_PEACH}30s${C_RESET}, ${C_PEACH}5m${C_RESET}, ${C_PEACH}1h${C_RESET}) ${C_MUTED}[Default: 5m]${C_RESET}
    ${C_GREEN}-j, --japanese${C_RESET}             Usa caratteri giapponesi (Katakana + glifi Matrix) in NCMatrix
    ${C_GREEN}-C, --color${C_RESET} ${C_PEACH}<colore>${C_RESET}        Colore Matrix: ${C_PEACH}green${C_RESET}, ${C_PEACH}cyan${C_RESET}, ${C_PEACH}blue${C_RESET}, ${C_PEACH}red${C_RESET}, ${C_PEACH}magenta${C_RESET}, ${C_PEACH}yellow${C_RESET}, ${C_PEACH}white${C_RESET}
    ${C_GREEN}-u, --speed${C_RESET} ${C_PEACH}<0-9>${C_RESET}           Velocità caduta codice Matrix (0=max, 9=lento) ${C_MUTED}[Default: 4]${C_RESET}

  ${C_BOLD}${C_SKY}CONTROLLO DEMONI E PROFILI:${C_RESET}
    ${C_GREEN}-N, --next${C_RESET}  │ ${C_GREEN}-P, --pause${C_RESET}  │ ${C_GREEN}--resume${C_RESET}  │ ${C_GREEN}-x, --stop${C_RESET}  │ ${C_GREEN}-R, --restore${C_RESET}
    ${C_GREEN}--save-profile${C_RESET} ${C_PEACH}<nome>${C_RESET}      │ ${C_GREEN}--load-profile${C_RESET} ${C_PEACH}<nome>${C_RESET}

EOF
}

execute_cli_args() {
    local action=""
    local target_output="all"
    local image_arg=""
    local dir_arg=""
    local fit_mode="fill"
    local transition="wipe"
    local interval="5m"
    local align_mode="center"
    local bg_color="18181a"
    local shuffle="true"
    local cm_color="green"
    local cm_speed="4"
    local cm_japanese="false"
    local profile_name=""
    local res_arg=""
    local scale_arg=""
    local pos_arg=""
    local solid_arg=""
    local swap_a=""
    local swap_b=""

    while (( $# > 0 )); do
        case "${1}" in
            -h|--help)         show_help; return 0 ;;
            -m|--map|-s|--status) action="map"; shift ;;
            -I|--interactive)  action="interactive"; shift ;;
            -l|--list)         action="list"; shift ;;
            -o|--output|--screen)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede lo schermo (1, 2, DP-1, all)."; return 1; }
                target_output="${2}"; shift 2 ;;
            -r|--res|--resolution|--mode)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede WxH[@Hz] oppure 'max'."; return 1; }
                action="res"; res_arg="${2}"; shift 2 ;;
            --scale)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '--scale' richiede un fattore (es. 1, 1.25)."; return 1; }
                action="scale"; scale_arg="${2}"; shift 2 ;;
            --pos|--position)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '--pos' richiede X,Y oppure 'auto'."; return 1; }
                action="pos"; pos_arg="${2}"; shift 2 ;;
            --modes)
                action="modes"; shift ;;
            -i|--image)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede il percorso di un'immagine."; return 1; }
                action="image"; image_arg="${2}"; shift 2 ;;
            -d|--dir|--slideshow)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede il percorso di una cartella."; return 1; }
                action="slideshow"; dir_arg="${2}"; shift 2 ;;
            --random|--rand)
                action="random"
                if [[ -n "${2:-}" && "${2}" != -* ]]; then
                    dir_arg="${2}"; shift 2
                else
                    dir_arg="${WALL_DIR}"; shift
                fi ;;
            --unicolor|--solid)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede un colore o hex (es. white, #18181a, cyber)."; return 1; }
                action="solid"; solid_arg="${2}"; shift 2 ;;
            unicolor|solid)
                [[ -z "${2:-}" || -z "${3:-}" ]] && { msg_err "Sintassi: ${1} <id|nome|all> <colore|hex> (es. ${1} all white)"; return 1; }
                action="solid"; target_output="${2}"; solid_arg="${3}"; shift 3 ;;
            --swap)
                [[ -z "${2:-}" || -z "${3:-}" ]] && { msg_err "L'opzione '--swap' richiede due schermi (es. --swap 1 2)."; return 1; }
                action="swap"; swap_a="${2}"; swap_b="${3}"; shift 3 ;;
            --fav|--favorite)
                action="fav"; shift ;;
            -c|--ncmatrix|--cmatrix)
                action="ncmatrix"; shift ;;
            -j|--japanese|--jp)
                cm_japanese="true"
                [[ -z "${action}" ]] && action="ncmatrix"
                shift ;;
            -f|--fit)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede una modalità (fill, fit, stretch, center)."; return 1; }
                fit_mode="${2}"; shift 2 ;;
            --align|--gravity)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede una posizione (top, center, bottom)."; return 1; }
                case "${2,,}" in
                    top|center|bottom) align_mode="${2,,}" ;;
                    *) msg_err "Allineamento '${2}' non valido (top, center, bottom)."; return 1 ;;
                esac
                shift 2 ;;
            -t|--transition)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede un tipo di transizione."; return 1; }
                transition="$(validate_transition "${2}")" || return 1; shift 2 ;;
            -n|--interval)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede un intervallo (es. 30s, 5m)."; return 1; }
                interval="${2}"; shift 2 ;;
            -b|--bg-color)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede un codice colore esadecimale."; return 1; }
                bg_color="${2#\#}"; shift 2 ;;
            --no-shuffle)
                shuffle="false"; shift ;;
            -C|--color)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede un colore."; return 1; }
                cm_color="$(validate_ncmatrix_color "${2}")" || return 1; shift 2 ;;
            -u|--speed)
                [[ -z "${2:-}" || ! "${2}" =~ ^[0-9]$ ]] && { msg_err "La velocità (-u/--speed) deve essere tra 0 e 9."; return 1; }
                cm_speed="${2}"; shift 2 ;;
            -N|--next)    action="next"; shift ;;
            -P|--pause)   action="pause"; shift ;;
            --resume)     action="resume"; shift ;;
            -x|--stop)    action="stop"; shift ;;
            -R|--restore) action="restore"; shift ;;
            --save-profile)
                [[ -z "${2:-}" ]] && { msg_err "Specifica il nome del profilo da salvare."; return 1; }
                action="save-profile"; profile_name="${2}"; shift 2 ;;
            --load-profile)
                [[ -z "${2:-}" ]] && { msg_err "Specifica il nome del profilo da caricare."; return 1; }
                action="load-profile"; profile_name="${2}"; shift 2 ;;
            *)
                msg_err "Opzione sconosciuta: '${1}'. Usa '--help' per la sintassi."
                return 1 ;;
        esac
    done

    case "${action}" in
        ""|"interactive") run_tui_or_map "tui" ;;
        "map")            run_tui_or_map "map" ;;
        "list")           cmd_list_wallpapers ;;
        "modes")          cmd_list_modes "${target_output}" ;;
        "res")
            local outs out
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_set_resolution "${out}" "${res_arg}" || return 1; }
            done <<< "${outs}" ;;
        "scale")
            local outs out
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_set_scale "${out}" "${scale_arg}" || return 1; }
            done <<< "${outs}" ;;
        "pos")
            local outs out
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_set_position "${out}" "${pos_arg}" || return 1; }
            done <<< "${outs}" ;;
        "image")
            local resolved_img outs out
            resolved_img="$(resolve_image_path "${image_arg}")" || return 1
            map_fit_to_awww "${fit_mode}" >/dev/null || return 1
            transition="$(validate_transition "${transition}")" || return 1
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_set_image "${out}" "${resolved_img}" "${fit_mode}" "${transition}" "${bg_color}" "${align_mode}" || return 1; }
            done <<< "${outs}" ;;
        "random")
            local outs out
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_set_random "${out}" "${dir_arg}" "${fit_mode}" "${transition}" "${bg_color}" || return 1; }
            done <<< "${outs}" ;;
        "solid")
            local outs out
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_set_solid "${out}" "${solid_arg}" || return 1; }
            done <<< "${outs}" ;;
        "swap")
            cmd_swap_outputs "${swap_a}" "${swap_b}" || return 1 ;;
        "fav")
            local outs out
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_fav_wallpaper "${out}" || return 1; }
            done <<< "${outs}" ;;
        "slideshow")
            local resolved_dir outs out
            resolved_dir="$(resolve_dir_path "${dir_arg}")" || return 1
            map_fit_to_awww "${fit_mode}" >/dev/null || return 1
            transition="$(validate_transition "${transition}")" || return 1
            parse_interval_seconds "${interval}" >/dev/null || return 1
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_set_slideshow "${out}" "${resolved_dir}" "${fit_mode}" "${transition}" "${interval}" "${bg_color}" "${shuffle}" || return 1; }
            done <<< "${outs}" ;;
        "ncmatrix"|"cmatrix")
            local outs out
            cm_color="$(validate_ncmatrix_color "${cm_color}")" || return 1
            outs="$(resolve_outputs "${target_output}")" || return 1
            while IFS= read -r out; do
                [[ -n "${out}" ]] && { cmd_set_ncmatrix "${out}" "${cm_color}" "${cm_speed}" "${cm_japanese}" || return 1; }
            done <<< "${outs}" ;;
        "next"|"pause"|"resume"|"stop")
            cmd_control_slideshow "${action}" "${target_output}" || return 1 ;;
        "restore")
            restore_state_from_dir "${STATE_DIR}" || return 1 ;;
        "save-profile")
            cmd_save_profile "${profile_name}" || return 1 ;;
        "load-profile")
            cmd_load_profile "${profile_name}" || return 1 ;;
    esac
    return 0
}

main() {
    if [[ "${1:-}" == "--internal-worker" ]]; then
        shift; run_slideshow_worker "$@"; exit 0
    fi

    ensure_wayland_env
    check_core_deps

    if (( $# == 0 )); then
        run_tui_or_map "tui"
        exit 0
    fi

    execute_cli_args "$@"
}

main "$@"
