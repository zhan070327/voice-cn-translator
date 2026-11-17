from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from opencc import OpenCC

from valorant_translator.audio.capture import AudioCapture
from valorant_translator.audio.segmenter import SpeechChunk, SpeechSegmenter
from valorant_translator.config import Settings
from valorant_translator.recognition.whisper import LocalWhisper, Recognition
from valorant_translator.translation.glossary import Glossary
from valorant_translator.translation.m2m import LocalM2M

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubtitleResult:
    original: str
    chinese: str
    language: str
    asr_seconds: float
    translation_seconds: float
    total_seconds: float


@dataclass(frozen=True)
class RecognizedTask:
    chunk: SpeechChunk
    recognized: Recognition
    asr_seconds: float


class Pipeline:
    def __init__(self, settings: Settings, on_status: Callable[[str], None],
                 on_level: Callable[[float], None], on_result: Callable[[SubtitleResult], None],
                 on_diagnostic: Callable[[str], None] | None = None,
                 on_runtime: Callable[[str], None] | None = None,
                 history=None):
        self.settings = settings
        self.on_status = on_status
        self.on_level = on_level
        self.on_result = on_result
        self.on_diagnostic = on_diagnostic or (lambda _: None)
        self.on_runtime = on_runtime or (lambda _: None)
        self.history = history
        self.chinese_converter = OpenCC("t2s")
        self.jobs: queue.Queue[SpeechChunk] = queue.Queue(maxsize=4)
        self.translation_jobs: queue.Queue[RecognizedTask] = queue.Queue(maxsize=4)
        self.stop_event = threading.Event()
        self.asr_busy = threading.Event()
        self.capture: AudioCapture | None = None
        self.worker: threading.Thread | None = None
        self.translation_worker: threading.Thread | None = None
        self.segmenter = SpeechSegmenter(self.enqueue, settings.vad_level, self.on_diagnostic)
        self.last_text = ""
        self.last_result_time = 0.0
        self.failures = 0

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._work, name="model-worker", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.capture:
            self.capture.stop()
            self.capture = None
        self.segmenter.flush()
        for pending in (self.jobs, self.translation_jobs):
            while not pending.empty():
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=3)
        if self.translation_worker and self.translation_worker is not threading.current_thread():
            self.translation_worker.join(timeout=3)
        self.on_status("已停止")

    def enqueue(self, chunk: SpeechChunk) -> None:
        if self.stop_event.is_set():
            return
        if self.jobs.full():
            try:
                self.jobs.get_nowait()
                self.on_diagnostic("处理跟不上语音，已丢弃排队中最旧的一句")
            except queue.Empty:
                pass
        try:
            self.jobs.put_nowait(chunk)
            if self.asr_busy.is_set():
                self.on_diagnostic(
                    f"已收到 {len(chunk.audio) / 16000:.1f} 秒疑似语音；"
                    f"正在识别上一句，队列约 {self.jobs.qsize()} 句。"
                )
            else:
                self.on_diagnostic(f"已收到 {len(chunk.audio) / 16000:.1f} 秒疑似语音，等待识别…")
        except queue.Full:
            pass

    def _work(self) -> None:
        try:
            self.on_status("加载本地识别模型…")
            asr = LocalWhisper(self.settings.whisper_model, self.settings.compute_device,
                               self.settings.fast_mode)
            self.on_status("加载本地翻译模型…")
            translator = LocalM2M(self.settings.compute_device, self.settings.fast_mode)
            self.on_runtime(f"识别 {asr.device.upper()} · 翻译 {translator.device.upper()}"
                            + (" · 低延迟模式" if self.settings.fast_mode else " · 准确优先模式"))
            glossary = Glossary()
            if self.stop_event.is_set():
                return
            self.translation_worker = threading.Thread(
                target=self._translate_loop, args=(translator, glossary, asr),
                name="translation-worker", daemon=True)
            self.translation_worker.start()
            self.capture = AudioCapture(self.settings.device_id, self.segmenter.feed,
                                        self.on_level, self.on_status)
            self.capture.start()
            self.on_status(f"正在翻译（识别 {asr.device}，翻译 {translator.device}）")
            self.on_diagnostic("等待语音；音量条跳动不代表已检测到说话")
            while not self.stop_event.is_set():
                try:
                    chunk = self.jobs.get(timeout=0.2)
                except queue.Empty:
                    continue
                if time.monotonic() - chunk.end_time > self.settings.max_task_age:
                    self.on_diagnostic("语音排队过久，已丢弃旧字幕")
                    continue
                try:
                    t0 = time.monotonic()
                    self.on_diagnostic(f"正在识别 {len(chunk.audio) / 16000:.1f} 秒音频片段…")
                    self.asr_busy.set()
                    watchdog = threading.Timer(
                        5.0,
                        lambda: self.on_diagnostic(
                            "识别已超过 5 秒；音频可能过长或含有游戏音效，正在等待模型返回。"
                        ) if self.asr_busy.is_set() and not self.stop_event.is_set() else None,
                    )
                    watchdog.daemon = True
                    watchdog.start()
                    try:
                        recognized = asr.transcribe(chunk.audio, self.settings.language)
                    finally:
                        self.asr_busy.clear()
                        watchdog.cancel()
                    self.on_runtime(f"识别 {asr.device.upper()} · 翻译 {translator.device.upper()}"
                                    + (" · 低延迟模式" if self.settings.fast_mode else " · 准确优先模式"))
                    t1 = time.monotonic()
                    if not recognized:
                        self.on_diagnostic(
                            f"识别耗时 {t1 - t0:.1f} 秒，未得到可靠文字；游戏音效也可能触发语音检测。"
                            "请用清楚的完整句测试，必要时锁定主要语言。"
                        )
                        continue
                    if time.monotonic() - chunk.end_time > self.settings.max_task_age:
                        self.on_diagnostic("识别完成时字幕已经过期；请检查 GPU/CPU 运行状态")
                        continue
                    task = RecognizedTask(chunk, recognized, t1 - t0)
                    if self.translation_jobs.full():
                        try:
                            self.translation_jobs.get_nowait()
                            self.on_diagnostic("翻译队列已满，丢弃最旧的待翻译语音")
                        except queue.Empty:
                            pass
                    try:
                        self.translation_jobs.put_nowait(task)
                        self.on_diagnostic(f"识别到：{recognized.text[:45]}；等待翻译…")
                    except queue.Full:
                        self.on_diagnostic("翻译队列已满，本句未入队")
                except Exception as exc:
                    self.failures += 1
                    LOG.exception("Speech task failed")
                    self.on_status(f"该句翻译失败，继续监听：{exc}")
                    self.on_diagnostic(f"该句处理出错：{exc}")
        except Exception as exc:
            self.failures += 1
            LOG.exception("Pipeline failed")
            self.on_status(f"运行失败：{exc}")
            self.stop_event.set()
        finally:
            self.asr_busy.clear()
            if self.capture:
                self.capture.stop()
                self.capture = None
            if self.translation_worker and self.translation_worker is not threading.current_thread():
                self.translation_worker.join(timeout=3)

    def _translate_loop(self, translator: LocalM2M, glossary: Glossary,
                        asr: LocalWhisper) -> None:
        while not self.stop_event.is_set():
            try:
                task = self.translation_jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            chunk, recognized = task.chunk, task.recognized
            if time.monotonic() - chunk.end_time > self.settings.max_task_age:
                self.on_diagnostic("翻译排队过久，已丢弃旧字幕")
                continue
            try:
                normalized = glossary.normalize(recognized.text)
                if normalized == self.last_text and time.monotonic() - self.last_result_time < 1.5:
                    self.on_diagnostic("与刚显示的报点重复，已合并")
                    continue
                started = time.monotonic()
                translated = None
                if self.history is not None:
                    try:
                        translated = self.history.lookup_correction(normalized, recognized.language)
                    except Exception:
                        LOG.exception("Could not read translation correction")
                if translated is None:
                    translated = (self.chinese_converter.convert(recognized.text)
                                  if recognized.language == "zh" else
                                  glossary.callout(recognized.text, recognized.language))
                if translated is None:
                    translated = translator.translate(recognized.text, recognized.language)
                self.on_runtime(f"识别 {asr.device.upper()} · 翻译 {translator.device.upper()}"
                                + (" · 低延迟模式" if self.settings.fast_mode else " · 准确优先模式"))
                finished = time.monotonic()
                translation_seconds = finished - started
                if (translation_seconds > self.settings.translation_timeout
                        or finished - chunk.end_time > self.settings.max_task_age):
                    self.failures += 1
                    LOG.warning("Discarded stale translation")
                    self.on_diagnostic(
                        f"翻译耗时 {translation_seconds:.1f} 秒，字幕已过期；可在详细设置调整超时或检查 GPU"
                    )
                    continue
                translated = glossary.postprocess(translated, recognized.text)
                if not translated:
                    continue
                if not re.search(r"[\u4e00-\u9fff]", translated):
                    self.failures += 1
                    self.on_diagnostic("翻译未生成中文，已过滤；可在详细设置锁定语言后重试")
                    continue
                if self.stop_event.is_set():
                    continue
                result = SubtitleResult(recognized.text, translated, recognized.language,
                                        task.asr_seconds, translation_seconds,
                                        finished - chunk.end_time)
                self.last_text = normalized
                self.last_result_time = finished
                if self.history is not None and self.settings.save_history:
                    try:
                        self.history.add_result(result)
                    except Exception:
                        LOG.exception("Could not save translation history")
                self.on_result(result)
                self.on_diagnostic("翻译已显示在字幕窗口")
            except Exception as exc:
                self.failures += 1
                LOG.exception("Translation task failed")
                self.on_status(f"该句翻译失败，继续监听：{exc}")
                self.on_diagnostic(f"该句处理出错：{exc}")
