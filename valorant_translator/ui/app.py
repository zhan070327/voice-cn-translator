from __future__ import annotations

import logging
import math
import sys
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

from packaging.version import Version

from PySide6.QtCore import QObject, Qt, Signal, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QProgressBar,
                               QScrollArea, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget, QTableWidget,
                               QTableWidgetItem, QAbstractItemView,
                               QButtonGroup, QColorDialog, QFileDialog, QInputDialog)

from valorant_translator.audio.capture import list_devices
from valorant_translator.config import APP_DIR, MODEL_DIR, Settings
from valorant_translator.history import HistoryStore
from valorant_translator.models.catalog import fetch_catalog, install_selected
from valorant_translator.models.cos import ModelPaused
from valorant_translator.models.download import (download_models, import_models,
                                                 model_ready, WHISPER_FILES, M2M_FILES)
from valorant_translator.pipeline import Pipeline, SubtitleResult, TaskTrace
from valorant_translator.recognition.whisper import LANGUAGES
from valorant_translator.translation.glossary import GAMES
from valorant_translator.ui.glass import GlassButton, GlassSurface, glass_styles
from valorant_translator.ui.onboarding import Onboarding
from valorant_translator.ui.overlay import SubtitleOverlay
from valorant_translator.ui.talk import PushToTalk
from valorant_translator.updates.channel import check_for_update
from valorant_translator.updates.domestic import load_channel
from valorant_translator.updates.github import (UpdateCancelled, download_installer,
                                                start_installer)
from valorant_translator.updates.windows import RESULT, start_silent_installer
from valorant_translator.version import APP_VERSION

LOG = logging.getLogger(__name__)


