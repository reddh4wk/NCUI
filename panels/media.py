#!/usr/bin/env python3
import re
from core.config import estimate_waybar_free_chars_budget
from core import probe
from ui.kit import Txt
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


def _is_music_stream(player_name: str, artist: str, title: str, album: str, url: str) -> bool:
    p_low = player_name.lower()
    if any(dp in p_low for dp in DEDICATED_MUSIC_PLAYERS):
        return True

    u_low = url.lower()
    if any(dom in u_low for dom in MUSIC_WEB_DOMAINS):
        return True

    if artist.endswith(" - Topic") or "vevo" in artist.lower():
        return True
    if artist and album:
        return True
    if "youtube.com" in u_low or "youtu.be" in u_low:
        if MUSIC_TITLE_KEYWORDS.search(title):
            return True
        if " - " in title and artist and artist.lower() not in title.lower():
            return True
        return False

    return bool(artist and title)


def get_playing_music_metadata(max_chars: int = None) -> str:
    if not probe.has_bin("playerctl"):
        return ""

    if max_chars is None:
        budget = estimate_waybar_free_chars_budget()
        max_chars = int(budget.get("media_chars", 48))

    fmt = "{{playerName}}\t{{status}}\t{{xesam:artist}}\t{{xesam:title}}\t{{xesam:album}}\t{{xesam:url}}"
    ok, out = probe.run_cmd(["playerctl", "-a", "metadata", "--format", fmt], timeout=1.2)
    if not ok or not out:
        return ""

    candidates = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        player = parts[0].strip()
        status = parts[1].strip()
        artist = parts[2].strip()
        title = parts[3].strip()
        album = parts[4].strip() if len(parts) > 4 else ""
        url = parts[5].strip() if len(parts) > 5 else ""

        if status != "Playing" or not title:
            continue
        if not _is_music_stream(player, artist, title, album, url):
            continue

        is_dedicated = any(dp in player.lower() for dp in DEDICATED_MUSIC_PLAYERS)
        clean_artist = re.sub(r"\s*-\s*Topic$", "", artist).strip()
        candidates.append((0 if is_dedicated else 1, player, clean_artist, title))

    if not candidates:
        return ""

    candidates.sort(key=lambda x: x[0])
    _, player, artist, title = candidates[0]

    if artist and artist.lower() not in title.lower():
        full_str = f"{artist} \u2014 {title}"
    else:
        full_str = title

    if len(full_str) > max_chars:
        full_str = full_str[: max(1, max_chars - 1)] + "\u2026"

    ic = "\uf1bc" if "spotify" in player.lower() else "\uf001"
    return f"{Txt(ic, role='focus')}  {Txt(full_str, role='focus')}"


class Panel(BasePanel):
    id = "media"
    title = "Media"
    icon = "\uf001"

    def get_bar_pill(self) -> dict:
        markup = get_playing_music_metadata()
        return {
            "text": markup,
            "tooltip": "",
            "class": "module-nc-media" if markup else "module-nc-media-empty",
        }


def create_panel(panel_id: str, shell=None) -> BasePanel:
    return Panel(shell)
