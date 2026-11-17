# 本地模型与主要依赖来源

实时字幕版仅在用户点击下载后获取模型；模型文件不打包在安装程序中。以下是 2026-11-16 核对的模型页标注，公开分发前应再次检查原页面和完整许可证文本。

| 项目 | 来源 | 页面标注 |
|---|---|---|
| M2M100 418M 翻译模型 | https://huggingface.co/facebook/m2m100_418M | MIT |
| faster-whisper-small 识别模型 | https://huggingface.co/Systran/faster-whisper-small | MIT |
| OpenCC Python 简繁转换 | https://pypi.org/project/opencc-python-reimplemented/ | Apache Software License |

旧版 NLLB-200 distilled 600M 不再由实时版下载、加载或打包。升级用户本机已有的 NLLB 文件不会被新版读取。其他 Python 依赖仍需在正式发布前完成许可证清单核对，并保留各项目要求的版权与许可证声明。

模型许可与 Riot 对游戏内覆盖层的政策是两个独立问题；模型许可标注为 MIT 不代表游戏内使用获得 Riot 批准。
