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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
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
    PickCandidate,
    TalentPick,
    recommend_bans,
    recommend_picks,
)
from ..db import Store
from ..lookup import PlayerSummary, lookup_players


# --- formatting helpers -------------------------------------------------------


def _fmt_pct(wr: float) -> str:
    return f"{wr*100:.0f}%"


def _fmt_kda(k: float, d: float, a: float) -> str:
    return f"{k:.1f}/{d:.1f}/{a:.1f}"


def _fmt_k(value: float) -> str:
    """Render large counts compactly (43,123 → 43k; 510 → 510)."""
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


def _hero_line(h) -> str:
    """One line per hero. Shows games / WR / KDA + the most-relevant
    impact metrics. We keep all of damage / damage-taken / structure /
    healing / xp / cc; tanks tend to have low hero-damage and high taken,
    healers low damage and high healing — by showing them all the user
    can read role from the numbers."""
    return (
        f"<b>{h.hero}</b>  "
        f"<span style='color:#aaa;'>"
        f"{h.games}G {_fmt_pct(h.winrate)}  "
        f"K/D/A {_fmt_kda(h.avg_k, h.avg_d, h.avg_a)}<br>"
        f"&nbsp;&nbsp;&nbsp;&nbsp;"
        f"英伤 {_fmt_k(h.avg_hero_dmg)} · "
        f"承伤 {_fmt_k(h.avg_dmg_taken)} · "
        f"推塔 {_fmt_k(h.avg_structure_dmg)} · "
        f"治疗 {_fmt_k(h.avg_healing)} · "
        f"XP {_fmt_k(h.avg_xp)} · "
        f"控时 {h.avg_cc:.0f}s"
        f"</span>"
    )


def _summary_body_html(summary: PlayerSummary, expanded: bool) -> str:
    """Render the body of a player card based on a PlayerSummary."""
    if summary.note:
        return f'<span style="color:#b77;">{summary.note}</span>'

    k, d, a = summary.overall_kda
    parts: list[str] = []
    parts.append(
        f"<b>{summary.total_games}</b> games &nbsp; "
        f"<b>{_fmt_pct(summary.winrate)}</b> WR &nbsp; "
        f"K/D/A {_fmt_kda(k, d, a)}"
    )
    # Career averages line — gives a quick tell on whether the player is
    # a damage dealer, tank, healer, or pusher even before reading hero list.
    if summary.total_games:
        parts.append(
            "<span style='color:#9ad;'>"
            f"avg 英伤 {_fmt_k(summary.avg_hero_dmg)} · "
            f"承伤 {_fmt_k(summary.avg_dmg_taken)} · "
            f"治疗 {_fmt_k(summary.avg_healing)} · "
            f"XP {_fmt_k(summary.avg_xp)} · "
            f"控时 {summary.avg_cc:.0f}s"
            "</span>"
        )

    heroes = summary.signature_heroes
    if not heroes:
        parts.append("<span style='color:#888;'>no hero usage in Storm League yet</span>")
        return "<br>".join(parts)

    shown = heroes if expanded else heroes[:3]
    parts.append("<u>Heroes used</u>")
    for h in shown:
        parts.append("&nbsp;&nbsp;• " + _hero_line(h))
    remaining = len(heroes) - len(shown)
    if not expanded and remaining > 0:
        parts.append(
            f"<span style='color:#888;'>&nbsp;&nbsp;(+{remaining} more heroes — click ▼ to expand)</span>"
        )
    return "<br>".join(parts)


# --- player card ------------------------------------------------------------


