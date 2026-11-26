import pytest

from valorant_translator.translation.glossary import Glossary


@pytest.mark.parametrize(("original", "literal", "expected"), [
    ("等我一下，我先看右边", "wait for me a moment, I'll watch right", "hold on a sec, I'll watch right"),
    ("等我一秒，别去 B", "wait for me one second, don't go B", "give me a sec, don't go B"),
    ("我在换弹，别露头", "I am changing the bullet, don't peek", "I’m reloading, don't peek"),
    ("等我换完弹再转点", "once I have finished changing bullets, then change the point",
     "once I have finished reloading, then rotate"),
    ("从左边转个点", "change a point from the left", "rotate from the left"),
    ("转去 B，别走右边", "switch the point to B, don't go right", "rotate to B, don't go right"),
])
def test_ptt_rewrites_keep_surrounding_callout(original, literal, expected):
    assert Glossary().finish_zh_en(literal, original) == expected


def test_fixed_phrase_and_default_variants():
    glossary = Glossary()
    assert glossary.exact("Fuck you bro!") == "操你妈兄弟。"
    for term in ("打默认", "打个默认", "默认打法"):
        assert glossary.prepare_zh_en(f"这局{term}。") == "这局打 default。"
    assert glossary.finish_zh_en("Give me a moment of silence", "默哀一下") == "Give me a moment of silence"


@pytest.mark.parametrize(("original", "expected"), [
    ("One top mid.", "中路顶部一个。"),
    ("Two mid stairs.", "中路楼梯两个。"),
    ("Three A backsite.", "A 后点三个。"),
    ("B back site four!", "B 后点四个。"),
    ("Five C backsite.", "C 后点五个。"),
    ("One in A connector.", "A 连接一个。"),
    ("2 B connector!", "B 连接两个。"),
    ("C connector three.", "C 连接三个。"),
    ("4 at mid top!", "中路顶部四个。"),
    ("5 on B backsite.", "B 后点五个。"),
    ("Don't peek left.", "别从左边露头。"),
    ("Don't peek right.", "别从右边露头。"),
    ("Don't push A main.", "别压 A 大。"),
    ("Don't push B main.", "别压 B 大。"),
    ("Don't rotate to A.", "别转 A。"),
    ("Don't rotate to B.", "别转 B。"),
    ("Don't rotate to C.", "别转 C。"),
    ("Don't plant yet.", "先别下包。"),
    ("Don't defuse yet.", "先别拆包。"),
    ("Spike isn't planted.", "还没下包。"),
])
def test_valorant_count_direction_and_negation(original, expected):
    from valorant_translator.config import Settings
    from valorant_translator.translation.enhancement import GameEnhancer

    engine = GameEnhancer("valorant", Glossary())
    assert engine.match(original, "en", "zh", Settings()).translation == expected


@pytest.mark.parametrize(("original", "corrected", "expected"), [
    ("To AMAIN. Don't rotate to B!", "Two A Main. Don't rotate to B!", "A 大两个。别转 B。"),
    ("My ultimus, ready! Rotate B.", "My ultimate is ready. Rotate B.", "我的大招好了。转点去 B。"),
    ("Ultimus. Rotate B.", "ultimate. Rotate B.", "大招。转点去 B。"),
    ("To AMAIN, don't rotate; wait for my call!", "Two A Main, don't rotate; wait for my call!", None),
    ("They are finding me. Ultimus；别露头", "They are finding me. ultimate；别露头", None),
])
def test_asr_correction_across_clauses(original, corrected, expected):
    from dataclasses import replace
    from valorant_translator.config import Settings
    from valorant_translator.translation.enhancement import GameEnhancer

    engine = GameEnhancer("valorant", Glossary())
    settings = Settings()
    result = engine.match(original, "en", "zh", settings)
    assert (result.text, result.translation) == (corrected, expected)
    assert "asr_correction:" in result.rules
    assert engine.match(original, "en", "zh", replace(settings, asr_correction=False)).text == original
