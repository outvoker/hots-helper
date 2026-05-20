"""Plain-text BP-brief builder used by the "copy report" button.

Lives in its own module so we can unit-test it without spinning up a Qt
widget. The popup hands us its current dataclass state (bans / picks /
per-card summaries + rank flag) and we render a chat-friendly digest.

The output is intentionally text-only — squad members paste this into a
Discord / WeChat / Slack DM during the loading screen and want it to
read cleanly without any Markdown post-processing.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..bp import BanCandidate, MapTierBan, PickCandidate
from ..i18n import t
from ..lookup import HeroUsage, PlayerSummary
from ..player_rank import PlayerRankRow


@dataclass
class CardBrief:
    """The minimal slot state the brief needs to render one player line."""
    typed_name: str
    summaries: list[PlayerSummary]
    flag_kind: str          # "" | "worst" | "best"
    flag_rank: PlayerRankRow | None


@dataclass
class SquadMemberMapBrief:
    """Top heroes a squad member runs on the current map."""
    display_name: str
    map_games: int          # total games this member has played on the map
    map_wins: int
    top_heroes: list[HeroUsage]   # up to 3, already sorted by winrate desc


def _fmt_kda(k: float, d: float, a: float) -> str:
    return f"{k:.1f}/{d:.1f}/{a:.1f}"


def _fmt_pct(x: float) -> str:
    return f"{x*100:.0f}%"


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


# --- ban / pick sections -----------------------------------------------------


def _ban_lines(
    bans: list[BanCandidate], map_tier: list[MapTierBan]
) -> list[str]:
    out: list[str] = []
    if bans:
        out.append(t("ui.popup.brief.ban_history_header"))
        for c in bans:
            wr = (c.total_wins / c.total_games) if c.total_games else 0.0
            top_contrib = ", ".join(name for name, *_ in c.contributors[:3])
            line = (
                f"  • {c.hero}  "
                f"{c.total_wins}/{c.total_games} ({_fmt_pct(wr)})"
            )
            if top_contrib:
                line += f"  — {top_contrib}"
            out.append(line)
    if map_tier:
        out.append(t("ui.popup.brief.ban_map_header"))
        for c in map_tier:
            squad_note = (
                t("ui.popup.brief.we_never_play")
                if c.squad_games_on_hero == 0
                else t("ui.popup.brief.we_play_n", n=c.squad_games_on_hero)
            )
            out.append(
                f"  • {c.hero}  "
                f"{c.map_wins}/{c.map_games} ({_fmt_pct(c.map_winrate)}, "
                f"WLB {_fmt_pct(c.map_wilson_lb)}) — {squad_note}"
            )
    if not bans and not map_tier:
        out.append("  " + t("ui.popup.brief.ban_empty"))
    return out


def _pick_lines(picks: list[PickCandidate]) -> list[str]:
    if not picks:
        return ["  " + t("ui.popup.brief.pick_empty")]
    out: list[str] = []
    for c in picks:
        sig = "✓ " if c.significant else ""
        lift = (
            t("ui.popup.brief.lift_above", lift=f"{c.lift_pp:+.0f}")
            if abs(c.lift_pp) >= 0.5
            else t("ui.popup.brief.lift_neutral")
        )
        out.append(
            f"  • {sig}{c.hero}  "
            f"{c.map_wins}/{c.map_games} ({_fmt_pct(c.map_winrate)}, "
            f"WLB {_fmt_pct(c.map_wilson_lb)}) — {lift}"
        )
    return out


# --- player section ----------------------------------------------------------


def _player_card_lines(card: CardBrief, *, side: str) -> list[str]:
    """Render one player slot as 1-3 lines.

    Layout per slot:
        <name> [flag]
            <X 局/Y%/KDA · 平均英伤 dmg · …>          (if data)
            <上次：日期 · 英雄 · 胜负 · KDA>          (if recent_match)
    """
    name = card.typed_name or "?"
    flag_suffix = ""
    if card.flag_kind == "worst" and card.flag_rank is not None:
        flag_suffix = "  " + t(
            "ui.popup.brief.flag_worst", power=f"{card.flag_rank.power:.0f}"
        )
    elif card.flag_kind == "best" and card.flag_rank is not None:
        flag_suffix = "  " + t(
            "ui.popup.brief.flag_best", power=f"{card.flag_rank.power:.0f}"
        )

    lines: list[str] = [f"  - {name}{flag_suffix}"]
    if not card.summaries:
        lines.append(f"      {t('ui.popup.brief.no_data')}")
        return lines

    # Use the first (or most-played) summary when the same display name
    # resolves to multiple handles. The brief is meant to be skim-able,
    # so we don't list every handle.
    s = max(card.summaries, key=lambda x: x.total_games)
    if s.note and not s.total_games:
        lines.append(f"      {t('ui.popup.brief.note_not_found')}")
        return lines

    k, d, a = s.overall_kda
    lines.append(
        "      "
        + t(
            "ui.popup.brief.summary_line",
            games=s.total_games,
            wr=_fmt_pct(s.winrate),
            kda=_fmt_kda(k, d, a),
            hd=_fmt_k(s.avg_hero_dmg),
            hl=_fmt_k(s.avg_healing),
            dt=_fmt_k(s.avg_dmg_taken),
        )
    )

    # Top 3 signature heroes (skip when nothing useful — e.g. brand new
    # player with one game, where map_heroes already covers it).
    sig = (s.map_heroes or s.signature_heroes)[:3]
    if sig:
        parts = []
        for h in sig:
            parts.append(
                t(
                    "ui.popup.brief.hero_chip",
                    hero=h.hero,
                    games=h.games,
                    wr=_fmt_pct(h.winrate),
                )
            )
        lines.append("      " + " · ".join(parts))

    if s.recent_matches:
        last = s.recent_matches[0]
        result_word = t(
            "ui.popup.card.match_won" if last.result == 1
            else "ui.popup.card.match_lost"
        )
        lines.append(
            "      "
            + t(
                "ui.popup.brief.last_match",
                when=last.played_at[:10],
                hero=last.hero,
                result=result_word,
                kda=_fmt_kda(last.kills, last.deaths, last.assists),
            )
        )
    return lines


# --- top-level ---------------------------------------------------------------


def _squad_lines(members: list[SquadMemberMapBrief]) -> list[str]:
    if not members:
        return ["  " + t("ui.popup.brief.squad_empty")]
    out: list[str] = []
    for m in members:
        wr = (m.map_wins / m.map_games) if m.map_games else 0.0
        out.append(
            f"  - {m.display_name}  "
            + t(
                "ui.popup.brief.squad_total",
                games=m.map_games,
                wr=_fmt_pct(wr),
            )
        )
        if not m.top_heroes:
            out.append(f"      {t('ui.popup.brief.squad_no_top')}")
            continue
        for h in m.top_heroes:
            out.append(
                "      "
                + t(
                    "ui.popup.brief.squad_hero_line",
                    hero=h.hero,
                    games=h.games,
                    wins=h.wins,
                    wr=_fmt_pct(h.winrate),
                    kda=_fmt_kda(h.avg_k, h.avg_d, h.avg_a),
                    hd=_fmt_k(h.avg_hero_dmg),
                    hl=_fmt_k(h.avg_healing),
                    dt=_fmt_k(h.avg_dmg_taken),
                )
            )
    return out


def build_brief(
    *,
    map_name: str | None,
    bans: list[BanCandidate],
    map_tier_bans: list[MapTierBan],
    picks: list[PickCandidate],
    ally_cards: list[CardBrief],
    enemy_cards: list[CardBrief],
    squad_map_briefs: list[SquadMemberMapBrief] | None = None,
) -> str:
    """Compose the final clipboard text. UTF-8, plain text, line breaks."""
    blocks: list[list[str]] = []

    header = [t("ui.popup.brief.title")]
    if map_name:
        header.append(t("ui.popup.brief.map_line", map=map_name))
    blocks.append(header)

    blocks.append([t("ui.popup.brief.ban_section")] + _ban_lines(bans, map_tier_bans))
    blocks.append([t("ui.popup.brief.pick_section")] + _pick_lines(picks))

    # Squad's own track record on this map — only meaningful when a map
    # is in scope. Helps the squad pick something they're already strong
    # on instead of a flavour-of-the-month meta hero.
    if map_name and squad_map_briefs is not None:
        blocks.append(
            [t("ui.popup.brief.squad_section", map=map_name)]
            + _squad_lines(squad_map_briefs)
        )

    # Allies / enemies. Skip empty slots so a half-filled draft doesn't
    # produce ten blank "?" lines.
    ally_block: list[str] = [t("ui.popup.brief.allies_section")]
    for c in ally_cards:
        if not c.typed_name and not c.summaries:
            continue
        ally_block.extend(_player_card_lines(c, side="ally"))
    if len(ally_block) > 1:
        blocks.append(ally_block)

    enemy_block: list[str] = [t("ui.popup.brief.enemies_section")]
    for c in enemy_cards:
        if not c.typed_name and not c.summaries:
            continue
        enemy_block.extend(_player_card_lines(c, side="enemy"))
    if len(enemy_block) > 1:
        blocks.append(enemy_block)

    return "\n".join("\n".join(block) for block in blocks)
