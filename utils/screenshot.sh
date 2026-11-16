#!/usr/bin/env bash

if [[ -f /usr/local/lib/nc-ui/nc_log.sh ]]; then
    source /usr/local/lib/nc-ui/nc_log.sh
else
    nc_log_info()  { echo "[INFO] $*" >&2; }
    nc_log_warn()  { echo "[WARN] $*" >&2; }
    nc_log_error() { echo "[ERROR] $*" >&2; }
    nc_log_debug() { :; }
fi

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"

# Palette pastello (24-bit ANSI)
C_RESET=$'\033[0m'
C_BOLD=$'\033[1m'
C_DIM=$'\033[38;2;147;153;178m'    # Overlay2
C_TEXT=$'\033[38;2;205;214;244m'   # Text
C_MINT=$'\033[38;2;166;227;161m'   # Green pastel
C_SKY=$'\033[38;2;137;220;235m'    # Sky pastel
C_PEACH=$'\033[38;2;250;179;135m'  # Peach pastel
C_ROSE=$'\033[38;2;243;139;168m'   # Red/Pink pastel
C_LAV=$'\033[38;2;180;190;254m'    # Lavender pastel

MODE="area"
DELAY=0
OUTPUT_DIR="${HOME}/Pictures/Screenshots"
EDIT_MODE=1
TMP_IMG=""

cleanup() {
    if [[ -n "$TMP_IMG" && -f "$TMP_IMG" ]]; then
        rm -f "$TMP_IMG"
    fi
}
trap cleanup EXIT INT TERM

print_msg() {
    local color="$1"
    local prefix="$2"
    local msg="$3"
    printf "%b[%s]%b %b%s%b\n" "$color$C_BOLD" "$prefix" "$C_RESET" "$C_TEXT" "$msg" "$C_RESET" >&2
}

notify_user() {
    local title="$1"
    local body="$2"
    if [[ -x /usr/local/lib/nc-ui/nc_util_notify.sh ]]; then
        /usr/local/lib/nc-ui/nc_util_notify.sh --title "$title" --body "$body" >/dev/null 2>&1 || true
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send "$title" "$body" >/dev/null 2>&1 || true
    fi
}

show_help() {
    cat << EOF
${C_LAV}${C_BOLD}nc_cmd_screenshot.sh${C_RESET} ${C_DIM}- Cattura screenshot per Wayland / Niri${C_RESET}

${C_SKY}${C_BOLD}USO:${C_RESET}
  ${C_MINT}nc_cmd_screenshot.sh${C_RESET} [OPZIONI]
  ${C_MINT}nc_cmd_screenshot.sh${C_RESET} [MODALITA] [RITARDO]

${C_SKY}${C_BOLD}OPZIONI:${C_RESET}
  ${C_PEACH}-m, --mode${C_RESET} <tipo>        Modalità di cattura: ${C_BOLD}area${C_RESET}, ${C_BOLD}window${C_RESET}, ${C_BOLD}screen${C_RESET} (default: area)
  ${C_PEACH}-d, --delay${C_RESET} <secondi>    Ritardo in secondi prima dello scatto (default: 0)
  ${C_PEACH}-o, --output-dir${C_RESET} <dir>   Directory di destinazione (default: ~/Pictures/Screenshots)
  ${C_PEACH}    --no-edit${C_RESET}            Salva e copia negli appunti senza aprire Satty
  ${C_PEACH}-h, --help${C_RESET}               Mostra questo messaggio di aiuto

${C_SKY}${C_BOLD}ESEMPI:${C_RESET}
  ${C_DIM}# Selezione interattiva immediata${C_RESET}
  nc_cmd_screenshot.sh -m area

  ${C_DIM}# Cattura finestra attiva dopo 3 secondi${C_RESET}
  nc_cmd_screenshot.sh --mode window --delay 3

  ${C_DIM}# Cattura schermo attivo dopo 5 secondi senza editor${C_RESET}
  nc_cmd_screenshot.sh -m screen -d 5 --no-edit
EOF
}

