"""Convert analysis-layer objects (sqlite3.Row + dataclasses) to JSON-ready
dicts.

We deliberately build dicts by hand rather than ``dataclasses.asdict()``
because almost every dataclass in the analysis layer exposes computed
``@property`` fields (``winrate``, ``kda``, ``combined_wr``,
``is_empty`` …) that ``asdict`` silently drops. Each serializer here
includes those derived fields explicitly, and ``tests/web/test_serialize.py``
guards against regressions.
"""

from __future__ import annotations

from typing import Any

from ..stats import wilson_lower_bound


def _wr(wins: int, games: int) -> float:
    return (wins / games) if games else 0.0


# --- heroes ------------------------------------------------------------------


def hero_aggregate_row(row: Any) -> dict:
    """A row from ``Store.hero_aggregate_stats`` / ``all_heroes``."""
    games = int(row["games"] or 0)
    wins = int(row["wins"] or 0)
    return {
        "hero": row["hero"],
        "games": games,
        "wins": wins,
        "winrate": _wr(wins, games),
        "wilson_lb": wilson_lower_bound(wins, games),
        "avg_k": float(row["avg_k"] or 0.0),
        "avg_d": float(row["avg_d"] or 0.0),
        "avg_a": float(row["avg_a"] or 0.0),
        "avg_hero_dmg": float(row["avg_hero_dmg"] or 0.0),
        "avg_siege_dmg": float(row["avg_siege_dmg"] or 0.0),
        "avg_healing": float(row["avg_healing"] or 0.0),
    }


def hero_report(report: Any) -> dict:
    """A :class:`hots_helper.lookup.HeroReport`."""
    return {
        "hero": report.hero,
        "total_games": report.total_games,
        "total_wins": report.total_wins,
        "winrate": report.winrate,
        "map_games": report.map_games,
        "map_wins": report.map_wins,
        "map_winrate": report.map_winrate,
        "map_records": [map_record(m) for m in report.map_records],
        "talents_by_tier": {
            str(tier): [
                {
                    "talent": talent,
                    "games": games,
                    "wins": wins,
                    "pick_rate": pick_rate,
                }
                for (talent, games, wins, pick_rate) in choices
            ]
            for tier, choices in report.talents_by_tier.items()
        },
    }


def _talent_choice(c: Any) -> dict:
    """A :class:`hots_helper.talent_build.TalentChoice`."""
    from ..talent_names import talent_label

    return {
        "talent": c.talent,
        "talent_label": talent_label(c.talent),
        "games": c.games,
        "wins": c.wins,
        "pick_rate": c.pick_rate,
        "win_rate": c.win_rate,
        "wilson_lb": c.wilson_lb,
    }


def talent_build(build: Any) -> dict:
    """A :class:`hots_helper.talent_build.TalentBuild`."""
    return {
        "hero": build.hero,
        "mode_group": build.mode_group,
        "total_games": build.total_games,
        "total_wins": build.total_wins,
        "win_rate": build.win_rate,
        "tiers": [
            {
                "tier": ti.tier,
                "recommended": _talent_choice(ti.recommended) if ti.recommended else None,
                "choices": [_talent_choice(c) for c in ti.choices],
            }
            for ti in build.tiers
        ],
    }


# --- players / lookup --------------------------------------------------------


def hero_usage(h: Any) -> dict:
    """A :class:`hots_helper.lookup.HeroUsage`."""
    return {
        "hero": h.hero,
        "hero_id": h.hero_id,
        "games": h.games,
        "wins": h.wins,
        "winrate": h.winrate,
        "avg_k": h.avg_k,
        "avg_d": h.avg_d,
        "avg_a": h.avg_a,
        "avg_hero_dmg": h.avg_hero_dmg,
        "avg_siege_dmg": h.avg_siege_dmg,
        "avg_healing": h.avg_healing,
        "avg_dmg_taken": h.avg_dmg_taken,
        "avg_xp": h.avg_xp,
        "avg_cc": h.avg_cc,
        "last_played": h.last_played,
    }


