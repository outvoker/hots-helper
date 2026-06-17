"""Browse ingested match records in the desktop tool.

Mirrors the web app's 比赛记录 view: a paginated, filterable list of
matches (mode / map / player), newest first, with a double-click into a
full per-match scoreboard (both teams, hero + K/D/A + key stats).

Read-only — it queries the same :class:`Store` the rest of the app uses.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..db import Store
from ..i18n import t
from .. import maps as maps_data
from .theme import BG_DEEP, BG_INPUT, GOLD_DIM, GOLD_BRIGHT, GOOD, LINE, TEXT, TEXT_DIM, WARN

_PAGE = 25
# Mode buckets exposed in the filter. None data = all modes.
_MODES = [
    ("Storm League", "ui.matches.mode_sl"),
    ("ARAM", "ui.matches.mode_aram"),
    ("Quick Match", "ui.matches.mode_qm"),
    (None, "ui.matches.mode_all"),
]


def _fmt_dur(seconds: int) -> str:
    m, s = divmod(int(seconds or 0), 60)
    return f"{m}:{s:02d}"


def _fmt_date(iso: str) -> str:
    return (iso or "").replace("T", " ")[:16]


class MatchListDialog(QDialog):
    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._offset = 0
        self._total = 0

        self.setWindowTitle(t("ui.matches.window_title"))
        self.resize(1040, 680)
        self.setStyleSheet(
            f"QDialog {{ background:{BG_DEEP}; color:{TEXT}; }}"
            f"QLabel {{ color:{TEXT}; }}"
            f"QLineEdit, QComboBox {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:4px; padding:4px 8px; }}"
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; padding:5px 12px; border-radius:4px; }}"
            f"QPushButton:hover {{ border-color:{GOLD_DIM}; color:{GOLD_BRIGHT}; }}"
            f"QPushButton:disabled {{ color:{TEXT_DIM}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Filter row.
        filters = QHBoxLayout()
        self.mode_combo = QComboBox()
        for value, _key in _MODES:
            self.mode_combo.addItem("", value)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_change)
        filters.addWidget(self.mode_combo)

        self.map_combo = QComboBox()
        self.map_combo.setMinimumWidth(160)
        self.map_combo.currentIndexChanged.connect(self._reload_from_start)
        filters.addWidget(self.map_combo)

        self.player_edit = QLineEdit()
        self.player_edit.setPlaceholderText(t("ui.matches.player_ph"))
        self.player_edit.returnPressed.connect(self._reload_from_start)
        filters.addWidget(self.player_edit, 1)

        self.search_btn = QPushButton(t("ui.matches.search"))
        self.search_btn.clicked.connect(self._reload_from_start)
        filters.addWidget(self.search_btn)
        root.addLayout(filters)

        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color:{TEXT_DIM};")
        root.addWidget(self.summary)

        # Match table.
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self._cols = [
            "ui.matches.col_date", "ui.matches.col_mode", "ui.matches.col_map",
            "ui.matches.col_dur", "ui.matches.col_result", "ui.matches.col_teams",
        ]
        self.table.setColumnCount(len(self._cols))
        self.table.cellDoubleClicked.connect(self._open_detail)
        hdr = self.table.horizontalHeader()
        for i in range(len(self._cols)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(len(self._cols) - 1, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        # Pager.
        pager = QHBoxLayout()
        self.prev_btn = QPushButton(t("ui.matches.prev"))
        self.prev_btn.clicked.connect(self._prev_page)
        pager.addWidget(self.prev_btn)
        self.page_label = QLabel("")
        self.page_label.setAlignment(Qt.AlignCenter)
        pager.addWidget(self.page_label, 1)
        self.next_btn = QPushButton(t("ui.matches.next"))
        self.next_btn.clicked.connect(self._next_page)
        pager.addWidget(self.next_btn)
        root.addLayout(pager)

        self._retranslate_modes()
        self._populate_map_combo()
        self._reload()

    # --- filters ----------------------------------------------------------

    def _retranslate_modes(self) -> None:
        for i, (_value, key) in enumerate(_MODES):
            self.mode_combo.setItemText(i, t(key))

    def _current_mode(self) -> str | None:
        return self.mode_combo.currentData()

    def _on_mode_change(self, _i: int) -> None:
        self._populate_map_combo()
        self._reload_from_start()

    def _populate_map_combo(self) -> None:
        """Map list for the active mode (SL maps vs ARAM maps)."""
        mode = self._current_mode()
        if mode == "ARAM":
            names = maps_data.ARAM_MAPS
        elif mode == "Storm League":
            names = maps_data.STORM_LEAGUE_MAPS
        else:
            names = maps_data.all_maps()
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        self.map_combo.addItem(t("ui.matches.all_maps"), None)
        for n in names:
            self.map_combo.addItem(n, n)
        self.map_combo.blockSignals(False)

    # --- paging -----------------------------------------------------------

    def _reload_from_start(self) -> None:
        self._offset = 0
        self._reload()

    def _prev_page(self) -> None:
        self._offset = max(0, self._offset - _PAGE)
        self._reload()

    def _next_page(self) -> None:
        if self._offset + _PAGE < self._total:
            self._offset += _PAGE
            self._reload()

    # --- data + render ----------------------------------------------------

    def _reload(self) -> None:
        try:
            self.store.drop_read_snapshot()
        except Exception:
            pass

        player = self.player_edit.text().strip() or None
        rows, total = self.store.list_matches(
            map_name=self.map_combo.currentData(),
            mode=self._current_mode(),
            player=player,
            limit=_PAGE,
            offset=self._offset,
        )
        self._total = total

        roster = self.store.match_roster_brief([int(r["id"]) for r in rows])
        by_replay: dict[int, list] = {}
        for r in roster:
            by_replay.setdefault(int(r["replay_id"]), []).append(r)

        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            rid = int(r["id"])
            team_brief = self._team_brief(by_replay.get(rid, []))
            result_txt, result_color = self._result_text(by_replay.get(rid, []), r, player)
            cells = [
                _fmt_date(r["played_at"]),
                r["mode"],
                r["map_name"],
                _fmt_dur(r["duration_s"]),
                result_txt,
                team_brief,
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, rid)
                if c == 4 and result_color:
                    item.setForeground(result_color)
                self.table.setItem(i, c, item)

        self.summary.setText(
            t("ui.matches.summary", total=total)
        )
        page = self._offset // _PAGE + 1
        pages = max(1, (total + _PAGE - 1) // _PAGE)
        self.page_label.setText(t("ui.matches.page", page=page, pages=pages))
        self.prev_btn.setEnabled(self._offset > 0)
        self.next_btn.setEnabled(self._offset + _PAGE < total)

    def _team_brief(self, roster: list) -> str:
        """Compact 'team0 heroes  vs  team1 heroes' string."""
        t0 = [r["hero"] for r in roster if int(r["team"]) == 0]
        t1 = [r["hero"] for r in roster if int(r["team"]) == 1]
        return f"{'、'.join(t0)}  vs  {'、'.join(t1)}"

    def _result_text(self, roster: list, replay, player):
        """If filtering by a player, show that player's win/loss; else the
        winning team number."""
        from PySide6.QtGui import QColor

        if player:
            for r in roster:
                if r["display_name"] == player or str(r.get("display_name")) == player:
                    won = int(r["result"]) == 1
                    return (t("ui.matches.win") if won else t("ui.matches.loss"),
                            QColor(GOOD) if won else QColor(WARN))
        wt = int(replay["winner_team"])
        return t("ui.matches.team_won", team=wt), None

    def _open_detail(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item is None:
            return
        rid = item.data(Qt.UserRole)
        if rid is None:
            return
        from .match_detail_dialog import MatchDetailDialog
        MatchDetailDialog(self.store, int(rid), parent=self).exec()
