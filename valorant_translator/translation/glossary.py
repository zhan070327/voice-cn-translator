from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from valorant_translator import config
from valorant_translator.config import APP_DIR
from valorant_translator.translation.game_catalog import GameCatalog

LOG = logging.getLogger(__name__)

GLOSSARY_FILE = Path(__file__).resolve().parents[2] / "config" / "glossary.json"
CUSTOM_GLOSSARY_FILE = APP_DIR / "glossary_custom.json"
GAMES = GameCatalog().names()


def default_glossary_path(game: str) -> Path:
    return GLOSSARY_FILE if game == "valorant" else GameCatalog(GLOSSARY_FILE.parent).path(game)


def read_default_glossary(game: str) -> dict:
    """Read the installed catalog without personal edits or a cached fallback."""
    return _valid_data(json.loads(default_glossary_path(game).read_text(encoding="utf-8-sig")))


def game_glossary(game: str) -> Glossary:
    selected = game if game in GameCatalog(GLOSSARY_FILE.parent).paths() else "valorant"
    return Glossary(default_glossary_path(selected), backup=config.APP_DIR / f"glossary-default-{selected}.last-good.json")


def _valid_data(data):
    if not isinstance(data, dict) or type(data.get("version", 1)) is not int or data.get("version", 1) != 1:
        raise ValueError("Unsupported glossary format")
    for name in ("exact_phrases", "target_replacements"):
        if any(not isinstance(value, str) for value in data.get(name, {}).values()):
            raise ValueError(f"Invalid {name}")
    for name in ("location_aliases", "entity_aliases", "translated_name_aliases"):
        if any(not isinstance(values, list) or not all(isinstance(value, str) for value in values)
               for values in data.get(name, {}).values()):
            raise ValueError(f"Invalid {name}")
    for name in ("recognition_hotwords", "weapon_names", "agent_names", "uncertain_recognition_fragments"):
        values = data.get(name, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"Invalid {name}")
    for rule in data.get("contextual_rewrites", []):
        if not (isinstance(rule["source"], str) and isinstance(rule["natural"], str)
                and isinstance(rule["literal"], list) and all(isinstance(v, str) for v in rule["literal"])):
            raise ValueError("Invalid contextual rewrite")
    for name in ("rules", "suppressed_lines"):
        if not isinstance(data.get(name, []), list):
            raise ValueError(f"Invalid {name}")
    return data


def _load_default(path, backup):
    for candidate in (path, backup):
        if candidate is None:
            continue
        try:
            payload = candidate.read_text(encoding="utf-8-sig")
            data = _valid_data(json.loads(payload))
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            LOG.warning("Glossary could not be loaded from %s", candidate, exc_info=True)
            continue
        if candidate == path and backup is not None:
            try:
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.is_file() or backup.read_text(encoding="utf-8", errors="replace") != payload:
                    tmp = backup.with_suffix(".tmp")
                    tmp.write_text(payload, encoding="utf-8")
                    tmp.replace(backup)
            except OSError:
                LOG.warning("Unable to cache default glossary", exc_info=True)
        return data
    return {}


