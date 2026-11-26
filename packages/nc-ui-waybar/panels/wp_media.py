#!/usr/bin/env python3
import glob
import json
import os
import re
from core.config import THEME, estimate_waybar_free_chars_budget
from core.i18n import t
from core import probe
from ui.canvas import Canvas
from ui.kit import Box, Txt
from panels.base import BasePanel

DEDICATED_MUSIC_PLAYERS = (
    "spotify",
    "ncspot",
    "mpd",
    "mopidy",
    "amberol",
    "rhythmbox",
    "elisa",
    "strawberry",
    "clementine",
    "audacious",
    "deadbeef",
    "feishin",
    "supersonic",
    "tauon",
    "cmus",
    "lollypop",
    "g4music",
)

MUSIC_WEB_DOMAINS = (
    "music.youtube.com",
    "open.spotify.com",
    "soundcloud.com",
    "bandcamp.com",
    "navidrome",
    "tidal.com",
    "deezer.com",
    "music.apple.com",
)

MUSIC_TITLE_KEYWORDS = re.compile(
    r"(\b(?:official\s+(?:music\s+)?video|official\s+audio|official\s+lyric|lyrics?|visualizer|full\s+album|ost|soundtrack|remix|feat\.|ft\.)\b)",
    re.IGNORECASE,
)

_BROWSER_PLAYERS = ("brave", "chromium", "chrome", "firefox", "zen", "vivaldi", "opera", "edge")

_CDN_SOURCE_RULES = (
    ("spotify", ("open.spotify.com", "spotify.com", "scdn.co", "spotifycdn.com")),
    ("youtube", ("music.youtube.com", "youtube.com", "youtu.be", "ytimg.com", "ggpht.com")),
    ("soundcloud", ("soundcloud.com", "sndcdn.com")),
    ("bandcamp", ("bandcamp.com", "bcbits.com")),
    ("apple", ("music.apple.com", "mzstatic.com")),
    ("amazon", ("music.amazon.",)),
    ("twitch", ("twitch.tv", "jtvnw.net")),
    ("lastfm", ("last.fm", "lastfm")),
    ("mixcloud", ("mixcloud.com",)),
    ("napster", ("napster.com",)),
    ("pandora", ("pandora.com",)),
    ("plex", ("plex.tv",)),
    ("deezer", ("deezer.com", "dzcdn.net")),
    ("tidal", ("tidal.com",)),
)

_SOURCE_ICONS = {
    "spotify": "\uf1bc",      # nf-fa-spotify
    "youtube": "\uf16a",      # nf-fa-youtube_play
    "soundcloud": "\uf1be",   # nf-fa-soundcloud
    "bandcamp": "\uf2d5",     # nf-fa-bandcamp
    "apple": "\uf179",        # nf-fa-apple
    "amazon": "\uf270",       # nf-fa-amazon
    "twitch": "\uf1e8",       # nf-fa-twitch
    "lastfm": "\uf202",       # nf-fa-lastfm
    "mixcloud": "\uf289",     # nf-fa-mixcloud
    "napster": "\ued0c",      # nf-fa-napster
    "pandora": "\U000f03db",  # nf-md-pandora
    "plex": "\U000f06ba",     # nf-md-plex
    "kodi": "\U000f0314",     # nf-md-kodi
    "emby": "\U000f06b4",     # nf-md-emby
    "vlc": "\U000f057c",      # nf-md-vlc
    "mpv": "\uf36e",          # nf-linux-mpv
}


def _source_from_domains(text: str) -> str:
    low = (text or "").lower()
    if not low:
        return ""
    for src, needles in _CDN_SOURCE_RULES:
        if any(n in low for n in needles):
            return src
    return ""


def _source_from_window_or_stream(text: str) -> str:
    low = (text or "").lower()
    if not low:
        return ""
    src = _source_from_domains(low)
    if src:
        return src
    if " \u2022 " in text or "spotify" in low:
        return "spotify"
    if "youtube" in low:
        return "youtube"
    if "soundcloud" in low:
        return "soundcloud"
    if "bandcamp" in low:
        return "bandcamp"
    if "apple music" in low:
        return "apple"
    if "twitch" in low:
        return "twitch"
    return ""


def _split_bullet_title(title: str, artist: str) -> tuple[str, str, bool]:
    if "\u2022" in (title or "") and not artist:
        cleaned = re.sub(r"\s*[-\u2013\u2014]\s*Spotify\s*$", "", title, flags=re.IGNORECASE).strip()
        parts = [p.strip() for p in cleaned.split("\u2022") if p.strip()]
        if len(parts) >= 2:
            return parts[0], parts[1], True
    return title, artist, False


