#!/usr/bin/env bash
source /usr/local/lib/nc-ui/nc_log.sh

CONFIG_FILE="${HOME}/.config/nc-ui/nc-addons.conf"
MAX_NOTIFS=$(grep -E '^max_notifications\s*=' "$CONFIG_FILE" | awk -F= '{print $2}' | tr -d ' ')
MAX_NOTIFS=${MAX_NOTIFS:-50}
HISTORY_FILE="${HOME}/.cache/nc_notifications.json"

TITLE="Notifica"
BODY=""
NOTATION="bash"

while [[ $# -gt 0 ]]; do
    case $1 in
        --title) TITLE="$2"; shift 2 ;;
        --body) BODY="$2"; shift 2 ;;
        --notation) NOTATION="$2"; shift 2 ;;
        *) shift ;;
    esac
done

ansi_to_pango() {
    local text="$1"
    local esc=$'\033'
    
    # Translate literal representations of escape into actual ESC bytes
    text="${text//\\033/$esc}"
    text="${text//\\e/$esc}"
    text="${text//\\x1B/$esc}"
    text="${text//\\x1b/$esc}"

    # FONDAMENTALE: Esegui l'escape HTML prima di aggiungere i tag Pango
    text="${text//&/&amp;}"
    text="${text//</&lt;}"
    text="${text//>/&gt;}"

    local out=""
    local open_spans=0
    local IFS="$esc"
    read -ra parts <<< "$text"

    out="${parts[0]}"
    for ((i=1; i<${#parts[@]}; i++)); do
        local part="${parts[i]}"
        if [[ ${part:0:1} != "[" ]]; then
            out+="${esc}${part}"
            continue
        fi

        if [[ $part =~ ^\[([0-9;]*)m(.*)$ ]]; then
            local codes="${BASH_REMATCH[1]}"
            local rest="${BASH_REMATCH[2]}"
            [[ -z "$codes" ]] && codes="0"

            local tags=""
            local IFS_old="$IFS"
            IFS=';'
            read -ra code_arr <<< "$codes"
            IFS="$IFS_old"

            for code in "${code_arr[@]}"; do
                if [[ "$code" == "0" ]]; then
                    while (( open_spans > 0 )); do
                        out+="</span>"
                        ((open_spans--))
                    done
                    tags=""
                else
                    case "$code" in
                        1) tags+=" weight=\"bold\"" ;;
                        3) tags+=" style=\"italic\"" ;;
                        4) tags+=" underline=\"single\"" ;;
                        30) tags+=" foreground=\"#000000\"" ;;
                        31) tags+=" foreground=\"#ff5555\"" ;;
                        32) tags+=" foreground=\"#50fa7b\"" ;;
                        33) tags+=" foreground=\"#f1fa8c\"" ;;
                        34) tags+=" foreground=\"#bd93f9\"" ;;
                        35) tags+=" foreground=\"#ff79c6\"" ;;
                        36) tags+=" foreground=\"#8be9fd\"" ;;
                        37) tags+=" foreground=\"#bbbbbb\"" ;;
                        90) tags+=" foreground=\"#555555\"" ;;
                        91) tags+=" foreground=\"#ff6e6e\"" ;;
                        92) tags+=" foreground=\"#69ff94\"" ;;
                        93) tags+=" foreground=\"#ffffa5\"" ;;
                        94) tags+=" foreground=\"#d6acff\"" ;;
                        95) tags+=" foreground=\"#ff92df\"" ;;
                        96) tags+=" foreground=\"#a4ffff\"" ;;
                        97) tags+=" foreground=\"#ffffff\"" ;;
                    esac
                fi
            done
            if [[ -n "$tags" ]]; then
                out+="<span${tags}>"
                ((open_spans++))
            fi
            out+="$rest"
        else
            out+="${esc}${part}"
        fi
    done

    while (( open_spans > 0 )); do
        out+="</span>"
        ((open_spans--))
    done
    echo "$out"
}