class Glossary:
    def __init__(self, path: Path = GLOSSARY_FILE, *, backup: Path | None = None):
        self.data = _load_default(path, backup)
        if path == GLOSSARY_FILE and CUSTOM_GLOSSARY_FILE.is_file():
            try:
                custom = json.loads(CUSTOM_GLOSSARY_FILE.read_text(encoding="utf-8"))
                if isinstance(custom, dict):
                    legacy_file = GLOSSARY_FILE.with_name("glossary_legacy_b4.json")
                    legacy = json.loads(legacy_file.read_text(encoding="utf-8")) if legacy_file.is_file() else {}
                    # The b4 installer sometimes saved a full copy of the old built-in dictionary.
                    # Keep actual edits from those copies without masking new b5 defaults.
                    full_copy = bool(legacy and legacy.keys() <= custom.keys())
                    merged = json.loads(json.dumps(self.data))
                    for key, value in custom.items():
                        if full_copy:
                            baseline = legacy.get(key)
                            if isinstance(value, dict) and isinstance(baseline, dict):
                                value = {k: v for k, v in value.items() if baseline.get(k) != v}
                            elif isinstance(value, list) and isinstance(baseline, list):
                                value = [item for item in value if item not in baseline]
                        if key in {"rules", "suppressed_lines", "version"} or key not in merged:
                            continue
                        if isinstance(value, dict) and isinstance(merged[key], dict):
                            merged[key].update(value)
                        elif isinstance(value, list) and isinstance(merged[key], list):
                            merged[key].extend(item for item in value if item not in merged[key])
                    self.data = _valid_data(merged)
            except (OSError, ValueError, TypeError, AttributeError, KeyError):
                LOG.warning("Invalid legacy custom glossary", exc_info=True)
        terms = [*self.data.get("recognition_hotwords", []),
                 *self.data.get("weapon_names", []), *self.data.get("agent_names", [])]
        self.hotwords = ", ".join(dict.fromkeys(terms))
        self.locations = {
            self.normalize(alias): place
            for place, aliases in self.data.get("location_aliases", {}).items()
            for alias in aliases
        }
        self.weapons = {self.normalize(name): name for name in self.data.get("weapon_names", [])}
        self.agents = {self.normalize(name): name for name in self.data.get("agent_names", [])}
        for canonical, aliases in self.data.get("entity_aliases", {}).items():
            target = self.weapons if canonical in self.weapons.values() else self.agents
            for alias in aliases:
                target[self.normalize(alias)] = canonical
        self.uncertain_fragments = {
            self.normalize(item) for item in self.data.get("uncertain_recognition_fragments", [])
        }

        words = {key: value for key, value in {**self.agents, **self.weapons}.items() if key}
        self._entity_names = words
        self._entity_pattern = self._pattern(words)
        targets = self.data.get("target_replacements", {})
        self._targets = {key.casefold(): value for key, value in targets.items() if key != "看你的眼睛"}
        self._target_pattern = self._pattern(self._targets)

    @staticmethod
    def _pattern(keys):
        if not keys:
            return None
        alternatives = "|".join(map(re.escape, sorted(keys, key=len, reverse=True)))
        return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)

    def is_uncertain_fragment(self, source: str) -> bool:
        return self.normalize(source) in self.uncertain_fragments

    @staticmethod
    def normalize(source: str) -> str:
        text = source.casefold().replace("'", "").replace("’", "")
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()

    def exact(self, source: str) -> str | None:
        return self.data.get("exact_phrases", {}).get(self.normalize(source))

    def callout(self, source: str, language: str = "en") -> str | None:
        """Translate tightly matched English callouts, even if a short clip got the language wrong."""
        if not self.data:
            return None
        text = self.normalize(source)
        exact = self.data.get("exact_phrases", {}).get(text)
        if exact:
            return exact
        if text in self.locations:
            return f"{self.locations[text]}。"

        # Short conversational requests are often translated literally by M2M100.
        # Match the whole utterance so a longer sentence keeps its own meaning.
        if re.fullmatch(r"(?:just )?give me a (?:moment|second|sec)(?: please)?", text):
            return "等我一下。"
        if re.fullmatch(r"(?:just )?give me a minute(?: please)?", text):
            return "等我一会儿。"
        if re.fullmatch(r"(?:can|could) you give me a (?:moment|second|sec)(?: please)?", text):
            return "能等我一下吗？"
        if re.fullmatch(r"(?:just )?(?:hold on|hang on|wait)(?: a)? (?:moment|second|sec)(?: please)?", text):
            return "等一下。"

        match = re.fullmatch(r"can (?:you|u) drop(?: me)?(?: a| an)? (.+)", text)
        if match and match.group(1) in self.weapons:
            return f"能给我发一把 {self.weapons[match.group(1)]} 吗？"
        match = re.fullmatch(r"(?:he|she|they) (?:has|have|got)(?: a| an)? (.+)", text)
        if match and match.group(1) in self.weapons:
            subject = {"he": "他", "she": "她", "they": "他们"}[text.split()[0]]
            return f"{subject}拿着 {self.weapons[match.group(1)]}。"
        match = re.fullmatch(r"(.+?) (?:is )?(?:low|one hp)", text)
        if match and match.group(1) in self.agents:
            return f"{self.agents[match.group(1)]} 残血。"
        match = re.fullmatch(r"(.+?) (?:is )?(?:at|in|on) (.+)", text)
        if match and match.group(1) in self.agents and match.group(2) in self.locations:
            return f"{self.agents[match.group(1)]} 在 {self.locations[match.group(2)]}。"
        match = re.fullmatch(r"(.+?) is flanking", text)
        if match and match.group(1) in self.agents:
            return f"{self.agents[match.group(1)]} 在绕后。"

        match = re.fullmatch(r"(they|he|she) (smoke|smoked) ([abc])", text)
        if match:
            subject = {"they": "他们", "he": "他", "she": "她"}[match.group(1)]
            action = "在" if match.group(2) == "smoke" else "已经"
            return f"{subject}{action}往 {match.group(3).upper()} 点封烟。"
        match = re.fullmatch(r"smoke ([abc])", text)
        if match:
            return f"往 {match.group(1).upper()} 点封烟。"
        match = re.fullmatch(r"smoked ([abc])", text)
        if match:
            return f"{match.group(1).upper()} 点已封烟。"

        count = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
        number = r"(one|two|three|four|five|[1-5])"
        match = re.fullmatch(rf"{number} (?:at |on |in )?(.+)", text)
        if match and match.group(2) in self.locations:
            amount, alias = count[match.group(1)], match.group(2)
        else:
            match = re.fullmatch(rf"(.+) {number}", text)
            alias, amount = (match.group(1), count[match.group(2)]) if match else ("", 0)
        if alias in self.locations:
            amount_text = {1: "一个", 2: "两个", 3: "三个", 4: "四个", 5: "五个"}
            return f"{self.locations[alias]}{amount_text[amount]}。"

        match = re.fullmatch(r"(?:go |lets |we )?rush(?: to)? ([abc])(?: now)?", text)
        if match:
            return f"快攻 {match.group(1).upper()}。"
        match = re.fullmatch(r"rotate (?:to )?([abc])", text)
        if match:
            return f"转点去 {match.group(1).upper()}。"
        match = re.fullmatch(r"(we need to|we should|lets) rotate (?:to )?([abc])", text)
        if match:
            prefix = {"we need to": "我们需要", "we should": "我们应该",
                      "lets": "我们"}[match.group(1)]
            return f"{prefix}转点去 {match.group(2).upper()}。"
        match = re.fullmatch(r"(?:hold|defend) ([abc])", text)
        if match:
            return f"守 {match.group(1).upper()}。"
        match = re.fullmatch(r"retake ([abc])", text)
        if match:
            return f"重夺 {match.group(1).upper()} 点。"
        match = re.fullmatch(r"go (?:to )?([abc])", text)
        if match:
            return f"去 {match.group(1).upper()}。"
        action, _, alias = text.partition(" ")
        place = self.locations.get(alias)
        if place:
            callout = {"push": f"压 {place}。", "smoke": f"{place}封烟。", "flash": f"{place}给闪。"}
            if action in callout:
                return callout[action]
        if text in self.agents:
            return f"{self.agents[text]}。"
        if text in self.weapons:
            return f"{self.weapons[text]}。"
        return None

    def postprocess(self, translated: str, original: str = "") -> str:
        text = translated.strip()
        if self._target_pattern:
            text = self._target_pattern.sub(lambda match: self._targets.get(match[0].casefold(), match[0]), text)
        eye_warning = self.data.get("target_replacements", {}).get("看你的眼睛")
        if eye_warning and any(self.normalize(part) == "watch your eyes"
                               for part in re.split(r"[.!?。！？,，;；]+", original)):
            text = text.replace("看你的眼睛", eye_warning, 1)
        # Repair known literal model output only when the matching English phrase
        # is a complete utterance or a punctuated clause in the ASR transcript.
        # This avoids changing e.g. "give me a moment of silence" into "wait for me".
        source_with_apostrophes = original.casefold().replace("'", "").replace("’", "")
        for rule in self.data.get("contextual_rewrites", []):
            pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, rule["source"].split()))
            if re.search(pattern + r"(?=\s*[,;.!?]|$)", source_with_apostrophes):
                for literal in rule["literal"]:
                    text = text.replace(literal, rule["natural"])
        source_words = self.normalize(original)
        source_entities = dict.fromkeys(
            self._entity_names[match[0]] for match in self._entity_pattern.finditer(source_words)
        ) if self._entity_pattern else {}
        aliases = {}
        for canonical in source_entities:
            for alias in self.data.get("translated_name_aliases", {}).get(canonical, []):
                aliases.setdefault(alias, canonical)
        if aliases:
            pattern = "|".join(map(re.escape, sorted(aliases, key=len, reverse=True)))
            text = re.sub(pattern, lambda match: aliases[match[0]], text)
        return text[:100]
