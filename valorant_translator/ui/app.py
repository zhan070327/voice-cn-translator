from __future__ import annotations
from valorant_translator.ui.texts import tr

import logging
import math
import sys
import threading
import time
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

from filelock import Timeout as FileLockTimeout
from packaging.version import Version

from PySide6.QtCore import QObject, QSignalBlocker, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
                               QHeaderView, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QProgressBar,
                               QScrollArea, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget, QTableWidget,
                               QTableWidgetItem, QAbstractItemView, QTreeWidget, QTreeWidgetItem,
                               QButtonGroup, QColorDialog, QSizePolicy)

from valorant_translator.audio.capture import list_devices, preferred_microphone
from valorant_translator.feedback import FeedbackStore
from valorant_translator.config import APP_DIR, MODEL_DIR, SPEED_MODES, Settings, get_model_dir
from valorant_translator.history import HistoryStore
from valorant_translator.models.catalog import fetch_catalog, install_selected
from valorant_translator.models.cos import ModelPaused
from valorant_translator.models.download import (check_huggingface_space, download_models, import_models,
                                                 model_ready, WHISPER_FILES, M2M_FILES)
from valorant_translator.models.location import InsufficientModelSpace, move_model_dir
from valorant_translator.pipeline import Pipeline, SubtitleResult, TaskTrace
from valorant_translator.recognition.whisper import LANGUAGES
from valorant_translator.translation.glossary import GAMES, read_default_glossary
from valorant_translator.translation.rules import Rule, RuleStore, now
from valorant_translator.ui.dialogs import (GlassFileDialog as QFileDialog, GlassInputDialog as QInputDialog,
                                           GlassMessageBox as QMessageBox, apply_dialog_theme)
from valorant_translator.ui.engine_settings import AzureCredentialsDialog
from valorant_translator.ui.glass import (BACKGROUND_THEMES, DEFAULT_BACKGROUND_COLOR,
                                        GAP, GUTTER, INLINE, CompactLabel, GlassButton, GlassSection,
                                        GlassSurface, accent_color, glass_styles, tint_icons)
from valorant_translator.ui.glossary_tools import (add_custom_term, default_glossary, entry_text, manage_rules, preview,
                                                   review_feedback, submit_feedback)
from valorant_translator.ui import tokens
from valorant_translator.ui.icons import LineIcon
from valorant_translator.ui.microphone import MicrophoneWizard, is_routed_source
from valorant_translator.ui.motion import ActionDock, CaptionLight, DetailPanel, LoadingSkeleton, SelectionCapsule, Snapshot, Toast
from valorant_translator.ui.onboarding import Onboarding
from valorant_translator.ui.overlay import SubtitleOverlay
from valorant_translator.ui.routing import RoutingWizard
from valorant_translator.ui.talk import SILENT_RMS, PushToTalk
from valorant_translator.updates.channel import check_for_update
from valorant_translator.updates.domestic import load_channel
from valorant_translator.updates.github import (UpdateCancelled, download_installer,
                                                start_installer)
from valorant_translator.updates.windows import RESULT, start_silent_installer
from valorant_translator.version import APP_VERSION

LOG = logging.getLogger(__name__)
FIRST_CAPTION_HINT = tr("app.first_caption_hint")
EMPTY_HISTORY = tr("app.empty_recent_history")
QUICK_START_GAP = tokens.TIGHT // 2


class Bridge(QObject):
    status = Signal(str)
    level = Signal(float)
    result = Signal(object)
    toggle = Signal()
    talk_begin = Signal()
    talk_end = Signal()
    talk_status = Signal(str)
    talk_result = Signal(object)
    talk_details = Signal(object)
    talk_level = Signal(float)
    model_catalog = Signal(object)
    download_done = Signal()
    model_location_done = Signal(object, str)
    model_download_progress = Signal(object, object, float)
    stopped = Signal()
    diagnostic = Signal(str)
    trace = Signal(object)
    devices_ready = Signal(object, str)
    runtime = Signal(str)
    update_checked = Signal(object)
    update_progress = Signal(int)
    update_ready = Signal(object)
    update_error = Signal(str)
    update_speed = Signal(float)
    update_cancelled = Signal()


