# SameFreq Cloud 部署

桌面端默认使用本地平衡档。用户在详细设置登录并选择极速后，客户端先查权益，再上传语音片段或待翻译文字。服务端将请求转交给 Azure。API 不保存原始语音、待翻文字或译文。

## 本地验证

在仓库根目录用 Python 3.11 或更新版本运行。

```bash
python3 -m venv server/.venv
source server/.venv/bin/activate
python -m pip install -r server/requirements.txt pytest
python -m pytest -q server/tests
```

测试启动 FastAPI 应用，使用临时 SQLite、测试生成的 RSA 密钥和模拟的 Azure、微信及支付宝网关。它们覆盖实际请求格式与签名核验路径。真实 Azure 资源、国内网络连通性、商户联调和游戏内延迟需要另行实测。

## 环境变量

由 HAOYU 在部署时提供下列必填变量。缺项会阻止启动，并列出缺失变量名。PEM 可以是多行，也可以用字面量 `\n` 表示换行。

| 变量 | 用途 |
| --- | --- |
| `JWT_SECRET` | JWT HS256 签名密钥，至少 32 字节，可用 `openssl rand -hex 32` 生成 |
| `PUBLIC_BASE_URL` | 对外 HTTPS 基址，用于支付通知，网关需能访问 |
| `AZURE_SPEECH_KEY` | Azure Speech 资源密钥 |
| `AZURE_SPEECH_RESOURCE` | Azure Speech 资源短名 |
| `AZURE_TRANSLATOR_KEY` | Azure Translator 资源密钥 |
| `AZURE_TRANSLATOR_REGION` | Translator 资源实际区域，例如 `chinaeast2` |
| `WECHAT_APP_ID` | 微信应用 ID，需与商户绑定 |
| `WECHAT_MERCHANT_ID` | 微信支付商户号，开通 Native 支付 |
| `WECHAT_MERCHANT_SERIAL` | 微信商户 API 证书序列号 |
| `WECHAT_MERCHANT_PRIVATE_KEY` | 微信商户 API RSA 私钥 PEM，用于签名 |
| `WECHAT_API_V3_KEY` | 微信 API v3 密钥，32 字节，用于解密通知 |
| `WECHAT_PLATFORM_SERIAL` | 微信支付公钥 ID 或平台证书序列号 |
| `WECHAT_PLATFORM_PUBLIC_KEY` | 与上项匹配的微信支付公钥 PEM。使用平台证书时，可通过 `openssl x509 -pubkey -noout -in platform.pem` 提取公钥 |
| `ALIPAY_APP_ID` | 支付宝应用 ID，开通当面付 |
| `ALIPAY_SELLER_ID` | 支付宝收款方账号对应的 PID |
| `ALIPAY_APP_PRIVATE_KEY` | 支付宝应用 RSA2 私钥 PEM |
| `ALIPAY_PUBLIC_KEY` | 支付宝平台公钥 PEM，用于响应及通知验签 |

| 可选变量 | 默认值及用途 |
| --- | --- |
| `DATABASE_PATH` | `server/samefreq.sqlite3`，生产环境设为服务用户专有的可写路径 |
| `PLANS_FILE` | `server/plans.example.json`，默认套餐停用，价格待 HAOYU 定价 |
| `AZURE_CLOUD` | `china`，决定 Speech 和 Translator 域名。保留 `global` 配置项 |
| `AZURE_SPEECH_URL` | 可覆盖所选资源的完整 HTTPS Fast Transcription 路径，不含查询参数 |
| `AZURE_SPEECH_API_VERSION` | `2025-10-15`，可按实际资源支持的版本配置 |
| `ONLINE_REQUESTS_PER_MINUTE` | 每账号在线请求数上限，默认 90 |
| `GLOBAL_ONLINE_REQUESTS_PER_MINUTE` | 全站在线请求数上限，默认 600 |
| `AUTH_REQUESTS_PER_MINUTE` | 每 IP 及每邮箱认证请求数上限，默认 12 |
| `ORDERS_PER_MINUTE` | 每账号下单上限，默认 5 |
| `TRANSLATION_MS_PER_CHARACTER` | 每个送译字符折算的等效试用毫秒数，默认 100，可按运营调整 |

