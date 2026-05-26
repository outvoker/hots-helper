"""Main window of the HotS Helper desktop app."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
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
from .hotkey_field import HotkeyField
from .launcher import FloatingLauncher
from .popup import PopupWindow
from .translate_popup import ChatTranslationPopup, ComposeTranslatePopup
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
from .workers import (
    ChatCropTranslateWorker,
    ChatTranslateWorker,
    ChatTranslationResult,
    HotkeyShotResult,
    HotkeyWorker,
    ScanWorker,
    SyncWorker,
    WatchWorker,
)


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

        # Wrap the whole layout in a QScrollArea so the window stays
        # usable on small screens — once the user expands the settings
        # panel + log box, the content can comfortably exceed 700px and
        # we'd otherwise clip the bottom (credit row, log textarea).
        # The scroll area is the QMainWindow's central widget; the real
        # content lives inside ``central`` and ``root`` below, exactly
        # like before.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(scroll)

        central = QWidget()
        scroll.setWidget(central)
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

        # --- Translation hotkeys section --------------------------------
        # Each hotkey is a HotkeyField (display + 编辑 button). Only
        # actively recording mode captures keystrokes — clicking
        # somewhere else on the window won't accidentally rewrite a
        # saved shortcut.
        self.trans_box = QGroupBox()
        tb = QVBoxLayout(self.trans_box)
        chat_row = QHBoxLayout()
        self.chat_hk_label = QLabel()
        chat_row.addWidget(self.chat_hk_label)
        self.chat_hk_field = HotkeyField(
            self.config.chat_translate_hotkey,
            qt_from_pynput=_pynput_to_qt_seq,
            pynput_from_qt=_qt_seq_to_pynput,
        )
        self.chat_hk_field.saved.connect(self._save_chat_translate_hotkey)
        chat_row.addWidget(self.chat_hk_field, 1)
        tb.addLayout(chat_row)

        compose_row = QHBoxLayout()
        self.compose_hk_label = QLabel()
        compose_row.addWidget(self.compose_hk_label)
        self.compose_hk_field = HotkeyField(
            self.config.compose_translate_hotkey,
            qt_from_pynput=_pynput_to_qt_seq,
            pynput_from_qt=_qt_seq_to_pynput,
        )
        self.compose_hk_field.saved.connect(self._save_compose_translate_hotkey)
        compose_row.addWidget(self.compose_hk_field, 1)
        tb.addLayout(compose_row)
        sp.addWidget(self.trans_box)

        # --- OCR language packs section ----------------------------------
        # Each enabled language adds ~1s to OCR wall time on a typical
        # laptop. CN+EN is forced on (covers Chinese + English on its
        # own); KR / JP are optional. The squad's default is CN+EN
        # plus Korean, since most opponents are on KR servers.
        self.ocr_lang_box = QGroupBox()
        olb = QVBoxLayout(self.ocr_lang_box)
        olb_row = QHBoxLayout()
        self.ocr_lang_cn_chk = QCheckBox()
        self.ocr_lang_cn_chk.setChecked(True)
        self.ocr_lang_cn_chk.setEnabled(False)  # always on
        self.ocr_lang_cn_chk.setToolTip(
            "中文 + 英文（始终启用，英文只靠这个模型识别）"
        )
        olb_row.addWidget(self.ocr_lang_cn_chk)
        self.ocr_lang_kr_chk = QCheckBox()
        self.ocr_lang_kr_chk.setChecked(
            "korean" in self.config.ocr_languages
        )
        self.ocr_lang_kr_chk.stateChanged.connect(
            lambda _s: self._save_ocr_languages()
        )
        olb_row.addWidget(self.ocr_lang_kr_chk)
        self.ocr_lang_jp_chk = QCheckBox()
        self.ocr_lang_jp_chk.setChecked(
            "japanese" in self.config.ocr_languages
        )
        self.ocr_lang_jp_chk.stateChanged.connect(
            lambda _s: self._save_ocr_languages()
        )
        olb_row.addWidget(self.ocr_lang_jp_chk)
        olb_row.addStretch(1)
        olb.addLayout(olb_row)
        self.ocr_lang_hint = QLabel()
        self.ocr_lang_hint.setStyleSheet(f"color:{TEXT_DIM}; font-size:9pt;")
        self.ocr_lang_hint.setWordWrap(True)
        olb.addWidget(self.ocr_lang_hint)
        sp.addWidget(self.ocr_lang_box)

        # Floating launcher toggle — single checkbox, persisted to config.
        # Wraps it in a QGroupBox so it visually groups with the other
        # settings sections.
        self.launcher_box = QGroupBox()
        lbox = QHBoxLayout(self.launcher_box)
        self.launcher_chk = QCheckBox()
        self.launcher_chk.setChecked(self.config.launcher_visible)
        self.launcher_chk.stateChanged.connect(self._toggle_launcher_visible)
        lbox.addWidget(self.launcher_chk)
        lbox.addStretch(1)
        sp.addWidget(self.launcher_box)

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
        # Lazily created when the user first clicks the player-rankings button.
        self._player_rank_dialog = None
        # Lazily created when the user first clicks the weekly-report button.
        self._weekly_dialog = None
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

        # Translation hotkeys: each gets its own pynput listener. They
        # share a single thread internally (pynput's GlobalHotKeys is a
        # single watcher per registration), and the listeners are
        # cheap, so wiring one HotkeyManager per purpose is fine.
        self.chat_translate_hotkey = HotkeyManager()
        self.chat_translate_hotkey.triggered.connect(self._on_chat_translate_hotkey)
        self.chat_translate_hotkey.error.connect(
            lambda m: self._log(f"[chat-trans hotkey] {m}")
        )
        self.compose_translate_hotkey = HotkeyManager()
        self.compose_translate_hotkey.triggered.connect(
            self._on_compose_translate_hotkey
        )
        self.compose_translate_hotkey.error.connect(
            lambda m: self._log(f"[compose-trans hotkey] {m}")
        )

        self.popup = PopupWindow(self.store)
        # Pass parent=None on purpose. With ``parent=self`` the
        # Qt.Tool window inherits the main window's "owner" and
        # Windows pulls the owner to the foreground when the tool
        # shows — which yanks the helper UI back over the game even
        # though we already have WA_ShowWithoutActivating set on the
        # progress dialog. Detached parent-less = no owner-window
        # foreground promotion.
        self.capture_progress = CaptureProgressDialog(None)
        # Lazy-create translation popups on first hotkey press.
        self._chat_trans_popup: ChatTranslationPopup | None = None
        self._compose_popup: ComposeTranslatePopup | None = None
        self._chat_trans_thread: QThread | None = None
        self._chat_trans_worker: ChatTranslateWorker | None = None
        # Stage-2 crop+OCR+translate worker (started after the user
        # finishes framing the chat region in the region selector).
        self._chat_crop_thread: QThread | None = None
        self._chat_crop_worker: ChatCropTranslateWorker | None = None
        self._chat_trans_screenshot: Path | None = None
        self._chat_trans_busy = False

        # Floating always-on-top launcher. The whole reason this exists
        # is that on Windows, HotS / Battle.net commonly run elevated
        # while our helper doesn't, and Windows UIPI silently drops
        # global keyboard events from a non-elevated process to an
        # elevated one. Mouse clicks on a top-most window aren't
        # affected, so the launcher works regardless of admin parity.
        self.launcher = FloatingLauncher(
            self.config,
            on_bp=lambda: self._on_hotkey(sample_path=None),
            on_chat_translate=self._on_chat_translate_hotkey,
            on_compose_translate=self._on_compose_translate_hotkey,
        )
        if self.config.launcher_visible:
            self.launcher.show()

        # Seed UI from config.
        self._refresh_roots()
        self._refresh_stats()
        if self.config.hotkey:
            self.hotkey.set_hotkey(self.config.hotkey)
            self._log(t("ui.main.hotkey_registered", combo=self.config.hotkey))
        if self.config.chat_translate_hotkey:
            self.chat_translate_hotkey.set_hotkey(self.config.chat_translate_hotkey)
            self._log(t(
                "ui.main.chat_translate_hotkey_registered",
                combo=self.config.chat_translate_hotkey,
            ))
        if self.config.compose_translate_hotkey:
            self.compose_translate_hotkey.set_hotkey(
                self.config.compose_translate_hotkey
            )
            self._log(t(
                "ui.main.compose_translate_hotkey_registered",
                combo=self.config.compose_translate_hotkey,
            ))
        if self.watch_chk.isChecked():
            self._start_watching()
        # Kick off a startup sync if the user has configured cloud creds.
        if self._cloud_sync is not None and self.config.sync_auto:
            self._start_sync(force=False)
        # Auto-scan the configured replay folders so the user doesn't have
        # to remember to click "Start scan" after each app launch. The
        # scan_index cache makes this near-free on subsequent runs (only
        # files with changed mtime/size get parsed). Defer to the next
        # event-loop tick so the main window paints first.
        if self.config.auto_scan_on_start and self.config.effective_replay_dirs():
            QTimer.singleShot(0, self._start_scan)

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

        # Hotkey row — gated edit so accidental keystrokes elsewhere
        # on the window don't rewrite the saved combo.
        hk_row = QHBoxLayout()
        self.shortcut_label = QLabel()
        hk_row.addWidget(self.shortcut_label)
        self.hotkey_field = HotkeyField(
            self.config.hotkey,
            qt_from_pynput=_pynput_to_qt_seq,
            pynput_from_qt=_qt_seq_to_pynput,
        )
        self.hotkey_field.saved.connect(self._save_bp_hotkey)
        hk_row.addWidget(self.hotkey_field, 1)
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

        # Mode buttons — hero strength leaderboards…
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

        # …and one row below for the player-side leaderboards (worst
        # teammates / strongest opponents).
        player_row = QHBoxLayout()
        player_row.setSpacing(10)
        self.player_rank_btn = QPushButton()
        self.player_rank_btn.clicked.connect(self._show_player_rankings)
        self.player_rank_btn.setMinimumHeight(36)
        player_row.addWidget(self.player_rank_btn)
        # Weekly report sits next to the player ranking entry — both are
        # "look at squad performance" actions, just at different time
        # scales.
        self.weekly_btn = QPushButton()
        self.weekly_btn.clicked.connect(self._show_weekly_report)
        self.weekly_btn.setMinimumHeight(36)
        player_row.addWidget(self.weekly_btn)
        v.addLayout(player_row)

        for b in (self.sl_btn, self.aram_btn, self.player_rank_btn, self.weekly_btn):
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
        self.hotkey_field.retranslate()
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
        self.player_rank_btn.setText(t("ui.main.player_ranking"))
        self.player_rank_btn.setToolTip(t("ui.main.player_ranking_tip"))
        self.weekly_btn.setText(t("ui.weekly.btn"))
        self.weekly_btn.setToolTip(t("ui.weekly.btn_tip"))

        self.log_box.setTitle(t("ui.main.activity"))
        self.credit_label.setText(t("ui.main.credit"))

        self.trans_box.setTitle(t("ui.main.trans_hotkeys_section"))
        self.chat_hk_label.setText(t("ui.main.chat_translate_label"))
        self.compose_hk_label.setText(t("ui.main.compose_translate_label"))
        self.chat_hk_field.retranslate()
        self.compose_hk_field.retranslate()

        self.launcher_box.setTitle(t("ui.main.launcher_section"))
        self.launcher_chk.setText(t("ui.main.launcher_visible"))

        self.ocr_lang_box.setTitle(t("ui.main.ocr_lang_section"))
        self.ocr_lang_cn_chk.setText(t("ui.main.ocr_lang_cn"))
        self.ocr_lang_kr_chk.setText(t("ui.main.ocr_lang_kr"))
        self.ocr_lang_jp_chk.setText(t("ui.main.ocr_lang_jp"))
        self.ocr_lang_hint.setText(t("ui.main.ocr_lang_hint"))

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

    def _save_bp_hotkey(self, combo: str) -> None:
        """Persist + re-register the BP-intelligence hotkey."""
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

    def _toggle_launcher_visible(self, state: int) -> None:
        visible = bool(state)
        self.config.launcher_visible = visible
        self.config.save()
        if visible:
            self.launcher.show()
            self.launcher.raise_()
        else:
            self.launcher.hide()

    def _save_ocr_languages(self) -> None:
        """Persist the user's checkbox choices. CN+EN is always on
        (it's the only model that recognises English at all)."""
        langs = ["cn+en"]
        if self.ocr_lang_kr_chk.isChecked():
            langs.append("korean")
        if self.ocr_lang_jp_chk.isChecked():
            langs.append("japanese")
        self.config.ocr_languages = langs
        self.config.save()
        self._log(t("ui.main.ocr_lang_saved", langs=", ".join(langs)))

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

    def _show_player_rankings(self) -> None:
        """Open the worst-teammate / strongest-opponent leaderboard."""
        if (
            not hasattr(self, "_player_rank_dialog")
            or self._player_rank_dialog is None
        ):
            from .player_rank_dialog import PlayerRankDialog
            self._player_rank_dialog = PlayerRankDialog(
                self.store, parent=self
            )
        else:
            self._player_rank_dialog._reload()
        self._player_rank_dialog.show()
        self._player_rank_dialog.raise_()
        self._player_rank_dialog.activateWindow()

    def _show_weekly_report(self) -> None:
        """Open the squad weekly-report dialog. Lazy-init + reused."""
        if (
            not hasattr(self, "_weekly_dialog")
            or self._weekly_dialog is None
        ):
            from .weekly_report_dialog import WeeklyReportDialog
            self._weekly_dialog = WeeklyReportDialog(
                self.store, days=7, parent=self,
            )
        else:
            self._weekly_dialog._reload()
        self._weekly_dialog.show()
        self._weekly_dialog.raise_()
        self._weekly_dialog.activateWindow()

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
        # Hide every helper-owned widget that's currently floating over
        # the game so it doesn't end up in the captured frame. The
        # progress dialog is *not* shown yet either — both come back
        # after the worker emits screenshot_taken.
        self._hide_helper_overlays_for_capture()

        thread = QThread(self)
        worker = HotkeyWorker(
            sample_path=sample_path,
            ocr_languages=list(self.config.ocr_languages),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._log)
        worker.progress.connect(self.capture_progress.update_substatus)
        worker.screenshot_taken.connect(self._on_screenshot_taken_bp)
        worker.finished.connect(self._on_hotkey_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_hotkey_thread)
        self._hotkey_thread = thread
        self._hotkey_worker = worker
        thread.start()

    def _hide_helper_overlays_for_capture(self) -> None:
        """Briefly hide the floating launcher (and any open per-shot
        popups) so they don't appear in the screenshot. Captured
        elsewhere because both BP and chat capture flows need it."""
        self._launcher_was_visible = (
            self.launcher is not None and self.launcher.isVisible()
        )
        if self._launcher_was_visible:
            self.launcher.hide()
        # The popup may be open from a previous BP run — hide it so it
        # doesn't sit half-overlapping the next draft screen.
        if self.popup is not None and self.popup.isVisible():
            self._popup_was_visible = True
            self.popup.hide()
        else:
            self._popup_was_visible = False
        # Same for the chat-translate popup.
        if (
            self._chat_trans_popup is not None
            and self._chat_trans_popup.isVisible()
        ):
            self._chat_trans_popup_was_visible = True
            self._chat_trans_popup.hide()
        else:
            self._chat_trans_popup_was_visible = False
        # Process the pending hide events so the OS actually paints
        # without the overlays before the worker grabs the frame.
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def _restore_helper_overlays_after_capture(self) -> None:
        """Bring the launcher (and previously-visible popups) back.
        Called from the worker's screenshot_taken signal."""
        if getattr(self, "_launcher_was_visible", False) and self.launcher is not None:
            self.launcher.show()
        if getattr(self, "_chat_trans_popup_was_visible", False) and self._chat_trans_popup is not None:
            self._chat_trans_popup.show()
        # Note: the BP popup intentionally stays hidden — it'll be
        # re-shown by show_for_map() with the new analysis. Same for
        # the chat popup if a fresh chat capture is replacing it.

    def _on_screenshot_taken_bp(self) -> None:
        """Worker tells us the BP frame is on disk → show the progress
        dialog and restore overlays. The flow="bp" script kicks in.

        Anchor is ``None`` (= screen center) on purpose. Anchoring to
        ``self`` would pull the main window into screen coordinates we
        don't want during a draft — the user wants to see the progress
        card over the game, not behind the helper UI."""
        self._restore_helper_overlays_after_capture()
        self.capture_progress.start(anchor=None, flow="bp")

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

    # --- translation hotkey config ----------------------------------------

    def _save_chat_translate_hotkey(self, combo: str) -> None:
        """Persist + re-register the chat-OCR-translate hotkey."""
        self.config.chat_translate_hotkey = combo
        self.config.save()
        self.chat_translate_hotkey.set_hotkey(combo)
        self._log(t("ui.main.chat_translate_hotkey_registered", combo=combo))

    def _save_compose_translate_hotkey(self, combo: str) -> None:
        """Persist + re-register the compose-translate hotkey."""
        self.config.compose_translate_hotkey = combo
        self.config.save()
        self.compose_translate_hotkey.set_hotkey(combo)
        self._log(t("ui.main.compose_translate_hotkey_registered", combo=combo))

    # --- chat translation hotkey -------------------------------------------

    def _on_chat_translate_hotkey(self) -> None:
        if self._chat_trans_busy:
            self._log(t("ui.main.hotkey_busy"))
            return
        if self._chat_trans_popup is None:
            self._chat_trans_popup = ChatTranslationPopup()
        self._chat_trans_busy = True
        self._log(t("ui.main.chat_translate_started"))
        # Same hide-before-capture pattern as the BP flow so the
        # launcher / progress card don't end up in the screenshot.
        self._hide_helper_overlays_for_capture()

        thread = QThread(self)
        worker = ChatTranslateWorker(
            target_lang="zh",
            ocr_languages=list(self.config.ocr_languages),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._log)
        worker.progress.connect(self.capture_progress.update_substatus)
        worker.screenshot_taken.connect(self._on_screenshot_taken_chat)
        worker.finished.connect(self._on_chat_translate_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_chat_translate_thread)
        self._chat_trans_thread = thread
        self._chat_trans_worker = worker
        thread.start()

    def _on_screenshot_taken_chat(self) -> None:
        """Worker tells us the chat-OCR frame is on disk → just restore
        the helper UI. We deliberately don't show the progress card here
        any more: nothing OCR-shaped is happening yet — the user is
        about to draw a region."""
        self._restore_helper_overlays_after_capture()

    def _cleanup_chat_translate_thread(self) -> None:
        if self._chat_trans_worker is not None:
            self._chat_trans_worker.deleteLater()
            self._chat_trans_worker = None
        if self._chat_trans_thread is not None:
            self._chat_trans_thread.deleteLater()
            self._chat_trans_thread = None
        self._chat_trans_busy = False

    def _on_chat_translate_finished(self, result: ChatTranslationResult) -> None:
        """First stage finished — just a screenshot. If we got one,
        prompt the user to draw a chat region; the actual OCR + translate
        stage is the :class:`ChatCropTranslateWorker` kicked off in
        ``_on_chat_region_picked``."""
        for line in result.log_lines:
            self._log(line)

        if result.error or not result.screenshot_path:
            # Capture itself failed — surface the error in a popup so
            # the user knows why nothing happened. Reuse the chat
            # translation popup since it already renders error strings.
            if self._chat_trans_popup is None:
                self._chat_trans_popup = ChatTranslationPopup()
            self._chat_trans_popup.show_result(result)
            return

        # Stash so the crop worker can read these in its callback.
        self._chat_trans_screenshot: Path = result.screenshot_path
        self._chat_trans_log_prefix: list[str] = list(result.log_lines)

        # Open the region selector. We want the helper UI fully restored
        # before we open the dialog so the user sees the screenshot
        # behind the dialog rather than a half-painted helper window.
        from .region_select import RegionSelectorDialog
        try:
            dlg = RegionSelectorDialog(
                result.screenshot_path, parent=self
            )
        except Exception as e:
            self._log(f"[chat-trans] cannot open region selector: {e}")
            self._chat_trans_busy = False
            return
        dlg.region_picked.connect(self._on_chat_region_picked)
        # Use exec() so the helper UI doesn't take input until the
        # user finishes the selection or cancels. Return code != 1
        # means the user hit Esc / clicked a tiny rect — release the
        # busy flag so the next hotkey press isn't ignored.
        if dlg.exec() != 1:
            self._chat_trans_busy = False
            self._log("[chat-trans] region selection cancelled")

    def _on_chat_region_picked(
        self, x: int, y: int, w: int, h: int
    ) -> None:
        """User finished framing the chat box → run OCR + translate
        on just that crop. Tiny region = sub-second OCR even with all
        three language packs enabled."""
        screenshot_path = getattr(self, "_chat_trans_screenshot", None)
        if not screenshot_path:
            return

        # Only show the progress card now: this is when actual work
        # starts. ``flow="chat"`` keeps the progress copy ("识别选中区
        # 域文字" etc.) consistent with the chat translate use case.
        self.capture_progress.start(anchor=None, flow="chat")

        thread = QThread(self)
        worker = ChatCropTranslateWorker(
            screenshot_path=screenshot_path,
            x=x, y=y, w=w, h=h,
            target_lang="zh",
            ocr_languages=list(self.config.ocr_languages),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._log)
        worker.progress.connect(self.capture_progress.update_substatus)
        worker.finished.connect(self._on_chat_crop_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_chat_crop_thread)
        self._chat_crop_thread = thread
        self._chat_crop_worker = worker
        thread.start()

    def _cleanup_chat_crop_thread(self) -> None:
        if getattr(self, "_chat_crop_worker", None) is not None:
            self._chat_crop_worker.deleteLater()
            self._chat_crop_worker = None
        if getattr(self, "_chat_crop_thread", None) is not None:
            self._chat_crop_thread.deleteLater()
            self._chat_crop_thread = None
        self._chat_trans_busy = False

    def _on_chat_crop_finished(
        self, result: ChatTranslationResult
    ) -> None:
        for line in result.log_lines:
            self._log(line)
        ok = bool(result.pairs) and not result.error
        self.capture_progress.finish(
            ok=ok,
            message=result.error if result.error else None,
        )
        if self._chat_trans_popup is None:
            self._chat_trans_popup = ChatTranslationPopup()
        self._chat_trans_popup.show_result(result)

    # --- compose translate hotkey -------------------------------------------

    def _on_compose_translate_hotkey(self) -> None:
        if self._compose_popup is None:
            self._compose_popup = ComposeTranslatePopup()
        self._compose_popup.open_centered()

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
        self.chat_translate_hotkey.stop()
        self.compose_translate_hotkey.stop()
        if self._scan_thread is not None:
            self._scan_thread.quit()
            self._scan_thread.wait(5000)
        if self._hotkey_thread is not None:
            self._hotkey_thread.quit()
            self._hotkey_thread.wait(3000)
        if self._chat_trans_thread is not None:
            self._chat_trans_thread.quit()
            self._chat_trans_thread.wait(3000)
        if self._chat_crop_thread is not None:
            self._chat_crop_thread.quit()
            self._chat_crop_thread.wait(3000)
        if self._sync_thread is not None:
            self._sync_thread.quit()
            self._sync_thread.wait(3000)
        if self.popup is not None:
            self.popup.close()
        if self._chat_trans_popup is not None:
            self._chat_trans_popup.close()
        if self._compose_popup is not None:
            self._compose_popup.close()
        if self.launcher is not None:
            self.launcher.close()
        super().closeEvent(event)
