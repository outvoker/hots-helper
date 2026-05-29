"""Frameless, always-on-top popup used during the draft phase.

Layout:
  [map input]                                                   [X close]
  +---------------------------------------------------------+
  | Ban suggestions (from enemy history)                    |
  +---------------------------------------------------------+
  | Pick suggestions (based on map statistics)              |
  +---------------------------------------------------------+
  | Allies (5 editable slots)  |  Enemies (5 editable slots) |
  +---------------------------+-----------------------------+

Each player slot is a card with:
- an editable name line (populated from OCR, user can correct)
- overall K/D/A + winrate
- top-3 signature heroes (games + WR + K/D/A)
- an expand button to show every hero the player has used
- a refresh button to re-run the query after correcting the name
"""

from __future__ import annotations

from PySide6.QtCore import (
    Qt,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    QTimer,
    Signal,
)
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..bp import (
    BanCandidate,
    MapTierBan,
    PickCandidate,
    TalentPick,
    recommend_bans,
    recommend_map_strong_bans,
    recommend_picks,
)
from ..db import Store
from ..i18n import on_change as on_lang_change, t
from ..lookup import PlayerSummary, lookup_players
from ..maps import all_maps
from ..player_rank import (
    PlayerRankRow,
    compute_player_rankings,
)
from ..talent_names import talent_label
from .macos_overlay import make_overlay_floating
from .popup_brief import CardBrief, SquadMemberMapBrief, build_brief
from .theme import (
    BG_DEEP,
    BG_ELEVATED,
    BG_HOVER,
    BG_INPUT,
    GOLD,
    GOLD_BRIGHT,
    GOLD_DIM,
    LINE,
    TEXT,
    TEXT_DIM,
)


# --- formatting helpers -------------------------------------------------------


def _fmt_pct(wr: float) -> str:
    return f"{wr*100:.0f}%"


def _fmt_kda(k: float, d: float, a: float) -> str:
    return f"{k:.1f}/{d:.1f}/{a:.1f}"