class _PlayerCard(QFrame):
    """A single player slot; emits a signal when the user wants to re-query."""

    refresh_requested = Signal()

    def __init__(self, *, accent: str) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: rgba(28,28,28,230); color: #eee; border-radius: 8px; }"
            "QLineEdit { background: #1b1b1b; color: #eee; border: 1px solid #444; "
            "            padding: 3px 6px; font-size: 11pt; }"
            "QLineEdit:focus { border: 1px solid #8cf; }"
            f"QLabel#title {{ color: {accent}; font-weight: 600; }}"
            "QPushButton { background: #224; color: #def; border: 1px solid #446; "
            "             padding: 1px 8px; border-radius: 4px; font-size: 9pt; }"
            "QPushButton:hover { background: #336; }"
        )
        self._expanded = False
        self._summaries: list[PlayerSummary] = []

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)

        # Name row: editable field + refresh button
        row = QHBoxLayout()
        row.setSpacing(4)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("player name")
        self.name_edit.returnPressed.connect(self.refresh_requested)
        row.addWidget(self.name_edit, 1)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setToolTip("Re-query this player")
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.clicked.connect(self.refresh_requested)
        row.addWidget(self.refresh_btn)

        self.expand_btn = QPushButton("▼")
        self.expand_btn.setToolTip("Show all heroes")
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
        # Visual hint for low-confidence OCR. Anything below 0.7 gets a yellow
        # border so the user knows to double-check.
        if name and confidence > 0 and confidence < 0.7:
            self.name_edit.setStyleSheet(
                "background: #1b1b1b; color: #fd6; "
                "border: 2px solid #d90; padding: 2px 5px; font-size: 11pt;"
            )
            self.name_edit.setToolTip(
                f"OCR confidence {confidence*100:.0f}% — please double-check"
            )
        else:
            self.name_edit.setStyleSheet("")  # fall back to the frame's style
            self.name_edit.setToolTip("")

    def set_summaries(self, summaries: list[PlayerSummary]) -> None:
        self._summaries = summaries
        self._render()

    def clear(self) -> None:
        self._summaries = []
        self.resolved.setVisible(False)
        self.body.setText("")

    def _toggle_expanded(self, checked: bool) -> None:
        self._expanded = checked
        self.expand_btn.setText("▲" if checked else "▼")
        self._render()

    def _render(self) -> None:
        if not self._summaries:
            self.body.setText("<i style='color:#888;'>no data</i>")
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
            self.resolved.setText(f"(found as: {self._summaries[0].display_name})")
        else:
            self.resolved.setText("")


# --- ban/pick lists ---------------------------------------------------------


