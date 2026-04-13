# Python WeChat Gateway

独立微信网关进程，直接对接 [`corespeed-io/wechatbot/python`](https://github.com/corespeed-io/wechatbot/tree/main/python) 的 Python SDK。

## 设计边界

- `backend/` 仍然是系统真相，负责账号、绑定、AI 检索、任务系统和通知队列。
- `im-gateway/` 只负责微信登录态、消息收发、媒体下载上传、调用后端内部接口。
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
- `IM_GATEWAY_DOWNLOAD_DIR`：微信媒体临时下载目录，默认 `./tmp`
- `IM_GATEWAY_CRED_PATH`：本地微信 SDK 凭证文件路径；启用 R2 持久化时默认使用 `IM_GATEWAY_DOWNLOAD_DIR/credentials.json`
- `IM_GATEWAY_CRED_R2_KEY`：启用后，将加密后的微信 SDK 凭证文件持久化到该 R2 key
- `IM_GATEWAY_CRED_ENCRYPTION_KEY`：启用 R2 持久化时必填；用于加密存入 R2 的凭证文件

## 启动

```bash
python im-gateway/main.py
```

若运行在 Hugging Face Space 的单容器部署中，根目录 `docker-entrypoint.sh` 会先启动后端，再在 `WECHAT_INTEGRATION_ENABLED=true` 时自动拉起 `im-gateway`。

## 当前实现说明

- 已接入后端内部 API 调用链和 Python 网关骨架。
- `wechatbot-sdk` 的真实运行依赖于本地环境安装该库并完成扫码登录。
- 配置 `IM_GATEWAY_CRED_R2_KEY` 后，网关会在启动时尝试从 R2 恢复凭证，并在登录成功后将加密后的凭证文件回写到 R2。
- 若当前环境未安装 `wechatbot-sdk`，启动时会直接报出明确错误，避免误以为已经完成真实接入。
