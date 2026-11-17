# 私聊发测试包，软件内检查更新

当前更新功能已加入源码，仓库 [zhan070327/voice-cn-translator](https://github.com/zhan070327/voice-cn-translator) 已创建为**私人仓库**，但尚未上传源码、发布 Release，也没有在 Windows 上构建并验证这个新版安装程序。私人仓库的 Release 无法让无 GitHub 登录的测试者检查更新；等准备发测试包时，需要将仓库改为公开并完成 Windows 实测。不要把本源码压缩包发给普通测试者当安装包。

## 测试者会怎么用

你先私聊发送第一次安装包 `VoiceCNTranslator_Beta_Setup.exe`。测试者安装一次、下载本地模型。以后你发布更高版本后，他们在首页点“检查更新”，确认更新说明，软件会下载安装包、校验 GitHub Release 提供的 SHA-256，关闭翻译器并打开 Windows 安装程序。安装程序仍会显示普通安装界面，不会静默安装。

模型和历史记录在 `%LOCALAPPDATA%\ValorantTranslator`，安装程序在 `%LOCALAPPDATA%\Programs\VoiceCNTranslator`。正常覆盖安装不需再次下载同一模型；如果将来你换模型种类，可能需要再下载新模型。软件仅在用户点击“检查更新”时访问 GitHub，不上传语音、识别原文或翻译历史。

**第一次要手动发新版：** 测试者现有的旧 EXE 没有“检查更新”功能，必须先安装一次包含该功能的新 EXE。之后才可以在软件里更新。

## 发布者首次设置

1. 已创建私人仓库 `zhan070327/voice-cn-translator`。现在保持私人，不在抖音公开下载链接。准备好给测试者使用软件内更新时，再改为公开；公开仓库和 Release **不是访问控制**，知道地址的人仍可下载。不要上传语音、历史数据库、模型文件或个人密钥。
2. 当前项目的 `config/update_channel.json` 已填入仓库名。若以后更换仓库，在你自己的 Windows 项目目录运行：

   ```bat
   .venv\Scripts\python.exe -m scripts.configure_updates https://github.com/zhan070327/voice-cn-translator
   ```

   它把仓库名写入 `config\update_channel.json`。**必须在打包前写好**；已发出的 EXE 不会因你后来修改源码目录而改变。
3. 用 `make_public_installer_windows.bat` 构建安装包，先在自己的 Windows 电脑验证“打开软件”“检查更新”“模型仍在”。`build_windows.bat` 会检查程序版本与安装脚本版本是否一致，并把更新仓库配置打进 EXE。
4. 在仓库的 Releases 创建第一个 Release。Tag 填 `v0.3.0b1`，可以标为 **Pre-release**；上传文件名完全相同的 `VoiceCNTranslator_Beta_Setup.exe`。填写简短更新说明。把这个已验证的安装包私聊发给测试者。GitHub 的 Release API 会提供安装包大小与 SHA-256 摘要；若缺少摘要或文件名不对，程序会拒绝自动安装。

## 以后每次更新

1. 在 `valorant_translator/version.py` 和 `installer/ValorantTranslator.iss` 中把版本同时改成更高的版本，例如 `0.3.0b2`。Tag 使用 `v0.3.0b2`。构建脚本会阻止两个版本不一致的打包。
2. 在 Windows 运行自动测试与 `make_public_installer_windows.bat`，验证新 EXE 的启动、音频、字幕及模型保留。
3. 创建新的 GitHub Release（可继续标为 Pre-release），附上新版 `VoiceCNTranslator_Beta_Setup.exe`。**不要只上传源码 ZIP 或便携版 ZIP**；更新按钮只认安装版 EXE。保持仓库名、安装程序文件名和 Inno Setup 的 `AppId` 不变。
4. 让测试者在旧版首页点“检查更新”。若下载中断或校验失败，会提示错误，不会运行不完整安装包；重试即可。

如果 GitHub 无法访问、Release 还没发布、仓库地址未配置或没有比当前更高的版本，按钮会显示相应结果。GitHub 私有仓库的 Release 不能免登录获取；本功能只面向你选择的**知道地址即可下载**方式，不会把 GitHub 访问密钥打进 EXE。

## 当前验证边界

本机 macOS 自动测试覆盖了版本比较、预发布版本、错误地址拒绝、大小与 SHA-256 校验、缓存和失败清理。Windows 安装程序的下载、关闭旧程序、覆盖安装及打开新版**未验证**。你建立仓库并完成首次 Windows 构建后，需用自己的电脑或一位自愿测试者做一次从旧版到新版的完整升级，再发给更多人。