中国区 Translator 域名为 `api.translator.azure.cn`。Speech Fast Transcription 的实际可用区域及版本需用 HAOYU 的 Azure 中国资源验证。Azure 中国主权云文档和 Fast Transcription API 文档可用于排查端点、版本和能力。 [Azure 主权云语音](https://learn.microsoft.com/zh-cn/azure/ai-services/speech-service/sovereign-clouds) [Fast Transcription REST API](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text) [Translator 主权云](https://learn.microsoft.com/zh-cn/azure/ai-services/translator/reference/sovereign-clouds)

## VM 启动与 HTTPS

将仓库部署到普通 Linux VM。使用权限为 `0600` 的环境文件，由服务管理器导入变量。配置文件与 SQLite 数据放在应用源码目录外。设置应用服务和数据库访问权限后，从仓库根目录启动。

```bash
server/.venv/bin/python -m uvicorn server.app:create_app --factory \
  --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

可用 systemd 维持进程，设置 `WorkingDirectory`、`EnvironmentFile`、非 root 的 `User`、`UMask=0077`、`Restart=on-failure`，并将上面的命令写入 `ExecStart`。用 Nginx 终止 TLS，签发该域名证书并启用续期。下例使用占位域名和路径。

```nginx
server {
    listen 443 ssl;
    server_name cloud.samefreq.example;
    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;
    client_max_body_size 1100k;
    client_body_timeout 10s;

    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_pass http://127.0.0.1:8000;
        proxy_read_timeout 15s;
    }
}
```

通过 `/healthz` 检查进程。客户端退出后在本机 `config.json` 设置 `cloud_url` 为 VM 的 HTTPS 基址。本地联调可以设为 `http://localhost:8000` 或 `http://127.0.0.1:8000`。Uvicorn 信任本机反代设置的 IP，Nginx 覆盖来访请求携带的转发头。SQLite 使用 WAL，备份用 SQLite 在线备份接口。

## API

除注册、登录、刷新、注销、支付回调和健康检查外，请求携带 `Authorization: Bearer <access_token>`。JSON 请求使用 `application/json`，JSON POST 请求设置 `Content-Length`。业务错误返回 `{"code":"…"}`。

| 方法与路径 | 请求与用途 |
| --- | --- |
| `POST /v1/auth/register` | `email`、`password`，注册并发放令牌，注册密码至少 10 字符 |
| `POST /v1/auth/login` | `email`、`password`，返回访问和刷新令牌 |
| `POST /v1/auth/refresh` | `refresh_token`，轮换刷新令牌 |
| `POST /v1/auth/logout` | `refresh_token`，吊销对应会话 |
| `GET /v1/entitlement` 和 `GET /v1/subscription` | 剩余试用毫秒数、订阅状态和在线权限 |
| `POST /v1/speech?language=auto` | `audio/wav` 请求体，16 kHz 单声道 16 位 PCM；返回文字及语种 |
| `POST /v1/translate` | `text`、`source`、`target`，返回译文 |
| `GET /v1/plans` | 已启用套餐及人民币分价格 |
| `POST /v1/orders` | `plan_id`、`channel`（`wechat` 或 `alipay`），返回订单 ID、支付链接、二维码 PNG 的 Base64 |
| `GET /v1/orders/{order_id}` | 查询当前账号的订单与支付状态 |
| `POST /v1/payments/wechat/callback` | 微信支付签名通知 |
| `POST /v1/payments/alipay/callback` | 支付宝 RSA2 签名通知 |

客户端访问令牌有效期为 15 分钟，刷新会话从登录起有效 30 天。退出后清除本机令牌并请求吊销服务器会话。

## 计量与收款

每账号初始试用 5 小时。识别按实际发送的 PCM 采样帧折算毫秒，翻译按实际送译字符折算毫秒。服务端在请求 Azure 之前以事务预留额度，成功后确认账目，明确失败或超时释放预留。进程中断遗留的预留在后续额度查询或计量时释放。在线调用在本次余额不足时返回 `402 quota_exhausted`。订阅期间使用单独的周期账目，原试用余额保持可用。

下单使用服务端套餐价格快照。将 `plans.example.json` 复制到部署配置目录，HAOYU 填写正整数 `price_cents` 并启用套餐后重启服务。微信 Native 和支付宝当面付均为扫码预付。付款后从当天起计一个日历月。提前续费从现有期限结束时起算，遇短月取末日。续费由用户主动发起。回调先验签，再核对应用、商户、订单和金额。订单结算与订阅周期在同一 SQLite 事务内写入，重复通知返回成功并沿用原周期。 [微信 Native 下单](https://pay.wechatpay.cn/doc/v3/merchant/4012791877) [微信支付回调](https://pay.wechatpay.cn/doc/v3/merchant/4012791882) [支付宝支付通知](https://aipay.alipay.com/article/2814)

异常频率会收到 `429 rate_limited`。服务端还限制在途 Azure 请求数。运营可在 SQLite 的 `accounts` 表按账号 ID 设置 `blocked = 1`，在线接口返回 `403 account_blocked`，设回 `0` 可恢复。认证、下单与回调日志包含事件、订单或账号标识，避免记录请求正文和凭据。