def map_record(m: Any) -> dict:
    return {
        "map_name": m.map_name,
        "games": m.games,
        "wins": m.wins,
        "winrate": m.winrate,
    }


def teammate(tm: Any) -> dict:
    return {
        "display_name": tm.display_name,
        "toon_handle": tm.toon_handle,
        "games": tm.games,
        "shared_wins": tm.shared_wins,
        "shared_winrate": tm.shared_winrate,
    }


def recent_match(rm: Any) -> dict:
    return {
        "played_at": rm.played_at,
        "map_name": rm.map_name,
        "mode": rm.mode,
        "hero": rm.hero,
        "hero_id": rm.hero_id,
        "result": rm.result,
        "kills": rm.kills,
        "deaths": rm.deaths,
        "assists": rm.assists,
        "hero_damage": rm.hero_damage,
        "siege_damage": rm.siege_damage,
        "healing": rm.healing,
    }


def player_summary(s: Any) -> dict:
    """A :class:`hots_helper.lookup.PlayerSummary`."""
    k, d, a = s.overall_kda
    return {
        "name_searched": s.name_searched,
        "toon_handle": s.toon_handle,
        "display_name": s.display_name,
        "total_games": s.total_games,
        "total_wins": s.total_wins,
        "winrate": s.winrate,
        "overall_kda": {"k": k, "d": d, "a": a},
        "recent_games": s.recent_games,
        "recent_wins": s.recent_wins,
        "recent_winrate": s.recent_winrate,
        "map_games": s.map_games,
        "map_wins": s.map_wins,
        "map_winrate": s.map_winrate,
        "avg_hero_dmg": s.avg_hero_dmg,
        "avg_dmg_taken": s.avg_dmg_taken,
        "avg_healing": s.avg_healing,
        "avg_xp": s.avg_xp,
        "avg_cc": s.avg_cc,
        "signature_heroes": [hero_usage(h) for h in s.signature_heroes],
        "map_heroes": [hero_usage(h) for h in s.map_heroes],
        "frequent_teammates": [teammate(t) for t in s.frequent_teammates],
        "frequent_opponents": [teammate(t) for t in s.frequent_opponents],
        "recent_matches": [recent_match(m) for m in s.recent_matches],
        "map_records": [map_record(m) for m in s.map_records],
        "ban_recommendations": [hero_usage(h) for h in s.ban_recommendations],
        "note": s.note,
    }


# --- player rankings ---------------------------------------------------------


def player_rank_row(r: Any) -> dict:
    """A :class:`hots_helper.player_rank.PlayerRankRow`."""
    return {
        "rank": r.rank,
        "toon_handle": r.toon_handle,
        "display_name": r.display_name,
        "games": r.games,
        "wins": r.wins,
        "win_rate": r.win_rate,
        "wilson_lb": r.wilson_lb,
        "kda": r.kda,
        "avg_k": r.avg_k,
        "avg_d": r.avg_d,
        "avg_a": r.avg_a,
        "avg_hero_dmg": r.avg_hero_dmg,
        "avg_siege_dmg": r.avg_siege_dmg,
        "avg_structure_dmg": r.avg_structure_dmg,
        "avg_healing": r.avg_healing,
        "avg_dmg_taken": r.avg_dmg_taken,
        "avg_dmg_soaked": r.avg_dmg_soaked,
        "avg_xp": r.avg_xp,
        "avg_cc": r.avg_cc,
        "power": r.power,
        "last_seen_at": r.last_seen_at,
        "is_squad": r.is_squad,
    }


# --- BP advisor --------------------------------------------------------------


def threat_hero(t: Any) -> dict:
    return {
        "hero": t.hero,
        "hero_id": t.hero_id,
        "games": t.games,
        "wins": t.wins,
        "raw_winrate": t.raw_winrate,
        "wilson_lb": t.wilson_lb,
        "avg_k": t.avg_k,
        "avg_d": t.avg_d,
        "avg_a": t.avg_a,
        "last_played": t.last_played,
        "lift_pp": t.lift_pp,
        "p_value": t.p_value,
    }


