"""Hero strength ranking dialog.

Shows hero strength on a chosen game mode, ordered by Wilson 95% lower
bound on win-rate so high-winrate-low-sample heroes don't dominate.

Supports both ARAM (天命乱斗) and Storm League (风暴联赛) — switch via
the mode dropdown. Includes a search box to jump to a specific hero.
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
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..db import Store
from ..i18n import on_change as on_lang_change, t
from ..maps import ARAM_MAPS, STORM_LEAGUE_MAPS
from ..player_rank import build_power_baseline, power_score
from ..stats import wilson_lower_bound


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


# Mode keys (DB string + i18n label key).
_MODES = [
    ("ARAM", "ui.aram.mode_aram"),
    ("Storm League", "ui.aram.mode_sl"),
]


class HeroRankingDialog(QDialog):
    """Resizable hero ranking table — switchable between ARAM and Storm League."""

    def __init__(self, store: Store, parent=None,
                 default_mode: str = "ARAM") -> None:
        super().__init__(parent)
        self.store = store
        self.setMinimumSize(1180, 720)
        # Inherit the global black-gold theme — no per-dialog override here.
        self._default_mode = default_mode
        self._build_ui()
        self._retranslate()
        on_lang_change(lambda _c: self._on_lang())
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        self.title = QLabel()
        f = QFont()
        f.setPointSize(14)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setProperty("role", "title")  # picks up gold from theme QSS
        header.addWidget(self.title)
        header.addStretch(1)

        self.mode_label = QLabel()
        header.addWidget(self.mode_label)
        self.mode_combo = QComboBox()
        for value, _label_key in _MODES:
            self.mode_combo.addItem("", value)
        for i in range(self.mode_combo.count()):
            if self.mode_combo.itemData(i) == self._default_mode:
                self.mode_combo.setCurrentIndex(i)
                break
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        header.addWidget(self.mode_combo)

        # Map filter — shows the maps relevant to the active mode.
        # ``None`` data means "all maps". The list is rebuilt whenever
        # the mode changes so SL maps and ARAM maps don't mix.
        self.map_label = QLabel()
        header.addWidget(self.map_label)
        self.map_combo = QComboBox()
        self.map_combo.setMinimumWidth(180)
        self.map_combo.currentIndexChanged.connect(self._reload)
        header.addWidget(self.map_combo)
        self._populate_map_combo()

        self.min_games_label = QLabel()
        header.addWidget(self.min_games_label)
        self.min_games_spin = QSpinBox()
        self.min_games_spin.setRange(1, 200)
        self.min_games_spin.setValue(5)
        self.min_games_spin.valueChanged.connect(self._reload)
        header.addWidget(self.min_games_spin)

        self.sort_label = QLabel()
        header.addWidget(self.sort_label)
        self.sort_combo = QComboBox()
        # Power ("composite combat rating") is first so it's the
        # default — it's the multi-metric synthesis the user came
        # here for. WLB and raw WR are still one click away.
        self.sort_combo.addItem("", "power")
        self.sort_combo.addItem("", "wlb")
        self.sort_combo.addItem("", "wr")
        self.sort_combo.addItem("", "games")
        self.sort_combo.addItem("", "hero")
        self.sort_combo.currentIndexChanged.connect(self._reload)
        header.addWidget(self.sort_combo)

        # Tiny "?" button next to the sort dropdown — opens the
        # power score help window. Same affordance shows up on the
        # player rank dialog so the help text lives in one place.
        self.power_help_btn = QPushButton("?")
        self.power_help_btn.setFixedWidth(28)
        self.power_help_btn.setToolTip(t("ui.power_help.btn_tip"))
        self.power_help_btn.clicked.connect(self._show_power_help)
        header.addWidget(self.power_help_btn)

        self.close_btn = QPushButton()
        self.close_btn.clicked.connect(self.close)
        header.addWidget(self.close_btn)
        root.addLayout(header)

        # Search row
        search_row = QHBoxLayout()
        self.search_label = QLabel()
        search_row.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._on_search_changed)
        self.search_edit.returnPressed.connect(self._jump_to_match)
        search_row.addWidget(self.search_edit, 1)
        self.match_label = QLabel("")
        self.match_label.setProperty("role", "subtitle")
        search_row.addWidget(self.match_label)
        root.addLayout(search_row)

        self.summary = QLabel("")
        self.summary.setStyleSheet("padding: 4px 0; color: #b8c7d9;")
        root.addWidget(self.summary)

        # Stats table — global theme handles the look; we only set
        # behaviour here.
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)

        # Column header keys; the labels are filled in via _retranslate.
        self._column_keys = [
            "ui.aram.col_rank", "ui.aram.col_hero",
            "ui.rank.col_power",
            "ui.aram.col_games", "ui.aram.col_wins",
            "ui.aram.col_wr", "ui.aram.col_wlb",
            "ui.aram.col_kda", "ui.aram.col_hero_dmg",
            "ui.aram.col_dmg_taken", "ui.aram.col_healing",
            "ui.aram.col_struct", "ui.rank.col_soak",
            "ui.aram.col_xp",
        ]
        self.table.setColumnCount(len(self._column_keys))
        for i in range(len(self._column_keys)):
            self.table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents
            )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        root.addWidget(self.table, 1)

        self.footer_label = QLabel()
        self.footer_label.setTextFormat(Qt.RichText)
        self.footer_label.setWordWrap(True)
        root.addWidget(self.footer_label)

    def _populate_map_combo(self) -> None:
        """Refill the map dropdown for the active mode.

        SL and ARAM run on disjoint map pools, so we don't want to mix
        them in the dropdown. ``itemData == None`` is the "all maps"
        sentinel — we always include it as the first entry so the user
        can opt out of the filter.
        """
        prev = self.map_combo.currentData() if self.map_combo.count() else None
        # Block signals so the rebuild doesn't trigger a useless _reload —
        # _on_mode_changed will call _reload itself once we're done.
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        self.map_combo.addItem(t("ui.aram.map_all"), None)
        mode = self.mode_combo.currentData() if self.mode_combo.count() else None
        pool = ARAM_MAPS if mode == "ARAM" else STORM_LEAGUE_MAPS
        for name in pool:
            self.map_combo.addItem(name, name)
        # Restore previous selection if still valid; otherwise default to "all".
        if prev:
            idx = self.map_combo.findData(prev)
            if idx >= 0:
                self.map_combo.setCurrentIndex(idx)
        self.map_combo.blockSignals(False)

    def _on_mode_changed(self) -> None:
        # Rebuild the map list (SL ↔ ARAM share no maps), then reload.
        self._populate_map_combo()
        self._reload()

    def _retranslate(self) -> None:
        self.setWindowTitle(t("ui.aram.window_title"))
        self.mode_label.setText(t("ui.aram.mode"))
        self.map_label.setText(t("ui.aram.map"))
        # Refresh the "all maps" sentinel label without losing selection.
        if self.map_combo.count() and self.map_combo.itemData(0) is None:
            self.map_combo.setItemText(0, t("ui.aram.map_all"))
        # Mode combo items
        for i, (value, key) in enumerate(_MODES):
            self.mode_combo.setItemText(i, t(key))
        self.min_games_label.setText(t("ui.aram.min_games"))
        self.sort_label.setText(t("ui.aram.sort"))
        # Sort combo items
        sort_keys = {
            "wr": "ui.aram.sort_wr",
            "wlb": "ui.aram.sort_wlb",
            "power": "ui.aram.sort_power",
            "games": "ui.aram.sort_games",
            "hero": "ui.aram.sort_hero",
        }
        for i in range(self.sort_combo.count()):
            key = sort_keys.get(self.sort_combo.itemData(i), "")
            if key:
                self.sort_combo.setItemText(i, t(key))
        self.sort_combo.setToolTip(t("ui.aram.sort_tip"))
        self.close_btn.setText(t("ui.aram.close"))
        self.search_label.setText(t("ui.aram.search_label"))
        self.search_edit.setPlaceholderText(t("ui.aram.search_placeholder"))
        # Column headers
        self.table.setHorizontalHeaderLabels([t(k) for k in self._column_keys])
        # Footer
        self.footer_label.setText(t("ui.aram.footer"))

    def _on_lang(self) -> None:
        self._retranslate()
        self._reload()

    def _reload(self) -> None:
        try:
            self._reload_inner()
        except Exception as e:
            # Without this, a SQL error (or any other unhandled exception)
            # silently aborts mid-update and leaves the table showing
            # whatever was there before — the user changes the filter
            # and nothing happens. Surface it so we can see what went
            # wrong instead.
            import traceback
            self.summary.setText(
                f"<span style='color:#e08585;'>"
                f"加载失败：{type(e).__name__}: {e}</span>"
            )
            traceback.print_exc()

    def _reload_inner(self) -> None:
        mode = self.mode_combo.currentData()
        mode_label = self.mode_combo.currentText()
        map_name = self.map_combo.currentData()  # None = "all maps"

        if map_name:
            self.title.setText(
                t("ui.aram.title_with_map", mode=mode_label, map=map_name)
            )
        else:
            self.title.setText(t("ui.aram.title", mode=mode_label))

        # Two filter clauses — one for queries that join replays as ``r``
        # (the main aggregate + the player_match total), and one for
        # queries that read from the ``replays`` table directly. The
        # earlier code reused a single ``r.map_name = ?`` snippet against
        # both contexts, which crashed in the un-aliased path with
        # ``no such column: r.map_name`` and silently aborted ``_reload``.
        params_joined: list = [mode]
        map_clause_joined = ""
        params_replays: list = [mode]
        map_clause_replays = ""
        if map_name:
            map_clause_joined = " AND r.map_name = ?"
            params_joined.append(map_name)
            map_clause_replays = " AND map_name = ?"
            params_replays.append(map_name)

        # Use the shared hero_aggregate_stats helper so the role-
        # contribution thresholds (only count "real healing" toward a
        # healer's healing average, etc.) live in one place.
        rows = self.store.hero_aggregate_stats(
            map_name=map_name,
            mode_filter=(mode,),
        )

        # Build the global per-match baseline once for the power score.
        # Heavy-tailed metrics get percentile-ranked against this so a
        # niche hero with 1 game can't get a 100 just by being its
        # board's only entry.
        try:
            baseline = build_power_baseline(self.store)
        except Exception:
            baseline = None

        min_games = self.min_games_spin.value()
        ranked = []
        for r in rows:
            g = int(r["games"])
            won = int(r["wins"] or 0)
            if g < min_games:
                continue
            wlb = wilson_lower_bound(won, g)
            wr = won / g if g else 0.0
            avg_k = float(r["avg_k"] or 0); avg_d = float(r["avg_d"] or 0)
            avg_a = float(r["avg_a"] or 0)
            avg_hd = float(r["avg_hero_dmg"] or 0)
            avg_strd = float(r["avg_structure_dmg"] or 0)
            avg_sgd = float(r["avg_siege_dmg"] or 0)
            avg_hl = float(r["avg_healing"] or 0)
            avg_dt = float(r["avg_dmg_taken"] or 0)
            avg_soak = float(r["avg_dmg_soaked"] or 0)
            avg_xp = float(r["avg_xp"] or 0)
            avg_cc = float(r["avg_cc"] or 0)
            if baseline is not None:
                power = power_score(
                    baseline=baseline,
                    win_rate=wr,
                    avg_k=avg_k, avg_d=avg_d, avg_a=avg_a,
                    avg_hero_dmg=avg_hd,
                    avg_siege_dmg=avg_sgd,
                    avg_structure_dmg=avg_strd,
                    avg_healing=avg_hl,
                    avg_dmg_soaked=avg_soak,
                    avg_dmg_taken=avg_dt,
                    avg_xp=avg_xp,
                    avg_cc=avg_cc,
                )
            else:
                power = 0.0
            ranked.append({
                "hero": r["hero"], "games": g, "wins": won,
                "wr": wr, "wlb": wlb,
                "power": power,
                "k": avg_k, "d": avg_d, "a": avg_a,
                "hd": avg_hd, "dt": avg_dt,
                "hl": avg_hl, "strd": avg_strd,
                "soak": avg_soak,
                "xp": avg_xp,
            })

        sort_key = self.sort_combo.currentData()
        if sort_key == "wlb":
            ranked.sort(key=lambda x: -x["wlb"])
        elif sort_key == "power":
            ranked.sort(key=lambda x: -x["power"])
        elif sort_key == "wr":
            ranked.sort(key=lambda x: -x["wr"])
        elif sort_key == "games":
            ranked.sort(key=lambda x: -x["games"])
        elif sort_key == "hero":
            ranked.sort(key=lambda x: x["hero"])

        # DB summary row — same map filter applied so the totals match
        # the data we actually charted.
        total_games = self.store.conn.execute(
            f"SELECT COUNT(*) FROM replays WHERE mode = ?{map_clause_replays}",
            tuple(params_replays),
        ).fetchone()[0]
        total_pm = self.store.conn.execute(f"""
            SELECT COUNT(*) FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE r.mode = ?{map_clause_joined}
        """, tuple(params_joined)).fetchone()[0]
        self.summary.setText(
            t("ui.aram.summary",
              games=total_games, mode=mode_label, pm=total_pm,
              ranked=len(ranked), min_games=min_games)
        )

        self.table.setRowCount(len(ranked))
        for i, r in enumerate(ranked):
            cells = [
                str(i + 1),
                r["hero"],
                f"{r['power']:.0f}",
                str(r["games"]),
                str(r["wins"]),
                f"{r['wr']*100:.0f}%",
                f"{r['wlb']*100:.0f}%",
                f"{r['k']:.1f}/{r['d']:.1f}/{r['a']:.1f}",
                _fmt_k(r["hd"]),
                _fmt_k(r["dt"]),
                _fmt_k(r["hl"]),
                _fmt_k(r["strd"]),
                _fmt_k(r["soak"]),
                _fmt_k(r["xp"]),
            ]
            for j, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setTextAlignment(
                    Qt.AlignVCenter | (Qt.AlignLeft if j == 1 else Qt.AlignRight)
                )
                wlb = r["wlb"]
                if wlb >= 0.50:
                    item.setForeground(QColor(120, 220, 120))
                elif wlb < 0.40:
                    item.setForeground(QColor(220, 110, 110))
                else:
                    item.setForeground(QColor(230, 230, 230))
                self.table.setItem(i, j, item)

        # Re-apply any active search highlight
        self._on_search_changed(self.search_edit.text())

    # --- power help ------------------------------------------------------

    def _show_power_help(self) -> None:
        from .power_help import PowerHelpDialog
        PowerHelpDialog(self).exec()

    # --- search ----------------------------------------------------------

    def _matches(self, query: str) -> list[int]:
        """Row indices whose hero name contains ``query`` (case-insensitive)."""
        q = query.strip().lower()
        if not q:
            return []
        out = []
        for row in range(self.table.rowCount()):
            hero_item = self.table.item(row, 1)
            if hero_item and q in hero_item.text().lower():
                out.append(row)
        return out

    def _on_search_changed(self, query: str) -> None:
        matches = self._matches(query)
        if not query.strip():
            self.match_label.setText("")
            return
        self.match_label.setTextFormat(Qt.RichText)
        if not matches:
            self.match_label.setText(t("ui.aram.no_match"))
            return
        self.match_label.setText(t("ui.aram.matches", n=len(matches)))
        self._jump_to_row(matches[0])

    def _jump_to_match(self) -> None:
        matches = self._matches(self.search_edit.text())
        if matches:
            self._jump_to_row(matches[0])

    def _jump_to_row(self, row: int) -> None:
        self.table.scrollToItem(
            self.table.item(row, 1),
            QAbstractItemView.PositionAtCenter,
        )
        self.table.selectRow(row)


# Backwards-compat alias for code paths that still import the old name.
AramRankingDialog = HeroRankingDialog
