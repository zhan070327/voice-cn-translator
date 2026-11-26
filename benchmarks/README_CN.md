# 0.6.0 翻译模型评估

基线是 SameFreq b8 的 `main @ 9f8d9c1`。评测集有 28 条，英→中 18 条，中→英 10 条。固定短语取自 b8 的 573 条 `exact_phrases`，口语来自两组 `contextual_rewrites` 和 `lexical_terms`。标为 `composed` 的句子组合了点位、数字、方向、否定和多个动作。测试者反馈收到后可以按同一列格式补入。

## 测量口径

- 主对照表测已加载模型收到一句文字到完成解码和输出格式转换的耗时。各模型都用同样的解码上限和 beam 数。英→中用 1 个 beam，中→英用 3 个。CPU 是 float32，CUDA 路径是 float16。
- 每个进程先预热，再用 4 个 PyTorch CPU 线程顺序跑 3 次。每句取 3 次的中位数。表里给单句中位数的总体中位数与 P95（最近秩法）。装载时间另记在 JSON。
- PTT 附测从已转写的中文开始，沿 b8 的 Valorant 文本路径调用 GameEnhancer、词库预处理、内置反向报点和翻译后处理。使用随程序分发的词库和默认设置。ASR、麦克风采集和按键处理在这两组测试之外。
- 逐句按游戏语义人工评分。**对**包括自然同义表达。**可接受**表示表达生硬但队友仍能判断应做什么。**错**表示关键信息有遗漏、歧义或错误。点位、人数、方向、否定及动作先后优先。每条翻译、计时和判定理由均有记录。

## 本机结果

macOS arm64，PyTorch 2.15.0，Transformers 4.57.6。CUDA 不可用。最后一行单测 b8 PTT 文本路径，样本只有 10 条，不能与前三行的 28 条模型总体延迟直接比较。

| 模型或路径 | CPU 中位 / P95 | CUDA GPU | 对 / 可接受 / 错 |
| --- | ---: | --- | ---: |
| m2m100_418M | 277.29 / 1202.45 ms | 未测 | 2 / 6 / 20 |
| m2m100_1.2B | 871.61 / 3313.19 ms | 未测 | 1 / 6 / 21 |
| nllb-200-distilled-600M | 406.78 / 2305.82 ms | 未测 | 2 / 6 / 20 |
| m2m100_418M（PTT 文本路径） | 848.57 / 1312.27 ms | 未测 | 1 / 0 / 9 |

**建议保留 M2M100 418M。** 它在这批句子上的 CPU 中位延迟最低。两个候选依旧会丢点位或翻错战术，1.2B 把“右边”翻成“左边”，NLLB 把“先别露头”翻成肯定指令。应先加强词库和口语改写，收到测试者的真实样本后再增加用例。

[逐句评分](results/cpu-quality.tsv) · [418M 原始结果](results/m2m100_418M-cpu.json) · [1.2B 原始结果](results/m2m100_1.2B-cpu.json) · [NLLB 原始结果](results/nllb-200-distilled-600M-cpu.json) · [PTT 原始结果](results/m2m100_418M-ptt-cpu.json)

## 复现

安装仓库现有依赖后，将以下原始模型文件放进同一个目录，每个模型存到表中的独立子目录。下载权重和 tokenizer 所需文件，跳过无关的 Rust 权重。脚本仅从本地目录加载。

| 子目录 | 来源 |
| --- | --- |
| `m2m100_418M` | [Meta M2M100 418M，MIT](https://huggingface.co/facebook/m2m100_418M) |
| `m2m100_1.2B` | [Meta M2M100 1.2B，MIT](https://huggingface.co/facebook/m2m100_1.2B/tree/7b36184180524c1a1bbfa37f120a608046250b98) |
| `nllb-200-distilled-600M` | [Meta NLLB 200 Distilled 600M，CC BY-NC 4.0](https://huggingface.co/facebook/nllb-200-distilled-600M/tree/f8d333a) |

从仓库根目录执行以下命令，依次替换 `--model` 和输出文件名。CUDA 测试把 `--device cpu` 改为 `--device cuda`。该参数要求 CUDA 实际可用。评测 PTT 时在 418M 命令添加 `--baseline-ptt` 并使用单独输出文件。

```text
python -m scripts.benchmark_translation --model m2m100_418M --model-root /path/to/models --device cpu --output m2m100_418M-cpu.json
```

## 接入方案

- **M2M100 1.2B**。在单独任务中让 M2M 适配器接受模型子目录，复用现有语言码、解码与 PTT 回退行为。在 `default_model_manager` 中调用 `register_translation("m2m100_1.2B", factory)`，选用时设置 `settings.translation_model = "m2m100_1.2B"`。另行加入模型目录与下载清单。原始 PyTorch 权重为 4.96 GB。 [原始权重文件](https://huggingface.co/facebook/m2m100_1.2B/blob/main/pytorch_model.bin)
- **NLLB 600M**。实验适配器实现 `device`、`translate` 和 `translate_to_english_details`，接入名及 `settings.translation_model` 值为 `nllb-200-distilled-600M`。中英文映射为 `zho_Hans` 和 `eng_Latn`，支持其它现有语种前需逐个验证语言映射。模型卡把它定位于研究，许可证含非商业限制。 [模型卡](https://huggingface.co/facebook/nllb-200-distilled-600M) · [语言码和推理接口](https://huggingface.co/docs/transformers/model_doc/nllb)
