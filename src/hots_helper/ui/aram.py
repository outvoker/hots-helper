"""ARAM hero ranking window.

Shows hero strength on the random-pick mode, ordered by Wilson 95% lower
bound on win-rate so high-winrate-low-sample heroes don't dominate.
Separate from the Storm League BP advisor — different mode, different
balance, different hero pool.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
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
from ..stats import wilson_lower_bound


def _fmt_k(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k"
    if value >= 1_000:
        return f"{value/1000:.1f}k"
    return f"{value:.0f}"


class AramRankingDialog(QDialog):
    """Modal-but-resizable hero ranking table for ARAM mode."""

    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("ARAM hero ranking — 天命乱斗英雄强度榜")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet("background:#161616; color:#eee;")
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("天命乱斗 英雄强度榜")
        title.setFont(QFont("", 14, QFont.Bold))
        title.setStyleSheet("color:#fc6;")
        header.addWidget(title)
        header.addStretch(1)

        header.addWidget(QLabel("最少局数:"))
        self.min_games_spin = QSpinBox()
        self.min_games_spin.setRange(1, 200)
        self.min_games_spin.setValue(5)
        self.min_games_spin.valueChanged.connect(self._reload)
        header.addWidget(self.min_games_spin)

        header.addWidget(QLabel("排序:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Wilson 置信下界 (推荐)", "wlb")
        self.sort_combo.addItem("胜率", "wr")
        self.sort_combo.addItem("局数", "games")
        self.sort_combo.addItem("英雄名", "hero")
        self.sort_combo.currentIndexChanged.connect(self._reload)
        header.addWidget(self.sort_combo)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        root.addLayout(header)

        self.summary = QLabel("")
        self.summary.setStyleSheet("color:#9ad; padding: 4px 0;")
        root.addWidget(self.summary)

        # Stats table
        self.table = QTableWidget()
        self.table.setStyleSheet(
            "QTableWidget { background:#1d1d1d; color:#eee; gridline-color:#333; }"
            "QHeaderView::section { background:#272727; color:#fc6; "
            "                       padding:4px; border:1px solid #333; "
            "                       font-weight:600; }"
            "QTableWidget::item:selected { background:#2a3540; color:#eee; }"
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)  # we sort ourselves to use WLB

        columns = [
            ("排名", "right"),
            ("英雄", "left"),
            ("局数", "right"),
            ("胜场", "right"),
            ("胜率", "right"),
            ("WLB", "right"),
            ("K/D/A", "right"),
            ("英雄伤害", "right"),
            ("承受伤害", "right"),
            ("治疗", "right"),
            ("推塔", "right"),
            ("XP", "right"),
        ]
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([c[0] for c in columns])
        for i, (_, align) in enumerate(columns):
            if align == "right":
                self.table.horizontalHeader().setSectionResizeMode(
                    i, QHeaderView.ResizeToContents
                )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        root.addWidget(self.table, 1)

        footer = QLabel(
            "<span style='color:#888;'>"
            "WLB = Wilson 95% 置信下界。WLB ≥ 50% 表示我们有 95% 把握真实胜率高于 50%；"
            "排序时用 WLB 而不是原始胜率，可以避免「5 局 5 胜」这种噪音排到第一。"
            "</span>"
        )
        footer.setTextFormat(Qt.RichText)
        footer.setWordWrap(True)
        root.addWidget(footer)

    def _reload(self) -> None:
        from ..db.store import _mode_clause  # noqa: F401  (just to confirm pkg shape)

        # Pull hero stats restricted to ARAM mode + grab extra metrics in
        # one query so we don't have to fan out per hero.
        rows = self.store.conn.execute("""
            SELECT pm.hero,
                   COUNT(*) AS games,
                   SUM(CASE WHEN pm.result = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(pm.kills)             AS k,
                   AVG(pm.deaths)            AS d,
                   AVG(pm.assists)           AS a,
                   AVG(pm.hero_damage)       AS hd,
                   AVG(pm.damage_taken)      AS dt,
                   AVG(pm.healing)           AS hl,
                   AVG(pm.structure_damage)  AS strd,
                   AVG(pm.experience_contribution) AS xp
            FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id
            WHERE r.mode = 'ARAM'
            GROUP BY pm.hero
        """).fetchall()

        min_games = self.min_games_spin.value()
        ranked = []
        for r in rows:
            g = int(r["games"])
            won = int(r["wins"] or 0)
            if g < min_games:
                continue
            wlb = wilson_lower_bound(won, g)
            ranked.append({
                "hero": r["hero"], "games": g, "wins": won,
                "wr": won / g if g else 0.0,
                "wlb": wlb,
                "k": float(r["k"] or 0), "d": float(r["d"] or 0),
                "a": float(r["a"] or 0),
                "hd": float(r["hd"] or 0), "dt": float(r["dt"] or 0),
                "hl": float(r["hl"] or 0), "strd": float(r["strd"] or 0),
                "xp": float(r["xp"] or 0),
            })

        sort_key = self.sort_combo.currentData()
        if sort_key == "wlb":
            ranked.sort(key=lambda x: -x["wlb"])
        elif sort_key == "wr":
            ranked.sort(key=lambda x: -x["wr"])
        elif sort_key == "games":
            ranked.sort(key=lambda x: -x["games"])
        elif sort_key == "hero":
            ranked.sort(key=lambda x: x["hero"])

        # DB summary row
        total_games = self.store.conn.execute(
            "SELECT COUNT(*) FROM replays WHERE mode='ARAM'"
        ).fetchone()[0]
        total_pm = self.store.conn.execute("""
            SELECT COUNT(*) FROM player_match pm
            JOIN replays r ON r.id = pm.replay_id WHERE r.mode='ARAM'
        """).fetchone()[0]
        self.summary.setText(
            f"数据库样本: {total_games} 局 ARAM (合计 {total_pm} 个英雄选用记录) · "
            f"展示 {len(ranked)} 个英雄 (≥ {min_games} 局)"
        )

        self.table.setRowCount(len(ranked))
        for i, r in enumerate(ranked):
            cells = [
                str(i + 1),
                r["hero"],
                str(r["games"]),
                str(r["wins"]),
                f"{r['wr']*100:.0f}%",
                f"{r['wlb']*100:.0f}%",
                f"{r['k']:.1f}/{r['d']:.1f}/{r['a']:.1f}",
                _fmt_k(r["hd"]),
                _fmt_k(r["dt"]),
                _fmt_k(r["hl"]),
                _fmt_k(r["strd"]),
                _fmt_k(r["xp"]),
            ]
            for j, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                if j == 1:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                else:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
                # Highlight strong picks (WLB ≥ 0.5) in green; weak (< 0.4) in red.
                wlb = r["wlb"]
                if wlb >= 0.50:
                    item.setForeground(Qt.green)
                elif wlb < 0.40:
                    item.setForeground(Qt.red)
                # Keep hero name readable regardless of color
                if j == 1 and wlb < 0.50:
                    item.setForeground(Qt.white)
                self.table.setItem(i, j, item)
