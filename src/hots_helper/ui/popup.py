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
    Signal,
)
from PySide6.QtGui import QFont, QGuiApplication
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
from ..talent_names import talent_label


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

    heroes = summary.signature_heroes
    if not heroes:
        parts.append(t("ui.popup.card.no_hero_usage"))
        return "<br>".join(parts)

    shown = heroes if expanded else heroes[:3]
    parts.append(f"<u>{t('ui.popup.card.heroes_used')}</u>")
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
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.RichText)
        self.title_label.setFont(QFont("", 11, QFont.Bold))
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
        head_lines.append(f"<u style='color:#fbb;'>{t('ui.popup.ban_section_history')}</u>")
        if cands:
            for c in cands:
                contrib = "  ·  ".join(
                    f"{name} <span style='color:#daa;'>{w}/{g} ({(w/g*100 if g else 0):.0f}%)</span>"
                    for name, g, w, _ in c.contributors
                )
                head_lines.append(
                    f"<b>{c.hero}</b> "
                    f"<span style='color:#caa;'>score {c.score:.2f} · "
                    f"combined {c.total_wins}/{c.total_games} ({c.combined_wr*100:.0f}%)</span>"
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
                    f"<span style='color:#caa;'>{c.map_wins}/{c.map_games} "
                    f"WR {c.map_winrate*100:.0f}% · WLB {c.map_wilson_lb*100:.0f}% · "
                    f"{squad_note}</span>"
                )
        self.body.setText("<br>".join(head_lines))


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
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.RichText)
        self.title_label.setFont(QFont("", 11, QFont.Bold))
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
        header = QLabel(
            f"<b>{sig}{c.hero}</b> "
            f"<span style='color:#9b9;'>{c.map_wins}/{c.map_games} "
            f"WR {c.map_winrate*100:.0f}% · WLB {c.map_wilson_lb*100:.0f}% · "
            f"lift {c.lift_pp:+.0f}pp · p={c.p_value:.2f}</span>"
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
        # Qt.Tool keeps it off the taskbar / Alt-Tab; the StaysOnTop +
        # ShowWithoutActivating combo lets the popup float over a
        # fullscreen game without ever stealing focus, which is what
        # would force the game to exit fullscreen / minimise.
        self.setWindowFlags(
            Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.NoDropShadowWindowHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        # Belt-and-suspenders: even if Qt accidentally tries to focus us,
        # Qt::NoFocus on the top-level prevents Windows from waking
        # exclusive-fullscreen apps and minimising them.
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("background: rgba(20,20,20,240); color: #eee;")
        self._drag_pos = None
        self._screenshot_path = None
        self._current_drafter = ""

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
        self.title.setStyleSheet("font-size: 14pt; font-weight: 600;")
        header.addWidget(self.title)
        header.addStretch(1)
        self.map_label = QLabel()
        header.addWidget(self.map_label)
        self.map_edit = QLineEdit()
        self.map_edit.setFixedWidth(180)
        self.map_edit.returnPressed.connect(self._run_analysis)
        header.addWidget(self.map_edit)
        self.analyze_btn = QPushButton()
        self.analyze_btn.clicked.connect(self._run_analysis)
        header.addWidget(self.analyze_btn)

        # Minimise → pill button. Sits just before the close button so it
        # gets the same visual weight without pushing other controls
        # around.
        self.minimize_btn = QPushButton("–")
        self.minimize_btn.setFixedSize(28, 28)
        self.minimize_btn.setToolTip(t("ui.popup.minimize_tip"))
        self.minimize_btn.clicked.connect(self._collapse_to_pill)
        self.minimize_btn.setStyleSheet(
            "background:#225; color:#eee; border-radius:14px; "
            "font-weight:bold; font-size: 14pt;"
        )
        header.addWidget(self.minimize_btn)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.hide)
        close_btn.setStyleSheet(
            "background:#552; color:#eee; border-radius:14px; font-weight:bold;"
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
        self.map_edit.setPlaceholderText(t("ui.popup.map_placeholder"))
        self.analyze_btn.setText(t("ui.popup.analyze"))
        # Side column titles
        if hasattr(self, "_ally_col"):
            self._ally_col.title_label.setText(t("ui.popup.allies"))
            self._enemy_col.title_label.setText(t("ui.popup.enemies"))
        self.footer_label.setText(f"<span style='color:#888;'>{t('ui.popup.footer')}</span>")

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
            self.minimize_btn,
        ):
            btn.hide()
        self.title.setStyleSheet(
            "font-size: 9pt; font-weight: 600; color:#eee; padding:0 6px;"
        )

        target = self._pill_target_geometry()
        self._geom_anim.stop()
        self._geom_anim.setStartValue(self.geometry())
        self._geom_anim.setEndValue(target)
        self._geom_anim.start()
        self.setStyleSheet(
            "background: rgba(20,20,28,235); color:#eee; border-radius:14px;"
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
            self.minimize_btn,
        ):
            btn.show()
        self.title.setStyleSheet("font-size: 14pt; font-weight: 600;")
        self.setStyleSheet("background: rgba(20,20,20,240); color: #eee;")

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
        try:
            dlg = RegionSelectorDialog(self._screenshot_path, parent=self)
        except Exception as e:
            QMessageBox.warning(self, t("ui.popup.region.cannot_open"), str(e))
            return

        chosen: dict = {}

        def _on_picked(x: int, y: int, w: int, h: int) -> None:
            chosen["bbox"] = (x, y, w, h)

        dlg.region_picked.connect(_on_picked)
        # PySide6 uses QDialog.DialogCode.Accepted (== 1). exec() returns
        # an int, so compare against the int directly to avoid PySide6/PyQt
        # version drift.
        if dlg.exec() != 1 or "bbox" not in chosen:
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

    def _refresh_bans(self) -> None:
        """Re-run the two ban analyses (opponent-history + map-tier)."""
        ally_names = [c.name for c in self._ally_cards if c.name]
        enemy_names = [c.name for c in self._enemy_cards if c.name]
        map_name = self.map_edit.text().strip() or None

        if enemy_names:
            from ..bp import profile_opponents
            bans = recommend_bans(self.store, enemy_names)
            profiles = profile_opponents(self.store, enemy_names)
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
        map_name = self.map_edit.text().strip() or None
        ally_names = [c.name for c in self._ally_cards]
        enemy_names = [c.name for c in self._enemy_cards]

        # Ban list from enemy names only.
        enemies_filled = [n for n in enemy_names if n]
        if enemies_filled:
            from ..bp import profile_opponents
            bans = recommend_bans(self.store, enemies_filled)
            profiles = profile_opponents(self.store, enemies_filled)
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
        # A single-card correction may shift the ban list (any side change
        # can affect map-tier bans through ally squad detection).
        self._refresh_bans()
