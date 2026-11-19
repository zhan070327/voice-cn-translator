# 在 Mac 上构建和发布 Windows 安装包

## 构建

1. 在 Mac 更新 `valorant_translator/version.py`、`installer/ValorantTranslator.iss` 和 `release_notes.txt`。已经分发 b4，正式发布从 **0.3.0b5** 开始。提交已审核的源码，把更改合入 GitHub 的 `main`。
2. 打开 [Windows beta build](https://github.com/zhan070327/voice-cn-translator/actions/workflows/windows-beta.yml)，点 **Run workflow**，选择分支 `main` 和 `domestic`。初次运行也可选包含工作流改动的测试分支。构建作业无需 COS 密钥。
3. 运行成功后，在页面底部 **Artifacts** 下载 `windows-domestic-运行ID-尝试次数`。解压拿到 `VoiceCNTranslator_Beta_Setup.exe`、`windows_build.json`、`domestic_build.json` 和 `release_notes.txt`。完整打包与安装日志在 `windows-logs-…`。运行 ID 是地址末尾的数字。安装包产物保留 3 天，日志保留 7 天。重要产物应及时下载备份。

云端沿用主项目 BAT，安装 Windows x64 Python 3.11、官方 PyTorch 2.6.0+cu124、PyInstaller 6 和 Inno Setup 6。无 GPU 构建会验证 CUDA wheel 和 DLL，随后在运行器上静默安装并运行安装后的 EXE 自检。GPU 推理仍需有兼容 NVIDIA 驱动的 Windows 设备确认。运行器不下载模型，也不生成便携 ZIP。每次打印实际剩余磁盘，预装环境前至少 12 GiB，预装后至少 5 GiB。构建过程会禁用 pip 缓存，在静默安装前回收解包的目录空间。如果磁盘检查仍失败，先从日志确认是哪一关不足，再选择仓库已配置的更大 Windows x64 runner（可能付费）或接入有足够磁盘的 Windows x64 自托管 runner，只需要修改工作流的 `runs-on`。标准运行器硬件规格见 [GitHub 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

## 国内发布一次性配置

1. 在仓库 **Settings → Environments** 创建 `cos-production`，限制部署分支为 `main`。启用 **Required reviewers** 并选择负责审批的账号。只有自己审批时，保持 **Prevent self-review** 关闭。
2. 在该 Environment 内添加 `COS_SECRET_ID` 和 `COS_SECRET_KEY`。临时凭证另加 `COS_SESSION_TOKEN`。使用权限仅覆盖指定存储桶的更新清单和版本对象的 CAM 凭证，通过 GitHub 的 Secrets 界面输入。
3. COS 需要让客户端匿名读取广州域名下的 `latest.json`、版本 EXE 和已有模型 ZIP。发布脚本会检查模型及新 EXE 的公开响应。

## 国内每次发布

先下载并核对 EXE、版本、SHA-256、日志。在有 Windows 的测试者或测试环境完成实机检查后，打开 [Publish domestic Windows release](https://github.com/zhan070327/voice-cn-translator/actions/workflows/windows-cos-release.yml)，选 `main`，输入成功构建的运行 ID、版本号（如 `0.3.0b5`）及确认语 `PUBLISH 0.3.0b5`。配置审批后，在运行页点 **Review deployments**，选择 `cos-production` 并批准。

发布流程重新核对构建提交、安装自检、SHA-256 和大小。它先确认已有模型可公开读取，再上传对应版本 EXE 到 `releases/版本号/` 并核验公开 HTTPS 响应，最后才更新根目录 `latest.json`。现有 b4 保持原路径，模型 ZIP 继续使用 COS 上已有的对象。

如果 Actions 产物过期但你已下载同一组文件，可在 Mac 按 [COS 发布指南](DOMESTIC_UPDATE_RELEASE_CN.md) 使用已有的 `scripts.publish_cos` 从本地发布。必须保留与该安装包对应的 `domestic_build.json` 及同版本源码。

## 国际发布

同一个 Windows 构建工作流选择 `github`。需要协调发布时，先确认同版本已通过 COS 正式发布，在 Mac 上运行 `.venv/bin/python -m scripts.verify_domestic_release --notes-file release_notes.txt`。随后在 [GitHub Releases](https://github.com/zhan070327/voice-cn-translator/releases/new) 创建新的 `v版本号` 预发布，目标选择该次构建的提交，附上国际版的同名 EXE，使用相同更新日志。国际客户端继续从 GitHub Releases 检查更新。

## 保证 b4 能接收后续正式更新

- 离开可用的 Windows 电脑前，保留一份已验证的 b4 国内安装包和对应的 `domestic_build.json`。确认 b4 安装版的 `_internal\config\update_channel.json` 为 `domestic`，且清单地址是广州域名的 `https://valorant-translator-update-1535709511.cos.ap-guangzhou.myqcloud.com/latest.json`。
- 确认 b4 首次启动能下载或复用模型。下载完成后断网启动，实际走一轮识别和翻译，查看两者的 CUDA 状态。离开前至少让你的一名 Windows 测试者能复测这些步骤。
- b5 发布后，用保留的 b4 安装版点 **检查更新**，确认它识别 b5、下载校验、覆盖安装并重启，模型和用户设置仍在。代码测试覆盖 b4 解析广州 COS 的 b5 清单，但这一整条升级链需要 Windows 实机验证。清单尚未存在时，b4 检查更新会提示获取失败。

## 费用

当前仓库为公开仓库，GitHub 标准托管 runner 的运行用量免费。私有仓库、超出套餐的存储或使用更大的 runner 可能计费。使用手动触发、短期产物保留且每次只构建一个通道控制成本。具体见 [GitHub Actions 计费](https://docs.github.com/en/actions/concepts/billing-and-usage)。

腾讯云可能按安装包及模型存储、请求和公网下行流量收费。模型下载和用户自动更新通常是主要流量来源。使用广州直连，不重复上传模型。账单和地域单价以 [腾讯云 COS 定价](https://buy.cloud.tencent.com/price/cos) 为准。
