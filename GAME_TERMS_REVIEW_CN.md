# VALORANT 英语术语与名称审核表

本表从 [独立词库](config/glossary.json) 生成。可以在同目录的 `GAME_TERMS_REVIEW.csv` 填写建议发回；不要把识别错词当成已翻译正确。

枪械与英雄英文名按 [Riot 枪械页](https://playvalorant.com/en-us/arsenal/) 和 [Riot 英雄页](https://playvalorant.com/en-us/agents/) 核对；`Warden` 另见 [VALORANT 官方 Wiki 武器表](https://wiki.playvalorant.com/en-us/Template:Navbox_weapons)。名称在字幕中保留英文。

**适用边界：** 固定译法和组合规则只对识别后的整句生效；少量上下文口语化规则只修正已知的字面机翻。若语音识别把 `They smoke B` 听成 `They won't be`，词库不会凭猜测改成封烟；需用原始测试音频调整识别。中文语音直接显示简体中文，不进入外语翻译模型。

## 固定整句（75 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| two a main | A 大两个。 | 识别原文整句匹配 |
| rotate b | 转点去 B。 | 识别原文整句匹配 |
| go rush a | 快攻 A。 | 识别原文整句匹配 |
| rush a | 快攻 A。 | 识别原文整句匹配 |
| rush b | 快攻 B。 | 识别原文整句匹配 |
| play slow | 慢打。 | 识别原文整句匹配 |
| fall back | 撤退。 | 识别原文整句匹配 |
| hold a | 守 A。 | 识别原文整句匹配 |
| hold b | 守 B。 | 识别原文整句匹配 |
| retake a | 重夺 A 点。 | 识别原文整句匹配 |
| retake b | 重夺 B 点。 | 识别原文整句匹配 |
| one is low | 有一个残血。 | 识别原文整句匹配 |
| dont peek | 别拉出去对枪。 | 识别原文整句匹配 |
| one hp | 残血。 | 识别原文整句匹配 |
| save | 保枪。 | 识别原文整句匹配 |
| eco | 经济局。 | 识别原文整句匹配 |
| low hp | 残血。 | 识别原文整句匹配 |
| site | 包点。 | 识别原文整句匹配 |
| rotate | 转点。 | 识别原文整句匹配 |
| flank | 绕后。 | 识别原文整句匹配 |
| push | 前压。 | 识别原文整句匹配 |
| peek | 拉出去对枪。 | 识别原文整句匹配 |
| spike | 爆能器。 | 识别原文整句匹配 |
| plant | 下包。 | 识别原文整句匹配 |
| defuse | 拆包。 | 识别原文整句匹配 |
| ult | 大招。 | 识别原文整句匹配 |
| smoke | 烟。 | 识别原文整句匹配 |
| flash | 闪。 | 识别原文整句匹配 |
| can you drop | 能给我发一把吗？ | 识别原文整句匹配 |
| can u drop | 能给我发一把吗？ | 识别原文整句匹配 |
| can you drop me | 能给我发一把吗？ | 识别原文整句匹配 |
| can someone drop | 有人能给我发一把吗？ | 识别原文整句匹配 |
| anyone can drop | 有人能给我发一把吗？ | 识别原文整句匹配 |
| drop me | 给我发一把。 | 识别原文整句匹配 |
| dont push | 别前压。 | 识别原文整句匹配 |
| dont rotate | 别转点。 | 识别原文整句匹配 |
| plant spike | 下包。 | 识别原文整句匹配 |
| defuse spike | 拆包。 | 识别原文整句匹配 |
| spike down | 包掉了。 | 识别原文整句匹配 |
| one tap | 一枪头。 | 识别原文整句匹配 |
| give me a moment | 等我一下。 | 识别原文整句匹配 |
| give me a second | 等我一下。 | 识别原文整句匹配 |
| give me a sec | 等我一下。 | 识别原文整句匹配 |
| give me a minute | 等我一会儿。 | 识别原文整句匹配 |
| gimme a sec | 等我一下。 | 识别原文整句匹配 |
| hold on | 等一下。 | 识别原文整句匹配 |
| hold on a second | 等一下。 | 识别原文整句匹配 |
| hold on a sec | 等一下。 | 识别原文整句匹配 |
| hang on | 等一下。 | 识别原文整句匹配 |
| hang on a second | 等一下。 | 识别原文整句匹配 |
| wait a second | 等一下。 | 识别原文整句匹配 |
| wait a sec | 等一下。 | 识别原文整句匹配 |
| one sec | 等一下。 | 识别原文整句匹配 |
| one second | 等一下。 | 识别原文整句匹配 |
| one moment | 稍等。 | 识别原文整句匹配 |
| wait for me | 等我一下。 | 识别原文整句匹配 |
| im coming | 我来了。 | 识别原文整句匹配 |
| i am coming | 我来了。 | 识别原文整句匹配 |
| on my way | 我在赶来。 | 识别原文整句匹配 |
| be right back | 我马上回来。 | 识别原文整句匹配 |
| brb | 我马上回来。 | 识别原文整句匹配 |
| my bad | 我的锅。 | 识别原文整句匹配 |
| sorry my bad | 抱歉，我的锅。 | 识别原文整句匹配 |
| i need help | 帮我一下。 | 识别原文整句匹配 |
| can you help me | 能帮我一下吗？ | 识别原文整句匹配 |
| i cant hear you | 我听不清你说话。 | 识别原文整句匹配 |
| say that again | 再说一遍。 | 识别原文整句匹配 |
| im lagging | 我卡了。 | 识别原文整句匹配 |
| i am lagging | 我卡了。 | 识别原文整句匹配 |
| im reloading | 我在换弹。 | 识别原文整句匹配 |
| i am reloading | 我在换弹。 | 识别原文整句匹配 |
| reloading | 换弹中。 | 识别原文整句匹配 |
| cover me | 掩护我一下。 | 识别原文整句匹配 |
| i need to heal | 我得回血。 | 识别原文整句匹配 |
| heal me | 帮我回血。 | 识别原文整句匹配 |

## 位置别名（11 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| a main, a-main | A 大 | 识别原文匹配后保留位置信息 |
| b main, b-main | B 大 | 识别原文匹配后保留位置信息 |
| a short, a-short | A 小 | 识别原文匹配后保留位置信息 |
| a long, a-long | A 长 | 识别原文匹配后保留位置信息 |
| b long, b-long | B 长 | 识别原文匹配后保留位置信息 |
| heaven | 天堂 | 识别原文匹配后保留位置信息 |
| hell | 地狱 | 识别原文匹配后保留位置信息 |
| mid, middle | 中路 | 识别原文匹配后保留位置信息 |
| a site, site a | A 点 | 识别原文匹配后保留位置信息 |
| b site, site b | B 点 | 识别原文匹配后保留位置信息 |
| c site, site c | C 点 | 识别原文匹配后保留位置信息 |

## 武器英文名（21 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Bandit | Bandit | 保留官方英文名 |
| Classic | Classic | 保留官方英文名 |
| Shorty | Shorty | 保留官方英文名 |
| Frenzy | Frenzy | 保留官方英文名 |
| Ghost | Ghost | 保留官方英文名 |
| Sheriff | Sheriff | 保留官方英文名 |
| Stinger | Stinger | 保留官方英文名 |
| Spectre | Spectre | 保留官方英文名 |
| Bucky | Bucky | 保留官方英文名 |
| Judge | Judge | 保留官方英文名 |
| Bulldog | Bulldog | 保留官方英文名 |
| Guardian | Guardian | 保留官方英文名 |
| Warden | Warden | 保留官方英文名 |
| Phantom | Phantom | 保留官方英文名 |
| Vandal | Vandal | 保留官方英文名 |
| Marshal | Marshal | 保留官方英文名 |
| Outlaw | Outlaw | 保留官方英文名 |
| Operator | Operator | 保留官方英文名 |
| Ares | Ares | 保留官方英文名 |
| Odin | Odin | 保留官方英文名 |
| Tactical Knife | Tactical Knife | 保留官方英文名 |

## 英雄英文名（30 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Astra | Astra | 保留官方英文名 |
| Breach | Breach | 保留官方英文名 |
| Brimstone | Brimstone | 保留官方英文名 |
| Chamber | Chamber | 保留官方英文名 |
| Clove | Clove | 保留官方英文名 |
| Cypher | Cypher | 保留官方英文名 |
| Deadlock | Deadlock | 保留官方英文名 |
| Fade | Fade | 保留官方英文名 |
| Gekko | Gekko | 保留官方英文名 |
| Harbor | Harbor | 保留官方英文名 |
| Hush | Hush | 保留官方英文名 |
| Iso | Iso | 保留官方英文名 |
| Jett | Jett | 保留官方英文名 |
| KAY/O | KAY/O | 保留官方英文名 |
| Killjoy | Killjoy | 保留官方英文名 |
| Miks | Miks | 保留官方英文名 |
| Neon | Neon | 保留官方英文名 |
| Omen | Omen | 保留官方英文名 |
| Phoenix | Phoenix | 保留官方英文名 |
| Raze | Raze | 保留官方英文名 |
| Reyna | Reyna | 保留官方英文名 |
| Sage | Sage | 保留官方英文名 |
| Skye | Skye | 保留官方英文名 |
| Sova | Sova | 保留官方英文名 |
| Tejo | Tejo | 保留官方英文名 |
| Veto | Veto | 保留官方英文名 |
| Viper | Viper | 保留官方英文名 |
| Vyse | Vyse | 保留官方英文名 |
| Waylay | Waylay | 保留官方英文名 |
| Yoru | Yoru | 保留官方英文名 |

## 英文别名（5 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Op, AWP | Operator | 仅在整句规则中使用 |
| Marshall | Marshal | 仅在整句规则中使用 |
| Kayo, Kay O | KAY/O | 仅在整句规则中使用 |
| Jet | Jett | 仅在整句规则中使用 |
| Raina, Rayna | Reyna | 仅在整句规则中使用 |

## 组合规则：封烟（2 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| They smoke B | 他们在往 B 点封烟。 | 示例；原文必须符合对应句型 |
| Smoke B | 往 B 点封烟。 | 示例；原文必须符合对应句型 |

## 组合规则：请求武器（2 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Can you drop a Bandit? | 能给我发一把 Bandit 吗？ | 示例；原文必须符合对应句型 |
| Can you drop an OP? | 能给我发一把 Operator 吗？ | 示例；原文必须符合对应句型 |

## 组合规则：携带武器（1 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| He has an Operator | 他拿着 Operator。 | 示例；原文必须符合对应句型 |

## 组合规则：英雄残血（1 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Jett is low | Jett 残血。 | 示例；原文必须符合对应句型 |

## 组合规则：英雄位置（1 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Sage at B main | Sage 在 B 大。 | 示例；原文必须符合对应句型 |

## 组合规则：英雄绕后（1 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Reyna is flanking | Reyna 在绕后。 | 示例；原文必须符合对应句型 |

## 组合规则：快攻（1 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Go rush A | 快攻 A。 | 示例；原文必须符合对应句型 |

## 组合规则：转点（1 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| Rotate B | 转点去 B。 | 示例；原文必须符合对应句型 |

## 译后名称修正（10 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| 飞机, 天空, 杰特 | Jett | 仅当识别原文含对应英文名时替换 |
| 智慧, 圣人, 贤者 | Sage | 仅当识别原文含对应英文名时替换 |
| 雷娜, 瑞娜 | Reyna | 仅当识别原文含对应英文名时替换 |
| 运营商, 操作员 | Operator | 仅当识别原文含对应英文名时替换 |
| 瓦达尔, 破坏者 | Vandal | 仅当识别原文含对应英文名时替换 |
| 幻影, 幽灵 | Phantom | 仅当识别原文含对应英文名时替换 |
| 幽灵 | Ghost | 仅当识别原文含对应英文名时替换 |
| 法官 | Judge | 仅当识别原文含对应英文名时替换 |
| 警长 | Sheriff | 仅当识别原文含对应英文名时替换 |
| 预兆, 奥门 | Omen | 仅当识别原文含对应英文名时替换 |

## 上下文口语化（3 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| give me a moment | 等我一下 | 仅当原文是完整句或独立分句，且机翻出现指定直译时替换 |
| give me a second | 等我一下 | 仅当原文是完整句或独立分句，且机翻出现指定直译时替换 |
| im reloading | 我在换弹 | 仅当原文是完整句或独立分句，且机翻出现指定直译时替换 |

## 不可靠片段（1 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| they wont be | 不显示字幕 | 只过滤整句完全匹配；不会猜测原话 |

## 译后术语替换（24 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| A Main | A 大 | 仅译文中仍出现该英文时替换 |
| B Main | B 大 | 仅译文中仍出现该英文时替换 |
| A Short | A 小 | 仅译文中仍出现该英文时替换 |
| A Long | A 长 | 仅译文中仍出现该英文时替换 |
| B Long | B 长 | 仅译文中仍出现该英文时替换 |
| Heaven | 天堂 | 仅译文中仍出现该英文时替换 |
| Hell | 地狱 | 仅译文中仍出现该英文时替换 |
| Mid | 中路 | 仅译文中仍出现该英文时替换 |
| Site | 包点 | 仅译文中仍出现该英文时替换 |
| Rotate | 转点 | 仅译文中仍出现该英文时替换 |
| Flank | 绕后 | 仅译文中仍出现该英文时替换 |
| Push | 前压 | 仅译文中仍出现该英文时替换 |
| Peek | 拉出去对枪 | 仅译文中仍出现该英文时替换 |
| One HP | 残血 | 仅译文中仍出现该英文时替换 |
| Low HP | 残血 | 仅译文中仍出现该英文时替换 |
| Save | 保枪 | 仅译文中仍出现该英文时替换 |
| Eco | 经济局 | 仅译文中仍出现该英文时替换 |
| Spike | 爆能器 | 仅译文中仍出现该英文时替换 |
| Plant | 下包 | 仅译文中仍出现该英文时替换 |
| Defuse | 拆包 | 仅译文中仍出现该英文时替换 |
| Ult | 大招 | 仅译文中仍出现该英文时替换 |
| Smoke | 烟 | 仅译文中仍出现该英文时替换 |
| Flash | 闪 | 仅译文中仍出现该英文时替换 |
| One Tap | 一枪头 | 仅译文中仍出现该英文时替换 |

## 识别提示词（15 条）

| 英语原话或规则 | 当前中文或示例 | 用途或限制 |
|---|---|---|
| A Main |  | 只影响 Whisper 提示，不保证识别 |
| B Main |  | 只影响 Whisper 提示，不保证识别 |
| A Short |  | 只影响 Whisper 提示，不保证识别 |
| A Long |  | 只影响 Whisper 提示，不保证识别 |
| Heaven |  | 只影响 Whisper 提示，不保证识别 |
| Hell |  | 只影响 Whisper 提示，不保证识别 |
| Mid |  | 只影响 Whisper 提示，不保证识别 |
| Spike |  | 只影响 Whisper 提示，不保证识别 |
| One HP |  | 只影响 Whisper 提示，不保证识别 |
| Smoke A |  | 只影响 Whisper 提示，不保证识别 |
| Smoke B |  | 只影响 Whisper 提示，不保证识别 |
| They smoke B |  | 只影响 Whisper 提示，不保证识别 |
| They smoked B |  | 只影响 Whisper 提示，不保证识别 |
| Can you drop |  | 只影响 Whisper 提示，不保证识别 |
| Drop |  | 只影响 Whisper 提示，不保证识别 |