def _read_niri_windows() -> list[dict]:
    if not probe.has_bin("niri"):
        return []
    ok, out = probe.run_cmd(["niri", "msg", "-j", "windows"], timeout=0.8)
    if not ok or not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _read_pactl_sink_inputs() -> list[dict]:
    if not probe.has_bin("pactl"):
        return []
    ok, out = probe.run_cmd(["pactl", "-f", "json", "list", "sink-inputs"], timeout=0.8)
    if not ok or not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _snss_session_files(player: str) -> list[str]:
    cfg_base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    cfg_base = os.path.expanduser(cfg_base) if cfg_base else os.path.expanduser("~/.config")
    p_low = (player or "").lower()
    if "brave" in p_low:
        rels = ["BraveSoftware/Brave-Browser"]
    elif "chrome" in p_low:
        rels = ["google-chrome"]
    elif "chromium" in p_low:
        rels = ["chromium"]
    elif "vivaldi" in p_low:
        rels = ["vivaldi"]
    elif "edge" in p_low:
        rels = ["microsoft-edge"]
    else:
        rels = ["BraveSoftware/Brave-Browser", "google-chrome", "chromium"]
    cand = []
    for rel in rels:
        base = os.path.join(cfg_base, rel)
        if not os.path.isdir(base):
            continue
        for pat in ("*/Sessions/Session_*", "*/Sessions/Tabs_*"):
            cand.extend(glob.glob(os.path.join(base, pat)))
    cand.sort(key=os.path.getmtime, reverse=True)
    return cand[:2]


def _detect_snss_source(player: str, title: str, artist: str, album: str) -> str:
    files = _snss_session_files(player)
    if not files:
        return ""
    needles = []
    if title:
        needles.append(title[:32].encode("utf-16le", errors="ignore"))
    if artist:
        needles.append(artist[:24].encode("utf-16le", errors="ignore"))
    needles = [n for n in needles if len(n) >= 4]
    for path in files:
        try:
            with open(path, "rb") as f:
                data = f.read(4 * 1024 * 1024)
        except OSError:
            continue
        for nd in needles:
            pos = data.rfind(nd)
            if pos != -1:
                win = data[max(0, pos - 512) : pos].decode("latin1", errors="ignore")
                src = _source_from_domains(win)
                if src:
                    return src
        if artist and album and b"open.spotify.com" in data and b"music.youtube.com" not in data:
            return "spotify"
    return ""


def _detect_browser_media_source(
    player: str,
    title: str,
    artist: str,
    album: str,
    url: str,
    art_url: str,
    track_id: str,
    is_bullet_spotify: bool = False,
) -> str:
    if is_bullet_spotify:
        return "spotify"
    for txt in (url, art_url, track_id):
        src = _source_from_domains(txt)
        if src:
            return src
    if "spotify" in (track_id or "").lower():
        return "spotify"
    if artist.endswith(" - Topic") or "vevo" in (artist or "").lower():
        return "youtube"

    p_low = (player or "").lower()
    if not any(b in p_low for b in _BROWSER_PLAYERS):
        return ""

    t_low = (title or "").lower()
    a_low = (artist or "").lower()
    for w in _read_niri_windows():
        app_id = str(w.get("app_id") or "")
        w_title = str(w.get("title") or "")
        combined = f"{app_id} {w_title}".lower()
        if p_low not in combined and not any(b in combined for b in _BROWSER_PLAYERS):
            continue
        if (t_low and t_low in w_title.lower()) or (a_low and a_low in w_title.lower()) or "spotify" in combined:
            src = _source_from_window_or_stream(f"{app_id} {w_title}")
            if src:
                return src

    for item in _read_pactl_sink_inputs():
        if item.get("corked", False):
            continue
        props = item.get("properties") or {}
        if props.get("media.class") == "Stream/Input/Audio":
            continue
        app = f"{props.get('application.name', '')} {props.get('application.process.binary', '')}".lower()
        if not any(b in app for b in _BROWSER_PLAYERS):
            continue
        src = _source_from_window_or_stream(str(props.get("media.name") or ""))
        if src:
            return src

    return _detect_snss_source(player, title, artist, album)


def _fallback_pactl_browser_music() -> tuple[str, str, str] | None:
    has_uncorked_browser = False
    for item in _read_pactl_sink_inputs():
        if item.get("corked", False):
            continue
        props = item.get("properties") or {}
        if props.get("media.class") == "Stream/Input/Audio":
            continue
        app = f"{props.get('application.name', '')} {props.get('application.process.binary', '')}".lower()
        if any(b in app for b in _BROWSER_PLAYERS):
            has_uncorked_browser = True
            mname = str(props.get("media.name") or "").strip()
            if " \u2022 " in mname:
                song, art, ok = _split_bullet_title(mname, "")
                if ok:
                    return "spotify", art, song
    if not has_uncorked_browser:
        return None
    for w in _read_niri_windows():
        app_id = str(w.get("app_id") or "").lower()
        w_title = str(w.get("title") or "").strip()
        if any(b in app_id for b in _BROWSER_PLAYERS) or "spotify" in app_id:
            clean_w = re.sub(r"\s*[-\u2013\u2014]\s*(?:Brave|Chromium|Google Chrome|Mozilla Firefox|Firefox|Zen|Vivaldi|Opera|Edge)\s*$", "", w_title, flags=re.IGNORECASE).strip()
            if "\u2022" in clean_w:
                song, art, ok = _split_bullet_title(clean_w, "")
                if ok:
                    return "spotify", art, song
    return None