class _BanList(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: rgba(40,18,18,235); color: #fdd; "
            "         border-radius: 8px; border: 1px solid #633; }"
            "QLabel { color: #fdd; }"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        title = QLabel("🚫 Ban suggestions "
                       "<span style='color:#b88; font-weight: normal;'>from enemy history</span>")
        title.setTextFormat(Qt.RichText)
        title.setFont(QFont("", 11, QFont.Bold))
        v.addWidget(title)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        v.addWidget(self.body)

    def set_candidates(self, cands: list[BanCandidate]) -> None:
        if not cands:
            self.body.setText("<i style='color:#a88;'>no threat data for these opponents yet</i>")
            return
        lines: list[str] = []
        for c in cands:
            contrib = "  ·  ".join(
                f"{name} <span style='color:#daa;'>{w}/{g} ({(w/g*100 if g else 0):.0f}%)</span>"
                for name, g, w, _ in c.contributors
            )
            lines.append(
                f"<b>{c.hero}</b> "
                f"<span style='color:#caa;'>score {c.score:.2f} · "
                f"combined {c.total_wins}/{c.total_games} ({c.combined_wr*100:.0f}%)</span>"
                f"<br>&nbsp;&nbsp;&nbsp;&nbsp;{contrib}"
            )
        self.body.setText("<br>".join(lines))


class _PickList(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: rgba(18,34,22,235); color: #dfd; "
            "         border-radius: 8px; border: 1px solid #363; }"
            "QLabel { color: #dfd; }"
            "QPushButton { background: #224; color: #def; border: 1px solid #446; "
            "             padding: 2px 8px; border-radius: 4px; }"
            "QPushButton:checked { background: #445; }"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        title = QLabel("✅ Pick suggestions "
                       "<span style='color:#9b9; font-weight: normal;'>strong on this map</span>")
        title.setTextFormat(Qt.RichText)
        title.setFont(QFont("", 11, QFont.Bold))
        v.addWidget(title)
        self._rows = QWidget()
        self._rows_layout = QVBoxLayout(self._rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._rows)

    def set_candidates(self, cands: list[PickCandidate]) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not cands:
            empty = QLabel("<i style='color:#9a9;'>no significantly strong picks on this map yet</i>")
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
        header = QLabel(
            f"<b>{sig}{c.hero}</b> "
            f"<span style='color:#9b9;'>{c.map_wins}/{c.map_games} "
            f"WR {c.map_winrate*100:.0f}% · WLB {c.map_wilson_lb*100:.0f}% · "
            f"lift {c.lift_pp:+.0f}pp · p={c.p_value:.2f}</span>"
        )
        header.setTextFormat(Qt.RichText)
        head.addWidget(header)
        head.addStretch(1)

        toggle = QPushButton("Build")
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
            return "<i style='color:#9a9;'>no talent data</i>"
        return "<br>".join(
            f"<b>T{tp.tier}</b> {tp.talent} "
            f"<span style='color:#9b9;'>{tp.wins}/{tp.games} "
            f"WR {(tp.wins/tp.games*100 if tp.games else 0):.0f}% · "
            f"WLB {tp.wilson_lb*100:.0f}% · pick {tp.pick_rate*100:.0f}%</span>"
            for tp in picks
        )


# --- main popup -------------------------------------------------------------


class PopupWindow(QWidget):
    """Floating, always-on-top pre-game scout window."""

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.setWindowTitle("HotS Helper — Pre-game scout")
        self.setWindowFlags(
            Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet("background: rgba(20,20,20,240); color: #eee;")
        self._drag_pos = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        # --- header ---------------------------------------------------------
        header = QHBoxLayout()
        self.title = QLabel("Pre-game scout")
        self.title.setStyleSheet("font-size: 14pt; font-weight: 600;")
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(QLabel("Map:"))
        self.map_edit = QLineEdit()
        self.map_edit.setPlaceholderText("e.g. 奥特兰克战道")
        self.map_edit.setFixedWidth(180)
        self.map_edit.returnPressed.connect(self._run_analysis)
        header.addWidget(self.map_edit)
        analyze_btn = QPushButton("Analyze all")
        analyze_btn.clicked.connect(self._run_analysis)
        header.addWidget(analyze_btn)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.hide)
        close_btn.setStyleSheet(
            "background:#552; color:#eee; border-radius:14px; font-weight:bold;"
        )
        header.addWidget(close_btn)
        root.addLayout(header)

        # --- scrollable body ------------------------------------------------
        scroll = QScrollArea()
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
        self._ally_cards = self._make_column(players_row, "Allies (your team)", "#8cf")
        self._enemy_cards = self._make_column(players_row, "Enemies", "#f88")
        body.addLayout(players_row)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        footer = QLabel(
            "<span style='color:#888;'>"
            "Names come from OCR — edit any slot + press Enter or ↻ to re-query. "
            "▼ expands a slot to every hero the player has used. "
            "Storm League data only."
            "</span>"
        )
        footer.setTextFormat(Qt.RichText)
        footer.setWordWrap(True)
        root.addWidget(footer)

        self.resize(1200, 840)

    def _make_column(self, parent_layout: QHBoxLayout, title: str, accent: str) -> list[_PlayerCard]:
        col = QFrame()
        col.setStyleSheet(
            "QFrame { background: rgba(24,24,24,180); border-radius: 6px; "
            f"border: 1px solid {accent}; }}"
        )
        v = QVBoxLayout(col)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(8)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {accent}; font-weight: 600; font-size: 11pt;")
        v.addWidget(lbl)
        cards: list[_PlayerCard] = []
        for _ in range(5):
            card = _PlayerCard(accent=accent)
            card.refresh_requested.connect(
                lambda c=card: self._requery_single(c)
            )
            cards.append(card)
            v.addWidget(card)
        parent_layout.addWidget(col)
        return cards

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
    ) -> None:
        if map_name is not None:
            self.map_edit.setText(map_name)
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
        if drafter:
            self.title.setText(f"Pre-game scout — drafting: {drafter}")
        else:
            self.title.setText("Pre-game scout")
        self.show()
        self.raise_()
        if map_name or ally_names or enemy_names:
            self._run_analysis()

    # --- frameless drag -------------------------------------------------------

    def mousePressEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.button() == Qt.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        if ev.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)
            ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # type: ignore[no-untyped-def]
        self._drag_pos = None

    # --- analysis -------------------------------------------------------------

    def _run_analysis(self) -> None:
        map_name = self.map_edit.text().strip() or None
        ally_names = [c.name for c in self._ally_cards]
        enemy_names = [c.name for c in self._enemy_cards]

        # Ban list from enemy names only.
        enemies_filled = [n for n in enemy_names if n]
        if enemies_filled:
            bans = recommend_bans(self.store, enemies_filled)
        else:
            bans = []
        self.ban_panel.set_candidates(bans)

        # Pick list keyed on map.
        if map_name:
            picks = recommend_picks(self.store, map_name)
        else:
            picks = []
        self.pick_panel.set_candidates(picks)

        # Per-card lookups. Only query cards with names.
        names = [n for n in (ally_names + enemy_names) if n]
        summaries = lookup_players(self.store, names) if names else {}
        for cards in (self._ally_cards, self._enemy_cards):
            for card in cards:
                if not card.name:
                    card.clear()
                    continue
                card.set_summaries(summaries.get(card.name, []))

    def _requery_single(self, card: _PlayerCard) -> None:
        name = card.name
        if not name:
            card.clear()
            self._run_analysis()  # picks/bans may change when a slot clears
            return
        summaries = lookup_players(self.store, [name]).get(name, [])
        card.set_summaries(summaries)
        # A single-card correction may shift the enemy ban list too.
        if card in self._enemy_cards:
            enemies = [c.name for c in self._enemy_cards if c.name]
            self.ban_panel.set_candidates(
                recommend_bans(self.store, enemies) if enemies else []
            )