def _fmt_relative_time(played_at_iso: str) -> str:
    """Render an ISO timestamp as a coarse relative phrase.

    Anything within 24 h shows hours; days under 14 show days; weeks
    under 8 show weeks; otherwise months. The display is i18n'd via
    the ui.popup.card.last_match_* keys so locale changes pick up the
    right unit suffix without code changes.
    """
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(played_at_iso)
    except Exception:
        return played_at_iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        secs = 0
    if secs < 3600:
        n = max(1, secs // 60)
        return t("ui.popup.card.relative_minutes", n=n)
    if secs < 86_400:
        n = secs // 3600
        return t("ui.popup.card.relative_hours", n=n)
    days = secs // 86_400
    if days < 14:
        return t("ui.popup.card.relative_days", n=days)
    if days < 56:
        return t("ui.popup.card.relative_weeks", n=days // 7)
    return t("ui.popup.card.relative_months", n=days // 30)


def _fmt_k(value: float) -> str:
    """Render large counts compactly (43,123 → 43k; 510 → 510)."""
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


def _hero_line(h) -> str:
    """One line per hero. Two visual lines: KDA summary, then per-metric
    breakdown so the user can read the player's role from the numbers."""
    main = t("ui.popup.card.hero_line_main",
             games=h.games,
             wr=f"{h.winrate*100:.0f}",
             kda=_fmt_kda(h.avg_k, h.avg_d, h.avg_a))
    metrics = t("ui.popup.card.hero_line_metrics",
                hd=_fmt_k(h.avg_hero_dmg),
                dt=_fmt_k(h.avg_dmg_taken),
                strd=_fmt_k(h.avg_structure_dmg),
                hl=_fmt_k(h.avg_healing),
                xp=_fmt_k(h.avg_xp),
                cc=f"{h.avg_cc:.0f}")
    return (
        f"<b>{h.hero}</b>  "
        f"<span style='color:#aaa;'>{main}<br>"
        f"&nbsp;&nbsp;&nbsp;&nbsp;{metrics}</span>"
    )


def _summary_body_html(summary: PlayerSummary, expanded: bool) -> str:
    """Render the body of a player card based on a PlayerSummary."""
    if summary.note:
        # Translate well-known notes; pass anything unknown through.
        if summary.note == "not found in local database":
            note = t("ui.popup.card.note_not_found")
        else:
            note = summary.note
        return f'<span style="color:#b77;">{note}</span>'

    k, d, a = summary.overall_kda
    parts: list[str] = []
    parts.append(
        t(
            "ui.popup.card.summary_line",
            games=f"<b>{summary.total_games}</b>",
            wr=f"<b>{summary.winrate*100:.0f}</b>",
            kda=_fmt_kda(k, d, a),
        )
    )
    if summary.total_games:
        parts.append(
            "<span style='color:#9ad;'>"
            + t("ui.popup.card.career_avg",
                hd=_fmt_k(summary.avg_hero_dmg),
                dt=_fmt_k(summary.avg_dmg_taken),
                hl=_fmt_k(summary.avg_healing),
                xp=_fmt_k(summary.avg_xp),
                cc=f"{summary.avg_cc:.0f}")
            + "</span>"
        )

    # "Last seen" line: most recent match's time + hero + KDA. The
    # relative-time helper turns 2026-04-28 into "21 天前" so the
    # user can tell at a glance whether this is a recent encounter
    # or an ancient ghost from the squad's first month of replays.
    if summary.recent_matches:
        last = summary.recent_matches[0]
        when = _fmt_relative_time(last.played_at)
        result_word = t(
            "ui.popup.card.match_won" if last.result == 1
            else "ui.popup.card.match_lost"
        )
        result_color = "#7c7" if last.result == 1 else "#c77"
        parts.append(
            "<span style='color:#888;'>"
            + t(
                "ui.popup.card.last_match",
                when=when,
                hero=f"<b style='color:#cdb;'>{last.hero}</b>",
                result=f"<span style='color:{result_color};'>{result_word}</span>",
                kda=_fmt_kda(last.kills, last.deaths, last.assists),
            )
            + "</span>"
        )

    # Per-map heroes first (when a map is in scope) — highest-winrate at
    # the top so the user can see "this player wins on this map with
    # hero X". Then the all-maps list below for context.
    if summary.map_heroes:
        shown_map = summary.map_heroes if expanded else summary.map_heroes[:3]
        parts.append(f"<u>{t('ui.popup.card.heroes_used_on_map')}</u>")
        for h in shown_map:
            parts.append("&nbsp;&nbsp;• " + _hero_line(h))
        remaining_map = len(summary.map_heroes) - len(shown_map)
        if not expanded and remaining_map > 0:
            parts.append(t("ui.popup.card.more_heroes", n=remaining_map))

    heroes = summary.signature_heroes
    if not heroes:
        if not summary.map_heroes:
            parts.append(t("ui.popup.card.no_hero_usage"))
        return "<br>".join(parts)

    shown = heroes if expanded else heroes[:3]
    # When map_heroes is also present, label this section as the
    # all-maps fallback so the two lists don't look like duplicates.
    section_key = (
        "ui.popup.card.heroes_used_all"
        if summary.map_heroes
        else "ui.popup.card.heroes_used"
    )
    parts.append(f"<u>{t(section_key)}</u>")
    for h in shown:
        parts.append("&nbsp;&nbsp;• " + _hero_line(h))
    remaining = len(heroes) - len(shown)
    if not expanded and remaining > 0:
        parts.append(t("ui.popup.card.more_heroes", n=remaining))
    return "<br>".join(parts)


# --- player card ------------------------------------------------------------


class _PlayerCard(QFrame):
    """A single player slot; emits a signal when the user wants to re-query."""

    refresh_requested = Signal()
    region_select_requested = Signal()

    def __init__(self, *, accent: str) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        # Object name lets us scope the QSS border-color override that
        # ``set_flag`` applies (otherwise the rule would cascade to
        # every QFrame inside, including the inner sub-cards).
        self.setObjectName("playerCard")
        self._base_qss = (
            f"QFrame#playerCard {{ background: {BG_DEEP}; color: {TEXT};"
            f"          border: 1px solid {LINE}; border-radius: 8px; }}"
            f"QLineEdit {{ background: {BG_INPUT}; color: {TEXT};"
            f"            border: 1px solid {LINE}; padding: 3px 6px; font-size: 11pt; }}"
            f"QLineEdit:focus {{ border: 1px solid {accent}; }}"
            f"QLabel#title {{ color: {accent}; font-weight: 600; }}"
            f"QPushButton {{ background: {BG_INPUT}; color: {TEXT};"
            f"             border: 1px solid {LINE}; padding: 1px 8px;"
            f"             border-radius: 4px; font-size: 9pt; }}"
            f"QPushButton:hover {{ background: {BG_ELEVATED};"
            f"                    border-color: {GOLD_DIM};"
            f"                    color: {GOLD_BRIGHT}; }}"
        )
        self.setStyleSheet(self._base_qss)
        self._expanded = False
        self._summaries: list[PlayerSummary] = []
        # Last flag state — kept here so the "copy brief" button can
        # render the same low-power / high-power tag without recomputing
        # the leaderboard.
        self._flag_kind: str = ""
        self._flag_rank: PlayerRankRow | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)

        # Name row: editable field + confidence label + buttons.
        row = QHBoxLayout()
        row.setSpacing(4)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(t("ui.popup.card.player_name_placeholder"))
        self.name_edit.returnPressed.connect(self.refresh_requested)
        row.addWidget(self.name_edit, 1)

        # OCR confidence indicator: shows "94%" in grey, or "50%" in
        # warning-yellow when below threshold. Hidden when no OCR was run
        # (user typed manually). The user can scan the column to spot
        # which slots need double-checking.
        self.conf_label = QLabel("")
        self.conf_label.setFixedWidth(46)
        self.conf_label.setStyleSheet("color:#888; font-size:9pt;")
        self.conf_label.setAlignment(Qt.AlignCenter)
        row.addWidget(self.conf_label)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setToolTip(t("ui.popup.card.requery_tip"))
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.clicked.connect(self.refresh_requested)
        row.addWidget(self.refresh_btn)

        # Manual region select: user drags a rectangle over the player's
        # name on the original screenshot, we re-OCR just that crop.
        self.region_btn = QPushButton("🎯")
        self.region_btn.setToolTip(t("ui.popup.card.region_tip"))
        self.region_btn.setFixedWidth(30)
        self.region_btn.clicked.connect(self.region_select_requested)
        row.addWidget(self.region_btn)

        self.expand_btn = QPushButton("▼")
        self.expand_btn.setToolTip(t("ui.popup.card.expand_tip"))
        self.expand_btn.setFixedWidth(30)
        self.expand_btn.setCheckable(True)
        self.expand_btn.toggled.connect(self._toggle_expanded)
        row.addWidget(self.expand_btn)
        v.addLayout(row)

        # Resolved name header (e.g. "Stored as: ..."). Hidden when name
        # matches exactly.
        self.resolved = QLabel("")
        self.resolved.setObjectName("title")
        self.resolved.setStyleSheet("color:#8cf; font-size: 9pt;")
        self.resolved.setVisible(False)
        v.addWidget(self.resolved)

        # Leaderboard flag — only visible when this slot's handle shows
        # up on the worst-teammate board (ally side) or the strongest-
        # opponent board (enemy side). Styled by ``set_flag``.
        self.flag_label = QLabel("")
        self.flag_label.setVisible(False)
        v.addWidget(self.flag_label)

        # Body
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        v.addWidget(self.body)

        v.addStretch(1)

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    def set_name(self, name: str, confidence: float = 1.0) -> None:
        self.name_edit.setText(name)
        # Visual hint for low-confidence OCR. Anything below 0.7 gets a
        # yellow border + warning-coloured label.
        if name and confidence > 0:
            self.conf_label.setText(f"{confidence*100:.0f}%")
            if confidence < 0.7:
                self.name_edit.setStyleSheet(
                    "background: #1b1b1b; color: #fd6; "
                    "border: 2px solid #d90; padding: 2px 5px; font-size: 11pt;"
                )
                self.name_edit.setToolTip(
                    f"OCR confidence {confidence*100:.0f}% — please double-check"
                )
                self.conf_label.setStyleSheet(
                    "color:#fd6; font-size:9pt; font-weight:600;"
                )
            else:
                self.name_edit.setStyleSheet("")
                self.name_edit.setToolTip("")
                self.conf_label.setStyleSheet("color:#9a9; font-size:9pt;")
        else:
            self.name_edit.setStyleSheet("")
            self.name_edit.setToolTip("")
            self.conf_label.setText("")
            self.conf_label.setStyleSheet("color:#888; font-size:9pt;")

    def set_summaries(self, summaries: list[PlayerSummary]) -> None:
        self._summaries = summaries
        self._render()

    def set_flag(
        self,
        kind: str,
        power: float,
        games: int,
        win_rate: float,
        *,
        rank_row: PlayerRankRow | None = None,
    ) -> None:
        """Flag this slot based on the player's combat-power percentile.

        ``kind == "worst"`` → ally slot in the bottom 25% by power
        (red banner, "low-power teammate"). ``kind == "best"`` →
        enemy slot in the top 25% (gold banner, "high-power opponent").
        ``kind == ""`` clears the flag.
        """
        self._flag_kind = kind
        self._flag_rank = rank_row if kind else None
        if not kind:
            self.flag_label.hide()
            self.flag_label.setText("")
            self.setStyleSheet(self._base_qss)
            return

        wr_pct = int(round(win_rate * 100))
        power_str = f"{power:.0f}"
        if kind == "worst":
            text = t(
                "ui.popup.card.flag_worst",
                power=power_str, games=games, wr=wr_pct,
            )
            tone = "#e08585"   # warning red
            tone_bg = "#3a1a1a"
        else:
            text = t(
                "ui.popup.card.flag_best",
                power=power_str, games=games, wr=wr_pct,
            )
            tone = GOLD_BRIGHT
            tone_bg = "#3a2c10"
        self.flag_label.setText(text)
        self.flag_label.setStyleSheet(
            f"background:{tone_bg}; color:{tone};"
            f" border:1px solid {tone}; border-radius:4px;"
            f" padding:2px 6px; font-weight:600; font-size:9pt;"
        )
        self.flag_label.show()
        # Re-skin the card with a 2px coloured border so it pops next
        # to neighbouring cards. Keep all other styling identical to
        # the base QSS.
        self.setStyleSheet(
            self._base_qss
            + f"\nQFrame#playerCard {{ border: 2px solid {tone}; }}"
        )

    def clear(self) -> None:
        self._summaries = []
        self.resolved.setVisible(False)
        self.body.setText("")
        self.set_flag("", 0, 0, 0.0)

    def _toggle_expanded(self, checked: bool) -> None:
        self._expanded = checked
        self.expand_btn.setText("▲" if checked else "▼")
        self._render()

    def _render(self) -> None:
        if not self._summaries:
            self.body.setText(t("ui.popup.card.no_data"))
            self.resolved.setVisible(False)
            return
        # When multiple handles share a display name, show them stacked.
        typed_name = self.name_edit.text().strip()
        blocks: list[str] = []
        any_resolved_diff = False
        for s in self._summaries:
            header = ""
            if s.display_name and s.display_name != typed_name:
                any_resolved_diff = True
                header = (
                    f"<span style='color:#8cf; font-weight:600;'>{s.display_name}</span>"
                    f"<br>"
                )
            blocks.append(header + _summary_body_html(s, self._expanded))
        self.body.setText("<hr style='border-color:#333;'>".join(blocks))
        self.resolved.setVisible(any_resolved_diff)
        if any_resolved_diff and len(self._summaries) == 1:
            self.resolved.setText(
                t("ui.popup.card.found_as", name=self._summaries[0].display_name)
            )
        else:
            self.resolved.setText("")


# --- ban/pick lists ---------------------------------------------------------


# Tag thresholds for the per-opponent capsule. Mirrors the BP flag cuts
# (top / bottom 25 % of the global power ranking) but with a minimum
# sample size so a 1-game outlier doesn't pin a 💀 on someone.
_PROFILE_TAG_HIGH_PCT = 0.75
_PROFILE_TAG_LOW_PCT = 0.25
_PROFILE_TAG_MIN_GAMES = 5
_PROFILE_TAG_FRIEND_MIN_ALLY = 3


def _power_color(power: float) -> str:
    """Map a 0..100 power score to a readable colour for the capsule."""
    if power >= 75:
        return "#ffd97a"   # gold
    if power >= 50:
        return "#cfd9c4"   # neutral
    if power >= 25:
        return "#caa"
    return "#e08585"


def _format_profile_line(p) -> str:
    """One-line capsule per opponent: name · power · ally/enemy split.

    Renders into the rich-text block at the top of ``_BanList`` so the
    user can see who they're playing against without scrolling through
    every threat hero. ``p`` is a :class:`bp.OpponentProfile`.
    """
    name = p.display_name or p.name_searched
    if not p.toon_handle:
        # AI slot or completely unseen handle — keep the line short.
        # ``ban_player_not_in_db`` already wraps itself in a <span>.
        return (
            f"&nbsp;&nbsp;• <b>{name}</b> "
            f"{t('ui.popup.ban_player_not_in_db')}"
        )

    parts: list[str] = [f"<b>{name}</b>"]

    # Power chip.
    if p.power > 0:
        col = _power_color(p.power)
        chip = (
            f"<span style='color:{col};'>"
            + t("ui.popup.profile_power", power=f"{p.power:.0f}")
            + "</span>"
        )
        if p.power_total:
            chip += (
                f" <span style='color:#888;'>"
                + t("ui.popup.profile_power_rank",
                    rank=p.power_rank, total=p.power_total)
                + "</span>"
            )
        parts.append(chip)
    else:
        parts.append(
            f"<span style='color:#888;'>{t('ui.popup.profile_no_power')}</span>"
        )

    # Side split. Show whichever sides actually have games; if both are
    # zero (handle is in the DB but never crossed our squad), say so.
    side_chunks: list[str] = []
    if p.ally_games:
        wr = (p.ally_wins / p.ally_games) * 100 if p.ally_games else 0.0
        losses = p.ally_games - p.ally_wins
        side_chunks.append(
            f"<span style='color:#8cf;'>"
            + t(
                "ui.popup.profile_with_us",
                games=p.ally_games,
                w=p.ally_wins,
                l=losses,
                wr=f"{wr:.0f}",
            )
            + "</span>"
        )
    if p.enemy_games:
        wr = (p.enemy_wins / p.enemy_games) * 100 if p.enemy_games else 0.0
        losses = p.enemy_games - p.enemy_wins
        side_chunks.append(
            f"<span style='color:#f88;'>"
            + t(
                "ui.popup.profile_vs_us",
                games=p.enemy_games,
                w=p.enemy_wins,
                l=losses,
                wr=f"{wr:.0f}",
            )
            + "</span>"
        )
    if side_chunks:
        parts.append(" · ".join(side_chunks))
    else:
        parts.append(
            f"<span style='color:#888;'>{t('ui.popup.profile_no_history')}</span>"
        )

    # Tags: only when the ranking has enough population to be meaningful
    # AND this player has crossed our path enough times to trust the
    # signal. Multiple tags can stack (rare but possible — e.g. a strong
    # opponent who was once a teammate).
    tags: list[str] = []
    has_rank_pop = p.power_total and p.power_rank
    if has_rank_pop:
        high_cut = max(1, int(p.power_total * (1.0 - _PROFILE_TAG_HIGH_PCT)))
        low_cut = max(1, int(p.power_total * (1.0 - _PROFILE_TAG_LOW_PCT)))
        shared_games = p.ally_games + p.enemy_games
        if (
            p.power_rank <= high_cut
            and shared_games >= _PROFILE_TAG_MIN_GAMES
        ):
            tags.append(
                f"<span style='color:#ffd97a; font-weight: bold;'>"
                f"{t('ui.popup.profile_tag_smurf')}</span>"
            )
        elif (
            p.power_rank > low_cut
            and shared_games >= _PROFILE_TAG_MIN_GAMES
        ):
            tags.append(
                f"<span style='color:#e08585; font-weight: bold;'>"
                f"{t('ui.popup.profile_tag_troll')}</span>"
            )
    if p.ally_games >= _PROFILE_TAG_FRIEND_MIN_ALLY and p.ally_games > p.enemy_games:
        tags.append(
            f"<span style='color:#8cf;'>"
            f"{t('ui.popup.profile_tag_friend')}</span>"
        )
    if tags:
        parts.append(" ".join(tags))

    return "&nbsp;&nbsp;• " + "  ·  ".join(parts)


class _BanList(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background: {BG_ELEVATED}; color: #fbd;"
            f"         border-radius: 8px; border: 1px solid #6a3030;"
            f"         border-left: 3px solid #b34848; }}"
            "QLabel { color: #fbd; }"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.RichText)
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        self.title_label.setFont(f)
        v.addWidget(self.title_label)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        v.addWidget(self.body)
        self._last_args: tuple = ()
        self._retranslate()
        on_lang_change(lambda _c: self._on_lang())

    def _retranslate(self) -> None:
        self.title_label.setText(
            f"{t('ui.popup.ban_title')} "
            f"<span style='color:#b88; font-weight: normal;'>{t('ui.popup.ban_subtitle')}</span>"
        )

    def _on_lang(self) -> None:
        self._retranslate()
        if self._last_args:
            self.set_candidates(*self._last_args)

    def set_candidates(
        self,
        cands: list[BanCandidate],
        profiles=None,
        map_tier: list[MapTierBan] | None = None,
    ) -> None:
        self._last_args = (cands, profiles, map_tier)
        head_lines: list[str] = []

        # Top section: per-opponent capsule (power score + side history).
        # Skip when the caller didn't pass profiles at all (legacy callers
        # that only render bans), but render it even if no threats exist
        # — it's the most useful "at a glance" block.
        if profiles:
            head_lines.append(
                f"<u style='color:#fbb;'>{t('ui.popup.ban_section_profiles')}</u>"
            )
            for p in profiles:
                head_lines.append(_format_profile_line(p))
            head_lines.append("")

        head_lines.append(f"<u style='color:#fbb;'>{t('ui.popup.ban_section_history')}</u>")
        if cands:
            for c in cands:
                contrib = "  ·  ".join(
                    f"{name} <span style='color:#daa;'>{w}/{g} 胜（{(w/g*100 if g else 0):.0f}%）</span>"
                    for name, g, w, _ in c.contributors
                )
                head_lines.append(
                    f"<b>{c.hero}</b> "
                    f"<span style='color:#caa;'>"
                    f"敌方共 {c.total_wins}/{c.total_games} 胜（{c.combined_wr*100:.0f}%）"
                    f"</span>"
                    f"<br>&nbsp;&nbsp;&nbsp;&nbsp;{contrib}"
                )
        else:
            if profiles:
                head_lines.append(t("ui.popup.ban_empty_advisory"))
                for p in profiles:
                    name = p.display_name or p.name_searched
                    if p.toon_handle == "":
                        head_lines.append(
                            f"&nbsp;&nbsp;• <b>{name}</b> — "
                            + t("ui.popup.ban_player_not_in_db")
                        )
                    else:
                        head_lines.append(
                            f"&nbsp;&nbsp;• <b>{name}</b> — "
                            + t("ui.popup.ban_player_games_seen", n=p.total_games)
                        )
            else:
                head_lines.append(t("ui.popup.ban_empty_default"))

        # Bottom section: map-tier strong heroes our squad doesn't play.
        if map_tier:
            head_lines.append("")
            head_lines.append(f"<u style='color:#fbb;'>{t('ui.popup.ban_section_map')}</u>")
            for c in map_tier:
                squad_note = (
                    t("ui.popup.we_never_play")
                    if c.squad_games_on_hero == 0
                    else t("ui.popup.we_play_n", n=c.squad_games_on_hero)
                )
                head_lines.append(
                    f"<b>{c.hero}</b> "
                    f"<span style='color:#caa;'>本图 {c.map_wins}/{c.map_games} 胜（"
                    f"胜率 {c.map_winrate*100:.0f}%，保守胜率 {c.map_wilson_lb*100:.0f}%）"
                    f" · {squad_note}</span>"
                )
        self.body.setText("<br>".join(head_lines))


class _PickList(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background: {BG_ELEVATED}; color: #cfd9c4;"
            f"         border-radius: 8px; border: 1px solid {GOLD_DIM};"
            f"         border-left: 3px solid {GOLD}; }}"
            "QLabel { color: #cfd9c4; }"
            f"QPushButton {{ background: {BG_INPUT}; color: {TEXT};"
            f"             border: 1px solid {LINE}; padding: 2px 8px;"
            f"             border-radius: 4px; }}"
            f"QPushButton:hover {{ border-color: {GOLD_DIM};"
            f"                    color: {GOLD_BRIGHT}; }}"
            f"QPushButton:checked {{ background: {BG_HOVER};"
            f"                      border-color: {GOLD};"
            f"                      color: {GOLD_BRIGHT}; }}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.RichText)
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        self.title_label.setFont(f)
        v.addWidget(self.title_label)
        self._rows = QWidget()
        self._rows_layout = QVBoxLayout(self._rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._rows)
        self._last_cands: list[PickCandidate] = []
        self._retranslate()
        on_lang_change(lambda _c: self._on_lang())

    def _retranslate(self) -> None:
        self.title_label.setText(
            f"{t('ui.popup.pick_title')} "
            f"<span style='color:#9b9; font-weight: normal;'>{t('ui.popup.pick_subtitle')}</span>"
        )

    def _on_lang(self) -> None:
        self._retranslate()
        if self._last_cands is not None:
            self.set_candidates(self._last_cands)

    def set_candidates(self, cands: list[PickCandidate]) -> None:
        self._last_cands = list(cands)
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not cands:
            empty = QLabel(t("ui.popup.pick_empty"))
            empty.setTextFormat(Qt.RichText)
            self._rows_layout.addWidget(empty)
            return
        for c in cands:
            self._rows_layout.addWidget(self._build_row(c))

    def _build_row(self, c: PickCandidate) -> QWidget:
        row = QWidget()
        v = QVBoxLayout(row)
        v.setContentsMargins(0, 2, 0, 2)

        head = QHBoxLayout()
        sig = "✓ " if c.significant else ""
        # Plain-language summary: actual win-rate, conservative win-rate
        # (≥ 50% only if the lower confidence bound is solid), and how
        # much better than the global average this hero is on this map.
        # The previous "lift … pp · p=…" notation was technically
        # accurate but unreadable for non-statistician squad members.
        lift_text = (
            f"比平均高 {c.lift_pp:+.0f}%"
            if abs(c.lift_pp) >= 0.5 else "与平均持平"
        )
        sig_note = "（统计显著）" if c.significant else ""
        header = QLabel(
            f"<b>{sig}{c.hero}</b> "
            f"<span style='color:#9b9;'>本图 {c.map_wins}/{c.map_games} 胜（"
            f"胜率 {c.map_winrate*100:.0f}%，保守胜率 {c.map_wilson_lb*100:.0f}%）"
            f" · {lift_text}{sig_note}</span>"
        )
        header.setTextFormat(Qt.RichText)
        head.addWidget(header)
        head.addStretch(1)

        toggle = QPushButton(t("ui.popup.build_btn"))
        toggle.setCheckable(True)
        head.addWidget(toggle)
        v.addLayout(head)

        build_label = QLabel(self._build_html(c.recommended_build))
        build_label.setTextFormat(Qt.RichText)
        build_label.setVisible(False)
        v.addWidget(build_label)
        toggle.toggled.connect(build_label.setVisible)
        return row

    def _build_html(self, picks: list[TalentPick]) -> str:
        if not picks:
            return t("ui.popup.no_talent_data")
        return "<br>".join(
            f"<b>T{tp.tier}</b> {talent_label(tp.talent)} "
            f"<span style='color:#9b9;'>{tp.wins}/{tp.games} 胜 "
            f"（胜率 {(tp.wins/tp.games*100 if tp.games else 0):.0f}%，"
            f"保守胜率 {tp.wilson_lb*100:.0f}%，"
            f"选取率 {tp.pick_rate*100:.0f}%）</span>"
            for tp in picks
        )


# --- main popup -------------------------------------------------------------


class PopupWindow(QWidget):
    """Floating, always-on-top pre-game scout window."""

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        # Qt.Tool keeps it off the taskbar / Alt-Tab; StaysOnTop +
        # ShowWithoutActivating lets the popup float over a fullscreen
        # game without stealing focus *on show*. We deliberately do NOT
        # set Qt.WindowDoesNotAcceptFocus or focusPolicy=NoFocus on the
        # top-level — those flags cascade and prevent any child
        # QLineEdit from receiving keyboard input, even when the user
        # explicitly clicks into the field. WA_ShowWithoutActivating is
        # already enough to keep the game in the foreground; if the
        # user clicks the popup we *want* it to take focus so they can
        # type.
        self.setWindowFlags(
            Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        # Frameless popup: paint our own gold-edged border so the floating
        # window still reads as a deliberate object on top of the game.
        # ``#popupRoot`` is the object name we set below; scoping the rule
        # this way keeps it from cascading into every child QWidget.
        self.setObjectName("popupRoot")
        self.setStyleSheet(
            f"QWidget#popupRoot {{"
            f" background-color: {BG_DEEP};"
            f" border: 1px solid {GOLD_DIM};"
            f" border-radius: 10px;"
            f"}}"
        )
        self._drag_pos = None
        self._screenshot_path = None
        self._current_drafter = ""

        # Latest analysis state, captured so the "copy brief" button can
        # serialise the same data the user is looking at without
        # re-running any of the heavier BP queries.
        self._last_bans: list[BanCandidate] = []
        self._last_map_tier: list[MapTierBan] = []
        self._last_picks: list[PickCandidate] = []

        # Minimised "pill" state — a small floating chip docked near the
        # top-center-right of the primary screen. Click it to restore.
        self._is_pill = False
        self._restore_geometry: QRect | None = None
        self._geom_anim = QPropertyAnimation(self, b"geometry")
        self._geom_anim.setDuration(220)
        self._geom_anim.setEasingCurve(QEasingCurve.OutCubic)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        # --- header ---------------------------------------------------------
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet(
            f"font-size: 14pt; font-weight: 600; color: {GOLD};"
            f" letter-spacing: 0.5px; padding: 2px 4px;"
        )
        header.addWidget(self.title)
        header.addStretch(1)
        self.map_label = QLabel()
        header.addWidget(self.map_label)
        # Editable combobox: the OCR may detect a name we don't have in
        # the canonical pool (typo, new map), so still let the user type.
        # Most of the time though they'll pick from the dropdown.
        self.map_edit = QComboBox()
        self.map_edit.setEditable(True)
        self.map_edit.setInsertPolicy(QComboBox.NoInsert)
        self.map_edit.setFixedWidth(220)
        self.map_edit.addItem("")  # blank = no map filter
        for name in all_maps():
            self.map_edit.addItem(name)
        self.map_edit.lineEdit().returnPressed.connect(self._run_analysis)
        self.map_edit.activated.connect(lambda _i: self._run_analysis())
        header.addWidget(self.map_edit)
        self.analyze_btn = QPushButton()
        self.analyze_btn.clicked.connect(self._run_analysis)
        header.addWidget(self.analyze_btn)

        # One-shot brief copier — squad members paste this into Discord/
        # WeChat during the loading screen.
        self.copy_btn = QPushButton()
        self.copy_btn.clicked.connect(self._copy_brief_to_clipboard)
        self.copy_btn.setStyleSheet(
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; padding: 4px 10px;"
            f" border-radius:4px; }}"
            f"QPushButton:hover {{ border-color:{GOLD_DIM};"
            f" color:{GOLD_BRIGHT}; }}"
        )
        header.addWidget(self.copy_btn)

        # Minimise → pill button. Sits just before the close button so it
        # gets the same visual weight without pushing other controls
        # around.
        self.minimize_btn = QPushButton("–")
        self.minimize_btn.setFixedSize(28, 28)
        self.minimize_btn.setToolTip(t("ui.popup.minimize_tip"))
        self.minimize_btn.clicked.connect(self._collapse_to_pill)
        self.minimize_btn.setStyleSheet(
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:14px;"
            f" font-weight:bold; font-size: 14pt; padding:0; }}"
            f"QPushButton:hover {{ color:{GOLD_BRIGHT};"
            f" border-color:{GOLD_DIM}; }}"
        )
        header.addWidget(self.minimize_btn)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.hide)
        close_btn.setStyleSheet(
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:14px;"
            f" font-weight:bold; padding:0; }}"
            f"QPushButton:hover {{ color:#e08585;"
            f" border-color:#e08585; background:{BG_DEEP}; }}"
        )
        header.addWidget(close_btn)
        root.addLayout(header)
        # Stash the header layout so we can hide *all* its child widgets
        # except the title when collapsing into a pill.
        self._header_layout = header

        # --- scrollable body ------------------------------------------------
        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        self.ban_panel = _BanList()
        body.addWidget(self.ban_panel)
        self.pick_panel = _PickList()
        body.addWidget(self.pick_panel)

        # 5-ally | 5-enemy side-by-side
        players_row = QHBoxLayout()
        players_row.setSpacing(10)
        self._ally_col, self._ally_cards = self._make_column(
            players_row, t("ui.popup.allies"), "#8cf"
        )
        self._enemy_col, self._enemy_cards = self._make_column(
            players_row, t("ui.popup.enemies"), "#f88"
        )
        body.addLayout(players_row)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self.footer_label = QLabel()
        self.footer_label.setTextFormat(Qt.RichText)
        self.footer_label.setWordWrap(True)
        root.addWidget(self.footer_label)

        self.resize(1200, 840)
        self._retranslate()
        on_lang_change(lambda _c: self._retranslate())

    def showEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        # Stay visible across Mission Control spaces on macOS so the
        # popup follows the user into a fullscreen game's space
        # instead of getting stranded behind it.
        make_overlay_floating(self)
        super().showEvent(ev)

    def _retranslate(self) -> None:
        # Window title and visible header.
        self.setWindowTitle(t("ui.app.title") + " — " + t("ui.popup.title"))
        if self._current_drafter:
            self.title.setText(
                t("ui.popup.title_drafting", name=self._current_drafter)
            )
        else:
            self.title.setText(t("ui.popup.title"))
        self.map_label.setText(t("ui.popup.map"))
        if hasattr(self.map_edit, "lineEdit"):
            self.map_edit.lineEdit().setPlaceholderText(t("ui.popup.map_placeholder"))
        self.analyze_btn.setText(t("ui.popup.analyze"))
        self.copy_btn.setText(t("ui.popup.copy_btn"))
        self.copy_btn.setToolTip(t("ui.popup.copy_btn_tip"))
        # Side column titles
        if hasattr(self, "_ally_col"):
            self._ally_col.title_label.setText(t("ui.popup.allies"))
            self._enemy_col.title_label.setText(t("ui.popup.enemies"))
        self.footer_label.setText(f"<span style='color:#888;'>{t('ui.popup.footer')}</span>")

    def _make_column(self, parent_layout: QHBoxLayout, title: str, accent: str) -> list[_PlayerCard]:
        col = QFrame()
        col.setStyleSheet(
            f"QFrame {{ background: {BG_ELEVATED};"
            f" border: 1px solid {accent}; border-radius: 6px; }}"
        )
        v = QVBoxLayout(col)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(8)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {accent}; font-weight: 600; font-size: 11pt;")
        col.title_label = lbl   # type: ignore[attr-defined]
        v.addWidget(lbl)
        cards: list[_PlayerCard] = []
        for _ in range(5):
            card = _PlayerCard(accent=accent)
            card.refresh_requested.connect(
                lambda c=card: self._requery_single(c)
            )
            card.region_select_requested.connect(
                lambda c=card: self._select_region_for(c)
            )
            cards.append(card)
            v.addWidget(card)
        parent_layout.addWidget(col)
        return col, cards

    # --- public API -----------------------------------------------------------

    def show_for_map(
        self,
        map_name: str | None,
        *,
        ally_names: list[str] | None = None,
        enemy_names: list[str] | None = None,
        ally_confidences: list[float] | None = None,
        enemy_confidences: list[float] | None = None,
        drafter: str | None = None,
        screenshot_path=None,
    ) -> None:
        self._screenshot_path = screenshot_path
        if map_name is not None:
            self.map_edit.setCurrentText(map_name)
        if ally_names:
            confs = ally_confidences or [1.0] * 5
            for i, n in enumerate(ally_names[:5]):
                self._ally_cards[i].set_name(n, confs[i] if i < len(confs) else 1.0)
        if enemy_names:
            confs = enemy_confidences or [1.0] * 5
            for i, n in enumerate(enemy_names[:5]):
                self._enemy_cards[i].set_name(n, confs[i] if i < len(confs) else 1.0)
        # Drafter banner (the player currently picking) is shown in the title
        # bar so the user can copy-paste them into the right slot. We don't
        # auto-fill because we can't reliably guess which side they belong to.
        self._current_drafter = drafter or ""
        if drafter:
            self.title.setText(t("ui.popup.title_drafting", name=drafter))
        else:
            self.title.setText(t("ui.popup.title"))
        # Showing fresh data is implicitly a "draft something just
        # happened" event, so always come back from pill mode here —
        # otherwise the new draft sits invisibly inside the chip.
        if self._is_pill:
            self._restore_from_pill()
        self.show()
        # raise_() bumps z-order without activating the window, so
        # fullscreen games keep their focus. Never call activateWindow().
        self.raise_()
        if map_name or ally_names or enemy_names:
            self._run_analysis()

    # --- frameless drag -------------------------------------------------------

    def mousePressEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.button() == Qt.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = ev.globalPosition().toPoint()
            self._dragging = False
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.buttons() & Qt.LeftButton and self._drag_pos is not None:
            # In pill mode, only treat as drag once the cursor moves
            # past a small threshold — otherwise a click-to-restore
            # would always look like a tiny drag.
            delta = ev.globalPosition().toPoint() - getattr(self, "_press_pos", ev.globalPosition().toPoint())
            if not self._is_pill or abs(delta.x()) + abs(delta.y()) > 4:
                self._dragging = True
                self.move(ev.globalPosition().toPoint() - self._drag_pos)
            ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        was_dragging = getattr(self, "_dragging", False)
        self._drag_pos = None
        self._dragging = False
        if (
            self._is_pill
            and ev.button() == Qt.LeftButton
            and not was_dragging
        ):
            self._restore_from_pill()
            ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        # Double-click anywhere on the pill restores the full popup. The
        # title is also a single-click target via _on_title_click_in_pill.
        if self._is_pill and ev.button() == Qt.LeftButton:
            self._restore_from_pill()
            ev.accept()

    # --- minimise / restore ---------------------------------------------------

    _PILL_SIZE = QSize(220, 36)

    def _pill_target_geometry(self) -> QRect:
        """Top-center-right slot on the screen the popup currently lives on."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        # Top-center-but-shifted-right: 60% across, ~12px from the top.
        x = avail.x() + int(avail.width() * 0.60) - self._PILL_SIZE.width() // 2
        y = avail.y() + 12
        return QRect(QPoint(x, y), self._PILL_SIZE)

    def _collapse_to_pill(self) -> None:
        if self._is_pill:
            return
        self._is_pill = True
        self._restore_geometry = self.geometry()

        # Hide everything except the title — keep the title visible inside
        # the pill so the user can see "drafting: <hero>" at a glance.
        self._scroll.hide()
        self.footer_label.hide()
        for btn in (
            self.map_label,
            self.map_edit,
            self.analyze_btn,
            self.copy_btn,
            self.minimize_btn,
        ):
            btn.hide()
        self.title.setStyleSheet(
            f"font-size: 9pt; font-weight: 600; color:{GOLD};"
            f" padding:0 6px; letter-spacing: 0.5px;"
        )

        target = self._pill_target_geometry()
        self._geom_anim.stop()
        self._geom_anim.setStartValue(self.geometry())
        self._geom_anim.setEndValue(target)
        self._geom_anim.start()
        self.setStyleSheet(
            f"QWidget#popupRoot {{"
            f" background-color: {BG_ELEVATED};"
            f" border: 1px solid {GOLD};"
            f" border-radius: 16px;"
            f"}}"
        )

    def _restore_from_pill(self) -> None:
        if not self._is_pill:
            return
        self._is_pill = False
        target = self._restore_geometry or QRect(self.geometry().topLeft(), QSize(1200, 840))
        self._geom_anim.stop()
        self._geom_anim.setStartValue(self.geometry())
        self._geom_anim.setEndValue(target)
        self._geom_anim.start()

        self._scroll.show()
        self.footer_label.show()
        for btn in (
            self.map_label,
            self.map_edit,
            self.analyze_btn,
            self.copy_btn,
            self.minimize_btn,
        ):
            btn.show()
        self.title.setStyleSheet(
            f"font-size: 14pt; font-weight: 600; color: {GOLD};"
            f" letter-spacing: 0.5px; padding: 2px 4px;"
        )
        self.setStyleSheet(
            f"QWidget#popupRoot {{"
            f" background-color: {BG_DEEP};"
            f" border: 1px solid {GOLD_DIM};"
            f" border-radius: 10px;"
            f"}}"
        )

    # --- analysis -------------------------------------------------------------

    def _select_region_for(self, card: "_PlayerCard") -> None:
        """Open the region-select dialog and rerun OCR on the user's crop."""
        from .region_select import RegionSelectorDialog, ocr_crop
        from PySide6.QtWidgets import QMessageBox

        if not self._screenshot_path:
            QMessageBox.information(
                self,
                t("ui.popup.region.no_screenshot_title"),
                t("ui.popup.region.no_screenshot_body"),
            )
            return
        # Important: do NOT pass ``parent=self`` here. On macOS Qt
        # ties a modal child's NSWindow level to its parent's, so a
        # parented dialog ends up at the popup's NSPopUpMenuWindowLevel
        # (101) — the same level the popup itself uses. The popup's
        # own ``showEvent`` keeps re-asserting that level via
        # ``orderFrontRegardless``, which drags the parented dialog
        # back behind it within the same z-stack. Detaching from the
        # popup lets the dialog claim a clean
        # NSScreenSaverWindowLevel (1000) all to itself.
        try:
            dlg = RegionSelectorDialog(self._screenshot_path, parent=None)
        except Exception as e:
            QMessageBox.warning(self, t("ui.popup.region.cannot_open"), str(e))
            return

        chosen: dict = {}

        def _on_picked(x: int, y: int, w: int, h: int) -> None:
            chosen["bbox"] = (x, y, w, h)

        dlg.region_picked.connect(_on_picked)
        # While the region selector is up, drop the popup back down to
        # the regular Qt always-on-top level so it can't keep racing
        # the dialog for screen-saver level. We restore via
        # ``make_overlay_floating(self)`` once the dialog closes.
        try:
            from .macos_overlay import lower_overlay_level
            lower_overlay_level(self)
        except Exception:
            pass
        # PySide6 uses QDialog.DialogCode.Accepted (== 1). exec() returns
        # an int, so compare against the int directly to avoid PySide6/PyQt
        # version drift.
        try:
            result = dlg.exec()
        finally:
            # Restore the popup's overlay level no matter how the
            # dialog closed (accept, reject, exception).
            try:
                make_overlay_floating(self)
            except Exception:
                pass
        if result != 1 or "bbox" not in chosen:
            return

        x, y, w, h = chosen["bbox"]
        text = ocr_crop(self._screenshot_path, x, y, w, h)
        if text:
            card.set_name(text, confidence=1.0)
            # Trigger a re-query of just this slot (and refresh bans if it's
            # an enemy slot).
            self._requery_single(card)
        else:
            QMessageBox.information(
                self,
                t("ui.popup.region.no_text_title"),
                t("ui.popup.region.no_text_body"),
            )

    # --- leaderboard-driven highlight ---------------------------------------

    # How extreme the player has to be on the global power ranking
    # before we light up their card. 25-th and 75-th percentile cuts
    # — strong enough to be a real signal without flagging every game.
    _BP_FLAG_LOW_PCT = 0.25
    _BP_FLAG_HIGH_PCT = 0.75

    def _refresh_rank_indices(self) -> None:
        """Recompute the global power ranking once per analysis pass.

        Stored as a ``{toon_handle: PlayerRankRow}`` dict so the per-
        card flag check is O(1). The dict is the *whole* board; the
        flag logic in ``_apply_flag`` decides whether a hit in the
        bottom quartile (ally cards) or top quartile (enemy cards)
        is loud enough to warn about.
        """
        try:
            ranked = compute_player_rankings(
                self.store, min_games=5,
            )
        except Exception:
            # DB hiccups shouldn't tank the whole BP flow — the flag
            # is a nice-to-have, not load-bearing.
            ranked = []
        self._ranked_by_handle: dict[str, PlayerRankRow] = {
            p.toon_handle: p for p in ranked if p.toon_handle
        }
        self._ranked_total = len(ranked)
        if self._ranked_total > 0:
            self._high_threshold_rank = max(
                1, int(self._ranked_total * (1.0 - self._BP_FLAG_HIGH_PCT))
            )
            self._low_threshold_rank = max(
                1, int(self._ranked_total * (1.0 - self._BP_FLAG_LOW_PCT))
            )
        else:
            self._high_threshold_rank = 0
            self._low_threshold_rank = 0

    def _apply_flag(
        self,
        card: "_PlayerCard",
        summaries: list[PlayerSummary],
        *,
        side: str,
    ) -> None:
        """Light up ``card`` based on the player's combat-power score.

        Ally cards (``side="ally"``) flag the bottom 25% of the
        ranking — these are the low-power players who'll likely
        drag the team down. Enemy cards (``side="enemy"``) flag
        the top 25% — strong opponents to watch out for. When the
        same display name resolves to multiple handles we pick the
        most extreme one in the relevant direction; the banner
        shows the player's actual power score so the user sees
        *why* the flag fired.
        """
        if not summaries or not getattr(self, "_ranked_total", 0):
            card.set_flag("", 0, 0, 0.0)
            return

        index = self._ranked_by_handle
        candidates: list[PlayerRankRow] = [
            index[s.toon_handle]
            for s in summaries
            if s.toon_handle in index
        ]
        if not candidates:
            card.set_flag("", 0, 0, 0.0)
            return

        if side == "ally":
            # Worst (highest rank number) = most likely to drag us down.
            chosen = max(candidates, key=lambda p: p.rank)
            if chosen.rank <= self._low_threshold_rank:
                card.set_flag("", 0, 0, 0.0)
                return
            kind = "worst"
        else:
            # Best (lowest rank number) = scariest.
            chosen = min(candidates, key=lambda p: p.rank)
            if chosen.rank > self._high_threshold_rank:
                card.set_flag("", 0, 0, 0.0)
                return
            kind = "best"
        card.set_flag(
            kind=kind,
            power=chosen.power,
            games=chosen.games,
            win_rate=chosen.win_rate,
            rank_row=chosen,
        )

    def _refresh_bans(self) -> None:
        """Re-run the two ban analyses (opponent-history + map-tier)."""
        ally_names = [c.name for c in self._ally_cards if c.name]
        enemy_names = [c.name for c in self._enemy_cards if c.name]
        map_name = self.map_edit.currentText().strip() or None

        # The ranking index is normally populated by ``_run_analysis`` once
        # per pass; ``_refresh_bans`` may be called from a single-card
        # correction before that has happened, so guard against missing
        # state.
        rank_index = getattr(self, "_ranked_by_handle", {}) or {}
        rank_total = getattr(self, "_ranked_total", 0) or 0
        squad_tuple = self._squad_handles_tuple()

        if enemy_names:
            from ..bp import profile_opponents
            bans = recommend_bans(self.store, enemy_names)
            profiles = profile_opponents(
                self.store,
                enemy_names,
                rank_index=rank_index,
                rank_total=rank_total,
                squad_handles=squad_tuple,
            )
        else:
            bans = []
            profiles = []

        map_tier: list[MapTierBan] = []
        if map_name:
            squad_handles = self._resolve_squad_handles(ally_names)
            if squad_handles:
                map_tier = recommend_map_strong_bans(
                    self.store, map_name, squad_handles
                )

        self.ban_panel.set_candidates(bans, profiles=profiles, map_tier=map_tier)
        self._last_bans = list(bans)
        self._last_map_tier = list(map_tier)

    def _squad_handles_tuple(self) -> tuple[str, ...]:
        """Squad handles from the DB, cached per popup instance.

        Used both by the side-split lookup in ban profiles and by any
        future BP query that needs "us vs them" framing. Cached
        because :meth:`Store.squad_handles` scans the whole player_match
        table and we'd otherwise re-run it on every BP refresh.
        """
        cached = getattr(self, "_squad_tuple_cache", None)
        if cached is not None:
            return cached
        try:
            squad = tuple(self.store.squad_handles())
        except Exception:
            squad = ()
        self._squad_tuple_cache = squad
        return squad

    def _resolve_squad_handles(self, ally_names: list[str]) -> list[str]:
        """Map ally display names to toon_handle for the map-tier ban query."""
        handles: list[str] = []
        for n in ally_names:
            if not n:
                continue
            rows = self.store.find_players_by_name(n)
            if rows:
                handles.append(rows[0]["toon_handle"])
        return handles

    def _run_analysis(self) -> None:
        # Drop any lingering read snapshot so we observe whatever the
        # watcher / cloud-sync threads have committed since the last
        # popup pass. Without this, a replay ingested between two
        # consecutive hotkey presses isn't reflected in the per-player
        # cards or BP recommendations until the app is restarted.
        self.store.drop_read_snapshot()
        # Squad-handles cache is also keyed on a fresh DB read — wipe
        # it here so a recently-ingested replay can promote a new
        # handle into the squad set on the next BP pass.
        self._squad_tuple_cache = None
        # Refresh the leaderboards once per analysis pass so the
        # highlight on each player card reflects the current DB state.
        # Top 30 of each board is a comfortable cutoff: anything below
        # that has too little signal to justify a "watch out" flag, and
        # the SQL is cheap enough we don't bother caching across runs.
        self._refresh_rank_indices()
        map_name = self.map_edit.currentText().strip() or None
        ally_names = [c.name for c in self._ally_cards]
        enemy_names = [c.name for c in self._enemy_cards]

        # Ban list from enemy names only.
        enemies_filled = [n for n in enemy_names if n]
        rank_index = getattr(self, "_ranked_by_handle", {}) or {}
        rank_total = getattr(self, "_ranked_total", 0) or 0
        squad_tuple = self._squad_handles_tuple()
        if enemies_filled:
            from ..bp import profile_opponents
            bans = recommend_bans(self.store, enemies_filled)
            profiles = profile_opponents(
                self.store,
                enemies_filled,
                rank_index=rank_index,
                rank_total=rank_total,
                squad_handles=squad_tuple,
            )
        else:
            bans = []
            profiles = []

        # Second ban section: heroes statistically strong on this map that
        # our squad rarely plays — even if no opponent has signal on them,
        # leaving them open is risky.
        map_tier_bans: list[MapTierBan] = []
        if map_name:
            squad_handles = self._resolve_squad_handles(ally_names)
            if squad_handles:
                map_tier_bans = recommend_map_strong_bans(
                    self.store, map_name, squad_handles
                )

        self.ban_panel.set_candidates(
            bans, profiles=profiles, map_tier=map_tier_bans
        )
        self._last_bans = list(bans)
        self._last_map_tier = list(map_tier_bans)

        # Pick list keyed on map.
        if map_name:
            picks = recommend_picks(self.store, map_name)
        else:
            picks = []
        self.pick_panel.set_candidates(picks)
        self._last_picks = list(picks)

        # Per-card lookups. Only query cards with names. Pass the
        # current map so each PlayerSummary gets a map_heroes list to
        # render alongside the all-maps signature.
        names = [n for n in (ally_names + enemy_names) if n]
        summaries = (
            lookup_players(self.store, names, map_name=map_name)
            if names else {}
        )
        for cards, side in (
            (self._ally_cards, "ally"),
            (self._enemy_cards, "enemy"),
        ):
            for card in cards:
                if not card.name:
                    card.clear()
                    continue
                slot_summaries = summaries.get(card.name, [])
                card.set_summaries(slot_summaries)
                self._apply_flag(card, slot_summaries, side=side)

    def _requery_single(self, card: _PlayerCard) -> None:
        name = card.name
        if not name:
            card.clear()
            self._run_analysis()  # picks/bans may change when a slot clears
            return
        # Same stale-snapshot concern as _run_analysis: a fresh ingest
        # may have happened on the watcher thread since the last query.
        self.store.drop_read_snapshot()
        map_name = self.map_edit.currentText().strip() or None
        summaries = (
            lookup_players(self.store, [name], map_name=map_name)
            .get(name, [])
        )
        card.set_summaries(summaries)
        # Reapply leaderboard flag — the user may have corrected an
        # OCR misread to a name that does (or doesn't) match a board.
        side = "ally" if card in self._ally_cards else "enemy"
        # If the rank index hasn't been built yet (e.g. user re-queried
        # a single card before opening the popup via the BP flow), do
        # it now so the flag check has data to work with.
        if not hasattr(self, "_ranked_by_handle"):
            self._refresh_rank_indices()
        self._apply_flag(card, summaries, side=side)
        # A single-card correction may shift the ban list (any side change
        # can affect map-tier bans through ally squad detection).
        self._refresh_bans()

    # --- copy brief to clipboard ---------------------------------------------

    def _card_briefs(self, cards: list["_PlayerCard"]) -> list[CardBrief]:
        """Snapshot each card's name + summaries + flag for the brief."""
        return [
            CardBrief(
                typed_name=c.name,
                summaries=list(c._summaries),
                flag_kind=c._flag_kind,
                flag_rank=c._flag_rank,
            )
            for c in cards
        ]

    # Heroes need at least this many games on the map before we list
    # them in the squad section — keeps a single 1/0 fluke off the brief.
    _SQUAD_MAP_HERO_MIN_GAMES = 2
    _SQUAD_MAP_TOP_N = 3

    def _squad_briefs(self, map_name: str | None) -> list[SquadMemberMapBrief]:
        """Squad's own track record on the current map, on this draft's
        ally cards.

        Reuses ``PlayerSummary.map_heroes`` that ``_run_analysis`` already
        loaded into each ally slot — no extra DB round-trips. Players
        with zero map history get skipped so the section stays scannable.
        """
        if not map_name:
            return []

        out: list[SquadMemberMapBrief] = []
        for card in self._ally_cards:
            for s in card._summaries:
                if not s.map_heroes:
                    continue
                # Filter out zero-sample heroes, then sort by winrate
                # with games as tiebreak so a 100% on 2 games still
                # tops 60% on 5 — what we want for "specialty hero on
                # this map".
                usages = [
                    h for h in s.map_heroes
                    if h.games >= self._SQUAD_MAP_HERO_MIN_GAMES
                ]
                usages.sort(key=lambda u: (-u.winrate, -u.games))
                total_games = sum(h.games for h in s.map_heroes)
                total_wins = sum(h.wins for h in s.map_heroes)
                out.append(
                    SquadMemberMapBrief(
                        display_name=s.display_name or card.name,
                        map_games=total_games,
                        map_wins=total_wins,
                        top_heroes=usages[: self._SQUAD_MAP_TOP_N],
                    )
                )
        return out

    def _copy_brief_to_clipboard(self) -> None:
        map_name = self.map_edit.currentText().strip() or None
        text = build_brief(
            map_name=map_name,
            bans=self._last_bans,
            map_tier_bans=self._last_map_tier,
            picks=self._last_picks,
            ally_cards=self._card_briefs(self._ally_cards),
            enemy_cards=self._card_briefs(self._enemy_cards),
            squad_map_briefs=self._squad_briefs(map_name),
        )
        QGuiApplication.clipboard().setText(text)
        # Flash the button to confirm. 1.5s is long enough to register
        # without lingering past the moment the user moves on.
        original = t("ui.popup.copy_btn")
        self.copy_btn.setText(t("ui.popup.copy_btn_done"))
        QTimer.singleShot(1500, lambda: self.copy_btn.setText(original))
