"""Modal dialog that renders the squad's weekly report.

Reads pre-computed :class:`WeeklyReport` data from
:func:`hots_helper.weekly_report.build_weekly_report` and shows each
section in its own block. A single "📋 复制周报" button at the top
copies the same digest as plain text to the clipboard so squad
members can paste it into Discord / WeChat / Slack.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..db import Store
from ..i18n import t
from ..weekly_report import (
    HeroPickStat,
    HighlightMatch,
    MapStat,
    MvpAward,
    PlayerWeekStats,
    StreakRun,
    WeeklyReport,
    build_weekly_report,
    format_weekly_brief,
)
from .theme import (
    BG_DEEP,
    BG_ELEVATED,
    BG_INPUT,
    GOLD,
    GOLD_BRIGHT,
    GOLD_DIM,
    LINE,
    TEXT,
    TEXT_DIM,
)


# --- formatting helpers (mirror weekly_report._fmt_*; small enough to dup) --


def _fmt_pct(x: float) -> str:
    return f"{x*100:.0f}%"


def _fmt_kda_avg(k: float, d: float, a: float) -> str:
    return f"{k:.1f}/{d:.1f}/{a:.1f}"


def _fmt_kda_int(k: int, d: int, a: int) -> str:
    return f"{k}/{d}/{a}"


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


def _fmt_date(iso: str) -> str:
    return iso[5:10] if iso and len(iso) >= 10 else (iso or "")


def _award_value(label_key: str, value: float) -> str:
    if label_key.endswith("god_kda"):
        return f"{value:.2f}"
    return _fmt_k(value)


# --- section widgets --------------------------------------------------------


def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Common framed section. Returns (frame, body_layout)."""
    frame = QFrame()
    frame.setStyleSheet(
        f"QFrame {{ background: {BG_ELEVATED}; color: {TEXT};"
        f" border: 1px solid {LINE}; border-left: 3px solid {GOLD};"
        f" border-radius: 8px; }}"
        f"QLabel {{ color: {TEXT}; }}"
    )
    v = QVBoxLayout(frame)
    v.setContentsMargins(12, 10, 12, 10)
    v.setSpacing(4)
    head = QLabel(f"<span style='color:{GOLD}; font-weight:600;'>{title}</span>")
    head.setTextFormat(Qt.RichText)
    v.addWidget(head)
    return frame, v


def _line(text: str, color: str = TEXT) -> QLabel:
    lbl = QLabel(f"<span style='color:{color};'>{text}</span>")
    lbl.setTextFormat(Qt.RichText)
    lbl.setWordWrap(True)
    return lbl


# --- the dialog --------------------------------------------------------------


