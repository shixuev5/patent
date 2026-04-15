# Python WeChat Gateway

独立微信网关进程，直接对接 [`corespeed-io/wechatbot/python`](https://github.com/corespeed-io/wechatbot/tree/main/python) 的 Python SDK。

## 设计边界

- `backend/` 仍然是系统真相，负责账号、绑定、AI 检索、任务系统和通知队列。
- `im_gateway/` 包只负责微信登录态、消息收发、媒体下载上传、调用后端内部接口。
- 不引入额外上层 agent。

## 关键能力

- 微信私聊扫码登录与入站消息监听
- 绑定用户的文本消息转发到后端 `/api/internal/wechat/messages`
- 轮询后端 `delivery-jobs`，将任务完成/失败结果主动回推到微信
- 对文件类结果优先走媒体发送，失败时回退为文本

## 依赖

当前仓库主 Python 依赖里已经包含 `httpx` / `fastapi` / `uvicorn`，并已将 `wechatbot-sdk` 纳入根 `pyproject.toml`，Docker 镜像构建时会一并安装。

## 环境变量

- `API_BASE_URL`：后端地址，默认 `http://127.0.0.1:${PORT:-7860}`
- `INTERNAL_GATEWAY_TOKEN`：与后端共享的内部 token
- `IM_GATEWAY_POLL_INTERVAL_SECONDS`：出站轮询间隔，默认 `8`
- `IM_GATEWAY_LOGIN_RETRY_SECONDS`：登录/重连失败后的重试间隔，默认 `5`
- `IM_GATEWAY_INBOUND_REPLY_WAIT_SECONDS`：同步等待首条回复的秒数，默认 `8`
- `IM_GATEWAY_INBOUND_REQUEST_TIMEOUT_SECONDS`：入站请求后端的总超时，默认 `180`
- `IM_GATEWAY_DOWNLOAD_DIR`：微信媒体临时下载目录，默认 `<repo>/data/im_gateway`
- `IM_GATEWAY_CRED_PATH`：本地微信 SDK 凭证根目录；每个 owner 使用 `<root>/<owner>/credentials.json`
- `IM_GATEWAY_CRED_R2_PREFIX`：推荐使用；每个 owner 的加密凭证写入该前缀下
- `IM_GATEWAY_CRED_ENCRYPTION_KEY`：启用 R2 持久化时必填；用于加密存入 R2 的凭证文件

## 启动

```bash
python -m im_gateway.main
```

若运行在 Hugging Face Space 的单容器部署中，根目录 `docker-entrypoint.sh` 会先启动后端，再在 `WECHAT_INTEGRATION_ENABLED=true` 时自动拉起 `im-gateway`。

## 当前实现说明

- 已接入后端内部 API 调用链和 Python 网关骨架。
- `wechatbot-sdk` 的真实运行依赖于本地环境安装该库并完成扫码登录。
- 配置 `IM_GATEWAY_CRED_R2_PREFIX` 后，网关会在启动时尝试从 R2 恢复 owner 级凭证，并在登录成功后将加密后的凭证文件回写到 R2。
- 若当前环境未安装 `wechatbot-sdk`，启动时会直接报出明确错误，避免误以为已经完成真实接入。
