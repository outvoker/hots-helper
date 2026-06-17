"""Winrate-based talent build for one hero, with a mode toggle.

Opened by double-clicking a hero in the ranking board. Shows the
recommended talent per tier (highest confidence-adjusted win-rate) with
the other tier choices listed as alternatives. The mode toggle switches
between 天命乱斗 (ARAM) and 风暴联赛/快速 (Storm League + Quick Match),
which have distinct talent metas.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
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
from ..talent_build import build_talent_recommendation, normalize_mode_group
from ..talent_names import talent_label
from .theme import (
    BG_DEEP,
    BG_ELEVATED,
    BG_INPUT,
    GOLD,
    GOLD_BRIGHT,
    GOLD_DIM,
    GOOD,
    LINE,
    TEXT,
    TEXT_DIM,
    WARN,
)

_MODE_GROUPS = [
    ("standard", "ui.talents.mode_standard"),
    ("aram", "ui.talents.mode_aram"),
]


class TalentBuildDialog(QDialog):
    def __init__(
        self,
        store: Store,
        hero: str,
        *,
        mode_group: str = "standard",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.hero = hero
        self._mode_group = normalize_mode_group(mode_group)

        self.setWindowTitle(t("ui.talents.dialog_title", hero=hero))
        self.resize(520, 640)
        self.setStyleSheet(
            f"QDialog {{ background: {BG_DEEP}; color: {TEXT}; }}"
            f"QLabel {{ color: {TEXT}; }}"
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; padding:5px 12px; border-radius:4px; }}"
            f"QPushButton:checked {{ background:{GOLD_DIM}; color:{GOLD_BRIGHT};"
            f" border-color:{GOLD}; font-weight:600; }}"
            f"QPushButton:hover {{ border-color:{GOLD_DIM}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Header: title + mode toggle.
        head = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet(
            f"color:{GOLD}; font-size:14pt; font-weight:600;"
        )
        head.addWidget(self.title_label, 1)

        self._mode_group_btns = QButtonGroup(self)
        self._mode_group_btns.setExclusive(True)
        for value, label_key in _MODE_GROUPS:
            btn = QPushButton(t(label_key))
            btn.setCheckable(True)
            btn.setChecked(value == self._mode_group)
            btn.clicked.connect(lambda _c, v=value: self._set_mode(v))
            self._mode_group_btns.addButton(btn)
            head.addWidget(btn)
        root.addLayout(head)

        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color:{TEXT_DIM};")
        root.addWidget(self.summary)

        # Scrollable body of tiers.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        self._body = QVBoxLayout(host)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(8)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        self._render()

    def _set_mode(self, group: str) -> None:
        self._mode_group = normalize_mode_group(group)
        self._render()

    def _clear_body(self) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render(self) -> None:
        self._clear_body()
        self.title_label.setText(t("ui.talents.title", hero=self.hero))

        try:
            self.store.drop_read_snapshot()
        except Exception:
            pass

        build = build_talent_recommendation(
            self.store, self.hero, mode_group=self._mode_group
        )

        if not build.tiers:
            self.summary.setText(t("ui.talents.empty"))
            self._body.addStretch(1)
            return

        self.summary.setText(
            t(
                "ui.talents.summary",
                games=build.total_games,
                wr=f"{build.win_rate * 100:.0f}%",
            )
        )

        for tier in build.tiers:
            self._body.addWidget(self._tier_widget(tier))
        self._body.addStretch(1)

    def _tier_widget(self, tier) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background:{BG_ELEVATED}; border:1px solid {LINE};"
            f" border-left:3px solid {GOLD}; border-radius:8px; }}"
            f"QLabel {{ color:{TEXT}; border:none; }}"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(3)

        rec = tier.recommended
        if rec is not None:
            wr_color = (
                GOOD if rec.win_rate >= 0.5 else WARN if rec.win_rate < 0.45 else TEXT
            )
            head = QLabel(
                f"<span style='color:{GOLD};font-weight:600;'>T{tier.tier}</span> "
                f"<b>{talent_label(rec.talent)}</b> &nbsp;"
                f"<span style='color:{wr_color};'>"
                f"{t('ui.talents.winrate')} {rec.win_rate * 100:.0f}%</span> "
                f"<span style='color:{TEXT_DIM};'>"
                f"（{rec.wins}/{rec.games} · "
                f"{t('ui.talents.wilson')} {rec.wilson_lb * 100:.0f}%）</span>"
            )
            head.setTextFormat(Qt.RichText)
            head.setWordWrap(True)
            v.addWidget(head)

        alts = [c for c in tier.choices if rec is None or c.talent != rec.talent]
        if alts:
            alt_html = " · ".join(
                f"{talent_label(c.talent)} "
                f"<span style='color:{TEXT_DIM};'>{c.win_rate * 100:.0f}% "
                f"/ {c.games}{t('ui.talents.games_suffix')}</span>"
                for c in alts
            )
            alt = QLabel(alt_html)
            alt.setTextFormat(Qt.RichText)
            alt.setWordWrap(True)
            alt.setStyleSheet(f"color:{TEXT_DIM}; font-size:9pt;")
            v.addWidget(alt)
        return frame
