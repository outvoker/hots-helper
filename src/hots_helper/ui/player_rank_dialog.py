"""Player rank leaderboards.

Two side-by-side tables:
* **最坑队友** — players the squad has met whose Storm League win rate
  is the lowest (Wilson 95% lower bound). When one of these handles
  shows up as an ally next game, the BP popup also lights them up.
* **最强对手** — same data, sorted the other way. Highlights cards on
  the enemy side of the BP popup.

Both lists exclude the squad's own handles by default (heuristically:
anyone in our DB with 20+ games of replay history is one of us).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
from ..player_rank import PlayerRankRow, compute_rankings


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


class PlayerRankDialog(QDialog):
    """Dual-table dialog: worst teammates / best opponents."""

    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setMinimumSize(1180, 720)
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

        self.include_squad_chk = QCheckBox()
        self.include_squad_chk.stateChanged.connect(lambda _s: self._reload())
        head.addWidget(self.include_squad_chk)

        self.close_btn = QPushButton()
        self.close_btn.clicked.connect(self.close)
        head.addWidget(self.close_btn)
        root.addLayout(head)

        self.summary = QLabel("")
        self.summary.setStyleSheet("padding: 4px 0; color: #b8c7d9;")
        root.addWidget(self.summary)

        # Two tables side by side. Same column shape so the layout stays
        # symmetric.
        tables_row = QHBoxLayout()
        tables_row.setSpacing(10)

        self.worst_box, self.worst_title, self.worst_table = self._build_table_box()
        self.best_box, self.best_title, self.best_table = self._build_table_box()
        tables_row.addWidget(self.worst_box, 1)
        tables_row.addWidget(self.best_box, 1)
        root.addLayout(tables_row, 1)

        self.footer_label = QLabel()
        self.footer_label.setTextFormat(Qt.RichText)
        self.footer_label.setWordWrap(True)
        root.addWidget(self.footer_label)

    def _build_table_box(self) -> tuple[QVBoxLayout, QLabel, QTableWidget]:
        # Wrap in a vertical layout with a section title above the table.
        from PySide6.QtWidgets import QWidget
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        title = QLabel()
        title.setProperty("role", "title")
        title.setFont(QFont("", 12, QFont.Bold))
        v.addWidget(title)

        tbl = QTableWidget()
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.setSortingEnabled(False)
        # rank | name | games | wins | wr | wlb | kda | hd | hl
        self._col_keys = [
            "ui.rank.col_rank", "ui.rank.col_name", "ui.rank.col_games",
            "ui.rank.col_wins", "ui.rank.col_wr", "ui.rank.col_wlb",
            "ui.rank.col_kda", "ui.rank.col_hero_dmg",
            "ui.rank.col_healing",
        ]
        tbl.setColumnCount(len(self._col_keys))
        for i in range(len(self._col_keys)):
            tbl.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        tbl.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        v.addWidget(tbl, 1)
        return box, title, tbl

    def _retranslate(self) -> None:
        self.setWindowTitle(t("ui.rank.window_title"))
        self.title.setText(t("ui.rank.title"))
        self.min_games_label.setText(t("ui.aram.min_games"))
        self.limit_label.setText(t("ui.rank.limit_label"))
        self.include_squad_chk.setText(t("ui.rank.include_squad"))
        self.close_btn.setText(t("ui.aram.close"))
        self.worst_title.setText(t("ui.rank.worst_title"))
        self.best_title.setText(t("ui.rank.best_title"))
        for tbl in (self.worst_table, self.best_table):
            tbl.setHorizontalHeaderLabels([t(k) for k in self._col_keys])
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
        min_games = self.min_games_spin.value()
        limit = self.limit_spin.value()
        include_squad = self.include_squad_chk.isChecked()
        worst, best = compute_rankings(
            self.store,
            min_games=min_games,
            limit=limit,
            include_squad=include_squad,
        )
        self.summary.setText(
            t("ui.rank.summary",
              worst=len(worst), best=len(best),
              min_games=min_games)
        )
        self._fill_table(self.worst_table, worst, mode="worst")
        self._fill_table(self.best_table, best, mode="best")

    def _fill_table(
        self,
        tbl: QTableWidget,
        rows: list[PlayerRankRow],
        *,
        mode: str,
    ) -> None:
        tbl.setRowCount(len(rows))
        for i, p in enumerate(rows):
            cells = [
                str(p.rank),
                p.display_name or "?",
                str(p.games),
                str(p.wins),
                f"{p.win_rate*100:.0f}%",
                f"{p.wilson_lb*100:.0f}%",
                f"{p.avg_k:.1f}/{p.avg_d:.1f}/{p.avg_a:.1f}",
                _fmt_k(p.avg_hero_dmg),
                _fmt_k(p.avg_healing),
            ]
            for j, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setTextAlignment(
                    Qt.AlignVCenter | (Qt.AlignLeft if j == 1 else Qt.AlignRight)
                )
                # Tint top-3 rows so the worst-of-the-worst / best-of-the-best
                # stand out without needing to read the rank column.
                if p.rank <= 3:
                    if mode == "worst":
                        item.setForeground(QColor(230, 110, 110))
                    else:
                        item.setForeground(QColor(255, 200, 100))
                else:
                    item.setForeground(QColor(220, 220, 220))
                tbl.setItem(i, j, item)
