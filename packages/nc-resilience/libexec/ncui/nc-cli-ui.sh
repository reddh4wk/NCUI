#!/usr/bin/env bash
# ==============================================================================
# /usr/lib/ncui/sbin/nc-cli-ui.sh
# Shared adaptive NC-UI CLI theme loader & terminal UI primitives
# ==============================================================================

[[ -n "${_NC_CLI_UI_LOADED:-}" ]] && return 0
_NC_CLI_UI_LOADED=1

THEME_HEX_STRUCTURE="#3e465c"
THEME_HEX_SCHEMA="#8b9bb8"
THEME_HEX_INACTIVE="#596278"
THEME_HEX_FOCUS="#ffffff"
THEME_HEX_FOCUS_VAL="#dce6f7"
THEME_HEX_INTENT="#00f0ff"
THEME_HEX_VITALITY="#00f57a"
THEME_HEX_ATTENTION="#ffe619"
THEME_HEX_INTERRUPT="#ff2a9d"
THEME_HEX_FAULT="#ff2442"
THEME_BOX_TL="╭"
THEME_BOX_TR="╮"
THEME_BOX_BL="╰"
THEME_BOX_BR="╯"
THEME_BOX_H="─"
THEME_BOX_V="│"

_UI_JUST_HEADER=0
_UI_OPT_INDENT=10

_resolve_ncui_theme_file() {
    local cand=""
    if [[ -n "${NCUI_THEME_FILE:-}" && -r "${NCUI_THEME_FILE}" ]]; then
        printf '%s' "$NCUI_THEME_FILE"
        return 0
    fi
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        cand="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 || true)/.config/ncui/theme.toml"
        [[ -r "$cand" ]] && { printf '%s' "$cand"; return 0; }
    fi
    if (( EUID != 0 )); then
        cand="${XDG_CONFIG_HOME:-${HOME}/.config}/ncui/theme.toml"
        [[ -r "$cand" ]] && { printf '%s' "$cand"; return 0; }
    fi
    local u _p uid _g _c h _s
    while IFS=: read -r u _p uid _g _c h _s; do
        if [[ "$uid" =~ ^[0-9]+$ ]] && (( uid >= 1000 && uid < 65000 )) && [[ -r "${h}/.config/ncui/theme.toml" ]]; then
            printf '%s' "${h}/.config/ncui/theme.toml"
            return 0
        fi
    done < /etc/passwd
    cand="/usr/share/ncui/config/ncui/theme.toml"
    [[ -r "$cand" ]] && { printf '%s' "$cand"; return 0; }
    return 1
}

_hex_to_fg() {
    local h="${1#\#}"
    [[ "$h" =~ ^[0-9a-fA-F]{6}$ ]] || return 0
    printf '\033[38;2;%d;%d;%dm' "0x${h:0:2}" "0x${h:2:2}" "0x${h:4:2}"
}

