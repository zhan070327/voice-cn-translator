from __future__ import annotations

import json
import re
from pathlib import Path

from valorant_translator.config import APP_DIR

GLOSSARY_FILE = Path(__file__).resolve().parents[2] / "config" / "glossary.json"
CUSTOM_GLOSSARY_FILE = APP_DIR / "glossary_custom.json"


class Glossary:
    def __init__(self, path: Path = GLOSSARY_FILE):
        self.data = json.loads(path.read_text(encoding="utf-8"))
        if path == GLOSSARY_FILE and CUSTOM_GLOSSARY_FILE.is_file():
            try:
                custom = json.loads(CUSTOM_GLOSSARY_FILE.read_text(encoding="utf-8"))
                if isinstance(custom, dict):
                    for key, value in custom.items():
                        if key not in self.data:
                            continue
                        if isinstance(value, dict) and isinstance(self.data[key], dict):
                            self.data[key].update(value)
                        elif isinstance(value, list) and isinstance(self.data[key], list):
                            self.data[key].extend(item for item in value if item not in self.data[key])
            except (OSError, ValueError, TypeError):
                pass
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

    def is_uncertain_fragment(self, source: str) -> bool:
        return self.normalize(source) in self.uncertain_fragments

    @staticmethod
    def normalize(source: str) -> str:
        text = source.casefold().replace("'", "").replace("’", "")
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()

    def exact(self, source: str) -> str | None:
        return self.data["exact_phrases"].get(self.normalize(source))

    def callout(self, source: str, language: str) -> str | None:
        """Translate tightly matched English callouts, even if a short clip got the language wrong."""
        text = self.normalize(source)
        exact = self.data["exact_phrases"].get(text)
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
        for alias, place in self.locations.items():
            escaped = re.escape(alias)
            number = r"(one|two|three|four|five|[1-5])"
            match = re.fullmatch(rf"{number} (?:at |on |in )?{escaped}", text)
            if not match:
                match = re.fullmatch(rf"{escaped} {number}", text)
            if match:
                amount = {1: "一个", 2: "两个", 3: "三个", 4: "四个", 5: "五个"}
                return f"{place}{amount[count[match.group(1)]]}。"

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
        for alias, place in self.locations.items():
            if text == f"push {alias}":
                return f"压 {place}。"
            if text == f"smoke {alias}":
                return f"{place}封烟。"
            if text == f"flash {alias}":
                return f"{place}给闪。"
        if text in self.agents:
            return f"{self.agents[text]}。"
        if text in self.weapons:
            return f"{self.weapons[text]}。"
        return None

    def postprocess(self, translated: str, original: str = "") -> str:
        text = translated.strip()
        for source, target in self.data["target_replacements"].items():
            text = re.sub(rf"(?i)(?<![\w]){re.escape(source)}(?![\w])", target, text)
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
        source_entities = {canonical for alias, canonical in {**self.weapons, **self.agents}.items()
                           if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", source_words)}
        for canonical in source_entities:
            aliases = self.data.get("translated_name_aliases", {}).get(canonical, [])
            for alias in sorted(aliases, key=len, reverse=True):
                text = text.replace(alias, canonical)
        return text[:100]
