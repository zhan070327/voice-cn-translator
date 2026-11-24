from __future__ import annotations

import pytest

from valorant_translator.translation.reverse_callouts import translate_callout


@pytest.mark.parametrize(
    ("chinese", "english"),
    [
        ("我们去B点集合等队友一起行动。", "Let's regroup at B site. Wait for teammates before making a move."),
        ("去 A 点集合，等队友到齐。", "Regroup at A site. Wait for teammates to get here."),
        ("大家先等一下队友，再转点去B。", "Wait for teammates first. Then rotate to B."),
        ("我们等我们的队友一起进点", "Let's wait for our teammates before entering site together."),
        ("在中路集合", "Regroup at mid."),
        ("集合到C点", "Regroup at C site."),
        ("撤退！", "Fall back."),
        ("别后撤", "Don't fall back."),
        ("先别冲C", "Don't push C yet."),
        ("退回B大", "Fall back to B main."),
        ("我们快攻Ａ点", "Let's rush A site."),
        ("进攻B小", "Push B short."),
        ("往后退，然后守住A长", "Fall back. Then hold A long."),
        ("不要打A，转B", "Don't push A. Rotate to B."),
        ("守住B包点", "Hold B site."),
        ("守住B点后", "Hold B back site."),
        ("架住中门", "Hold mid doors."),
        ("两个敌人在天台", "Two enemies at heaven."),
        ("B点有3个人", "Three enemies at B site."),
        ("左边有一个敌人", "One enemy on the left."),
        ("二楼有两个人", "Two enemies upstairs."),
        ("小心身后", "Watch your back."),
        ("看右边", "Watch the right."),
        ("往水烟扔闪", "Flash hookah."),
        ("给中路封个烟", "Smoke mid."),
        ("我们去B下包", "Let's plant the spike at B."),
        ("别拆包", "Don't defuse the spike."),
        ("先开大，再冲A", "Use your ult first. Then push A."),
        ("撤退，撤退", "Fall back. Fall back."),
    ],
)
def test_callout_preserves_tactical_intents(chinese, english):
    assert translate_callout(chinese) == english


@pytest.mark.parametrize("chinese", [
    "去B点集合，然后向能看到烟的位置靠拢",
    "如果队友没到就先别冲A",
    "撤退还是打A",
    "你能帮我买把枪吗？",
    "去B点集合？等队友。",
    "别着急，我们还可以想想。",
    "我去转B",
    "去AB集合",
    "集合，然后",
    " ",
])
def test_unrecognized_or_ambiguous_speech_falls_back_whole(chinese):
    assert translate_callout(chinese) is None
