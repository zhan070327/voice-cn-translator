# VoiceTrans b5 双渠道发布检查单

- [ ] 从待发布提交运行 Windows beta build，选择 `dual` 和 `both`。两种 EXE 都通过静默安装和启动自检后，下载同一次运行的 GPU、CPU 两个 Artifacts 并分别解压。实机验证 GPU 翻译、纯 CPU 运行、b4 国内版升级、b4 用户数据保留和两种 b5 的各自更新。
- [ ] GPU 用户下载 `VoiceCNTranslator_Beta_Setup.exe`。无兼容 NVIDIA 显卡的用户下载 `VoiceCNTranslator_CPU_Beta_Setup.exe`。GitHub 上两种包都在同一 [Releases 下载页](https://github.com/zhan070327/voice-cn-translator/releases)。COS 使用相同的文件名，按版本保存在 `releases/版本号/`。
- [ ] 在 Mac 的本分支安装发布依赖，确认 `gh auth status` 的账号有仓库 Releases 写权限。COS 凭证按 [现有发布指南](DOMESTIC_UPDATE_RELEASE_CN.md) 在终端会话中设置。
- [ ] 先在 Mac 预检，脚本会验证两个真实 EXE 的 SHA‑256、文件大小、构建来源、版本、更新日志、CPU 体积差，写本地清单。

  ```bash
  python -m scripts.publish_paired_release --gpu "/路径/GPU产物" --cpu "/路径/CPU产物"
  ```

- [ ] 复核后执行同一命令，再加 `--confirm 'PUBLISH 0.3.0b5'`。脚本会先上传并校验两个 COS 安装包，再把同一份 EXE 上传 GitHub 草稿并校验 GitHub 返回的 SHA‑256，最后连续开放 GitHub Release 和 COS `latest.json`。两个服务没有跨服务原子提交。如果最后一步网络中断，保持同一批文件并重跑原命令，再确认两边都可见后通知测试者。
- [ ] 检查 b4 GPU 客户端仍能识别 COS 清单的顶层 GPU 入口。b5 CPU 客户端只使用 CPU 入口。GPU 和 CPU 在两边各自使用同一个安装文件，因此同版同类型的 SHA‑256 完全相同。

## 模型发布

运行 Actions 的 **Whisper COS model assets**。选择上传且具备 COS Secrets 时，会先下载锁定的 Systran 模型版本，只打包四件文件，核验公开的 ZIP，最后写 `models/models.json`。缺少 Secrets 时仍会产出两个 ZIP、清单预览和许可声明。

手动上传时，将两个 ZIP 放在 COS 的 `models/` 内。核对真实对象 URL、大小及原始 SHA‑256 后，再把 `models.preview.json` 发布为 `models/models.json`。已有其他模型条目时需要合并保留。原 Small + M2M100 合包继续使用。若发布 medium，则按同一目录结构上传 medium ZIP、生成真实元数据并加到清单，不需要重发 EXE。

## b4 界面回归对照

| 区域 | b5 保留项 |
| --- | --- |
| 快速开始 | 检查更新、版本、进度与取消、Riot 提示；下载、导入与暂停；音频设备列表、刷新、提示；桌面与游戏字幕模式、20 秒预览、位置调整和隐藏；开始/停止、状态、运行设备、音量、处理进度；最近中文、识别原文/语言、三段延迟、失败数、音频设备、复制反馈。 |
| 详细设置 | 双入口检查更新、版本/更新日志；识别语言、四种 Whisper 型号、M2M100、自动/GPU/CPU、低延迟；显示器、X/Y、字号、不透明度、原文、历史、穿透、Ctrl+Shift+T、VAD、翻译超时、字幕过期与时长。CPU 安装包固定使用 CPU，避免调用 CUDA。 |
| 翻译历史 | 五列表格、刷新、修正、导出 CSV、清空、清空含修正。只保留最近 1000 条、不保存音频、修正优先。 |
| 字幕与反向 | 桌面、游戏置顶以及拖动模式。最近两条、淡出、按设置显示原文。音频源无说话人编号时只标“队友”。穿透时从主窗口复制英文，关闭穿透时字幕条也提供复制按钮。 |
| 下载与更新 | COS 续传、暂停、SHA‑256 校验和目录导入；国际 Hugging Face 路径保留。两种安装包独立更新。COS 可静默安装，GitHub 打开安装程序。 |
