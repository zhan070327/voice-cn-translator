# Windows 国内版构建修复补丁（0.3.0b3）

把 `Windows_Build_Fix.zip` 解压到**含 `main.py` 的原项目根目录**，保留 ZIP 内相对路径并覆盖同名文件。不要解压到 `.venv`、`dist` 或另一个新文件夹。补丁不含模型、虚拟环境和安装包；现有 `.venv` 会继续使用。

在该项目目录双击 `make_domestic_installer_windows.bat`，或在 CMD 中执行唯一命令：

```bat
make_domestic_installer_windows.bat
```

脚本会先检查现有 CUDA PyTorch。2.6.0+cu124 如果能执行 GPU FP16 运算，会直接复用。随后依次校验国内更新通道、版本、图标、PyInstaller 输出、自检、CUDA DLL、Inno Setup 安装包及国内版更新配置；失败时会恢复原更新通道。

每次运行会打印 `build_logs\domestic_build_*.log` 的**完整路径**。终端最后显示日志末尾 25 行；完整错误和构建输出保存在日志中。成功时末尾显示 `VERSION`、`INSTALLER` 的绝对路径和 `SIZE_BYTES`。安装包仍位于 `dist\VoiceCNTranslator_Beta_Setup.exe`。发布前仍须在 Windows 实机安装和启动验证；本补丁在 Mac 上只能完成静态检查与 Python 测试。

如果看到 GPU 内核不兼容，构建会停止，避免重复下载；请根据日志查明显卡与官方 PyTorch CUDA wheel 的匹配情况。不要删除 `.venv` 或模型目录。日志中若包含本机用户名或路径，对外发送前请自行检查。

## 后续同步与打包

Mac 上提交改动后，在项目根目录运行 `git archive --format=zip --output=Valorant_Windows_Update.zip HEAD`。把 ZIP 解压到 Windows 原项目根目录并覆盖同名源码。保留机器上已有的个人配置、模型和 `.venv`。如果手动改过 `config/update_channel.json` 或 `config/glossary.json`，覆盖前先备份并按需合并。

在 Windows 项目根目录运行 `make_domestic_installer_windows.bat`。如需发布新版，先让程序和 Inno Setup 的版本号一致，更新 `release_notes.txt`，再按 [COS 发布指南](DOMESTIC_UPDATE_RELEASE_CN.md) 完成发布。

继续沿用已验证的国内入口、环境预检、PyInstaller 自检和 Inno Setup 流程。只为具体问题修改 BAT/CMD，保持 ASCII 无 BOM 和 CRLF，再运行 `tests/test_windows_build_scripts.py` 中的回归测试。
