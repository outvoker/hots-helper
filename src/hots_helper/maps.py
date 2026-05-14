"""Canonical Heroes of the Storm map names (zh-CN client).

Used to populate UI dropdowns. We intentionally hard-code the active-
rotation maps in the order they typically show up in the in-game
battleground picker, rather than scraping ``replays.map_name`` from
the local DB — that way the dropdown is consistent across squad
members regardless of who has played what, and we get sensible
ordering instead of by-popularity which changes every week.

Two pools:

* ``STORM_LEAGUE_MAPS`` — battlegrounds the SL queue rotates through.
* ``ARAM_MAPS`` — fixed pool of single-lane brawl maps.

If Blizzard adds a new map and we forget to update this file, the user
can still type the name in the popup's editable combobox; the SL
ranking dialog includes an "All maps" entry that picks up everything.
"""

from __future__ import annotations

# Order roughly matches the in-game lobby: alphabetical by Chinese name.
STORM_LEAGUE_MAPS: tuple[str, ...] = (
    "奥特兰克战道",       # Alterac Pass
    "巨龙镇",            # Dragon Shire
    "末日塔",            # Towers of Doom
    "黑心湾",            # Blackheart's Bay
    "诅咒谷",            # Cursed Hollow
    "蛛后墓",            # Tomb of the Spider Queen
    "炼狱圣坛",          # Infernal Shrines
    "花村寺",            # Hanamura Temple
    "天空殿",            # Sky Temple
    "永恒战场",          # Battlefield of Eternity
    "恐魔园",            # Garden of Terror
    "弹头枢纽站",        # Warhead Junction
    "沃斯卡娅铸造厂",     # Volskaya Foundry
    "布莱克西斯禁区",     # Braxis Holdout
)

ARAM_MAPS: tuple[str, ...] = (
    "白银城",            # Silver City
    "失落洞窟",          # Lost Cavern
    "布莱克西斯前哨",     # Braxis Outpost
    "工业园区",          # Industrial District
)


def all_maps() -> tuple[str, ...]:
    """Every map we know about, SL pool first."""
    return STORM_LEAGUE_MAPS + ARAM_MAPS
