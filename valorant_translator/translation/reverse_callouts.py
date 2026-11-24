"""Translate fully recognized Chinese tactical callouts before neural decoding."""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata


_LOCATIONS = {
    "中路": "mid", "中门": "mid doors", "天台": "heaven", "地狱": "hell",
    "水烟": "hookah", "左边": "the left", "右边": "the right",
    "身后": "behind us", "后面": "behind us", "前面": "ahead",
    "门口": "the doorway", "拐角": "the corner", "二楼": "upstairs",
}
_SITE_NAMES = {
    "点": "site", "包点": "site", "点后": "back site",
    "大": "main", "小": "short", "长": "long", "短": "short",
}
_LOCATION = (
    r"(?:[ABCabc](?:点后|包点|点|大|小|长|短)?|"
    + "|".join(_LOCATIONS) + ")"
)
_ANCHOR = rf"(?P<location>{_LOCATION})"
_SUBJECT = r"(?P<subject>我们|大家)?"
_COMMAND = _SUBJECT + r"(?P<order>先别|不要|别|先)?"
_NUMBERS = {
    "一": "One", "两": "Two", "二": "Two", "三": "Three",
    "四": "Four", "五": "Five", "1": "One", "2": "Two",
    "3": "Three", "4": "Four", "5": "Five",
}
_COUNT = r"(?P<count>[一两二三四五1-5])个(?:敌人|人)"


def _place(name: str) -> str:
    if name in _LOCATIONS:
        return _LOCATIONS[name]
    suffix = _SITE_NAMES.get(name[1:])
    return f"{name[0].upper()} {suffix}" if suffix else name.upper()


def _action(match: re.Match, phrase: str) -> str:
    order = match["order"]
    if order == "先别":
        return f"Don't {phrase} yet"
    if order in {"别", "不要"}:
        return f"Don't {phrase}"
    if order == "先":
        phrase = f"{phrase} first"
    if match["subject"] == "我们":
        return f"Let's {phrase}"
    return phrase[0].upper() + phrase[1:]


def _command(pattern, phrase):
    return re.compile(_COMMAND + f"(?:{pattern})"), lambda match: _action(match, phrase(match))


def _at(pattern, phrase):
    return _command(pattern, lambda match: phrase.format(place=_place(match["location"])))


def _literal(pattern, phrase):
    return _command(pattern, lambda _match: phrase)


def _report(match: re.Match) -> str:
    position = _place(match["location"])
    if position in {"the left", "the right"}:
        position = f"on {position}"
    elif position not in {"behind us", "upstairs", "ahead"}:
        position = f"at {position}"
    count = _NUMBERS[match["count"]]
    enemies = "enemy" if count == "One" else "enemies"
    return f"{count} {enemies} {position}"


def _wait(match: re.Match) -> str:
    who = "our teammates" if match["subject"] == "我们" or match["our"] else "teammates"
    tail = {
        "到齐": " to get here",
        "过来": " to come over",
        "来了": " to get here",
        "一起行动": " before making a move",
        "一起进点": " before entering site together",
        "一起走": " so we can move together",
    }
    return f"wait for {who}{tail.get(match['plan'], '')}"


# Rules describe intents and named slots, never a particular utterance.
_RULES = (
    _command(r"等(?:一下|一等|下)?(?P<our>我们的)?队友"
             r"(?P<plan>到齐|过来|来了|一起行动|一起进点|一起走)?", _wait),
    _at(rf"(?:去|到|在){_ANCHOR}集合", "regroup at {place}"),
    _at(rf"集合(?:到|去){_ANCHOR}", "regroup at {place}"),
    _literal(r"集合", "regroup"),
    _at(rf"(?:撤回|退回){_ANCHOR}", "fall back to {place}"),
    _literal(r"撤退|后撤|撤离|往后退", "fall back"),
    _at(rf"(?:转点(?:去|到)?|转(?:去|到)?|换(?:去|到)){_ANCHOR}", "rotate to {place}"),
    _at(rf"(?:快攻|速攻|快冲){_ANCHOR}", "rush {place}"),
    _at(rf"(?:进攻|推进|进点去|打|冲|推){_ANCHOR}", "push {place}"),
    _at(rf"(?:守住|守|架住|架|防守){_ANCHOR}", "hold {place}"),
    _at(rf"(?:盯住|看住|看){_ANCHOR}", "watch {place}"),
    _at(rf"(?:往|朝|向){_ANCHOR}(?:扔闪|丢闪|给个闪)", "flash {place}"),
    _at(rf"(?:扔闪|丢闪|闪一下){_ANCHOR}", "flash {place}"),
    _at(rf"(?:给|往){_ANCHOR}(?:封烟|丢烟|补烟|给烟|封个烟)", "smoke {place}"),
    _at(rf"(?:封烟|烟一下){_ANCHOR}", "smoke {place}"),
    _at(rf"(?:去|在){_ANCHOR}(?:下包|装包|埋包)", "plant the spike at {place}"),
    _literal(r"下包|装包|埋包", "plant the spike"),
    _literal(r"拆包", "defuse the spike"),
    _literal(r"开大|开大招|放大招", "use your ult"),
    _literal(r"小心(?:我们)?(?:身后|后面)", "watch your back"),
    (re.compile(rf"{_ANCHOR}有{_COUNT}"), _report),
    (re.compile(rf"{_COUNT}在{_ANCHOR}"), _report),
)
_BREAK = re.compile(r"[，,。.!！?？；;、]+")
_THEN = re.compile(r"然后|接着|再")


def translate_callout(source: str) -> str | None:
    """Return English only if every clause is recognized unambiguously."""
    source = re.sub(r"\s+", "", unicodedata.normalize("NFKC", source))
    if not source or len(source) > 160 or "?" in source:
        return None

    @lru_cache(maxsize=None)
    def parse(offset: int, remaining: int) -> tuple[str, ...] | None:
        punctuation = _BREAK.match(source, offset)
        if punctuation:
            offset = punctuation.end()
        if offset == len(source):
            return ()
        if not remaining:
            return None
        then = _THEN.match(source, offset) if offset else None
        if then:
            offset = then.end()

        # Whisper may omit punctuation. Consume the entire source so that
        # recognizing a familiar prefix never drops an unfamiliar command.
        candidates = []
        for pattern, render in _RULES:
            match = pattern.match(source, offset)
            if match:
                candidates.append((match.end(), render(match)))
        for end, phrase in sorted(candidates, key=lambda item: item[0], reverse=True):
            tail = parse(end, remaining - 1)
            if tail is not None:
                if then:
                    phrase = f"Then {phrase[0].lower()}{phrase[1:]}"
                return (f"{phrase}.", *tail)
        return None

    clauses = parse(0, 6)
    return " ".join(clauses) if clauses else None
