"""Per-match scoreboard for the desktop tool.

Shows both teams of one replay with hero, player, K/D/A, and the key
damage/healing columns. Opened by double-clicking a row in
:class:`MatchListDialog`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..db import Store
from ..i18n import t
from .theme import BG_DEEP, GOLD, GOOD, LINE, TEXT, TEXT_DIM, WARN


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value / 1000:.0f}k"
    if value >= 1_000:
        return f"{value / 1000:.1f}k"
    return f"{value:.0f}"


def _fmt_dur(seconds: int) -> str:
    m, s = divmod(int(seconds or 0), 60)
    return f"{m}:{s:02d}"


class MatchDetailDialog(QDialog):
    def __init__(self, store: Store, replay_id: int, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(t("ui.match_detail.window_title"))
        self.resize(900, 620)
        self.setStyleSheet(
            f"QDialog {{ background:{BG_DEEP}; color:{TEXT}; }}"
            f"QLabel {{ color:{TEXT}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        replay, players = store.match_detail(replay_id)

        self.title = QLabel()
        self.title.setStyleSheet(f"color:{GOLD}; font-size:13pt; font-weight:600;")
        root.addWidget(self.title)

        if replay is None:
            self.title.setText(t("ui.match_detail.not_found"))
            return

        winner = int(replay["winner_team"])
        self.title.setText(
            t(
                "ui.match_detail.header",
                map=replay["map_name"],
                mode=replay["mode"],
                dur=_fmt_dur(replay["duration_s"]),
                when=(replay["played_at"] or "").replace("T", " ")[:16],
            )
        )

        for team in (0, 1):
            team_rows = [p for p in players if int(p["team"]) == team]
            won = team == winner
            label = QLabel(
                t("ui.match_detail.team_won" if won else "ui.match_detail.team_lost",
                  team=team)
            )
            label.setStyleSheet(
                f"color:{GOOD if won else WARN}; font-weight:600; padding-top:6px;"
            )
            root.addWidget(label)
            root.addWidget(self._team_table(team_rows))

    def _team_table(self, rows: list) -> QTableWidget:
        cols = [
            "ui.match_detail.col_hero", "ui.match_detail.col_player",
            "ui.match_detail.col_kda", "ui.match_detail.col_hero_dmg",
            "ui.match_detail.col_siege", "ui.match_detail.col_healing",
            "ui.match_detail.col_taken", "ui.match_detail.col_xp",
        ]
        tbl = QTableWidget()
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.NoSelection)
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels([t(k) for k in cols])
        tbl.verticalHeader().setVisible(False)
        hdr = tbl.horizontalHeader()
        for i in range(len(cols)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        tbl.setRowCount(len(rows))
        # Keep tables compact: size to content rows.
        for i, p in enumerate(rows):
            kda = f"{int(p['kills'])}/{int(p['deaths'])}/{int(p['assists'])}"
            cells = [
                p["hero"],
                p["display_name"] or p["toon_handle"],
                kda,
                _fmt_k(float(p["hero_damage"] or 0)),
                _fmt_k(float(p["siege_damage"] or 0)),
                _fmt_k(float(p["healing"] or 0)),
                _fmt_k(float(p["damage_taken"] or 0)),
                _fmt_k(float(p["experience_contribution"] or 0)),
            ]
            for c, text in enumerate(cells):
                tbl.setItem(i, c, QTableWidgetItem(str(text)))
        tbl.setMaximumHeight(max(120, 30 * (len(rows) + 1)))
        return tbl
