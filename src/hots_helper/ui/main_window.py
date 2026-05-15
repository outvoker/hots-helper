"""Main window of the HotS Helper desktop app."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, default_hots_replay_roots, discover_replay_dirs
from ..db import Store
from ..i18n import available_languages, on_change as on_lang_change, set_language, t
from ..sync import make_sync
from ..sync_defaults import DEFAULT_SUPABASE_ANON_KEY, DEFAULT_SUPABASE_URL
from ..watcher.ingest import IngestResult
from .capture_progress import CaptureProgressDialog
from .hotkey import HotkeyManager
from .popup import PopupWindow
from .theme import (
    BG_DEEP,
    BG_ELEVATED,
    BG_HOVER,
    BG_INPUT,
    GOLD,
    GOLD_BRIGHT,
    GOLD_DIM,
    LINE,
    TEXT,
    TEXT_DIM,
)
from .workers import HotkeyShotResult, HotkeyWorker, ScanWorker, SyncWorker, WatchWorker


def _qt_seq_to_pynput(seq: QKeySequence) -> str:
    """Best-effort conversion of a Qt shortcut string into pynput form."""
    text = seq.toString(QKeySequence.PortableText)
    if not text:
        return ""
    out: list[str] = []
    for part in text.split("+"):
        p = part.strip()
        low = p.lower()
        if low in {"ctrl", "alt", "shift", "meta", "cmd"}:
            # pynput uses <ctrl>, <alt>, <shift>, <cmd>. Qt on macOS maps
            # Ctrl<->Cmd; we keep whatever the user pressed.
            key = "cmd" if low == "meta" else low
            out.append(f"<{key}>")
        elif len(p) == 1:
            out.append(p.lower())
        elif low.startswith("f") and low[1:].isdigit():
            out.append(f"<{low}>")
        else:
            out.append(f"<{low}>")
    return "+".join(out)


def _pynput_to_qt_seq(combo: str) -> QKeySequence:
    """Reverse mapping so the UI widget can display the configured hotkey."""
    if not combo:
        return QKeySequence()
    parts: list[str] = []
    for part in combo.split("+"):
        token = part.strip("<> ").lower()
        if token in {"ctrl", "alt", "shift"}:
            parts.append(token.capitalize())
        elif token == "cmd":
            parts.append("Meta")
        elif len(token) == 1:
            parts.append(token.upper())
        else:
            parts.append(token.upper())
    return QKeySequence("+".join(parts))


class MainWindow(QMainWindow):
    def __init__(self, store: Store, config: Config) -> None:
        super().__init__()
        self.store = store
        self.config = config
        # Apply persisted language *before* we build any widgets.
        set_language(getattr(config, "language", "zh") or "zh")
        self.resize(1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(12)

        # --- Top bar: language picker -----------------------------------------
        top_bar = QHBoxLayout()
        top_bar.addStretch(1)
        self.lang_label = QLabel()
        top_bar.addWidget(self.lang_label)
        self.lang_combo = QComboBox()
        for code, label in available_languages():
            self.lang_combo.addItem(label, code)
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == self.config.language:
                self.lang_combo.setCurrentIndex(i)
                break
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        top_bar.addWidget(self.lang_combo)
        root.addLayout(top_bar)

        # =====================================================================
        # PRIMARY FEATURES — two big "hero cards" side by side. These are the
        # things squad members open the app for; everything else is config.
        # =====================================================================
        primary_row = QHBoxLayout()
        primary_row.setSpacing(12)
        primary_row.addWidget(self._build_bp_card(), 1)
        primary_row.addWidget(self._build_ranking_card(), 1)
        root.addLayout(primary_row)

        # =====================================================================
        # SETTINGS — collapsible group containing replay folders, scan/watch,
        # cloud sync, etc. Closed by default so the hero cards have room.
        # =====================================================================
        self.settings_toggle = QToolButton()
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(False)
        self.settings_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.settings_toggle.setArrowType(Qt.NoArrow)
        self.settings_toggle.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none;"
            f" color: {TEXT_DIM}; padding: 6px 4px;"
            f" font-weight: 600; letter-spacing: 0.5px; }}"
            f"QToolButton:hover {{ color: {GOLD_BRIGHT}; }}"
            f"QToolButton:checked {{ color: {GOLD}; }}"
        )
        self.settings_toggle.toggled.connect(self._on_settings_toggled)
        root.addWidget(self.settings_toggle)

        self.settings_panel = QWidget()
        sp = QVBoxLayout(self.settings_panel)
        sp.setContentsMargins(0, 0, 0, 0)
        sp.setSpacing(10)

        # --- Recording roots section ------------------------------------------
        self.roots_box = QGroupBox()
        rb = QVBoxLayout(self.roots_box)
        self.roots_list = QListWidget()
        self.roots_list.setMaximumHeight(120)
        rb.addWidget(self.roots_list)
        btns = QHBoxLayout()
        self.add_btn = QPushButton()
        self.add_btn.clicked.connect(self._add_root)
        self.remove_btn = QPushButton()
        self.remove_btn.clicked.connect(self._remove_root)
        self.detect_btn = QPushButton()
        self.detect_btn.clicked.connect(self._auto_detect)
        btns.addWidget(self.add_btn)
        btns.addWidget(self.remove_btn)
        btns.addWidget(self.detect_btn)
        btns.addStretch(1)
        rb.addLayout(btns)
        self.effective_label = QLabel()
        self.effective_label.setStyleSheet(f"color:{TEXT_DIM};")
        rb.addWidget(self.effective_label)
        sp.addWidget(self.roots_box)

        # --- Actions section --------------------------------------------------
        self.actions_box = QGroupBox()
        ab = QHBoxLayout(self.actions_box)
        self.scan_btn = QPushButton()
        self.scan_btn.clicked.connect(self._start_scan)
        ab.addWidget(self.scan_btn)
        self.watch_chk = QCheckBox()
        self.watch_chk.setChecked(self.config.auto_watch)
        self.watch_chk.stateChanged.connect(self._toggle_watch)
        ab.addWidget(self.watch_chk)
        ab.addStretch(1)
        self.stats_label = QLabel("…")
        ab.addWidget(self.stats_label)
        sp.addWidget(self.actions_box)

        # --- Cloud sync section -----------------------------------------------
        self.sync_box = QGroupBox()
        sb_outer = QVBoxLayout(self.sync_box)
        # Header row: status + sync now + auto + override
        sb_top = QHBoxLayout()
        self.sync_now_btn = QPushButton()
        self.sync_now_btn.clicked.connect(lambda: self._start_sync(force=True))
        sb_top.addWidget(self.sync_now_btn)
        self.sync_auto_chk = QCheckBox()
        self.sync_auto_chk.setChecked(self.config.sync_auto)
        self.sync_auto_chk.stateChanged.connect(self._toggle_sync_auto)
        sb_top.addWidget(self.sync_auto_chk)
        sb_top.addStretch(1)
        self.sync_status_label = QLabel()
        self.sync_status_label.setStyleSheet("color:#9ad;")
        sb_top.addWidget(self.sync_status_label)
        sb_outer.addLayout(sb_top)

        # Override row — only relevant when the user wants to use a custom
        # Supabase project. Hidden by default if the embedded defaults are
        # present and the user hasn't overridden them.
        self.sync_override_widget = QWidget()
        sb_override = QHBoxLayout(self.sync_override_widget)
        sb_override.setContentsMargins(0, 0, 0, 0)
        self.sync_url_label = QLabel()
        sb_override.addWidget(self.sync_url_label)
        self.sync_url_edit = QLineEdit()
        self.sync_url_edit.setText(self.config.supabase_url)
        sb_override.addWidget(self.sync_url_edit, 1)
        self.sync_key_label = QLabel()
        sb_override.addWidget(self.sync_key_label)
        self.sync_key_edit = QLineEdit()
        self.sync_key_edit.setEchoMode(QLineEdit.Password)
        self.sync_key_edit.setText(self.config.supabase_anon_key)
        sb_override.addWidget(self.sync_key_edit, 1)
        self.sync_save_btn = QPushButton()
        self.sync_save_btn.clicked.connect(self._save_sync_credentials)
        sb_override.addWidget(self.sync_save_btn)
        sb_outer.addWidget(self.sync_override_widget)
        # Hidden by default; toggle below.
        self.sync_override_widget.setVisible(False)

        # Override toggle button — small affordance for the rare user who
        # wants to point at a private Supabase project.
        sb_bot = QHBoxLayout()
        self.sync_override_btn = QPushButton()
        self.sync_override_btn.setCheckable(True)
        self.sync_override_btn.toggled.connect(
            self.sync_override_widget.setVisible
        )
        sb_bot.addWidget(self.sync_override_btn)
        sb_bot.addStretch(1)
        sb_outer.addLayout(sb_bot)
        sp.addWidget(self.sync_box)

        # Settings panel sits below the toggle, expandable on click.
        self.settings_panel.setVisible(False)
        root.addWidget(self.settings_panel)

        # --- Log --------------------------------------------------------------
        self.log_box = QGroupBox()
        lb = QVBoxLayout(self.log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        lb.addWidget(self.log)
        root.addWidget(self.log_box)
        root.addStretch(1)

        # Bottom-right vanity credit. The squad has been asking for
        # this; just an italic gold-dim line, right-aligned so it
        # doesn't fight the rest of the layout.
        credit_row = QHBoxLayout()
        credit_row.addStretch(1)
        self.credit_label = QLabel()
        self.credit_label.setStyleSheet(
            f"color: {GOLD_DIM}; font-style: italic;"
            f" font-size: 9pt; padding: 0 4px;"
        )
        credit_row.addWidget(self.credit_label)
        root.addLayout(credit_row)

        # --- Runtime: store, workers, hotkey, popup --------------------------
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        # Lazily created when the user first clicks the ARAM button.
        self._aram_dialog = None
        # Cloud sync runtime
        self._sync_thread: QThread | None = None
        self._sync_worker: SyncWorker | None = None
        self._sync_busy = False
        # If the user hasn't set their own credentials, fall back to the
        # built-in defaults shipped with the app. ``has_defaults()`` returns
        # False on dev builds, so this path is a no-op when defaults are
        # blank.
        eff_url = self.config.supabase_url or DEFAULT_SUPABASE_URL
        eff_key = self.config.supabase_anon_key or DEFAULT_SUPABASE_ANON_KEY
        self._cloud_sync = make_sync(self.store, eff_url, eff_key)

        self.watch_worker = WatchWorker(self.store)
        self.watch_worker.ingested.connect(self._on_watch_ingested)
        self.watch_worker.started_watching.connect(self._on_watch_started)
        self.watch_worker.stopped.connect(
            lambda: self._log(t("ui.main.watcher_stopped"))
        )

        # Apply translations to all UI text.
        self._retranslate()
        on_lang_change(lambda _code: self._retranslate())

        # Hotkey-triggered screenshot+OCR runs on its own QThread so the UI
        # stays responsive even when Windows OCR takes a couple of seconds.
        self._hotkey_thread: QThread | None = None
        self._hotkey_worker: HotkeyWorker | None = None
        self._hotkey_busy = False

        self.hotkey = HotkeyManager()
        self.hotkey.triggered.connect(self._on_hotkey)
        self.hotkey.error.connect(lambda msg: self._log(f"[hotkey] {msg}"))

        self.popup = PopupWindow(self.store)
        self.capture_progress = CaptureProgressDialog(self)

        # Seed UI from config.
        self._refresh_roots()
        self._refresh_stats()
        if self.config.hotkey:
            self.hotkey.set_hotkey(self.config.hotkey)
            self._log(t("ui.main.hotkey_registered", combo=self.config.hotkey))
        if self.watch_chk.isChecked():
            self._start_watching()
        # Kick off a startup sync if the user has configured cloud creds.
        if self._cloud_sync is not None and self.config.sync_auto:
            self._start_sync(force=False)

    # --- primary feature cards ---------------------------------------------

    def _build_bp_card(self) -> QFrame:
        """The big "BP intelligence" card: hotkey config + test button.

        Visually the most prominent thing on the main window since the
        hotkey-driven OCR analysis is the app's primary feature.
        """
        card = QFrame()
        card.setObjectName("primaryCard")
        card.setStyleSheet(
            f"QFrame#primaryCard {{"
            f" background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            f"   stop:0 {BG_ELEVATED}, stop:1 {BG_DEEP});"
            f" border: 1px solid {GOLD_DIM};"
            f" border-radius: 10px;"
            f"}}"
            f"QFrame#primaryCard:hover {{ border-color: {GOLD}; }}"
        )

        v = QVBoxLayout(card)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)

        # Eyebrow / title row.
        head = QHBoxLayout()
        head.setSpacing(10)
        eyebrow = QLabel("⚡")
        eyebrow.setStyleSheet(f"color: {GOLD_BRIGHT}; font-size: 22pt;")
        head.addWidget(eyebrow)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.bp_title = QLabel()
        self.bp_title.setStyleSheet(
            f"color: {GOLD}; font-size: 16pt; font-weight: 700;"
            f" letter-spacing: 0.5px;"
        )
        title_box.addWidget(self.bp_title)
        self.bp_subtitle = QLabel()
        self.bp_subtitle.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 9pt;"
        )
        self.bp_subtitle.setWordWrap(True)
        title_box.addWidget(self.bp_subtitle)
        head.addLayout(title_box, 1)
        v.addLayout(head)

        # Hotkey row.
        hk_row = QHBoxLayout()
        self.shortcut_label = QLabel()
        hk_row.addWidget(self.shortcut_label)
        self.hotkey_edit = QKeySequenceEdit()
        self.hotkey_edit.setKeySequence(_pynput_to_qt_seq(self.config.hotkey))
        hk_row.addWidget(self.hotkey_edit, 1)
        self.apply_btn = QPushButton()
        self.apply_btn.clicked.connect(self._apply_hotkey)
        hk_row.addWidget(self.apply_btn)
        v.addLayout(hk_row)

        # Two CTAs side by side: live capture (real screenshot, what the
        # hotkey actually does — gold/primary), and sample capture (uses
        # a bundled BP screenshot, no game required — secondary). Split
        # because the previous single "测试 (无需对局)" button was misleading
        # — it also took a real screenshot, which on a desktop without a
        # HotS game running just OCR'd the user's email/Discord/etc.
        cta_row = QHBoxLayout()
        cta_row.setSpacing(10)
        self.capture_btn = QPushButton()
        self.capture_btn.clicked.connect(self._trigger_real_capture)
        self.capture_btn.setProperty("variant", "primary")
        self.capture_btn.setMinimumHeight(40)
        cta_row.addWidget(self.capture_btn, 2)
        self.sample_btn = QPushButton()
        self.sample_btn.clicked.connect(self._trigger_sample_capture)
        self.sample_btn.setMinimumHeight(40)
        cta_row.addWidget(self.sample_btn, 1)
        v.addLayout(cta_row)
        for b in (self.capture_btn, self.sample_btn):
            b.style().unpolish(b)
            b.style().polish(b)

        return card

    def _build_ranking_card(self) -> QFrame:
        """The hero strength ranking entry point — a peer of the BP card."""
        card = QFrame()
        card.setObjectName("primaryCard")
        card.setStyleSheet(
            f"QFrame#primaryCard {{"
            f" background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            f"   stop:0 {BG_ELEVATED}, stop:1 {BG_DEEP});"
            f" border: 1px solid {GOLD_DIM};"
            f" border-radius: 10px;"
            f"}}"
            f"QFrame#primaryCard:hover {{ border-color: {GOLD}; }}"
        )

        v = QVBoxLayout(card)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        eyebrow = QLabel("📊")
        eyebrow.setStyleSheet(f"color: {GOLD_BRIGHT}; font-size: 22pt;")
        head.addWidget(eyebrow)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.ranking_title = QLabel()
        self.ranking_title.setStyleSheet(
            f"color: {GOLD}; font-size: 16pt; font-weight: 700;"
            f" letter-spacing: 0.5px;"
        )
        title_box.addWidget(self.ranking_title)
        self.ranking_subtitle = QLabel()
        self.ranking_subtitle.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 9pt;"
        )
        self.ranking_subtitle.setWordWrap(True)
        title_box.addWidget(self.ranking_subtitle)
        head.addLayout(title_box, 1)
        v.addLayout(head)

        # Two big mode buttons.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.sl_btn = QPushButton()
        self.sl_btn.clicked.connect(lambda: self._show_hero_ranking("Storm League"))
        self.sl_btn.setMinimumHeight(40)
        self.sl_btn.setProperty("variant", "primary")
        btn_row.addWidget(self.sl_btn)
        self.aram_btn = QPushButton()
        self.aram_btn.clicked.connect(lambda: self._show_hero_ranking("ARAM"))
        self.aram_btn.setMinimumHeight(40)
        btn_row.addWidget(self.aram_btn)
        v.addLayout(btn_row)
        for b in (self.sl_btn, self.aram_btn):
            b.style().unpolish(b)
            b.style().polish(b)
        # Trailing flexible space so the card matches the BP card's height.
        v.addStretch(1)

        return card

    def _on_settings_toggled(self, on: bool) -> None:
        self.settings_panel.setVisible(on)
        self.settings_toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.settings_toggle.setText(
            ("▾ " if on else "▸ ") + t("ui.main.settings")
        )
        # Hide the ToolButton's own arrow icon now that we're putting an
        # arrow glyph in the text — keeping both looks redundant.
        self.settings_toggle.setArrowType(Qt.NoArrow)

    # --- i18n ---------------------------------------------------------------

    def _retranslate(self) -> None:
        """Push the current locale's strings into every visible widget.

        Called once after __init__ wires up widgets, then again whenever
        the user picks a new language. Anything that contains user-facing
        text needs to be re-set here.
        """
        self.setWindowTitle(t("ui.app.title"))
        self.lang_label.setText(t("ui.main.language") + ":")

        self.roots_box.setTitle(t("ui.main.replay_folders"))
        self.add_btn.setText(t("ui.main.add_folder"))
        self.remove_btn.setText(t("ui.main.remove_selected"))
        self.detect_btn.setText(t("ui.main.auto_detect"))

        self.actions_box.setTitle(t("ui.main.ingest"))
        self.scan_btn.setText(t("ui.main.start_scan"))
        self.watch_chk.setText(t("ui.main.watch"))

        # Primary feature cards.
        self.bp_title.setText(t("ui.main.bp_card_title"))
        self.bp_subtitle.setText(t("ui.main.bp_card_subtitle"))
        self.shortcut_label.setText(t("ui.main.shortcut"))
        self.apply_btn.setText(t("ui.main.apply"))
        self.capture_btn.setText(t("ui.main.bp_capture_cta"))
        self.capture_btn.setToolTip(t("ui.main.bp_capture_tip"))
        self.sample_btn.setText(t("ui.main.bp_sample_cta"))
        self.sample_btn.setToolTip(t("ui.main.bp_sample_tip"))

        self.ranking_title.setText(t("ui.main.ranking_card_title"))
        self.ranking_subtitle.setText(t("ui.main.ranking_card_subtitle"))

        self.settings_toggle.setText(
            "▾ " + t("ui.main.settings") if self.settings_toggle.isChecked()
            else "▸ " + t("ui.main.settings")
        )

        self.sync_box.setTitle(t("ui.main.sync_section"))
        self.sync_url_label.setText(t("ui.main.sync_url"))
        self.sync_key_label.setText(t("ui.main.sync_key"))
        self.sync_url_edit.setPlaceholderText(t("ui.main.sync_url_placeholder"))
        self.sync_key_edit.setPlaceholderText(t("ui.main.sync_key_placeholder"))
        self.sync_save_btn.setText(t("ui.main.sync_save"))
        self.sync_now_btn.setText(t("ui.main.sync_now"))
        self.sync_auto_chk.setText(t("ui.main.sync_auto"))
        if hasattr(self, "sync_override_btn"):
            self.sync_override_btn.setText(t("ui.main.sync_override_btn"))
        if hasattr(self, "sync_status_label"):
            if self._cloud_sync is None:
                self.sync_status_label.setText(t("ui.main.sync_disabled"))
            elif (
                not self.config.supabase_url
                and not self.config.supabase_anon_key
            ):
                # The user hasn't typed anything; we're using the embedded
                # defaults. Tell them that with a friendlier label than the
                # generic "Sync done: pushed 0…" first impression.
                cur = self.sync_status_label.text()
                if not cur or cur == t("ui.main.sync_disabled"):
                    self.sync_status_label.setText(t("ui.main.sync_using_defaults"))

        self.sl_btn.setText(t("ui.main.sl_ranking"))
        self.sl_btn.setToolTip(t("ui.main.sl_ranking_tip"))
        self.aram_btn.setText(t("ui.main.aram_ranking"))
        self.aram_btn.setToolTip(t("ui.main.aram_ranking_tip"))

        self.log_box.setTitle(t("ui.main.activity"))
        self.credit_label.setText(t("ui.main.credit"))

        # Refresh derived labels that include translated text.
        self._refresh_roots()
        self._refresh_stats()

    def _on_language_changed(self) -> None:
        code = self.lang_combo.currentData()
        if not code:
            return
        set_language(code)
        self.config.language = code
        self.config.save()

    # --- logging -------------------------------------------------------------

    def _log(self, msg: str) -> None:
        self.log.appendPlainText(msg)

    # --- recording roots -----------------------------------------------------

    def _refresh_roots(self) -> None:
        self.roots_list.clear()
        for r in self.config.recording_roots:
            self.roots_list.addItem(r)
        effective = self.config.effective_replay_dirs()
        if effective:
            self.effective_label.setText(
                t("ui.main.folders_resolved", n=len(effective)) + "\n"
                + "\n".join(f"  • {d}" for d in effective)
            )
        else:
            self.effective_label.setText(t("ui.main.no_folders_resolved"))

    def _add_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self, t("ui.main.replay_folders"))
        if d:
            if d not in self.config.recording_roots:
                self.config.recording_roots.append(d)
                self.config.save()
                self._refresh_roots()
                self._log(t("ui.main.added_folder", path=d))

    def _remove_root(self) -> None:
        for item in self.roots_list.selectedItems():
            self.config.recording_roots.remove(item.text())
        self.config.save()
        self._refresh_roots()

    def _auto_detect(self) -> None:
        found_any = False
        for root in default_hots_replay_roots():
            if root.exists() and str(root) not in self.config.recording_roots:
                self.config.recording_roots.append(str(root))
                found_any = True
        if found_any:
            self.config.save()
            self._refresh_roots()
            self._log(t("ui.main.autodetect_added"))
        else:
            self._log(t("ui.main.autodetect_none"))

    # --- scan ----------------------------------------------------------------

    def _start_scan(self) -> None:
        if self._scan_thread is not None:
            return
        dirs = self.config.effective_replay_dirs()
        if not dirs:
            QMessageBox.warning(
                self,
                t("ui.main.no_folders_warn_title"),
                t("ui.main.no_folders_warn_body"),
            )
            return
        self.scan_btn.setEnabled(False)
        self._scan_thread = QThread(self)
        self._scan_worker = ScanWorker(self.store, dirs)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._cleanup_scan_thread)
        self._scan_thread.start()
        self._log(t("ui.main.scanning", n=len(dirs)))

    def _cleanup_scan_thread(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.deleteLater()
            self._scan_worker = None
        if self._scan_thread is not None:
            self._scan_thread.deleteLater()
            self._scan_thread = None
        self.scan_btn.setEnabled(True)

    def _on_scan_progress(self, result: IngestResult) -> None:
        if result.error:
            self._log(f"[error] {result.path.name}: {result.error}")
        elif result.inserted:
            self._log(f"[+] {result.path.name}")
        elif result.reason == "match-dup":
            self._log(f"[~] {result.path.name}  (duplicate perspective of an existing match)")

    def _on_scan_finished(self, new: int, skipped: int, errors: int) -> None:
        self._log(t("ui.main.scan_done", new=new, dup=skipped, err=errors))
        self._refresh_stats()
        if new > 0:
            self._start_sync(force=False)

    # --- watcher -------------------------------------------------------------

    def _toggle_watch(self, state: int) -> None:
        self.config.auto_watch = bool(state)
        self.config.save()
        if state:
            self._start_watching()
        else:
            self.watch_worker.stop()

    def _start_watching(self) -> None:
        dirs = self.config.effective_replay_dirs()
        if not dirs:
            self._log("Watcher: no folders configured.")
            return
        self.watch_worker.start(dirs)

    def _on_watch_started(self, dirs: list) -> None:
        self._log(t("ui.main.watcher_active", n=len(dirs)))

    def _on_watch_ingested(self, result: IngestResult) -> None:
        if result.error:
            self._log(f"[watch error] {result.path.name}: {result.error}")
        elif result.inserted:
            self._log(f"[watch +] {result.path.name}")
            # Push the freshly-ingested replay up.
            self._start_sync(force=False)
        elif result.reason == "match-dup":
            self._log(f"[watch ~] {result.path.name}  (same match as one already in DB)")
        self._refresh_stats()

    # --- hotkey --------------------------------------------------------------

    def _apply_hotkey(self) -> None:
        combo = _qt_seq_to_pynput(self.hotkey_edit.keySequence())
        if not combo:
            QMessageBox.warning(
                self,
                t("ui.main.invalid_hotkey_title"),
                t("ui.main.invalid_hotkey_body"),
            )
            return
        self.config.hotkey = combo
        self.config.save()
        self.hotkey.set_hotkey(combo)
        self._log(t("ui.main.hotkey_set", combo=combo))

    def _trigger_real_capture(self) -> None:
        """Run the real screenshot pipeline (same as pressing the hotkey)."""
        self._on_hotkey(sample_path=None)

    def _trigger_sample_capture(self) -> None:
        """Run the pipeline against the bundled sample BP image so the
        user can preview the popup without being in a draft."""
        from .assets import sample_bp_screenshot
        sample = sample_bp_screenshot()
        if sample is None:
            QMessageBox.warning(
                self,
                t("ui.main.sample_missing_title"),
                t("ui.main.sample_missing_body"),
            )
            return
        self._on_hotkey(sample_path=sample)

    # --- cloud sync ---------------------------------------------------------

    def _save_sync_credentials(self) -> None:
        url = self.sync_url_edit.text().strip()
        key = self.sync_key_edit.text().strip()
        # Allow blank/blank to disable, otherwise require both.
        if (bool(url) ^ bool(key)):
            QMessageBox.warning(
                self,
                t("ui.main.sync_save_warn_title"),
                t("ui.main.sync_save_warn_body"),
            )
            return
        self.config.supabase_url = url
        self.config.supabase_anon_key = key
        self.config.save()
        self._cloud_sync = make_sync(self.store, url, key)
        if self._cloud_sync is None:
            self.sync_status_label.setText(t("ui.main.sync_disabled"))
        else:
            self._start_sync(force=True)

    def _toggle_sync_auto(self, state: int) -> None:
        self.config.sync_auto = bool(state)
        self.config.save()

    def _start_sync(self, *, force: bool) -> None:
        if self._cloud_sync is None:
            self.sync_status_label.setText(t("ui.main.sync_disabled"))
            return
        if self._sync_busy:
            return
        if not force and not self.config.sync_auto:
            return
        self._sync_busy = True
        self.sync_status_label.setText(t("ui.main.sync_running"))

        thread = QThread(self)
        worker = SyncWorker(self._cloud_sync)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_sync_progress)
        worker.finished.connect(self._on_sync_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_sync_thread)
        self._sync_thread = thread
        self._sync_worker = worker
        thread.start()

    def _on_sync_progress(self, msg: str) -> None:
        self._log(t("ui.main.sync_progress", msg=msg))
        self.sync_status_label.setText(t("ui.main.sync_progress", msg=msg))

    def _on_sync_finished(self, result) -> None:
        for err in result.errors:
            self._log(f"[sync error] {err}")
        text = t(
            "ui.main.sync_done",
            pushed=result.total_pushed, pulled=result.total_pulled,
        )
        if result.errors:
            text += " · " + t("ui.main.sync_errors", n=len(result.errors))
        self.sync_status_label.setText(text)
        self._log(text)
        # Refresh the DB stats label since pulls may have added rows.
        self._refresh_stats()

    def _cleanup_sync_thread(self) -> None:
        if self._sync_worker is not None:
            self._sync_worker.deleteLater()
            self._sync_worker = None
        if self._sync_thread is not None:
            self._sync_thread.deleteLater()
            self._sync_thread = None
        self._sync_busy = False

    def _show_hero_ranking(self, mode: str = "ARAM") -> None:
        """Open the hero-strength dialog focused on the given mode.

        Lazy-init and reused across opens; switching modes inside the
        dialog also works via the dropdown.
        """
        if not hasattr(self, "_aram_dialog") or self._aram_dialog is None:
            from .aram import HeroRankingDialog
            self._aram_dialog = HeroRankingDialog(
                self.store, parent=self, default_mode=mode,
            )
        else:
            # Switch the open dialog to the requested mode.
            for i in range(self._aram_dialog.mode_combo.count()):
                if self._aram_dialog.mode_combo.itemData(i) == mode:
                    self._aram_dialog.mode_combo.setCurrentIndex(i)
                    break
        self._aram_dialog.show()
        self._aram_dialog.raise_()
        self._aram_dialog.activateWindow()

    def _on_hotkey(self, sample_path: Path | None = None) -> None:
        # Reentry guard: if the user spams the hotkey while OCR is running
        # we'd queue up multiple worker threads and confuse winrt.
        if self._hotkey_busy:
            self._log(t("ui.main.hotkey_busy"))
            return
        self._hotkey_busy = True
        self._log(t("ui.main.capturing_screenshot"))
        # Show the marketing-y progress card so the user has feedback
        # during the 1–3 s OCR pipeline. Anchor it to the main window
        # for now; the popup itself takes over once we have results.
        self.capture_progress.start(anchor=self)

        thread = QThread(self)
        worker = HotkeyWorker(sample_path=sample_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._log)
        worker.progress.connect(self.capture_progress.update_substatus)
        worker.finished.connect(self._on_hotkey_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_hotkey_thread)
        self._hotkey_thread = thread
        self._hotkey_worker = worker
        thread.start()

    def _cleanup_hotkey_thread(self) -> None:
        if self._hotkey_worker is not None:
            self._hotkey_worker.deleteLater()
            self._hotkey_worker = None
        if self._hotkey_thread is not None:
            self._hotkey_thread.deleteLater()
            self._hotkey_thread = None
        self._hotkey_busy = False

    def _on_hotkey_finished(self, result: HotkeyShotResult) -> None:
        for line in result.log_lines:
            self._log(line)

        map_name = result.map_name or None
        allies = result.ally_names if any(result.ally_names) else None
        enemies = result.enemy_names if any(result.enemy_names) else None
        ally_conf = result.ally_confidences if any(result.ally_confidences) else None
        enemy_conf = result.enemy_confidences if any(result.enemy_confidences) else None

        if result.drafter:
            self._log(t("ui.main.drafting", name=result.drafter))

        # Got something useful → success state; otherwise keep the
        # progress card around long enough for the user to read the
        # error before it fades.
        ok = bool(result.screenshot_path) and (allies or enemies or map_name)
        self.capture_progress.finish(ok=bool(ok))

        self.popup.show_for_map(
            map_name,
            ally_names=allies,
            enemy_names=enemies,
            ally_confidences=ally_conf,
            enemy_confidences=enemy_conf,
            drafter=result.drafter or None,
            screenshot_path=result.screenshot_path,
        )

    # --- stats ---------------------------------------------------------------

    def _refresh_stats(self) -> None:
        try:
            self.stats_label.setText(
                t("ui.main.db_summary",
                  replays=self.store.count_replays(),
                  players=self.store.count_players())
            )
        except Exception as e:
            self.stats_label.setText(t("ui.main.db_error", e=e))

    # --- close ---------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.watch_worker.stop()
        self.hotkey.stop()
        if self._scan_thread is not None:
            self._scan_thread.quit()
            self._scan_thread.wait(5000)
        if self._hotkey_thread is not None:
            self._hotkey_thread.quit()
            self._hotkey_thread.wait(3000)
        if self._sync_thread is not None:
            self._sync_thread.quit()
            self._sync_thread.wait(3000)
        if self.popup is not None:
            self.popup.close()
        super().closeEvent(event)
