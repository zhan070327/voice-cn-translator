from __future__ import annotations

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

from packaging.version import Version

from PySide6.QtCore import QObject, QSignalBlocker, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame,
                               QHeaderView, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QProgressBar,
                               QScrollArea, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget, QTableWidget,
                               QTableWidgetItem, QAbstractItemView, QTreeWidget, QTreeWidgetItem,
                               QButtonGroup, QColorDialog, QSizePolicy)

from valorant_translator.audio.capture import list_devices, preferred_microphone
from valorant_translator.feedback import FeedbackStore
from valorant_translator.config import APP_DIR, MODEL_DIR, Settings
from valorant_translator.history import HistoryStore
from valorant_translator.models.catalog import fetch_catalog, install_selected
from valorant_translator.models.cos import ModelPaused
from valorant_translator.models.download import (download_models, import_models,
                                                 model_ready, WHISPER_FILES, M2M_FILES)
from valorant_translator.pipeline import Pipeline, SubtitleResult, TaskTrace
from valorant_translator.recognition.whisper import LANGUAGES
from valorant_translator.translation.glossary import GAMES, read_default_glossary
from valorant_translator.translation.rules import Rule, RuleStore, now
from valorant_translator.ui.dialogs import (GlassFileDialog as QFileDialog, GlassInputDialog as QInputDialog,
                                           GlassMessageBox as QMessageBox, apply_dialog_theme)
from valorant_translator.ui.glass import (BACKGROUND_THEMES, DEFAULT_BACKGROUND_COLOR,
                                        GAP, GUTTER, INLINE, CompactLabel, GlassButton, GlassSection,
                                        GlassSurface, accent_color, glass_styles, tint_icons)
from valorant_translator.ui.glossary_tools import (default_glossary, entry_text, manage_rules, preview,
                                                   review_feedback, submit_feedback)
from valorant_translator.ui import tokens
from valorant_translator.ui.icons import LineIcon
from valorant_translator.ui.motion import ActionDock, CaptionLight, DetailPanel, LoadingSkeleton, SelectionCapsule, Snapshot, Toast
from valorant_translator.ui.onboarding import Onboarding
from valorant_translator.ui.overlay import SubtitleOverlay
from valorant_translator.ui.routing import RoutingWizard, routing_devices
from valorant_translator.ui.talk import SILENT_RMS, PushToTalk
from valorant_translator.updates.channel import check_for_update
from valorant_translator.updates.domestic import load_channel
from valorant_translator.updates.github import (UpdateCancelled, download_installer,
                                                start_installer)
from valorant_translator.updates.windows import RESULT, start_silent_installer
from valorant_translator.version import APP_VERSION

