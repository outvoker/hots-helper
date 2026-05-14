"""Main window of the HotS Helper desktop app."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, default_hots_replay_roots, discover_replay_dirs
from ..db import Store
from ..i18n import available_languages, on_change as on_lang_change, set_language, t
from ..watcher.ingest import IngestResult
from .hotkey import HotkeyManager
from .popup import PopupWindow
from .workers import HotkeyShotResult, HotkeyWorker, ScanWorker, WatchWorker


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

        # --- Recording roots section ------------------------------------------
        self.roots_box = QGroupBox()
        rb = QVBoxLayout(self.roots_box)
        self.roots_list = QListWidget()
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
        self.effective_label.setStyleSheet("color:#888;")
        rb.addWidget(self.effective_label)
        root.addWidget(self.roots_box)

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
        root.addWidget(self.actions_box)

        # --- Hotkey section ---------------------------------------------------
        self.hk_box = QGroupBox()
        hb = QHBoxLayout(self.hk_box)
        self.shortcut_label = QLabel()
        hb.addWidget(self.shortcut_label)
        self.hotkey_edit = QKeySequenceEdit()
        self.hotkey_edit.setKeySequence(_pynput_to_qt_seq(self.config.hotkey))
        hb.addWidget(self.hotkey_edit)
        self.apply_btn = QPushButton()
        self.apply_btn.clicked.connect(self._apply_hotkey)
        hb.addWidget(self.apply_btn)
        self.test_btn = QPushButton()
        self.test_btn.clicked.connect(self._test_popup)
        hb.addWidget(self.test_btn)
        hb.addStretch(1)
        root.addWidget(self.hk_box)

        # --- Stats tools ------------------------------------------------------
        self.tools_box = QGroupBox()
        tb = QHBoxLayout(self.tools_box)
        self.sl_btn = QPushButton()
        self.sl_btn.clicked.connect(lambda: self._show_hero_ranking("Storm League"))
        tb.addWidget(self.sl_btn)
        self.aram_btn = QPushButton()
        self.aram_btn.clicked.connect(lambda: self._show_hero_ranking("ARAM"))
        tb.addWidget(self.aram_btn)
        tb.addStretch(1)
        root.addWidget(self.tools_box)

        # --- Log --------------------------------------------------------------
        self.log_box = QGroupBox()
        lb = QVBoxLayout(self.log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lb.addWidget(self.log)
        root.addWidget(self.log_box, 1)

        # --- Runtime: store, workers, hotkey, popup --------------------------
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        # Lazily created when the user first clicks the ARAM button.
        self._aram_dialog = None

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

        # Seed UI from config.
        self._refresh_roots()
        self._refresh_stats()
        if self.config.hotkey:
            self.hotkey.set_hotkey(self.config.hotkey)
            self._log(t("ui.main.hotkey_registered", combo=self.config.hotkey))
        if self.watch_chk.isChecked():
            self._start_watching()

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

        self.hk_box.setTitle(t("ui.main.hotkey_section"))
        self.shortcut_label.setText(t("ui.main.shortcut"))
        self.apply_btn.setText(t("ui.main.apply"))
        self.test_btn.setText(t("ui.main.test_popup"))

        self.tools_box.setTitle(t("ui.main.tools"))
        self.sl_btn.setText(t("ui.main.sl_ranking"))
        self.sl_btn.setToolTip(t("ui.main.sl_ranking_tip"))
        self.aram_btn.setText(t("ui.main.aram_ranking"))
        self.aram_btn.setToolTip(t("ui.main.aram_ranking_tip"))

        self.log_box.setTitle(t("ui.main.activity"))

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

    def _test_popup(self) -> None:
        self._on_hotkey()

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

    def _on_hotkey(self) -> None:
        # Reentry guard: if the user spams the hotkey while OCR is running
        # we'd queue up multiple worker threads and confuse winrt.
        if self._hotkey_busy:
            self._log(t("ui.main.hotkey_busy"))
            return
        self._hotkey_busy = True
        self._log(t("ui.main.capturing_screenshot"))

        thread = QThread(self)
        worker = HotkeyWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._log)
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
        if self.popup is not None:
            self.popup.close()
        super().closeEvent(event)