html_to_pango() {
    local text="$1"
    local out=""
    local sq="'"
    local dq='"'
    local p_span=0
    local IFS='<'
    read -ra parts <<< "$text"

    out="${parts[0]}"
    shopt -s nocasematch
    for ((i=1; i<${#parts[@]}; i++)); do
        local part="${parts[i]}"
        if [[ $part =~ ^([^>]*)\>(.*)$ ]]; then
            local tag_content="${BASH_REMATCH[1]}"
            local rest="${BASH_REMATCH[2]}"

            if [[ $tag_content =~ ^p([[:space:]].*)?$ ]]; then
                local attr="${BASH_REMATCH[1]}"
                if [[ $attr =~ color[[:space:]]*:[[:space:]]*([^;\>[:space:]${sq}${dq}]+) ]]; then
                    out+="<span foreground=\"${BASH_REMATCH[1]}\">"
                    p_span=1
                fi
            elif [[ $tag_content == "/p" ]]; then
                if (( p_span > 0 )); then
                    out+="</span>"
                    p_span=0
                fi
                out+=$'\n'
            elif [[ $tag_content == "strong" || $tag_content == "b" ]]; then
                out+="<b>"
            elif [[ $tag_content == "/strong" || $tag_content == "/b" ]]; then
                out+="</b>"
            elif [[ $tag_content == "em" || $tag_content == "i" ]]; then
                out+="<i>"
            elif [[ $tag_content == "/em" || $tag_content == "/i" ]]; then
                out+="</i>"
            elif [[ $tag_content == "u" ]]; then
                out+="<u>"
            elif [[ $tag_content == "/u" ]]; then
                out+="</u>"
            elif [[ $tag_content =~ ^font[[:space:]]+color=[${sq}${dq}]([^${sq}${dq}]+)[${sq}${dq}] ]]; then
                out+="<span foreground=\"${BASH_REMATCH[1]}\">"
            elif [[ $tag_content == "/font" ]]; then
                out+="</span>"
            elif [[ $tag_content =~ ^span[[:space:]]+style=([${sq}${dq}])([^${sq}${dq}]+)\1 ]] || \
                 [[ $tag_content =~ ^span[[:space:]]+style=([^[:space:]${sq}${dq}]+) ]]; then
                local style="${BASH_REMATCH[2]:-${BASH_REMATCH[1]}}"
                if [[ $style =~ color[[:space:]]*:[[:space:]]*([^;]+) ]]; then
                    local col="${BASH_REMATCH[1]}"
                    col="${col%"${col##*[![:space:]]}"}"
                    out+="<span foreground=\"$col\">"
                else
                    out+="<span>"
                fi
            else
                out+="<${tag_content}>"
            fi
            out+="$rest"
        else
            out+="&lt;$part"
        fi
    done
    shopt -u nocasematch
    echo "$out"
}

if [[ "$NOTATION" == "html" ]]; then
    PANGO_TITLE=$(html_to_pango "$TITLE")
    PANGO_BODY=$(html_to_pango "$BODY")
else
    PANGO_TITLE=$(ansi_to_pango "$TITLE")
    PANGO_BODY=$(ansi_to_pango "$BODY")
fi

if command -v jq >/dev/null 2>&1; then
    NEW_OBJ=$(jq -n --arg title "$PANGO_TITLE" --arg body "$PANGO_BODY" \
        '{"id": (now * 1000 | floor), "title": $title, "body": $body, "deleted": false}')

    if [[ -s "$HISTORY_FILE" ]] && jq -e . "$HISTORY_FILE" >/dev/null 2>&1; then
        jq --argjson new "$NEW_OBJ" --arg max "$MAX_NOTIFS" \
           '[$new] + . | .[:($max | tonumber)]' "$HISTORY_FILE" > "${HISTORY_FILE}.tmp" && \
        mv "${HISTORY_FILE}.tmp" "$HISTORY_FILE"
    else
        echo "[$NEW_OBJ]" > "$HISTORY_FILE"
    fi
else
    TS=$(date +%s000)
    echo "[{\"id\": $TS, \"title\": \"$PANGO_TITLE\", \"body\": \"$PANGO_BODY\", \"deleted\": false}]" > "$HISTORY_FILE"
fi

notify-send "$PANGO_TITLE" "$PANGO_BODY"