LOG = logging.getLogger(__name__)
FIRST_CAPTION_HINT = "开始翻译，看到第一条字幕"
EMPTY_HISTORY = "暂无翻译历史。启用本地历史后，新的译文会显示在这里。"


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
        self._recent_trace: deque[TaskTrace] = deque(maxlen=10)
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
        self.setWindowTitle("VALORANT AI 实时语音翻译器")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[2] / "assets" / "app_icon.ico")))
        self.resize(1160, 840)
        self._build()
        self.tabs.currentChanged.connect(self._page_changed)
        self._refresh_history()
        self.statusBar()
        self.overlay = SubtitleOverlay(self.settings)
        self.overlay.position_changed.connect(self._overlay_moved)
        self.overlay.show()
        self._start_hotkey()
        self.refresh_devices()
        self._refresh_model_status()
        self._update_mode_hint()
        QTimer.singleShot(0, self._show_update_result)
        if sys.platform == "win32" and not self.settings.routing_intro_seen:
            QTimer.singleShot(0, self._show_routing_intro)
        if self._model_channel and self._model_channel["channel"] in ("domestic", "dual") and "model_manifest_url" in self._model_channel:
            threading.Thread(target=self._fetch_model_catalog, name="model-catalog", daemon=True).start()
        else:
            self.catalog_checked.set()

    def _build(self) -> None:
        self.tabs = QTabWidget()
        main_page = QWidget()
        main_layout = QVBoxLayout(main_page)
        main_layout.setContentsMargins(GUTTER, 0, GUTTER, GUTTER)
        main_layout.setSpacing(GUTTER)

        self.update_button = GlassButton("检查更新", icon="refresh")
        self.update_button.clicked.connect(self._check_update)
        self.update_button.setFixedWidth(tokens.UPDATE_WIDTH)
        update_strip = QFrame()
        update_strip.setObjectName("updateStrip")
        updates = QVBoxLayout(update_strip)
        updates.setContentsMargins(GUTTER, tokens.TIGHT, GUTTER, tokens.TIGHT)
        updates.setSpacing(INLINE)
        self.update_label = self._compact(f"当前版本：{APP_VERSION}")
        updates.addWidget(self.update_label)
        self.update_bar = QProgressBar()
        self.update_bar.setRange(0, 100)
        self.update_bar.hide()
        updates.addWidget(self.update_bar)
        self.update_cancel_button = GlassButton("取消下载")
        self.update_cancel_button.setProperty("ghost", True)
        self.update_cancel_button.clicked.connect(self._cancel_update)
        self.update_cancel_button.hide()
        updates.addWidget(self.update_cancel_button, alignment=Qt.AlignmentFlag.AlignLeft)
        cards = QHBoxLayout()
        cards.setSpacing(INLINE)
        self.model_stat, self.model_icon = self._stat(cards, "识别模型", "未检测", "layers")
        self.device_stat, _ = self._stat(cards, "运行设备", "未检测", "chip")
        self.latency_stat = QLabel()
        self.latency_stat.setProperty("metric", True)
        self.latency_stat.hide()
        self.microphone_stat, self.microphone_icon = self._stat(cards, "麦克风", "未检测", "mic")
        warning = QFrame()
        warning.setObjectName("warning")
        warning.setToolTip("本工具未经 Riot 批准。游戏内字幕存在政策和反作弊风险，建议先在桌面或第二屏测试。")
        risk = QHBoxLayout(warning)
        risk.setContentsMargins(GAP, tokens.TIGHT, GAP, tokens.TIGHT)
        risk.setSpacing(INLINE)
        risk.addWidget(LineIcon("shield"))
        risk.addWidget(QLabel("游戏内字幕有 Riot 合规风险，建议先在桌面测试。"), 1)
        updates.addWidget(warning)
        main_layout.addWidget(update_strip)

        columns = QHBoxLayout()
        columns.setSpacing(GAP)
        left_column = QVBoxLayout()
        right_column = QVBoxLayout()
        left_column.setSpacing(GAP)
        right_column.setSpacing(GAP)
        columns.addLayout(left_column, 11)
        columns.addLayout(right_column, 9)
        main_layout.addLayout(columns, 1)

        model_group, model_layout = self._group("本地模型", "layers")
        self.model_status_label = self._compact("检查模型…")
        model_layout.addWidget(self.model_status_label)
        self.model_loading = LoadingSkeleton()
        model_layout.addWidget(self.model_loading)
        self.model_state = self._hint("")
        self.model_state.setTextFormat(Qt.TextFormat.PlainText)
        self.model_state.hide()
        model_layout.addWidget(self.model_state)
        model_row = QHBoxLayout()
        model_row.setSpacing(INLINE)
        self.download_button = GlassButton("一键下载模型", icon="download")
        self.download_button.clicked.connect(self._download)
        self.import_button = GlassButton("从文件夹导入", icon="folder")
        self.import_button.clicked.connect(self._import_models)
        model_row.addWidget(self.download_button)
        model_row.addWidget(self.import_button)
        self.model_pause_button = GlassButton("暂停下载")
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
        self.download_button.setToolTip("按详细设置中的型号下载，下载后可离线使用。")
        self.import_button.setToolTip("选择已保存的本地模型文件夹。")
        left_column.addWidget(model_group)

        audio_group, audio_layout = self._group("声音来源", "speaker")
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._save_settings)
        self.refresh_button = GlassButton("刷新设备", icon="refresh")
        self.refresh_button.clicked.connect(self._request_devices)
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        audio_layout.addLayout(device_row)
        self.device_hint = self._compact("")
        audio_layout.addWidget(self.device_hint)
        left_column.addWidget(audio_group)

        display_group, display_layout = self._group("字幕显示", "screen")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("桌面 / 第二屏", "desktop")
        self.mode_combo.addItem("游戏内覆盖（实验）", "game")
        self.mode_combo.setCurrentIndex(0 if self.settings.normal_window else 1)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        display_layout.addWidget(self.mode_combo)
        self.mode_hint = self._compact("")
        display_layout.addWidget(self.mode_hint)
        overlay_row = QHBoxLayout()
        self.test_button = GlassButton("测试字幕（20 秒）", icon="screen")
        self.test_button.clicked.connect(self._test_overlay)
        self.position_button = GlassButton("拖动字幕调整位置")
        self.position_button.clicked.connect(self._toggle_position_edit)
        self.show_button = GlassButton("隐藏字幕窗口")
        self.show_button.clicked.connect(self._toggle_overlay)
        overlay_row.addWidget(self.test_button)
        overlay_row.addWidget(self.show_button)
        display_layout.addLayout(overlay_row)
        display_layout.addWidget(self.position_button)
        left_column.addWidget(display_group)

        run_group, run_layout = self._group("实时翻译", "play")
        run_group.setProperty("compactRun", True)
        run_layout.setContentsMargins(GUTTER, INLINE, GUTTER, INLINE)
        run_layout.setSpacing(tokens.TIGHT)
        self.run_icon = run_group.icon
        run_row = QHBoxLayout()
        run_row.setSpacing(INLINE)
        self.start_button = GlassButton("开始翻译", icon="play")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_translation)
        self.stop_button = GlassButton("停止翻译", icon="stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_translation)
        run_row.addWidget(self.start_button)
        run_row.addWidget(self.stop_button)
        run_row.addStretch()
        self.status_label = self._compact("当前状态：待机")
        self.status_label.hide()
        run_row.addWidget(self.status_label, 1)
        self.audio_icon = LineIcon("activity")
        self.audio_icon.setToolTip("音频输入电平")
        self.audio_icon.hide()
        run_row.addWidget(self.audio_icon)
        self.volume = QProgressBar()
        self.volume.setRange(0, 100)
        self.volume.setValue(0)
        self.volume.setFixedWidth(tokens.VOLUME_WIDTH)
        self.volume.setAccessibleName("音频输入音量")
        self.volume.setToolTip("音频输入音量")
        self.volume.hide()
        run_row.addWidget(self.volume)
        run_layout.addLayout(run_row)
        run_layout.addLayout(cards)
        self.runtime_label = self._compact("实际运行设备：模型尚未加载")
        self.diagnostic_label = self._compact("")
        self.diagnostic_label.hide()
        self.runtime_label.hide()
        metrics = QHBoxLayout()
        metrics.setSpacing(GAP)
        metrics.addWidget(self.runtime_label, 2)
        metrics.addWidget(self.diagnostic_label, 3)
        run_layout.addLayout(metrics)
        self.last_discard = self._compact("")
        self.last_discard.setObjectName("lastDiscard")
        self.last_discard.hide()
        run_layout.addWidget(self.last_discard)
        main_layout.insertWidget(main_layout.indexOf(columns), run_group)

        self.reverse_icon = LineIcon("mic")
        self.talk_state = self._compact("按住说话，松开翻译。")
        self.microphone_level = self._compact("")
        self.microphone_level.hide()
        self.talk_button = GlassButton("按住说话", icon="mic")
        self.talk_button.setToolTip("按住按钮或设置中的说话快捷键，松开后翻译。")
        self.talk_button.pressed.connect(self._begin_talk)
        self.talk_button.released.connect(self.talk.end)
        self.reverse_copy = GlassButton("复制英文", icon="copy")
        self.reverse_copy.setEnabled(False)
        self.reverse_copy.clicked.connect(self._copy_english)
        talk_row = QHBoxLayout()
        talk_row.setSpacing(INLINE)
        talk_row.addWidget(self.reverse_icon)
        talk_row.addWidget(self._hint("中文 → 英文"))
        talk_row.addWidget(self.talk_button)
        talk_row.addWidget(self.reverse_copy)
        talk_row.addWidget(self.talk_state, 1)
        run_layout.addLayout(talk_row)
        run_layout.addWidget(self.microphone_level)

        self.hero = QFrame()
        self.hero.setObjectName("hero")
        result_layout = QVBoxLayout(self.hero)
        result_layout.setContentsMargins(GAP, INLINE, GAP, INLINE)
        result_layout.setSpacing(INLINE)
        header = QHBoxLayout()
        self.result_icon = LineIcon("chat", accented=True)
        heading = QLabel("实时字幕")
        heading.setObjectName("sectionTitle")
        header.addWidget(self.result_icon)
        header.addWidget(heading)
        header.addStretch()
        self.caption_tools = GlassButton("显示/隐藏字幕", icon="screen")
        self.caption_tools.clicked.connect(self._toggle_overlay)
        header.addWidget(self.caption_tools)
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
        self.previous_caption = self._compact("")
        self.previous_caption.setObjectName("previousCaption")
        self.previous_caption.setAccessibleName("上一条字幕")
        self.previous_caption.hide()
        self.previous_latency = QLabel()
        self.previous_latency.setProperty("metric", True)
        self.previous_latency.hide()
        previous = QHBoxLayout()
        previous.setSpacing(INLINE)
        previous.addWidget(self.previous_caption, 1)
        previous.addWidget(self.previous_latency, 0, Qt.AlignmentFlag.AlignVCenter)
        captions.addLayout(previous)
        result_layout.addWidget(caption_well)
        self.caption_hint = self._hint(FIRST_CAPTION_HINT)
        self.caption_hint.setTextFormat(Qt.TextFormat.PlainText)
        result_layout.addWidget(self.caption_hint)
        self.original_label = self._compact("")
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
        result_layout.addLayout(source)
        self.detail_toggle = GlassButton("识别详情")
        self.detail_toggle.setCheckable(True)
        header.addWidget(self.detail_toggle)
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
        result_layout.addWidget(self.details_panel)
        self.feedback_button = GlassButton("复制反馈信息", icon="copy")
        self.feedback_button.clicked.connect(self._copy_feedback)
        recent_group, recent_layout = self._group("最近翻译", "clock")
        recent_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        recent_layout.setContentsMargins(GUTTER, INLINE, GUTTER, INLINE)
        self.recent_hint = self._hint(EMPTY_HISTORY)
        recent_layout.addWidget(self.recent_hint)
        self.recent_list = QTreeWidget()
        self.recent_list.setObjectName("recentTranslations")
        self.recent_list.setAccessibleName("最近翻译")
        self.recent_list.setColumnCount(2)
        self.recent_list.setHeaderLabels(("译文", "时间"))
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
        right_column.addWidget(recent_group)
        right_column.addWidget(self.feedback_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.caption_light = CaptionLight(self.hero)
        main_layout.insertWidget(0, self.hero)
        self.dock = ActionDock((
            ("测试字幕", "play", self.test_button.click),
            ("显示/隐藏字幕", "screen", self.show_button.click),
            ("调整字幕位置", "settings", self.position_button.click),
        ))
        left_column.addStretch()
        right_column.addStretch()
        self.tabs.addTab(self._scroll(main_page), "快速开始")

        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(GUTTER, GAP, GUTTER, GUTTER)
        settings_layout.setSpacing(GAP)
        form = self._settings_card(settings_layout, "软件更新", "download")
        self.settings_update_button = GlassButton("检查更新", icon="refresh")
        self.settings_update_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.settings_update_button.clicked.connect(self._check_update)
        form.addRow(self.settings_update_button)
        self.update_version_label = self._hint(f"当前版本：{APP_VERSION}；最新版本：尚未检查")
        form.addRow(self.update_version_label)
        self.update_notes_label = self._hint("更新日志：检查后显示")
        form.addRow(self.update_notes_label)
        form = self._settings_card(settings_layout, "识别和翻译", "chip")
        self.language_combo = self._combo([("自动检测", "auto"),
                                            *[(name, code) for code, name in LANGUAGES.items()]], self.settings.language)
        self._row(form, "识别语言", self.language_combo)
        self.model_combo = self._combo([(name, name) for name in ("tiny", "base", "small", "medium")],
                                       self.settings.whisper_model)
        self._row(form, "识别模型", self.model_combo)
        if self._model_channel and self._model_channel["channel"] == "dual":
            self.model_source = self._combo([("国内 COS", "cos"), ("国际 Hugging Face", "huggingface")],
                                            self.settings.model_source)
            self._row(form, "下载来源", self.model_source)
        self._row(form, "翻译模型", QLabel("M2M100 418M（本地）"))
        self.compute_combo = self._combo([("自动：优先 GPU", "auto"), ("GPU", "cuda"), ("CPU", "cpu")],
                                         self.settings.compute_device)
        self._row(form, "运行设备", self.compute_combo)
        self.fast_check = self._check("低延迟模式", self.settings.fast_mode)
        self.fast_check.setToolTip("可能略降识别和翻译质量。")
        form.addRow(self.fast_check)
        self.ptt_hotkey_edit = QLineEdit(self.settings.ptt_hotkey)
        self.ptt_hotkey_edit.editingFinished.connect(self._save_settings)
        self._row(form, "说话快捷键", self.ptt_hotkey_edit)
        self.mic_combo = QComboBox()
        self.mic_combo.currentIndexChanged.connect(self._save_settings)
        self._row(form, "麦克风", self.mic_combo)
        self.routing_button = GlassButton("语音分流设置", icon="speaker")
        self.routing_button.clicked.connect(self._open_routing)
        form.addRow(self.routing_button)
        self.routing_status = self._hint("")
        form.addRow(self.routing_status)
        self.advanced_button = GlassButton("高级设置", icon="settings")
        self.advanced_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.advanced_button.setCheckable(True)
        self.advanced_button.setToolTip("展开或收起语音检测和超时选项。")
        form.addRow(self.advanced_button)
        self.advanced_fields = QWidget()
        advanced = self._form(self.advanced_fields)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.vad_spin = self._spin(self.settings.vad_level, 0, 3)
        self._row(advanced, "语音检测", self.vad_spin, "数字越大越严格，短报点建议 1。")
        self.timeout_spin = self._spin(int(self.settings.translation_timeout), 1, 30)
        self._row(advanced, "翻译超时（秒）", self.timeout_spin)
        self.age_spin = self._spin(int(self.settings.max_task_age), 2, 30)
        self._row(advanced, "过期丢弃（秒）", self.age_spin, "从音频结束起计算字幕时限。")
        self.advanced_fields.hide()
        self.advanced_button.toggled.connect(self.advanced_fields.setVisible)
        form.addRow(self.advanced_fields)
        form = self._settings_card(settings_layout, "字幕", "screen")
        self.screen_combo = QComboBox()
        for index, screen in enumerate(QApplication.screens()):
            self.screen_combo.addItem(f"显示器 {index + 1}：{screen.name()}", index)
        self.screen_combo.setCurrentIndex(min(self.settings.overlay_screen, max(0, self.screen_combo.count() - 1)))
        self.screen_combo.currentIndexChanged.connect(self._save_settings)
        self._row(form, "显示器", self.screen_combo)
        self.x_spin = self._spin(self.settings.overlay_x, -5000, 5000)
        self.y_spin = self._spin(self.settings.overlay_y, -5000, 5000)
        self._row(form, "横向位置", self.x_spin)
        self._row(form, "纵向位置", self.y_spin)
        self.font_spin = self._spin(self.settings.font_size, 14, 60)
        self._row(form, "字号", self.font_spin)
        self.opacity_spin = self._spin(self.settings.overlay_opacity, 0, 100)
        self._row(form, "背景不透明度", self.opacity_spin)
        self.original_check = self._check("同时显示识别原文", self.settings.show_original)
        self.click_check = self._check("鼠标穿透", self.settings.click_through)
        self.click_check.setToolTip("拖动字幕时关闭穿透。")
        form.addRow(self.original_check)
        form.addRow(self.click_check)
        self.hotkey_edit = QLineEdit(self.settings.hotkey)
        self.hotkey_edit.editingFinished.connect(self._save_settings)
        self._row(form, "字幕快捷键", self.hotkey_edit)
        theme = GlassButton("选择点缀色")
        theme.clicked.connect(self._choose_accent)
        self._row(form, "点缀色", theme)
        self.duration_spin = self._spin(int(self.settings.subtitle_seconds), 1, 30)
        self._row(form, "显示时长（秒）", self.duration_spin)
        self.model_combo.setToolTip("更改识别模型后需下载对应模型。")
        self.background_combo = QComboBox()
        for key, (label, _) in BACKGROUND_THEMES.items():
            self.background_combo.addItem(label, key)
        self.background_combo.addItem("自定义", "custom")
        self.background_combo.setCurrentIndex(max(0, self.background_combo.findData(self.settings.background_theme)))
        self.background_combo.currentIndexChanged.connect(self._select_background)
        self._row(form, "背景主题", self.background_combo)
        colors = QHBoxLayout()
        self.background_color_button = GlassButton("自定义背景色")
        self.background_color_button.clicked.connect(self._choose_background)
        self.theme_reset_button = GlassButton("恢复默认")
        self.theme_reset_button.clicked.connect(self._reset_theme)
        colors.addWidget(self.background_color_button)
        colors.addWidget(self.theme_reset_button)
        colors.addStretch()
        self.game_accent_reset_button = GlassButton("恢复当前游戏点缀色")
        self.game_accent_reset_button.clicked.connect(self._reset_game_accent)
        self._row(form, "游戏皮肤", self.game_accent_reset_button)
        form.addRow(colors)
        self.reduce_motion_check = QCheckBox("减少动效")
        self.reduce_motion_check.setChecked(self.settings.reduce_motion)
        self.reduce_motion_check.toggled.connect(self._set_reduce_motion)
        form.addRow(self.reduce_motion_check)
        form = self._settings_card(settings_layout, "历史和隐私", "shield")
        self.history_check = self._check("保存本地历史", self.settings.save_history)
        self.history_check.setToolTip("只保存识别文字与译文，不保存音频。")
        form.addRow(self.history_check)
        self.feedback_button.setToolTip("诊断仅本次运行保留，分享前可检查是否包含对话原文。")
        glossary_form = self._settings_card(settings_layout, "游戏词库", "settings")
        self.glossary_checks = {}
        for key, title in (("game_terms", "启用游戏术语优化"), ("official_lines", "启用官方台词匹配"),
                           ("asr_correction", "启用语音识别纠错"), ("teammate_only", "只翻译队友语音（过滤英雄语音）")):
            check = self._check(title, getattr(self.settings, key))
            self.glossary_checks[key] = check
            glossary_form.addRow(check)
        self.glossary_checks["teammate_only"].setToolTip(
            "只过滤词库中已核实的整句英雄台词。分流可避免游戏音效进入翻译器。")
        self.glossary_status = self._hint("")
        self.glossary_status.setTextFormat(Qt.TextFormat.PlainText)
        glossary_form.addRow(self.glossary_status)
        self.glossary_loading = LoadingSkeleton()
        glossary_form.addRow(self.glossary_loading)
        self._glossary_actions(glossary_form)
        settings_layout.addStretch()
        self.tabs.addTab(self._scroll(settings_page), "详细设置")

        self.history_page = QWidget()
        self.history_page.setObjectName("historyPage")
        history_layout = QVBoxLayout(self.history_page)
        history_layout.setContentsMargins(GUTTER, GAP, GUTTER, GUTTER)
        history_hint = self._hint("最近 1000 条 · 仅在本机保存")
        history_hint.setToolTip("只修正选中记录，或明确选择将译法保存到我的词库。")
        history_layout.addWidget(history_hint)
        self.history_state = self._hint("")
        self.history_state.setTextFormat(Qt.TextFormat.PlainText)
        history_layout.addWidget(self.history_state)
        self.history_loading = LoadingSkeleton()
        history_layout.addWidget(self.history_loading)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(("时间", "识别原文", "字幕译文", "语言", "延迟"))
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
        for label, callback in (("刷新", self._request_history),
                                ("修正选中译文", self._correct_history),
                                ("导出 CSV", self._export_history),
                                ("清空历史", self._clear_history),
                                ("删除历史和修正", self._clear_all_history)):
            button = GlassButton(label)
            button.clicked.connect(callback)
            button.setEnabled(self.history is not None)
            history_buttons.addWidget(button)
        history_buttons.addStretch()
        history_layout.addLayout(history_buttons)
        extra = QHBoxLayout()
        for label, action in (("查看详情", self._history_details), ("反馈翻译错误", self._feedback_history),
                              ("反馈与审核", self._review_feedback), ("导入旧 CSV", self._import_history)):
            button = GlassButton(label)
            button.clicked.connect(action)
            button.setEnabled(self.history is not None or label == "反馈与审核")
            extra.addWidget(button)
        extra.addStretch()
        history_layout.addLayout(extra)
        self.history_table.doubleClicked.connect(self._history_details)
        self.tabs.addTab(self.history_page, "翻译历史")
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
        for index in range(self.tabs.count()):
            button = GlassButton(self.tabs.tabText(index), icon=("chat", "settings", "clock")[index])
            button.setProperty("navigation", True)
            button.setCheckable(True)
            self.nav.addButton(button, index)
            side.addWidget(button)
        self.nav.button(0).setChecked(True)
        self.nav.idClicked.connect(self._navigate)
        self.tabs.currentChanged.connect(lambda index: self.nav.button(index).setChecked(True))
        side.addStretch()
        guide = GlassButton("新手引导", icon="book")
        guide.clicked.connect(self._show_onboarding)
        side.addWidget(guide)
        side.addWidget(self._hint(f"v{APP_VERSION}"))
        self.nav_capsule = SelectionCapsule(sidebar)
        self.nav_capsule.select(self.nav.button(0), animated=False)
        self.tabs.currentChanged.connect(lambda index: self.nav_capsule.select(self.nav.button(index)))
        shell.addWidget(sidebar)
        content = QVBoxLayout()
        content.setSpacing(GAP)
        game_bar = QFrame()
        game_bar.setObjectName("glassCard")
        games = QHBoxLayout(game_bar)
        games.setContentsMargins(GAP, INLINE, GAP, INLINE)
        games.setSpacing(INLINE)
        self.game_group = QButtonGroup(self)
        for key, name in GAMES.items():
            button = GlassButton(name)
            button.setProperty("gamePill", True)
            button.setCheckable(True)
            button.setChecked(key == self.settings.game)
            button.clicked.connect(lambda checked=False, game=key: self._select_game(game))
            self.game_group.addButton(button)
            games.addWidget(button)
        self.game_capsule = SelectionCapsule(game_bar)
        self.game_capsule.select(self.game_group.checkedButton(), animated=False)
        self.game_group.buttonClicked.connect(self.game_capsule.select)
        games.addWidget(self.update_button)
        content.addWidget(game_bar)
        self.game_notice = Toast(self.surface)
        self.tabs.tabBar().hide()
        content.addWidget(self.tabs, 1)
        dock_slot = QFrame()
        dock_slot.setFixedHeight(tokens.WIDE + tokens.INLINE)
        dock_row = QHBoxLayout(dock_slot)
        dock_row.setContentsMargins(0, 0, 0, 0)
        dock_row.addWidget(self.dock, alignment=Qt.AlignmentFlag.AlignCenter)
        self.tabs.currentChanged.connect(lambda index: self.dock.setVisible(index == 0))
        content.addWidget(dock_slot)
        shell.addLayout(content, 1)
        self.setCentralWidget(self.surface)
        self.page_snapshot = Snapshot(self.tabs)
        self.skin_snapshot = Snapshot(self.surface)
        self.toast_timer = QTimer(self, singleShot=True)
        self.toast_timer.timeout.connect(self.game_notice.dismiss)
        self.detail_effect = DetailPanel(self.hero)
        self.detail_effect.hide()
        self._apply_theme()
        self._refresh_glossary()
        order = [*self.nav.buttons(), guide, *self.game_group.buttons(), self.update_button,
                 self.caption_tools, self.detail_toggle, self.start_button, self.stop_button,
                 self.talk_button, self.reverse_copy, self.download_button, self.import_button,
                 self.model_pause_button, self.device_combo, self.refresh_button, self.mode_combo,
                 self.test_button, self.show_button, self.position_button, self.recent_list, self.feedback_button,
                 *self.dock.buttons]
        for previous, following in zip(order, order[1:]):
            QWidget.setTabOrder(previous, following)

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
        color = self._pick_theme_color(QColor(accent_color(self.settings.accent)), "选择点缀色")
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
        color = self._pick_theme_color(current, "自定义背景色")
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
        if index == 2:
            self._request_history()
        elif index == 1:
            self._refresh_glossary()

    def _toggle_details(self, opened):
        if opened:
            self.details_panel.show()
            self.hero.layout().activate()
        self.detail_effect.show_from(self.detail_toggle, self.details_panel.geometry(), opening=opened)
        if not opened:
            self.details_panel.hide()

    def _announce(self, text, *, success=False):
        self.game_notice.announce(text, reduced=self.settings.reduce_motion, success=success)
        self.toast_timer.start(tokens.SUCCESS_MS if success else tokens.TOAST_MS)

    def _refresh_glossary(self):
        self._glossary_request += 1
        request = self._glossary_request
        self.glossary_status.setText("正在读取游戏词库…")
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
            text = "游戏词库读取失败。请尝试重新打开设置，或使用通用翻译。"
        else:
            text = ("已加载游戏词库" if any(value for value in data.values() if isinstance(value, (list, dict)))
                    else "该游戏词库建设中，暂用通用翻译")
        self.glossary_status.setText(text)

    def _select_game(self, game):
        if game == self.settings.game:
            return
        image = self.skin_snapshot.capture()
        self.settings.game = game
        self._save_theme()
        self.skin_snapshot.reveal(image, tokens.SKIN_MS)
        self._refresh_glossary()
        self._announce(f"已切换至 {GAMES[game]}")
        if self.pipeline:
            self._restart_after_stop = True
            self.stop_translation()

    def _show_onboarding(self):
        if self.onboarding and self.onboarding.isVisible():
            self.onboarding.raise_()
            return
        self.onboarding = Onboarding(self)
        self.onboarding.finished.connect(self._onboarding_finished)
        self.onboarding.open()

    def _onboarding_finished(self, _result):
        self.settings.onboarding_done = True
        self.settings.save()

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

    def _models_ready(self) -> bool:
        return (model_ready(MODEL_DIR / f"whisper-{self.model_combo.currentData()}", WHISPER_FILES)
                and model_ready(MODEL_DIR / "m2m100_418M", M2M_FILES))

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
        whisper = model_ready(MODEL_DIR / f"whisper-{size}", WHISPER_FILES)
        m2m_dir = MODEL_DIR / "m2m100_418M"
        m2m = model_ready(m2m_dir, M2M_FILES)
        if self.pipeline is None:
            self.model_stat.setText(size if whisper else "未下载")
        self.model_status_label.setText(
            f"识别模型 {size}：{'已下载' if whisper else '未下载'}  ·  中文翻译模型：{'已下载' if m2m else '未下载'}"
            + ("  ·  PyTorch 需更新至 2.6 以上" if not self._torch_version_ok() else "")
        )
        if not self._model_busy and not self._model_error:
            self.model_state.setText("点击下载或导入所选模型。" if not (whisper and m2m) else "")
            self.model_state.setVisible(not (whisper and m2m))
        self.download_button.setEnabled(not self._model_busy and not (whisper and m2m))
        stopping = self._stop_thread is not None and self._stop_thread.is_alive()
        if (hasattr(self, "start_button") and self.pipeline is None and not stopping
                and not getattr(self, "_model_busy", False)):
            self.start_button.setEnabled(True)
            self.start_button.setToolTip("" if whisper and m2m else "请先下载或导入所选模型。")

    def _save_settings(self, *_):
        if not hasattr(self, "duration_spin"):
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
        self.original_label.setVisible(self._has_result and settings.show_original and bool(self.original_label.text()))
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
            self.mode_hint.setText("游戏内建议用无边框窗口。")
            self.mode_hint.setToolTip("先用测试字幕定位，再开始翻译。独占全屏或 Vanguard 可能让字幕窗口不可见。")
            self.mode_combo.setToolTip(self.mode_hint.toolTip())
            self.mode_hint.show()
            self.position_button.setText("调整位置（暂时关闭穿透）" if self.click_check.isChecked() else "完成调整（开启穿透）")
        else:
            self.click_check.setEnabled(False)
            self.mode_hint.setText("字幕可拖到桌面或第二屏。")
            self.mode_combo.setToolTip(self.mode_hint.text())
            self.mode_hint.hide()
            self.position_button.setText("拖动字幕调整位置")

    def _toggle_position_edit(self) -> None:
        if self.mode_combo.currentData() == "game":
            self.click_check.setChecked(not self.click_check.isChecked())
            self._update_mode_hint()
        self.overlay.add_subtitle("拖动字幕到不遮挡准星和地图的位置", duration=30)
        self.overlay.show()
        self.show_button.setText("隐藏字幕窗口")

    def _test_overlay(self) -> None:
        self.overlay.add_subtitle("字幕预览：A 大两个。", "Two A main", duration=20)
        self.overlay.show()
        self.overlay.raise_()
        self.show_button.setText("隐藏字幕窗口")

    def _toggle_overlay(self) -> None:
        self.overlay.setVisible(not self.overlay.isVisible())
        self.show_button.setText("隐藏字幕窗口" if self.overlay.isVisible() else "显示字幕窗口")

    def refresh_devices(self) -> None:
        try:
            devices = list_devices()
        except Exception as exc:
            self._status(f"设备枚举失败：{exc}")
            return
        self._apply_devices(devices)

    def _request_devices(self) -> None:
        if not self.refresh_button.isEnabled():
            return
        self.refresh_button.setText("加载中…")
        self.refresh_button.setEnabled(False)

        def task():
            try:
                if sys.platform == "win32":
                    from ctypes import windll
                    com = windll.ole32
                    hr = com.CoInitializeEx(None, 0)
                    if hr < 0:
                        raise OSError(f"CoInitializeEx 失败（0x{hr & 0xffffffff:08X}）")
                    try:
                        devices = list_devices()
                    finally:
                        com.CoUninitialize()
                else:
                    devices = list_devices()
                self.bridge.devices_ready.emit(devices, "")
            except Exception as exc:
                self.bridge.devices_ready.emit(None, str(exc) or type(exc).__name__)

        threading.Thread(target=task, name="refresh-audio-devices", daemon=True).start()

    def _finish_device_refresh(self, devices, error: str) -> None:
        self.refresh_button.setText("刷新设备")
        self.refresh_button.setEnabled(True)
        if error:
            self._status(f"设备枚举失败：{error}")
        else:
            self._apply_devices(devices)
        self.devices_refreshed.emit(not error)

    def _apply_devices(self, devices) -> None:
        self.audio_devices = list(devices)
        devices = self.audio_devices
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for device in devices:
            prefix = "系统声音" if device.loopback else "麦克风"
            self.device_combo.addItem(f"{prefix}：{device.name}", device.id)
            self.device_combo.setItemData(self.device_combo.count() - 1, device.loopback,
                                          Qt.ItemDataRole.UserRole + 1)
        index = self.device_combo.findData(self.settings.device_id)
        if index < 0:
            index = next((i for i, device in enumerate(devices) if device.loopback), 0)
        self.device_combo.setCurrentIndex(index)
        self.device_combo.blockSignals(False)
        microphone = preferred_microphone(devices, self.settings.microphone_id)
        self._apply_microphones(devices, microphone)
        old = self.settings.microphone_id
        self._save_settings()
        if microphone and old != microphone.id:
            self.statusBar().showMessage(f"已自动使用麦克风：{microphone.name}", 5000)

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
        self.microphone_stat.setText(f"{mic} · 可用" if mic else "未检测到")
        route = next((device for device in routing_devices(self.audio_devices)
                      if device.id == self.device_combo.currentData()), None)
        routed = route is not None and route.id == self.settings.routing_verified_id
        self.routing_status.setText(f"上次已确认分流 · {route.name}" if routed else
                                    f"分流来源 · {route.name}（待验证）" if route else "未分流")
        self.device_hint.show()
        if self.device_combo.currentIndex() < 0:
            self.device_hint.setText("没有可用设备，连接后刷新。")
            self.device_hint.setStyleSheet("color: #ffd699;")
        elif not self.device_combo.currentData(Qt.ItemDataRole.UserRole + 1):
            self.device_hint.setText(
                "当前选的是麦克风。翻译队友请选择系统声音。")
            self.device_hint.setStyleSheet("color: #ffd699;")
        else:
            self.device_hint.setText("正在听系统播放声音。")
            self.device_hint.setStyleSheet("")
            self.device_hint.hide()
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
        self._save_settings()
        if not self._models_ready():
            QMessageBox.warning(self, "请先下载模型",
                                "缺少所选识别模型或翻译模型。请在「本地模型」点击「一键下载模型」或「从文件夹导入」。")
            return
        if not self._torch_version_ok():
            self._start_unavailable("PyTorch 版本过低。请先关闭程序，运行 repair_torch_windows.bat 后再打开。")
            return
        if not self.device_combo.currentData():
            self._start_unavailable("未选择音频设备。请在「声音来源」刷新设备并选择系统声音。")
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
        self.start_button.setText("模型加载中…")
        self._caption_state("loading", "模型加载完成后开始监听。")
        self.run_icon.set_mode("spin", highlight=True)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pipeline.start()

    def _start_unavailable(self, message: str) -> None:
        self._status("未开始翻译")
        self._caption_state("error", message)

    def stop_translation(self) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            return
        self.pipeline = None
        self._active_icon_tasks.clear()
        self.run_icon.set_mode("idle")
        self.audio_icon.set_mode("idle")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.volume.hide()
        self.audio_icon.hide()
        self.diagnostic_label.hide()
        self._status("已停止翻译")
        self.device_stat.setText("已停止")
        self._caption_state("default" if self._has_result else "empty", "" if self._has_result else FIRST_CAPTION_HINT)

        def task():
            pipeline.stop()
            self.bridge.stopped.emit()

        self._stop_thread = threading.Thread(target=task, name="pipeline-stop", daemon=True)
        self._stop_thread.start()

    def _on_stopped(self) -> None:
        self.start_button.setText("开始翻译")
        self.start_button.setEnabled(True)
        if self._restart_after_stop:
            self._restart_after_stop = False
            self.start_translation()

    def _caption_state(self, state, hint=""):
        self.caption_loading.setVisible(state == "loading" or (state == "empty" and not self._has_result))
        self.caption_hint.setText(hint)
        self.caption_hint.setVisible(bool(hint))
        if not self._has_result:
            text = {"loading": "正在加载", "error": "翻译暂不可用"}.get(state, "")
            self.chinese_label.setText(text)
            self.chinese_label.setVisible(bool(text))

    def _status(self, message: str) -> None:
        self.status_label.setText(f"当前状态：{message}")
        self.status_label.show()
        self.statusBar().showMessage(message)
        if message.startswith(("模型下载失败", "模型导入失败")):
            self._model_error = message
            self.model_state.setText(message + "。可以重试或导入本地模型。")
            self.model_state.show()
        if message.startswith("运行失败"):
            self._caption_state("error", message + "。可以检查音频设备与本地模型后重试。")
            self._active_icon_tasks.clear()
            self.run_icon.set_mode("idle")
            self.audio_icon.set_mode("idle")
            self.start_button.setText("开始翻译")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def _diagnostic(self, message: str) -> None:
        self.diagnostic_label.setText(f"处理进度：{message}")
        self.diagnostic_label.show()
        self.statusBar().showMessage(message)
        if self.pipeline:
            self.failures_label.setText(f"翻译失败次数：{self.pipeline.failures}")
        if message.startswith("检测到的语音过短"):
            self._record_trace(TaskTrace(0, "VAD 分段丢弃", message, discarded=True))

    def _receive_trace(self, payload) -> None:
        token, trace = payload
        if token is self._trace_token:
            self._record_trace(trace)

    def _record_trace(self, trace: TaskTrace) -> None:
        self._recent_trace.append(trace)
        if self.pipeline is not None:
            if trace.stage == "推理开始":
                self._active_icon_tasks.add((trace.task_id, "asr"))
            elif trace.stage == "翻译出队":
                self._active_icon_tasks.add((trace.task_id, "translation"))
            if trace.stage in {"推理结束", "结果丢弃"}:
                self._active_icon_tasks.discard((trace.task_id, "asr"))
            if trace.stage in {"翻译结束", "结果丢弃"}:
                self._active_icon_tasks.discard((trace.task_id, "translation"))
            self._refresh_run_icon()
        if trace.discarded:
            task = f" #{trace.task_id}" if trace.task_id else ""
            self.last_discard.setText(f"最近丢弃{task}：{trace.detail}")
            self.last_discard.show()
        if trace.timings:
            times = self._task_timings.pop(trace.task_id, {})
            times.update(trace.timings)
            self._task_timings[trace.task_id] = times
            if len(self._task_timings) > 10:
                del self._task_timings[next(iter(self._task_timings))]

    def _runtime(self, message: str) -> None:
        if self.pipeline:
            self.start_button.setText("开始翻译")
            self._refresh_run_icon()
            self._caption_state("default" if self._has_result else "empty",
                                "" if self._has_result else "正在监听所选声音来源。")
        self.runtime_label.setText(f"实际运行设备：{message}")
        self.runtime_label.show()
        runtime, marker, model = message.partition(" · Whisper ")
        devices = runtime.rsplit(" · ", 1)[0].replace("识别 ", "").replace("翻译 ", "")
        self.device_stat.setText(devices)
        if marker and model:
            self.model_stat.setText(model)

    @staticmethod
    def _set_caption_latency(label, seconds, hint):
        valid = math.isfinite(seconds) and seconds >= 0
        label.setText(f"{seconds:.2f} 秒" if valid else "")
        label.setToolTip(hint if valid else "")
        label.setVisible(valid)

    def _show_caption(self, translated, original, language, seconds, *, historical=False):
        self._has_result = True
        self._caption_state("default")
        self.language_label.setText(f"识别语言：{LANGUAGES.get(language, language)}" if language else "")
        self.language_label.setVisible(bool(language))
        self.original_label.setText(f"识别原文：{original}" if original else "")
        self.original_label.setVisible(self.settings.show_original and bool(original))
        if self.chinese_label.property("quiet"):
            self.chinese_label.setProperty("quiet", False)
            self.chinese_label.style().unpolish(self.chinese_label)
            self.chinese_label.style().polish(self.chinese_label)
        self.chinese_label.setText(translated)
        self.chinese_label.show()
        hint = "历史记录中的实际处理耗时" if historical else "音频结束至字幕显示的实测耗时"
        self._set_caption_latency(self.latency_stat, seconds, hint)

    def _result(self, result: SubtitleResult) -> None:
        if self._has_result:
            self.previous_caption.setText(self.chinese_label.text())
            self.previous_caption.show()
            self.previous_latency.setText(self.latency_stat.text())
            self.previous_latency.setToolTip(self.latency_stat.toolTip())
            self.previous_latency.setVisible(bool(self.previous_latency.text()))
        self._has_live_result = True
        self.overlay.add_subtitle(result.chinese, result.original)
        total = result.total_seconds if result.audio_end_time is None else time.monotonic() - result.audio_end_time
        self._show_caption(result.chinese, result.original, result.language, total)
        self.result_icon.set_mode("done", highlight=True, restart=True)
        self.caption_light.animate()
        confidence = result.confidence
        valid = confidence is not None and math.isfinite(confidence) and 0 <= confidence <= 1
        self.confidence_label.setVisible(valid)
        if valid:
            self.confidence_label.setText(f"Whisper 语种置信度：{confidence:.1%}")
        self.failures_label.setText(f"翻译失败次数：{self.pipeline.failures}" if self.pipeline else "")
        self.statusBar().showMessage(f"最近字幕：{result.chinese[:55]}")
        timings = (("识别", result.asr_seconds), ("翻译", result.translation_seconds), ("音频结束至显示", total))
        self.latency_label.setText(" · ".join(f"{name} {seconds:.2f}s" for name, seconds in timings
                                             if math.isfinite(seconds) and seconds >= 0))
        if result.task_id is not None:
            if self.pipeline is not None:
                self.pipeline.diagnostics.add("字幕已显示")
            self._record_trace(TaskTrace(result.task_id, "界面已更新", f"字幕已显示，处理耗时 {total:.2f} 秒"))
        if self.tabs.currentWidget() is self.history_page:
            self._refresh_history()
        elif self.settings.save_history:
            self._refresh_recent_translations()

    def _refresh_run_icon(self):
        running = bool(self._active_icon_tasks) or self.start_button.text() == "模型加载中…"
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
            "队友语音翻译器公开测试反馈",
            "问题类型（漏识别/错译/延迟/字幕/安装）：请填写",
            "实际听到的话：请填写",
            f"软件识别原文：{self.original_label.text().removeprefix('识别原文：')}",
            f"软件中文字幕：{self.chinese_label.text()}",
            f"识别语言：{self.language_label.text().removeprefix('识别语言：')}",
            f"运行设备：{self.runtime_label.text().removeprefix('实际运行设备：')}",
            f"耗时：{self.latency_label.text()}",
            f"处理进度：{self.diagnostic_label.text().removeprefix('处理进度：')}",
            f"识别模型：{self.settings.whisper_model}",
            "补充说明：请填写",
        ))
        report += "\n\n最近 10 条诊断（可能包含识别文字）\n"
        report += "\n".join(trace.line() for trace in self._recent_trace) or "暂无"
        report += "\n\n最近各句分段耗时\n"
        for task_id, times in self._task_timings.items():
            report += f"#{task_id} " + " · ".join(f"{name} {value:.3f} 秒" for name, value in times.items()) + "\n"
        if not self._task_timings:
            report += "暂无"
        if self._last_diagnostics:
            report += "\n\n最近会话队列与丢弃统计\n" + self._last_diagnostics.summary()
        QApplication.clipboard().setText(report)
        QMessageBox.information(self, "已复制反馈信息", "可粘贴给开发者；发送前请检查是否含有不想公开的对话文字。")

    def _check_update(self) -> None:
        if self._update_busy:
            return
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            QMessageBox.information(self, "仅安装版支持", "请在 Windows 安装版 EXE 中检查更新。源码版需重新打包。")
            return
        self._set_update_busy(True)
        try:
            channel = load_channel()
        except Exception as exc:
            self._update_error(str(exc))
            return
        self._domestic_update = channel["channel"] == "domestic"
        self._set_update_status("正在检查可用更新通道…")

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
            self.update_version_label.setText(f"当前版本：{APP_VERSION}；最新版本：{APP_VERSION}")
            self._set_update_status(f"当前版本 {APP_VERSION} 已是最新可用版本。")
            return
        self.update_version_label.setText(f"当前版本：{APP_VERSION}；最新版本：{release.version}")
        self.update_notes_label.setText(f"更新日志：{release.notes[:600] or '发布者没有填写更新说明。'}")
        self._set_update_status(f"发现新版 {release.version}，等待你选择是否安装。")
        box = QMessageBox(self)
        box.setWindowTitle("发现新版")
        box.setTextFormat(Qt.TextFormat.PlainText)
        install_hint = ("安装后程序会自动重新启动。" if self._domestic_update else
                        "程序会关闭并打开 Windows 安装程序。")
        box.setText(f"当前版本：{APP_VERSION}\n新版：{release.version}\n\n"
                    f"更新说明：\n{release.notes[:600] or '发布者没有填写更新说明。'}\n\n"
                    f"是否下载并安装？{install_hint}")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            self._set_update_busy(False)
            return
        self._set_update_status(f"正在下载并校验 {release.version}…")
        for button in (self.update_button, self.settings_update_button):
            button.setText("下载中…")
        self.update_bar.setValue(0)
        self.update_bar.show()
        self.update_cancel_button.show()
        self._update_cancel_event.clear()

        def task():
            try:
                path = download_installer(release, self.bridge.update_progress.emit,
                                          cancel=self._update_cancel_event,
                                          speed=self.bridge.update_speed.emit)
                self.bridge.update_ready.emit((path, release))
            except UpdateCancelled:
                self.bridge.update_cancelled.emit()
            except Exception as exc:
                LOG.exception("Update download failed")
                self.bridge.update_error.emit(str(exc))

        threading.Thread(target=task, name="update-download", daemon=True).start()

    def _update_progress(self, value: int) -> None:
        self.update_bar.setValue(value)

    def _update_speed(self, bytes_per_second: float) -> None:
        self._set_update_status(
            f"下载进度 {self.update_bar.value()}% · {bytes_per_second / 1024**2:.1f} MB/s")

    def _cancel_update(self) -> None:
        self._update_cancel_event.set()
        self.update_cancel_button.setEnabled(False)
        self._set_update_status("正在取消下载…")

    def _update_cancelled(self) -> None:
        self._set_update_busy(False)
        self._set_update_status("下载已取消；点击“检查更新”可重试。")

    def _update_ready(self, payload: tuple[Path, object]) -> None:
        path, release = payload
        try:
            if self._domestic_update:
                start_silent_installer(path, release)
            else:
                start_installer(path, release)
        except Exception as exc:
            self._update_error(f"无法启动安装程序：{exc}")
            return
        self._set_update_status("安装包已校验，正在关闭软件并启动安装程序…")
        self.close()
        QApplication.quit()

    def _update_error(self, message: str) -> None:
        self._set_update_busy(False)
        self._set_update_status(f"更新失败：{message}；点击“检查更新”可重试。")
        QMessageBox.warning(self, "更新失败", message)

    def _set_update_busy(self, busy: bool) -> None:
        self._update_busy = busy
        for button in (self.update_button, self.settings_update_button):
            button.setText("检查中…" if busy else "检查更新")
            button.setEnabled(not busy)
        if not busy:
            self.update_bar.hide()
            self.update_cancel_button.hide()
            self.update_cancel_button.setEnabled(True)

    def _set_update_status(self, text: str) -> None:
        self.update_label.setText(text)

    def _show_update_result(self) -> None:
        try:
            message = RESULT.read_text(encoding="utf-8-sig").strip()
            RESULT.unlink(missing_ok=True)
        except OSError:
            return
        if message:
            self._set_update_status(message)
            QMessageBox.warning(self, "更新结果", message)

    def _open_recent_translation(self, *_):
        self._navigate(self.tabs.indexOf(self.history_page))

    def _refresh_recent_translations(self):
        try:
            entries = self.history.recent(5, target_language="zh") if self.history is not None else []
            message = EMPTY_HISTORY if self.history is not None else "本地翻译历史暂不可用。"
        except Exception:
            LOG.exception("Could not read recent translations")
            entries = []
            message = "暂时读取不到翻译历史。"
        self.recent_list.clear()
        for entry in entries:
            timestamp = entry.created_at
            try:
                timestamp = datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
            except ValueError:
                pass
            text = entry.corrected_chinese or entry.chinese
            item = QTreeWidgetItem((text, timestamp))
            item.setToolTip(0, text)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.recent_list.addTopLevelItem(item)
        self.recent_hint.setText(message)
        self.recent_hint.setVisible(not entries)
        self.recent_list.setVisible(bool(entries))
        if entries:
            hint = self.recent_list.sizeHintForRow(0)
            self.recent_list.setFixedHeight(max(hint, self.recent_list.fontMetrics().height()) * len(entries)
                                            + 2 * self.recent_list.frameWidth())
        if self._has_live_result or self.pipeline is not None:
            return
        self._restore_history_caption(entries)

    def _restore_history_caption(self, entries):
        self.previous_caption.hide()
        self.previous_latency.hide()
        if entries:
            latest = entries[0]
            self._show_caption(latest.corrected_chinese or latest.chinese, latest.original,
                               latest.language, latest.total_seconds, historical=True)
            if len(entries) > 1:
                previous = entries[1]
                self.previous_caption.setText(previous.corrected_chinese or previous.chinese)
                self.previous_caption.show()
                self._set_caption_latency(self.previous_latency, previous.total_seconds,
                                          "历史记录中的实际处理耗时")
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
        self.history_state.setText("正在读取本地历史…")
        self.history_state.show()
        self.history_loading.show()
        QTimer.singleShot(tokens.HISTORY_LOAD_MS, self, self._refresh_history)

    def _refresh_history(self) -> None:
        self._history_pending = False
        self.history_loading.hide()
        self._refresh_recent_translations()
        if self.history is None:
            self.history_state.setText(f"历史记录暂不可用：{self.history_error}。请重新打开软件。")
            self.history_state.show()
            return
        try:
            entries = self.history.recent(100)
        except Exception as exc:
            self.history_state.setText(f"历史记录读取失败：{exc}。可点击刷新重试。")
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
            QMessageBox.information(self, "先选一条记录", "请在历史表格中选中一条记录。")
            return None
        try:
            return self.history.entry(entry_id)
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", str(exc))
            return None

    def _history_details(self):
        entry = self._selected_entry()
        if entry:
            preview(self, "翻译详情", entry_text(entry))

    def _correct_history(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        corrected, accepted = QInputDialog.getMultiLineText(
            self, "修正译文", f"识别原文：{entry.original}\n当前译文：{entry.corrected_chinese or entry.chinese}\n请输入正确译文：",
            entry.corrected_chinese or entry.chinese)
        if not accepted or not corrected.strip():
            return
        modes = ["仅修正本条历史", "保存到我的词库"]
        mode, accepted = QInputDialog.getItem(self, "修正范围", "选择这条修正的用途", modes, editable=False)
        if not accepted:
            return
        try:
            self.history.correct(entry.id, corrected, apply_future=False)
            if mode == modes[1]:
                rule = Rule(entry.game, entry.language, entry.target_language,
                            entry.corrected_original or entry.original, corrected.strip(),
                            origin="用户历史修正", priority=100, updated=now())
                conflict = self.custom_rules.add(rule)
                message = "已保存到我的词库" + ("。同范围冲突按词条优先级处理。" if conflict else "")
            else:
                message = "已修正本条历史"
            self._refresh_history()
            self.statusBar().showMessage(message)
        except Exception as exc:
            QMessageBox.warning(self, "保存修正失败", str(exc))

    def _feedback_history(self):
        entry = self._selected_entry()
        if entry:
            submit_feedback(self, self.feedback_store, entry)

    def _review_feedback(self):
        review_feedback(self, self.feedback_store, self.custom_rules)

    def _glossary_actions(self, form):
        row = QHBoxLayout()
        for text, action in (("查看默认词库", lambda: default_glossary(self)),
                             ("管理自定义词库", lambda: manage_rules(self, self.custom_rules)),
                             ("查看反馈记录", self._review_feedback)):
            button = GlassButton(text)
            button.clicked.connect(action)
            row.addWidget(button)
        row.addStretch()
        form.addRow(row)

    def _import_history(self):
        if self.history is None:
            return
        name, _ = QFileDialog.getOpenFileName(self, "导入旧历史", "", "CSV 文件 (*.csv)")
        if not name:
            return
        try:
            imported, skipped = self.history.import_csv(Path(name))
            self._refresh_history()
            self.statusBar().showMessage(f"已导入 {imported} 条。跳过 {skipped} 条。")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _export_history(self) -> None:
        if self.history is None:
            return
        name, _ = QFileDialog.getSaveFileName(self, "导出翻译历史", "翻译历史.csv", "CSV 文件 (*.csv)")
        if not name:
            return
        try:
            from pathlib import Path
            self.history.export_csv(Path(name))
            self.statusBar().showMessage(f"历史记录已导出：{name}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _clear_history(self) -> None:
        if self.history is None:
            return
        choice = QMessageBox.question(
            self, "清空翻译历史", "清空本机历史记录？你手动修正过的译法会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            self.history.clear_history()
            self._refresh_history()
        except Exception as exc:
            QMessageBox.warning(self, "清空失败", str(exc))

    def _clear_all_history(self) -> None:
        if self.history is None:
            return
        choice = QMessageBox.question(
            self, "删除全部本地记录", "删除全部翻译历史和手动修正？此操作无法撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            self.history.clear_all()
            self._refresh_history()
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _download(self) -> None:
        if self._model_busy or self._models_ready():
            return
        self._save_settings()
        if self.pipeline is not None:
            QMessageBox.information(self, "先停止翻译", "请先停止翻译，再下载或导入模型。")
            return
        if self._model_channel is None:
            self._status("模型下载配置缺失。请重新安装软件或导入本地模型。")
            return
        uses_cos = self._uses_cos_models()
        size = self.model_combo.currentData()
        self._model_pause.clear()
        self._set_model_busy(True)
        self.model_pause_button.setEnabled(True)
        self.model_pause_button.setVisible(uses_cos)
        self.download_button.setText("一键下载模型")

        def task():
            try:
                if uses_cos:
                    if size != "small" and size not in self.catalog:
                        self.catalog_checked.wait(9)
                    install_selected(self._model_channel, self.catalog, size, MODEL_DIR,
                                     self.bridge.status.emit, self.bridge.model_download_progress.emit,
                                     self._model_pause)
                else:
                    download_models(size, self.bridge.status.emit)
            except ModelPaused:
                self.bridge.status.emit("已暂停，点击下载可从上次位置继续。")
            except Exception as exc:
                LOG.exception("Model download failed")
                self.bridge.status.emit(f"模型下载失败：{exc}")
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
        self.model_transfer.setText("正在暂停…")
        self.model_transfer.show()

    def _model_download_progress(self, received: int, total: int, speed: float) -> None:
        if total <= 0:
            return
        self.model_progress.setRange(0, 100)
        self.model_progress.setValue(int(100 * received / total))
        mib = 1024**2
        remaining = max(0, total - received)
        rate = f"  ·  {speed / mib:,.1f} MiB/s" if speed > 0 else ""
        eta = f"  ·  预计 {math.ceil(remaining / speed)} 秒" if speed > 0 and remaining else ""
        self.model_transfer.setText(f"{received / mib:,.1f} / {total / mib:,.1f} MiB"
                                    f"  ·  剩余 {remaining / mib:,.1f} MiB{rate}{eta}")
        self.model_transfer.show()

    def _import_models(self) -> None:
        if self._model_busy:
            return
        if self.pipeline is not None:
            QMessageBox.information(self, "先停止翻译", "请先停止翻译，再下载或导入模型。")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择模型文件夹")
        if not folder:
            return
        self._set_model_busy(True)
        size = self.model_combo.currentData()

        def task():
            try:
                import_models(Path(folder), size, self.bridge.status.emit)
            except Exception as exc:
                LOG.exception("Model import failed")
                self.bridge.status.emit(f"模型导入失败：{exc}")
            finally:
                self.bridge.download_done.emit()

        threading.Thread(target=task, name="model-import", daemon=True).start()

    def _set_model_busy(self, busy: bool) -> None:
        was_busy = self._model_busy
        self._model_busy = busy
        self.download_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.model_progress.setVisible(busy)
        self.model_loading.setVisible(busy)
        if busy:
            self._model_error = ""
            self.model_state.setText("正在准备本地模型…")
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
                self._announce("✓ 模型已就绪", success=True)

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
        prompt.setWindowTitle("可选：语音分流")
        prompt.setText("可将队友语音单独送到翻译器。也可以继续使用系统混音。")
        setup = prompt.addButton("打开分流设置", QMessageBox.ButtonRole.AcceptRole)
        prompt.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
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

    def _begin_talk(self):
        if self._model_busy or not self._models_ready():
            self._talk_status("请先准备完整的识别和翻译模型。")
            return False
        if self.talk.capture is not None:
            return False
        try:
            devices = list_devices()
        except Exception as exc:
            self._talk_status(f"无法检查麦克风：{exc}")
            return False
        microphone = preferred_microphone(devices, self.settings.microphone_id)
        self._apply_microphones(devices, microphone)
        if microphone is None:
            self.microphone_stat.setText("未检测到")
            self._talk_status("未检测到麦克风。请连接后重试。")
            return False
        self.microphone_stat.setText(f"{microphone.name} · 可用")
        if microphone.id != self.settings.microphone_id:
            self.settings.microphone_id = microphone.id
            self.settings.save()
            self.statusBar().showMessage(f"已自动使用麦克风：{microphone.name}", 5000)
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
        message = f"麦克风 RMS {rms:.4f}"
        if self._quiet_since is not None and now - self._quiet_since >= 0.6:
            message += " · 麦克风没有收到声音，请检查麦克风选择"
        self.microphone_level.setText(message)
        self.microphone_level.setToolTip(message)
        self.microphone_level.show()

    def _save_reverse_history(self, details):
        chinese, english, game, audio_end, metrics = details
        if self.history is None or not self.settings.save_history:
            return
        metrics = dict(metrics)
        try:
            self.history.add_result(SubtitleResult(
                chinese, english, "zh", metrics.pop("asr_seconds", -1.0),
                metrics.pop("translation_seconds", -1.0), time.monotonic() - audio_end,
                game=game, target_language="en", **metrics,
            ))
            if self.tabs.currentWidget() is self.history_page:
                self._refresh_history()
        except Exception:
            LOG.exception("Could not save reverse history")

    def _talk_status(self, message):
        self.talk_state.setText(message)
        listening = message.startswith(("正在收听", "音频设备已连接"))
        translating = message.startswith("正在将中文语音翻译")
        self.microphone_icon.set_mode("pulse" if listening else "idle", highlight=listening)
        self.reverse_icon.set_mode("spin" if translating else "idle", highlight=translating)

    def _reverse_result(self, pair):
        chinese, english = pair
        self.reverse_text = english
        self.reverse_copy.setEnabled(True)
        self._talk_status(english)
        self.reverse_icon.set_mode("done", highlight=True, restart=True)
        self.overlay.add_subtitle(english, chinese, speaker="我 · 英文", reverse=True)

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
                self._talk_status("字幕快捷键格式无效，继续使用 Ctrl+Shift+T。")
            try:
                talk_keys = set(keyboard.HotKey.parse(self.settings.ptt_hotkey))
                if not talk_keys or talk_keys <= toggle_keys or toggle_keys <= talk_keys:
                    raise ValueError("conflict")
            except ValueError:
                talk_keys = set()
                self._talk_status("说话快捷键格式无效或与字幕键冲突，可先使用按住说话按钮。")
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
            self._talk_status("系统快捷键不可用，可在窗口中按住说话。")

    def closeEvent(self, event) -> None:
        if self.routing_wizard is not None:
            wizard, self.routing_wizard = self.routing_wizard, None
            wizard.close()
            wizard.deleteLater()
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
    app.setApplicationName("VALORANT AI 实时语音翻译器")
    window = MainWindow()
    window.show()
    if not window.settings.onboarding_done:
        QTimer.singleShot(0, window._show_onboarding)
    return app.exec()
