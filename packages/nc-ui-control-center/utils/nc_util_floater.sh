#!/usr/bin/env bash

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    echo -e "Uso: nc-niri-floater.sh\nTool interattivo per la gestione delle regole floating di Niri."
    exit 0
fi

CONFIG_FILE="${HOME}/.config/niri/config.kdl"

C_RESET='\e[0m'
C_TITLE='\e[1;38;2;203;166;247m'
C_HEAD='\e[1;38;2;137;180;250m'
C_ACTIVE='\e[38;2;166;227;161m'
C_FLOAT='\e[38;2;249;226;175m'
C_PROMPT='\e[1;38;2;137;220;250m'
C_ERR='\e[38;2;243;139;168m'

get_active_apps() {
    niri msg windows 2>/dev/null | grep -i "App ID:" | awk -F'"' '{print $2}' | sort -u
}

get_floating_apps() {
    if [[ -f "$CONFIG_FILE" ]]; then
        grep -B 2 'open-floating true' "$CONFIG_FILE" 2>/dev/null | grep 'match app-id=' | cut -d'"' -f2 | sed 's/\\\\././g' | sort -u
    fi
}

draw_ui() {
    clear
    echo -e "${C_TITLE}=== Niri Floating Window Manager ===${C_RESET}\n"

    mapfile -t active_list < <(get_active_apps)
    mapfile -t float_list < <(get_floating_apps)

    declare -g -a index_map=()
    declare -g -a type_map=()
    declare -g idx=0

    declare -a filtered_active=()
    for app in "${active_list[@]}"; do
        is_float=0
        for f_app in "${float_list[@]}"; do
            [[ "$app" == "$f_app" ]] && is_float=1 && break
        done
        [[ $is_float -eq 0 && -n "$app" ]] && filtered_active+=("$app")
    done

    printf "${C_HEAD}%-5s | %-15s | %s${C_RESET}\n" "ID" "STATO" "APP-ID"
    echo "-------------------------------------------------------"

    for app in "${filtered_active[@]}"; do
        printf "${C_RESET}%-5d | ${C_ACTIVE}%-15s${C_RESET} | %s\n" "$idx" "[Attiva]" "$app"
        index_map[$idx]="$app"
        type_map[$idx]="active"
        ((idx++))
    done

    for app in "${float_list[@]}"; do
        [[ -z "$app" ]] && continue
        printf "${C_RESET}%-5d | ${C_FLOAT}%-15s${C_RESET} | %s\n" "$idx" "[Galleggiante]" "$app"
        index_map[$idx]="$app"
        type_map[$idx]="floating"
        ((idx++))
    done
    echo ""
}

while true; do
    draw_ui
    echo -en "${C_PROMPT}Seleziona ID, premi Invio vuoto per aggiornare o 'q' per uscire: ${C_RESET}"
    read -r choice

    if [[ -z "$choice" ]]; then continue; fi
    if [[ "${choice,,}" == "q" ]]; then echo -e "\n${C_ACTIVE}Uscita.${C_RESET}"; exit 0; fi

    if ! [[ "$choice" =~ ^[0-9]+$ ]] || [[ "$choice" -ge "$idx" ]]; then
        echo -e "\n${C_ERR}Errore: Inserimento non valido. Premi Invio per riprovare.${C_RESET}"
        read -r; continue
    fi

    target_app="${index_map[$choice]}"
    target_type="${type_map[$choice]}"
    esc_app=$(echo "$target_app" | sed 's/\./\\\\./g')

    if [[ "$target_type" == "active" ]]; then
        echo -en "\n${C_PROMPT}Impostare '${target_app}' come FLOATING? [Invio=Conferma, Altro=Annulla]${C_RESET} "
        read -rsn1 confirm
        if [[ "$confirm" == "" ]]; then
            cat << RULE >> "$CONFIG_FILE"

window-rule {
    match app-id="${esc_app}"
    open-floating true
}
RULE
            echo -e "\n${C_ACTIVE}Regola aggiunta con successo.${C_RESET}"
            sleep 1
        else
            echo -e "\n${C_ERR}Operazione annullata.${C_RESET}"
            sleep 1
        fi
    elif [[ "$target_type" == "floating" ]]; then
        echo -en "\n${C_PROMPT}Rimuovere '${target_app}' dalle regole FLOATING? [Invio=Conferma, Altro=Annulla]${C_RESET} "
        read -rsn1 confirm
        if [[ "$confirm" == "" ]]; then
            perl -0777 -pi -e 's/\n*window-rule\s*\{\s*match app-id="\Q'"${esc_app}"'\E"\s*open-floating true\s*\}//gs' "$CONFIG_FILE"
            echo -e "\n${C_ACTIVE}Regola rimossa con successo.${C_RESET}"
            sleep 1
        else
            echo -e "\n${C_ERR}Operazione annullata.${C_RESET}"
            sleep 1
        fi
    fi
done
