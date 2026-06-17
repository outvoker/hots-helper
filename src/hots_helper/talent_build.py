"""Winrate-based talent build recommendations per hero.

Given a hero, return the recommended talent at each tier — the choice
with the highest Wilson 95% lower-bound win-rate (so a 1-game 100%
fluke doesn't outrank a 40-game 58% staple), with the other choices
listed as alternatives.

Game modes are split into two buckets because their talent metas differ:

* ``aram``     — 天命乱斗 (ARAM): random heroes, single lane.
* ``standard`` — 风暴联赛 + 快速匹配 (Storm League + Quick Match):
  drafted/normal maps, which share a close-enough talent meta to pool.

Reuses :meth:`Store.hero_talents`, which already folds 繁體/alias hero
spellings and decodes the per-match talent JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import Store
from .stats import wilson_lower_bound

# Mode buckets. ``standard`` pools the drafted + normal queues; ARAM is
# kept separate. Tuples feed straight into ``hero_talents(mode_filter=)``.
MODE_GROUPS: dict[str, tuple[str, ...]] = {
    "standard": ("Storm League", "Quick Match"),
    "aram": ("ARAM",),
}
DEFAULT_MODE_GROUP = "standard"


@dataclass
class TalentChoice:
    talent: str
    games: int
    wins: int
    pick_rate: float   # within its tier
    win_rate: float    # raw wins / games
    wilson_lb: float   # confidence-adjusted, used for ranking

    @property
    def is_recommended(self) -> bool:
        return False  # set True on the chosen copy in TalentTier


@dataclass
class TalentTier:
    tier: int
    recommended: TalentChoice | None
    choices: list[TalentChoice] = field(default_factory=list)  # all, best-first


@dataclass
class TalentBuild:
    hero: str
    mode_group: str
    total_games: int
    total_wins: int
    tiers: list[TalentTier] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return (self.total_wins / self.total_games) if self.total_games else 0.0


def normalize_mode_group(value: str | None) -> str:
    """Map an arbitrary input to a known bucket, defaulting safely."""
    if value and value in MODE_GROUPS:
        return value
    return DEFAULT_MODE_GROUP


def build_talent_recommendation(
    store: Store,
    hero: str,
    *,
    mode_group: str = DEFAULT_MODE_GROUP,
    min_games_for_pick: int = 1,
) -> TalentBuild:
    """Recommended talent per tier for ``hero`` in the given mode bucket.

    The recommendation per tier is the choice with the highest Wilson
    lower bound (ties broken by sample size), among choices meeting
    ``min_games_for_pick``. All choices are returned best-first so the
    UI can show alternatives.
    """
    group = normalize_mode_group(mode_group)
    modes = MODE_GROUPS[group]

    rows = store.hero_talents(hero, mode_filter=modes)

    by_tier: dict[int, list[dict]] = {}
    for r in rows:
        by_tier.setdefault(int(r["tier"]), []).append(dict(r))

    total_games = 0
    total_wins = 0
    tiers: list[TalentTier] = []
    for tier in sorted(by_tier):
        raw = by_tier[tier]
        tier_total = sum(int(c["games"]) for c in raw) or 1
        # Tier 1 games ≈ the hero's total games (everyone takes a T1
        # talent), so use the largest tier to estimate hero totals.
        total_games = max(total_games, tier_total)

        choices = [
            TalentChoice(
                talent=str(c["talent"]),
                games=int(c["games"]),
                wins=int(c["wins"]),
                pick_rate=int(c["games"]) / tier_total,
                win_rate=(int(c["wins"]) / int(c["games"])) if int(c["games"]) else 0.0,
                wilson_lb=wilson_lower_bound(int(c["wins"]), int(c["games"])),
            )
            for c in raw
        ]
        choices.sort(key=lambda c: (c.wilson_lb, c.games), reverse=True)

        eligible = [c for c in choices if c.games >= min_games_for_pick]
        recommended = eligible[0] if eligible else (choices[0] if choices else None)
        tiers.append(TalentTier(tier=tier, recommended=recommended, choices=choices))

    # Estimate total wins from the largest tier's chosen distribution:
    # sum of wins across that tier == hero wins in the mode.
    if by_tier:
        biggest = max(by_tier.values(), key=lambda rs: sum(int(c["games"]) for c in rs))
        total_wins = sum(int(c["wins"]) for c in biggest)

    return TalentBuild(
        hero=hero,
        mode_group=group,
        total_games=total_games,
        total_wins=total_wins,
        tiers=tiers,
    )