class MainWindow(QMainWindow):
    devices_refreshed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        if not isinstance(self.settings.game_accents, dict):
            self.settings.game_accents = {}
        if not self.settings.game_accents and accent_color(self.settings.accent).lower() != tokens.LEGACY_ACCENT:
            self.settings.game_accents[self.settings.game] = accent_color(self.settings.accent)
        self.settings.accent = self._game_accent()
        self._has_result = False
        self._has_live_result = False
        self._model_error = ""
        self._history_pending = False
        self._glossary_request = 0
        self.custom_rules = RuleStore()
        self.feedback_store = FeedbackStore()
        self.audio_devices = []
        self._quiet_since = None
        self.routing_wizard = None
        try:
            self.history = HistoryStore()
            self.history_error = ""
        except Exception as exc:
            LOG.exception("Translation history is unavailable")
            self.history = None
            self.history_error = str(exc)
        self.pipeline: Pipeline | None = None
        self._stop_thread: threading.Thread | None = None
        self._restart_after_stop = False
        self._update_busy = False
        self._update_cancel_event = threading.Event()
        self._domestic_update = False
        self._model_busy = False
        self._model_moving = False
        self._model_pause = threading.Event()
        try:
            self._model_channel = load_channel()
        except Exception:
            LOG.exception("Could not read the model download channel")
            self._model_channel = None
        self.hotkey_listener = None
        self.catalog = {}
        self.catalog_checked = threading.Event()
        self.reverse_text = ""
        self.onboarding = None
        self.microphone_wizard = None
        self._recent_trace: deque[TaskTrace] = deque(maxlen=10)
        self._recent_results: deque[SubtitleResult] = deque(maxlen=100)
        self._task_timings: dict[int, dict[str, float]] = {}
        self._trace_token = None
        self._last_diagnostics = None
        self._active_icon_tasks: set[tuple[int, str]] = set()
        self.bridge = Bridge()
        self.talk = PushToTalk(self.bridge.talk_status.emit, self.bridge.talk_result.emit,
                               level=self.bridge.talk_level.emit, details=self.bridge.talk_details.emit,
                               custom_rules=self.custom_rules)
        self.bridge.talk_begin.connect(self._begin_talk)
        self.bridge.talk_end.connect(self.talk.end)
        self.bridge.talk_result.connect(self._reverse_result)
        self.bridge.talk_status.connect(self._talk_status)
        self.bridge.talk_level.connect(self._talk_level)
        self.bridge.talk_details.connect(self._save_reverse_history)
        self.bridge.model_catalog.connect(self._catalog_ready)
        self.bridge.status.connect(self._status)
        self.bridge.level.connect(self._show_audio_level)
        self.bridge.result.connect(self._result)
        self.bridge.toggle.connect(self._toggle_overlay)
        self.bridge.download_done.connect(self._download_done)
        self.bridge.model_location_done.connect(self._finish_model_location_move)
        self.bridge.model_download_progress.connect(self._model_download_progress)
        self.bridge.stopped.connect(self._on_stopped)
        self.bridge.diagnostic.connect(self._diagnostic)
        self.bridge.trace.connect(self._receive_trace)
        self.bridge.devices_ready.connect(self._finish_device_refresh)
        self.bridge.runtime.connect(self._runtime)
        self.bridge.update_checked.connect(self._update_checked)
        self.bridge.update_progress.connect(self._update_progress)
        self.bridge.update_ready.connect(self._update_ready)
        self.bridge.update_error.connect(self._update_error)
        self.bridge.update_speed.connect(self._update_speed)
        self.bridge.update_cancelled.connect(self._update_cancelled)
        self.setWindowTitle(tr("app.0001"))
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[2] / "assets" / "app_icon.ico")))
        self.resize(1160, 840)
        self._build()
        self.tabs.currentChanged.connect(self._page_changed)
        self._refresh_history()
        self.engine_runtime = self._compact("")
        self.statusBar().addPermanentWidget(self.engine_runtime)
        self.engine_runtime.hide()
        self.overlay = SubtitleOverlay(self.settings)
        self.overlay.position_changed.connect(self._overlay_moved)
        self.overlay.show()
        self._start_hotkey()
        self.refresh_devices()
        self._refresh_model_status()
        self._update_mode_hint()
        QTimer.singleShot(0, self._show_update_result)
        if self._model_channel and self._model_channel["channel"] in ("domestic", "dual") and "model_manifest_url" in self._model_channel:
            threading.Thread(target=self._fetch_model_catalog, name="model-catalog", daemon=True).start()
        else:
            self.catalog_checked.set()

    def _build(self) -> None:
        self.tabs = QTabWidget()
        main_page = QWidget()
        main_layout = QVBoxLayout(main_page)
        main_layout.setContentsMargins(GUTTER, GAP, GUTTER, GUTTER)
        main_layout.setSpacing(GAP)
        audio_page = QWidget()
        audio_page_layout = QVBoxLayout(audio_page)
        audio_page_layout.setContentsMargins(GUTTER, GAP, GUTTER, GUTTER)
        audio_page_layout.setSpacing(GUTTER)
        self.diagnostics_page = QWidget()
        diagnostics_layout = QVBoxLayout(self.diagnostics_page)
        diagnostics_layout.setContentsMargins(GUTTER, GAP, GUTTER, GUTTER)
        diagnostics_layout.setSpacing(GAP)
        audio_page_layout.addWidget(self._build_game_bar())

        self.update_button = GlassButton(tr("app.0006"), icon="refresh")
        self.update_button.clicked.connect(self._check_update)
        self.update_button.setFixedWidth(tokens.UPDATE_WIDTH)
        update_strip = QFrame()
        update_strip.setObjectName("updateStrip")
        updates = QVBoxLayout(update_strip)
        updates.setContentsMargins(GUTTER, tokens.TIGHT, GUTTER, tokens.TIGHT)
        updates.setSpacing(INLINE)
        self.update_label = self._compact("")
        self.update_label.hide()
        updates.addWidget(self.update_label)
        self.update_bar = QProgressBar()
        self.update_bar.setRange(0, 100)
        self.update_bar.hide()
        updates.addWidget(self.update_bar)
        self.update_cancel_button = GlassButton(tr("app.0008"))
        self.update_cancel_button.setProperty("ghost", True)
        self.update_cancel_button.clicked.connect(self._cancel_update)
        self.update_cancel_button.hide()
        updates.addWidget(self.update_cancel_button, alignment=Qt.AlignmentFlag.AlignLeft)
        cards = QHBoxLayout()
        cards.setSpacing(INLINE)
        self.model_stat, self.model_icon = self._stat(cards, tr("app.0009"), tr("app.0013"), "layers")
        self.device_stat, _ = self._stat(cards, tr("app.0010"), tr("app.0013"), "chip")
        self.latency_stat = QLabel()
        self.latency_stat.setProperty("metric", True)
        self.latency_stat.hide()
        self.microphone_stat, self.microphone_icon = self._stat(cards, tr("app.0012"), tr("app.0013"), "mic")
        warning = QFrame()
        warning.setObjectName("warning")
        warning.setToolTip(tr("app.0014"))
        risk = QHBoxLayout(warning)
        risk.setContentsMargins(GAP, tokens.TIGHT, GAP, tokens.TIGHT)
        risk.setSpacing(INLINE)
        risk.addWidget(LineIcon("shield"))
        risk.addWidget(QLabel(tr("app.0015")), 1)

        model_group, model_layout = self._group(tr("app.0016"), "layers")
        self.model_status_label = self._compact(tr("app.0017"))
        model_group.header.addWidget(self.model_status_label, 2)
        self.model_loading = LoadingSkeleton()
        model_layout.addWidget(self.model_loading)
        self.model_state = self._hint("")
        self.model_state.setTextFormat(Qt.TextFormat.PlainText)
        self.model_state.hide()
        model_layout.addWidget(self.model_state)
        model_row = QHBoxLayout()
        model_row.setSpacing(INLINE)
        self.download_button = GlassButton(tr("app.0018"), icon="download")
        self.download_button.clicked.connect(self._download)
        self.import_button = GlassButton(tr("app.0019"), icon="folder")
        self.import_button.clicked.connect(self._import_models)
        model_row.addWidget(self.download_button)
        model_row.addWidget(self.import_button)
        self.model_pause_button = GlassButton(tr("app.0020"))
        self.model_pause_button.setProperty("ghost", True)
        self.model_pause_button.clicked.connect(self._pause_model_download)
        self.model_pause_button.hide()
        model_row.addWidget(self.model_pause_button)
        model_row.addStretch()
        model_layout.addLayout(model_row)
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 0)
        self.model_progress.setTextVisible(False)
        self.model_progress.hide()
        model_layout.addWidget(self.model_progress)
        self.model_transfer = QLabel()
        self.model_transfer.setObjectName("modelTransfer")
        self.model_transfer.hide()
        model_layout.addWidget(self.model_transfer)
        self.download_button.setToolTip(tr("app.0021"))
        self.import_button.setToolTip(tr("app.0022"))

        audio_group, audio_layout = self._group(tr("app.0023"), "speaker")
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.device_combo.currentIndexChanged.connect(self._save_settings)
        self.refresh_button = GlassButton(tr("app.0024"), icon="refresh")
        self.refresh_button.clicked.connect(self._request_devices)
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        audio_layout.addLayout(device_row)
        self.device_hint = self._hint("")
        self.device_hint.setTextFormat(Qt.TextFormat.PlainText)
        audio_layout.addWidget(self.device_hint)
        audio_page_layout.addWidget(audio_group)

        display_group, display_layout = self._group(tr("app.0025"), "screen")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("app.0026"), "desktop")
        self.mode_combo.addItem(tr("app.0027"), "game")
        self.mode_combo.setCurrentIndex(0 if self.settings.normal_window else 1)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.mode_hint = self._hint("")
        overlay_row = QHBoxLayout()
        overlay_row.addWidget(self.mode_combo)
        self.test_button = GlassButton(tr("app.0028"), icon="screen")
        self.test_button.clicked.connect(self._test_overlay)
        self.position_button = GlassButton(tr("app.0029"))
        self.position_button.clicked.connect(self._toggle_position_edit)
        self.show_button = GlassButton(tr("app.0030"))
        self.show_button.clicked.connect(self._toggle_overlay)
        overlay_row.addWidget(self.test_button)
        overlay_row.addWidget(self.show_button)
        overlay_row.addWidget(self.position_button)
        overlay_row.addStretch()
        overlay_row.setSpacing(tokens.TIGHT)
        display_layout.addLayout(overlay_row)
        display_layout.addWidget(self.mode_hint)
        display_layout.addWidget(warning)
        audio_page_layout.addWidget(display_group)

        run_group, run_layout = self._group(tr("app.0031"), "activity")
        run_group.setProperty("compactRun", True)
        run_layout.setContentsMargins(GUTTER, tokens.TIGHT, GUTTER, tokens.TIGHT)
        run_layout.setSpacing(tokens.TIGHT)
        self.run_icon = run_group.icon
        run_row = QHBoxLayout()
        run_row.setSpacing(INLINE)
        self.start_button = GlassButton(tr("app.0032"), icon="play")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_translation)
        self.stop_button = GlassButton(tr("app.0033"), icon="stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_translation)
        run_row.addWidget(self.start_button)
        run_row.addWidget(self.stop_button)
        run_row.addStretch()
        audio_status = QHBoxLayout()
        audio_status.setSpacing(INLINE)
        self.status_label = self._compact(tr("app.0034"))
        self.status_label.hide()
        audio_status.addWidget(self.status_label, 1)
        self.audio_icon = LineIcon("activity")
        self.audio_icon.setToolTip(tr("app.0035"))
        self.audio_icon.hide()
        audio_status.addWidget(self.audio_icon)
        self.volume = QProgressBar()
        self.volume.setRange(0, 100)
        self.volume.setValue(0)
        self.volume.setFixedWidth(tokens.VOLUME_WIDTH)
        self.volume.setAccessibleName(tr("app.0036"))
        self.volume.setToolTip(tr("app.0037"))
        self.volume.hide()
        audio_status.addWidget(self.volume)
        run_layout.addLayout(audio_status)
        run_layout.addLayout(cards)
        self.runtime_label = self._hint(tr("app.0038"))
        self.diagnostic_label = self._hint("")
        self.runtime_label.setTextFormat(Qt.TextFormat.PlainText)
        self.diagnostic_label.setTextFormat(Qt.TextFormat.PlainText)
        self.diagnostic_label.hide()
        self.runtime_label.hide()
        metrics = QVBoxLayout()
        metrics.setSpacing(INLINE)
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.addWidget(self.runtime_label)
        metrics.addWidget(self.diagnostic_label)
        self.last_discard = self._hint("")
        self.last_discard.setTextFormat(Qt.TextFormat.PlainText)
        self.last_discard.setObjectName("lastDiscard")
        self.last_discard.hide()
        metrics.addWidget(self.last_discard)
        run_layout.addLayout(metrics)

        self.reverse_icon = LineIcon("mic")
        self.talk_state = self._compact(tr("app.0041"))
        self.microphone_level = self._compact("")
        self.microphone_level.hide()
        self.talk_button = GlassButton(tr("app.0042"), icon="mic")
        self.talk_button.setToolTip(tr("app.0043"))
        self.talk_button.pressed.connect(self._begin_talk)
        self.talk_button.released.connect(self.talk.end)
        self.reverse_copy = GlassButton(tr("app.0044"), icon="copy")
        self.reverse_copy.setEnabled(False)
        self.reverse_copy.clicked.connect(self._copy_english)
        talk_row = QHBoxLayout()
        talk_row.setSpacing(INLINE)
        talk_row.addWidget(self.reverse_icon)
        talk_row.addWidget(self.talk_button)
        talk_row.addWidget(self.reverse_copy)
        talk_row.addWidget(self.talk_state, 1)
        talk_group, talk_layout = self._group(tr("app.talk_section"), "mic")
        talk_layout.addLayout(talk_row)
        talk_layout.addWidget(self.microphone_level)
        audio_page_layout.addWidget(talk_group)

        self.hero = QFrame()
        self.hero.setObjectName("hero")
        result_layout = QVBoxLayout(self.hero)
        result_layout.setContentsMargins(GAP, INLINE, GAP, INLINE)
        result_layout.setSpacing(INLINE)
        header = QHBoxLayout()
        self.result_icon = LineIcon("chat", accented=True)
        heading = QLabel(tr("app.caption_heading"))
        heading.setObjectName("sectionTitle")
        header.addWidget(self.result_icon)
        header.addWidget(heading)
        header.addStretch()
        self.caption_tools = GlassButton(tr("app.caption_visibility"), icon="screen")
        self.caption_tools.clicked.connect(self._toggle_overlay)
        result_layout.addLayout(header)
        self.chinese_label = QLabel()
        self.chinese_label.hide()
        self.chinese_label.setObjectName("translation")
        self.chinese_label.setProperty("quiet", True)
        self.chinese_label.setTextFormat(Qt.TextFormat.PlainText)
        self.chinese_label.setWordWrap(True)
        caption_well = QFrame()
        caption_well.setObjectName("captionWell")
        captions = QVBoxLayout(caption_well)
        captions.setContentsMargins(INLINE, tokens.TIGHT, INLINE, tokens.TIGHT)
        captions.setSpacing(tokens.TIGHT)
        current = QHBoxLayout()
        current.setSpacing(INLINE)
        current.addWidget(self.chinese_label, 1)
        current.addWidget(self.latency_stat, 0, Qt.AlignmentFlag.AlignVCenter)
        captions.addLayout(current)
        self.caption_loading = LoadingSkeleton()
        captions.addWidget(self.caption_loading)
        self.previous_caption = self._hint("")
        self.previous_caption.setObjectName("previousCaption")
        self.previous_caption.setAccessibleName(tr("app.previous_caption"))
        self.previous_caption.setTextFormat(Qt.TextFormat.PlainText)
        self.previous_caption.hide()
        self.previous_latency = QLabel()
        self.previous_latency.setProperty("metric", True)
        self.previous_latency.hide()
        previous = QHBoxLayout()
        previous.setSpacing(INLINE)
        previous.addWidget(self.previous_caption, 1)
        previous.addWidget(self.previous_latency, 0, Qt.AlignmentFlag.AlignVCenter)
        self.previous_original = self._hint("")
        self.previous_original.setTextFormat(Qt.TextFormat.PlainText)
        self.previous_original.setAccessibleName(tr("app.previous_original"))
        self.previous_original.hide()
        self.caption_hint = self._hint(FIRST_CAPTION_HINT)
        self.caption_hint.setTextFormat(Qt.TextFormat.PlainText)
        captions.addWidget(self.caption_hint)
        result_layout.addWidget(caption_well)
        self.original_label = self._hint("")
        self.original_label.setTextFormat(Qt.TextFormat.PlainText)
        self.language_label = QLabel()
        self.language_label.setTextFormat(Qt.TextFormat.PlainText)
        self.language_label.setProperty("metric", True)
        self.latency_label = self._compact("")
        self.latency_label.setProperty("metric", True)
        self.original_label.hide()
        self.language_label.hide()
        self.failures_label = self._compact("")
        source = QHBoxLayout()
        source.setSpacing(INLINE)
        source.addWidget(self.original_label, 1)
        source.addWidget(self.language_label)
        captions.addLayout(source)
        captions.addLayout(previous)
        captions.addWidget(self.previous_original)
        self.detail_toggle = GlassButton(tr("app.recognition_details"))
        self.detail_toggle.setCheckable(True)
        self.details_panel = QFrame()
        self.details_panel.setObjectName("glassCard")
        details = QVBoxLayout(self.details_panel)
        details.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        details.setSpacing(INLINE)
        details.addWidget(self.latency_label)
        self.confidence_label = self._compact("")
        self.confidence_label.setProperty("metric", True)
        self.confidence_label.hide()
        details.addWidget(self.confidence_label)
        details.addWidget(self.failures_label)
        self.details_panel.hide()
        self.detail_toggle.toggled.connect(self._toggle_details)
        self.feedback_button = GlassButton(tr("app.0051"), icon="copy")
        self.feedback_button.clicked.connect(self._copy_feedback)
        self.recent_group, recent_layout = self._quick_start_group(tr("app.recent_translations"), "clock")
        self.recent_hint = self._hint("")
        recent_layout.addWidget(self.recent_hint)
        self.recent_list = QTreeWidget()
        self.recent_list.setObjectName("recentTranslations")
        self.recent_list.setAccessibleName(tr("app.recent_translations"))
        self.recent_list.setColumnCount(2)
        self.recent_list.setHeaderLabels((tr("app.recent_translation_column"), tr("app.0114")))
        self.recent_list.setHeaderHidden(True)
        self.recent_list.setRootIsDecorated(False)
        self.recent_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.recent_list.setUniformRowHeights(True)
        self.recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_list.header().setStretchLastSection(False)
        self.recent_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.recent_list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.recent_list.itemClicked.connect(self._open_recent_translation)
        self.recent_list.itemActivated.connect(self._open_recent_translation)
        self.recent_list.hide()
        recent_layout.addWidget(self.recent_list)
        self.caption_light = CaptionLight(self.hero)
        main_layout.addWidget(self.hero)
        main_layout.addLayout(run_row)
        main_layout.addStretch()
        self.quick_page = self._scroll(main_page)
        self.tabs.addTab(self.quick_page, tr("app.0052"))

        self.dock = ActionDock((
            (tr("app.test_caption_action"), "play", self.test_button.click),
            (tr("app.caption_visibility"), "screen", self.show_button.click),
            (tr("app.position_caption_action"), "settings", self.position_button.click),
        ))
        for button in self.dock.buttons:
            button.setFixedSize(tokens.SPACE, tokens.SPACE)
        diagnostic_actions = QHBoxLayout()
        diagnostic_actions.addWidget(self.detail_toggle)
        diagnostic_actions.addWidget(self.feedback_button)
        diagnostic_actions.addWidget(self.update_button)
        diagnostic_actions.addStretch()
        diagnostic_actions.addWidget(self.caption_tools)
        diagnostic_actions.addWidget(self.dock)
        diagnostics_layout.addWidget(run_group)
        diagnostics_layout.addLayout(diagnostic_actions)
        diagnostics_layout.addWidget(self.details_panel)
        diagnostics_layout.addStretch()

        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(GUTTER, GAP, GUTTER, GUTTER)
        settings_layout.setSpacing(GAP)
        tools_row = QHBoxLayout()
        self.intro_button = GlassButton(tr("onboarding.0001"), icon="book")
        self.intro_button.clicked.connect(self._show_onboarding)
        tools_row.addWidget(self.intro_button)
        self.settings_microphone_wizard_button = GlassButton(tr("mic_setup.title"), icon="mic")
        self.settings_microphone_wizard_button.clicked.connect(self._open_microphone_wizard)
        tools_row.addWidget(self.settings_microphone_wizard_button)
        tools_row.addStretch()
        settings_layout.addLayout(tools_row)
        form = self._settings_card(settings_layout, tr("app.0053"), "download")
        self.settings_update_button = GlassButton(tr("app.0054"), icon="refresh")
        self.settings_update_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.settings_update_button.clicked.connect(self._check_update)
        form.addRow(self.settings_update_button)
        self.update_version_label = self._hint(tr("app.0055", p0=APP_VERSION))
        updates.addWidget(self.update_version_label)
        self.update_notes_label = self._hint(tr("app.0056"))
        self.update_notes_label.hide()
        updates.addWidget(self.update_notes_label)
        form.addRow(update_strip)
        settings_layout.addWidget(model_group)
        self._engine_controls(self._settings_card(settings_layout, tr("engine.title"), "chip"))
        form = self._settings_card(settings_layout, tr("app.0057"), "chip")
        self.language_combo = self._combo([(tr("app.0058"), "auto"),
                                            *[(name, code) for code, name in LANGUAGES.items()]], self.settings.language)
        self._row(form, tr("app.0059"), self.language_combo)
        self.model_combo = self._combo([(name, name) for name in ("tiny", "base", "small", "medium")],
                                       self.settings.whisper_model)
        self._row(form, tr("app.0060"), self.model_combo)
        if self._model_channel and self._model_channel["channel"] == "dual":
            self.model_source = self._combo([(tr("app.0061"), "cos"), (tr("app.0062"), "huggingface")],
                                            self.settings.model_source)
            self._row(form, tr("app.0063"), self.model_source)
        model_location = QWidget()
        model_location_row = QHBoxLayout(model_location)
        model_location_row.setContentsMargins(0, 0, 0, 0)
        model_location_row.setSpacing(INLINE)
        self.model_location_path = QLineEdit(str(self._get_model_dir()))
        self.model_location_path.setReadOnly(True)
        self.model_location_path.setAccessibleName(tr("app.0320"))
        self.model_location_button = GlassButton(tr("app.0321"), icon="folder")
        self.model_location_button.clicked.connect(self._choose_model_location)
        model_location_row.addWidget(self.model_location_path, 1)
        model_location_row.addWidget(self.model_location_button)
        self._row(form, tr("app.0320"), model_location)
        self._row(form, tr("app.0064"), QLabel(tr("app.0065")))
        self.compute_combo = self._combo([(tr("app.0066"), "auto"), ("GPU", "cuda"), ("CPU", "cpu")],
                                         self.settings.compute_device)
        self._row(form, tr("app.0067"), self.compute_combo)
        self.fast_check = self._check(tr("app.0068"), self.settings.fast_mode)
        self.fast_check.setToolTip(tr("app.0069"))
        form.addRow(self.fast_check)
        talk_form = self._form()
        talk_layout.addLayout(talk_form)
        self.ptt_hotkey_edit = QLineEdit(self.settings.ptt_hotkey)
        self.ptt_hotkey_edit.editingFinished.connect(self._save_settings)
        self._row(talk_form, tr("app.0070"), self.ptt_hotkey_edit)
        self.mic_combo = QComboBox()
        self.mic_combo.setToolTip(tr("app.ptt_microphone_help"))
        self.mic_combo.currentIndexChanged.connect(self._save_settings)
        self._row(talk_form, tr("app.0071"), self.mic_combo)
        self.routing_button = GlassButton(tr("app.0072"), icon="speaker")
        self.routing_button.clicked.connect(self._open_routing)
        route_row = QHBoxLayout()
        self.microphone_wizard_button = GlassButton(tr("mic_setup.title"), icon="mic")
        self.microphone_wizard_button.clicked.connect(self._open_microphone_wizard)
        route_row.addWidget(self.microphone_wizard_button)
        route_row.addWidget(self.routing_button)
        self.routing_status = self._hint("")
        route_row.addWidget(self.routing_status, 1)
        audio_layout.addLayout(route_row)
        self.advanced_button = GlassButton(tr("app.0073"), icon="settings")
        self.advanced_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.advanced_button.setCheckable(True)
        self.advanced_button.setToolTip(tr("app.0074"))
        form.addRow(self.advanced_button)
        self.advanced_fields = QWidget()
        advanced = self._form(self.advanced_fields)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.vad_spin = self._spin(self.settings.vad_level, 0, 3)
        self._row(advanced, tr("app.0075"), self.vad_spin, tr("app.0076"))
        self.timeout_spin = self._spin(int(self.settings.translation_timeout), 1, 30)
        self._row(advanced, tr("app.0077"), self.timeout_spin)
        self.age_spin = self._spin(int(self.settings.max_task_age), 2, 30)
        self._row(advanced, tr("app.0078"), self.age_spin, tr("app.0079"))
        self.advanced_fields.hide()
        self.advanced_button.toggled.connect(self.advanced_fields.setVisible)
        form.addRow(self.advanced_fields)
        form = self._settings_card(settings_layout, tr("app.0080"), "screen")
        self.screen_combo = QComboBox()
        for index, screen in enumerate(QApplication.screens()):
            self.screen_combo.addItem(tr("app.0081", p0=index + 1, p1=screen.name()), index)
        self.screen_combo.setCurrentIndex(min(self.settings.overlay_screen, max(0, self.screen_combo.count() - 1)))
        self.screen_combo.currentIndexChanged.connect(self._save_settings)
        self._row(form, tr("app.0082"), self.screen_combo)
        self.x_spin = self._spin(self.settings.overlay_x, -5000, 5000)
        self.y_spin = self._spin(self.settings.overlay_y, -5000, 5000)
        self._row(form, tr("app.0083"), self.x_spin)
        self._row(form, tr("app.0084"), self.y_spin)
        self.font_spin = self._spin(self.settings.font_size, 14, 60)
        self._row(form, tr("app.0085"), self.font_spin)
        self.opacity_spin = self._spin(self.settings.overlay_opacity, 0, 100)
        self._row(form, tr("app.0086"), self.opacity_spin)
        self.original_check = self._check(tr("app.0087"), self.settings.show_original)
        self.click_check = self._check(tr("app.0088"), self.settings.click_through)
        self.click_check.setToolTip(tr("app.0089"))
        form.addRow(self.original_check)
        form.addRow(self.click_check)
        self.hotkey_edit = QLineEdit(self.settings.hotkey)
        self.hotkey_edit.editingFinished.connect(self._save_settings)
        self._row(form, tr("app.0090"), self.hotkey_edit)
        theme = GlassButton(tr("app.0091"))
        theme.clicked.connect(self._choose_accent)
        self._row(form, tr("app.0092"), theme)
        self.duration_spin = self._spin(int(self.settings.subtitle_seconds), 1, 30)
        self._row(form, tr("app.0093"), self.duration_spin)
        self.model_combo.setToolTip(tr("app.0094"))
        self.background_combo = QComboBox()
        for key, (label, _) in BACKGROUND_THEMES.items():
            self.background_combo.addItem(label, key)
        self.background_combo.addItem(tr("app.0095"), "custom")
        self.background_combo.setCurrentIndex(max(0, self.background_combo.findData(self.settings.background_theme)))
        self.background_combo.currentIndexChanged.connect(self._select_background)
        self._row(form, tr("app.0096"), self.background_combo)
        colors = QHBoxLayout()
        self.background_color_button = GlassButton(tr("app.0097"))
        self.background_color_button.clicked.connect(self._choose_background)
        self.theme_reset_button = GlassButton(tr("app.0098"))
        self.theme_reset_button.clicked.connect(self._reset_theme)
        colors.addWidget(self.background_color_button)
        colors.addWidget(self.theme_reset_button)
        colors.addStretch()
        self.game_accent_reset_button = GlassButton(tr("app.reset_game_accent"))
        self.game_accent_reset_button.clicked.connect(self._reset_game_accent)
        self._row(form, tr("app.game_skin"), self.game_accent_reset_button)
        form.addRow(colors)
        self.reduce_motion_check = QCheckBox(tr("app.reduce_motion"))
        self.reduce_motion_check.setChecked(self.settings.reduce_motion)
        self.reduce_motion_check.toggled.connect(self._set_reduce_motion)
        form.addRow(self.reduce_motion_check)
        form = self._settings_card(settings_layout, tr("app.0099"), "shield")
        self.history_check = self._check(tr("app.0100"), self.settings.save_history)
        self.history_check.setToolTip(tr("app.0101"))
        form.addRow(self.history_check)
        self.feedback_button.setToolTip(tr("app.0102"))
        glossary_form = self._settings_card(settings_layout, tr("app.0103"), "settings")
        self.glossary_checks = {}
        for key, title in (("game_terms", tr("app.0104")), ("official_lines", tr("app.0105")),
                           ("asr_correction", tr("app.0106")), ("teammate_only", tr("app.0107"))):
            check = self._check(title, getattr(self.settings, key))
            self.glossary_checks[key] = check
            glossary_form.addRow(check)
        self.glossary_checks["teammate_only"].setToolTip(
            tr("app.0108"))
        self.glossary_status = self._hint("")
        self.glossary_status.setTextFormat(Qt.TextFormat.PlainText)
        glossary_form.addRow(self.glossary_status)
        self.glossary_loading = LoadingSkeleton()
        glossary_form.addRow(self.glossary_loading)
        self._glossary_actions(glossary_form)
        settings_layout.addStretch()
        audio_page_layout.addStretch()
        self.audio_page = self._scroll(audio_page)
        self.tabs.addTab(self.audio_page, tr("app.audio_page"))
        self.settings_page = self._scroll(settings_page)
        self.tabs.addTab(self.settings_page, tr("app.0110"))

        self.history_page = QWidget()
        self.history_page.setObjectName("historyPage")
        history_layout = QVBoxLayout(self.history_page)
        history_layout.setContentsMargins(GUTTER, GAP, GUTTER, GUTTER)
        history_hint = self._hint(tr("app.0111"))
        history_hint.setToolTip(tr("app.0112"))
        history_layout.addWidget(history_hint)
        history_layout.addWidget(self.recent_group)
        self.history_state = self._hint("")
        self.history_state.setTextFormat(Qt.TextFormat.PlainText)
        history_layout.addWidget(self.history_state)
        self.history_loading = LoadingSkeleton()
        history_layout.addWidget(self.history_loading)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels((tr("app.0114"), tr("app.0115"), tr("app.0116"), tr("app.0117"), tr("app.0118")))
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setColumnWidth(0, 150)
        self.history_table.setColumnWidth(1, 260)
        self.history_table.setColumnWidth(2, 300)
        self.history_table.setColumnWidth(3, 75)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        history_layout.addWidget(self.history_table)
        history_buttons = QHBoxLayout()
        for label, callback in ((tr("app.0119"), self._request_history),
                                (tr("app.0120"), self._correct_history),
                                (tr("app.0121"), self._export_history),
                                (tr("app.0122"), self._clear_history),
                                (tr("app.0123"), self._clear_all_history)):
            button = GlassButton(label)
            button.clicked.connect(callback)
            button.setEnabled(self.history is not None)
            history_buttons.addWidget(button)
        history_buttons.addStretch()
        history_layout.addLayout(history_buttons)
        extra = QHBoxLayout()
        for label, action in ((tr("app.0124"), self._history_details), (tr("app.0125"), self._feedback_history),
                              (tr("app.0126"), self._review_feedback), (tr("app.0127"), self._import_history)):
            button = GlassButton(label)
            button.clicked.connect(action)
            button.setEnabled(self.history is not None or label == tr("app.0128"))
            extra.addWidget(button)
        extra.addStretch()
        history_layout.addLayout(extra)
        self.history_table.doubleClicked.connect(self._history_details)
        self.tabs.addTab(self.history_page, tr("app.0129"))
        self.runtime_page = self._scroll(self.diagnostics_page)
        self.tabs.addTab(self.runtime_page, tr("app.runtime_page"))
        self._build_shell()

    def _build_shell(self):
        self.surface = GlassSurface(self.settings.background_theme, self.settings.background_color)
        shell = QHBoxLayout(self.surface)
        shell.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        shell.setSpacing(GAP)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(tokens.SIDEBAR_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        side.setSpacing(INLINE)
        brand = QLabel("VoiceTrans")
        brand.setObjectName("brand")
        side.addWidget(brand)
        side.addSpacing(GAP)
        self.nav = QButtonGroup(self)
        icons = {self.quick_page: "chat", self.audio_page: "speaker", self.settings_page: "settings",
                 self.history_page: "clock", self.runtime_page: "activity"}
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            button = GlassButton(self.tabs.tabText(index), icon=icons[page])
            button.setProperty("navigation", True)
            button.setCheckable(True)
            self.nav.addButton(button, index)
            side.addWidget(button)
        self.nav.button(0).setChecked(True)
        self.nav.idClicked.connect(self._navigate)
        self.tabs.currentChanged.connect(lambda index: self.nav.button(index).setChecked(True))
        side.addStretch()
        guide = GlassButton(tr("app.0130"), icon="book")
        guide.clicked.connect(self._show_onboarding)
        side.addWidget(guide)
        side.addWidget(self._hint(f"v{APP_VERSION}"))
        self.nav_capsule = SelectionCapsule(sidebar)
        self.nav_capsule.select(self.nav.button(0), animated=False)
        self.tabs.currentChanged.connect(lambda index: self.nav_capsule.select(self.nav.button(index)))
        shell.addWidget(sidebar)
        content = QVBoxLayout()
        content.setSpacing(GAP)
        self.game_notice = Toast(self.surface)
        self.tabs.tabBar().hide()
        content.addWidget(self.tabs, 1)
        shell.addLayout(content, 1)
        self.setCentralWidget(self.surface)
        self.page_snapshot = Snapshot(self.tabs)
        self.skin_snapshot = Snapshot(self.surface)
        self.toast_timer = QTimer(self, singleShot=True)
        self.toast_timer.timeout.connect(self.game_notice.dismiss)
        self.detail_effect = DetailPanel(self.diagnostics_page)
        self.detail_effect.hide()
        self._apply_theme()
        self._refresh_glossary()
        order = [*self.nav.buttons(), guide, self.start_button, self.stop_button,
                 *self.game_group.buttons(), self.device_combo, self.refresh_button,
                 self.microphone_wizard_button, self.routing_button,
                 self.mode_combo, self.test_button, self.show_button, self.position_button,
                 self.talk_button, self.reverse_copy, self.ptt_hotkey_edit, self.mic_combo,
                 self.intro_button, self.settings_microphone_wizard_button, self.settings_update_button,
                 self.update_cancel_button, self.download_button,
                 self.import_button, self.model_pause_button, self.recent_list, self.history_table,
                 self.detail_toggle, self.feedback_button, self.update_button, self.caption_tools, *self.dock.buttons]
        for previous, following in zip(order, order[1:]):
            QWidget.setTabOrder(previous, following)

    def _build_game_bar(self):
        game_bar = QFrame()
        game_bar.setObjectName("glassCard")
        games = QHBoxLayout(game_bar)
        games.setContentsMargins(GAP, INLINE, GAP, INLINE)
        games.setSpacing(INLINE)
        self.game_group = QButtonGroup(self)
        game_order = {key: index for index, key in enumerate(tokens.SKINS)}
        for key in sorted(GAMES, key=lambda key: game_order.get(key, len(game_order))):
            button = GlassButton(GAMES[key])
            button.setProperty("gamePill", True)
            button.setCheckable(True)
            button.setChecked(key == self.settings.game)
            button.clicked.connect(lambda checked=False, game=key: self._select_game(game))
            self.game_group.addButton(button)
            games.addWidget(button)
        self.game_capsule = SelectionCapsule(game_bar)
        self.game_capsule.select(self.game_group.checkedButton(), animated=False)
        self.game_group.buttonClicked.connect(self.game_capsule.select)
        return game_bar

    def _stat(self, row, title, initial, name):
        frame = QFrame()
        frame.setObjectName("controlStat")
        contents = QHBoxLayout(frame)
        contents.setContentsMargins(INLINE, tokens.TIGHT, INLINE, tokens.TIGHT)
        contents.setSpacing(INLINE)
        icon = LineIcon(name, accented=True)
        contents.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(tokens.TIGHT)
        label = self._hint(title)
        label.setObjectName("statLabel")
        text.addWidget(label)
        value = self._compact(initial)
        value.setObjectName("statValue")
        text.addWidget(value)
        contents.addLayout(text, 1)
        row.addWidget(frame, 1)
        return value, icon

    def download_sizes(self):
        if not self._uses_cos_models():
            return {}
        size = self.model_combo.currentData()
        specs = self._model_channel.get("model_bundle", {}).get("component_compressed_bytes", {})
        sizes = {"M2M100 418M": specs.get("m2m100_418M")}
        bundle = self.catalog.get(size)
        sizes[f"Whisper {size}"] = bundle.size if bundle else specs.get(f"whisper-{size}")
        return sizes

    def _pick_theme_color(self, initial, title):
        picker = QColorDialog(initial, self)
        picker.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        picker.setWindowTitle(title)
        apply_dialog_theme(picker, self.settings)
        try:
            return picker.selectedColor() if picker.exec() else QColor()
        finally:
            picker.deleteLater()

    def _choose_accent(self):
        color = self._pick_theme_color(QColor(accent_color(self.settings.accent)), tr("app.0131"))
        if color.isValid():
            self.settings.game_accents[self.settings.game] = color.name()
            self._save_theme()

    def _select_background(self):
        self.settings.background_theme = self.background_combo.currentData()
        self._save_theme()

    def _choose_background(self):
        stored = self.settings.background_color
        current = QColor(stored if isinstance(stored, str) else "")
        if not current.isValid():
            current = QColor(DEFAULT_BACKGROUND_COLOR)
        color = self._pick_theme_color(current, tr("app.0132"))
        if color.isValid():
            self.settings.background_color = color.name()
            self.settings.background_theme = "custom"
            self._save_theme()

    def _reset_theme(self):
        self.settings.background_theme = "default"
        self.settings.background_color = ""
        self._reset_game_accent()

    def _reset_game_accent(self):
        self.settings.game_accents.pop(self.settings.game, None)
        self._save_theme()

    def _game_accent(self):
        return self.settings.game_accents.get(self.settings.game, tokens.skin_for(self.settings.game).accent)

    def _save_theme(self):
        self.settings.accent = self._game_accent()
        with QSignalBlocker(self.background_combo):
            self.background_combo.setCurrentIndex(self.background_combo.findData(self.settings.background_theme))
        self.settings.save()
        self._apply_theme()

    def _apply_theme(self):
        settings = self.settings
        self.surface.set_background(settings.background_theme, settings.background_color, settings.game)
        stylesheet = glass_styles(settings.accent, settings.game)
        if stylesheet != self.styleSheet():
            self.setStyleSheet(stylesheet)
        tint_icons(self, settings.accent)
        for effect in (self.caption_light, self.nav_capsule, self.game_capsule):
            effect.accent = accent_color(settings.accent)
            effect.update()
        if self.onboarding is not None:
            self.onboarding.apply_theme()
        if self.routing_wizard is not None:
            apply_dialog_theme(self.routing_wizard, settings)
        if hasattr(self, "overlay"):
            self.overlay.refresh_theme()
        self._sync_motion()

    def _set_reduce_motion(self, checked):
        self.settings.reduce_motion = checked
        self.settings.save()
        self._apply_theme()

    def _sync_motion(self):
        reduced = self.settings.reduce_motion
        for item in (*self.findChildren(LoadingSkeleton), self.caption_light, self.nav_capsule,
                     self.game_capsule, self.page_snapshot, self.skin_snapshot, self.detail_effect):
            item.set_reduced(reduced)
        for button in self.findChildren(GlassButton):
            button.set_reduced_motion(reduced)
        for icon in self.findChildren(LineIcon):
            icon.set_reduced_motion(reduced)
        self.dock.set_reduced(reduced)
        self.game_notice.set_reduced(reduced)

    def _navigate(self, index):
        if self.tabs.currentIndex() == index:
            return
        image = self.page_snapshot.capture()
        self.tabs.setCurrentIndex(index)
        self.page_snapshot.reveal(image, tokens.PAGE_MS)

    def _page_changed(self, index):
        page = self.tabs.widget(index)
        if page is self.history_page:
            self._request_history()
        elif page is self.settings_page:
            self._refresh_glossary()

    def _toggle_details(self, opened):
        if opened:
            self.details_panel.show()
            self.diagnostics_page.layout().activate()
        self.detail_effect.show_from(self.detail_toggle, self.details_panel.geometry(), opening=opened)
        if not opened:
            self.details_panel.hide()

    def _announce(self, text, *, success=False):
        self.game_notice.announce(text, reduced=self.settings.reduce_motion, success=success)
        self.toast_timer.start(tokens.SUCCESS_MS if success else tokens.TOAST_MS)

    def _refresh_glossary(self):
        self._glossary_request += 1
        request = self._glossary_request
        self.glossary_status.setText(tr("app.loading_game_glossary"))
        self.glossary_loading.show()
        QTimer.singleShot(tokens.GLOSSARY_LOAD_MS, self, lambda: self._show_glossary(request))

    def _show_glossary(self, request):
        if request != self._glossary_request:
            return
        self.glossary_loading.hide()
        try:
            data = read_default_glossary(self.settings.game)
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
            LOG.warning("Could not read game glossary: %s", error)
            text = tr("app.game_glossary_read_failed")
        else:
            text = (tr("app.game_glossary_ready") if any(value for value in data.values() if isinstance(value, (list, dict)))
                    else tr("app.game_glossary_pending"))
        self.glossary_status.setText(text)

    def _select_game(self, game):
        if game == self.settings.game:
            return
        image = self.skin_snapshot.capture()
        self.settings.game = game
        self._save_theme()
        self.skin_snapshot.reveal(image, tokens.SKIN_MS)
        self._refresh_glossary()
        self._announce(tr("app.0133", p0=GAMES[game]))
        if self.pipeline:
            self._restart_after_stop = True
            self.stop_translation()

    def _show_first_run(self):
        if not self.settings.onboarding_done:
            self._show_onboarding()
        elif sys.platform == "win32" and not self.settings.routing_intro_seen:
            self._show_routing_intro()

    def _show_onboarding(self):
        if self.onboarding and self.onboarding.isVisible():
            self.onboarding.raise_()
            return
        self.onboarding = Onboarding(self)
        self.onboarding.finished.connect(self._onboarding_finished)
        self.onboarding.open()

    def _onboarding_finished(self, result):
        destination = self.onboarding.destination
        self.onboarding = None
        if not self.settings.onboarding_done and sys.platform == "win32":
            self.settings.routing_intro_seen = True
        self.settings.onboarding_done = True
        self.settings.save()
        if result == QDialog.DialogCode.Accepted:
            self._navigate(self.tabs.indexOf(destination))

    def _fetch_model_catalog(self):
        try:
            self.catalog = fetch_catalog(self._model_channel)
            self.bridge.model_catalog.emit(self.catalog)
        except Exception as exc:
            LOG.info("Using built-in Small model bundle: %s", exc)
        finally:
            self.catalog_checked.set()

    def _catalog_ready(self, catalog):
        self.catalog = catalog
        self._refresh_model_status()

    @classmethod
    def _settings_card(cls, parent_layout, title, icon):
        card = GlassSection(title, icon)
        form = cls._form()
        card.body.addLayout(form)
        parent_layout.addWidget(card)
        return form

    @staticmethod
    def _form(parent=None):
        form = QFormLayout(parent)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(GAP)
        form.setVerticalSpacing(INLINE)
        return form

    @staticmethod
    def _row(form, text, widget, tip=""):
        if isinstance(widget, (QComboBox, QLineEdit, QSpinBox)):
            widget.setMaximumWidth(460)
        label = QLabel(text)
        label.setMinimumWidth(94)
        if tip:
            label.setToolTip(tip)
            widget.setToolTip(tip)
        form.addRow(label, widget)

    @staticmethod
    def _group(title, icon):
        group = GlassSection(title, icon)
        return group, group.body

    @classmethod
    def _quick_start_group(cls, title, icon):
        group, layout = cls._group(title, icon)
        group.setProperty("compactQuickStart", True)
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.setContentsMargins(INLINE, 1, INLINE, 1)
        layout.setSpacing(QUICK_START_GAP)
        return group, layout

    @staticmethod
    def _compact(text):
        label = CompactLabel(text)
        label.setObjectName("muted")
        return label

    @staticmethod
    def _scroll(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        page.setObjectName("pageBody")
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        label.setWordWrap(True)
        return label

    def _combo(self, entries, current):
        combo = QComboBox()
        for label, data in entries:
            combo.addItem(label, data)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.currentIndexChanged.connect(self._save_settings)
        return combo

    def _spin(self, value, minimum, maximum):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.valueChanged.connect(self._save_settings)
        return spin

    def _check(self, label, value):
        check = QCheckBox(label)
        check.setChecked(value)
        check.toggled.connect(self._save_settings)
        return check

    def _get_model_dir(self) -> Path:
        return get_model_dir(self.settings, default=MODEL_DIR)

    def _models_ready(self) -> bool:
        folder = self._get_model_dir()
        return (model_ready(folder / f"whisper-{self.model_combo.currentData()}", WHISPER_FILES)
                and model_ready(folder / "m2m100_418M", M2M_FILES))

    def _engine_controls(self, form):
        self.speed_combo = QComboBox()
        for mode in SPEED_MODES:
            name = tr(f"engine.mode_{mode}")
            self.speed_combo.addItem(name, mode)
            self._row(form, name, self._hint(tr(f"engine.config_{mode}")))
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(self.settings.speed_mode))
        self.speed_combo.activated.connect(self._select_speed_mode)
        self._row(form, tr("engine.preset"), self.speed_combo)
        form.addRow(self._hint(tr("engine.privacy")))
        self.engine_credentials_button = GlassButton(tr("engine.credentials"), icon="settings")
        self.engine_credentials_button.clicked.connect(self._edit_engine_credentials)
        form.addRow(self.engine_credentials_button)
        self.engine_key_hint = self._hint(tr("engine.credentials_required"))
        self.engine_key_hint.setHidden(self.settings.online_configured())
        form.addRow(self.engine_key_hint)

    def _sync_engine_preset(self):
        widgets = (self.speed_combo, self.model_combo, self.fast_check, *self.glossary_checks.values())
        blockers = [QSignalBlocker(widget) for widget in widgets]
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(self.settings.speed_mode))
        self.model_combo.setCurrentIndex(self.model_combo.findData(self.settings.whisper_model))
        self.fast_check.setChecked(self.settings.fast_mode)
        for key, check in self.glossary_checks.items():
            check.setChecked(getattr(self.settings, key))
        del blockers

    def _select_speed_mode(self, index):
        previous = (self.settings.speed_mode, self.settings.whisper_model, self.settings.fast_mode)
        selected = self.settings.select_speed_mode(self.speed_combo.itemData(index))
        self._sync_engine_preset()
        if not selected:
            self.engine_key_hint.show()
            self.engine_credentials_button.setFocus()
            self.statusBar().showMessage(tr("engine.credentials_required"), 5000)
            return
        self._save_settings()
        current = (self.settings.speed_mode, self.settings.whisper_model, self.settings.fast_mode)
        if self.pipeline and previous != current:
            self._restart_after_stop = True
            self.stop_translation()

    def _edit_engine_credentials(self):
        dialog = AzureCredentialsDialog(self.settings, self)
        if not dialog.exec():
            return
        values = dialog.values()
        changed = any(getattr(self.settings, name) != value for name, value in values.items())
        was_online = self.settings.speed_mode == "extreme"
        for name, value in values.items():
            setattr(self.settings, name, value)
        configured = self.settings.online_configured()
        self.engine_key_hint.setHidden(configured)
        if was_online and not configured:
            self.settings.select_speed_mode("balanced")
            self._sync_engine_preset()
            self.statusBar().showMessage(tr("engine.credentials_required"), 5000)
        self._save_settings()
        if self.pipeline and was_online and changed:
            self._restart_after_stop = True
            self.stop_translation()

    @staticmethod
    def _torch_version_ok() -> bool:
        try:
            installed = version("torch")
        except PackageNotFoundError:
            try:
                import torch
                installed = torch.__version__
            except Exception:
                return False
        try:
            return Version(installed.split("+")[0]) >= Version("2.6")
        except ValueError:
            return False

    def _refresh_model_status(self) -> None:
        size = self.model_combo.currentData()
        folder = self._get_model_dir()
        whisper = model_ready(folder / f"whisper-{size}", WHISPER_FILES)
        m2m_dir = folder / "m2m100_418M"
        m2m = model_ready(m2m_dir, M2M_FILES)
        if self.pipeline is None:
            self.model_stat.setText(size if whisper else tr("app.0134"))
        model_status = (
            tr("app.0135", p0=size, p1=tr('app.0313') if whisper else tr('app.0134'), p2=tr('app.0313') if m2m else tr('app.0134'))
            + (tr("app.0136") if not self._torch_version_ok() else "")
        )
        self.model_status_label.setText(tr("app.model_status_compact", p0=size, p1='✓' if whisper else '×', p2='✓' if m2m else '×'))
        self.model_status_label.setToolTip(model_status)
        self.model_status_label.setAccessibleDescription(model_status)
        if not self._model_busy and not self._model_error:
            self.model_state.setText(tr("app.model_download_or_import") if not (whisper and m2m) else "")
            self.model_state.setVisible(not (whisper and m2m))
        self.download_button.setEnabled(not self._model_busy and not (whisper and m2m))
        stopping = self._stop_thread is not None and self._stop_thread.is_alive()
        if (hasattr(self, "start_button") and self.pipeline is None and not stopping
                and not getattr(self, "_model_busy", False)):
            self.start_button.setEnabled(True)
            self.start_button.setToolTip("" if whisper and m2m else tr("app.start_models_missing"))

    def _save_settings(self, *_):
        if not hasattr(self, "duration_spin") or self._model_moving:
            return
        settings = self.settings
        old_device = settings.device_id
        old_hotkey = (settings.hotkey, settings.ptt_hotkey)
        old_normal = settings.normal_window
        old_fast = settings.fast_mode
        old_model = settings.whisper_model
        old_compute = settings.compute_device
        if self.device_combo.currentData():
            settings.device_id = str(self.device_combo.currentData())
        settings.language = self.language_combo.currentData()
        settings.whisper_model = self.model_combo.currentData()
        settings.compute_device = self.compute_combo.currentData()
        if hasattr(self, "model_source"):
            settings.model_source = self.model_source.currentData()
        settings.fast_mode = self.fast_check.isChecked()
        settings.font_size = self.font_spin.value()
        settings.overlay_screen = int(self.screen_combo.currentData() or 0)
        settings.overlay_x, settings.overlay_y = self.x_spin.value(), self.y_spin.value()
        settings.overlay_opacity = self.opacity_spin.value()
        settings.show_original = self.original_check.isChecked()
        for label in (self.original_label, self.previous_original):
            label.setVisible(self._has_result and bool(label.text())
                             and (settings.show_original or label.property("bilingual")))
        settings.save_history = self.history_check.isChecked()
        settings.normal_window = self.mode_combo.currentData() == "desktop"
        settings.click_through = self.click_check.isChecked() and not settings.normal_window
        if settings.normal_window and self.click_check.isChecked():
            self.click_check.blockSignals(True)
            self.click_check.setChecked(False)
            self.click_check.blockSignals(False)
        settings.hotkey = self.hotkey_edit.text().strip()
        settings.ptt_hotkey = self.ptt_hotkey_edit.text().strip()
        if self.mic_combo.currentData():
            settings.microphone_id = str(self.mic_combo.currentData())
        for key, check in self.glossary_checks.items():
            enabled = check.isChecked()
            setattr(settings, key, enabled)
            if self.pipeline:
                setattr(self.pipeline.settings, key, enabled)
        settings.vad_level = self.vad_spin.value()
        settings.translation_timeout = float(self.timeout_spin.value())
        settings.max_task_age = float(self.age_spin.value())
        settings.subtitle_seconds = float(self.duration_spin.value())
        settings.save()
        self._update_device_hint()
        if hasattr(self, "overlay"):
            was_visible = self.overlay.isVisible()
            if old_normal != settings.normal_window:
                self.overlay.close()
                self.overlay = SubtitleOverlay(settings)
                self.overlay.position_changed.connect(self._overlay_moved)
                if was_visible:
                    self.overlay.show()
            else:
                self.overlay.apply_settings()
            if old_hotkey != (settings.hotkey, settings.ptt_hotkey):
                self._start_hotkey()
            self._refresh_model_status()
        if self.pipeline and (old_device != settings.device_id or old_fast != settings.fast_mode
                              or old_model != settings.whisper_model or old_compute != settings.compute_device):
            self._restart_after_stop = True
            self.stop_translation()
        self._update_mode_hint()

    def _mode_changed(self) -> None:
        game_mode = self.mode_combo.currentData() == "game"
        self.click_check.blockSignals(True)
        self.click_check.setChecked(game_mode)
        self.click_check.blockSignals(False)
        self._save_settings()
        self._update_mode_hint()
        self._test_overlay()

    def _update_mode_hint(self) -> None:
        if self.mode_combo.currentData() == "game":
            self.click_check.setEnabled(True)
            self.mode_hint.setText(tr("app.0137"))
            self.mode_hint.setToolTip(tr("app.0138"))
            self.mode_combo.setToolTip(self.mode_hint.toolTip())
            self.mode_hint.show()
            self.position_button.setText(tr("app.0139") if self.click_check.isChecked() else tr("app.0140"))
        else:
            self.click_check.setEnabled(False)
            self.mode_hint.setText(tr("app.0141"))
            self.mode_combo.setToolTip(self.mode_hint.text())
            self.mode_hint.hide()
            self.position_button.setText(tr("app.0142"))

    def _toggle_position_edit(self) -> None:
        if self.mode_combo.currentData() == "game":
            self.click_check.setChecked(not self.click_check.isChecked())
            self._update_mode_hint()
        self.overlay.add_subtitle(tr("app.0143"), duration=30)
        self.overlay.show()
        self.show_button.setText(tr("app.0144"))

    def _test_overlay(self) -> None:
        self.overlay.add_subtitle(tr("app.0145"), "Two A main", duration=20)
        self.overlay.show()
        self.overlay.raise_()
        self.show_button.setText(tr("app.0146"))

    def _toggle_overlay(self) -> None:
        self.overlay.setVisible(not self.overlay.isVisible())
        self.show_button.setText(tr("app.0147") if self.overlay.isVisible() else tr("app.0148"))

    def refresh_devices(self) -> None:
        try:
            devices = list_devices()
        except Exception as exc:
            self._status(tr("app.0149", p0=exc))
            return
        self._apply_devices(devices)

    def _request_devices(self) -> None:
        if not self.refresh_button.isEnabled():
            return
        self.refresh_button.setText(tr("app.0150"))
        self.refresh_button.setEnabled(False)

        def task():
            try:
                devices = self._enumerate_audio_devices()
                self.bridge.devices_ready.emit(devices, "")
            except Exception as exc:
                self.bridge.devices_ready.emit(None, str(exc) or type(exc).__name__)

        threading.Thread(target=task, name="refresh-audio-devices", daemon=True).start()

    @staticmethod
    def _enumerate_audio_devices():
        if sys.platform != "win32":
            return list_devices()
        from ctypes import windll

        com = windll.ole32
        hr = com.CoInitializeEx(None, 0)
        if hr < 0:
            raise OSError(tr("app.0151", p0=hr & 0xffffffff))
        try:
            return list_devices()
        finally:
            com.CoUninitialize()

    def _finish_device_refresh(self, devices, error: str) -> None:
        self.refresh_button.setText(tr("app.0152"))
        self.refresh_button.setEnabled(True)
        if error:
            self._status(tr("app.0153", p0=error))
        else:
            self._apply_devices(devices)
        self.devices_refreshed.emit(not error)

    def _apply_devices(self, devices, *, persist=True) -> None:
        self.audio_devices = list(devices)
        devices = self.audio_devices
        with QSignalBlocker(self.device_combo):
            self.device_combo.clear()
            for device in devices:
                system_sound = device.loopback or is_routed_source(device)
                prefix = tr("app.0154") if system_sound else tr("app.0155")
                self.device_combo.addItem(f"{prefix}：{device.name}", device.id)
                self.device_combo.setItemData(self.device_combo.count() - 1, system_sound,
                                              Qt.ItemDataRole.UserRole + 1)
            index = self.device_combo.findData(self.settings.device_id)
            if index < 0:
                index = next((i for i, device in enumerate(devices) if device.loopback), 0)
            self.device_combo.setCurrentIndex(index)
        microphone = preferred_microphone(devices, self.settings.microphone_id)
        self._apply_microphones(devices, microphone)
        if not persist:
            self._update_device_hint()
            return
        old = self.settings.microphone_id
        self._save_settings()
        if microphone and old != microphone.id:
            self.statusBar().showMessage(tr("app.0156", p0=microphone.name), 5000)

    def _save_audio_setup(self, devices, microphone_id, source):
        settings = self.settings
        old_device = settings.device_id
        verified = source.id if is_routed_source(source) else ""
        # Persist a copy before publishing the selections to this window or its pipeline.
        replace(settings, microphone_id=microphone_id, device_id=source.id, routing_verified_id=verified).save()
        settings.microphone_id = microphone_id
        settings.device_id = source.id
        settings.routing_verified_id = verified
        self._apply_devices(devices, persist=False)
        if self.pipeline and old_device != source.id:
            self._restart_after_stop = True
            self.stop_translation()

    def _apply_microphones(self, devices, microphone):
        with QSignalBlocker(self.mic_combo):
            self.mic_combo.clear()
            for device in devices:
                if not device.loopback:
                    self.mic_combo.addItem(device.name, device.id)
            if microphone:
                self.mic_combo.setCurrentIndex(self.mic_combo.findData(microphone.id))

    def _update_device_hint(self) -> None:
        mic = self.mic_combo.currentText()
        microphone_warning = tr("app.microphone_virtual_warning") if mic and any(
            token in mic.casefold() for token in ("cable", "virtual", "sonar", "voicemeeter")
        ) else ""
        self.microphone_stat.setText(
            (tr("app.0157", p0=mic) + microphone_warning) if mic else tr("app.0158"))
        route = next((device for device in self.audio_devices
                      if device.id == self.device_combo.currentData() and is_routed_source(device)), None)
        routed = route is not None and route.id == self.settings.routing_verified_id
        self.routing_status.setText(tr("app.0159", p0=route.name) if routed else
                                    tr("app.0160", p0=route.name) if route else tr("app.0161"))
        self.device_hint.show()
        if self.device_combo.currentIndex() < 0:
            self.device_hint.setText(tr("app.0162"))
            self.device_hint.setStyleSheet("color: #ffd699;")
        elif not self.device_combo.currentData(Qt.ItemDataRole.UserRole + 1):
            self.device_hint.setText(
                tr("app.0163"))
            self.device_hint.setStyleSheet("color: #ffd699;")
        else:
            name = self.device_combo.currentText().casefold()
            virtual = any(token in name for token in ("cable", "virtual", "sonar", "fxsound", "voicemeeter"))
            self.device_hint.setText(tr("app.virtual_source_warning") if virtual else tr("app.0164"))
            self.device_hint.setStyleSheet("color: #ffd699;" if virtual else "")
            self.device_hint.setVisible(virtual)
        self.device_combo.setToolTip(self.device_hint.text())

    def _overlay_moved(self, screen: int, x: int, y: int) -> None:
        for widget in (self.screen_combo, self.x_spin, self.y_spin):
            widget.blockSignals(True)
        self.screen_combo.setCurrentIndex(screen)
        self.x_spin.setValue(x)
        self.y_spin.setValue(y)
        for widget in (self.screen_combo, self.x_spin, self.y_spin):
            widget.blockSignals(False)

    def start_translation(self) -> None:
        if self._model_busy:
            return
        self._save_settings()
        if not self._models_ready():
            QMessageBox.warning(self, tr("app.0165"),
                                tr("app.choose_models_before_start"))
            return
        if not self._torch_version_ok():
            self._start_unavailable(tr("app.start_pytorch_too_old"))
            return
        if not self.device_combo.currentData():
            self._start_unavailable(tr("app.start_device_missing"))
            return
        self._recent_trace.clear()
        self._task_timings.clear()
        self._active_icon_tasks.clear()
        self.last_discard.hide()
        self._trace_token = token = object()
        self.pipeline = Pipeline(self._effective_settings(), self.bridge.status.emit, self.bridge.level.emit,
                                 self.bridge.result.emit, self.bridge.diagnostic.emit,
                                 self.bridge.runtime.emit, self.history,
                                 on_trace=lambda trace: self.bridge.trace.emit((token, trace)),
                                 custom_rules=self.custom_rules)
        self._last_diagnostics = self.pipeline.diagnostics
        self.start_button.setText(tr("app.0171"))
        self._caption_state("loading", tr("app.caption_models_loading"))
        self.run_icon.set_mode("spin", highlight=True)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pipeline.start()

    def _start_unavailable(self, message: str) -> None:
        self._status(tr("app.translation_not_started"))
        self._caption_state("error", message)

    def stop_translation(self) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            return
        self.pipeline = None
        self.engine_runtime.hide()
        self._active_icon_tasks.clear()
        self.run_icon.set_mode("idle")
        self.audio_icon.set_mode("idle")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.volume.hide()
        self.audio_icon.hide()
        self.diagnostic_label.hide()
        self._status(tr("app.translation_stopped"))
        self.device_stat.setText(tr("app.0172"))
        self._caption_state("default" if self._has_result else "empty", "" if self._has_result else FIRST_CAPTION_HINT)

        def task():
            pipeline.stop()
            self.bridge.stopped.emit()

        self._stop_thread = threading.Thread(target=task, name="pipeline-stop", daemon=True)
        self._stop_thread.start()

    def _on_stopped(self) -> None:
        self.start_button.setText(tr("app.0173"))
        self.start_button.setEnabled(True)
        if self._restart_after_stop:
            self._restart_after_stop = False
            self.start_translation()

    def _caption_state(self, state, hint=""):
        self.caption_loading.setVisible(state == "loading")
        self.caption_hint.setText(hint)
        self.caption_hint.setVisible(bool(hint))
        if not self._has_result:
            text = {"loading": tr("app.caption_loading"), "error": tr("app.caption_unavailable")}.get(state, "")
            self.chinese_label.setText(text)
            self.chinese_label.setVisible(bool(text))

    def _status(self, message: str) -> None:
        self.status_label.setText(tr("app.0174", p0=message))
        self.status_label.show()
        self.statusBar().showMessage(message)
        if message.startswith("音频采集失败"):
            self._caption_state("error", tr("app.capture_failed_hint", p0=message))
            self.diagnostic_label.setText(tr("app.capture_failed_hint", p0=message))
            self.diagnostic_label.show()
        elif message.startswith("音频设备已连接") and self.pipeline is not None:
            self._caption_state("loading", tr("app.capture_resumed_hint"))
        if message.startswith((tr("app.model_download_failed_prefix"), tr("app.model_import_failed_prefix"))):
            self._model_error = message
            self.model_state.setText(message + tr("app.model_error_retry"))
            self.model_state.show()
        if message.startswith(tr("app.0175")):
            self._caption_state("error", message + tr("app.caption_error_retry"))
            self._active_icon_tasks.clear()
            self.run_icon.set_mode("idle")
            self.audio_icon.set_mode("idle")
            self.start_button.setText(tr("app.0176"))
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def _diagnostic(self, message: str) -> None:
        self.diagnostic_label.setText(tr("app.0177", p0=message))
        self.diagnostic_label.show()
        self.statusBar().showMessage(message)
        if self.pipeline:
            self.failures_label.setText(tr("app.0178", p0=self.pipeline.failures))
        if message.startswith(tr("app.0179")):
            self._record_trace(TaskTrace(0, tr("app.0180"), message, discarded=True))

    def _receive_trace(self, payload) -> None:
        token, trace = payload
        if token is self._trace_token:
            self._record_trace(trace)

    def _record_trace(self, trace: TaskTrace) -> None:
        self._recent_trace.append(trace)
        if self.pipeline is not None:
            if trace.stage == tr("app.0181"):
                self._active_icon_tasks.add((trace.task_id, "asr"))
            elif trace.stage == tr("app.0182"):
                self._active_icon_tasks.add((trace.task_id, "translation"))
            if trace.stage in {tr("app.0183"), tr("app.0184")}:
                self._active_icon_tasks.discard((trace.task_id, "asr"))
            if trace.stage in {tr("app.0185"), tr("app.0186")}:
                self._active_icon_tasks.discard((trace.task_id, "translation"))
            self._refresh_run_icon()
        if trace.discarded:
            task = f" #{trace.task_id}" if trace.task_id else ""
            self.last_discard.setText(tr("app.0187", p0=task, p1=trace.detail))
            self.last_discard.show()
        if trace.timings:
            times = self._task_timings.pop(trace.task_id, {})
            times.update(trace.timings)
            self._task_timings[trace.task_id] = times
            if len(self._task_timings) > 10:
                del self._task_timings[next(iter(self._task_timings))]

    def _runtime(self, message: str) -> None:
        if self.pipeline:
            self.start_button.setText(tr("app.0188"))
            self._refresh_run_icon()
            self._caption_state("default" if self._has_result else "empty",
                                "" if self._has_result else tr("app.caption_listening"))
        self.runtime_label.setText(tr("app.0189", p0=message))
        self.runtime_label.show()
        runtime, marker, model = message.partition(" · Whisper ")
        devices = runtime.rsplit(" · ", 1)[0].replace(tr("app.0190"), "").replace(tr("app.0191"), "")
        self.device_stat.setText(devices)
        if self.pipeline and self.settings.speed_mode == "extreme":
            self.engine_runtime.setText(runtime.rsplit(" · ", 1)[0])
            self.engine_runtime.show()
        if marker and model:
            self.model_stat.setText(model)

    @staticmethod
    def _set_caption_latency(label, seconds, hint):
        valid = math.isfinite(seconds) and seconds >= 0
        label.setText(tr("app.0197", p0=seconds) if valid else "")
        label.setToolTip(hint if valid else "")
        label.setVisible(valid)

    def _show_caption(self, translated, original, language, seconds, *, historical=False):
        self._has_result = True
        self._caption_state("default")
        self.original_label.setProperty("bilingual", language == "zh")
        self.language_label.setText(tr("app.0193", p0=LANGUAGES.get(language, language)) if language else "")
        self.language_label.setVisible(bool(language))
        self.original_label.setText(tr("app.0194", p0=original) if original else "")
        self.original_label.setVisible((self.settings.show_original or language == "zh") and bool(original))
        if self.chinese_label.property("quiet"):
            self.chinese_label.setProperty("quiet", False)
            self.chinese_label.style().unpolish(self.chinese_label)
            self.chinese_label.style().polish(self.chinese_label)
        self.chinese_label.setText(translated)
        self.chinese_label.show()
        hint = tr("app.caption_latency_historical") if historical else tr("app.caption_latency_measured")
        self._set_caption_latency(self.latency_stat, seconds, hint)

    def _result(self, result: SubtitleResult) -> None:
        self._recent_results.append(result)
        if self._has_result:
            self.previous_caption.setText(self.chinese_label.text())
            self.previous_caption.show()
            self._set_previous_original(self.original_label.text(), self.original_label.property("bilingual"))
            self.previous_latency.setText(self.latency_stat.text())
            self.previous_latency.setToolTip(self.latency_stat.toolTip())
            self.previous_latency.setVisible(bool(self.previous_latency.text()))
        self._has_live_result = True
        display_original = result.display_original or result.original
        self.overlay.add_subtitle(result.chinese, display_original,
                                  always_bilingual=result.language == "zh" and bool(result.display_original))
        total = result.total_seconds if result.audio_end_time is None else time.monotonic() - result.audio_end_time
        self._show_caption(result.chinese, display_original, result.language, total)
        self.result_icon.set_mode("done", highlight=True, restart=True)
        self.caption_light.animate()
        confidence = result.confidence
        valid = confidence is not None and math.isfinite(confidence) and 0 <= confidence <= 1
        self.confidence_label.setVisible(valid)
        if valid:
            self.confidence_label.setText(tr("app.0192", p0=confidence))
        self.failures_label.setText(tr("app.0178", p0=self.pipeline.failures) if self.pipeline else "")
        self.statusBar().showMessage(tr("app.0196", p0=result.chinese[:55]))
        timings = ((tr("app.recognition_timing"), result.asr_seconds), (tr("app.translation_timing"), result.translation_seconds), (tr("app.caption_timing"), total))
        self.latency_label.setText(" · ".join(f"{name} {seconds:.2f}s" for name, seconds in timings
                                             if math.isfinite(seconds) and seconds >= 0))
        if result.task_id is not None:
            if self.pipeline is not None:
                self.pipeline.diagnostics.add(tr("app.0199"))
            self._record_trace(TaskTrace(result.task_id, tr("app.0200"), tr("app.0201", p0=total)))
        if self.tabs.currentWidget() is self.history_page:
            self._refresh_history()
        elif self.settings.save_history:
            self._refresh_recent_translations()

    def _refresh_run_icon(self):
        running = bool(self._active_icon_tasks) or self.start_button.text() == tr("app.0202")
        self.run_icon.set_mode("spin" if running else "idle", highlight=running)

    def _show_audio_level(self, level):
        if self.pipeline is not None:
            self.volume.show()
            self.audio_icon.show()
        self.volume.setValue(int(level * 100))
        self.audio_icon.set_level(level)
        active = self.pipeline is not None and level >= 0.02
        self.audio_icon.set_mode("pulse" if active else "idle", highlight=active)

    def _copy_feedback(self) -> None:
        report = "\n".join((
            tr("app.0203"),
            tr("app.0204"),
            tr("app.0205"),
            tr("app.0206", p0=self.original_label.text().removeprefix(tr("app.0316"))),
            tr("app.0207", p0=self.chinese_label.text()),
            tr("app.0208", p0=self.language_label.text().removeprefix(tr("app.0317"))),
            tr("app.0209", p0=self.runtime_label.text().removeprefix(tr("app.0318"))),
            tr("app.0210", p0=self.latency_label.text()),
            tr("app.0211", p0=self.diagnostic_label.text().removeprefix(tr("app.0319"))),
            tr("app.0212", p0=self.settings.whisper_model),
            tr("app.0213"),
        ))
        report += tr("app.0214")
        report += "\n".join(trace.line() for trace in self._recent_trace) or tr("app.0215")
        report += tr("app.0216")
        for task_id, times in self._task_timings.items():
            report += f"#{task_id} " + " · ".join(tr("app.0217", p0=name, p1=value) for name, value in times.items()) + "\n"
        if not self._task_timings:
            report += tr("app.0218")
        if self._last_diagnostics:
            report += tr("app.0219") + self._last_diagnostics.summary()
        report += "\n" + tr("app.feedback_evidence") + "\n"
        for item in self._recent_results:
            confidence = (f"{item.confidence:.3f}" if item.confidence is not None
                          and math.isfinite(item.confidence) else tr("app.feedback_confidence_unknown"))
            report += tr("app.feedback_entry", p0=item.original, p1=item.chinese,
                         p2=item.language, p3=confidence) + "\n"
        QApplication.clipboard().setText(report)
        QMessageBox.information(self, tr("app.0220"), tr("app.0221"))

    def _check_update(self) -> None:
        if self._update_busy:
            return
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            QMessageBox.information(self, tr("app.0222"), tr("app.0223"))
            return
        self._set_update_busy(True)
        try:
            channel = load_channel()
        except Exception as exc:
            self._update_error(str(exc))
            return
        self._domestic_update = channel["channel"] == "domestic"
        self._set_update_status(tr("app.0224"))

        def task():
            try:
                self.bridge.update_checked.emit(check_for_update(channel))
            except Exception as exc:
                LOG.exception("Update check failed")
                self.bridge.update_error.emit(str(exc))

        threading.Thread(target=task, name="update-check", daemon=True).start()

    def _update_checked(self, release) -> None:
        if release is not None and release.source:
            self._domestic_update = release.source == "domestic"
        if release is None:
            self._set_update_busy(False)
            self.update_version_label.setText(tr("app.0225", p0=APP_VERSION, p1=APP_VERSION))
            self._set_update_status(tr("app.0226", p0=APP_VERSION))
            return
        self.update_version_label.setText(tr("app.0227", p0=APP_VERSION, p1=release.version))
        self.update_notes_label.setText(tr("app.0228", p0=release.notes[:600] or tr("app.0315")))
        self.update_notes_label.show()
        self._set_update_status(tr("app.0229", p0=release.version))
        box = QMessageBox(self)
        box.setWindowTitle(tr("app.0230"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        install_hint = (tr("app.0231") if self._domestic_update else
                        tr("app.0232"))
        box.setText(tr("app.0233", p0=APP_VERSION, p1=release.version, p2=release.notes[:600] or tr("app.0315"), p3=install_hint))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            self._set_update_busy(False)
            return
        self._set_update_status(tr("app.0234", p0=release.version))
        for button in (self.update_button, self.settings_update_button):
            button.setText(tr("app.0235"))
        self.update_bar.setValue(0)
        self.update_bar.show()
        self.update_cancel_button.show()
        self._update_cancel_event.clear()

        def task():
            try:
                RESULT.parent.mkdir(parents=True, exist_ok=True)
                RESULT.write_text(f"开始下载：{release.installer_name}\n", encoding="utf-8")
                path = download_installer(release, self.bridge.update_progress.emit,
                                          cancel=self._update_cancel_event,
                                          speed=self.bridge.update_speed.emit)
                with RESULT.open("a", encoding="utf-8") as log:
                    log.write(f"下载完成：{path}\n")
                self.bridge.update_ready.emit((path, release))
            except UpdateCancelled:
                self.bridge.update_cancelled.emit()
            except Exception as exc:
                LOG.exception("Update download failed")
                with RESULT.open("a", encoding="utf-8") as log:
                    log.write(f"更新失败：下载或校验失败：{exc}\n")
                self.bridge.update_error.emit(str(exc))

        threading.Thread(target=task, name="update-download", daemon=True).start()

    def _update_progress(self, value: int) -> None:
        self.update_bar.setValue(value)

    def _update_speed(self, bytes_per_second: float) -> None:
        self._set_update_status(
            tr("app.0236", p0=self.update_bar.value(), p1=bytes_per_second / 1024**2))

    def _cancel_update(self) -> None:
        self._update_cancel_event.set()
        self.update_cancel_button.setEnabled(False)
        self._set_update_status(tr("app.0237"))

    def _update_cancelled(self) -> None:
        self._set_update_busy(False)
        self._set_update_status(tr("app.0238"))

    def _update_ready(self, payload: tuple[Path, object]) -> None:
        path, release = payload
        try:
            if self._domestic_update:
                start_silent_installer(path, release)
            else:
                start_installer(path, release)
        except Exception as exc:
            self._update_error(tr("app.0239", p0=exc))
            return
        self._set_update_status(tr("app.0240"))
        self.close()
        QApplication.quit()

    def _update_error(self, message: str) -> None:
        self._set_update_busy(False)
        self._set_update_status(tr("app.0241", p0=message))
        QMessageBox.warning(self, tr("app.0242"), message)

    def _set_update_busy(self, busy: bool) -> None:
        self._update_busy = busy
        for button in (self.update_button, self.settings_update_button):
            button.setText(tr("app.0243") if busy else tr("app.0244"))
            button.setEnabled(not busy)
        if not busy:
            self.update_bar.hide()
            self.update_cancel_button.hide()
            self.update_cancel_button.setEnabled(True)

    def _set_update_status(self, text: str) -> None:
        self.update_label.setText(text)
        self.update_label.setVisible(bool(text))

    def _show_update_result(self) -> None:
        try:
            message = RESULT.read_text(encoding="utf-8-sig").strip()
            RESULT.unlink(missing_ok=True)
        except OSError:
            return
        if message:
            self._set_update_status(message)
            if "更新成功；已重开新程序" in message and "更新失败" not in message:
                QMessageBox.information(self, tr("app.0245"), message)
            else:
                QMessageBox.warning(self, tr("app.0245"), message)

    def _open_recent_translation(self, item, _column):
        entry_id = item.data(0, Qt.ItemDataRole.UserRole)
        self._navigate(self.tabs.indexOf(self.history_page))
        for row in range(self.history_table.rowCount()):
            first = self.history_table.item(row, 0)
            if first and first.data(Qt.ItemDataRole.UserRole) == entry_id:
                self.history_table.selectRow(row)
                self.history_table.scrollToItem(first)
                self.history_table.setFocus()
                break

    def _refresh_recent_translations(self):
        read_failed = False
        try:
            entries = self.history.recent(3, target_language="zh") if self.history is not None else []
            message = EMPTY_HISTORY if self.history is not None else tr("app.recent_history_unavailable")
        except Exception:
            LOG.exception("Could not read recent translations")
            entries = []
            message = tr("app.recent_history_read_failed")
            read_failed = True
        self.recent_list.clear()
        for entry in entries:
            timestamp = entry.created_at
            try:
                timestamp = datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
            except ValueError:
                pass
            text = entry.corrected_chinese or entry.chinese
            item = QTreeWidgetItem((text, timestamp))
            item.setData(0, Qt.ItemDataRole.UserRole, entry.id)
            item.setToolTip(0, text)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.recent_list.addTopLevelItem(item)
        self.recent_hint.setText(message)
        self.recent_hint.setVisible(not entries)
        self.recent_list.setVisible(bool(entries))
        self.recent_group.setVisible(bool(entries) or read_failed)
        if entries:
            hint = self.recent_list.sizeHintForRow(0)
            self.recent_list.setFixedHeight(max(hint, self.recent_list.fontMetrics().height()) * len(entries)
                                            + 2 * self.recent_list.frameWidth())
        if self._has_live_result or self.pipeline is not None:
            return
        self._restore_history_caption(entries)

    def _set_previous_original(self, text, bilingual):
        self.previous_original.setText(text)
        self.previous_original.setProperty("bilingual", bool(bilingual))
        self.previous_original.setVisible(bool(text) and (self.settings.show_original or bilingual))

    def _restore_history_caption(self, entries):
        self.previous_caption.hide()
        self.previous_latency.hide()
        self._set_previous_original("", False)
        if entries:
            latest = entries[0]
            original = latest.display_original if latest.language == "zh" else latest.original
            self._show_caption(latest.corrected_chinese or latest.chinese, original,
                               latest.language, latest.total_seconds, historical=True)
            if len(entries) > 1:
                previous = entries[1]
                self.previous_caption.setText(previous.corrected_chinese or previous.chinese)
                self.previous_caption.show()
                original = previous.display_original if previous.language == "zh" else previous.original
                self._set_previous_original(tr("app.0194", p0=original) if original else "",
                                            previous.language == "zh")
                self._set_caption_latency(self.previous_latency, previous.total_seconds,
                                          tr("app.caption_latency_historical"))
        else:
            self._has_result = False
            self.original_label.hide()
            self.language_label.hide()
            self.latency_stat.hide()
            self._caption_state("empty", FIRST_CAPTION_HINT)

    def _request_history(self):
        if self._history_pending:
            return
        self._history_pending = True
        self.history_state.setText(tr("app.loading_local_history"))
        self.history_state.show()
        self.history_loading.show()
        QTimer.singleShot(tokens.HISTORY_LOAD_MS, self, self._refresh_history)

    def _refresh_history(self) -> None:
        self._history_pending = False
        self.history_loading.hide()
        self._refresh_recent_translations()
        if self.history is None:
            self.history_state.setText(tr("app.history_unavailable", p0=self.history_error))
            self.history_state.show()
            return
        try:
            entries = self.history.recent(100)
        except Exception as exc:
            self.history_state.setText(tr("app.history_read_failed", p0=exc))
            self.history_state.show()
            return
        self.history_state.setText(EMPTY_HISTORY)
        self.history_state.setVisible(not entries)
        self.history_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (entry.created_at.replace("T", " ")[:19], entry.original,
                      entry.corrected_chinese or entry.chinese,
                      (LANGUAGES.get(entry.language, entry.language) if entry.target_language == "zh"
                       else f"{entry.language} → {entry.target_language}"),
                      f"{entry.total_seconds:.2f}s" if entry.total_seconds >= 0 else "")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry.id)
                if column in (1, 2):
                    item.setToolTip(entry_text(entry))
                self.history_table.setItem(row, column, item)

    def _selected_history_id(self) -> int | None:
        row = self.history_table.currentRow()
        item = self.history_table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _selected_entry(self):
        entry_id = self._selected_history_id()
        if entry_id is None or self.history is None:
            QMessageBox.information(self, tr("app.0247"), tr("app.0248"))
            return None
        try:
            return self.history.entry(entry_id)
        except Exception as exc:
            QMessageBox.warning(self, tr("app.0249"), str(exc))
            return None

    def _history_details(self):
        entry = self._selected_entry()
        if entry:
            preview(self, tr("app.0250"), entry_text(entry))

    def _correct_history(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        corrected, accepted = QInputDialog.getMultiLineText(
            self, tr("app.0251"), tr("app.0252", p0=entry.original, p1=entry.corrected_chinese or entry.chinese),
            entry.corrected_chinese or entry.chinese)
        if not accepted or not corrected.strip():
            return
        modes = [tr("app.0253"), tr("app.0254")]
        mode, accepted = QInputDialog.getItem(self, tr("app.0255"), tr("app.0256"), modes, editable=False)
        if not accepted:
            return
        try:
            self.history.correct(entry.id, corrected, apply_future=False)
            if mode == modes[1]:
                rule = Rule(entry.game, entry.language, entry.target_language,
                            entry.corrected_original or entry.original, corrected.strip(),
                            origin=tr("app.0257"), priority=100, updated=now())
                conflict = self.custom_rules.add(rule)
                message = tr("app.0258") + (tr("app.0259") if conflict else "")
            else:
                message = tr("app.0260")
            self._refresh_history()
            self.statusBar().showMessage(message)
        except Exception as exc:
            QMessageBox.warning(self, tr("app.0261"), str(exc))

    def _feedback_history(self):
        entry = self._selected_entry()
        if entry:
            submit_feedback(self, self.feedback_store, entry)

    def _review_feedback(self):
        review_feedback(self, self.feedback_store, self.custom_rules)

    def _glossary_actions(self, form):
        self.add_term_button = GlassButton(tr("terms.add"))
        self.add_term_button.clicked.connect(lambda: add_custom_term(self, self.custom_rules))
        form.addRow(self.add_term_button)
        row = QHBoxLayout()
        for text, action in ((tr("app.0262"), lambda: default_glossary(self)),
                             (tr("app.0263"), lambda: manage_rules(self, self.custom_rules)),
                             (tr("app.0264"), self._review_feedback)):
            button = GlassButton(text)
            button.clicked.connect(action)
            row.addWidget(button)
        row.addStretch()
        form.addRow(row)

    def _import_history(self):
        if self.history is None:
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("app.0265"), "", tr("app.0266"))
        if not name:
            return
        try:
            imported, skipped = self.history.import_csv(Path(name))
            self._refresh_history()
            self.statusBar().showMessage(tr("app.0267", p0=imported, p1=skipped))
        except Exception as exc:
            QMessageBox.warning(self, tr("app.0268"), str(exc))

    def _export_history(self) -> None:
        if self.history is None:
            return
        name, _ = QFileDialog.getSaveFileName(self, tr("app.0269"), tr("app.0270"), tr("app.0271"))
        if not name:
            return
        try:
            from pathlib import Path
            self.history.export_csv(Path(name))
            self.statusBar().showMessage(tr("app.0272", p0=name))
        except Exception as exc:
            QMessageBox.warning(self, tr("app.0273"), str(exc))

    def _clear_history(self) -> None:
        if self.history is None:
            return
        choice = QMessageBox.question(
            self, tr("app.0274"), tr("app.0275"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            self.history.clear_history()
            self._refresh_history()
        except Exception as exc:
            QMessageBox.warning(self, tr("app.0276"), str(exc))

    def _clear_all_history(self) -> None:
        if self.history is None:
            return
        choice = QMessageBox.question(
            self, tr("app.0277"), tr("app.0278"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            self.history.clear_all()
            self._refresh_history()
        except Exception as exc:
            QMessageBox.warning(self, tr("app.0279"), str(exc))

    def _download(self) -> None:
        if self._model_busy or self._models_ready():
            return
        self._save_settings()
        if self.pipeline is not None:
            QMessageBox.information(self, tr("app.0280"), tr("app.0281"))
            return
        if self._model_channel is None:
            self._status(tr("app.0282"))
            return
        uses_cos = self._uses_cos_models()
        size = self.model_combo.currentData()
        folder = self._get_model_dir()
        self._model_pause.clear()
        self._set_model_busy(True)
        self.model_pause_button.setEnabled(True)
        self.model_pause_button.setVisible(uses_cos)
        self.download_button.setText(tr("app.0283"))

        def task():
            try:
                if uses_cos:
                    if size != "small" and size not in self.catalog:
                        self.catalog_checked.wait(9)
                    install_selected(self._model_channel, self.catalog, size, folder,
                                     self.bridge.status.emit, self.bridge.model_download_progress.emit,
                                     self._model_pause)
                else:
                    check_huggingface_space(size, folder)
                    download_models(size, self.bridge.status.emit, folder=folder)
            except ModelPaused:
                self.bridge.status.emit(tr("app.0284"))
            except InsufficientModelSpace as exc:
                self.bridge.status.emit(self._model_space_message(exc))
            except Exception as exc:
                LOG.exception("Model download failed")
                self.bridge.status.emit(tr("app.0285", p0=exc))
            finally:
                self.bridge.download_done.emit()

        threading.Thread(target=task, name="model-download", daemon=True).start()

    def _uses_cos_models(self) -> bool:
        if not self._model_channel:
            return False
        channel = self._model_channel["channel"]
        return channel == "domestic" or (channel == "dual" and self.settings.model_source == "cos")

    def _pause_model_download(self) -> None:
        self._model_pause.set()
        self.model_pause_button.setEnabled(False)
        self.model_transfer.setText(tr("app.0286"))
        self.model_transfer.show()

    def _model_download_progress(self, received: int, total: int, speed: float) -> None:
        if total <= 0:
            return
        self.model_progress.setRange(0, 100)
        self.model_progress.setValue(int(100 * received / total))
        mib = 1024**2
        remaining = max(0, total - received)
        rate = f"  ·  {speed / mib:,.1f} MiB/s" if speed > 0 else ""
        eta = tr("app.0287", p0=math.ceil(remaining / speed)) if speed > 0 and remaining else ""
        self.model_transfer.setText(tr("app.0288", p0=received / mib, p1=total / mib, p2=remaining / mib, p3=rate, p4=eta))
        self.model_transfer.show()

    def _import_models(self) -> None:
        if self._model_busy:
            return
        if self.pipeline is not None:
            QMessageBox.information(self, tr("app.0289"), tr("app.0290"))
            return
        source = QFileDialog.getExistingDirectory(self, tr("app.0291"))
        if not source:
            return
        self._set_model_busy(True)
        size = self.model_combo.currentData()
        folder = self._get_model_dir()

        def task():
            try:
                import_models(Path(source), size, self.bridge.status.emit, folder=folder)
            except InsufficientModelSpace as exc:
                self.bridge.status.emit(self._model_space_message(exc))
            except Exception as exc:
                LOG.exception("Model import failed")
                self.bridge.status.emit(tr("app.0292", p0=exc))
            finally:
                self.bridge.download_done.emit()

        threading.Thread(target=task, name="model-import", daemon=True).start()

    @staticmethod
    def _model_space_message(exc: InsufficientModelSpace) -> str:
        return tr("app.0322", p0=exc.folder.anchor or exc.folder, p1=exc.required, p2=exc.available)

    def _model_location_in_use(self) -> bool:
        return (self._model_busy or self.pipeline is not None or self.talk._busy
                or (self._stop_thread is not None and self._stop_thread.is_alive()))

    def _choose_model_location(self) -> None:
        if self._model_location_in_use():
            self._status(tr("app.0323"))
            return
        selected = QFileDialog.getExistingDirectory(self, tr("app.0320"))
        if not selected:
            return
        if self._model_location_in_use():
            self._status(tr("app.0323"))
            return

        settings = replace(self.settings)
        self._model_moving = True
        self._set_model_busy(True)
        self.centralWidget().setEnabled(False)
        self._status(tr("app.0324"))

        def task():
            try:
                move_model_dir(settings, Path(selected), default=MODEL_DIR)
            except InsufficientModelSpace as exc:
                self.bridge.model_location_done.emit(None, self._model_space_message(exc))
            except FileExistsError as exc:
                self.bridge.model_location_done.emit(None, tr("app.0325", p0=exc))
            except FileLockTimeout:
                self.bridge.model_location_done.emit(None, tr("app.0323"))
            except Exception as exc:
                LOG.exception("Model location change failed")
                self.bridge.model_location_done.emit(None, tr("app.0326", p0=exc))
            else:
                self.bridge.model_location_done.emit(settings.model_dir, "")

        threading.Thread(target=task, name="model-location", daemon=True).start()

    def _finish_model_location_move(self, model_dir, error: str) -> None:
        self._model_moving = False
        self.centralWidget().setEnabled(True)
        if not error:
            self.settings.model_dir = model_dir
            self.model_location_path.setText(str(self._get_model_dir()))
        self._set_model_busy(False)
        if error:
            self._status(error)
            QMessageBox.warning(self, tr("app.0320"), error)
        else:
            self._status(tr("app.0327", p0=self._get_model_dir()))

    def _set_model_busy(self, busy: bool) -> None:
        was_busy = self._model_busy
        self._model_busy = busy
        self.download_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.model_location_button.setEnabled(not busy)
        self.model_progress.setVisible(busy)
        self.model_loading.setVisible(busy)
        if busy:
            self._model_error = ""
            self.model_state.setText(tr("app.preparing_local_models"))
            self.model_state.show()
            self.model_icon.set_mode("spin", highlight=True)
            self.model_progress.setRange(0, 0)
            self.start_button.setEnabled(False)
        else:
            self.model_pause_button.hide()
            self.model_transfer.hide()
            self._refresh_model_status()
            complete = was_busy and self._models_ready()
            self.model_icon.set_mode("done" if complete else "idle", highlight=complete)
            if complete:
                self._announce(tr("app.models_ready"), success=True)

    def _download_done(self) -> None:
        self._set_model_busy(False)

    def _effective_settings(self):
        if self._model_channel and self._model_channel.get("edition") == "cpu":
            return replace(self.settings, compute_device="cpu")
        return self.settings

    def _show_routing_intro(self):
        if self.settings.routing_intro_seen:
            return
        self.settings.routing_intro_seen = True
        self.settings.save()
        prompt = QMessageBox(self)
        prompt.setWindowTitle(tr("app.0293"))
        prompt.setText(tr("app.0294"))
        setup = prompt.addButton(tr("app.0295"), QMessageBox.ButtonRole.AcceptRole)
        prompt.addButton(tr("app.0296"), QMessageBox.ButtonRole.RejectRole)
        prompt.buttonClicked.connect(lambda button: self._open_routing() if button is setup else None)
        prompt.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        prompt.show()

    def _open_routing(self):
        if self.routing_wizard is None:
            self.routing_wizard = RoutingWizard(self)
        apply_dialog_theme(self.routing_wizard, self.settings)
        self.routing_wizard.open()
        self.routing_wizard.raise_()
        self.routing_wizard.activateWindow()
        self._request_devices()

    def _open_microphone_wizard(self):
        if self.microphone_wizard is not None:
            self.microphone_wizard.raise_()
            return
        self.microphone_wizard = MicrophoneWizard(self)
        self.microphone_wizard.finished.connect(self._microphone_wizard_finished)
        self.microphone_wizard.open()

    def _microphone_wizard_finished(self):
        self.microphone_wizard = None

    def _begin_talk(self):
        if self._model_busy or not self._models_ready():
            self._talk_status(tr("app.0297"))
            return False
        if self.talk.capture is not None:
            return False
        try:
            devices = list_devices()
        except Exception as exc:
            self._talk_status(tr("app.0298", p0=exc))
            return False
        microphone = preferred_microphone(devices, self.settings.microphone_id)
        self._apply_microphones(devices, microphone)
        if microphone is None:
            self.microphone_stat.setText(tr("app.0299"))
            self._talk_status(tr("app.0300"))
            return False
        self.microphone_stat.setText(tr("app.0301", p0=microphone.name))
        if microphone.id != self.settings.microphone_id:
            self.settings.microphone_id = microphone.id
            self.settings.save()
            self.statusBar().showMessage(tr("app.0302", p0=microphone.name), 5000)
        return self.talk.begin(
            self._effective_settings(),
            diagnostic_session=self.pipeline.translate_microphone_details if self.pipeline else None,
        )

    def _talk_level(self, rms):
        if rms < 0:
            self._quiet_since = None
            self.microphone_level.hide()
            return
        now = time.monotonic()
        self._quiet_since = (self._quiet_since or now) if rms < SILENT_RMS else None
        message = tr("app.0303", p0=rms)
        if self._quiet_since is not None and now - self._quiet_since >= 0.6:
            message += tr("app.0304")
        self.microphone_level.setText(message)
        self.microphone_level.setToolTip(message)
        self.microphone_level.show()

    def _save_reverse_history(self, details):
        chinese, english, game, audio_end, metrics = details
        metrics = dict(metrics)
        result = SubtitleResult(chinese, english, "zh", metrics.pop("asr_seconds", -1.0),
                                metrics.pop("translation_seconds", -1.0), time.monotonic() - audio_end,
                                game=game, target_language="en", **metrics)
        self._recent_results.append(result)
        if self.history is None or not self.settings.save_history:
            return
        try:
            self.history.add_result(result)
            if self.tabs.currentWidget() is self.history_page:
                self._refresh_history()
        except Exception:
            LOG.exception("Could not save reverse history")

    def _talk_status(self, message):
        self.talk_state.setText(message)
        listening = message.startswith((tr("app.0305"), tr("app.0306")))
        translating = message.startswith(tr("app.0307"))
        self.microphone_icon.set_mode("pulse" if listening else "idle", highlight=listening)
        self.reverse_icon.set_mode("spin" if translating else "idle", highlight=translating)

    def _reverse_result(self, pair):
        chinese, english = pair
        self.reverse_text = english
        self.reverse_copy.setEnabled(True)
        self._talk_status(english)
        self.reverse_icon.set_mode("done", highlight=True, restart=True)
        self.overlay.add_subtitle(english, chinese, speaker=tr("app.0308"), reverse=True)

    def _copy_english(self):
        if self.reverse_text:
            QApplication.clipboard().setText(self.reverse_text)

    def _start_hotkey(self) -> None:
        if self.hotkey_listener:
            self.hotkey_listener.stop()
            self.talk.end()
            self.hotkey_listener = None
        try:
            from pynput import keyboard
            try:
                toggle_keys = set(keyboard.HotKey.parse(self.settings.hotkey))
            except ValueError:
                toggle_keys = set(keyboard.HotKey.parse("<ctrl>+<shift>+t"))
                self._talk_status(tr("app.0309"))
            try:
                talk_keys = set(keyboard.HotKey.parse(self.settings.ptt_hotkey))
                if not talk_keys or talk_keys <= toggle_keys or toggle_keys <= talk_keys:
                    raise ValueError("conflict")
            except ValueError:
                talk_keys = set()
                self._talk_status(tr("app.0310"))
            toggle = keyboard.HotKey(toggle_keys, self.bridge.toggle.emit)
            pressed = set()

            def press(key):
                key = listener.canonical(key)
                was_active = talk_keys <= pressed
                pressed.add(key)
                toggle.press(key)
                if talk_keys and not was_active and talk_keys <= pressed:
                    self.bridge.talk_begin.emit()

            def release(key):
                key = listener.canonical(key)
                was_active = talk_keys <= pressed
                pressed.discard(key)
                toggle.release(key)
                if talk_keys and was_active and not talk_keys <= pressed:
                    self.bridge.talk_end.emit()

            listener = keyboard.Listener(on_press=press, on_release=release)
            self.hotkey_listener = listener
            listener.start()
        except Exception as exc:
            LOG.warning("Global hotkey unavailable: %s", exc)
            self._talk_status(tr("app.0311"))

    def closeEvent(self, event) -> None:
        if self._model_moving:
            event.ignore()
            return
        if self.routing_wizard is not None:
            wizard, self.routing_wizard = self.routing_wizard, None
            wizard.close()
            wizard.deleteLater()
        if self.microphone_wizard is not None:
            self.microphone_wizard.reject()
        self._model_pause.set()
        self.talk.close()
        if self.pipeline:
            self.pipeline.stop()
            self.pipeline = None
        if self._stop_thread and self._stop_thread.is_alive():
            self._stop_thread.join(timeout=3)
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        self.overlay.close()
        super().closeEvent(event)


def run() -> int:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(APP_DIR / "app.log", encoding="utf-8"),
                                  logging.StreamHandler()])
    app = QApplication(sys.argv)
    app.setApplicationName(tr("app.0312"))
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window._show_first_run)
    return app.exec()
