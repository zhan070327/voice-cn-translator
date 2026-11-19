# b5 云端发布与权限核对

## 先验证和合并

1. 合并集成分支前，看 **Verify and publish b5 CPU and GPU** 的 verify 作业和 `b5-verified-manifest-preview`。该作业只读下载 Windows Run **44600127347** 的整合版 GPU 和 CPU Artifacts（分别为 `windows-dual-gpu-44600127347-1`、`windows-dual-cpu-44600127347-1`），复算两件安装包的哈希/大小，读取原 b4 解析器验证预览清单。适用构建提交 **8cddb5c3c1796ca6685946add7614311d1852259**、尝试 1。预览不会写入 COS 或 GitHub Release。
2. 与实机测试者共用同一批 Artifacts。确认测试和审核后，可合并到 `main`。默认分支已有关键的 b4 上传修复，保留独立的一次性 b4 工作流。旧的单渠道发布流程不用于 b5。
3. 这批 EXE 的 GitHub Artifacts 默认只保留 3 天，过期后 Actions 无法直接取回。需要重新构建 `dual / both` 并在 `.github/actions/load-b5-build/action.yml` 更新核准的 run、attempt、提交和两个 Artifact ID，再做验证。发布 workflow 的约束不会自动改用其他安装包。

## 实机放行清单

- 用有 NVIDIA 显卡的 Windows 安装 GPU 包，实播音频，确认 Whisper 与 M2M100 两项确实走 CUDA。
- 用无 NVIDIA GPU 的 Windows 使用 CPU 包，运行一次正向识别和翻译。用 b5 两种安装分别验证检查更新与同型号安装包选择。
- 实测按住 Ctrl+Shift+Z 录中文、松开生成英文和复制。实测 Ctrl+Shift+T、字幕上屏、拖动、鼠标穿透、淡出与焦点。
- 使用原有 b4 国内 EXE 验证实际下载、SHA-256、静默覆盖、重启，以及模型、用户词库、翻译历史、音频与字幕配置保留。解析通过不能替代此项。若已安装的老 b4 仍携带无 BOM 的 PowerShell 脚本，在某些 Windows 语言环境中首次升级可能需要手动运行 b5 GPU 安装包。

## 已验证后正式发布

在 `main` 手动运行 **Verify and publish b5 CPU and GPU**，选 `publish`，输入 `PUBLISH 0.3.0b5`，审核 `cos-production`。该作业直接在云端核验并上传两件 EXE、校对 GitHub 草稿的两个同名文件和对应 SHA-256，依次公开 GitHub Release 及 COS 根目录 `latest.json`。两者之间无跨服务原子提交，失败后用同一批产物重跑。b4 只读顶层 GPU 条目，b5 CPU 使用 `editions.cpu`。模型清单 和安装包更新清单由不同流程维护。

模型工作流 **Whisper COS model assets** 默认只产出 ZIP。启用发布并输入 `PUBLISH MODELS` 后，由独立作业经 `cos-production` 审批，验证同一运行的 ZIP、SHA-256 和内容，再上传与写 `models/models.json`。两件 ZIP 校验不全或发现另一发布修改了清单时会中止。原 Small + M2M100 包不变。

上传使用 32 MiB 分块、四路并发、逐块 MD5 和 CRC64、对临时错误退避重试、按块日志、禁止覆盖及完成后再次比对 COS 和公开 HTTPS HEAD。已存在且同内容的对象跳过传输。出错尝试 Abort 清理该次上传。若 runner 被强制终止，可按桶生命周期规则清理未完成分块。遇到不可访问的旧版本对象时不要放宽校验。

## CAM 最小权限

把策略附到 `cos-production` 已使用的 CAM 子用户。先保留 b4 现有授权，再按需添加以下增量。切勿替换为全桶或管理员权限。SDK 使用禁止覆盖，需要现有的桶级 `GetBucketVersioning` **只读**权限。仅在该桶版本控制从未启用时执行。两套 b5 EXE 使用与模型 ZIP 相同的分块/清理动作及 `HeadObject`，分别限定到 `releases/0.3.0b5/VoiceCNTranslator_Beta_Setup.exe` 和 `releases/0.3.0b5/VoiceCNTranslator_CPU_Beta_Setup.exe`。正式更新清单只额外需要根 `latest.json` 的 `GetObject` / `PutObject`。未来版本应新增下一版各自的对象键。

新增模型的 CAM 策略示例

```json
{
  "version": "2.0",
  "statement": [
    {
      "effect": "allow",
      "action": [
        "name/cos:HeadObject",
        "name/cos:InitiateMultipartUpload",
        "name/cos:UploadPart",
        "name/cos:CompleteMultipartUpload",
        "name/cos:AbortMultipartUpload"
      ],
      "resource": [
        "qcs::cos:ap-guangzhou:uid/1535709511:valorant-translator-update-1535709511/models/whisper-tiny.zip",
        "qcs::cos:ap-guangzhou:uid/1535709511:valorant-translator-update-1535709511/models/whisper-base.zip"
      ]
    },
    {
      "effect": "allow",
      "action": ["name/cos:GetObject", "name/cos:PutObject"],
      "resource": [
        "qcs::cos:ap-guangzhou:uid/1535709511:valorant-translator-update-1535709511/models/models.json"
      ]
    }
  ]
}
```

该脚本不枚举上传或对象，因此无需为模型新增 `ListBucket`、`ListParts`、`ListMultipartUploads` 或 `DeleteObject`。另核对客户端对指定 ZIP 和清单可匿名读取，保持写入只对 CAM 子用户开放。腾讯云参考：[分块上传授权](https://cloud.tencent.com/document/product/436/64991)、[禁止覆盖](https://cloud.tencent.com/document/product/436/7742)、[CRC64 校验](https://cloud.tencent.com/document/product/436/62808)。