_load_ncui_theme() {
    local tf=""
    tf="$(_resolve_ncui_theme_file 2>/dev/null || true)"
    if [[ -n "$tf" && -r "$tf" ]]; then
        local sec="" line k v
        while IFS= read -r line || [[ -n "$line" ]]; do
            line="${line#"${line%%[![:space:]]*}"}"
            [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
            if [[ "$line" =~ ^\[([a-zA-Z0-9_.-]+)\] ]]; then
                sec="${BASH_REMATCH[1]}"
                continue
            fi
            if [[ "$sec" == "colors" && "$line" =~ ^([a-z_]+)[[:space:]]*=[[:space:]]*\"(#[0-9a-fA-F]{6})\" ]]; then
                k="${BASH_REMATCH[1]}"
                v="${BASH_REMATCH[2]}"
                case "$k" in
                    structure) THEME_HEX_STRUCTURE="$v" ;;
                    schema)    THEME_HEX_SCHEMA="$v" ;;
                    inactive)  THEME_HEX_INACTIVE="$v" ;;
                    focus)     THEME_HEX_FOCUS="$v" ;;
                    focus_val) THEME_HEX_FOCUS_VAL="$v" ;;
                    intent)    THEME_HEX_INTENT="$v" ;;
                    vitality)  THEME_HEX_VITALITY="$v" ;;
                    attention) THEME_HEX_ATTENTION="$v" ;;
                    interrupt) THEME_HEX_INTERRUPT="$v" ;;
                    fault)     THEME_HEX_FAULT="$v" ;;
                esac
            elif [[ "$sec" == "ascii.chars" && "$line" =~ ^(tl|tr|bl|br|h|v)[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
                k="${BASH_REMATCH[1]}"
                v="${BASH_REMATCH[2]}"
                case "$k" in
                    tl) THEME_BOX_TL="$v" ;;
                    tr) THEME_BOX_TR="$v" ;;
                    bl) THEME_BOX_BL="$v" ;;
                    br) THEME_BOX_BR="$v" ;;
                    h)  THEME_BOX_H="$v" ;;
                    v)  THEME_BOX_V="$v" ;;
                esac
            fi
        done < "$tf"
    fi

    if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
        CLR_RESET=$'\033[0m'
        CLR_BOLD=$'\033[1m'
        CLR_STRUCT="$(_hex_to_fg "$THEME_HEX_STRUCTURE")"
        CLR_SCHEMA="$(_hex_to_fg "$THEME_HEX_SCHEMA")"
        CLR_DIM="$(_hex_to_fg "$THEME_HEX_INACTIVE")"
        CLR_FOCUS="$(_hex_to_fg "$THEME_HEX_FOCUS")"
        CLR_VAL="$(_hex_to_fg "$THEME_HEX_FOCUS_VAL")"
        CLR_INTENT="$(_hex_to_fg "$THEME_HEX_INTENT")"
        CLR_OK="$(_hex_to_fg "$THEME_HEX_VITALITY")"
        CLR_WARN="$(_hex_to_fg "$THEME_HEX_ATTENTION")"
        CLR_ERR="$(_hex_to_fg "$THEME_HEX_FAULT")"
        CLR_INFO="$CLR_SCHEMA"
        CLR_STEP="$CLR_INTENT"
    else
        CLR_RESET='' CLR_BOLD='' CLR_STRUCT='' CLR_SCHEMA='' CLR_DIM=''
        CLR_FOCUS='' CLR_VAL='' CLR_INTENT='' CLR_OK='' CLR_WARN='' CLR_ERR=''
        CLR_INFO='' CLR_STEP=''
    fi

    # Compatibility aliases for scripts using C_* palette names
    C_RESET="$CLR_RESET"
    C_BOLD="$CLR_BOLD"
    C_SLATE="$CLR_SCHEMA"
    C_CYAN="$CLR_INTENT"
    C_BLUE="$CLR_INTENT"
    C_LAVENDER="$CLR_INTENT"
    C_MINT="$CLR_OK"
    C_GREEN="$CLR_OK"
    C_PEACH="$CLR_WARN"
    C_YELLOW="$CLR_WARN"
    C_ROSE="$CLR_ERR"
    C_RED="$CLR_ERR"
    C_MUTED="$CLR_DIM"
    C_MUTED_CMD="$CLR_STRUCT"
    C_CTX="$CLR_VAL"
}

_load_ncui_theme

msg_info() { printf "  %s::%s %b\n" "$CLR_SCHEMA" "$CLR_RESET" "$*"; }
msg_step() { printf "  %s⯈%s %s%b%s\n" "$CLR_INTENT" "$CLR_RESET" "$CLR_VAL" "$*" "$CLR_RESET"; }
msg_ok()   { printf "  %s✓%s %s%b%s\n" "$CLR_OK" "$CLR_RESET" "$CLR_VAL" "$*" "$CLR_RESET"; }
msg_warn() { printf "  %s!%s %s%b%s\n" "$CLR_WARN" "$CLR_RESET" "$CLR_WARN" "$*" "$CLR_RESET" >&2; }
msg_err()  { printf "  %s✗ ERROR:%s %b\n" "$CLR_ERR" "$CLR_RESET" "$*" >&2; }

