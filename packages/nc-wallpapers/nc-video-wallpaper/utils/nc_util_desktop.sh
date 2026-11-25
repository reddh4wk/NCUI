#!/usr/bin/env bash
# ==============================================================================
# nc-desktop.sh — Gestore Sfondi, Display & Matrix (ASCII/JP) per Niri Wayland
# ==============================================================================
# Architettura modulare:
#   1. Bootstrap, Drop Privilegi & Auto-discovery Socket (Wayland / Niri)
#   2. Palette Cyberpunk Pastello & Gestione Stato / Processi per Monitor
#   3. Risoluzione Output Niri (swap/multi-monitor)
#   4. Motori Grafici Background (awww Immagine/Unicolor/Random, Slideshow, NCMatrix JP)
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
        "${PID_DIR}/nc-matrix_${out}.pid" \
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
        "^([^ ]*/)?nc-matrix .*--output[= ]${out}( |\$)" \
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
        "${PID_DIR}/nc-matrix_${out}.pid" \
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
            "${PID_DIR}/slideshow_${out}.pid" "${PID_DIR}/nc-matrix_${out}.pid" "${PID_DIR}/ncmatrix_${out}.pid" \
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
            "${PID_DIR}/slideshow_${out}.pid" "${PID_DIR}/nc-matrix_${out}.pid" "${PID_DIR}/ncmatrix_${out}.pid" \
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
                "${PID_DIR}/slideshow_${out}.pid" "${PID_DIR}/nc-matrix_${out}.pid" "${PID_DIR}/ncmatrix_${out}.pid" \
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
            "${PID_DIR}/nc-matrix_${out}.pid" "${PID_DIR}/ncmatrix_${out}.pid" "${PID_DIR}/cmatrix_${out}.pid" \
            "${PID_DIR}/video_${out}.pid" "${PID_DIR}/solid_${out}.pid"
        kill_collected_pids "${worker_pid}" "${old_pids[@]}"
    fi

    msg_ok "Schermo ${out}: slideshow avviata da '$(basename "${dir}")' (${count} img, ogni ${interval_raw})"
}

