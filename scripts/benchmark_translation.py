"""Compare single-utterance translation without calling a network service."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import platform
import shutil
import statistics
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("m2m100_418M", "m2m100_1.2B", "nllb-200-distilled-600M")
NLLB_LANGUAGES = {"en": "eng_Latn", "zh": "zho_Hans"}


def load_candidate(name, model_root, device):
    import torch
    from opencc import OpenCC
    from transformers import AutoModelForSeq2SeqLM, M2M100Tokenizer, NllbTokenizerFast

    folder = model_root / name
    nllb = name == "nllb-200-distilled-600M"
    tokenizer_class = NllbTokenizerFast if nllb else M2M100Tokenizer
    tokenizer = tokenizer_class.from_pretrained(folder, local_files_only=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(
        folder, local_files_only=True,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    ).to(device).eval()
    converter = OpenCC("t2s")

    def translate(text, source, target):
        if nllb:
            tokenizer.src_lang = NLLB_LANGUAGES[source]
            target_id = tokenizer.convert_tokens_to_ids(NLLB_LANGUAGES[target])
        else:
            tokenizer.src_lang = source
            target_id = tokenizer.get_lang_id(target)
        batch = tokenizer(text, return_tensors="pt", truncation=True, max_length=128).to(device)
        generation = {"num_beams": 1}
        if target == "en":
            generation.update(num_beams=3, no_repeat_ngram_size=3)
        with torch.inference_mode():
            output = model.generate(
                **batch, forced_bos_token_id=target_id, max_new_tokens=48, **generation
            )
        translated = tokenizer.batch_decode(output, skip_special_tokens=True)[0].strip()
        return converter.convert(translated) if target == "zh" else translated

    return translate


def load_baseline(model_root, device):
    from valorant_translator.translation.m2m import LocalM2M

    model = LocalM2M(device, fast_mode=True, model_dir=model_root)

    def translate(text, source, target):
        translated = model.translate(text, source, target)
        if model.device != device:
            raise RuntimeError(f"Requested {device}, but baseline ran on {model.device}")
        return translated

    return model, translate


def load_ptt(model, device):
    from valorant_translator.config import Settings
    from valorant_translator.translation.enhancement import GameEnhancer
    from valorant_translator.translation.glossary import Glossary

    settings = Settings()
    with TemporaryDirectory() as directory:
        bundled = Path(directory) / "glossary.json"
        shutil.copyfile(ROOT / "config/glossary.json", bundled)
        glossary = Glossary(bundled)
    enhancer = GameEnhancer(settings.game, glossary)

    def translate(text, source, target):
        match = enhancer.match(text, source, target, settings, filter_speech=False)
        if match.translation:
            return match.translation
        english, *_ = model.translate_to_english_details(glossary.prepare_zh_en(match.text))
        if model.device != device:
            raise RuntimeError(f"Requested {device}, but baseline ran on {model.device}")
        return glossary.finish_zh_en(english, match.text)

    return translate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=MODELS)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--device", required=True, choices=("cpu", "cuda"))
    parser.add_argument("--cases", type=Path, default=ROOT / "benchmarks/valorant_translation.tsv")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--baseline-ptt", action="store_true",
                        help="Run the baseline's Valorant PTT text path on Chinese-to-English cases")
    args = parser.parse_args()
    if min(args.threads, args.repeats) < 1:
        parser.error("threads and repeats must be positive")
    if args.baseline_ptt and args.model != MODELS[0]:
        parser.error("--baseline-ptt is only available for m2m100_418M")

    import torch
    import transformers

    if args.device == "cuda" and not torch.cuda.is_available():
        parser.error("CUDA is unavailable")
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    with args.cases.open(encoding="utf-8", newline="") as handle:
        cases = list(csv.DictReader(handle, delimiter="\t"))
    if args.baseline_ptt:
        cases = [case for case in cases if case["direction"] == "zh-en"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        parser.error("cases must be nonempty with unique IDs")

    started = perf_counter()
    if args.model == MODELS[0]:
        model, translate = load_baseline(args.model_root, args.device)
        if args.baseline_ptt:
            translate = load_ptt(model, args.device)
    else:
        translate = load_candidate(args.model, args.model_root, args.device)
    load_ms = round((perf_counter() - started) * 1000, 2)
    if not args.baseline_ptt:
        translate("Don't peek yet.", "en", "zh")
    translate("我在换弹，等我一下。", "zh", "en")

    rows = []
    for case in cases:
        source, target = case["direction"].split("-")
        samples = []
        for _ in range(args.repeats):
            if args.device == "cuda":
                torch.cuda.synchronize()
            started = perf_counter()
            translated = translate(case["source"], source, target)
            if args.device == "cuda":
                torch.cuda.synchronize()
            samples.append(round((perf_counter() - started) * 1000, 2))
        rows.append({
            **case, "translation": translated, "samples_ms": samples,
            "median_ms": statistics.median(samples),
        })

    result = {
        "model": args.model, "device": args.device, "baseline_ptt": args.baseline_ptt,
        "platform": platform.platform(),
        "processor": torch.cuda.get_device_name() if args.device == "cuda" else platform.processor(),
        "torch": torch.__version__, "transformers": transformers.__version__,
        "threads": args.threads, "repeats": args.repeats, "load_ms": load_ms,
        "cases": rows,
    }
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"{args.model} {args.device}: {len(rows)} cases, "
          f"median {statistics.median(row['median_ms'] for row in rows):.2f} ms")


if __name__ == "__main__":
    main()
