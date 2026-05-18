"""Player rank leaderboards.

A single table with a dropdown that switches between four views:
* 🪦 最坑队友  (worst WR while playing on our side)
* 🤝 最强队友  (highest WR while playing on our side)
* 👑 最强对手  (highest WR while playing against us)
* 🎯 最弱对手  (lowest WR while playing against us)

The "side" (teammate vs opponent) is determined per match by which
team the squad's heuristic-detected handles were on. The board you
pick controls how those per-side records are sorted (Wilson 95% lower
bound on win-rate, ascending or descending).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..db import Store
from ..i18n import on_change as on_lang_change, t
from ..player_rank import (
    ALL_BOARDS,
    ALL_SORTS,
    BOARD_BEST_OPPONENT,
    BOARD_BEST_TEAMMATE,
    BOARD_WORST_OPPONENT,
    BOARD_WORST_TEAMMATE,
    PlayerRankRow,
    SORT_POWER,
    SORT_WLB,
    compute_board,
)


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


# Maps each board id → ("title i18n key", row tint colour for top-3).
_BOARD_META: dict[str, tuple[str, QColor]] = {
    BOARD_WORST_TEAMMATE: ("ui.rank.board_worst_teammate", QColor(230, 110, 110)),
    BOARD_BEST_TEAMMATE:  ("ui.rank.board_best_teammate",  QColor(120, 220, 120)),
    BOARD_BEST_OPPONENT:  ("ui.rank.board_best_opponent",  QColor(255, 200, 100)),
    BOARD_WORST_OPPONENT: ("ui.rank.board_worst_opponent", QColor(140, 200, 230)),
}


class PlayerRankDialog(QDialog):
    """Single-table dialog with a board-selector dropdown."""

    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        # Single table — narrower than the side-by-side layout used to
        # be, but the name column is comfortable now.
        self.setMinimumSize(900, 620)
        self.resize(1100, 720)
        self._build_ui()
        self._retranslate()
        on_lang_change(lambda _c: self._on_lang())
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setFont(QFont("", 14, QFont.Bold))
        self.title.setProperty("role", "title")
        head.addWidget(self.title)
        head.addStretch(1)

        self.board_label = QLabel()
        head.addWidget(self.board_label)
        self.board_combo = QComboBox()
        for board in ALL_BOARDS:
            self.board_combo.addItem("", board)
        self.board_combo.currentIndexChanged.connect(lambda _i: self._reload())
        head.addWidget(self.board_combo)

        # Sort mode — Wilson lower bound (default) or composite combat
        # power. Same for every board — the dialog flips the direction
        # internally based on whether the board is a "worst" or "best"
        # board.
        self.sort_label = QLabel()
        head.addWidget(self.sort_label)
        self.sort_combo = QComboBox()
        for sort in ALL_SORTS:
            self.sort_combo.addItem("", sort)
        self.sort_combo.currentIndexChanged.connect(lambda _i: self._reload())
        head.addWidget(self.sort_combo)

        self.min_games_label = QLabel()
        head.addWidget(self.min_games_label)
        self.min_games_spin = QSpinBox()
        self.min_games_spin.setRange(2, 100)
        self.min_games_spin.setValue(5)
        self.min_games_spin.valueChanged.connect(self._reload)
        head.addWidget(self.min_games_spin)

        self.limit_label = QLabel()
        head.addWidget(self.limit_label)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(5, 100)
        self.limit_spin.setValue(20)
        self.limit_spin.valueChanged.connect(self._reload)
        head.addWidget(self.limit_spin)

        self.close_btn = QPushButton()
        self.close_btn.clicked.connect(self.close)
        head.addWidget(self.close_btn)
        root.addLayout(head)

        self.summary = QLabel("")
        self.summary.setStyleSheet("padding: 4px 0; color: #b8c7d9;")
        root.addWidget(self.summary)

        # Single table.
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self._col_keys = [
            "ui.rank.col_rank", "ui.rank.col_name",
            "ui.rank.col_power",
            "ui.rank.col_games", "ui.rank.col_wins",
            "ui.rank.col_wr", "ui.rank.col_wlb",
            "ui.rank.col_kda", "ui.rank.col_hero_dmg",
            "ui.rank.col_struct", "ui.rank.col_healing",
            "ui.rank.col_soak", "ui.rank.col_xp",
        ]
        self.table.setColumnCount(len(self._col_keys))
        for i in range(len(self._col_keys)):
            self.table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        # Name column stretches; the other columns hug content.
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self.table.horizontalHeader().setMinimumSectionSize(40)
        self.table.setColumnWidth(1, 260)
        root.addWidget(self.table, 1)

        self.footer_label = QLabel()
        self.footer_label.setTextFormat(Qt.RichText)
        self.footer_label.setWordWrap(True)
        root.addWidget(self.footer_label)

    def _retranslate(self) -> None:
        self.setWindowTitle(t("ui.rank.window_title"))
        self.title.setText(t("ui.rank.title"))
        self.board_label.setText(t("ui.rank.board"))
        for i in range(self.board_combo.count()):
            board = self.board_combo.itemData(i)
            key, _tone = _BOARD_META.get(
                board, ("ui.rank.board_worst_teammate", QColor(220, 220, 220))
            )
            self.board_combo.setItemText(i, t(key))
        self.sort_label.setText(t("ui.rank.sort"))
        sort_keys = {
            SORT_WLB:   "ui.rank.sort_wlb",
            SORT_POWER: "ui.rank.sort_power",
        }
        for i in range(self.sort_combo.count()):
            key = sort_keys.get(self.sort_combo.itemData(i), "")
            if key:
                self.sort_combo.setItemText(i, t(key))
        self.sort_combo.setToolTip(t("ui.rank.sort_tip"))
        self.min_games_label.setText(t("ui.aram.min_games"))
        self.limit_label.setText(t("ui.rank.limit_label"))
        self.close_btn.setText(t("ui.aram.close"))
        self.table.setHorizontalHeaderLabels([t(k) for k in self._col_keys])
        self.footer_label.setText(t("ui.rank.footer"))

    def _on_lang(self) -> None:
        self._retranslate()
        self._reload()

    def _reload(self) -> None:
        try:
            self._reload_inner()
        except Exception as e:
            import traceback
            self.summary.setText(
                f"<span style='color:#e08585;'>"
                f"加载失败：{type(e).__name__}: {e}</span>"
            )
            traceback.print_exc()

    def _reload_inner(self) -> None:
        board = self.board_combo.currentData() or BOARD_WORST_TEAMMATE
        sort_mode = self.sort_combo.currentData() or SORT_WLB
        min_games = self.min_games_spin.value()
        limit = self.limit_spin.value()

        rows = compute_board(
            self.store,
            board,
            min_games=min_games,
            limit=limit,
            sort_mode=sort_mode,
        )
        board_label = self.board_combo.currentText()
        self.summary.setText(
            t("ui.rank.summary_single",
              board=board_label, count=len(rows), min_games=min_games)
        )
        _, tone = _BOARD_META.get(board, ("", QColor(220, 220, 220)))
        self._fill_table(rows, tone=tone)

    def _fill_table(
        self,
        rows: list[PlayerRankRow],
        *,
        tone: QColor,
    ) -> None:
        tbl = self.table
        tbl.setRowCount(len(rows))
        for i, p in enumerate(rows):
            cells = [
                str(p.rank),
                p.display_name or "?",
                f"{p.power:.0f}",
                str(p.games),
                str(p.wins),
                f"{p.win_rate*100:.0f}%",
                f"{p.wilson_lb*100:.0f}%",
                f"{p.avg_k:.1f}/{p.avg_d:.1f}/{p.avg_a:.1f}",
                _fmt_k(p.avg_hero_dmg),
                _fmt_k(p.avg_structure_dmg),
                _fmt_k(p.avg_healing),
                _fmt_k(p.avg_dmg_soaked),
                _fmt_k(p.avg_xp),
            ]
            for j, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setTextAlignment(
                    Qt.AlignVCenter | (Qt.AlignLeft if j == 1 else Qt.AlignRight)
                )
                # Top-3 rows get the board-specific tint so the
                # standout entries pop without reading the rank column.
                if p.rank <= 3:
                    item.setForeground(tone)
                else:
                    item.setForeground(QColor(220, 220, 220))
                tbl.setItem(i, j, item)