class WeeklyReportDialog(QDialog):
    """Read-only modal showing the squad's last-N-days digest.

    The dialog is a thin renderer over :class:`WeeklyReport`; the only
    interactive control is the "copy to clipboard" button. Layouts are
    rebuilt from scratch on each open so a fresh ingest is reflected
    when the user reopens.
    """

    def __init__(self, store: Store, *, days: int = 7, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.days = days
        self.setWindowTitle(t("ui.weekly.dialog_title"))
        self.resize(720, 720)
        self.setStyleSheet(
            f"QDialog {{ background: {BG_DEEP}; color: {TEXT}; }}"
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; padding:5px 12px;"
            f" border-radius:4px; }}"
            f"QPushButton:hover {{ border-color:{GOLD_DIM};"
            f" color:{GOLD_BRIGHT}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # --- header bar with copy button -----------------------------------
        head = QHBoxLayout()
        self.title_label = QLabel("")
        self.title_label.setStyleSheet(
            f"color:{GOLD}; font-size:14pt; font-weight:600;"
        )
        head.addWidget(self.title_label, 1)
        self.copy_btn = QPushButton(t("ui.weekly.copy_btn"))
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        head.addWidget(self.copy_btn)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet(
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:14px;"
            f" font-weight:bold; padding:0; }}"
            f"QPushButton:hover {{ color:#e08585;"
            f" border-color:#e08585; background:{BG_DEEP}; }}"
        )
        head.addWidget(close_btn)
        root.addLayout(head)

        # --- scrollable body ----------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body_host = QWidget()
        self._body = QVBoxLayout(body_host)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(10)
        scroll.setWidget(body_host)
        root.addWidget(scroll, 1)

        self._report: WeeklyReport | None = None
        self._reload()

    # --- data + render -------------------------------------------------------

    def _reload(self) -> None:
        # Drop the read snapshot so a recent ingest is visible.
        try:
            self.store.drop_read_snapshot()
        except Exception:
            pass

        self._report = build_weekly_report(self.store, days=self.days)
        self._render(self._report)

    def _clear_body(self) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render(self, report: WeeklyReport) -> None:
        self._clear_body()
        self.title_label.setText(t("ui.weekly.title", days=report.overview.current.days))

        # Empty-state shortcut.
        if report.overview.current.games == 0:
            empty = QLabel(t("ui.weekly.empty", days=report.overview.current.days))
            empty.setTextFormat(Qt.RichText)
            empty.setWordWrap(True)
            self._body.addWidget(empty)
            self._body.addStretch(1)
            return

        # Window line under the title — small, dim.
        win = report.overview.current
        sub = QLabel(
            f"<span style='color:{TEXT_DIM};'>"
            + t(
                "ui.weekly.window_line",
                start=_fmt_date(win.start_iso),
                end=_fmt_date(win.end_iso),
            )
            + "</span>"
        )
        sub.setTextFormat(Qt.RichText)
        self._body.addWidget(sub)

        self._render_overview(report)
        if report.players:
            self._render_players(report.players)
        if report.awards:
            self._render_awards(report.awards)
        if report.highlights:
            self._render_highlights(report.highlights)
        if report.hero_top_picked or report.hero_top_winrate:
            self._render_heroes(report.hero_top_picked, report.hero_top_winrate)
        if report.maps:
            self._render_maps(report.maps)
        self._render_streaks(report.longest_win_streak, report.longest_loss_streak)
        self._body.addStretch(1)

    # --- per-section renderers ----------------------------------------------

    def _render_overview(self, report: WeeklyReport) -> None:
        frame, v = _section(t("ui.weekly.section.overview"))
        cur, prev = report.overview.current, report.overview.previous
        v.addWidget(_line(t(
            "ui.weekly.overview_line",
            games=cur.games, wr=_fmt_pct(cur.winrate),
            prev_games=prev.games, prev_wr=_fmt_pct(prev.winrate),
        )))
        delta_color = (
            "#7c7" if report.overview.winrate_delta_pp > 0
            else "#c77" if report.overview.winrate_delta_pp < 0
            else TEXT_DIM
        )
        v.addWidget(_line(
            t(
                "ui.weekly.overview_delta",
                games_delta=report.overview.games_delta,
                wr_delta=report.overview.winrate_delta_pp,
            ),
            color=delta_color,
        ))
        self._body.addWidget(frame)

    def _render_players(self, players: list[PlayerWeekStats]) -> None:
        frame, v = _section(t("ui.weekly.section.players"))
        for p in players:
            kda = _fmt_kda_avg(p.avg_k, p.avg_d, p.avg_a)
            if p.most_played_hero:
                line = t(
                    "ui.weekly.player_line",
                    name=f"<b>{p.display_name}</b>",
                    games=p.games, wr=_fmt_pct(p.winrate),
                    kda=kda,
                    hero=p.most_played_hero,
                    hero_wins=p.most_played_hero_wins,
                    hero_games=p.most_played_hero_games,
                )
            else:
                line = t(
                    "ui.weekly.player_line_no_hero",
                    name=f"<b>{p.display_name}</b>",
                    games=p.games, wr=_fmt_pct(p.winrate),
                    kda=kda,
                )
            v.addWidget(_line(line))
        self._body.addWidget(frame)

    def _render_awards(self, awards: list[MvpAward]) -> None:
        frame, v = _section(t("ui.weekly.section.awards"))
        for a in awards:
            v.addWidget(_line(
                t(
                    "ui.weekly.award_line",
                    label=f"<span style='color:{GOLD_BRIGHT};'>{t(a.label_key)}</span>",
                    name=f"<b>{a.display_name}</b>",
                    hero=a.hero or "?",
                    value=_award_value(a.label_key, a.value),
                    games=a.games,
                )
            ))
        self._body.addWidget(frame)

    def _render_highlights(self, highlights: list[HighlightMatch]) -> None:
        frame, v = _section(t("ui.weekly.section.highlights"))
        for h in highlights:
            result_word = t(
                "ui.weekly.match_won" if h.result == 1
                else "ui.weekly.match_lost"
            )
            result_color = "#7c7" if h.result == 1 else "#c77"
            v.addWidget(_line(
                t(
                    "ui.weekly.highlight_line",
                    when=_fmt_date(h.played_at),
                    name=f"<b>{h.display_name or '?'}</b>",
                    hero=h.hero,
                    map=h.map_name or "?",
                    result=f"<span style='color:{result_color};'>{result_word}</span>",
                    kda=_fmt_kda_int(h.kills, h.deaths, h.assists),
                    hd=_fmt_k(h.hero_damage),
                )
            ))
        self._body.addWidget(frame)

    def _render_heroes(
        self,
        top_picked: list[HeroPickStat],
        top_wr: list[HeroPickStat],
    ) -> None:
        frame, v = _section(t("ui.weekly.section.heroes"))
        if top_picked:
            chips = " · ".join(
                t("ui.weekly.hero_chip",
                  hero=h.hero, wins=h.wins, games=h.games,
                  wr=_fmt_pct(h.winrate))
                for h in top_picked
            )
            v.addWidget(_line(
                f"<b>{t('ui.weekly.heroes_top_picked')}</b> {chips}"
            ))
        if top_wr:
            chips = " · ".join(
                t("ui.weekly.hero_chip",
                  hero=h.hero, wins=h.wins, games=h.games,
                  wr=_fmt_pct(h.winrate))
                for h in top_wr
            )
            v.addWidget(_line(
                f"<b>{t('ui.weekly.heroes_top_wr')}</b> {chips}"
            ))
        self._body.addWidget(frame)

    def _render_maps(self, maps: list[MapStat]) -> None:
        frame, v = _section(t("ui.weekly.section.maps"))
        for m in maps:
            v.addWidget(_line(
                t(
                    "ui.weekly.map_line",
                    map=f"<b>{m.map_name or '?'}</b>",
                    wins=m.wins, games=m.games,
                    wr=_fmt_pct(m.winrate),
                )
            ))
        self._body.addWidget(frame)

    def _render_streaks(
        self,
        win_streak: StreakRun,
        loss_streak: StreakRun,
    ) -> None:
        frame, v = _section(t("ui.weekly.section.streaks"))
        if win_streak.is_empty:
            v.addWidget(_line(t("ui.weekly.streak_none_win"), color=TEXT_DIM))
        else:
            v.addWidget(_line(
                t(
                    "ui.weekly.streak_win",
                    n=win_streak.length,
                    start=_fmt_date(win_streak.started_at),
                    end=_fmt_date(win_streak.ended_at),
                ),
                color="#7c7",
            ))
        if loss_streak.is_empty:
            v.addWidget(_line(t("ui.weekly.streak_none_loss"), color=TEXT_DIM))
        else:
            v.addWidget(_line(
                t(
                    "ui.weekly.streak_loss",
                    n=loss_streak.length,
                    start=_fmt_date(loss_streak.started_at),
                    end=_fmt_date(loss_streak.ended_at),
                ),
                color="#c77",
            ))
        self._body.addWidget(frame)

    # --- copy --------------------------------------------------------------

    def _copy_to_clipboard(self) -> None:
        if self._report is None:
            return
        QGuiApplication.clipboard().setText(format_weekly_brief(self._report))
        original = t("ui.weekly.copy_btn")
        self.copy_btn.setText(t("ui.weekly.copy_btn_done"))
        QTimer.singleShot(1500, lambda: self.copy_btn.setText(original))
