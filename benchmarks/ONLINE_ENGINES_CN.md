# 0.6.0 在线引擎核实记录

核对日期为 2026-11-26。选择 Microsoft Azure，原因是语音和文本翻译都有文档化的 HTTPS 接口，支持按音频片段同步调用，也有世纪互联中国区接口。极速沿用已有语音切分，以每段音频调用 Fast Transcription，需支持该功能的 S0 语音资源。文本翻译使用 Translator v3 REST，凭据与语音资源分开。自动语种依赖 Fast Transcription 的语种覆盖，遇到服务报错或不支持的语种回退 Whisper。 [全球语音 API](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/fast-transcription-create) · [中国区语音 API](https://docs.azure.cn/en-us/ai-services/speech-service/fast-transcription-create) · [全球文本翻译 API](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/v3/translate)

| 服务 | 官方免费额度和按量价格 | 中国大陆 |
| --- | --- | --- |
| Microsoft Azure | Fast Transcription 需要 S0，中国区 ¥2.29/音频小时。中国区实时转写另有 F0 的 5 小时/月，S0 标准实时 ¥3/音频小时。Translator 中国区 F0 200 万字符/月，S1 ¥102/百万字符。全球定价页确认 Translator F0 200 万字符/月，全球付费价格随区域和账户而异，本次未核实具体金额。 [中国区定价](https://www.azure.cn/pricing/details/cognitive-services/index.html) · [Fast 额度](https://docs.azure.cn/en-us/ai-services/speech-service/speech-services-quotas-and-limits) · [全球 Speech 定价](https://azure.microsoft.com/en-us/pricing/details/speech/) · [全球 Translator 定价](https://azure.microsoft.com/en-us/pricing/details/translator/) | 有世纪互联端点。语音需要确认账户和资源支持 Fast。官方文档列明中国区订阅资格，本次未验证玩家网络及有效账户。 [中国区语音](https://docs.azure.cn/en-us/ai-services/speech-service/sovereign-clouds) · [中国区翻译](https://docs.azure.cn/en-us/ai-services/translator/reference/sovereign-clouds) |
| Google Cloud | Speech-to-Text V2 标准用量起价 US$0.016/分钟，V1 的 60 分钟/月免费额度适用于 V1。NMT 前 50 万字符/月由每月抵扣覆盖，超额 US$20/百万字符。 [语音价格](https://cloud.google.com/speech-to-text/pricing) · [翻译价格](https://cloud.google.com/products/translate/pricing) | 翻译公布全球及美欧多区域端点，未列出大陆区域。玩家网络可达性未测。 [翻译端点](https://docs.cloud.google.com/translate/docs/advanced/endpoints) |
| DeepL | Developer 有一次性 100 万文本字符额度。Growth 含固定订阅费、语音和文本额度，超额按量收费。具体金额未核实。 [方案](https://support.deepl.com/hc/en-us/articles/360021200939-DeepL-API-plans) · [计费](https://support.deepl.com/hc/en-us/articles/360020685720-Usage-count-and-billing-in-DeepL-API) | 付费计划地区列表未列中国大陆。玩家网络可达性未测。 [地区列表](https://support.deepl.com/hc/en-us/articles/360020016339-Countries-and-regions-where-DeepL-paid-plans-are-available) |
| OpenAI Realtime | GPT-Live-Transcribe 为 US$0.017/音频分钟，GPT-Realtime-Translate 为 US$0.034/音频分钟。两款均无免费层，后者可直接生成翻译转录。 [转写](https://developers.openai.com/api/docs/models/gpt-live-transcribe) · [实时翻译](https://developers.openai.com/api/docs/models/gpt-realtime-translate) | API 支持地区列表未列中国大陆。玩家网络可达性未测。 [地区列表](https://developers.openai.com/api/docs/supported-countries) |

## 验证记录

- 只读请求本仓库的生产 COS 模型清单，确认提供 tiny、base、small、medium。精准使用 small。现有 Small + M2M100 组合包也能提供 small。
- 本机无凭据探测 Azure Translator 的公开语言列表。中国端点 HTTP 200，单次总耗时 0.517 秒。全球端点 HTTP 200，单次总耗时 0.845 秒。这两次请求只读服务元数据。带凭据的识别、翻译延迟与大陆玩家实际网络可达性均未独立验证。其它候选的端到端延迟未测。
- 在 Mac 上用一条 2.98 秒的本地合成语音运行真实 tiny 和 small，再用 M2M100 翻译。平衡、精准都生成文本。另注入模拟在线故障，确认极速用真实 tiny 和 M2M100 回退。该样本仍有战术用词误译。
- 用替身覆盖了在线两阶段成功、超时回退时同一句字幕继续交付、实际路由状态、请求格式、缺 Key 时禁止选择极速，以及本地模式配置迁移。真实 Azure S0 语音资源、Translator 凭据和队友语音样本仍是第 2 步验收的前置条件。

## 真机验收

在详细设置的「配置 Azure」填写对应云区的语音资源名、语音 Key、翻译区域和翻译 Key。在同一台实际游玩设备上选择极速，录入多条有代表性的队友语音，记录每段语音结束到字幕显示的时间、识别及翻译路由。分别验证在线成功和一次断网后恢复本地，再比较同一批语音在平衡档的表现。使用应用已有的「界面已更新」任务跟踪核对字幕出现在界面的时间。
