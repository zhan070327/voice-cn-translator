from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import count
from typing import Callable

from opencc import OpenCC

from valorant_translator.audio.capture import AudioCapture
from valorant_translator.audio.segmenter import SpeechChunk, SpeechSegmenter
from valorant_translator.config import Settings
from valorant_translator.recognition.whisper import LocalWhisper, Recognition
from valorant_translator.translation.glossary import Glossary, game_glossary
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
    confidence: float | None = None
    task_id: int | None = None
    audio_end_time: float | None = None


@dataclass(frozen=True)
class RecognizedTask:
    chunk: SpeechChunk
    recognized: Recognition
    asr_seconds: float
    queued_at: float


@dataclass(frozen=True)
class TaskTrace:
    task_id: int
    stage: str
    detail: str
    discarded: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))

    def line(self) -> str:
        return f"{self.timestamp} #{self.task_id} {self.stage}：{self.detail}"


class Pipeline:
    def __init__(self, settings: Settings, on_status: Callable[[str], None],
                 on_level: Callable[[float], None], on_result: Callable[[SubtitleResult], None],
                 on_diagnostic: Callable[[str], None] | None = None,
                 on_runtime: Callable[[str], None] | None = None,
                 history=None, *, on_trace: Callable[[TaskTrace], None] | None = None):
        self.settings = settings
        self.asr_lock = threading.Lock()
        self.mt_lock = threading.Lock()
        self.model_session = None
        self.on_status = on_status
        self.on_level = on_level
        self.on_result = on_result
        self.on_diagnostic = on_diagnostic or (lambda _: None)
        self.on_runtime = on_runtime or (lambda _: None)
        self.on_trace = on_trace or (lambda _: None)
        self.task_ids = count(1)
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
        chunk = replace(chunk, task_id=next(self.task_ids), queued_at=time.monotonic())
        duration = len(chunk.audio) / 16000
        self._trace(chunk, "VAD 分段完成", f"片段 {duration:.2f} 秒")
        if self.jobs.full():
            try:
                oldest = self.jobs.get_nowait()
                self._discard(oldest, f"识别队列已满（上限 {self.jobs.maxsize} 句），丢弃最旧的一句",
                              识别排队=time.monotonic() - oldest.queued_at)
            except queue.Empty:
                pass
        try:
            self.jobs.put_nowait(chunk)
            self._trace(chunk, "识别入队", f"音频 {duration:.2f} 秒，队列约 {self.jobs.qsize()} 句")
            if self.asr_busy.is_set():
                self.on_diagnostic(
                    f"已收到 {duration:.1f} 秒疑似语音；"
                    f"正在识别上一句，队列约 {self.jobs.qsize()} 句。"
                )
            else:
                self.on_diagnostic(f"已收到 {duration:.1f} 秒疑似语音，等待识别…")
        except queue.Full:
            self._discard(chunk, f"识别队列已满（上限 {self.jobs.maxsize} 句），本句未入队")

    def _trace(self, chunk, stage, detail, discarded=False, **timings):
        self.on_trace(TaskTrace(chunk.task_id, stage, detail, discarded, timings))

    def _discard(self, chunk, reason, **timings):
        age = time.monotonic() - chunk.end_time
        self._trace(chunk, "结果丢弃", f"{reason}；距音频结束 {age:.2f} 秒", True, **timings)
        self.on_diagnostic(reason)

    def _recognize(self, chunk, asr):
        t0 = time.monotonic()
        decoding = threading.Event()
        self.asr_busy.set()
        self.on_diagnostic(f"正在识别 {len(chunk.audio) / 16000:.1f} 秒音频片段…")
        self._trace(chunk, "等待识别锁", "等待识别模型")
        rejection = None

        def rejected(reason, detail):
            nonlocal rejection
            rejection = reason
            self._trace(chunk, "结果过滤", f"{reason}；{detail}")

        def overdue():
            if self.asr_busy.is_set() and not self.stop_event.is_set():
                phase = "Whisper 推理" if decoding.is_set() else "等待识别锁"
                message = f"{phase}已超过 {time.monotonic() - t0:.1f} 秒，仍在等待返回"
                self._trace(chunk, "5 秒看门狗", message)
                self.on_diagnostic(message)

        watchdog = threading.Timer(5.0, overdue)
        watchdog.daemon = True
        watchdog.start()
        try:
            with self.asr_lock:
                started = time.monotonic()
                decoding.set()
                self._trace(chunk, "推理开始", f"{asr.device.upper()}，等锁 {started - t0:.3f} 秒",
                            识别等锁=started - t0)
                asr.on_rejection = rejected
                try:
                    result = asr.transcribe(chunk.audio, self.settings.language)
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    self._trace(chunk, "推理结束", f"耗时 {elapsed:.3f} 秒，异常 {exc}", 识别=elapsed)
                    raise
                finally:
                    asr.on_rejection = None
                elapsed = time.monotonic() - started
                detail = f"耗时 {elapsed:.3f} 秒，{result.language} {result.text[:160]!r}" if result \
                    else f"耗时 {elapsed:.3f} 秒，无可用识别结果"
                self._trace(chunk, "推理结束", detail, 识别=elapsed)
                return result, rejection, elapsed
        finally:
            self.asr_busy.clear()
            watchdog.cancel()

    def _report_runtime(self, asr, translator) -> None:
        mode = "低延迟模式" if self.settings.fast_mode else "准确优先模式"
        path = getattr(asr, "path", None)
        model = f" · Whisper {path.name.removeprefix('whisper-')}" if path else ""
        self.on_runtime(f"识别 {asr.device.upper()} · 翻译 {translator.device.upper()} · {mode}{model}")

    def translate_microphone(self, audio):
        session = self.model_session
        if session is None:
            raise RuntimeError("翻译模型仍在加载，请稍后再试。")
        asr, translator = session
        with self.asr_lock:
            recognized = asr.transcribe(audio, "zh", use_hotwords=False)
        if not recognized:
            return None, ""
        with self.mt_lock:
            return recognized, translator.translate_to_english(recognized.text)

    def _work(self) -> None:
        try:
            self.on_status("加载本地识别模型…")
            asr = LocalWhisper(self.settings.whisper_model, self.settings.compute_device,
                               self.settings.fast_mode)
            self.on_status("加载本地翻译模型…")
            translator = LocalM2M(self.settings.compute_device, self.settings.fast_mode)
            self._report_runtime(asr, translator)
            glossary = game_glossary(self.settings.game)
            asr.glossary = glossary
            self.model_session = (asr, translator)
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
                now = time.monotonic()
                age = now - chunk.end_time
                waited = now - chunk.queued_at
                self._trace(chunk, "识别出队", f"排队 {waited:.2f} 秒，距音频结束 {age:.2f} 秒", 识别排队=waited)
                if age > self.settings.max_task_age:
                    self._discard(chunk, f"识别排队过期：距音频结束 {age:.2f} 秒 / 上限 {self.settings.max_task_age:.1f} 秒")
                    continue
                try:
                    recognized, rejection, elapsed = self._recognize(chunk, asr)
                    self._report_runtime(asr, translator)
                    finished = time.monotonic()
                    age = finished - chunk.end_time
                    if not recognized:
                        self._discard(chunk, f"识别 {elapsed:.2f} 秒后丢弃：{rejection or '无可靠文字'}")
                        continue
                    if age > self.settings.max_task_age:
                        self._discard(chunk, f"识别完成已过期：距音频结束 {age:.2f} 秒 / 上限 {self.settings.max_task_age:.1f} 秒")
                        continue
                    task = RecognizedTask(chunk, recognized, elapsed, finished)
                    if self.translation_jobs.full():
                        try:
                            oldest = self.translation_jobs.get_nowait()
                            self._discard(
                                oldest.chunk, f"翻译队列已满（上限 {self.translation_jobs.maxsize} 句），丢弃最旧的一句",
                                翻译排队=time.monotonic() - oldest.queued_at,
                            )
                        except queue.Empty:
                            pass
                    try:
                        self.translation_jobs.put_nowait(task)
                        self._trace(chunk, "翻译入队", f"队列约 {self.translation_jobs.qsize()} 句")
                        self.on_diagnostic(f"识别到：{recognized.text[:45]}；等待翻译…")
                    except queue.Full:
                        self._discard(chunk, f"翻译队列已满（上限 {self.translation_jobs.maxsize} 句），本句未入队")
                except Exception as exc:
                    self.failures += 1
                    LOG.exception("Speech task failed")
                    self.on_status(f"该句识别失败，继续监听：{exc}")
                    self._discard(chunk, f"识别异常：{exc}")
        except Exception as exc:
            self.failures += 1
            LOG.exception("Pipeline failed")
            self.on_trace(TaskTrace(0, "启动失败", str(exc)))
            self.on_status(f"运行失败：{exc}")
            self.stop_event.set()
        finally:
            self.model_session = None
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
            now = time.monotonic()
            age = now - chunk.end_time
            waited = now - task.queued_at
            self._trace(chunk, "翻译出队", f"排队 {waited:.2f} 秒，距音频结束 {age:.2f} 秒", 翻译排队=waited)
            if age > self.settings.max_task_age:
                self._discard(chunk, f"翻译排队过期：距音频结束 {age:.2f} 秒 / 上限 {self.settings.max_task_age:.1f} 秒")
                continue
            started = now
            try:
                normalized = glossary.normalize(recognized.text)
                duplicate_age = time.monotonic() - self.last_result_time
                if normalized == self.last_text and duplicate_age < 1.5:
                    self._discard(chunk, f"重复报点已合并（距上次 {duplicate_age:.2f} 秒 / 窗口 1.5 秒）")
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
                    with self.mt_lock:
                        translated = translator.translate(recognized.text, recognized.language)
                self._report_runtime(asr, translator)
                finished = time.monotonic()
                translation_seconds = finished - started
                age = finished - chunk.end_time
                self._trace(chunk, "翻译结束", f"翻译 {translation_seconds:.2f} 秒，距音频结束 {age:.2f} 秒",
                            翻译=translation_seconds)
                if translation_seconds > self.settings.translation_timeout or age > self.settings.max_task_age:
                    self.failures += 1
                    self._discard(chunk, f"字幕已过期：翻译 {translation_seconds:.2f} 秒 / 上限 "
                                  f"{self.settings.translation_timeout:.1f} 秒，距音频结束 {age:.2f} 秒 / 上限 "
                                  f"{self.settings.max_task_age:.1f} 秒")
                    continue
                translated = glossary.postprocess(translated, recognized.text)
                if not translated:
                    self._discard(chunk, f"翻译结果为空（翻译 {translation_seconds:.2f} 秒）")
                    continue
                if not re.search(r"[\u4e00-\u9fff]", translated):
                    self.failures += 1
                    self._discard(chunk, f"翻译未生成中文（翻译 {translation_seconds:.2f} 秒），已过滤")
                    continue
                if self.stop_event.is_set():
                    continue
                result = SubtitleResult(recognized.text, translated, recognized.language,
                                        task.asr_seconds, translation_seconds,
                                        finished - chunk.end_time,
                                        recognized.probability if self.settings.language == "auto" else None,
                                        task_id=chunk.task_id, audio_end_time=chunk.end_time)
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
                self._discard(chunk, f"翻译异常：{exc}", 翻译=time.monotonic() - started)
