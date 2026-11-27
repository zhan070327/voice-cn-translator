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
from valorant_translator.diagnostics import Diagnostics
from valorant_translator.languages import DEFAULT_LANGUAGES, LanguageCatalog
from valorant_translator.models.cloud import CloudClient, ready_engine_settings
from valorant_translator.models.providers import (
    ModelManager, create_local_model, default_model_manager, translate_pair,
)
from valorant_translator.recognition.whisper import LocalWhisper, Recognition
from valorant_translator.translation.enhancement import GameEnhancer
from valorant_translator.translation.glossary import Glossary, game_glossary
from valorant_translator.translation.m2m import LocalM2M
from valorant_translator.ui.texts import tr

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
    corrected_original: str = ""
    machine_translation: str | None = None
    game: str = "valorant"
    target_language: str = "zh"
    glossary_rules: str = ""
    display_original: str = ""


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
                 history=None, *, on_trace: Callable[[TaskTrace], None] | None = None,
                 custom_rules=None, languages: LanguageCatalog | None = None,
                 models: ModelManager | None = None, games=None, cloud_client: CloudClient | None = None,
                 on_cloud_blocked: Callable[[str], None] | None = None,
                 on_effective_mode: Callable[[str], None] | None = None,
                 on_activity: Callable[[int, str, str], None] | None = None):
        self.settings = settings
        self.requested_speed_mode = settings.speed_mode
        self.cloud_client = cloud_client or CloudClient(settings)
        self.on_cloud_blocked = on_cloud_blocked or (lambda _: None)
        self.on_effective_mode = on_effective_mode or (lambda _: None)
        self._fallback_announced = False
        self._fallback_lock = threading.Lock()
        self.languages = languages or DEFAULT_LANGUAGES
        self.models = models
        self.games = games
        self.asr_lock = threading.Lock()
        self.mt_lock = threading.Lock()
        self.model_session = None
        self.on_status = on_status
        self.on_level = on_level
        self.on_result = on_result
        self.on_diagnostic = on_diagnostic or (lambda _: None)
        self.on_runtime = on_runtime or (lambda _: None)
        self.on_trace = on_trace or (lambda _: None)
        self._activity_callback = on_activity
        self.task_ids = count(1)
        self.history = history
        self.custom_rules = custom_rules
        self.diagnostics = Diagnostics()
        self.chinese_converter = OpenCC("t2s")
        self.jobs: queue.Queue[SpeechChunk] = queue.Queue(maxsize=4)
        self.translation_jobs: queue.Queue[RecognizedTask] = queue.Queue(maxsize=4)
        self.stop_event = threading.Event()
        self.asr_busy = threading.Event()
        self.capture: AudioCapture | None = None
        self.worker: threading.Thread | None = None
        self.translation_worker: threading.Thread | None = None
        self.segmenter = SpeechSegmenter(self.enqueue, settings.vad_level, self._segment_event)
        self.last_text = ""
        self.last_result_time = 0.0
        self.failures = 0
        self.asr_seconds_per_audio: float | None = None

    def _age_limit(self, asr) -> float:
        # Keep a slow first inference visible, then adapt to measured throughput.
        model = getattr(getattr(asr, "path", None), "name", "")
        baseline = 9.0 if model.endswith("tiny") else 18.0
        measured = (self.asr_seconds_per_audio or 0) * SpeechSegmenter.MAX_FRAMES * .02
        return max(self.settings.max_task_age, min(24.0, max(baseline, measured + 4.0)))

    def _expected_asr_seconds(self, asr, audio) -> float:
        # The online adapter can recover on the next utterance. A slow local
        # attempt should not prevent that utterance from reaching the provider.
        if getattr(asr, "on_fallback", None) is not None:
            return 0.0
        return (self.asr_seconds_per_audio or 0) * len(audio) / 16000

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._work, name="model-worker", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.cloud_client.disable_online()
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
        self.diagnostics.add("语音片段")
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
            self.diagnostics.depth("识别队列", self.jobs.qsize())
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

    def _segment_event(self, message):
        if message.startswith("检测到的语音过短"):
            self.diagnostics.add("语音片段")
            self.diagnostics.add("discard", "VAD 语音过短")
        self.on_diagnostic(message)

    def _discard(self, chunk, reason, *, category=None, **timings):
        age = time.monotonic() - chunk.end_time
        if category is None:
            category = reason.split("（", 1)[0].split("：", 1)[0]
        hits = self.diagnostics.add("discard", category)
        self._trace(chunk, "结果丢弃", f"{reason}；距音频结束 {age:.2f} 秒；本类 {hits} 次", True, **timings)
        self.on_diagnostic(reason)

    def pending_count(self) -> int:
        return self.jobs.qsize() + self.translation_jobs.qsize()

    def _report_activity(self, task_id: int, phase: str, text: str = "") -> None:
        if self._activity_callback is not None:
            try:
                self._activity_callback(task_id, phase, text)
            except Exception:
                LOG.exception("Could not report pipeline activity")

    def _recognize(self, chunk, asr):
        t0 = time.monotonic()
        decoding = threading.Event()
        self.asr_busy.set()
        self._report_activity(chunk.task_id, "recognizing")
        self.on_diagnostic(f"正在识别 {len(chunk.audio) / 16000:.1f} 秒音频片段…")
        self._trace(chunk, "等待识别锁", "等待识别模型")
        rejection = None

        def rejected(reason, detail):
            nonlocal rejection
            rejection = reason
            self._trace(chunk, "结果过滤", f"{reason}；{detail}")

        def overdue():
            if self.asr_busy.is_set() and not self.stop_event.is_set():
                phase = tr("engine.asr_inference") if decoding.is_set() else "等待识别锁"
                message = f"{phase}已超过 {time.monotonic() - t0:.1f} 秒，仍在等待返回"
                self._trace(chunk, "5 秒看门狗", message)
                self.on_diagnostic(message)

        watchdog = threading.Timer(5.0, overdue)
        watchdog.daemon = True
        watchdog.start()
        try:
            with self.asr_lock:
                started = time.monotonic()
                limit = self._age_limit(asr)
                age = started - chunk.end_time
                expected = self._expected_asr_seconds(asr, chunk.audio)
                if age >= limit or (expected and age + expected >= limit):
                    self._discard(chunk, f"等待识别锁后已过期：距音频结束 {age:.2f} 秒 / 上限 {limit:.1f} 秒")
                    return None, "已过期", 0.0
                decoding.set()
                self._trace(chunk, "推理开始", f"{asr.device.upper()}，等锁 {started - t0:.3f} 秒",
                            识别等锁=started - t0)
                asr.on_rejection = rejected
                report_partial = self._activity_callback is not None and hasattr(asr, "on_partial")
                if report_partial:
                    asr.on_partial = lambda text: self._report_activity(chunk.task_id, "recognizing", text)
                try:
                    result = asr.transcribe(chunk.audio, self.languages.source(self.settings.source_language))
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    self._trace(chunk, "推理结束", f"耗时 {elapsed:.3f} 秒，异常 {exc}", 识别=elapsed)
                    raise
                finally:
                    asr.on_rejection = None
                    if report_partial:
                        asr.on_partial = None
                elapsed = time.monotonic() - started
                ratio = elapsed / max(0.1, len(chunk.audio) / 16000)
                reset_estimate = self.asr_seconds_per_audio is None or getattr(asr, "fallback_seconds", 0)
                self.asr_seconds_per_audio = (ratio if reset_estimate else
                                              .5 * self.asr_seconds_per_audio + .5 * ratio)
                detail = f"耗时 {elapsed:.3f} 秒，{result.language} {result.text[:160]!r}" if result \
                    else f"耗时 {elapsed:.3f} 秒，无可用识别结果"
                self._trace(chunk, "推理结束", detail, 识别=elapsed)
                return result, rejection, elapsed
        finally:
            self.asr_busy.clear()
            watchdog.cancel()
            self._report_activity(chunk.task_id, "finished")

    @staticmethod
    def _device_label(provider) -> str:
        if provider.device in ("online", "pending"):
            return tr(f"engine.route_{provider.device}")
        return provider.device.upper()

    def _report_runtime(self, asr, translator) -> None:
        mode = self.settings.speed_mode
        if self.requested_speed_mode == "extreme" and (
                mode != "extreme" or any(p.device not in ("online", "pending") for p in (asr, translator))):
            mode = "local_fallback"
        self.on_effective_mode(mode)
        mode = "本地回退" if mode == "local_fallback" else tr(f"engine.mode_{mode}")
        path = getattr(asr, "path", None)
        model = f" · Whisper {path.name.removeprefix('whisper-')}" if path else ""
        self.on_runtime(f"识别 {self._device_label(asr)} · 翻译 {self._device_label(translator)} · {mode}{model}")

    def translate_microphone(self, audio, on_rejection=None):
        recognized, english, _ = self.translate_microphone_details(audio, on_rejection)
        return recognized, english

    def translate_microphone_details(self, audio, on_rejection=None):
        session = self.model_session
        if session is None:
            raise RuntimeError("翻译模型仍在加载，请稍后再试。")
        asr, translator = session
        started = time.monotonic()
        with self.asr_lock:
            previous = getattr(asr, "on_rejection", None)
            asr.on_rejection = on_rejection
            try:
                recognized = asr.transcribe_chinese_callout(audio)
            finally:
                asr.on_rejection = previous
        if not recognized:
            self._report_runtime(asr, translator)
            return None, "", {}
        translated_at = time.monotonic()
        enhancer = getattr(self, "enhancer", None)
        match = enhancer.match(recognized.text, "zh", "en", self.settings, filter_speech=False) if enhancer else None
        corrected = match.text if match else recognized.text
        glossary = enhancer.glossary if enhancer else game_glossary(self.settings.game)
        if match and match.translation:
            english, raw, rule = match.translation, None, match.rules
        else:
            with self.mt_lock:
                details = getattr(translator, "translate_to_english_details", None)
                if details:
                    english, raw, rule = details(glossary.prepare_zh_en(corrected))
                else:
                    english, raw, rule = translator.translate_to_english(glossary.prepare_zh_en(corrected)), None, ""
            english = glossary.finish_zh_en(english, corrected)
            if match and match.rules:
                rule = " · ".join(filter(None, (match.rules, rule)))
        self._report_runtime(asr, translator)
        return recognized, english, dict(
            asr_seconds=translated_at - started, translation_seconds=time.monotonic() - translated_at,
            corrected_original=corrected if corrected != recognized.text else "",
            machine_translation=raw, glossary_rules=rule, confidence=recognized.probability,
        )

    def _notify_fallback(self, stage: str) -> None:
        with self._fallback_lock:
            if self._fallback_announced:
                return
            self._fallback_announced = True
        self.on_status(tr(f"engine.fallback_{stage}"))

    def _work(self) -> None:
        try:
            self.languages.source(self.settings.source_language)
            self.languages.target(self.settings.target_language)
            self.settings = ready_engine_settings(
                self.settings, self.cloud_client, self.on_cloud_blocked, self.on_status)
            if self.stop_event.is_set():
                return
            models = self.models or default_model_manager(
                lambda s: create_local_model(LocalWhisper, s, s.whisper_model, s.compute_device, s.fast_mode),
                lambda s: create_local_model(LocalM2M, s, s.compute_device, s.fast_mode),
                cloud_client=self.cloud_client)
            self.on_status(tr("engine.loading_asr"))
            asr = models.create_asr(self.settings)
            self.on_status(tr("engine.loading_translation"))
            translator = models.create_translation(self.settings)
            for provider in (asr, translator):
                if hasattr(provider, "on_fallback"):
                    provider.on_fallback = self._notify_fallback
            self._report_runtime(asr, translator)
            if self.games is None:
                glossary = game_glossary(self.settings.game)
            else:
                glossary = Glossary(self.games.path(self.settings.game))
            asr.glossary = glossary
            asr.languages = self.languages
            translator.languages = self.languages
            self.enhancer = enhancer = GameEnhancer(self.settings.game, glossary, self.custom_rules)
            self.model_session = (asr, translator)
            if self.stop_event.is_set():
                return
            self.translation_worker = threading.Thread(
                target=self._translate_loop, args=(translator, glossary, asr, enhancer),
                name="translation-worker", daemon=True)
            self.translation_worker.start()
            self.capture = AudioCapture(self.settings.device_id, self.segmenter.feed,
                                        self.on_level, self.on_status)
            self.capture.start()
            self.on_status(tr("engine.running", asr=self._device_label(asr), translation=self._device_label(translator)))
            self.on_diagnostic("等待语音；音量条跳动不代表已检测到说话")
            while not self.stop_event.is_set():
                try:
                    chunk = self.jobs.get(timeout=0.2)
                    self.diagnostics.depth("识别队列", self.jobs.qsize())
                except queue.Empty:
                    continue
                now = time.monotonic()
                age = now - chunk.end_time
                waited = now - chunk.queued_at
                self._trace(chunk, "识别出队", f"排队 {waited:.2f} 秒，距音频结束 {age:.2f} 秒", 识别排队=waited)
                limit = self._age_limit(asr)
                expected = self._expected_asr_seconds(asr, chunk.audio)
                if age >= limit or (expected and age + expected >= limit):
                    self._discard(chunk, f"识别排队过期：距音频结束 {age:.2f} 秒 / 上限 {limit:.1f} 秒")
                    continue
                try:
                    recognized, rejection, elapsed = self._recognize(chunk, asr)
                    self._report_runtime(asr, translator)
                    finished = time.monotonic()
                    age = finished - chunk.end_time
                    if not recognized:
                        if rejection == "已过期":
                            continue
                        reason = rejection or "无可靠文字"
                        category = "语种置信度不足" if reason.startswith("语种置信度") else reason.split("（", 1)[0]
                        category = "不支持的语种" if category.startswith("不支持的语种") else category
                        if category.startswith(("中文仅", "文本过短")):
                            category = "识别文本过短"
                        self._discard(chunk, f"识别 {elapsed:.2f} 秒后丢弃：{reason}", category=category)
                        continue
                    limit = self._age_limit(asr)
                    if age > limit:
                        self._discard(chunk, f"识别完成已过期：距音频结束 {age:.2f} 秒 / 上限 {limit:.1f} 秒")
                        continue
                    if enhancer.suppresses(recognized.text, recognized.language, self.settings):
                        self._discard(chunk, f"已抑制英雄台词：{recognized.text[:160]}",
                                      category="已抑制英雄台词")
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
                        self.diagnostics.depth("翻译队列", self.translation_jobs.qsize())
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
                        asr: LocalWhisper, enhancer: GameEnhancer | None = None) -> None:
        enhancer = enhancer or GameEnhancer(self.settings.game, glossary, self.custom_rules)
        while not self.stop_event.is_set():
            try:
                task = self.translation_jobs.get(timeout=0.2)
                self.diagnostics.depth("翻译队列", self.translation_jobs.qsize())
            except queue.Empty:
                continue
            chunk, recognized = task.chunk, task.recognized
            now = time.monotonic()
            age = now - chunk.end_time
            waited = now - task.queued_at
            self._trace(chunk, "翻译出队", f"排队 {waited:.2f} 秒，距音频结束 {age:.2f} 秒", 翻译排队=waited)
            limit = self._age_limit(asr)
            if age > limit:
                self._discard(chunk, f"翻译排队过期：距音频结束 {age:.2f} 秒 / 上限 {limit:.1f} 秒")
                continue
            started = now
            self._report_activity(chunk.task_id, "translating", recognized.text)
            try:
                normalized = glossary.normalize(recognized.text)
                duplicate_age = time.monotonic() - self.last_result_time
                if normalized == self.last_text and duplicate_age < 1.5:
                    self._discard(chunk, f"重复报点已合并（距上次 {duplicate_age:.2f} 秒 / 窗口 1.5 秒）")
                    continue
                started = time.monotonic()
                target = self.settings.target_language
                matched = enhancer.match(recognized.text, recognized.language, target, self.settings,
                                         filter_speech=False)
                translated, used = matched.translation, matched.rules
                raw_translation = None
                display_original = ""
                fallback_seconds = 0.0
                if self.history is not None and self.settings.game == "valorant" and target == "zh":
                    try:
                        legacy = self.history.lookup_correction(normalized, recognized.language)
                        if legacy:
                            translated, used = legacy, "legacy_user_correction"
                    except Exception:
                        LOG.exception("Could not read translation correction")
                if translated is None:
                    if recognized.language == target:
                        translated = self.chinese_converter.convert(matched.text) if target == "zh" else matched.text
                    else:
                        lock_start = time.monotonic()
                        with self.mt_lock:
                            lock_wait = time.monotonic() - lock_start
                            source_text = (glossary.prepare_zh_en(matched.text)
                                           if recognized.language == "zh" and target == "en" else matched.text)
                            raw_translation = translate_pair(translator, source_text, recognized.language, target)
                            fallback_seconds = getattr(translator, "fallback_seconds", 0.0)
                        self._trace(chunk, "翻译推理", f"等锁 {lock_wait:.3f} 秒", 翻译等锁=lock_wait)
                        translated = raw_translation
                        if translated and recognized.language == "zh" and target == "en":
                            translated = glossary.finish_zh_en(translated, matched.text)
                        if translated and self.settings.game_terms and recognized.language == "en" and target == "zh":
                            try:
                                translated = glossary.postprocess(translated, matched.text)
                                if translated != raw_translation:
                                    used = " · ".join(filter(None, (used, "term_postprocess")))
                            except (TypeError, ValueError, KeyError, AttributeError):
                                LOG.exception("Glossary postprocessing failed; retaining machine translation")
                if recognized.language == "zh" and target == "zh":
                    source_text = glossary.prepare_zh_en(matched.text)
                    with self.mt_lock:
                        details = getattr(translator, "translate_to_english_details", None)
                        if details:
                            english, _, english_rule = details(source_text)
                        else:
                            english = translate_pair(translator, source_text, "zh", "en")
                            english_rule = ""
                        fallback_seconds = getattr(translator, "fallback_seconds", 0.0)
                    display_original = glossary.finish_zh_en(english, matched.text) if english else ""
                    if english_rule:
                        used = " · ".join(filter(None, (used, english_rule)))
                self._report_runtime(asr, translator)
                finished = time.monotonic()
                translation_seconds = finished - started
                age = finished - chunk.end_time
                self._trace(chunk, "翻译结束", f"翻译 {translation_seconds:.2f} 秒，距音频结束 {age:.2f} 秒",
                            翻译=translation_seconds)
                translation_limit = max(self.settings.translation_timeout, 8.0) if display_original else self.settings.translation_timeout
                translation_limit += fallback_seconds
                limit += fallback_seconds
                if translation_seconds > translation_limit or age > limit:
                    self.failures += 1
                    self._discard(chunk, f"字幕已过期：翻译 {translation_seconds:.2f} 秒 / 上限 "
                                  f"{translation_limit:.1f} 秒，距音频结束 {age:.2f} 秒 / 上限 "
                                  f"{limit:.1f} 秒")
                    continue
                if not translated:
                    self._discard(chunk, f"翻译结果为空（翻译 {translation_seconds:.2f} 秒）")
                    continue
                if recognized.language == "zh" and target == "zh" and not display_original:
                    self._discard(chunk, "中文语音未生成英文字幕")
                    continue
                if target == "zh" and not re.search(r"[\u4e00-\u9fff]", translated):
                    self.failures += 1
                    self._discard(chunk, f"翻译未生成中文（翻译 {translation_seconds:.2f} 秒），已过滤")
                    continue
                if self.stop_event.is_set():
                    continue
                result = SubtitleResult(recognized.text, translated, recognized.language,
                                        task.asr_seconds, translation_seconds,
                                        finished - chunk.end_time,
                                        recognized.probability if self.settings.language == "auto" else None,
                                        task_id=chunk.task_id, audio_end_time=chunk.end_time,
                                        corrected_original=matched.text if matched.text != recognized.text else "",
                                        machine_translation=raw_translation, game=self.settings.game,
                                        glossary_rules=used, target_language=target,
                                        display_original=display_original)
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
            finally:
                self._report_activity(chunk.task_id, "finished")