class Bridge(QObject):
    status = Signal(str)
    level = Signal(float)
    result = Signal(object)
    toggle = Signal()
    talk_begin = Signal()
    talk_end = Signal()
    talk_status = Signal(str)
    talk_result = Signal(object)
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
        self.bridge = Bridge()
        self.talk = PushToTalk(self.bridge.talk_status.emit, self.bridge.talk_result.emit)
        self.bridge.talk_begin.connect(self._begin_talk)
        self.bridge.talk_end.connect(self.talk.end)
        self.bridge.talk_result.connect(self._reverse_result)
        self.bridge.talk_status.connect(self._talk_status)
        self.bridge.model_catalog.connect(self._catalog_ready)
        self.bridge.status.connect(self._status)
        self.bridge.level.connect(lambda value: self.volume.setValue(int(value * 100)))
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
        self.tabs.currentChanged.connect(lambda _: self._refresh_history())
        self._refresh_history()
        self.statusBar().showMessage("待机 · 尚无翻译")
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
        main_layout.setContentsMargins(22, 18, 22, 20)
        main_layout.setSpacing(13)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(20, 15, 20, 15)
        hero_layout.setSpacing(5)
        eyebrow = QLabel("VOICE / CN    ·    公开测试版")
        eyebrow.setObjectName("eyebrow")
        hero_top = QHBoxLayout()
        hero_top.addWidget(eyebrow)
        hero_top.addStretch()
        self.update_button = GlassButton("检查更新")
        self.update_button.clicked.connect(self._check_update)
        hero_top.addWidget(self.update_button)
        hero_layout.addLayout(hero_top)
        title = QLabel("队友语音，实时看懂")
        title.setObjectName("title")
        hero_layout.addWidget(title)
        intro = QLabel("选好声音来源，点开始翻译。首次准备模型需要联网，之后识别和翻译在本机完成。")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        hero_layout.addWidget(intro)
        self.update_label = self._hint(f"当前版本：{APP_VERSION}。仅点击检查更新时联网。")
        hero_layout.addWidget(self.update_label)
        self.update_bar = QProgressBar()
        self.update_bar.setRange(0, 100)
        self.update_bar.hide()
        hero_layout.addWidget(self.update_bar)
        self.update_cancel_button = GlassButton("取消下载")
        self.update_cancel_button.clicked.connect(self._cancel_update)
        self.update_cancel_button.hide()
        hero_layout.addWidget(self.update_cancel_button)
        main_layout.addWidget(hero)
        cards = QHBoxLayout()
        self.model_stat = self._stat(cards, "识别模型", "未加载")
        self.device_stat = self._stat(cards, "运行设备", "未检测")
        self.latency_stat = self._stat(cards, "延迟", "等待翻译")
        self.microphone_stat = self._stat(cards, "麦克风", "未检测")
        main_layout.addLayout(cards)
        warning = QLabel("⚠ 游戏内实时字幕有 Riot 政策和反作弊风险；本工具未经 Riot 批准。请先在桌面或第二屏测试。")
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        main_layout.addWidget(warning)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        left_column = QVBoxLayout()
        right_column = QVBoxLayout()
        left_column.setSpacing(13)
        right_column.setSpacing(13)
        columns.addLayout(left_column, 11)
        columns.addLayout(right_column, 9)
        main_layout.addLayout(columns, 1)

        model_group, model_layout = self._group("① 准备本地模型")
        self.model_status_label = QLabel("正在检查模型…")
        model_layout.addWidget(self.model_status_label)
        model_row = QHBoxLayout()
        self.download_button = GlassButton("一键下载模型")
        self.download_button.clicked.connect(self._download)
        self.import_button = GlassButton("从文件夹导入")
        self.import_button.clicked.connect(self._import_models)
        model_row.addWidget(self.download_button)
        model_row.addWidget(self.import_button)
        self.model_pause_button = GlassButton("暂停下载")
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
        self.model_transfer.hide()
        model_layout.addWidget(self.model_transfer)
        model_layout.addWidget(self._hint("按设置中的型号下载，也可导入已有模型。下载完成后可离线使用。"))
        left_column.addWidget(model_group)

        audio_group, audio_layout = self._group("② 选择要听的声音")
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._save_settings)
        self.refresh_button = GlassButton("刷新设备")
        self.refresh_button.clicked.connect(self._request_devices)
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        audio_layout.addLayout(device_row)
        self.device_hint = self._hint("")
        audio_layout.addWidget(self.device_hint)
        left_column.addWidget(audio_group)

        display_group, display_layout = self._group("③ 选择字幕显示方式")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("桌面 / 第二显示器（推荐）", "desktop")
        self.mode_combo.addItem("游戏窗口覆盖（实验）", "game")
        self.mode_combo.setCurrentIndex(0 if self.settings.normal_window else 1)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        display_layout.addWidget(self.mode_combo)
        self.mode_hint = self._hint("")
        display_layout.addWidget(self.mode_hint)
        overlay_row = QHBoxLayout()
        self.test_button = GlassButton("测试字幕（20 秒）")
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

        run_group, run_layout = self._group("④ 开始翻译")
        run_row = QHBoxLayout()
        self.start_button = GlassButton("开始翻译")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_translation)
        self.stop_button = GlassButton("停止翻译")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_translation)
        run_row.addWidget(self.start_button)
        run_row.addWidget(self.stop_button)
        run_row.addStretch()
        run_layout.addLayout(run_row)
        self.status_label = QLabel("当前状态：待机")
        self.status_label.setWordWrap(True)
        run_layout.addWidget(self.status_label)
        self.runtime_label = self._hint("实际运行设备：模型尚未加载")
        run_layout.addWidget(self.runtime_label)
        run_layout.addWidget(QLabel("音频输入音量"))
        self.volume = QProgressBar()
        self.volume.setRange(0, 100)
        self.volume.setValue(0)
        run_layout.addWidget(self.volume)
        self.diagnostic_label = self._hint("处理进度：尚未开始；可先用麦克风说一句完整的话自测。")
        self.diagnostic_label.setTextFormat(Qt.TextFormat.PlainText)
        run_layout.addWidget(self.diagnostic_label)
        self.last_discard = self._hint("")
        self.last_discard.setObjectName("lastDiscard")
        self.last_discard.setTextFormat(Qt.TextFormat.PlainText)
        self.last_discard.hide()
        run_layout.addWidget(self.last_discard)
        right_column.addWidget(run_group)

        talk_group, talk_layout = self._group("中文语音 → 英文")
        self.talk_state = self._hint("按住说话快捷键或下方按钮，松开后翻译。")
        talk_layout.addWidget(self.talk_state)
        self.talk_button = GlassButton("按住说话")
        self.talk_button.pressed.connect(self._begin_talk)
        self.talk_button.released.connect(self.talk.end)
        self.reverse_copy = GlassButton("复制英文")
        self.reverse_copy.setEnabled(False)
        self.reverse_copy.clicked.connect(self._copy_english)
        talk_row = QHBoxLayout()
        talk_row.addWidget(self.talk_button)
        talk_row.addWidget(self.reverse_copy)
        talk_layout.addLayout(talk_row)
        right_column.addWidget(talk_group)

        result_group, result_layout = self._group("最近一条翻译")
        self.chinese_label = QLabel("等待语音…")
        self.chinese_label.setObjectName("translation")
        self.chinese_label.setWordWrap(True)
        result_layout.addWidget(self.chinese_label)
        self.original_label = self._hint("识别原文：—")
        self.language_label = self._hint("识别语言：—")
        self.latency_label = self._hint("音频结束至显示：—")
        self.failures_label = self._hint("翻译失败次数：0")
        self.device_label = self._hint("当前音频设备：未选择")
        for widget in (self.original_label, self.language_label, self.latency_label,
                       self.failures_label, self.device_label):
            result_layout.addWidget(widget)
        self.confidence_label = self._hint("")
        self.confidence_label.hide()
        result_layout.addWidget(self.confidence_label)
        right_column.addWidget(result_group)
        self.feedback_button = GlassButton("复制反馈信息")
        self.feedback_button.clicked.connect(self._copy_feedback)
        right_column.addWidget(self.feedback_button)
        left_column.addStretch()
        right_column.addStretch()
        self.tabs.addTab(self._scroll(main_page), "快速开始")

        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(22, 18, 22, 22)
        settings_layout.setSpacing(12)
        form = self._settings_card(settings_layout, "软件更新")
        self.settings_update_button = GlassButton("检查更新")
        self.settings_update_button.clicked.connect(self._check_update)
        form.addRow(self.settings_update_button)
        self.update_version_label = self._hint(f"当前版本：{APP_VERSION}；最新版本：尚未检查")
        form.addRow(self.update_version_label)
        self.update_notes_label = self._hint("更新日志：检查后显示")
        form.addRow(self.update_notes_label)
        form = self._settings_card(settings_layout, "识别和翻译")
        self.language_combo = self._combo([("自动检测五种外语及中文", "auto"),
                                            *[(name, code) for code, name in LANGUAGES.items()]], self.settings.language)
        form.addRow("识别语言", self.language_combo)
        self.model_combo = self._combo([(name, name) for name in ("tiny", "base", "small", "medium")],
                                       self.settings.whisper_model)
        form.addRow("识别模型", self.model_combo)
        if self._model_channel and self._model_channel["channel"] == "dual":
            self.model_source = self._combo([("国内 COS", "cos"), ("国际 Hugging Face", "huggingface")],
                                            self.settings.model_source)
            form.addRow("模型下载来源", self.model_source)
        form.addRow("翻译模型", QLabel("M2M100 418M（本地）"))
        self.compute_combo = self._combo([("自动：优先 GPU", "auto"), ("GPU", "cuda"), ("CPU", "cpu")],
                                         self.settings.compute_device)
        form.addRow("运行设备", self.compute_combo)
        self.fast_check = self._check("低延迟模式（可能略降识别和翻译质量）", self.settings.fast_mode)
        form.addRow(self.fast_check)
        self.ptt_hotkey_edit = QLineEdit(self.settings.ptt_hotkey)
        self.ptt_hotkey_edit.editingFinished.connect(self._save_settings)
        form.addRow("按住说话快捷键", self.ptt_hotkey_edit)
        self.mic_combo = QComboBox()
        self.mic_combo.currentIndexChanged.connect(self._save_settings)
        form.addRow("说话用麦克风", self.mic_combo)
        self.vad_spin = self._spin(self.settings.vad_level, 0, 3)
        form.addRow("语音检测严格度（短报点建议 1）", self.vad_spin)
        self.timeout_spin = self._spin(int(self.settings.translation_timeout), 1, 30)
        form.addRow("翻译超时（秒）", self.timeout_spin)
        self.age_spin = self._spin(int(self.settings.max_task_age), 2, 30)
        form.addRow("过期字幕丢弃（秒）", self.age_spin)
        form = self._settings_card(settings_layout, "字幕")
        self.screen_combo = QComboBox()
        for index, screen in enumerate(QApplication.screens()):
            self.screen_combo.addItem(f"显示器 {index + 1}：{screen.name()}", index)
        self.screen_combo.setCurrentIndex(min(self.settings.overlay_screen, max(0, self.screen_combo.count() - 1)))
        self.screen_combo.currentIndexChanged.connect(self._save_settings)
        form.addRow("字幕显示器", self.screen_combo)
        self.x_spin = self._spin(self.settings.overlay_x, -5000, 5000)
        self.y_spin = self._spin(self.settings.overlay_y, -5000, 5000)
        form.addRow("字幕 X 位置", self.x_spin)
        form.addRow("字幕 Y 位置", self.y_spin)
        self.font_spin = self._spin(self.settings.font_size, 14, 60)
        form.addRow("字幕字号", self.font_spin)
        self.opacity_spin = self._spin(self.settings.overlay_opacity, 0, 100)
        form.addRow("背景不透明度", self.opacity_spin)
        self.original_check = self._check("同时显示识别原文", self.settings.show_original)
        self.click_check = self._check("鼠标穿透（调整位置时请关闭）", self.settings.click_through)
        form.addRow(self.original_check)
        form.addRow(self.click_check)
        self.hotkey_edit = QLineEdit(self.settings.hotkey)
        self.hotkey_edit.editingFinished.connect(self._save_settings)
        form.addRow("显示/隐藏字幕快捷键", self.hotkey_edit)
        theme = GlassButton("选择点缀色")
        theme.clicked.connect(self._choose_accent)
        form.addRow("主题色", theme)
        self.duration_spin = self._spin(int(self.settings.subtitle_seconds), 1, 30)
        form.addRow("字幕显示时长（秒）", self.duration_spin)
        form.addRow(self._hint("音频设备与字幕模式在“快速开始”页面设置。更改识别模型后需下载对应模型。"))
        form = self._settings_card(settings_layout, "历史和隐私")
        self.history_check = self._check("保存本地翻译历史（不保存音频）", self.settings.save_history)
        form.addRow(self.history_check)
        form.addRow(self._hint("诊断记录仅在本次运行内保留。复制反馈前请检查是否包含对话原文。"))
        settings_layout.addStretch()
        self.tabs.addTab(self._scroll(settings_page), "详细设置")

        self.history_page = QWidget()
        history_layout = QVBoxLayout(self.history_page)
        history_layout.setContentsMargins(20, 20, 20, 20)
        history_layout.addWidget(self._hint(
            "仅在本机保存最近 1000 条识别原文和中文，不保存音频。选中一条可修正译文；同样的原文之后会优先使用你的修正。"
        ))
        if self.history_error:
            history_layout.addWidget(self._hint(f"历史记录暂不可用：{self.history_error}"))
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(("时间", "识别原文", "中文字幕", "语言", "延迟"))
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
        for label, callback in (("刷新", self._refresh_history),
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
        self.tabs.addTab(self.history_page, "翻译历史")
        self._build_shell()

    def _build_shell(self):
        self.surface = GlassSurface()
        shell = QHBoxLayout(self.surface)
        shell.setContentsMargins(15, 15, 15, 15)
        shell.setSpacing(14)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(164)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(13, 21, 13, 15)
        brand = QLabel("VoiceTrans")
        brand.setObjectName("brand")
        side.addWidget(brand)
        side.addSpacing(18)
        self.nav = QButtonGroup(self)
        for index in range(self.tabs.count()):
            button = GlassButton(self.tabs.tabText(index))
            button.setCheckable(True)
            self.nav.addButton(button, index)
            side.addWidget(button)
        self.nav.button(0).setChecked(True)
        self.nav.idClicked.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(lambda index: self.nav.button(index).setChecked(True))
        side.addStretch()
        guide = GlassButton("新手引导")
        guide.clicked.connect(self._show_onboarding)
        side.addWidget(guide)
        side.addWidget(self._hint(f"v{APP_VERSION}"))
        shell.addWidget(sidebar)
        content = QVBoxLayout()
        game_bar = QFrame()
        game_bar.setObjectName("glassCard")
        games = QHBoxLayout(game_bar)
        games.setContentsMargins(11, 10, 11, 10)
        self.game_group = QButtonGroup(self)
        for key, name in GAMES.items():
            button = GlassButton(name)
            button.setCheckable(True)
            button.setChecked(key == self.settings.game)
            button.clicked.connect(lambda checked=False, game=key: self._select_game(game))
            self.game_group.addButton(button)
            games.addWidget(button)
        content.addWidget(game_bar)
        self.game_notice = self._hint("")
        self.game_notice.hide()
        content.addWidget(self.game_notice)
        self.tabs.tabBar().hide()
        content.addWidget(self.tabs, 1)
        shell.addLayout(content, 1)
        self.setCentralWidget(self.surface)
        self.setStyleSheet(glass_styles(self.settings.accent))

    def _stat(self, row, title, initial):
        frame = QFrame()
        frame.setObjectName("glassCard")
        contents = QVBoxLayout(frame)
        contents.addWidget(self._hint(title))
        value = self._hint(initial)
        value.setObjectName("statValue")
        contents.addWidget(value)
        row.addWidget(frame, 1)
        return value

    def download_sizes(self):
        if not self._uses_cos_models():
            return {}
        size = self.model_combo.currentData()
        specs = self._model_channel.get("model_bundle", {}).get("component_compressed_bytes", {})
        sizes = {"M2M100 418M": specs.get("m2m100_418M")}
        bundle = self.catalog.get(size)
        sizes[f"Whisper {size}"] = bundle.size if bundle else specs.get(f"whisper-{size}")
        return sizes

    def _choose_accent(self):
        from PySide6.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.settings.accent), self, "选择点缀色")
        if color.isValid():
            self.settings.accent = color.name()
            self.settings.save()
            self.setStyleSheet(glass_styles(self.settings.accent))

    def _select_game(self, game):
        if game == self.settings.game:
            return
        self.settings.game = game
        self.settings.save()
        self.game_notice.setText(f"已切换至 {GAMES[game]}")
        self.game_notice.show()
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

    @staticmethod
    def _settings_card(parent_layout, title):
        card = QGroupBox(title)
        form = QFormLayout(card)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSpacing(10)
        parent_layout.addWidget(card)
        return form

    @staticmethod
    def _group(title: str) -> tuple[QGroupBox, QVBoxLayout]:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.setSpacing(9)
        return group, layout

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
            self.model_stat.setText(f"{size} · 已下载" if whisper else f"{size} · 未下载")
        self.model_status_label.setText(
            f"识别模型 {size}：{'已下载' if whisper else '未下载'}  ·  中文翻译模型：{'已下载' if m2m else '未下载'}"
            + ("  ·  PyTorch 需更新至 2.6 以上" if not self._torch_version_ok() else "")
        )
        self.download_button.setEnabled(not self._model_busy and not (whisper and m2m))
        stopping = self._stop_thread is not None and self._stop_thread.is_alive()
        if (hasattr(self, "start_button") and self.pipeline is None and not stopping
                and not getattr(self, "_model_busy", False)):
            self.start_button.setEnabled(whisper and m2m)

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
        settings.device_id = str(self.device_combo.currentData() or "")
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
        settings.save_history = self.history_check.isChecked()
        settings.normal_window = self.mode_combo.currentData() == "desktop"
        settings.click_through = self.click_check.isChecked() and not settings.normal_window
        if settings.normal_window and self.click_check.isChecked():
            self.click_check.blockSignals(True)
            self.click_check.setChecked(False)
            self.click_check.blockSignals(False)
        settings.hotkey = self.hotkey_edit.text().strip()
        settings.ptt_hotkey = self.ptt_hotkey_edit.text().strip()
        settings.microphone_id = str(self.mic_combo.currentData() or "")
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
            self.mode_hint.setText(
                "先在 VALORANT 中选择“无边框窗口/窗口化全屏”，再回到这里点“测试字幕”。"
                "然后点“开始翻译”并切回游戏。独占全屏和 Vanguard 可能使普通窗口不可见，"
                "不保证游戏内置顶；可用快捷键显示/隐藏字幕。"
            )
            self.position_button.setText("调整位置（暂时关闭穿透）" if self.click_check.isChecked() else "完成调整（开启穿透）")
        else:
            self.click_check.setEnabled(False)
            self.mode_hint.setText("推荐先在桌面或第二显示器测试。字幕是独立窗口，可直接拖动；不会改变游戏画面。")
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
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        for device in devices:
            if not device.loopback:
                self.mic_combo.addItem(device.name, device.id)
        self.mic_combo.setCurrentIndex(max(0, self.mic_combo.findData(self.settings.microphone_id)))
        self.mic_combo.blockSignals(False)
        self._save_settings()
        self.device_label.setText(f"当前音频设备：{self.device_combo.currentText() or '未选择'}")

    def _update_device_hint(self) -> None:
        self.microphone_stat.setText(self.mic_combo.currentText() or "未检测到")
        if self.device_combo.currentIndex() < 0:
            self.device_hint.setText("没有可用设备。请连接耳机/扬声器并点“刷新设备”。")
            self.device_hint.setStyleSheet("color: #ffd699;")
        elif not self.device_combo.currentData(Qt.ItemDataRole.UserRole + 1):
            self.device_hint.setText(
                "⚠ 当前选的是麦克风，只能听到麦克风输入。翻译队友请改选“系统声音：耳机/扬声器”。")
            self.device_hint.setStyleSheet("color: #ffd699;")
        else:
            self.device_hint.setText(
                "正在听系统播放声音；队友语音需要从这副耳机/扬声器输出，也会听到游戏音效。")
            self.device_hint.setStyleSheet("")

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
            QMessageBox.warning(self, "请先下载模型", "请先点击第 ① 步的“下载所选模型”，等待完成后再开始。")
            return
        if not self._torch_version_ok():
            QMessageBox.warning(self, "PyTorch 版本过低",
                                "请先关闭程序，在项目文件夹双击 repair_torch_windows.bat，更新完成后再打开。")
            return
        if not self.settings.device_id:
            QMessageBox.warning(self, "未选择音频设备", "请在第 ② 步选择“系统声音”设备。")
            return
        self._recent_trace.clear()
        self._task_timings.clear()
        self.last_discard.hide()
        self._trace_token = token = object()
        self.pipeline = Pipeline(self._effective_settings(), self.bridge.status.emit, self.bridge.level.emit,
                                 self.bridge.result.emit, self.bridge.diagnostic.emit,
                                 self.bridge.runtime.emit, self.history,
                                 on_trace=lambda trace: self.bridge.trace.emit((token, trace)))
        self.start_button.setText("模型加载中…")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pipeline.start()
        self.device_label.setText(f"当前音频设备：{self.device_combo.currentText()}")

    def stop_translation(self) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            return
        self.pipeline = None
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.volume.setValue(0)
        self.device_stat.setText("已停止")

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

    def _status(self, message: str) -> None:
        self.status_label.setText(f"当前状态：{message}")
        self.statusBar().showMessage(message)
        if message.startswith("运行失败"):
            self.start_button.setText("开始翻译")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def _diagnostic(self, message: str) -> None:
        self.diagnostic_label.setText(f"处理进度：{message}")
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
        self.runtime_label.setText(f"实际运行设备：{message}")
        runtime, marker, model = message.partition(" · Whisper ")
        self.device_stat.setText(runtime.rsplit(" · ", 1)[0])
        if marker and model:
            self.model_stat.setText(model)

    def _result(self, result: SubtitleResult) -> None:
        confidence = result.confidence
        valid = confidence is not None and math.isfinite(confidence) and 0 <= confidence <= 1
        self.confidence_label.setVisible(valid)
        if valid:
            self.confidence_label.setText(f"Whisper 语种置信度：{confidence:.1%}")
        self.language_label.setText(f"识别语言：{LANGUAGES.get(result.language, result.language)}")
        self.original_label.setText(f"识别原文：{result.original}")
        self.chinese_label.setText(result.chinese)
        self.failures_label.setText(f"翻译失败次数：{self.pipeline.failures if self.pipeline else 0}")
        self.statusBar().showMessage(f"最近字幕：{result.chinese[:55]}")
        self.overlay.add_subtitle(result.chinese, result.original)
        total = result.total_seconds if result.audio_end_time is None else time.monotonic() - result.audio_end_time
        self.latency_stat.setText(f"{total:.2f} 秒")
        self.latency_label.setText(
            f"识别 {result.asr_seconds:.2f}s · 翻译 {result.translation_seconds:.2f}s · 音频结束至显示 {total:.2f}s"
        )
        if result.task_id is not None:
            self._record_trace(TaskTrace(result.task_id, "界面已更新", f"字幕已显示，处理耗时 {total:.2f} 秒"))
        if self.tabs.currentWidget() is self.history_page:
            self._refresh_history()

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

    def _refresh_history(self) -> None:
        if self.history is None or not hasattr(self, "history_table"):
            return
        try:
            entries = self.history.recent(100)
        except Exception as exc:
            self.statusBar().showMessage(f"历史记录读取失败：{exc}")
            return
        self.history_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = (entry.created_at.replace("T", " ")[:19], entry.original,
                      entry.corrected_chinese or entry.chinese,
                      LANGUAGES.get(entry.language, entry.language),
                      f"{entry.total_seconds:.2f}s")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry.id)
                if column == 2 and entry.corrected_chinese:
                    item.setToolTip(f"机器原译：{entry.chinese}")
                self.history_table.setItem(row, column, item)

    def _selected_history_id(self) -> int | None:
        row = self.history_table.currentRow()
        item = self.history_table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _correct_history(self) -> None:
        entry_id = self._selected_history_id()
        if entry_id is None or self.history is None:
            QMessageBox.information(self, "先选一条记录", "请先在表格中选中需要修正的译文。")
            return
        row = self.history_table.currentRow()
        original = self.history_table.item(row, 1).text()
        current = self.history_table.item(row, 2).text()
        corrected, accepted = QInputDialog.getMultiLineText(
            self, "修正译文", f"识别原文：{original}\n请输入正确的简体中文：", current)
        if not accepted:
            return
        try:
            self.history.correct(entry_id, corrected)
            self._refresh_history()
            self.statusBar().showMessage("修正已保存在本机；同样的识别原文之后会优先使用")
        except Exception as exc:
            QMessageBox.warning(self, "保存修正失败", str(exc))

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
        self._model_busy = busy
        self.download_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.model_progress.setVisible(busy)
        if busy:
            self.model_progress.setRange(0, 0)
            self.start_button.setEnabled(False)
        else:
            self.model_pause_button.hide()
            self.model_transfer.hide()
            self._refresh_model_status()

    def _download_done(self) -> None:
        self._set_model_busy(False)

    def _effective_settings(self):
        if self._model_channel and self._model_channel.get("edition") == "cpu":
            return replace(self.settings, compute_device="cpu")
        return self.settings

    def _begin_talk(self):
        if self._model_busy or not self._models_ready():
            self._talk_status("请先准备完整的识别和翻译模型。")
            return False
        return self.talk.begin(self._effective_settings(),
                               self.pipeline.translate_microphone if self.pipeline else None)

    def _talk_status(self, message):
        self.talk_state.setText(message)

    def _reverse_result(self, pair):
        chinese, english = pair
        self.reverse_text = english
        self.reverse_copy.setEnabled(True)
        self._talk_status(english)
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