_repeat_char() {
    local ch="$1" n="${2:-0}" i
    (( n > 0 )) || return 0
    for (( i = 0; i < n; i++ )); do
        printf '%s' "$ch"
    done
}

ui_rule() {
    local width="${1:-68}"
    _UI_JUST_HEADER=0
    printf "  %s" "$CLR_STRUCT"
    _repeat_char "$THEME_BOX_H" "$width"
    printf "%s\n" "$CLR_RESET"
}

ui_header() {
    local title="$1" subtitle="${2:-}" inner=66
    local pad=$(( inner - 2 - ${#title} ))
    (( pad < 1 )) && { title="${title:0:$(( inner - 3 ))}"; pad=1; }
    printf "\n  %s%s" "$CLR_STRUCT" "$THEME_BOX_TL"
    _repeat_char "$THEME_BOX_H" "$inner"
    printf "%s%s\n" "$THEME_BOX_TR" "$CLR_RESET"
    printf "  %s%s%s  %s%s%s%s%*s%s%s%s\n" \
        "$CLR_STRUCT" "$THEME_BOX_V" "$CLR_RESET" \
        "$CLR_BOLD" "$CLR_FOCUS" "$title" "$CLR_RESET" \
        "$pad" "" "$CLR_STRUCT" "$THEME_BOX_V" "$CLR_RESET"
    if [[ -n "$subtitle" ]]; then
        local spad=$(( inner - 2 - ${#subtitle} ))
        (( spad < 1 )) && { subtitle="${subtitle:0:$(( inner - 6 ))}..."; spad=$(( inner - 2 - ${#subtitle} )); }
        (( spad < 1 )) && spad=1
        printf "  %s%s%s  %s%s%s%*s%s%s%s\n" \
            "$CLR_STRUCT" "$THEME_BOX_V" "$CLR_RESET" \
            "$CLR_SCHEMA" "$subtitle" "$CLR_RESET" \
            "$spad" "" "$CLR_STRUCT" "$THEME_BOX_V" "$CLR_RESET"
    fi
    printf "  %s%s" "$CLR_STRUCT" "$THEME_BOX_BL"
    _repeat_char "$THEME_BOX_H" "$inner"
    printf "%s%s\n" "$THEME_BOX_BR" "$CLR_RESET"
    _UI_JUST_HEADER=1
}

ui_section() {
    local tag="$1" title="${2:-}" width=68 used fill
    if (( ${_UI_JUST_HEADER:-0} )); then
        printf '\n'
    else
        printf '\n\n'
    fi
    _UI_JUST_HEADER=0
    if [[ -z "$title" ]]; then
        title="$tag"
        _UI_OPT_INDENT=5
        used=$(( 5 + ${#title} + 1 ))
        fill=$(( width - used ))
        (( fill < 4 )) && fill=4
        printf "  %s" "$CLR_STRUCT"
        _repeat_char "$THEME_BOX_H" 2
        printf " %s%s%s%s %s" \
            "$CLR_BOLD" "$CLR_FOCUS" "$title" "$CLR_RESET" \
            "$CLR_STRUCT"
        _repeat_char "$THEME_BOX_H" "$fill"
        printf "%s\n" "$CLR_RESET"
    else
        _UI_OPT_INDENT=$(( 8 + ${#tag} ))
        used=$(( 5 + ${#tag} + 3 + ${#title} + 1 ))
        fill=$(( width - used ))
        (( fill < 4 )) && fill=4
        printf "  %s" "$CLR_STRUCT"
        _repeat_char "$THEME_BOX_H" 2
        printf " %s%s%s %s·%s %s%s%s%s %s" \
            "$CLR_INTENT" "$tag" "$CLR_RESET" \
            "$CLR_STRUCT" "$CLR_RESET" \
            "$CLR_BOLD" "$CLR_FOCUS" "$title" "$CLR_RESET" \
            "$CLR_STRUCT"
        _repeat_char "$THEME_BOX_H" "$fill"
        printf "%s\n" "$CLR_RESET"
    fi
}

ui_kv() {
    local key="$1" val="$2" extra="${3:-}"
    _UI_JUST_HEADER=0
    if [[ -n "$extra" ]]; then
        printf "  %s%-16s%s %s%s%s %s%s%s\n" \
            "$CLR_SCHEMA" "$key" "$CLR_RESET" \
            "$CLR_VAL" "$val" "$CLR_RESET" \
            "$CLR_DIM" "$extra" "$CLR_RESET"
    else
        printf "  %s%-16s%s %s%s%s\n" \
            "$CLR_SCHEMA" "$key" "$CLR_RESET" \
            "$CLR_VAL" "$val" "$CLR_RESET"
    fi
}

ui_opt() {
    local key="$1" label="$2" note="${3:-}"
    local ind="${_UI_OPT_INDENT:-10}"
    local note_ind=$(( ind + ${#key} + 3 ))
    _UI_JUST_HEADER=0
    printf "%*s%s[%s%s%s]%s %s%s%s\n" \
        "$ind" "" \
        "$CLR_STRUCT" "$CLR_INTENT" "$key" "$CLR_STRUCT" "$CLR_RESET" \
        "$CLR_VAL" "$label" "$CLR_RESET"
    if [[ -n "$note" ]]; then
        printf "%*s%s%s%s\n" "$note_ind" "" "$CLR_DIM" "$note" "$CLR_RESET"
    fi
}

ui_help_row() {
    local key="$1" desc="$2" w="${3:-22}"
    _UI_JUST_HEADER=0
    printf "  %s%-*s%s %s%s%s\n" \
        "$CLR_INTENT" "$w" "$key" "$CLR_RESET" \
        "$CLR_VAL" "$desc" "$CLR_RESET"
}

_rl_prompt() {
    local txt="$1"
    if [[ -n "$CLR_INTENT" ]]; then
        printf '  \001%s\002›\001%s\002 \001%s\002%s\001%s\002 ' "$CLR_INTENT" "$CLR_RESET" "$CLR_FOCUS" "$txt" "$CLR_RESET"
    else
        printf '  › %s ' "$txt"
    fi
}

ui_confirm() {
    local msg="$1" def="${2:-n}" ans="" hint="" prompt="" _in="/dev/stdin"
    def="${def,,}"
    [[ "$def" == "y" ]] || def="n"
    if [[ -t 0 && -r /dev/tty ]]; then
        _in="/dev/tty"
    fi
    if [[ -n "$CLR_INTENT" ]]; then
        if [[ "$def" == "y" ]]; then
            hint="${CLR_STRUCT}[${CLR_INTENT}Y${CLR_STRUCT}/${CLR_DIM}n${CLR_STRUCT}]${CLR_RESET}"
        else
            hint="${CLR_STRUCT}[${CLR_DIM}y${CLR_STRUCT}/${CLR_INTENT}N${CLR_STRUCT}]${CLR_RESET}"
        fi
        prompt="  \001${CLR_INTENT}\002›\001${CLR_RESET}\002 \001${CLR_FOCUS}\002${msg}\001${CLR_RESET}\002 ${hint}: "
    else
        [[ "$def" == "y" ]] && hint="[Y/n]" || hint="[y/N]"
        prompt="  › ${msg} ${hint}: "
    fi
    while true; do
        if [[ "$_in" == "/dev/tty" ]]; then
            read -r -e -p "$(printf '%b' "$prompt")" ans </dev/tty || ans="$def"
        else
            IFS= read -r ans || ans="$def"
        fi
        ans="${ans#"${ans%%[![:space:]]*}"}"
        ans="${ans%"${ans##*[![:space:]]}"}"
        ans="${ans,,}"
        [[ -z "$ans" ]] && ans="$def"
        case "$ans" in
            y|yes|s|si|sì) return 0 ;;
            n|no)          return 1 ;;
            *)             msg_warn "Invalid answer '${ans}'. Enter y/yes or n/no (Enter=${def^^})." ;;
        esac
    done
}