check_deps() {
    local missing=()
    local deps=("wl-copy")

    [[ "$EDIT_MODE" -eq 1 ]] && deps+=("satty")
    case "$MODE" in
        area)   deps+=("grim" "slurp") ;;
        window) deps+=("niri") ;;
        screen) deps+=("grim") ;;
    esac

    for cmd in "${deps[@]}"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        local err="Dipendenze mancanti: ${missing[*]}"
        nc_log_error "$err"
        print_msg "$C_ROSE" "ERRORE" "$err"
        notify_user "Errore Screenshot" "$err"
        exit 1
    fi
}

trim_transparent_margin() {
    local img_path="$1"
    command -v magick >/dev/null 2>&1 || return 0

    local crop_box
    crop_box=$(magick "$img_path" -alpha extract -threshold 50% -format "%@" info: 2>/dev/null) || return 0

    if [[ "$crop_box" =~ ^[1-9][0-9]*x[1-9][0-9]*\+[0-9]+\+[0-9]+$ ]]; then
        local tmp_crop="${img_path}.crop.png"
        if magick "$img_path" -crop "$crop_box" +repage "$tmp_crop" 2>/dev/null && [[ -s "$tmp_crop" ]]; then
            mv -f "$tmp_crop" "$img_path"
        else
            rm -f "$tmp_crop"
        fi
    fi
}

capture_active_window() {
    local out_path="$1"
    local win_info

    win_info=$(niri msg -j focused-window 2>/dev/null) || return 1
    if [[ -z "$win_info" || "$win_info" == "null" ]]; then
        return 2
    fi

    if ! niri msg action screenshot-window --write-to-disk true --path "$out_path" >/dev/null 2>&1; then
        return 1
    fi

    # Niri accoda lo scatto al frame successivo e scrive su disco in modo asincrono
    local waited=0
    while [[ ! -s "$out_path" && $waited -lt 40 ]]; do
        sleep 0.05
        ((waited++))
    done

    [[ -s "$out_path" ]] || return 1
    sleep 0.05

    trim_transparent_margin "$out_path"
    return 0
}

get_active_output_name() {
    if command -v niri >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
        niri msg -j focused-output 2>/dev/null | jq -r '.name // empty' 2>/dev/null
    fi
}

# Parsing argomenti (supporta sia flag GNU/Linux che posizionali legacy)
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -m|--mode)
            [[ -z "${2:-}" ]] && { print_msg "$C_ROSE" "ERRORE" "Opzione $1 richiede un argomento."; exit 1; }
            MODE="$2"
            shift 2
            ;;
        --mode=*)
            MODE="${1#*=}"
            shift
            ;;
        -d|--delay)
            [[ -z "${2:-}" ]] && { print_msg "$C_ROSE" "ERRORE" "Opzione $1 richiede un argomento."; exit 1; }
            DELAY="$2"
            shift 2
            ;;
        --delay=*)
            DELAY="${1#*=}"
            shift
            ;;
        -o|--output-dir)
            [[ -z "${2:-}" ]] && { print_msg "$C_ROSE" "ERRORE" "Opzione $1 richiede un argomento."; exit 1; }
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --output-dir=*)
            OUTPUT_DIR="${1#*=}"
            shift
            ;;
        --no-edit)
            EDIT_MODE=0
            shift
            ;;
        --)
            shift
            POSITIONAL+=("$@")
            break
            ;;
        -*)
            print_msg "$C_ROSE" "ERRORE" "Opzione sconosciuta: $1"
            show_help
            exit 1
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

