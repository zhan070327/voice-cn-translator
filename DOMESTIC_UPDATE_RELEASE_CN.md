# 腾讯云 COS 国内版自动更新发布指南

## 当前配置与验证边界

- 存储桶：`valorant-translator-update-1535709511`，地域：`ap-guangzhou`。
- 固定更新清单：[latest.json](https://valorant-translator-update-1535709511.cos.ap-guangzhou.myqcloud.com/latest.json)。
- 安装包对象键为 `releases/版本号/VoiceCNTranslator_Beta_Setup.exe`，模型对象键为 `models/VoiceCNTranslator_Models_small_M2M100.zip`。
- 2026-11-19，广州 COS 对 0.3.0b3 EXE 与模型 ZIP 均返回 HEAD 200 和 4 字节 Range 206。大小分别为 1,624,524,847 与 2,428,144,996 字节，类型分别为 `application/x-msdownload` 与 `application/zip`。两件对象的 CRC64 与对应本地文件相符。加速域名返回 400，广州 `latest.json` 尚为 404。
- 默认使用广州直连。模型备用地址可在 `model_bundle.urls` 配置。确认备用 EXE 地址可访问后，可在 `installer_fallback_hosts` 添加白名单域名，发布器会把同一对象路径写入清单供新版重试。保留广州主地址以兼容旧客户端。加速线路和公网流量按腾讯云规则计费，实际速度需在使用地区测试。

`config/update_channel_domestic.json` 是国内版打包配置；`config/update_channel.json` 保持 GitHub 国际版。`make_domestic_installer_windows.bat` 会在打包时暂时启用国内配置，检查打包目录中的实际配置，完成后恢复 GitHub 配置。旧版 GitHub 安装包不会自动切换国内源；需要手动分发首个国内版 EXE 一次。

## 国内外同版本发布顺序

每个版本共用版本号与 `release_notes.txt`。仅用 Mac 时，按 [云端 Windows 构建与发布](CLOUD_WINDOWS_RELEASE_CN.md) 手动构建国内与国际渠道。先验证并确认 COS 发布，再创建相同版本的 GitHub Release。

## Windows：构建国内版

1. 项目版本在 `valorant_translator/version.py` 与 `installer/ValorantTranslator.iss` 中必须一致。当前源码是 `0.3.0b4`。以后发布新版本时两处同时递增。
2. 在 Windows 项目目录运行：

   ```bat
   make_domestic_installer_windows.bat
   ```

   脚本沿用 PyInstaller + Inno Setup、原安装路径和 `AppId`，输出 `dist\VoiceCNTranslator_Beta_Setup.exe`。它还生成仅供发布者本地使用的 `dist\domestic_build.json`，记录版本、SHA-256、大小和国内通道；**不要把这个凭据上传 COS**。如果打包后的配置不对，凭据不会生成。
   构建前先运行 `.venv\Scripts\python.exe -m scripts.check_torch_cuda`：现有 PyTorch 满足 2.6 至 2.x、带 CUDA，且能在本机显卡执行一次 FP16 运算时会直接复用，包括 2.6.0+cu124。只有缺失、CPU 版或版本不符合要求时，才从 [PyTorch 官方 CUDA 12.8 源](https://pytorch.org/get-started/previous-versions/)安装 2.8.0；pip 默认使用本地缓存。GPU/驱动不可用或现有 CUDA 版不支持显卡内核时构建会停止，不会反复下载；此时应按官方指引手动选择兼容的 CUDA wheel。PyInstaller 完成后会检查精确重复的 CUDA DLL，重复超过 256 MiB 时停止打包，避免盲目删除可能必需的运行库。
3. 在 Windows 的干净用户账户安装 0.3.0b4。点“一键下载模型”，下载一部分后暂停或断网，再点一次验证从已有进度继续。等校验、解压完成后断网重启，确认两个模型都显示已下载并能翻译。安装后的测试机无需单独安装 Python、CUDA 或手动解压模型。已有完整模型的账户应直接可用；如果选择了 COS 未提供且本机没有的识别型号，一键下载会切换到 Small。
4. 发布前在隔离的测试环境验证旧版检查更新和覆盖安装。正式清单启用后，再用 0.3.0b3 客户端验证升级至 0.3.0b4、软件重启与配置保留。若先验证旧版的自动更新路径，可用 Windows 单独构建一个指向测试桶的旧版，使用测试清单和同一个已核验的 b4 安装包。

## Mac 或 Windows：dry-run 与上传

修改项目根目录 UTF-8 文本 `release_notes.txt`，写本次更新说明，并随源码一起推送；GitHub Release 与 COS 清单必须使用同一份内容。上传端单独安装腾讯云官方 SDK，客户端 EXE 不包含 SDK 或密钥：

| 系统 | 安装发布依赖 | 无凭证 dry-run | 正式上传 |
|---|---|---|---|
| Windows | `.venv\Scripts\python.exe -m pip install -r requirements_publish.txt` | `.venv\Scripts\python.exe -m scripts.publish_cos --notes-file release_notes.txt --dry-run` | `.venv\Scripts\python.exe -m scripts.publish_cos --notes-file release_notes.txt --upload` |
| Mac | `.venv/bin/python -m pip install -r requirements_publish.txt` | `.venv/bin/python -m scripts.publish_cos --installer "/安装包路径/VoiceCNTranslator_Beta_Setup.exe" --receipt "/凭据路径/domestic_build.json" --notes-file release_notes.txt --dry-run` | 将左侧命令最后的 `--dry-run` 改为 `--upload` |

Mac 发布时，从 Windows 复制**同一次构建**的 EXE 与 `domestic_build.json`，并保持 Mac 项目源码版本相同。先执行 dry-run：它不读取 COS 凭证、不访问网络，只生成 `dist/latest.json`，核对真实安装包 SHA-256、字节数、构建凭据和清单格式。旧 GitHub 安装包或与凭据不匹配的文件会被拒绝。`latest.json` 是给 COS 的公开文件；不要修改生成后的内容。

正式上传只从环境变量读取 `COS_SECRET_ID`、`COS_SECRET_KEY`；临时凭证可再设 `COS_SESSION_TOKEN`。不要将密钥写入源码、命令行参数、聊天或安装包。建议使用只允许该桶所需对象操作的子账号。Windows PowerShell 可用 `Read-Host` 设置当前终端会话环境变量，Mac 终端可用 `read -s` 隐藏输入；操作结束关闭终端即可。SDK 设置 HTTPS 与单次请求超时。

Windows PowerShell（在**同一个窗口**运行上传命令）：

```powershell
$env:COS_SECRET_ID = Read-Host 'COS SecretId'
$secure = Read-Host 'COS SecretKey' -AsSecureString
$env:COS_SECRET_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
```

Mac 终端：

```sh
printf 'COS SecretId: '; read -r COS_SECRET_ID; export COS_SECRET_ID
printf 'COS SecretKey: '; read -r -s COS_SECRET_KEY; printf '\n'; export COS_SECRET_KEY
```

### 上传的自动顺序

1. 读取 COS 上现有 `latest.json` 和目标版本安装包：若已发布相同/更高版本，或版本路径存在不同内容，**拒绝重复发布和覆盖**。若此前上传安装包成功、但尚未更新清单，且 COS 大小与 SHA-256 元数据一致，可从公开下载检查处继续。读取失败且不是 404 时中止。
2. 发布命令先对主模型地址做 HEAD，校验大小和 CRC64。再用 SDK 分块上传 `releases/版本号/VoiceCNTranslator_Beta_Setup.exe`，启用分块 MD5，安装包设置长期缓存。
3. 对 COS 安装包做 HEAD 校验，比较大小与记录的 SHA-256 元数据；再从公开 HTTPS 地址做 HEAD，确认测试者可访问且大小一致。任何一步失败都**不上传**新清单。
4. 再次检查远端版本，最后上传固定地址 `latest.json`，设置 `Content-Type: application/json; charset=utf-8` 和 `Cache-Control: public, max-age=60, must-revalidate`，再读取并比对清单字节。

以后每版上传 COS 的文件只有 **两个**：版本目录里的 EXE 和桶根目录的 `latest.json`。本地 `domestic_build.json` 只用于防止拿错安装包。模型和用户配置、翻译历史、个人词库位于 `%LOCALAPPDATA%\ValorantTranslator`，正常覆盖安装不会删除。

原模型 ZIP 可继续复用。软件安装包会附带模型 MIT 版权与许可，并在自动解压时复制到模型目录。如另外直接分享模型 ZIP，请随附完整的 [第三方许可声明](THIRD_PARTY_NOTICES_CN.md)，尤其是从 COS 向用户提供独立下载时。

## 最简发布命令

Windows 每次构建并实机测试后，准备 `release_notes.txt`，设置当前终端的 COS 凭证，一条命令生成清单、上传该版 EXE 并在核验后发布清单。发布依赖首次用 `.venv\Scripts\python.exe -m pip install -r requirements_publish.txt` 安装。

```bat
.venv\Scripts\python.exe -m scripts.publish_cos --notes-file release_notes.txt --upload
```

第一次发布前，检查桶对象的公开读取权限；`test.txt` 可下载不能自动证明新上传的 `releases/...` 也可匿名下载。上传脚本会做公开 HTTPS HEAD 校验。COS 存储、公网流量及请求可能产生费用，以腾讯云账单为准。

## 测试与已知限制

本地模拟：

```bat
.venv\Scripts\python.exe -m pytest -q tests\test_publish_cos.py tests\test_domestic_updates.py tests\test_updates.py tests\test_gui.py tests\test_dual_release.py
```

测试使用假 COS SDK 客户端和临时文件，覆盖清单生成、dry-run、版本查重、先 EXE 后清单、网络/校验失败不更新清单、现有 GitHub 更新、下载和安装启动。真实 Windows 打包、PowerShell 静默覆盖、真实 COS 上传和公开下载需要在对应环境实际验证；不能把本地模拟当作已发布成功。

安全边界：客户端只接受配置域名下的 HTTPS 地址并验证安装包 SHA-256。当前清单没有独立数字签名；若 COS 写权限被盗，攻击者可能同时改写清单和文件。正式大范围发行前应考虑独立签名及 Windows 代码签名。安装失败时更新脚本尝试重启旧程序并显示错误，但不能保证已覆盖文件完全回滚。

腾讯云 SDK 参考：[上传对象](https://cloud.tencent.com/document/product/436/65820)、[查询对象元数据](https://cloud.tencent.com/document/product/436/65828)、[异常处理](https://cloud.tencent.com/document/product/436/35154)。

## 独立发布国内模型

1. 从 `main` 手动运行 **Whisper COS model assets**，保持 `publish_models` 未勾选。成功后查看打包日志和 `cos-model-assets`，其中有四个 Whisper ZIP、旧客户端兼容的 Small+M2M100 ZIP、大小和 SHA-256。
2. 审阅后再次从同一个 `main` 提交运行工作流，勾选 `publish_models`，输入 `PUBLISH MODELS`，将首轮运行号填入 `package_run_id`。在 `cos-production` 审批。该环境需要 `COS_SECRET_ID` 和 `COS_SECRET_KEY`，临时凭证还需要 `COS_SESSION_TOKEN`。
3. CAM 权限限定在本桶。存储桶级仅需读取版本状态和列出分块上传。对象级给 `models/*` 分块上传、读取与校验权限，另给 `latest.json` 读取及写入权限。模型工作流不需要 `releases/*` 的写权限。
4. 流程核验每个 ZIP 后发布 `models/models.json`，再给 b5 的 `latest.json` 增加模型字段。原清单的安装包字段保留原字节。确认校验任务通过后，在中国 Windows 上用全新的测试账户启动国内或双渠道安装版，选择模型并点「一键下载模型」。
