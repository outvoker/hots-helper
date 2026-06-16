"""Modal dialog to choose the squad roster.

We no longer assume the squad is a fixed five players. The user picks
who counts as "us" from a checkbox list of frequent players; the choice
is persisted in :class:`~hots_helper.config.Config` (``squad_handles`` +
``squad_configured``) and drives both the weekly report scope and the
rankings highlight.

The candidate list comes from :meth:`Store.squad_candidates` (most
games first). A search box filters it for big databases, and the
server's frequency heuristic seeds the initial selection so a
first-time user can usually just click 保存.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..db import Store
from ..i18n import t
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


class SquadPickerDialog(QDialog):
    """Pick squad members; persists to config on 保存.

    Returns ``QDialog.Accepted`` after a successful save so callers can
    refresh. The chosen handles are written to ``config`` (and saved to
    disk) before ``accept()``.
    """

    def __init__(self, store: Store, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.config = config
        self._checks: dict[str, QCheckBox] = {}

        self.setWindowTitle(t("ui.squad.dialog_title"))
        self.resize(560, 640)
        self.setStyleSheet(
            f"QDialog {{ background: {BG_DEEP}; color: {TEXT}; }}"
            f"QLabel {{ color: {TEXT}; }}"
            f"QLineEdit {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; border-radius:4px; padding:5px 8px; }}"
            f"QLineEdit:focus {{ border-color:{GOLD_DIM}; }}"
            f"QCheckBox {{ color:{TEXT}; padding:4px 2px; }}"
            f"QPushButton {{ background:{BG_INPUT}; color:{TEXT};"
            f" border:1px solid {LINE}; padding:6px 14px; border-radius:4px; }}"
            f"QPushButton:hover {{ border-color:{GOLD_DIM}; color:{GOLD_BRIGHT}; }}"
            f"QPushButton:disabled {{ color:{TEXT_DIM}; border-color:{LINE}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel(t("ui.squad.heading"))
        title.setStyleSheet(f"color:{GOLD}; font-size:14pt; font-weight:600;")
        root.addWidget(title)

        sub = QLabel(t("ui.squad.subtitle"))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{TEXT_DIM};")
        root.addWidget(sub)

        self.search = QLineEdit()
        self.search.setPlaceholderText(t("ui.squad.search_ph"))
        self.search.textChanged.connect(self._apply_filter)
        root.addWidget(self.search)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color:{GOLD}; font-weight:600;")
        root.addWidget(self.count_label)

        # Scrollable checkbox list.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(4, 4, 4, 4)
        self._list.setSpacing(2)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        # Action row.
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_btn = QPushButton(t("ui.squad.cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        actions.addWidget(self.cancel_btn)
        self.save_btn = QPushButton(t("ui.squad.save"))
        self.save_btn.setStyleSheet(
            f"QPushButton {{ background:{GOLD_DIM}; color:{GOLD_BRIGHT};"
            f" border:1px solid {GOLD}; padding:6px 16px; border-radius:4px;"
            f" font-weight:600; }}"
            f"QPushButton:hover {{ background:{GOLD}; color:{BG_DEEP}; }}"
            f"QPushButton:disabled {{ background:{BG_INPUT}; color:{TEXT_DIM};"
            f" border-color:{LINE}; }}"
        )
        self.save_btn.clicked.connect(self._save)
        actions.addWidget(self.save_btn)
        root.addLayout(actions)

        self._populate()

    # --- build -------------------------------------------------------------

    def _populate(self) -> None:
        candidates = self.store.squad_candidates()
        # Pre-check: existing config if any, else the heuristic suggestion.
        if self.config.squad_configured:
            preset = set(self.config.squad_handles)
        else:
            preset = set(self.store.squad_handles())

        # Make sure already-selected handles that fell below the candidate
        # threshold still appear (so re-editing never silently drops one).
        known = {c["toon_handle"] for c in candidates}
        for handle in preset - known:
            row = self.store.conn.execute(
                "SELECT display_name FROM players WHERE toon_handle = ?",
                (handle,),
            ).fetchone()
            candidates.append({
                "toon_handle": handle,
                "display_name": (row["display_name"] if row else handle) or handle,
                "games": 0,
            })

        for c in candidates:
            handle = c["toon_handle"]
            cb = QCheckBox(
                t("ui.squad.row", name=c["display_name"], games=c["games"])
            )
            cb.setChecked(handle in preset)
            cb.toggled.connect(self._update_count)
            self._checks[handle] = cb
            self._list.addWidget(cb)
        self._list.addStretch(1)
        self._update_count()

    # --- interaction -------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        q = text.strip().casefold()
        for handle, cb in self._checks.items():
            label = cb.text().casefold()
            cb.setVisible(not q or q in label or q in handle.casefold())

    def _selected(self) -> list[str]:
        return [h for h, cb in self._checks.items() if cb.isChecked()]

    def _update_count(self) -> None:
        n = len(self._selected())
        self.count_label.setText(t("ui.squad.count", n=n))
        self.save_btn.setEnabled(n > 0)

    def _save(self) -> None:
        self.config.squad_handles = self._selected()
        self.config.squad_configured = True
        self.config.save()
        self.accept()
