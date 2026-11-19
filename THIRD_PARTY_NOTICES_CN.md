# 本地模型与主要依赖来源

实时字幕版仅在用户点击下载后获取模型，模型文件由安装包外部分发。以下为 2026-11-19 核对的模型卡。国内版在每个新安装的模型目录中保存本说明。

| 项目 | 来源 | 页面标注 |
|---|---|---|
| M2M100 418M 翻译模型 | https://huggingface.co/facebook/m2m100_418M | MIT |
| faster-whisper-small 识别模型 | https://huggingface.co/Systran/faster-whisper-small | MIT |
| OpenCC Python 简繁转换 | https://pypi.org/project/opencc-python-reimplemented/ | Apache Software License |

旧版 NLLB-200 distilled 600M 不再由实时版下载、加载或打包。升级用户本机已有的 NLLB 文件不会被新版读取。其他 Python 依赖仍需在正式发布前完成许可证清单核对，并保留各项目要求的版权与许可证声明。

模型许可与 Riot 对游戏内覆盖层的政策是两个独立问题；模型许可标注为 MIT 不代表游戏内使用获得 Riot 批准。

## 模型版权与 MIT 声明

- M2M100 418M（原项目 [fairseq](https://raw.githubusercontent.com/facebookresearch/fairseq/main/LICENSE)）：Copyright (c) Facebook, Inc. and its affiliates.
- Whisper small（原项目 [Whisper](https://raw.githubusercontent.com/openai/whisper/main/LICENSE)）：Copyright (c) 2022 OpenAI
- faster-whisper 的 CTranslate2 转换与相关代码（[SYSTRAN](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/LICENSE)）：Copyright (c) 2023 SYSTRAN

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
