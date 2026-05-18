"""Cross-game player rankings — one board over every player who has
shared a match with the squad, scored on a composite combat-power
metric. The dialog (and the BP popup) does its own sort; this
module only computes the rows.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .db import Store
from .stats import wilson_lower_bound


@dataclass(frozen=True)
class PlayerRankRow:
    """One row of any of the four boards."""
    rank: int                # 1-indexed within the board
    toon_handle: str
    display_name: str
    games: int
    wins: int
    win_rate: float          # 0..1
    wilson_lb: float         # 0..1, lower bound at 95%
    avg_k: float
    avg_d: float
    avg_a: float
    avg_hero_dmg: float
    avg_siege_dmg: float
    avg_structure_dmg: float
    avg_healing: float
    avg_dmg_taken: float
    avg_dmg_soaked: float
    avg_xp: float
    avg_cc: float
    last_seen_at: str
    # Composite "power" score in 0..100 (per-board population rescaling
    # is applied in :func:`_score_population`).
    power: float = 0.0

    @property
    def kda(self) -> float:
        return (self.avg_k + self.avg_a) / max(self.avg_d, 1.0)


def _row_to_rank(row, *, rank: int) -> PlayerRankRow:
    games = int(row["games"] or 0)
    wins = int(row["wins"] or 0)
    return PlayerRankRow(
        rank=rank,
        toon_handle=row["toon_handle"],
        display_name=row["display_name"] or "",
        games=games,
        wins=wins,
        win_rate=(wins / games) if games else 0.0,
        wilson_lb=wilson_lower_bound(wins, games),
        avg_k=float(row["avg_k"] or 0.0),
        avg_d=float(row["avg_d"] or 0.0),
        avg_a=float(row["avg_a"] or 0.0),
        avg_hero_dmg=float(row["avg_hero_dmg"] or 0.0),
        avg_siege_dmg=float(row["avg_siege_dmg"] or 0.0),
        avg_structure_dmg=float(row["avg_structure_dmg"] or 0.0),
        avg_healing=float(row["avg_healing"] or 0.0),
        avg_dmg_taken=float(row["avg_dmg_taken"] or 0.0),
        avg_dmg_soaked=float(row["avg_dmg_soaked"] or 0.0),
        avg_xp=float(row["avg_xp"] or 0.0),
        avg_cc=float(row["avg_cc"] or 0.0),
        last_seen_at=row["last_seen_at"] or "",
    )


def _percentile_in(sorted_pop: list[float], v: float) -> float:
    """Percentile rank of ``v`` in a pre-sorted population, in 0..1.

    Uses ``bisect_right`` so identical values count as "at or below",
    matching the intuition "how many of the population did I beat".
    Empty / degenerate populations fall back to 0.5 (neutral).
    """
    n = len(sorted_pop)
    if n == 0:
        return 0.5
    return bisect_right(sorted_pop, v) / n


# Role-contribution thresholds. Mirrors db.store._METRIC_CONTRIB_THRESHOLDS
# — kept duplicated here so the percentile pipeline doesn't have to
# import the SQL helper. Update them in tandem if you tune one.
_SIEGE_THRESHOLD  = 5_000
_STRUCT_THRESHOLD = 1_000
_HEAL_THRESHOLD   = 1_000
_SOAK_THRESHOLD   = 5_000
_TAKEN_THRESHOLD  = 30_000
_CC_THRESHOLD     = 1.0
# Mirrors db.store._METRIC_SANITY_MAX. Some replays record uint32
# overflow sentinels (~4.29G) for cumulative-damage fields; we reject
# anything above 10M from the baseline so it can't poison percentile
# scoring. Real per-match values top out around 250k.
_SANITY_MAX = 10_000_000.0


@dataclass(frozen=True)
class PowerBaseline:
    """Pre-sorted samples we use to compute percentile ranks.

    Built once per dialog refresh / BP analysis pass and shared across
    every player + every hero we score. The population is the *whole*
    DB (filtered by Storm League by default), so a sparse leaderboard
    can't artificially inflate scores — laolang's 22k hero damage
    sits at percentile ~0.15 globally instead of 1.0 because they're
    the only person on their board.

    ``raw_power_pool`` holds the pre-rank weighted scores for every
    per-match sample in the baseline — :func:`power_score` takes its
    own raw output, looks it up in this pool, and returns the
    percentile rank. The two-step pipeline means the final number is
    always "you beat X% of all observed performances" rather than a
    weighted-average that could be skewed by a single extreme metric.
    """
    hero_damage: list[float]
    siege_damage: list[float]
    structure_damage: list[float]
    healing: list[float]
    damage_soaked: list[float]
    damage_taken: list[float]
    xp: list[float]
    cc: list[float]
    kda: list[float]                  # per-match KDA = (K+A)/max(D,1)
    deaths: list[float]               # raw deaths (smaller = better)
    win_rates_per_match: list[float]  # 0 or 1 per match — for WR percentile
    raw_power_pool: list[float]       # sorted; for the final percentile pass


def _build_baseline_from_rows(rows) -> PowerBaseline:
    hero, siege, struct, heal, soak, taken, xp, cc, kda, deaths, wr = (
        [], [], [], [], [], [], [], [], [], [], [],
    )
    for r in rows:
        d = max(float(r["deaths"] or 0.0), 1.0)
        kda.append((float(r["kills"] or 0) + float(r["assists"] or 0)) / d)
        hero.append(float(r["hero_damage"] or 0))
        siege.append(float(r["siege_damage"] or 0))
        struct.append(float(r["structure_damage"] or 0))
        heal.append(float(r["healing"] or 0))
        soak.append(float(r["damage_soaked"] or 0))
        taken.append(float(r["damage_taken"] or 0))
        xp.append(float(r["xp"] or 0))
        cc.append(float(r["cc"] or 0))
        deaths.append(float(r["deaths"] or 0))
        wr.append(1.0 if int(r["result"] or 0) == 1 else 0.0)

    # Specialist baselines: drop samples that fall below the role
    # contribution threshold (mirrors store._METRIC_CONTRIB_THRESHOLDS).
    # A 500-healing self-leech assassin shouldn't be treated as a
    # healer, and "everyone takes some damage" shouldn't make every
    # hero's damage_taken percentile useful — only real frontliners.
    # Upper bound _SANITY_MAX rejects uint32-overflow sentinels
    # (~4.29G) that show up in rare replays — they'd otherwise
    # dominate every percentile baseline.
    siege  = [v for v in siege  if _SIEGE_THRESHOLD  < v < _SANITY_MAX]
    struct = [v for v in struct if _STRUCT_THRESHOLD < v < _SANITY_MAX]
    heal   = [v for v in heal   if _HEAL_THRESHOLD   < v < _SANITY_MAX]
    soak   = [v for v in soak   if _SOAK_THRESHOLD   < v < _SANITY_MAX]
    taken  = [v for v in taken  if _TAKEN_THRESHOLD  < v < _SANITY_MAX]
    cc     = [v for v in cc     if _CC_THRESHOLD     < v < _SANITY_MAX]
    # Hero damage and XP get only the upper sanity bound; lower bound
    # is meaningless (every hero deals some damage / earns some XP).
    hero = [v for v in hero if v < _SANITY_MAX]
    xp   = [v for v in xp   if v < _SANITY_MAX]

    for lst in (hero, siege, struct, heal, soak, taken, xp, cc, kda, deaths, wr):
        lst.sort()
    base = PowerBaseline(
        hero_damage=hero,
        siege_damage=siege,
        structure_damage=struct,
        healing=heal,
        damage_soaked=soak,
        damage_taken=taken,
        xp=xp,
        cc=cc,
        kda=kda,
        deaths=deaths,
        win_rates_per_match=wr,
        raw_power_pool=[],
    )

    # Second pass: compute the raw weighted score for every per-match
    # sample in the baseline, sorted, so the final ``power_score``
    # call can re-percentile-rank itself. Without this pool the score
    # is just a weighted average — fine, but it loses the "percentile
    # of percentiles" interpretation the user wants.
    raw_pool: list[float] = []
    for r in rows:
        raw_pool.append(_raw_power(
            baseline=base,
            win_rate=1.0 if int(r["result"] or 0) == 1 else 0.0,
            avg_k=float(r["kills"] or 0),
            avg_d=float(r["deaths"] or 0),
            avg_a=float(r["assists"] or 0),
            avg_hero_dmg=float(r["hero_damage"] or 0),
            avg_siege_dmg=float(r["siege_damage"] or 0),
            avg_structure_dmg=float(r["structure_damage"] or 0),
            avg_healing=float(r["healing"] or 0),
            avg_dmg_soaked=float(r["damage_soaked"] or 0),
            avg_dmg_taken=float(r["damage_taken"] or 0),
            avg_xp=float(r["xp"] or 0),
            avg_cc=float(r["cc"] or 0),
        ))
    raw_pool.sort()
    # Replace the placeholder. We rebuild the dataclass because it's
    # frozen — cheap, all the underlying lists are shared by reference.
    return PowerBaseline(
        hero_damage=hero,
        siege_damage=siege,
        structure_damage=struct,
        healing=heal,
        damage_soaked=soak,
        damage_taken=taken,
        xp=xp,
        cc=cc,
        kda=kda,
        deaths=deaths,
        win_rates_per_match=wr,
        raw_power_pool=raw_pool,
    )


def build_power_baseline(store: Store) -> PowerBaseline:
    """Pull the global per-match population once. ~1ms per 1k rows.

    The per-metric percentile lists are built from individual matches
    (so e.g. "this player averaged 50k hero damage" gets compared
    against the distribution of single-match hero-damage values).
    The ``raw_power_pool`` used for the final percentile is rebuilt
    from *aggregated* baselines — one entry per hero and one entry
    per player — so an English-textbook 1-game outlier in the per-
    match space doesn't keep every aggregated row out of the top
    quintile. See :func:`_attach_aggregate_pool`.
    """
    rows = store.per_match_metric_samples()
    base = _build_baseline_from_rows(rows)
    return _attach_aggregate_pool(base, store)


def _attach_aggregate_pool(
    base: PowerBaseline, store: Store
) -> PowerBaseline:
    """Replace ``raw_power_pool`` with a pool of aggregated raw scores.

    Per-match raw scores produce a heavy upper tail (a 0-death 30-kill
    Greymane game scores ~98 raw, no aggregated row can reach that),
    which compresses the rank space the dialogs actually display
    onto. Building the pool from real aggregates — every hero in
    ``hero_aggregate_stats`` plus every player in
    ``player_rankings_seen`` — keeps the second-stage percentile
    meaningful: top of the leaderboard reads as "≈ top 5 % of
    similar-shape rows you'll ever see", not "≈ top 60 % because
    every single-game outlier is somewhere ahead of you".
    """
    pool: list[float] = []

    # Hero aggregates — every hero with ≥1 game.
    for r in store.hero_aggregate_stats():
        pool.append(_raw_power(
            baseline=base,
            win_rate=(int(r["wins"] or 0) / int(r["games"] or 1)),
            avg_k=float(r["avg_k"] or 0),
            avg_d=float(r["avg_d"] or 0),
            avg_a=float(r["avg_a"] or 0),
            avg_hero_dmg=float(r["avg_hero_dmg"] or 0),
            avg_siege_dmg=float(r["avg_siege_dmg"] or 0),
            avg_structure_dmg=float(r["avg_structure_dmg"] or 0),
            avg_healing=float(r["avg_healing"] or 0),
            avg_dmg_soaked=float(r["avg_dmg_soaked"] or 0),
            avg_dmg_taken=float(r["avg_dmg_taken"] or 0),
            avg_xp=float(r["avg_xp"] or 0),
            avg_cc=float(r["avg_cc"] or 0),
        ))

    # Player aggregates — every handle that's ever queued with us.
    squad = tuple(store.squad_handles())
    if squad:
        for r in store.player_rankings_seen(squad, min_games=1, limit=2000):
            pool.append(_raw_power(
                baseline=base,
                win_rate=(int(r["wins"] or 0) / int(r["games"] or 1)),
                avg_k=float(r["avg_k"] or 0),
                avg_d=float(r["avg_d"] or 0),
                avg_a=float(r["avg_a"] or 0),
                avg_hero_dmg=float(r["avg_hero_dmg"] or 0),
                avg_siege_dmg=float(r["avg_siege_dmg"] or 0),
                avg_structure_dmg=float(r["avg_structure_dmg"] or 0),
                avg_healing=float(r["avg_healing"] or 0),
                avg_dmg_soaked=float(r["avg_dmg_soaked"] or 0),
                avg_dmg_taken=float(r["avg_dmg_taken"] or 0),
                avg_xp=float(r["avg_xp"] or 0),
                avg_cc=float(r["avg_cc"] or 0),
            ))

    pool.sort()
    return PowerBaseline(
        hero_damage=base.hero_damage,
        siege_damage=base.siege_damage,
        structure_damage=base.structure_damage,
        healing=base.healing,
        damage_soaked=base.damage_soaked,
        damage_taken=base.damage_taken,
        xp=base.xp,
        cc=base.cc,
        kda=base.kda,
        deaths=base.deaths,
        win_rates_per_match=base.win_rates_per_match,
        raw_power_pool=pool,
    )


# Weights for the "combat power" score. Tuned by feel: KDA + win rate
# carry the most weight (they're the cleanest signals of "this player
# does the right thing in fights"); damage / soak / xp / cc fill in
# the rest. Each component is a percentile rank against the global
# baseline so the weights are about *relative importance*, not units.
#
# Self-healing is excluded — it's mostly inflated by self-sustain
# heroes' baseline regen and doesn't track player skill well.
_POWER_WEIGHTS: dict[str, float] = {
    "win_rate":          0.28,
    "kda":               0.18,
    "hero_damage":       0.11,
    "siege_damage":      0.04,
    "structure_damage":  0.04,
    "healing":           0.08,
    "damage_soaked":     0.06,
    "damage_taken":      0.05,  # frontline pressure (gated by threshold)
    "experience":        0.06,
    "cc":                0.04,
    "deaths_inverse":    0.06,  # lower deaths = higher percentile
}


def _raw_power(
    *,
    baseline: PowerBaseline,
    win_rate: float,
    avg_k: float,
    avg_d: float,
    avg_a: float,
    avg_hero_dmg: float,
    avg_siege_dmg: float,
    avg_structure_dmg: float,
    avg_healing: float,
    avg_dmg_soaked: float,
    avg_dmg_taken: float,
    avg_xp: float,
    avg_cc: float,
) -> float:
    """Stage 1: weighted-average percentile across the metrics the
    player actually contributed to. 0..100.

    Specialist metrics (soak / heal / siege / struct / cc / damage_taken)
    only count when the player actually contributed on that axis at a
    role-meaningful level. If a metric is skipped, its weight is
    *redistributed* across the metrics the player did contribute to,
    so a pure assassin with high damage / KDA can still hit 100
    instead of being capped just for never healing or tanking.
    """
    kda = (avg_k + avg_a) / max(avg_d, 1.0)

    parts: list[tuple[float, float]] = [
        (_POWER_WEIGHTS["win_rate"],
         _percentile_in(baseline.win_rates_per_match, win_rate)),
        (_POWER_WEIGHTS["kda"], _percentile_in(baseline.kda, kda)),
        (_POWER_WEIGHTS["hero_damage"],
         _percentile_in(baseline.hero_damage, avg_hero_dmg)),
        (_POWER_WEIGHTS["experience"],
         _percentile_in(baseline.xp, avg_xp)),
        # Lower deaths = higher score.
        (_POWER_WEIGHTS["deaths_inverse"],
         1.0 - _percentile_in(baseline.deaths, avg_d)),
    ]

    # Specialist metrics: include only if the player contributed
    # meaningfully (above the role threshold). The thresholds match
    # the SQL side so a player whose SQL average is 0 (under-threshold
    # samples got dropped) also fails the per-call check here.
    if avg_siege_dmg > _SIEGE_THRESHOLD:
        parts.append((_POWER_WEIGHTS["siege_damage"],
                      _percentile_in(baseline.siege_damage, avg_siege_dmg)))
    if avg_structure_dmg > _STRUCT_THRESHOLD:
        parts.append((_POWER_WEIGHTS["structure_damage"],
                      _percentile_in(baseline.structure_damage, avg_structure_dmg)))
    if avg_healing > _HEAL_THRESHOLD:
        parts.append((_POWER_WEIGHTS["healing"],
                      _percentile_in(baseline.healing, avg_healing)))
    if avg_dmg_soaked > _SOAK_THRESHOLD:
        parts.append((_POWER_WEIGHTS["damage_soaked"],
                      _percentile_in(baseline.damage_soaked, avg_dmg_soaked)))
    if avg_dmg_taken > _TAKEN_THRESHOLD:
        parts.append((_POWER_WEIGHTS["damage_taken"],
                      _percentile_in(baseline.damage_taken, avg_dmg_taken)))
    if avg_cc > _CC_THRESHOLD:
        parts.append((_POWER_WEIGHTS["cc"],
                      _percentile_in(baseline.cc, avg_cc)))

    if not parts:
        return 0.0
    total_weight = sum(w for w, _ in parts)
    return sum(w * p for w, p in parts) / total_weight * 100.0


def power_score(
    *,
    baseline: PowerBaseline,
    win_rate: float,
    avg_k: float,
    avg_d: float,
    avg_a: float,
    avg_hero_dmg: float,
    avg_siege_dmg: float,
    avg_structure_dmg: float,
    avg_healing: float,
    avg_dmg_soaked: float,
    avg_dmg_taken: float,
    avg_xp: float,
    avg_cc: float,
) -> float:
    """Final 0..100 combat-power score.

    Two-stage:
    1. Compute a raw weighted-average percentile (:func:`_raw_power`).
    2. Look that raw value up in the baseline's pool of raw values
       (every per-match sample) and return its percentile rank * 100.

    The second stage means the displayed score is "you beat X% of all
    observed performances" — easy to read, robust to any single
    metric being extreme, and naturally squashes the long tail at
    both ends.
    """
    raw = _raw_power(
        baseline=baseline,
        win_rate=win_rate,
        avg_k=avg_k, avg_d=avg_d, avg_a=avg_a,
        avg_hero_dmg=avg_hero_dmg,
        avg_siege_dmg=avg_siege_dmg,
        avg_structure_dmg=avg_structure_dmg,
        avg_healing=avg_healing,
        avg_dmg_soaked=avg_dmg_soaked,
        avg_dmg_taken=avg_dmg_taken,
        avg_xp=avg_xp,
        avg_cc=avg_cc,
    )
    if not baseline.raw_power_pool:
        # No baseline yet (we're inside _build_baseline_from_rows
        # itself, computing the pool). Return raw so the second-stage
        # data set can be assembled.
        return raw
    return _percentile_in(baseline.raw_power_pool, raw) * 100.0


def _score_population(
    rows: list[PlayerRankRow], baseline: PowerBaseline
) -> list[PlayerRankRow]:
    """Attach a power score to each row using the shared global baseline."""
    out: list[PlayerRankRow] = []
    for r in rows:
        s = power_score(
            baseline=baseline,
            win_rate=r.win_rate,
            avg_k=r.avg_k, avg_d=r.avg_d, avg_a=r.avg_a,
            avg_hero_dmg=r.avg_hero_dmg,
            avg_siege_dmg=r.avg_siege_dmg,
            avg_structure_dmg=r.avg_structure_dmg,
            avg_healing=r.avg_healing,
            avg_dmg_soaked=r.avg_dmg_soaked,
            avg_dmg_taken=r.avg_dmg_taken,
            avg_xp=r.avg_xp,
            avg_cc=r.avg_cc,
        )
        out.append(PlayerRankRow(**{**r.__dict__, "power": s}))
    return out


def compute_player_rankings(
    store: Store,
    *,
    min_games: int = 5,
    hero: str | None = None,
    baseline: PowerBaseline | None = None,
) -> list[PlayerRankRow]:
    """Return every player who has shared a match with the squad.

    Pulls *all* matching rows from the DB, scores them, sorts by
    power descending, and assigns a permanent ``rank`` (1..N) based
    on power so callers can show "this player is ranked #128 by
    combat power" even when displaying a different sort order. The
    dialog applies its own slicing and column sorts on top.

    ``hero`` restricts the aggregate to games where the player was
    on that hero (used by the player dialog's hero dropdown).
    """
    squad = tuple(store.squad_handles())
    if not squad:
        return []

    rows = store.player_rankings_seen(
        squad,
        min_games=min_games,
        limit=10_000,
        hero=hero,
    )
    if not rows:
        return []
    if baseline is None:
        baseline = build_power_baseline(store)
    scored = [_row_to_rank(r, rank=0) for r in rows]
    scored = _score_population(scored, baseline)
    scored.sort(key=lambda p: -p.power)

    return [
        PlayerRankRow(**{**p.__dict__, "rank": i + 1})
        for i, p in enumerate(scored)
    ]
