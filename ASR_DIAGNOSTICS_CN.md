# 0.3.0b5 识别诊断

## 在 RTX 3060 上运行

打开已有源码和 `.venv` 的项目目录，在 PowerShell 执行：

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_asr.py --device both
```

也可用同一条命令的 `--device cuda` 或 `--device cpu` 单独运行。默认查找应用使用的 `%LOCALAPPDATA%\ValorantTranslator\models\whisper-small`。如模型放在别处，加 `--model-dir 'D:\模型目录'`。脚本使用本地文件，缺文件时逐项列出。设备初始化失败会记入报告。

脚本先找 `test_audio/manifest.example.json` 里的英文参考音频 `test_audio/en.wav`。没有时，尝试用 Windows SAPI 已安装的英文语音生成 “Two A main”，保存为 `diagnose_asr_input.wav`。也可以自己录这句，导出 16 kHz、单声道、16-bit PCM WAV，用 `--audio 'D:\test\en.wav'` 指定。短音频用静音补到 3 秒，超过 3 秒的音频会保留原长度。结果写入当前目录的 `diagnose_asr_report.json`。

每个设备先跑应用词库热词，再跑无热词。推理耗时包含完整读取 Whisper 的输出，模型加载时间另列。报告同时保存原始识别结果与应用过滤后的结果。

| RTX 3060 结果 | 下一步 |
| --- | --- |
| 3 秒输入正确识别、通过应用过滤，CUDA 低于 1 秒 | 在应用内采集同一句和实际失败句。核对识别排队、等锁、翻译排队和距音频结束的时间。 |
| 原始文本正确，过滤后为空 | 看段落指标、语种置信度、短词过滤规则以及触发条件。 |
| 原始文本为空或错误，或 3 秒输入 CUDA 超过 2 秒 | 先核对模型文件、CUDA 加载、设备与真实输入。 |
| CUDA 为 1–2 秒 | 未达到目标，结合重复测试和应用日志判断。 |
| 看门狗触发 | 它会标明仍在等待识别锁或 Whisper 推理。只有推理开始及结束时间，才能区分锁等待和推理慢。 |

然后在软件里选正确音源，保持 Small、自动语言和低延迟模式，说一句完整英文。验收目标为 10 秒内显示中文。若丢弃，观察「最近丢弃」并立即点「复制反馈信息」，反馈含最近 10 条事件与逐句分段耗时。发送前检查是否含对话原文。

## 目前已验证

- Mac M3 CPU 对 3 秒合成音频，带热词识别 “Two A Main.”，耗时 2.354 秒，语种置信度 0.906。无热词输出 “to AMAIN.”，耗时 2.161 秒。见 [Mac 诊断报告](test_audio/diagnose_asr_macos_report.json)。
- 同一 WAV 模拟采集进入应用，由实际 VAD、Whisper Small 和词库生成「A 大两个。」并显示在字幕界面。反馈包含同一任务的 VAD、入队、出队、推理、翻译及界面更新记录。这句命中词库，翻译阶段未使用 M2M100 推理。
- Mac 上的 CTranslate2 不支持 CUDA。RTX 3060 的 CPU/CUDA 对照、Windows SAPI、实际麦克风/系统声音和 10 秒实机验收待测。现有证据无法区分原故障的排队过期、过滤和阻塞，保留原超时、VAD、过滤和队列策略。
