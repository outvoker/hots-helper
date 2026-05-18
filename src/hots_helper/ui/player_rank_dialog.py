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
    QComboBox,
    QCompleter,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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


# Column indices. We bring back an explicit rank column because the
# table mixes the top-N slice with squad "extras" whose real rank
# (e.g. 100) the user wants to see.
COL_RANK   = 0
COL_NAME   = 1
COL_POWER  = 2
COL_GAMES  = 3
COL_WINS   = 4
COL_WR     = 5
COL_WLB    = 6
COL_KDA    = 7
COL_HD     = 8
COL_STRUCT = 9
COL_HEAL   = 10
COL_SOAK   = 11
COL_XP     = 12


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

        # Hero filter — leftmost so it reads "玩家排行 [英雄: 全部] …".
        # Empty selection = all heroes (the default leaderboard).
        # The combo is editable so the user can type to search; the
        # attached QCompleter does substring-contains filtering, so
        # typing "阿" surfaces both 阿巴瑟 and 阿兹莫丹, and typing
        # "ling" surfaces 雷诺 (Raynor → keeps Latin transliteration
        # users honest too — currently not in our DB but the cost is
        # zero). NoInsert keeps free-text from being committed: the
        # user must pick a real hero (or clear the box for "all").
        self.hero_label = QLabel()
        head.addWidget(self.hero_label)
        self.hero_combo = QComboBox()
        self.hero_combo.setMinimumWidth(180)
        self.hero_combo.setEditable(True)
        self.hero_combo.setInsertPolicy(QComboBox.NoInsert)
        self.hero_combo.lineEdit().setClearButtonEnabled(True)
        self.hero_combo.currentIndexChanged.connect(lambda _i: self._reload())
        head.addWidget(self.hero_combo)
        self._populate_hero_combo()

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
        # Limit only changes how many cached rows we display — no DB
        # round-trip needed.
        self.limit_spin.valueChanged.connect(self._render_table)
        head.addWidget(self.limit_spin)

        # Player-name search. Substring + case-insensitive; also
        # case-folded so "Bigge" matches "BeigeBison". Filters the
        # cached population without re-querying the DB.
        self.search_label = QLabel()
        head.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setMinimumWidth(160)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _t: self._render_table())
        head.addWidget(self.search_edit)

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
        # We do our own sorting in Python so the squad-extras rows
        # can stay glued to the bottom no matter which column the
        # user clicks. Qt's setSortingEnabled would honour the click
        # but reorder *everyone*, dragging extras into the slice.
        self.table.setSortingEnabled(False)

        self._col_keys = [
            "ui.rank.col_rank",
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
        # Make the header act sortable to the eye (arrow indicator,
        # click highlight) even though we run the sort ourselves.
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        # Default state — power desc.
        self._sort_col = COL_POWER
        self._sort_desc = True
        self.table.horizontalHeader().setSortIndicator(
            COL_POWER, Qt.DescendingOrder
        )
        self.table.verticalHeader().setVisible(True)
        root.addWidget(self.table, 1)

        # Cache the full sorted-by-power population so a header click
        # can re-slice without re-querying the DB. Each row's
        # ``rank`` is fixed to its global power-rank (#1..#N), so the
        # rank cell still shows the player's overall standing even
        # when the user is currently sorting by a different column.
        self._cached_all: list[PlayerRankRow] = []

        self.footer_label = QLabel()
        self.footer_label.setTextFormat(Qt.RichText)
        self.footer_label.setWordWrap(True)
        root.addWidget(self.footer_label)

    def _retranslate(self) -> None:
        self.setWindowTitle(t("ui.rank.window_title"))
        self.title.setText(t("ui.rank.title"))
        self.hero_label.setText(t("ui.rank.hero_filter"))
        # Refresh the "all heroes" sentinel label without losing the
        # current selection.
        if self.hero_combo.count() and self.hero_combo.itemData(0) is None:
            self.hero_combo.setItemText(0, t("ui.rank.hero_all"))
        self.min_games_label.setText(t("ui.aram.min_games"))
        self.limit_label.setText(t("ui.rank.limit_label"))
        self.search_label.setText(t("ui.rank.search_label"))
        self.search_edit.setPlaceholderText(t("ui.rank.search_placeholder"))
        self.power_help_btn.setText(t("ui.power_help.btn_label"))
        self.close_btn.setText(t("ui.aram.close"))
        self.table.setHorizontalHeaderLabels([t(k) for k in self._col_keys])
        self.footer_label.setText(t("ui.rank.footer_single"))

    def _on_lang(self) -> None:
        self._retranslate()
        self._reload()

    def _populate_hero_combo(self) -> None:
        """Fill the hero dropdown with every hero in the DB.

        ``itemData == None`` is the "all heroes" sentinel — kept as
        the first entry so the dialog opens unfiltered. Items are
        sorted by hero name. The companion QCompleter does substring-
        contains filtering on whatever the user types, so a 90-hero
        list is searchable without keyboard memorising the exact
        opening glyph.
        """
        self.hero_combo.blockSignals(True)
        self.hero_combo.clear()
        self.hero_combo.addItem(t("ui.rank.hero_all"), None)
        try:
            rows = self.store.all_heroes()
        except Exception:
            rows = []
        for r in sorted(rows, key=lambda x: x["hero"]):
            self.hero_combo.addItem(r["hero"], r["hero"])
        self.hero_combo.blockSignals(False)

        # Substring-contains completer so typing "阿" matches both
        # 阿巴瑟 and 阿兹莫丹 — the default "starts-with" mode would
        # only match items whose first character equals the prefix.
        completer = QCompleter(
            [self.hero_combo.itemText(i) for i in range(self.hero_combo.count())],
            self.hero_combo,
        )
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.hero_combo.setCompleter(completer)

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
        hero = self.hero_combo.currentData()  # None = all heroes

        all_rows = compute_player_rankings(
            self.store,
            min_games=min_games,
            hero=hero,
        )
        # Squad set so we can tint our own rows in the table.
        self._squad_set: set[str] = set(self.store.squad_handles())
        self._cached_all = all_rows
        if hero:
            self.summary.setText(
                t("ui.rank.summary_hero",
                  hero=hero, count=len(all_rows), min_games=min_games)
            )
        else:
            self.summary.setText(
                t("ui.rank.summary_total",
                  count=len(all_rows), min_games=min_games)
            )
        self._render_table()

    def _render_table(self) -> None:
        """Re-paint the table from ``self._cached_all``.

        The visible slice is the top ``limit`` rows by the *currently
        selected sort column*. Click "胜率" desc and the slice is the
        50 highest-WR players overall, not just "the 50 highest-power
        players sorted by WR within themselves".

        Squad members who don't make that slice (because they're not
        extreme on the chosen metric) get pinned at the bottom in a
        separate group, with their global rank preserved.
        """
        tbl = self.table
        limit = self.limit_spin.value()

        if not self._cached_all:
            tbl.setRowCount(0)
            return

        # Player-name search filter. Empty query → no filter. Match
        # against display_name + toon_handle so Latin-only users can
        # find Korean handles by typing the visible name.
        query = (self.search_edit.text() if hasattr(self, "search_edit") else "")
        query = query.strip().casefold()
        if query:
            visible = [
                p for p in self._cached_all
                if query in (p.display_name or "").casefold()
                or query in (p.toon_handle or "").casefold()
            ]
        else:
            visible = self._cached_all

        sorted_all = sorted(
            visible,
            key=lambda p: self._sort_key(p),
            reverse=self._sort_desc,
        )
        slice_rows = sorted_all[:limit]
        slice_handles = {p.toon_handle for p in slice_rows}
        # Extras = squad members from the *visible* (search-filtered)
        # population that didn't make the slice. Sort them by the
        # current column too so the pinned section is internally
        # consistent with the main slice.
        extras = sorted(
            (p for p in visible
             if p.toon_handle in self._squad_set
             and p.toon_handle not in slice_handles),
            key=lambda p: self._sort_key(p),
            reverse=self._sort_desc,
        )

        all_visible = slice_rows + extras
        tbl.setRowCount(len(all_visible))
        n_slice = len(slice_rows)
        for i, p in enumerate(all_visible):
            self._fill_row(tbl, i, p, is_extra=(i >= n_slice))

    # Column → key extractor for Python-side sorting. Power is the
    # default fallback so an unknown column doesn't blow up.
    _SORT_GETTERS = {
        COL_RANK:   lambda p: p.rank,
        COL_NAME:   lambda p: p.display_name or "",
        COL_POWER:  lambda p: p.power,
        COL_GAMES:  lambda p: p.games,
        COL_WINS:   lambda p: p.wins,
        COL_WR:     lambda p: p.win_rate,
        COL_WLB:    lambda p: p.wilson_lb,
        COL_KDA:    lambda p: p.kda,
        COL_HD:     lambda p: p.avg_hero_dmg,
        COL_STRUCT: lambda p: p.avg_structure_dmg,
        COL_HEAL:   lambda p: p.avg_healing,
        COL_SOAK:   lambda p: p.avg_dmg_soaked,
        COL_XP:     lambda p: p.avg_xp,
    }

    def _sort_key(self, p: PlayerRankRow):
        getter = self._SORT_GETTERS.get(self._sort_col)
        if getter is None:
            return p.power
        return getter(p)

    def _on_header_clicked(self, col: int) -> None:
        """Toggle direction when clicking the same column; otherwise
        switch to the new column with a sensible default direction
        (numeric columns descend, the name column ascends)."""
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = (col != COL_NAME)
        self.table.horizontalHeader().setSortIndicator(
            col,
            Qt.DescendingOrder if self._sort_desc else Qt.AscendingOrder,
        )
        self._render_table()

    def _fill_row(
        self,
        tbl: QTableWidget,
        row_idx: int,
        p: PlayerRankRow,
        *,
        is_extra: bool,
    ) -> None:
        specs = [
            (COL_RANK,   str(p.rank),                  p.rank),
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
        is_squad = p.toon_handle in self._squad_set
        # Squad rows: gold text + dim-gold background so they pop in
        # the main slice. Extras rows: same colours plus a slightly
        # darker background so the visual break between the slice
        # and the appended squad section reads at a glance.
        if is_squad:
            fg = QColor(244, 196, 83)
            bg = QColor(40, 32, 12) if is_extra else QColor(60, 48, 18)
        else:
            fg = QColor(220, 220, 220)
            bg = None

        for col, text, sort_value in specs:
            item = _NumericItem(text, sort_value)
            item.setTextAlignment(
                Qt.AlignVCenter | (Qt.AlignLeft if col == COL_NAME else Qt.AlignRight)
            )
            item.setForeground(fg)
            if bg is not None:
                item.setBackground(bg)
            tbl.setItem(row_idx, col, item)
