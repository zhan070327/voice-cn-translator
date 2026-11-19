# 公开测试版：发布与收集反馈

这是一款 Windows 10/11 64 位的本地测试软件，**尚未完成目标 Windows 环境的安装包验证**。先用自己的 Windows 电脑验证构建产物，再提供给测试者。不要把源代码压缩包当作普通用户的下载包。

**版本提示：**GPU 兼容修复版 `v0.3.0b2` 已在 [GitHub Releases](https://github.com/zhan070327/voice-cn-translator/releases/tag/v0.3.0b2) 发布；现有 QQ 闪传与百度网盘文件夹仍是旧安装包 `v0.3.0b1`，直到重新上传前不要把它们称为 GPU 修复版。离线模型 ZIP 可继续使用，无需重下。

## 给测试者的最短步骤

1. GPU 修复版安装包从 [GitHub 直链](https://github.com/zhan070327/voice-cn-translator/releases/download/v0.3.0b2/VoiceCNTranslator_Beta_Setup.exe) 下载。原来的 [QQ 闪传文件夹](https://qfile.qq.com/q/E6R6LgAwrQ) 和[百度网盘备用链接](https://pan.baidu.com/s/1hN8dXZoCHIIR6w2K7SsNcw?pwd=r9mw)（提取码 `r9mw`）中，模型 ZIP 仍可使用，但安装包尚未替换为 `v0.3.0b2`。双击新版 EXE 安装，从桌面快捷方式打开；不需要安装 Python。国内访问 GitHub 可能失败，需等待新版安装包重新上传 QQ 闪传。
2. 将模型 ZIP “全部解压”，确认解压目录里有 `whisper-small` 和 `m2m100_418M` 两个文件夹。在软件中点击“从文件夹导入”，选择包含这两个文件夹的上级目录，不要选择 ZIP 或只选其中一个子文件夹。当前“一键下载模型”直接连接 Hugging Face，国内网络不保证可用；导入完整模型后可离线运行，软件运行时不上传语音或字幕。
3. 选择自己听游戏声音的“系统声音：耳机/扬声器”，先点“测试字幕（20 秒）”，确认能看到字幕窗口，再点“开始翻译”。
   使用 GPU 修复版测试时，确认首页版本为 `0.3.0b2`，并在开始后查看“实际运行设备”：目标是“识别 CUDA · 翻译 CUDA”。若其中一项显示 CPU 或再次出现 CUDA 错误，请发送截图和“复制反馈信息”；不能仅凭模型已导入就判断 GPU 正常。
4. 遇到问题时点“复制反馈信息”，打开抖音私信开发者，粘贴并发送，再补充队友实际说的话、软件翻译结果和期望译文；报错时可附截图。按钮只复制到剪贴板，**不会自动发送反馈**。也可以在“翻译历史”里修正译文、导出 CSV。发送前请检查是否包含不想公开的对话文字。

如果状态显示 `CUDA error: no kernel image is available for execution on the device`，模型已经加载，失败发生在显卡识别或翻译阶段。现有安装版请先点“停止翻译”，到“详细设置”把“运行设备”改为“CPU”，再点“开始翻译”；CPU 会更慢。源码已补充识别和翻译的 CUDA 运行失败时自动重载到 CPU 的处理，但需要发布新版安装包后测试者才能用到。

## 旧版 QQ 闪传说明（仅适用于 v0.3.0b1，暂勿发给 GPU 故障测试者）

> 下载链接：https://qfile.qq.com/q/E6R6LgAwrQ  
> 请下载里面的两个文件：`VoiceCNTranslator_Beta_Setup.exe`（安装包）和 `VoiceCNTranslator_Models_small_M2M100.zip`（离线模型）。先双击 EXE 安装软件，不用安装 Python；再把模型 ZIP 全部解压。打开软件，点“从文件夹导入”，选择同时包含 `whisper-small` 和 `m2m100_418M` 两个文件夹的上级文件夹。选择耳机/扬声器的“系统声音”，先点“测试字幕（20 秒）”，再点“开始翻译”。有问题请点“复制反馈信息”，在抖音私信发给我，并附截图和正确译文。游戏内覆盖有 Riot 政策及反作弊风险，建议先在桌面或第二屏测试。  
> 如果 QQ 闪传链接失效，备用百度网盘：https://pan.baidu.com/s/1hN8dXZoCHIIR6w2K7SsNcw?pwd=r9mw ，提取码：r9mw。

你可以在抖音只征集自愿测试者，私聊发送安装包直链，不在视频或简介里放下载链接。GitHub Releases 更新通道已经配置；以后发布更高版本时可按[更新发布指南](UPDATE_RELEASE_GUIDE_CN.md)操作。公开仓库虽然不在抖音宣传，知道地址的人仍可下载；不要把这种方式描述成仅受邀者可访问。更新按钮只在用户点击时联网，不影响模型下载完成后的离线翻译。

模型使用 `%LOCALAPPDATA%\ValorantTranslator\models`，同一模型在普通软件更新时不需要再次下载；从旧 NLLB 版升级时需下载一次 M2M100。历史记录使用 `%LOCALAPPDATA%\ValorantTranslator\history.sqlite3`，可在软件中关闭保存或删除。卸载程序不会自动删除模型与历史，防止误删用户数据。

## 生成 Windows 下载包

在自己的 Windows 项目目录双击 `make_public_installer_windows.bat`。它会安装缺少的 Python 依赖、用 PyInstaller 构建程序、执行打包后的启动自检，并生成 `dist\ValorantTranslator_Portable.zip`。若本机有 `winget`，脚本还会尝试安装 Inno Setup 6，编译单文件 `dist\VoiceCNTranslator_Beta_Setup.exe`。**制作安装包的人仍需要 Python 3.11/3.12 和网络；下载安装包的测试者不需要 Python。** 如果自动安装 Inno Setup 失败，按脚本显示的官方链接安装 Inno Setup 6，再双击脚本。不要给测试者发送源码 ZIP 或制作脚本。

公开 GitHub 仓库 [zhan070327/voice-cn-translator](https://github.com/zhan070327/voice-cn-translator) 已上传源码。`.github/workflows/windows-beta.yml` 的 Windows 构建已通过 35 项自动测试、打包后启动自检和安装器编译；预览版 Release `v0.3.0b2` 已附上[可免登录下载的安装包](https://github.com/zhan070327/voice-cn-translator/releases/download/v0.3.0b2/VoiceCNTranslator_Beta_Setup.exe)。真实 Windows 安装、游戏音频和测试者显卡 GPU 运行尚未验证。

正式对外推广前请在一台干净的 Windows 10/11 电脑验证：安装、首次模型下载或导入、系统声音设备选择、字幕预览、真实语音、断网重启、卸载和再次安装。已有 Windows CI 构建结果和安装包，但这些用户电脑上的步骤尚未验证，不能说成已通过。

## 抖音介绍文案草稿

> 我在做一个免费的本地队友语音翻译测试版，支持测试英语、马来语、印地语、泰米尔语、泰语转简体中文字幕。Windows 10/11 可用，首次要下载约 2.5 GB 本地模型，之后不用联网；不需要注册，也不上传语音。当前仍有漏识别和错译，希望找几位愿意测试的朋友反馈实际听到的话、识别原文、中文译文和延迟。下载地址与使用说明：〔填入验证后的链接〕。本工具不是 Riot 官方软件；游戏内实时字幕可能有第三方软件和反作弊风险，建议先在桌面或第二屏测试。

## 反馈格式

- Windows 版本、显卡型号、软件“实际运行设备”显示 CUDA 还是 CPU。
- 所选音频设备、识别模型和识别语言（自动/锁定）。
- 实际听到的话、软件识别原文、中文字幕、希望的正确译法。
- 识别、翻译和音频结束至显示的耗时；“处理进度”提示。
- 问题属于安装、漏识别、错译、延迟、音频设备或字幕窗口哪一类。

只收集测试者自愿发送的反馈。未经同意不要公开队友语音录音。软件没有自动上传或遥测功能。

## 发布前需确认的限制

实时字幕版现在改用 [M2M100 418M 模型](https://huggingface.co/facebook/m2m100_418M)，模型页标注 MIT。旧版 NLLB 不再处于下载或推理路径，也不会进入新版源码包。旧用户仍需下载新的 M2M100 模型；旧 NLLB 文件留在本机不会被新版使用。许可证问题按当前模型页与依赖的许可证重新核对；**模型许可合适不代表翻译质量达标，也不解决 Riot 政策问题**。side chat 的录音文件测试版已有小样本实测，显示部分语言和报点误译；实时版换模型后尚未在 Windows 复测。

[Riot 的 VALORANT 政策](https://developer.riotgames.com/docs/valorant)把部分可能立即改变玩家行为的游戏内实时覆盖层列为未获批准示例；本程序的实时语音翻译是否属于该类并未得到 Riot 确认。不要声称获得 Riot 批准，也不要建议绕过 Vanguard。安装程序目前未签名，可能触发 Windows 来源提示；公开分发前应评估代码签名和来源验证。

具体做法：使用 [给 Riot 的用途说明草稿](safe_public_beta/RIOT_QUERY_DRAFT_CN.md) 说明音频来源、窗口机制、无上传和不读取游戏数据，并分别询问桌面/第二屏及游戏覆盖两种使用方式。草稿尚未发送，也没有官方答复；软件现有“游戏窗口覆盖（实验）”不能写成安全或已获批准。