cmd_set_ncmatrix() {
    local out="${1}" color="${2}" speed="${3}" japanese="${4:-true}" font_size="${5:-9}" spawn_rate="${6:-1.5}" head_color="${7:-blend}"
    local speed_variance="${8:-5.0}" overlap_mode="${9:-hybrid}" depth_parallax="${10:-0.50}" glow="${11:-0.60}" decay_mode="${12:-exponential}" shimmer="${13:-insane}" mirror="${14:-random}" tail_length="${15:-long}" col_density="${16:-dense}"
    local charset="ascii"
    [[ "${japanese}" == "true" ]] && charset="katakana"

    local ncmatrix_bin="" cfg_bin=""
    cfg_bin="$(python3 -c '
import os, tomllib
p = os.path.expanduser("~/.config/nc-ui/config.toml")
if os.path.isfile(p):
    d = tomllib.load(open(p, "rb"))
    wp = d.get("wallpaper") or {}
    v = str(wp.get("nc_matrix_bin") or wp.get("ncmatrix_bin") or "").strip()
    if v: print(os.path.expanduser(v))
' 2>/dev/null || true)"
    if [[ -n "${cfg_bin}" ]]; then
        ncmatrix_bin="${cfg_bin}"
    else
        ncmatrix_bin="$(command -v nc-matrix 2>/dev/null || true)"
    fi
    if [[ -z "${ncmatrix_bin}" || ! -x "${ncmatrix_bin}" ]]; then
        msg_err "nc-matrix non installato o non trovato (installa pacchetto nc-matrix o imposta [wallpaper].nc_matrix_bin)."
        return 1
    fi

    cat > "/tmp/nc-matrix_${out}.conf" <<EOF
charset=${charset}
color=${color}
speed=${speed}
spawn_rate=${spawn_rate}
head_color=${head_color}
speed_variance=${speed_variance}
overlap_mode=${overlap_mode}
depth_parallax=${depth_parallax}
glow=${glow}
decay_mode=${decay_mode}
shimmer=${shimmer}
mirror=${mirror}
tail_length=${tail_length}
col_density=${col_density}
font_size=${font_size}
bold=2
async=1
fade=1
EOF

    local pidfile="${PID_DIR}/nc-matrix_${out}.pid"
    local running_pid=""
    if [[ -f "${pidfile}" ]]; then
        local cand
        cand="$(cat "${pidfile}" 2>/dev/null || true)"
        if [[ -n "${cand}" && "${cand}" =~ ^[0-9]+$ ]] && kill -0 "${cand}" 2>/dev/null; then
            if tr '\0' ' ' < "/proc/${cand}/cmdline" 2>/dev/null | grep -q "nc-matrix"; then
                local exe_path ncmatrix_real
                exe_path="$(readlink "/proc/${cand}/exe" 2>/dev/null || true)"
                ncmatrix_real="$(readlink -f "${ncmatrix_bin}" 2>/dev/null || printf '%s' "${ncmatrix_bin}")"
                if [[ -n "${exe_path}" && "${exe_path}" != *" (deleted)" && "${exe_path}" == "${ncmatrix_real}" ]]; then
                    running_pid="${cand}"
                fi
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
            --spawn-rate "${spawn_rate}" \
            --head-color "${head_color}" \
            --speed-variance "${speed_variance}" \
            --overlap "${overlap_mode}" \
            --parallax "${depth_parallax}" \
            --glow "${glow}" \
            --decay "${decay_mode}" \
            --shimmer "${shimmer}" \
            --mirror "${mirror}" \
            --tail-length "${tail_length}" \
            --dense "${col_density}" \
            --font-size "${font_size}" \
            --pid-file "${pidfile}" \
            --ipc-socket "/tmp/nc_mpvpaper_${out}.sock" \
            -a -b \
            >> "${LOG_DIR}/nc-matrix_${out}.log" 2>&1 &
        local new_pid=$!
        disown "${new_pid}" 2>/dev/null || true
        if (( ${#old_pids[@]} > 0 )); then
            sleep 0.35
            kill_collected_pids "${new_pid}" "${old_pids[@]}"
        fi
    fi

    local tmp_st
    tmp_st="$(jq \
        --arg size "${font_size}" \
        --arg spawn_rate "${spawn_rate}" \
        --arg head_color "${head_color}" \
        --arg speed_variance "${speed_variance}" \
        --arg overlap_mode "${overlap_mode}" \
        --arg depth_parallax "${depth_parallax}" \
        --arg glow "${glow}" \
        --arg decay_mode "${decay_mode}" \
        --arg shimmer "${shimmer}" \
        --arg mirror "${mirror}" \
        --arg tail_length "${tail_length}" \
        --arg col_density "${col_density}" \
        '. + {size: $size, spawn_rate: $spawn_rate, head_color: $head_color, speed_variance: $speed_variance, overlap_mode: $overlap_mode, depth_parallax: $depth_parallax, glow: $glow, decay_mode: $decay_mode, shimmer: $shimmer, mirror: $mirror, tail_length: $tail_length, col_density: $col_density}' \
        "${STATE_DIR}/${out}.json" 2>/dev/null || true)"
    [[ -n "${tmp_st}" ]] && printf '%s\n' "${tmp_st}" > "${STATE_DIR}/${out}.json"

    local jp_label=""
    [[ "${japanese}" == "true" ]] && jp_label=" [Katakana JP]"
    msg_ok "Schermo ${out}: nc-matrix${jp_label} attivato [colore: ${color}, speed: ${speed}, ${font_size}pt]"
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
        local out mode target fit align trans interval color speed bgcolor shuffle japanese size spawn_rate head_color speed_variance overlap_mode depth_parallax glow decay_mode shimmer mirror tail_length col_density
        out="$(jq -r '.output // empty' "${sf}")"
        mode="$(jq -r '.mode // empty' "${sf}")"
        target="$(jq -r '.target // empty' "${sf}")"
        fit="$(jq -r '.fit // "fill"' "${sf}")"
        align="$(jq -r '.align // "center"' "${sf}")"
        trans="$(jq -r '.transition // "wipe"' "${sf}")"
        interval="$(jq -r '.interval // "5m"' "${sf}")"
        color="$(jq -r '.color // "green"' "${sf}")"
        speed="$(jq -r '.speed // "5"' "${sf}")"
        bgcolor="$(jq -r '.bgcolor // "18181a"' "${sf}")"
        shuffle="$(jq -r '.shuffle // "true"' "${sf}")"
        japanese="$(jq -r '.japanese // "true"' "${sf}")"
        size="$(jq -r '.size // "9"' "${sf}")"
        spawn_rate="$(jq -r '.spawn_rate // "1.5"' "${sf}")"
        head_color="$(jq -r '.head_color // "blend"' "${sf}")"
        speed_variance="$(jq -r '.speed_variance // "5.0"' "${sf}")"
        overlap_mode="$(jq -r '.overlap_mode // "hybrid"' "${sf}")"
        depth_parallax="$(jq -r '.depth_parallax // "0.50"' "${sf}")"
        glow="$(jq -r '.glow // "0.60"' "${sf}")"
        decay_mode="$(jq -r '.decay_mode // "exponential"' "${sf}")"
        shimmer="$(jq -r '.shimmer // "insane"' "${sf}")"
        mirror="$(jq -r '.mirror // "random"' "${sf}")"
        tail_length="$(jq -r '.tail_length // "long"' "${sf}")"
        col_density="$(jq -r '.col_density // "dense"' "${sf}")"

        [[ -z "${out}" ]] && continue
        resolve_outputs "${out}" >/dev/null 2>&1 || continue

        case "${mode}" in
            image)     [[ -f "${target}" ]] && cmd_set_image "${out}" "${target}" "${fit}" "${trans}" "${bgcolor}" "${align}" ;;
            solid)     cmd_set_solid "${out}" "${bgcolor}" ;;
            slideshow) [[ -d "${target}" ]] && cmd_set_slideshow "${out}" "${target}" "${fit}" "${trans}" "${interval}" "${bgcolor}" "${shuffle}" ;;
            ncmatrix|cmatrix) cmd_set_ncmatrix "${out}" "${color}" "${speed}" "${japanese}" "${size}" "${spawn_rate}" "${head_color}" "${speed_variance}" "${overlap_mode}" "${depth_parallax}" "${glow}" "${decay_mode}" "${shimmer}" "${mirror}" "${tail_length}" "${col_density}" ;;
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
# 5. HELPER & PARSER UNIFICATO CLI
# ==============================================================================
show_help() {
    cat << EOF

${C_BOLD}${C_MAUVE}╭──────────────────────────────────────────────────────────────────────────────╮${C_RESET}
${C_BOLD}${C_MAUVE}│${C_RESET}  ${C_BOLD}${C_LAVENDER}NC-DESKTOP${C_RESET} ${C_MUTED}v${VERSION}${C_RESET} — ${C_TEXT}Gestore Sfondi, Display & Matrix per Niri Wayland${C_RESET}       ${C_BOLD}${C_MAUVE}│${C_RESET}
${C_BOLD}${C_MAUVE}╰──────────────────────────────────────────────────────────────────────────────╯${C_RESET}

  ${C_BOLD}${C_SKY}UTILIZZO:${C_RESET}
    ${C_TEXT}${SCRIPT_NAME}${C_RESET} ${C_SUBTEXT}[OPZIONI CLI]${C_RESET}                ${C_SUBTEXT}Backend sfondi, slideshow & NC-Matrix per Niri/NC-UI${C_RESET}
    ${C_TEXT}${SCRIPT_NAME}${C_RESET} ${C_GREEN}unicolor${C_RESET} ${C_PEACH}<id|all> <col|hex>${C_RESET}  ${C_SUBTEXT}Applica subito un colore uniforme (es. unicolor all white)${C_RESET}

  ${C_BOLD}${C_SKY}SELEZIONE E ARCHIVIO:${C_RESET}
    ${C_GREEN}-o, --output${C_RESET} ${C_PEACH}<id|nome|all>${C_RESET}  Schermo target: numero (${C_PEACH}1${C_RESET}, ${C_PEACH}2${C_RESET}), porta (${C_PEACH}DP-1${C_RESET}, ${C_PEACH}DP-3${C_RESET}) o ${C_PEACH}all${C_RESET}
    ${C_GREEN}-l, --list${C_RESET}                 Elenca immagini e cartelle in ${C_DIM}~/.local/nc-desktop/wallpapers${C_RESET}
    ${C_GREEN}-h, --help${C_RESET}                 Mostra questa guida e termina

  ${C_BOLD}${C_SKY}MODALITÀ SFONDO:${C_RESET}
    ${C_GREEN}-i, --image${C_RESET} ${C_PEACH}<file>${C_RESET}          Imposta un'immagine statica
    ${C_GREEN}-d, --dir${C_RESET} ${C_PEACH}<cartella>${C_RESET}        Avvia una slideshow indipendente dalla cartella indicata
    ${C_GREEN}-c, --nc-matrix${C_RESET}            Imposta ${C_TEAL}nc-matrix${C_RESET} in background nativo Wayland sullo schermo
    ${C_GREEN}    --random${C_RESET} ${C_PEACH}[cartella]${C_RESET}     Applica subito uno sfondo casuale
    ${C_GREEN}    --unicolor${C_RESET} ${C_PEACH}<col|hex>${C_RESET}    Applica un colore uniforme (es. ${C_PEACH}white${C_RESET}, ${C_PEACH}black${C_RESET}, ${C_PEACH}#18181a${C_RESET}, ${C_PEACH}cyber${C_RESET})
    ${C_GREEN}    --solid${C_RESET} ${C_PEACH}<col|hex>${C_RESET}       Alias di ${C_GREEN}--unicolor${C_RESET}
    ${C_GREEN}    --swap${C_RESET} ${C_PEACH}<id1> <id2>${C_RESET}      Scambia istantaneamente gli sfondi tra due monitor
    ${C_GREEN}    --fav${C_RESET}                  Salva lo sfondo corrente dello schermo nella cartella ${C_PEACH}favorites/${C_RESET}

  ${C_BOLD}${C_SKY}OPZIONI IMMAGINE, SLIDESHOW E NC-MATRIX:${C_RESET}
    ${C_GREEN}-f, --fit${C_RESET} ${C_PEACH}<modo>${C_RESET}            Riempimento: ${C_PEACH}fill${C_RESET} (crop), ${C_PEACH}fit${C_RESET}, ${C_PEACH}stretch${C_RESET}, ${C_PEACH}center${C_RESET} ${C_MUTED}[Default: fill]${C_RESET}
    ${C_GREEN}-t, --transition${C_RESET} ${C_PEACH}<tipo>${C_RESET}     Transizione: ${C_PEACH}wipe${C_RESET}, ${C_PEACH}wave${C_RESET}, ${C_PEACH}grow${C_RESET}, ${C_PEACH}outer${C_RESET}, ${C_PEACH}fade${C_RESET}, ${C_PEACH}simple${C_RESET}, ${C_PEACH}random${C_RESET}, ${C_PEACH}none${C_RESET}
    ${C_GREEN}-n, --interval${C_RESET} ${C_PEACH}<tempo>${C_RESET}      Intervallo slideshow (${C_PEACH}30s${C_RESET}, ${C_PEACH}5m${C_RESET}, ${C_PEACH}1h${C_RESET}) ${C_MUTED}[Default: 5m]${C_RESET}
    ${C_GREEN}-j, --japanese${C_RESET}             Usa caratteri giapponesi (Katakana + glifi Matrix) in NC-Matrix
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
    local solid_arg=""
    local swap_a=""
    local swap_b=""

    while (( $# > 0 )); do
        case "${1}" in
            -h|--help)         show_help; return 0 ;;
            -l|--list)         action="list"; shift ;;
            -o|--output|--screen)
                [[ -z "${2:-}" ]] && { msg_err "L'opzione '${1}' richiede lo schermo (1, 2, DP-1, all)."; return 1; }
                target_output="${2}"; shift 2 ;;
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
            -c|--nc-matrix|--ncmatrix|--cmatrix)
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
                [[ -z "${2:-}" || ! "${2}" =~ ^([0-9]|10)$ ]] && { msg_err "La velocità (-u/--speed) deve essere tra 0 e 10."; return 1; }
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
        "")               show_help ;;
        "list")           cmd_list_wallpapers ;;
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
        show_help
        exit 0
    fi

    execute_cli_args "$@"
}

main "$@"