if [[ ${#POSITIONAL[@]} -gt 2 ]]; then
    print_msg "$C_ROSE" "ERRORE" "Troppi argomenti posizionali."
    show_help
    exit 1
fi

[[ ${#POSITIONAL[@]} -ge 1 ]] && MODE="${POSITIONAL[0]}"
[[ ${#POSITIONAL[@]} -ge 2 ]] && DELAY="${POSITIONAL[1]}"

# Validazione input
case "$MODE" in
    area|window|screen) ;;
    *)
        err="Modalità non valida: '$MODE' (usa: area, window, screen)"
        nc_log_error "$err"
        print_msg "$C_ROSE" "ERRORE" "$err"
        exit 1
        ;;
esac

if ! [[ "$DELAY" =~ ^[0-9]+$ ]]; then
    err="Ritardo non valido: '$DELAY' (deve essere un intero >= 0)"
    nc_log_error "$err"
    print_msg "$C_ROSE" "ERRORE" "$err"
    exit 1
fi
DELAY=$((10#${DELAY:-0}))

check_deps

if ! mkdir -p "$OUTPUT_DIR" 2>/dev/null; then
    err="Impossibile creare la directory di output: $OUTPUT_DIR"
    nc_log_error "$err"
    print_msg "$C_ROSE" "ERRORE" "$err"
    exit 1
fi

# Generazione percorso output senza collisioni
BASE_NAME="screenshot-$(date '+%Y%m%d-%H%M%S')"
OUT_FILE="${OUTPUT_DIR}/${BASE_NAME}.png"
COUNTER=1
while [[ -e "$OUT_FILE" ]]; do
    OUT_FILE="${OUTPUT_DIR}/${BASE_NAME}-${COUNTER}.png"
    ((COUNTER++))
done

if (( DELAY > 0 )); then
    nc_log_info "Waiting ${DELAY}s before screenshot (mode: ${MODE})"
    print_msg "$C_SKY" "ATTESA" "Scatto ($MODE) tra ${DELAY}s..."
    sleep "$DELAY"
fi

TMP_IMG=$(mktemp /tmp/nc-screenshot-XXXXXX.png)

nc_log_info "Capturing screenshot (mode: ${MODE})"
case "$MODE" in
    area)
        GEOM=$(slurp 2>/dev/null) || {
            nc_log_info "Screenshot selection cancelled by user"
            print_msg "$C_PEACH" "ANNULLATO" "Selezione area annullata."
            exit 0
        }
        if [[ -z "$GEOM" ]]; then
            nc_log_warn "Empty geometry returned by slurp"
            exit 0
        fi
        if ! grim -g "$GEOM" "$TMP_IMG"; then
            nc_log_error "grim failed on area geometry: $GEOM"
            print_msg "$C_ROSE" "ERRORE" "Cattura area fallita."
            exit 1
        fi
        ;;
    window)
        capture_active_window "$TMP_IMG"
        rc=$?
        if [[ $rc -eq 2 ]]; then
            err="Nessuna finestra attiva rilevata al momento dello scatto."
            nc_log_warn "$err"
            print_msg "$C_PEACH" "AVVISO" "$err"
            notify_user "Screenshot Finestra" "$err"
            exit 1
        elif [[ $rc -ne 0 ]]; then
            err="Cattura finestra tramite niri fallita."
            nc_log_error "$err"
            print_msg "$C_ROSE" "ERRORE" "$err"
            notify_user "Errore Screenshot" "$err"
            exit 1
        fi
        ;;
    screen)
        ACTIVE_OUT=$(get_active_output_name)
        if [[ -n "$ACTIVE_OUT" ]]; then
            if ! grim -o "$ACTIVE_OUT" "$TMP_IMG"; then
                nc_log_error "grim failed on output: $ACTIVE_OUT"
                print_msg "$C_ROSE" "ERRORE" "Cattura schermo ($ACTIVE_OUT) fallita."
                exit 1
            fi
        else
            if ! grim "$TMP_IMG"; then
                nc_log_error "grim failed on full desktop"
                print_msg "$C_ROSE" "ERRORE" "Cattura schermo fallita."
                exit 1
            fi
        fi
        ;;
esac

if [[ ! -s "$TMP_IMG" ]]; then
    nc_log_error "Generated screenshot file is empty"
    print_msg "$C_ROSE" "ERRORE" "File immagine generato vuoto."
    exit 1
fi

# Copia sincrona negli appunti Wayland
if ! wl-copy < "$TMP_IMG"; then
    nc_log_warn "Failed to copy screenshot to clipboard"
    print_msg "$C_PEACH" "AVVISO" "Copia negli appunti non riuscita."
fi

if [[ "$EDIT_MODE" -eq 1 ]]; then
    nc_log_debug "Opening satty for annotation -> $OUT_FILE" 5
    satty --filename "$TMP_IMG" --output-filename "$OUT_FILE"
else
    cp "$TMP_IMG" "$OUT_FILE"
    print_msg "$C_MINT" "OK" "Salvato in $OUT_FILE e copiato negli appunti."
    notify_user "Screenshot Salvato" "$(basename "$OUT_FILE")"
fi

nc_log_debug "Screenshot process completed" 5
