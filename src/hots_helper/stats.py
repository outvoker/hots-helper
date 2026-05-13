"""Small-sample statistics helpers for hero/map winrate analysis.

These live here (not inline in the store/CLI) so they can be unit-tested and
reused by the UI layer without importing heavy dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 95% two-sided z value. Not 1.96 — we use 1.959963985... for a touch more
# precision since this code runs once per hero and cost is trivial.
Z_95 = 1.959963984540054


def wilson_lower_bound(wins: int, games: int, z: float = Z_95) -> float:
    """Lower bound of the Wilson score interval for ``wins / games``.

    Standard technique for ranking things by "probably good" winrate in the
    presence of small samples. 2/2 → ~0.34, 14/20 → ~0.48, 70/100 → ~0.60.

    Returns 0.0 when ``games <= 0``.
    """
    if games <= 0:
        return 0.0
    n = games
    p = wins / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (center - margin) / denom)


def wilson_upper_bound(wins: int, games: int, z: float = Z_95) -> float:
    if games <= 0:
        return 0.0
    n = games
    p = wins / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (center + margin) / denom)


def beta_mean(wins: int, games: int, prior_wins: float = 5, prior_losses: float = 5) -> float:
    """Bayesian posterior mean with a Beta prior.

    Beta(5, 5) says "before seeing data, I believe winrate is around 50% with
    moderate confidence." 0/2 → 0.38 (not 0.00); 2/2 → 0.58 (not 1.00);
    20/30 → 0.62 (close to MLE 0.67 once evidence dominates the prior).
    """
    return (wins + prior_wins) / (games + prior_wins + prior_losses)


# --- Two-proportion z-test ---------------------------------------------------


@dataclass
class ZTestResult:
    z: float
    p_value: float           # two-sided
    lift: float              # p1 - p2
    significant_05: bool
    significant_10: bool

    @property
    def direction(self) -> str:
        if not self.significant_10:
            return "neutral"
        return "better" if self.lift > 0 else "worse"


def _std_normal_sf(x: float) -> float:
    """Survival function (1 - CDF) of the standard normal.

    math.erfc-based; avoids pulling scipy as a dependency.
    """
    return 0.5 * math.erfc(x / math.sqrt(2))


def two_proportion_z_test(
    wins1: int, games1: int, wins2: int, games2: int
) -> ZTestResult:
    """Compare p1 = wins1/games1 against p2 = wins2/games2.

    Returns the standard pooled-proportion z-statistic and two-sided p-value.
    NaN-safe: returns neutral result when either sample has zero games.
    """
    if games1 == 0 or games2 == 0:
        return ZTestResult(z=0.0, p_value=1.0, lift=0.0, significant_05=False, significant_10=False)

    p1 = wins1 / games1
    p2 = wins2 / games2
    p_pool = (wins1 + wins2) / (games1 + games2)

    variance = p_pool * (1 - p_pool) * (1 / games1 + 1 / games2)
    if variance <= 0:
        return ZTestResult(z=0.0, p_value=1.0, lift=p1 - p2, significant_05=False, significant_10=False)

    z = (p1 - p2) / math.sqrt(variance)
    # Two-sided p-value.
    p_value = 2 * _std_normal_sf(abs(z))
    return ZTestResult(
        z=z,
        p_value=p_value,
        lift=p1 - p2,
        significant_05=p_value < 0.05,
        significant_10=p_value < 0.10,
    )
