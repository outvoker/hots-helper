"""Parse a Heroes of the Storm ``.StormReplay`` file into structured records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from mpyq import MPQArchive

from .protocols import load_latest_protocol, load_protocol_for_build


class ReplayParseError(Exception):
    pass


# --- utility ------------------------------------------------------------------


def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("utf-8", errors="replace")
    return value


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Blizzard uses "Windows filetime": 100-nanosecond ticks since 1601-01-01 UTC.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _filetime_to_dt(filetime: int) -> datetime:
    return _FILETIME_EPOCH + timedelta(microseconds=filetime // 10)


# --- game mode detection ------------------------------------------------------

_AMM_ID_TO_MODE = {
    50001: "Quick Match",
    50021: "Try Mode",
    50031: "Brawl",
    50041: "Hero League",
    50051: "Team League",
    50061: "Unranked Draft",
    50071: "ARAM",
    50091: "Storm League",
    50101: "ARAM",
}

# Maps that are exclusively used for ARAM (天命乱斗) on the CN client.
# Anything mode-tagged ARAM but on a different map is misclassified and
# should fall back to Quick Match (most likely) or whatever the ammId says.
_ARAM_MAPS = {
    "白银城",          # Silver City
    "失落洞窟",        # Lost Cavern
    "布莱克西斯前哨",   # Braxis Outpost
    "工业园区",        # Industrial District
    "Lost Cavern", "Silver City", "Industrial District", "Braxis Outpost",
}


def _infer_mode(
    game_opts: dict[str, Any],
    attr_map: dict[int, str],
    map_name: str = "",
) -> str:
    """Classify game mode from the lobby + attribute events + map name.

    The naive ``heroDuplicatesAllowed → ARAM`` rule fails on the live
    client: Quick Match also allows duplicates, and so does Try Mode.
    We use a layered decision tree instead, with the ARAM map whitelist
    as the strongest signal (HotS only runs ARAM on a fixed pool of
    maps).
    """
    amm = game_opts.get("m_amm", False)
    amm_id = int(game_opts.get("m_ammId") or 0)
    competitive = game_opts.get("m_competitive", False)
    dupes = game_opts.get("m_heroDuplicatesAllowed", False)
    pick_mode = attr_map.get(4010) or attr_map.get(4015) or ""

    if not amm:
        return "Custom"

    # Strongest signals first.
    if amm_id == 50091:
        return "Storm League"
    if amm_id == 50021:
        return "Try Mode"
    if amm_id == 50031:
        return "Brawl"
    if amm_id == 50051:
        return "Team League"
    if amm_id == 50061:
        return "Unranked Draft"

    # ARAM detection: must be on an ARAM map. Otherwise duplicate-allowed
    # games are Quick Match (which also allows dupes for fill).
    if map_name in _ARAM_MAPS:
        return "ARAM"

    # Storm League ammId may not be 50091 on every patch — fall back to
    # the structural signals.
    if competitive and not dupes and pick_mode == "drft":
        return "Storm League"

    if amm_id == 50001:
        return "Quick Match"
    if amm_id in _AMM_ID_TO_MODE:
        # Fallback for any other amm_id we know about, EXCEPT ARAM ids
        # which we already gated on the map whitelist.
        m = _AMM_ID_TO_MODE[amm_id]
        if m == "ARAM":
            return "Quick Match"
        return m

    return "Quick Match"


# --- dataclasses --------------------------------------------------------------


@dataclass
class PlayerMatch:
    slot: int
    name: str
    toon_handle: str
    hero: str                # display name (localized)
    hero_id: str             # internal hero id (e.g. "Azmo")
    skin: str                # e.g. "Azm5"
    banner: str
    team: int
    result: int              # 1 win, 2 loss
    # KDA
    kills: int
    deaths: int
    assists: int
    takedowns: int
    solo_kills: int
    # progression
    level: int
    hero_mastery_tiers: dict[str, int] = field(default_factory=dict)
    # damage / healing
    hero_damage: int = 0
    siege_damage: int = 0
    structure_damage: int = 0
    creep_damage: int = 0
    minion_damage: int = 0
    summon_damage: int = 0
    physical_damage: int = 0
    spell_damage: int = 0
    healing: int = 0
    self_healing: int = 0
    damage_taken: int = 0
    damage_soaked: int = 0
    # teamfight
    teamfight_hero_damage: int = 0
    teamfight_healing: int = 0
    teamfight_damage_taken: int = 0
    teamfight_escapes: int = 0
    # map objectives / utility
    experience_contribution: int = 0
    time_spent_dead: int = 0
    time_on_point: int = 0
    merc_camp_captures: int = 0
    watch_tower_captures: int = 0
    minion_kills: int = 0
    regen_globes: int = 0
    town_kills: int = 0
    meta_experience: int = 0
    # cc
    time_cc_enemy_heroes: int = 0
    time_stunning_enemy_heroes: int = 0
    time_rooting_enemy_heroes: int = 0
    time_silencing_enemy_heroes: int = 0
    # combat highlights
    highest_kill_streak: int = 0
    multikill: int = 0
    escapes_performed: int = 0
    vengeances_performed: int = 0
    outnumbered_deaths: int = 0
    clutch_heals: int = 0
    protection_given_to_allies: int = 0
    on_fire_time: int = 0
    # talents (choice names like "AzmoDemonicInvasionMastery")
    talents: list[str] = field(default_factory=list)
    # awards (semicolon-joined list of award keys the player earned)
    awards: list[str] = field(default_factory=list)


@dataclass
class Replay:
    file_path: str
    file_hash: str
    # Stable identity of the *match itself* — same across every player's
    # recording of the same game. Derived from randomSeed + start timestamp.
    match_key: str
    random_seed: int
    map_name: str
    mode: str
    build: int
    protocol_build: int
    played_at: datetime
    duration_seconds: int
    winner_team: int
    bans: list[str] = field(default_factory=list)   # hero_ids banned (order = ban order if recoverable)
    bans_team0: list[str] = field(default_factory=list)
    bans_team1: list[str] = field(default_factory=list)
    players: list[PlayerMatch] = field(default_factory=list)

    def to_json(self) -> str:
        d = asdict(self)
        d["played_at"] = self.played_at.isoformat()
        return json.dumps(d, ensure_ascii=False, indent=2)


# --- stat map -----------------------------------------------------------------

# Pure-int stats pulled directly from SScoreResultEvent. Mapping: stat -> attr.
_INT_STATS = {
    "Takedowns": "takedowns",
    "SoloKill": "solo_kills",
    "Deaths": "deaths",
    "Assists": "assists",
    "Level": "level",
    "HeroDamage": "hero_damage",
    "SiegeDamage": "siege_damage",
    "StructureDamage": "structure_damage",
    "CreepDamage": "creep_damage",
    "MinionDamage": "minion_damage",
    "MinionKills": "minion_kills",
    "SummonDamage": "summon_damage",
    "PhysicalDamage": "physical_damage",
    "SpellDamage": "spell_damage",
    "Healing": "healing",
    "SelfHealing": "self_healing",
    "DamageTaken": "damage_taken",
    "DamageSoaked": "damage_soaked",
    "TeamfightHeroDamage": "teamfight_hero_damage",
    "TeamfightHealingDone": "teamfight_healing",
    "TeamfightDamageTaken": "teamfight_damage_taken",
    "TeamfightEscapesPerformed": "teamfight_escapes",
    "ExperienceContribution": "experience_contribution",
    "TimeSpentDead": "time_spent_dead",
    "TimeOnPoint": "time_on_point",
    "MercCampCaptures": "merc_camp_captures",
    "WatchTowerCaptures": "watch_tower_captures",
    "RegenGlobes": "regen_globes",
    "TownKills": "town_kills",
    "MetaExperience": "meta_experience",
    "TimeCCdEnemyHeroes": "time_cc_enemy_heroes",
    "TimeStunningEnemyHeroes": "time_stunning_enemy_heroes",
    "TimeRootingEnemyHeroes": "time_rooting_enemy_heroes",
    "TimeSilencingEnemyHeroes": "time_silencing_enemy_heroes",
    "HighestKillStreak": "highest_kill_streak",
    "Multikill": "multikill",
    "EscapesPerformed": "escapes_performed",
    "VengeancesPerformed": "vengeances_performed",
    "OutnumberedDeaths": "outnumbered_deaths",
    "ClutchHealsPerformed": "clutch_heals",
    "ProtectionGivenToAllies": "protection_given_to_allies",
    "OnFireTimeOnFire": "on_fire_time",
}

_TALENT_TIERS = [f"Tier{i}Talent" for i in range(1, 8)]

_AWARD_STATS = {
    "EndOfMatchAwardMVPBoolean": "MVP",
    "EndOfMatchAwardHatTrickBoolean": "HatTrick",
    "EndOfMatchAwardHighestKillStreakBoolean": "HighestKillStreak",
    "EndOfMatchAwardClutchHealerBoolean": "ClutchHealer",
    "EndOfMatchAward0DeathsBoolean": "Deathless",
    "EndOfMatchAward0OutnumberedDeathsBoolean": "0OutnumberedDeaths",
    "EndOfMatchAwardMostKillsBoolean": "MostKills",
    "EndOfMatchAwardMostHeroDamageDoneBoolean": "MostHeroDamage",
    "EndOfMatchAwardMostSiegeDamageDoneBoolean": "MostSiegeDamage",
    "EndOfMatchAwardMostHealingBoolean": "MostHealing",
    "EndOfMatchAwardMostDamageTakenBoolean": "MostDamageTaken",
    "EndOfMatchAwardMostXPContributionBoolean": "MostXP",
    "EndOfMatchAwardMostMercCampsCapturedBoolean": "MostMercCamps",
    "EndOfMatchAwardMostStunsBoolean": "MostStuns",
    "EndOfMatchAwardMostRootsBoolean": "MostRoots",
    "EndOfMatchAwardMostSilencesBoolean": "MostSilences",
    "EndOfMatchAwardMostTeamfightDamageTakenBoolean": "MostTeamfightDmgTaken",
    "EndOfMatchAwardMostTeamfightHealingDoneBoolean": "MostTeamfightHealing",
    "EndOfMatchAwardMostTeamfightHeroDamageDoneBoolean": "MostTeamfightHeroDmg",
    "EndOfMatchAwardMostProtectionBoolean": "MostProtection",
    "EndOfMatchAwardMostDaredevilEscapesBoolean": "MostEscapes",
    "EndOfMatchAwardMostVengeancesPerformedBoolean": "MostVengeances",
    "EndOfMatchAwardMapSpecificBoolean": "MapSpecific",
}

# --- helpers ------------------------------------------------------------------


def _extract_attr_map(attr_events: Any) -> dict[int, Any]:
    result: dict[int, Any] = {}
    scopes = attr_events.get("scopes", {}) if isinstance(attr_events, dict) else {}
    for scope_id, scope in scopes.items():
        if scope_id != 16:
            continue
        for attr_id, attr_list in scope.items():
            for attr in attr_list:
                val = _decode(attr.get("value"))
                if isinstance(val, str):
                    val = val.strip("\x00 ")
                result[attr_id] = val
    return result


# In Storm League (3ban): 4023, 4043 were team 0 / team 1 first bans we saw;
# full span based on community research: 4023-4030 and 4043-4046 are ban slots.
_BAN_ATTR_IDS_TEAM0 = (4023, 4028, 4030)
_BAN_ATTR_IDS_TEAM1 = (4043, 4027, 4045)
_ALL_BAN_ATTR_IDS = _BAN_ATTR_IDS_TEAM0 + _BAN_ATTR_IDS_TEAM1 + (4029, 4022)


def _extract_bans(attr_events: Any) -> tuple[list[str], list[str]]:
    """Return ``(team0_bans, team1_bans)`` as hero internal ids."""
    scopes = attr_events.get("scopes", {}) if isinstance(attr_events, dict) else {}
    global_scope = scopes.get(16, {})
    placeholder = {"", "Hmmr", "22", "no", "yes", "none"}  # observed defaults / non-hero tokens

    def _collect(attr_ids):
        out = []
        for aid in attr_ids:
            lst = global_scope.get(aid, [])
            for attr in lst:
                v = _decode(attr.get("value"))
                if isinstance(v, str):
                    v = v.strip("\x00 ")
                if v and v not in placeholder:
                    out.append(v)
                    break  # the last written value is usually what we want
        return out

    return _collect(_BAN_ATTR_IDS_TEAM0), _collect(_BAN_ATTR_IDS_TEAM1)


def _collect_stats(tracker_events: Iterable[dict[str, Any]]) -> dict[str, list[Any]]:
    last_score = None
    for ev in tracker_events:
        if ev.get("_event") == "NNet.Replay.Tracker.SScoreResultEvent":
            last_score = ev
    if last_score is None:
        return {}

    out: dict[str, list[Any]] = {}
    for inst in last_score.get("m_instanceList", []):
        name = _decode(inst["m_name"])
        values: list[Any] = []
        for v in inst["m_values"]:
            if not v:
                values.append(None)
            else:
                item = v[0]
                # ScoreResult values can be m_value (int) or m_fixed / m_string
                if "m_value" in item:
                    values.append(item["m_value"])
                else:
                    values.append(_decode(item.get("m_fixed") or item.get("m_string")))
        out[name] = values
    return out


def _mastery_tiers(slot: dict[str, Any]) -> dict[str, int]:
    tiers = slot.get("m_heroMasteryTiers") or []
    return {_decode(t["m_hero"]): int(t["m_tier"]) for t in tiers}


def _infer_winner(players: list[PlayerMatch]) -> int:
    for p in players:
        if p.result == 1:
            return p.team
    return -1


# --- core parser --------------------------------------------------------------


def _extract_talents_from_tracker(tracker: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Return ``{playerId: {"talents": [...], "hero": str, "win": bool|None}}``.

    Uses the definitive ``EndOfGameTalentChoices`` stat event. Each player in a
    match emits exactly one such event at game end. Tier order is preserved.
    """
    result: dict[int, dict[str, Any]] = {}
    for ev in tracker:
        if ev.get("_event") != "NNet.Replay.Tracker.SStatGameEvent":
            continue
        name = ev.get("m_eventName")
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        if name != "EndOfGameTalentChoices":
            continue
        string_data = {
            _decode(x["m_key"]): _decode(x["m_value"])
            for x in (ev.get("m_stringData") or [])
        }
        int_data = {
            _decode(x["m_key"]): int(x["m_value"])
            for x in (ev.get("m_intData") or [])
        }
        player_id = int_data.get("PlayerID")
        if player_id is None:
            continue
        # Pull Tier 1..7 Choice in order; Blizzard only emits filled tiers.
        talents: list[str] = []
        for i in range(1, 8):
            v = string_data.get(f"Tier {i} Choice", "")
            if v:
                talents.append(v)
        result[player_id] = {
            "talents": talents,
            "hero": string_data.get("Hero", ""),
            "win": string_data.get("Win/Loss") == "Win",
            "level_at_end": int_data.get("Level"),
        }
    return result


