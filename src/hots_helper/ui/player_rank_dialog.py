"""Player ranking — single sortable table over every player who has
shared a match with the squad.

Click any column header to sort by that column; click again to flip
direction. Default sort = combat power desc. The dialog deliberately
doesn't split the data into "teammate / opponent" boards any more —
the squad's own handles show up alongside random teammates and
opponents, and the user can sort to see whichever extreme they want.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from ..player_rank import PlayerRankRow, compute_player_rankings


class _NumericItem(QTableWidgetItem):
    """Cell with formatted display text but numeric sort. See
    :mod:`.aram` for the same pattern — duplicated here to keep the
    import graph small."""

    def __init__(self, text: str, sort_value=None) -> None:
        super().__init__(text)
        self._sort_value = sort_value

    def __lt__(self, other: "QTableWidgetItem") -> bool:  # type: ignore[override]
        if isinstance(other, _NumericItem) and self._sort_value is not None \
                and other._sort_value is not None:
            return self._sort_value < other._sort_value
        return super().__lt__(other)


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


# Column indices. Rank is shown via Qt's built-in vertical header
# (left of the table) — clicking any column header re-sorts and the
# row numbers on the side update automatically, which is the
# behaviour the user actually wants.
COL_NAME   = 0
COL_POWER  = 1
COL_GAMES  = 2
COL_WINS   = 3
COL_WR     = 4
COL_WLB    = 5
COL_KDA    = 6
COL_HD     = 7
COL_STRUCT = 8
COL_HEAL   = 9
COL_SOAK   = 10
COL_XP     = 11


class PlayerRankDialog(QDialog):
    """Click-to-sort player leaderboard."""

    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setMinimumSize(960, 620)
        self.resize(1180, 720)
        self._build_ui()
        self._retranslate()
        on_lang_change(lambda _c: self._on_lang())
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setProperty("role", "title")
        head.addWidget(self.title)
        head.addStretch(1)

        self.min_games_label = QLabel()
        head.addWidget(self.min_games_label)
        self.min_games_spin = QSpinBox()
        self.min_games_spin.setRange(1, 100)
        self.min_games_spin.setValue(2)
        self.min_games_spin.valueChanged.connect(self._reload)
        head.addWidget(self.min_games_spin)

        self.limit_label = QLabel()
        head.addWidget(self.limit_label)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(10, 500)
        self.limit_spin.setSingleStep(10)
        self.limit_spin.setValue(50)
        self.limit_spin.valueChanged.connect(self._reload)
        head.addWidget(self.limit_spin)

        self.power_help_btn = QPushButton()
        self.power_help_btn.setToolTip(t("ui.power_help.btn_tip"))
        self.power_help_btn.clicked.connect(self._show_power_help)
        head.addWidget(self.power_help_btn)

        self.close_btn = QPushButton()
        self.close_btn.clicked.connect(self.close)
        head.addWidget(self.close_btn)
        root.addLayout(head)

        self.summary = QLabel("")
        self.summary.setStyleSheet("padding: 4px 0; color: #b8c7d9;")
        root.addWidget(self.summary)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        # Click-to-sort. Columns hold numeric data via setData(EditRole)
        # so the sort is numerically correct even though the cell text
        # is formatted (e.g. "12.3k", "47%").
        self.table.setSortingEnabled(True)

        self._col_keys = [
            "ui.rank.col_name",
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
        self.table.horizontalHeader().setSectionResizeMode(
            COL_NAME, QHeaderView.Stretch
        )
        self.table.horizontalHeader().setMinimumSectionSize(40)
        self.table.setColumnWidth(COL_NAME, 220)
        # Default sort: combat power, descending.
        self.table.sortByColumn(COL_POWER, Qt.DescendingOrder)
        # Show row numbers on the left so the user can read off rank
        # within the current sort without burning a column on it.
        self.table.verticalHeader().setVisible(True)
        root.addWidget(self.table, 1)

        self.footer_label = QLabel()
        self.footer_label.setTextFormat(Qt.RichText)
        self.footer_label.setWordWrap(True)
        root.addWidget(self.footer_label)

    def _retranslate(self) -> None:
        self.setWindowTitle(t("ui.rank.window_title"))
        self.title.setText(t("ui.rank.title"))
        self.min_games_label.setText(t("ui.aram.min_games"))
        self.limit_label.setText(t("ui.rank.limit_label"))
        self.power_help_btn.setText(t("ui.power_help.btn_label"))
        self.close_btn.setText(t("ui.aram.close"))
        self.table.setHorizontalHeaderLabels([t(k) for k in self._col_keys])
        self.footer_label.setText(t("ui.rank.footer_single"))

    def _on_lang(self) -> None:
        self._retranslate()
        self._reload()

    def _show_power_help(self) -> None:
        from .power_help import PowerHelpDialog
        PowerHelpDialog(self).exec()

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

        rows = compute_player_rankings(
            self.store,
            min_games=min_games,
            limit=limit,
        )
        # Squad set so we can tint our own rows in the table — makes
        # it easy to see at a glance who in the 5-stack is over- or
        # under-performing relative to the random handles we've
        # queued with.
        self._squad_set: set[str] = set(self.store.squad_handles())
        self.summary.setText(
            t("ui.rank.summary_total",
              count=len(rows), min_games=min_games)
        )
        self._fill_table(rows)

    def _fill_table(self, rows: list[PlayerRankRow]) -> None:
        tbl = self.table
        # Disable sorting while populating; a bunch of setItem() calls
        # under live sorting would shuffle the table on every insert.
        tbl.setSortingEnabled(False)
        tbl.setRowCount(len(rows))
        for i, p in enumerate(rows):
            specs = [
                (COL_NAME,   p.display_name or "?",        None),
                (COL_POWER,  f"{p.power:.0f}",             p.power),
                (COL_GAMES,  str(p.games),                 p.games),
                (COL_WINS,   str(p.wins),                  p.wins),
                (COL_WR,     f"{p.win_rate*100:.0f}%",     p.win_rate),
                (COL_WLB,    f"{p.wilson_lb*100:.0f}%",    p.wilson_lb),
                (COL_KDA,    f"{p.avg_k:.1f}/{p.avg_d:.1f}/{p.avg_a:.1f}", p.kda),
                (COL_HD,     _fmt_k(p.avg_hero_dmg),       p.avg_hero_dmg),
                (COL_STRUCT, _fmt_k(p.avg_structure_dmg),  p.avg_structure_dmg),
                (COL_HEAL,   _fmt_k(p.avg_healing),        p.avg_healing),
                (COL_SOAK,   _fmt_k(p.avg_dmg_soaked),     p.avg_dmg_soaked),
                (COL_XP,     _fmt_k(p.avg_xp),             p.avg_xp),
            ]
            # Squad rows get tinted gold so the user can spot their
            # own 5-stack inside the random-handle population.
            is_squad = p.toon_handle in self._squad_set
            fg = QColor(244, 196, 83) if is_squad else QColor(220, 220, 220)
            bg = QColor(60, 48, 18) if is_squad else None

            for col, text, sort_value in specs:
                item = _NumericItem(text, sort_value)
                item.setTextAlignment(
                    Qt.AlignVCenter | (Qt.AlignLeft if col == COL_NAME else Qt.AlignRight)
                )
                item.setForeground(fg)
                if bg is not None:
                    item.setBackground(bg)
                tbl.setItem(i, col, item)
        tbl.setSortingEnabled(True)