def opponent_profile(p: Any) -> dict:
    return {
        "name_searched": p.name_searched,
        "toon_handle": p.toon_handle,
        "display_name": p.display_name,
        "total_games": p.total_games,
        "threats": [threat_hero(t) for t in p.threats],
        "note": p.note,
        "power": p.power,
        "power_rank": p.power_rank,
        "power_total": p.power_total,
        "ally_games": p.ally_games,
        "ally_wins": p.ally_wins,
        "enemy_games": p.enemy_games,
        "enemy_wins": p.enemy_wins,
    }


def ban_candidate(c: Any) -> dict:
    return {
        "hero": c.hero,
        "hero_id": c.hero_id,
        "score": c.score,
        "total_games": c.total_games,
        "total_wins": c.total_wins,
        "combined_wr": c.combined_wr,
        "contributors": [
            {"name": name, "games": games, "wins": wins, "wilson_lb": wlb}
            for (name, games, wins, wlb) in c.contributors
        ],
    }


def talent_pick(tp: Any) -> dict:
    return {
        "tier": tp.tier,
        "talent": tp.talent,
        "games": tp.games,
        "wins": tp.wins,
        "pick_rate": tp.pick_rate,
        "wilson_lb": tp.wilson_lb,
    }


def pick_candidate(c: Any) -> dict:
    return {
        "hero": c.hero,
        "map_games": c.map_games,
        "map_wins": c.map_wins,
        "map_winrate": c.map_winrate,
        "map_wilson_lb": c.map_wilson_lb,
        "global_games": c.global_games,
        "global_wins": c.global_wins,
        "global_winrate": c.global_winrate,
        "lift_pp": c.lift_pp,
        "p_value": c.p_value,
        "significant": c.significant,
        "recommended_build": [talent_pick(tp) for tp in c.recommended_build],
    }


def map_tier_ban(b: Any) -> dict:
    return {
        "hero": b.hero,
        "map_games": b.map_games,
        "map_wins": b.map_wins,
        "map_winrate": b.map_winrate,
        "map_wilson_lb": b.map_wilson_lb,
        "global_winrate": b.global_winrate,
        "lift_pp": b.lift_pp,
        "p_value": b.p_value,
        "squad_games_on_hero": b.squad_games_on_hero,
    }


# --- match records -----------------------------------------------------------


def _ban_list(raw: str) -> list[str]:
    return [b for b in (raw or "").split(",") if b]


def match_list_row(row: Any, roster: list[Any]) -> dict:
    """One row of the match list, with its ten heroes split by team.

    ``roster`` is the subset of ``Store.match_roster_brief`` rows for
    this replay.
    """
    team0 = [
        {"hero": r["hero"], "display_name": r["display_name"]}
        for r in roster
        if int(r["team"]) == 0
    ]
    team1 = [
        {"hero": r["hero"], "display_name": r["display_name"]}
        for r in roster
        if int(r["team"]) == 1
    ]
    return {
        "replay_id": int(row["id"]),
        "match_key": row["match_key"],
        "map_name": row["map_name"],
        "mode": row["mode"],
        "played_at": row["played_at"],
        "duration_s": int(row["duration_s"] or 0),
        "winner_team": int(row["winner_team"]),
        "bans_team0": _ban_list(row["bans_team0"]),
        "bans_team1": _ban_list(row["bans_team1"]),
        "team0": team0,
        "team1": team1,
    }


def match_player(row: Any) -> dict:
    """One player_match row inside a match detail."""
    return {
        "slot": int(row["slot"]),
        "team": int(row["team"]),
        "toon_handle": row["toon_handle"],
        "display_name": row["display_name"],
        "hero": row["hero"],
        "hero_id": row["hero_id"],
        "result": int(row["result"]),
        "kills": int(row["kills"]),
        "deaths": int(row["deaths"]),
        "assists": int(row["assists"]),
        "hero_damage": int(row["hero_damage"]),
        "siege_damage": int(row["siege_damage"]),
        "structure_damage": int(row["structure_damage"]),
        "healing": int(row["healing"]),
        "damage_taken": int(row["damage_taken"]),
        "experience_contribution": int(row["experience_contribution"]),
        "level": int(row["level"]),
    }


