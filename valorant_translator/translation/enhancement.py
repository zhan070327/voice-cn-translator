"""Resolve exact, scoped rules before model translation."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from valorant_translator.translation.glossary import Glossary
from valorant_translator.translation.rules import Rule, RuleStore, parse_rules

LOG = logging.getLogger(__name__)
_SENTENCES = re.compile(r"((?<=[.!?])\s+|(?<=[。！？])\s*|[;；]\s*)")
_COMMAS = re.compile(r"([,，]\s*)")


@dataclass(frozen=True)
class Match:
    text: str
    translation: str | None = None
    rules: str = ""
    suppressed: bool = False


class GameEnhancer:
    def __init__(self, game: str, glossary: Glossary, custom: RuleStore | None = None):
        self.game, self.glossary, self.custom = game, glossary, custom
        self.default: dict[tuple, Rule] = {}
        try:
            if glossary.data.get("version", 1) == 1:
                for rule in parse_rules(glossary.data):
                    previous = self.default.get(rule.key)
                    if rule.enabled and (previous is None or rule.rank >= previous.rank):
                        self.default[rule.key] = rule
        except ValueError:
            LOG.warning("Invalid built-in enhancement rules for %s", game, exc_info=True)
        self.suppressed = set()
        for entry in glossary.data.get("suppressed_lines", []):
            if (isinstance(entry, dict) and entry.get("verified") is True
                    and entry.get("game") == game and entry.get("origin") and entry.get("character")
                    and isinstance(entry.get("original"), str) and isinstance(entry.get("source"), str)):
                self.suppressed.add((entry["source"], glossary.normalize(entry["original"])))

    def _rule(self, normalized, source, target, kind):
        custom = self.custom.index if self.custom else {}
        key = self.game, source, target, kind, normalized
        def choose(lookup_key):
            return max((rule for rule in (self.default.get(lookup_key), custom.get(lookup_key)) if rule and (kind != "official_line" or rule.verified)),
                       key=lambda rule: rule.rank, default=None)
        # A general rule only fills a gap in the selected game's vocabulary.
        return choose(key) or (choose(("general", *key[1:])) if kind == "generic" else None)

    def suppresses(self, text, source, settings) -> bool:
        return settings.teammate_only and (source, self.glossary.normalize(text)) in self.suppressed

    def _correct(self, text, source, target):
        rule = self._rule(self.glossary.normalize(text), source, target, "asr_correction")
        # Full normalized equality gives match confidence 1. No fuzzy substitutions.
        return rule

    def _correct_clauses(self, parts, source, target):
        corrected, evidence = [], []
        for index, clause in enumerate(parts):
            if index % 2 or not self.glossary.normalize(clause):
                corrected.append(clause)
                continue
            rule = self._correct(clause, source, target)
            if rule:
                evidence.append(f"asr_correction:{self.glossary.normalize(clause)}")
                next_separator = parts[index + 1] if index + 1 < len(parts) else ""
                replacement = rule.corrected
                if next_separator.startswith((",", "，", ";", "；")):
                    replacement = replacement.rstrip(".!?。！？")
                elif (ending := re.search(r"[.!?。！？]+$", clause)) and replacement[-1] not in ".!?。！？":
                    replacement += ending[0]
                corrected.append(replacement)
            else:
                corrected.append(clause)
        return corrected, evidence

    def _translate(self, text, source, target, settings):
        normalized = self.glossary.normalize(text)
        if self.custom and settings.game_terms:
            custom = self.custom.index.get((self.game, source, target, "term", normalized))
            if custom:
                return custom.translated, f"custom_term:{normalized}"
        if settings.official_lines:
            official = self._rule(normalized, source, target, "official_line")
            if official and official.verified:
                return official.translated, f"official_line:{normalized}"
        if settings.game_terms:
            for kind in ("term", "generic"):
                rule = self._rule(normalized, source, target, kind)
                if rule:
                    return rule.translated, f"{kind}:{normalized}"
            if (source, target) == ("en", "zh"):
                translated = self.glossary.callout(text)
                if translated:
                    return translated, f"legacy:{normalized}"
        return None, ""

    def match(self, original: str, source: str, target: str, settings, *, filter_speech: bool = True) -> Match:
        try:
            normalized = self.glossary.normalize(original)
            if filter_speech and self.suppresses(original, source, settings):
                return Match(original, rules=f"suppressed:{normalized}", suppressed=True)
            rule = self._correct(original, source, target) if settings.asr_correction else None
            text = rule.corrected if rule else original
            correction = f"asr_correction:{normalized}" if rule else ""
            translated, used = self._translate(text, source, target, settings)
            if translated:
                return Match(text, translated, " · ".join(filter(None, (correction, used))))
            parts = _SENTENCES.split(text)
            rules = [correction] if correction else []
            if settings.asr_correction and not rule:
                parts, evidence = self._correct_clauses(parts, source, target)
                rules.extend(evidence)
                for index in range(0, len(parts), 2):
                    comma_parts = _COMMAS.split(parts[index])
                    if len(comma_parts) == 1:
                        continue
                    comma_parts, evidence = self._correct_clauses(comma_parts, source, target)
                    if evidence:
                        parts[index] = "".join(comma_parts)
                        rules.extend(evidence)
                if rules:
                    text = "".join(parts)
                    translated, used = self._translate(text, source, target, settings)
                    if translated:
                        return Match(text, translated, " · ".join((*rules, used)))
            clauses = [part.strip() for part in parts[::2] if self.glossary.normalize(part)]
            if len(clauses) < 2:
                return Match(text, rules=" · ".join(rules))
            outputs, terms = [], []
            for clause in clauses:
                translated, used = self._translate(clause, source, target, settings)
                outputs.append(translated)
                if used:
                    terms.append(used)
            if all(outputs):
                return Match(text, "".join(outputs), " · ".join((*rules, *terms)))
            return Match(text, rules=" · ".join(rules))
        except (TypeError, ValueError, KeyError, AttributeError):
            LOG.exception("Glossary matching failed for %s %s→%s", self.game, source, target)
            return Match(original)