def parse_replay(path: str | Path) -> Replay:
    p = Path(path)
    if not p.exists():
        raise ReplayParseError(f"file not found: {p}")

    try:
        archive = MPQArchive(str(p))
    except Exception as e:
        raise ReplayParseError(f"not a valid MPQ archive: {p} ({e})") from e

    try:
        bootstrap = load_latest_protocol()
        header_contents = archive.header["user_data_header"]["content"]
        header = bootstrap.decode_replay_header(header_contents)
        build = int(header["m_version"]["m_baseBuild"])
    except Exception as e:
        raise ReplayParseError(f"failed to read replay header: {e}") from e

    protocol, protocol_build = load_protocol_for_build(build)

    try:
        details = protocol.decode_replay_details(archive.read_file("replay.details"))
    except Exception as e:
        raise ReplayParseError(f"failed to decode replay.details: {e}") from e

    map_name = _decode(details.get("m_title", "")) or "Unknown"
    played_at = _filetime_to_dt(int(details["m_timeUTC"]))

    # initData: slot-level data (hero_id, skin, banner, mastery tiers, randomSeed).
    slot_meta: list[dict[str, Any]] = []
    game_opts: dict[str, Any] = {}
    random_seed = 0
    try:
        init = protocol.decode_replay_initdata(archive.read_file("replay.initData"))
        game_opts = dict(init["m_syncLobbyState"]["m_gameDescription"].get("m_gameOptions") or {})
        lobby = init["m_syncLobbyState"]["m_lobbyState"]
        random_seed = int(lobby.get("m_randomSeed") or 0)
        raw_slots = lobby["m_slots"]
        for s in raw_slots:
            if s.get("m_control") != 2:  # only human-occupied slots
                continue
            slot_meta.append(
                {
                    "hero_id": _decode(s.get("m_hero", b"")) or "",
                    "banner": _decode(s.get("m_banner", b"")) or "",
                    "mastery": _mastery_tiers(s),
                }
            )
    except Exception:
        slot_meta = [{} for _ in range(len(details["m_playerList"]))]

    # Attributes: mode + bans + per-slot skin/hero attrs.
    try:
        attr_events = protocol.decode_replay_attributes_events(
            archive.read_file("replay.attributes.events")
        )
    except Exception:
        attr_events = {"scopes": {}}
    attr_map = _extract_attr_map(attr_events)
    bans_t0, bans_t1 = _extract_bans(attr_events)

    mode = _infer_mode(game_opts, attr_map, map_name)

    # Per-slot skin attr (4003) and internal hero id (4002).
    skins_by_slot: dict[int, str] = {}
    hero_ids_by_slot: dict[int, str] = {}
    for scope_id, scope in attr_events.get("scopes", {}).items():
        if not isinstance(scope_id, int) or scope_id < 1 or scope_id > 15:
            continue
        slot_idx = scope_id - 1
        for attr_id, lst in scope.items():
            if attr_id not in (4002, 4003):
                continue
            for a in lst:
                v = _decode(a.get("value"))
                if isinstance(v, str):
                    v = v.strip("\x00 ")
                if not v:
                    continue
                if attr_id == 4003:
                    skins_by_slot[slot_idx] = v
                elif attr_id == 4002:
                    hero_ids_by_slot[slot_idx] = v

    # Tracker events: score stats + duration.
    try:
        tracker = list(
            protocol.decode_replay_tracker_events(archive.read_file("replay.tracker.events"))
        )
    except Exception as e:
        raise ReplayParseError(f"failed to decode tracker events: {e}") from e

    stat_columns = _collect_stats(tracker)
    # PlayerId in tracker events uses 1-based slot indexing that matches the
    # details playerList slot order + 1. Confirmed empirically.
    talents_by_player_id = _extract_talents_from_tracker(tracker)

    last_loop = 0
    for ev in tracker:
        gl = ev.get("_gameloop", 0)
        if gl > last_loop:
            last_loop = gl
    duration = last_loop // 16

    players: list[PlayerMatch] = []
    for slot, pl in enumerate(details["m_playerList"]):
        toon = pl["m_toon"]
        handle = (
            f"{toon['m_region']}-{_decode(toon['m_programId'])}-"
            f"{toon['m_realm']}-{toon['m_id']}"
        )

        def _stat_int(name: str) -> int:
            col = stat_columns.get(name)
            if not col or slot >= len(col):
                return 0
            v = col[slot]
            if v is None:
                return 0
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        takedowns = _stat_int("Takedowns")
        assists = _stat_int("Assists")
        solo_kills = _stat_int("SoloKill")
        kills = takedowns - assists

        # Prefer talent *names* from EndOfGameTalentChoices. Fallback to the
        # tier-index values in SScoreResultEvent when the stat game event
        # isn't present (very old replays).
        talent_entry = talents_by_player_id.get(slot + 1) or {}
        talents: list[str] = list(talent_entry.get("talents") or [])
        if not talents:
            for t in _TALENT_TIERS:
                col = stat_columns.get(t)
                if not col or slot >= len(col):
                    continue
                val = col[slot]
                if val is None:
                    continue
                if isinstance(val, (bytes, str)):
                    tv = _decode(val).strip("\x00 ")
                    if tv:
                        talents.append(tv)
                elif val:
                    talents.append(str(val))

        awards = [friendly for stat, friendly in _AWARD_STATS.items() if _stat_int(stat)]

        kwargs = {"slot": slot,
                  "name": _decode(pl["m_name"]),
                  "toon_handle": handle,
                  "hero": _decode(pl["m_hero"]),
                  "hero_id": hero_ids_by_slot.get(slot, "")
                             or (slot_meta[slot].get("hero_id") if slot < len(slot_meta) else ""),
                  "skin": skins_by_slot.get(slot, ""),
                  "banner": slot_meta[slot].get("banner", "") if slot < len(slot_meta) else "",
                  "team": int(pl["m_teamId"]),
                  "result": int(pl["m_result"]),
                  "kills": kills,
                  "deaths": _stat_int("Deaths"),
                  "assists": assists,
                  "takedowns": takedowns,
                  "solo_kills": solo_kills,
                  "level": _stat_int("Level"),
                  "hero_mastery_tiers": slot_meta[slot].get("mastery", {}) if slot < len(slot_meta) else {},
                  "talents": talents,
                  "awards": awards}
        for stat_name, attr in _INT_STATS.items():
            if attr in kwargs:
                continue  # already filled
            kwargs[attr] = _stat_int(stat_name)
        players.append(PlayerMatch(**kwargs))

    # match_key identifies the *match* (not the file). It must be stable
    # across every player's recording of the same game. randomSeed is the
    # strongest signal since the engine must agree on it. We fall back to
    # (timeUTC + duration + map) only when seed is missing/zero.
    if random_seed:
        match_key = f"seed:{random_seed}"
    else:
        # The filetime is the exact match-start UTC to 100ns precision —
        # effectively unique in practice.
        match_key = f"t:{int(details['m_timeUTC'])}:{duration}:{map_name}"

    return Replay(
        file_path=str(p.resolve()),
        file_hash=_sha256(p),
        match_key=match_key,
        random_seed=random_seed,
        map_name=map_name,
        mode=mode,
        build=build,
        protocol_build=protocol_build,
        played_at=played_at,
        duration_seconds=duration,
        winner_team=_infer_winner(players),
        bans_team0=bans_t0,
        bans_team1=bans_t1,
        bans=bans_t0 + bans_t1,
        players=players,
    )
