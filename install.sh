#!/usr/bin/env bash
# Build NC-UI split packages and install them as pacman group 'ncui' (interactive selection, Enter=all)
set -euo pipefail
cd "$(dirname "$(realpath "$0")")"
makepkg -sf --noconfirm
[[ -e /var/lib/pacman/db.lck ]] && { echo "pacman is running (db.lck present)" >&2; exit 1; }
tmp="$(mktemp -d /tmp/ncui-repo.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/repo" "$tmp/db/sync"
cp -f nc-*.pkg.tar.zst "$tmp/repo/"
repo-add -q "$tmp/repo/ncui.db.tar.gz" "$tmp"/repo/*.pkg.tar.zst
ln -s /var/lib/pacman/local "$tmp/db/local"
for f in /var/lib/pacman/sync/*.db; do ln -s "$f" "$tmp/db/sync/"; done
cp -L "$tmp/repo/ncui.db" "$tmp/db/sync/ncui.db"
{ cat /etc/pacman.conf; printf '\n[ncui]\nSigLevel = Optional TrustAll\nServer = file://%s/repo\n' "$tmp"; } > "$tmp/pacman.conf"
sudo pacman --config "$tmp/pacman.conf" --dbpath "$tmp/db" -S --needed "$@" ncui
