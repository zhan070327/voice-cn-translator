from pathlib import Path
import time

import numpy as np
import pytest
from PySide6.QtTest import QTest

from valorant_translator import config
from valorant_translator.audio.capture import AudioDevice, SAMPLE_RATE
from valorant_translator.ui import app as ui, microphone


class ControlledCapture:
    def __init__(self, device, frames, _level, status):
        self.device = device
        self.frames = frames
        self.status = status
        self.stopped = False

    def start(self):
        self.status("音频设备已连接，正在监听")

    def stop(self):
        self.stopped = True

    def feed(self, samples):
        block_size = SAMPLE_RATE // 10
        start = time.monotonic()
        for index in range(0, len(samples), block_size):
            self.frames(samples[index:index + block_size], start + index / SAMPLE_RATE)


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    devices = [
        AudioDevice("mic", "USB Mic", False),
        AudioDevice("mic-2", "Studio Mic", False),
        AudioDevice("cable", "CABLE Output (VB-Audio Virtual Cable)", False),
        AudioDevice("virtual", "BlackHole", False),
        AudioDevice("speaker", "Headset", True),
    ]
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(ui, "list_devices", lambda: devices)
    monkeypatch.setattr(ui, "load_channel", lambda: {})
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    monkeypatch.setattr(microphone, "AudioCapture", ControlledCapture)
    config.Settings(onboarding_done=True, routing_intro_seen=True).save()
    widget = ui.MainWindow()
    widget.show()
    try:
        yield widget
    finally:
        widget.close()


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def audio_sample(wizard, samples=None):
    wizard.sample_button.click()
    capture = wizard._capture
    assert capture is not None
    if samples is not None:
        capture.feed(samples)
        wait_for(lambda: wizard._has_frames)
    wizard._finish_sample()
    wait_for(lambda: not wizard._stopping)
    return capture


def test_microphone_and_teammate_audio_need_separate_evidence(window, monkeypatch):
    window.tabs.setCurrentWidget(window.audio_page)
    window.microphone_wizard_button.click()
    wizard = window.microphone_wizard
    assert wizard.isVisible()
    assert [wizard.mic.itemData(index) for index in range(wizard.mic.count())] == ["mic", "mic-2"]
    assert wizard.source.findData("cable") >= 0 and wizard.source.findData("speaker") >= 0
    assert wizard.source.findData("virtual") < 0
    wizard.mic.setCurrentIndex(wizard.mic.findData("mic-2"))
    wizard.next_button.click()
    assert not wizard.next_button.isEnabled()

    audio_sample(wizard, np.zeros(1600, dtype=np.float32))
    assert "收不到麦克风声音" in wizard.status.text()
    assert not wizard.next_button.isEnabled() and wizard.meter.value() == 0
    audio_sample(wizard, np.full(1600, .05, dtype=np.float32))
    assert wizard.next_button.isEnabled() and wizard.meter.value() > 0
    assert wizard.status.text() == "收到麦克风声音。"
    wizard.next_button.click()
    wizard.source.setCurrentIndex(wizard.source.findData("cable"))
    wizard.next_button.click()

    audio_sample(wizard, np.zeros(1600, dtype=np.float32))
    assert "收不到队友语音" in wizard.status.text()
    assert not wizard.next_button.isEnabled() and wizard.confirm_button.isHidden()
    audio_sample(wizard)
    assert "未收到音频数据" in wizard.status.text()
    wizard.sample_button.click()
    wizard._capture.status("音频采集失败：设备已断开")
    wait_for(lambda: wizard.sample_button.isEnabled())
    assert "无法采集音频" in wizard.status.text() and not wizard.next_button.isEnabled()

    # Signal processed by the production WebRTC VAD. Human attribution stays separate.
    seconds = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
    signal = sum(amp * np.sin(2 * np.pi * pitch * seconds)
                 for amp, pitch in ((1, 175), (.4, 350), (.2, 525)))
    signal *= .12 * np.sin(5 * np.pi * seconds) ** 2
    audio_sample(wizard, signal.astype(np.float32))
    assert "检测到语音" in wizard.status.text()
    assert wizard.confirm_button.isVisible() and not wizard.next_button.isEnabled()
    wizard.confirm_button.click()
    assert wizard.status.text() == "能收到队友语音。" and wizard.next_button.isEnabled()
    saves = []
    original_save = config.Settings.save
    before = Path(config.CONFIG_FILE).read_bytes()

    def fail_save(_):
        raise OSError("disk full")

    monkeypatch.setattr(config.Settings, "save", fail_save)
    wizard.next_button.click()
    assert window.microphone_wizard is wizard and "无法保存声音设置" in wizard.status.text()
    assert window.settings.device_id == window.device_combo.currentData() == "speaker"
    assert window.settings.microphone_id == window.mic_combo.currentData() == "mic"
    assert not window.settings.routing_verified_id and Path(config.CONFIG_FILE).read_bytes() == before

    def save(settings):
        saves.append((settings.microphone_id, settings.device_id))
        return original_save(settings)

    monkeypatch.setattr(config.Settings, "save", save)
    wizard.next_button.click()
    assert window.microphone_wizard is None
    assert saves == [("mic-2", "cable")]
    saved = config.Settings.load()
    assert (saved.microphone_id, saved.device_id, saved.routing_verified_id) == ("mic-2", "cable", "cable")
    assert "已确认分流" in window.routing_status.text()
    assert window.device_combo.currentText().startswith("系统声音")
    assert "当前选的是麦克风" not in window.device_hint.text()


def test_cancelling_capture_ignores_late_frames_and_keeps_existing_settings(window):
    before = Path(config.CONFIG_FILE).read_bytes()
    window.settings_microphone_wizard_button.click()
    wizard = window.microphone_wizard
    wizard.mic.setCurrentIndex(wizard.mic.findData("mic-2"))
    wizard.next_button.click()
    wizard.sample_button.click()
    capture = wizard._capture
    wizard.cancel_button.click()
    wait_for(lambda: capture.stopped)
    assert window.microphone_wizard is None
    # A late device callback after stop must not update the finished dialog.
    capture.feed(np.full(1600, .05, dtype=np.float32))
    capture.status("音频采集失败：迟到的状态")
    QTest.qWait(20)
    assert Path(config.CONFIG_FILE).read_bytes() == before
    assert window.settings.microphone_id == "mic" and window.settings.device_id == "speaker"


def test_refresh_lists_current_devices_without_auto_saving(window, monkeypatch):
    window.audio_devices = []
    before = Path(config.CONFIG_FILE).read_bytes()
    window.settings_microphone_wizard_button.click()
    wizard = window.microphone_wizard
    assert "未检测到真实麦克风" in wizard.status.text() and not wizard.next_button.isEnabled()

    monkeypatch.setattr(window, "_enumerate_audio_devices", lambda: [AudioDevice("new-mic", "New Mic", False)])
    wizard.refresh_button.click()
    wait_for(lambda: wizard.next_button.isEnabled())
    assert wizard.mic.currentData() == "new-mic"
    assert Path(config.CONFIG_FILE).read_bytes() == before
    wizard.cancel_button.click()


def test_intro_sound_action_opens_the_independent_wizard(window):
    window._show_onboarding()
    intro = window.onboarding
    intro.step = 2
    intro._refresh()
    intro.action.click()
    wait_for(lambda: window.microphone_wizard is not None)
    assert window.onboarding is None
    assert window.tabs.currentWidget() is window.audio_page
    assert window.microphone_wizard.step == 0
    assert window.routing_wizard is None
    window.microphone_wizard.reject()
