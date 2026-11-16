#!/usr/bin/env python3
import json
import os

HIST_FILE = os.path.expanduser("~/.cache/nc_notifications.json")


def load_notifs() -> list[dict]:
    if not os.path.exists(HIST_FILE):
        return []
    try:
        with open(HIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_notifs(notifs: list[dict]) -> None:
    try:
        with open(HIST_FILE, "w", encoding="utf-8") as f:
            json.dump(notifs, f)
    except Exception:
        pass


def get_active_notifs() -> list[dict]:
    return [n for n in load_notifs() if not n.get("deleted")]


def clear_all_notifs() -> None:
    notifs = load_notifs()
    for n in notifs:
        n["deleted"] = True
    save_notifs(notifs)


def delete_notif(target_id: str) -> None:
    notifs = load_notifs()
    for n in notifs:
        if str(n.get("id")) == str(target_id):
            n["deleted"] = True
    save_notifs(notifs)