def match_detail(replay: Any, players: list[Any]) -> dict:
    return {
        "replay_id": int(replay["id"]),
        "match_key": replay["match_key"],
        "map_name": replay["map_name"],
        "mode": replay["mode"],
        "played_at": replay["played_at"],
        "duration_s": int(replay["duration_s"] or 0),
        "winner_team": int(replay["winner_team"]),
        "bans_team0": _ban_list(replay["bans_team0"]),
        "bans_team1": _ban_list(replay["bans_team1"]),
        "players": [match_player(p) for p in players],
    }


# --- weekly report -----------------------------------------------------------


def _window(w: Any) -> dict:
    return {
        "days": w.days,
        "start_iso": w.start_iso,
        "end_iso": w.end_iso,
        "games": w.games,
        "wins": w.wins,
        "winrate": w.winrate,
    }


def weekly_report(report: Any, *, brief: str = "") -> dict:
    """A :class:`hots_helper.weekly_report.WeeklyReport`.

    Award ``label_key`` is resolved to a display string server-side via
    :func:`hots_helper.i18n.t` so the frontend doesn't carry the label
    table.
    """
    from ..i18n import t

    ov = report.overview
    return {
        "overview": {
            "current": _window(ov.current),
            "previous": _window(ov.previous),
            "games_delta": ov.games_delta,
            "winrate_delta_pp": ov.winrate_delta_pp,
        },
        "players": [
            {
                "toon_handle": p.toon_handle,
                "display_name": p.display_name,
                "games": p.games,
                "wins": p.wins,
                "winrate": p.winrate,
                "avg_k": p.avg_k,
                "avg_d": p.avg_d,
                "avg_a": p.avg_a,
                "most_played_hero": p.most_played_hero,
                "most_played_hero_games": p.most_played_hero_games,
                "most_played_hero_wins": p.most_played_hero_wins,
            }
            for p in report.players
        ],
        "awards": [
            {
                "label_key": a.label_key,
                "label": t(a.label_key),
                "display_name": a.display_name,
                "hero": a.hero,
                "value": a.value,
                "games": a.games,
            }
            for a in report.awards
        ],
        "highlights": [
            {
                "played_at": h.played_at,
                "display_name": h.display_name,
                "hero": h.hero,
                "map_name": h.map_name,
                "result": h.result,
                "kills": h.kills,
                "deaths": h.deaths,
                "assists": h.assists,
                "hero_damage": h.hero_damage,
            }
            for h in report.highlights
        ],
        "hero_top_picked": [_hero_pick(h) for h in report.hero_top_picked],
        "hero_top_winrate": [_hero_pick(h) for h in report.hero_top_winrate],
        "hero_combos": [
            {
                "hero_a": c.hero_a,
                "hero_b": c.hero_b,
                "games": c.games,
                "wins": c.wins,
                "winrate": c.winrate,
            }
            for c in report.hero_combos
        ],
        "maps": [
            {
                "map_name": m.map_name,
                "games": m.games,
                "wins": m.wins,
                "winrate": m.winrate,
            }
            for m in report.maps
        ],
        "longest_win_streak": _streak(report.longest_win_streak),
        "longest_loss_streak": _streak(report.longest_loss_streak),
        "brief": brief,
    }


def _hero_pick(h: Any) -> dict:
    return {"hero": h.hero, "games": h.games, "wins": h.wins, "winrate": h.winrate}


def _streak(s: Any) -> dict:
    return {
        "length": s.length,
        "started_at": s.started_at,
        "ended_at": s.ended_at,
        "is_empty": s.is_empty,
    }