def _is_music_stream(player_name: str, artist: str, title: str, album: str, url: str, detected_src: str = "") -> bool:
    p_low = player_name.lower()
    if any(dp in p_low for dp in DEDICATED_MUSIC_PLAYERS):
        return True

    if detected_src in ("spotify", "soundcloud", "bandcamp", "apple", "amazon", "pandora", "deezer", "tidal"):
        return True

    u_low = url.lower()
    if any(dom in u_low for dom in MUSIC_WEB_DOMAINS):
        return True

    if artist.endswith(" - Topic") or "vevo" in artist.lower():
        return True
    if artist and album:
        return True
    if detected_src == "youtube" or "youtube.com" in u_low or "youtu.be" in u_low:
        if "music.youtube.com" in u_low:
            return True
        if MUSIC_TITLE_KEYWORDS.search(title):
            return True
        if " - " in title and artist and artist.lower() not in title.lower():
            return True
        return False

    return bool(artist and title)


def _resolve_media_icon(player: str, url: str, artist: str = "", source: str = "") -> str:
    if not probe.has_nerd_font():
        return "\uf001"
    if source in _SOURCE_ICONS:
        return _SOURCE_ICONS[source]

    u_low = (url or "").lower()
    p_low = (player or "").lower()
    a_low = (artist or "").lower()

    if "spotify" in p_low or "ncspot" in p_low or "spotify.com" in u_low:
        return _SOURCE_ICONS["spotify"]
    if "youtube" in p_low or "youtube.com" in u_low or "youtu.be" in u_low or artist.endswith(" - Topic") or "vevo" in a_low:
        return _SOURCE_ICONS["youtube"]
    for key, icon in _SOURCE_ICONS.items():
        if key in p_low or (key in u_low and key not in ("vlc", "mpv", "kodi", "emby")):
            return icon
    return "\uf001"


def get_playing_music_metadata(max_chars: int = None) -> str:
    if max_chars is None:
        budget = estimate_waybar_free_chars_budget()
        max_chars = int(budget.get("media_chars", 48))

    candidates = []
    if probe.has_bin("playerctl"):
        fmt = "{{playerName}}\t{{status}}\t{{xesam:artist}}\t{{xesam:title}}\t{{xesam:album}}\t{{xesam:url}}\t{{mpris:artUrl}}\t{{mpris:trackid}}"
        _ok, out = probe.run_cmd(["playerctl", "-a", "metadata", "--format", fmt], timeout=1.2)
        for line in (out or "").splitlines():
            parts = [p.strip() for p in (line.split("\t") + [""] * 8)[:8]]
            player, status, artist, title, album, url, art_url, track_id = parts
            if status != "Playing" or not title:
                continue
            title, artist, is_bullet_spotify = _split_bullet_title(title, artist)
            detected_src = _detect_browser_media_source(
                player, title, artist, album, url, art_url, track_id, is_bullet_spotify=is_bullet_spotify
            )
            if not _is_music_stream(player, artist, title, album, url, detected_src=detected_src):
                continue

            is_dedicated = any(dp in player.lower() for dp in DEDICATED_MUSIC_PLAYERS)
            clean_artist = re.sub(r"\s*-\s*Topic$", "", artist).strip()
            candidates.append((0 if is_dedicated else 1, player, clean_artist, title, url, artist, detected_src))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        _, player, artist, title, url, raw_artist, detected_src = candidates[0]
        ic = _resolve_media_icon(player, url, raw_artist, source=detected_src)
    else:
        fb = _fallback_pactl_browser_music()
        if not fb:
            return ""
        detected_src, artist, title = fb
        ic = _resolve_media_icon("", "", artist, source=detected_src)

    if artist and artist.lower() not in title.lower():
        full_str = f"{artist} \u2014 {title}"
    else:
        full_str = title

    if len(full_str) > max_chars:
        full_str = full_str[: max(1, max_chars - 1)] + "\u2026"

    return f"{Txt(ic, role='focus')}  {Txt(full_str, role='focus')}"


class Panel(BasePanel):
    id = "media"
    icon = "\uf001"
    supports_expand = False

    def __init__(self, shell=None):
        self.title = t("cc_waybar.p_custom_media_lbl") if shell is not None else self.id
        super().__init__(shell)

    def get_cols(self, force_expanded: bool = False) -> int:
        return int(THEME["geometry"]["compact_cols"])

    def get_bar_pill(self) -> dict:
        markup = get_playing_music_metadata()
        return {
            "text": markup,
            "tooltip": "",
            "class": "module-nc-media" if markup else "module-nc-media-empty",
        }

    def build_cards(self, canvas: Canvas, force_expanded: bool = False) -> list:
        target_cols = self.get_cols()
        canvas.set_cols(target_cols)
        box = Box(
            t("cc_waybar.p_custom_media_lbl"),
            icon=self.icon,
            highlighted=False,
            fixed_width=True,
            min_cols=target_cols,
            max_cols=target_cols,
        )
        box.add_row(" ")
        now_m = get_playing_music_metadata(max_chars=target_cols - 6)
        box.add_row(f"  {now_m or Txt(t('media.idle'), role='inactive')}")
        box.add_row(" ")
        return [box]


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return Panel(shell)
