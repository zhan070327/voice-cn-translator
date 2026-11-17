"""Export the editable term inventory from config/glossary.json."""
from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TERMS = ROOT / "config" / "glossary.json"
CSV = ROOT / "GAME_TERMS_REVIEW.csv"
MARKDOWN = ROOT / "GAME_TERMS_REVIEW_CN.md"

RULES = [
    ("封烟", "They smoke B", "他们在往 B 点封烟。"),
    ("封烟", "Smoke B", "往 B 点封烟。"),
    ("请求武器", "Can you drop a Bandit?", "能给我发一把 Bandit 吗？"),
    ("请求武器", "Can you drop an OP?", "能给我发一把 Operator 吗？"),
    ("携带武器", "He has an Operator", "他拿着 Operator。"),
    ("英雄残血", "Jett is low", "Jett 残血。"),
    ("英雄位置", "Sage at B main", "Sage 在 B 大。"),
    ("英雄绕后", "Reyna is flanking", "Reyna 在绕后。"),
    ("快攻", "Go rush A", "快攻 A。"),
    ("转点", "Rotate B", "转点去 B。"),
]


def main() -> None:
    data = json.loads(TERMS.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str, str, str]] = []

    def add(category: str, source: str, target: str, note: str = "") -> None:
        rows.append((category, source, target, "", note))

    for source, target in data["exact_phrases"].items():
        add("固定整句", source, target, "识别原文整句匹配")
    for target, aliases in data["location_aliases"].items():
        add("位置别名", ", ".join(aliases), target, "识别原文匹配后保留位置信息")
    for name in data["weapon_names"]:
        add("武器英文名", name, name, "保留官方英文名")
    for name in data["agent_names"]:
        add("英雄英文名", name, name, "保留官方英文名")
    for canonical, aliases in data.get("entity_aliases", {}).items():
        add("英文别名", ", ".join(aliases), canonical, "仅在整句规则中使用")
    for category, source, target in RULES:
        add("组合规则：" + category, source, target, "示例；原文必须符合对应句型")
    for canonical, aliases in data.get("translated_name_aliases", {}).items():
        add("译后名称修正", ", ".join(aliases), canonical,
            "仅当识别原文含对应英文名时替换")
    for rule in data.get("contextual_rewrites", []):
        add("上下文口语化", rule["source"], rule["natural"],
            "仅当原文是完整句或独立分句，且机翻出现指定直译时替换")
    for text in data.get("uncertain_recognition_fragments", []):
        add("不可靠片段", text, "不显示字幕", "只过滤整句完全匹配；不会猜测原话")
    for source, target in data["target_replacements"].items():
        add("译后术语替换", source, target, "仅译文中仍出现该英文时替换")
    for word in data["recognition_hotwords"]:
        add("识别提示词", word, "", "只影响 Whisper 提示，不保证识别")

    with CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("类别", "英语原话或规则", "当前中文或示例", "你的建议（请填写）", "程序用途或限制"))
        writer.writerows(rows)

    lines = [
        "# VALORANT 英语术语与名称审核表",
        "",
        "本表从 [独立词库](config/glossary.json) 生成。可以在同目录的 `GAME_TERMS_REVIEW.csv` 填写建议发回；不要把识别错词当成已翻译正确。",
        "",
        "枪械与英雄英文名按 [Riot 枪械页](https://playvalorant.com/en-us/arsenal/) 和 [Riot 英雄页](https://playvalorant.com/en-us/agents/) 核对；`Warden` 另见 [VALORANT 官方 Wiki 武器表](https://wiki.playvalorant.com/en-us/Template:Navbox_weapons)。名称在字幕中保留英文。",
        "",
        "**适用边界：** 固定译法和组合规则只对识别后的整句生效；少量上下文口语化规则只修正已知的字面机翻。若语音识别把 `They smoke B` 听成 `They won't be`，词库不会凭猜测改成封烟；需用原始测试音频调整识别。中文语音直接显示简体中文，不进入外语翻译模型。",
        "",
    ]
    categories = dict.fromkeys(row[0] for row in rows)
    def escape(value: str) -> str:
        return value.replace("|", "\\|")

    for category in categories:
        matches = [row for row in rows if row[0] == category]
        lines.extend((f"## {category}（{len(matches)} 条）", "",
                      "| 英语原话或规则 | 当前中文或示例 | 用途或限制 |", "|---|---|---|"))
        for _, source, target, _, note in matches:
            lines.append(f"| {escape(source)} | {escape(target)} | {escape(note)} |")
        lines.append("")
    MARKDOWN.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported {len(rows)} rows to {CSV.name} and {MARKDOWN.name}")


if __name__ == "__main__":
    main()
