# Maintainer: NC-UI Contributors
pkgbase=nc-ui
pkgname=(
    'nc-ui'
    'nc-ui-core'
    'nc-ui-control-center'
    'nc-ui-waybar'
    'nc-ui-notification'
    'nc-ui-input-daemons'
    'nc-resilience'
    'nc-bootloader'
    'nc-calendar'
    'nc-nftables'
    'nc-nut'
    'nc-print'
    'nc-asciifier'
    'nc-video-wallpaper'
    'nc-matrix'
)
pkgver=1.0.0
pkgrel=5
arch=('x86_64')
url='https://github.com/reddh4wk/ncui'
license=('MIT')
makedepends=('python' 'gcc' 'pkgconf' 'gtk3' 'gtk-layer-shell' 'pango' 'cairo' 'libepoxy' 'ncurses' 'wayland' 'libglvnd')
source=()

_install_py_dir() {
    local src_dir="$1"
    local rel_dst="$2"
    install -d "$pkgdir/usr/lib/ncui/$rel_dst"
    local f
    for f in "$src_dir"/*.py; do
        [[ -f "$f" ]] || continue
        install -m644 "$f" "$pkgdir/usr/lib/ncui/$rel_dst/$(basename "$f")"
    done
}

build() {
    rm -rf "$srcdir/nc-matrix"
    cp -r "$startdir/packages/nc-wallpapers/nc-matrix" "$srcdir/nc-matrix"
    make -C "$srcdir/nc-matrix" OPT_FLAGS="" TARGET=nc-matrix
}

_compile_pkg_py() {
    if [[ -d "$pkgdir/usr/lib/ncui" ]]; then
        python -m compileall -q -f -d /usr/lib/ncui "$pkgdir/usr/lib/ncui"
    fi
}

package_nc-ui() {
    arch=('any')
    pkgdesc='NC-UI complete desktop Control Center, Waybar popup and resilience suite (meta-package)'
    depends=(
        'nc-ui-core'
        'nc-ui-control-center'
        'nc-ui-waybar'
        'nc-ui-notification'
        'nc-ui-input-daemons'
        'nc-resilience'
        'nc-bootloader'
        'nc-calendar'
        'nc-nftables'
        'nc-nut'
        'nc-print'
        'nc-asciifier'
        'nc-video-wallpaper'
        'nc-matrix'
        'swaync'
        'cpupower'
        'alsa-utils'
    )
    :
}

package_nc-ui-core() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI core runtime, Cairo/Pango UI kit, locales and default schemas'
    install=nc-ui.install
    depends=('python' 'python-gobject' 'python-cairo' 'gtk3' 'gtk-layer-shell' 'cairo' 'pango' 'gsfonts' 'ttf-nerd-fonts-symbols')
    optdepends=(
        'awww: static/animated Wayland wallpaper daemon'
        'libvterm: embedded terminal TUI panel support'
        'xdg-utils: open directories and external URLs'
        'otf-font-awesome: extra icon glyph coverage'
        'ttf-dejavu: fallback sans/mono coverage'
    )

    local _src="$startdir/packages"

    install -Dm755 "$_src/nc-ui-core/bin/nc-ctl" "$pkgdir/usr/bin/nc-ctl"

    _install_py_dir "$_src/nc-ui-core/core" "core"
    _install_py_dir "$_src/nc-ui-core/ui" "ui"
    _install_py_dir "$_src/nc-ui-core/panels" "panels"
    _compile_pkg_py

    install -d "$pkgdir/usr/share/ncui/locales/help/en" "$pkgdir/usr/share/ncui/locales/help/it"
    install -m644 "$_src/nc-ui-core/locales/en.toml" "$_src/nc-ui-core/locales/it.toml" \
        "$pkgdir/usr/share/ncui/locales/"
    install -m644 "$_src/nc-ui-core/locales/help/en.toml" "$_src/nc-ui-core/locales/help/it.toml" \
        "$pkgdir/usr/share/ncui/locales/help/"
    install -m644 "$_src/nc-ui-core/locales/help/en"/*.toml "$pkgdir/usr/share/ncui/locales/help/en/"
    install -m644 "$_src/nc-ui-core/locales/help/it"/*.toml "$pkgdir/usr/share/ncui/locales/help/it/"

    install -d "$pkgdir/usr/share/ncui/assets/cursors/ncui-cursor"
    install -m644 "$_src/nc-ui-core/assets/cursors/ncui-cursor"/* \
        "$pkgdir/usr/share/ncui/assets/cursors/ncui-cursor/"

    install -d "$pkgdir/usr/share/ncui/config/ncui"
    install -m644 \
        "$_src/nc-ui-core/config/ncui/config.toml" \
        "$_src/nc-ui-core/config/ncui/theme.toml" \
        "$_src/nc-ui-core/config/ncui/firewall_rules.json" \
        "$_src/nc-ui-core/config/ncui/notifications_rules.json" \
        "$_src/nc-ui-core/config/ncui/resilience.example.toml" \
        "$_src/nc-ui-core/config/ncui/shortcuts_store.json" \
        "$_src/nc-ui-core/config/ncui/sudoers_state.json" \
        "$pkgdir/usr/share/ncui/config/ncui/"

    install -Dm644 "$_src/nc-wallpapers/nc-matrix/LICENSE" "$pkgdir/usr/share/licenses/nc-ui-core/LICENSE"
}

package_nc-ui-control-center() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI desktop Control Center panels, engines and privileged helpers'
    depends=('nc-ui-core')
    optdepends=(
        'niri: Wayland compositor integration'
        'ghostty: default terminal emulator'
        'pipewire: audio management'
        'wireplumber: audio session management'
        'networkmanager: network management'
        'bluez-utils: Bluetooth management'
        'brightnessctl: laptop display backlight'
        'pacman-contrib: checkupdates and paccache'
        'flatpak: Flatpak updates tab'
        'usbguard: USBGuard policy management'
        'sbctl: Secure Boot inspection'
        'wireguard-tools: WireGuard VPN interface control'
        'smartmontools: disk S.M.A.R.T. health telemetry'
        'nvme-cli: NVMe drive health and temperature telemetry'
        'lm_sensors: hardware thermal and fan sensors'
        'btop: embedded system resource monitor TUI'
        'nvtop: embedded GPU monitor TUI'
        'polkit-gnome: PolicyKit authentication agent'
        'cpupower: CPU governor/EPP control'
        'alsa-utils: speaker-test channel test'
    )

    local _src="$startdir/packages"

    install -Dm755 "$_src/nc-ui-control-center/bin/nc-nightlight" "$pkgdir/usr/bin/nc-nightlight"

    install -d "$pkgdir/usr/lib/ncui/sbin"
    install -m755 \
        "$_src/nc-ui-control-center/libexec/ncui/nc-disks" \
        "$_src/nc-ui-control-center/libexec/ncui/nc-fan-profile" \
        "$_src/nc-ui-control-center/libexec/ncui/nc-posture" \
        "$_src/nc-ui-control-center/libexec/ncui/nc-usbguard" \
        "$pkgdir/usr/lib/ncui/sbin/"

    _install_py_dir "$_src/nc-ui-control-center/panels" "panels"
    _compile_pkg_py
}

package_nc-ui-waybar() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI Waybar bar agent, layer-shell popups and layout manager'
    depends=('nc-ui-core' 'waybar')
    optdepends=(
        'wl-clipboard: clipboard popup'
        'cliphist: clipboard history'
        'grim: screenshot capture'
        'slurp: region selection'
        'satty: interactive screenshot annotation'
        'playerctl: MPRIS media player metadata and controls'
    )

    local _src="$startdir/packages"

    install -d "$pkgdir/usr/bin"
    install -m755 \
        "$_src/nc-ui-waybar/bin/nc-bar-agent" \
        "$_src/nc-ui-waybar/bin/nc-telegram-status" \
        "$pkgdir/usr/bin/"

    _install_py_dir "$_src/nc-ui-waybar/panels" "panels"
    _compile_pkg_py

    install -d "$pkgdir/usr/share/ncui/config/waybar"
    install -m644 \
        "$_src/nc-ui-waybar/config/waybar/config.jsonc" \
        "$_src/nc-ui-waybar/config/waybar/style.css" \
        "$pkgdir/usr/share/ncui/config/waybar/"
}

package_nc-ui-notification() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI notification daemon helper, popup host and history panel'
    depends=('nc-ui-core')
    optdepends=(
        'libnotify: desktop notifications via notify-send'
        'jq: fast notification history updates'
        'swaync: SwayNotificationCenter integration (swaync-client)'
    )

    local _src="$startdir/packages"

    install -d "$pkgdir/usr/bin"
    install -m755 \
        "$_src/nc-ui-notification/bin/nc-notify" \
        "$_src/nc-ui-notification/bin/nc-notify-popup" \
        "$_src/nc-ui-notification/bin/nc-record-notification" \
        "$pkgdir/usr/bin/"

    _install_py_dir "$_src/nc-ui-notification/panels" "panels"
    _compile_pkg_py
}

package_nc-ui-input-daemons() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI uinput macro player, controller mapper daemon and input panels'
    depends=('nc-ui-core')
    optdepends=('evtest: input device event inspection')

    local _src="$startdir/packages"

    install -d "$pkgdir/usr/bin"
    install -m755 \
        "$_src/nc-ui-input-daemons/bin/nc-macro-play" \
        "$_src/nc-ui-input-daemons/bin/nc-pad-mapper" \
        "$pkgdir/usr/bin/"

    _install_py_dir "$_src/nc-ui-input-daemons/core" "core"
    _install_py_dir "$_src/nc-ui-input-daemons/panels" "panels"
    _compile_pkg_py
}

package_nc-resilience() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI system resilience suite: LVM snapshots, vault sync, golden images and recovery UKI'
    depends=('nc-ui-core' 'bash')
    optdepends=(
        'lvm2: LVM snapshot and rollback engine'
        'cryptsetup: LUKS2 encrypted root unlock and chroot'
        'limine: recovery UKI boot registration'
        'sbctl: Secure Boot UKI signing'
        'rsync: vault backup synchronization'
        'cifs-utils: SMB/CIFS NAS backup mounts'
        'nfs-utils: NFS NAS backup mounts'
        'squashfs-tools: Live RAM recovery SquashFS build and chroot edit'
        'arch-install-scripts: Arch Live Recovery rootfs bootstrap (pacstrap/arch-chroot)'
        'pv: progress metering for golden image capture/restore'
        'zstd: high-ratio compression for golden images and recovery SquashFS'
    )

    local _src="$startdir/packages"

    install -d "$pkgdir/usr/lib/ncui/sbin" "$pkgdir/usr/bin"
    install -m755 \
        "$_src/nc-resilience/libexec/ncui/nc-chroot" \
        "$_src/nc-resilience/libexec/ncui/nc-golden" \
        "$_src/nc-resilience/libexec/ncui/nc-hanoi" \
        "$_src/nc-resilience/libexec/ncui/nc-rescue" \
        "$_src/nc-resilience/libexec/ncui/nc-snap" \
        "$_src/nc-resilience/libexec/ncui/nc-vault" \
        "$pkgdir/usr/lib/ncui/sbin/"

    local cli
    for cli in nc-chroot nc-golden nc-hanoi nc-rescue nc-snap nc-vault; do
        ln -s "/usr/lib/ncui/sbin/$cli" "$pkgdir/usr/bin/$cli"
    done

    _install_py_dir "$_src/nc-resilience/panels" "panels"
    _compile_pkg_py
}

package_nc-bootloader() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI bootloader, kernel cmdline and UKI management panels'
    depends=('nc-ui-core')
    optdepends=('limine: Limine bootloader management' 'sbctl: Secure Boot status')

    local _src="$startdir/packages"
    _install_py_dir "$_src/nc-ui-tools/nc-bootloader/panels" "panels"
    _compile_pkg_py
}

package_nc-calendar() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI calendar panel and Waybar calendar popup'
    depends=('nc-ui-core')

    local _src="$startdir/packages"
    _install_py_dir "$_src/nc-ui-tools/nc-calendar/panels" "panels"
    _compile_pkg_py
}

package_nc-nftables() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI nftables firewall rules, chains and connections panels'
    depends=('nc-ui-core' 'nftables')

    local _src="$startdir/packages"
    _install_py_dir "$_src/nc-ui-tools/nc-nftables/panels" "panels"
    _compile_pkg_py
}

package_nc-nut() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI Network UPS Tools (NUT) monitoring panel and popup'
    depends=('nc-ui-core')
    optdepends=('nut: UPS monitoring via upsc')

    local _src="$startdir/packages"
    _install_py_dir "$_src/nc-ui-tools/nc-nut/panels" "panels"
    _compile_pkg_py
}

package_nc-print() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI CUPS printer discovery, queue and server panels'
    depends=('nc-ui-core')
    optdepends=(
        'cups: CUPS print server and queues'
        'cups-filters: printer driver filter chain'
    )

    local _src="$startdir/packages"
    _install_py_dir "$_src/nc-ui-tools/nc-print/panels" "panels"
    _compile_pkg_py
}

package_nc-asciifier() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI image/frame ASCII art wallpaper renderer'
    depends=('nc-ui-core' 'python-pillow')

    local _src="$startdir/packages"
    _install_py_dir "$_src/nc-wallpapers/nc-asciifier/core" "core"
    _compile_pkg_py
}

package_nc-video-wallpaper() {
    arch=('any')
    groups=('ncui')
    pkgdesc='NC-UI Wayland animated video wallpaper daemon'
    depends=('nc-ui-core' 'ffmpeg')
    optdepends=(
        'awww: static/animated Wayland wallpaper daemon'
        'yt-dlp: YouTube wallpaper sources'
    )

    local _src="$startdir/packages"
    install -Dm755 "$_src/nc-wallpapers/nc-video-wallpaper/bin/nc-video-wallpaper" \
        "$pkgdir/usr/bin/nc-video-wallpaper"
}

package_nc-matrix() {
    pkgdesc='High-performance C11/EGL/Wayland & TTY Matrix digital rain engine'
    groups=('ncui')
    depends=('gtk3' 'gtk-layer-shell' 'pango' 'cairo' 'libepoxy' 'ncurses' 'wayland' 'libglvnd' 'gcc-libs')
    optdepends=(
        'otf-ipafont: Japanese Katakana glyph support (lightweight)'
        'noto-fonts-cjk: full CJK and Katakana glyph support'
    )
    conflicts=('ncmatrix')
    replaces=('ncmatrix')

    make -C "$srcdir/nc-matrix" DESTDIR="$pkgdir" PREFIX=/usr TARGET=nc-matrix install
}
