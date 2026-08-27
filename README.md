# Cursor MyAgent Base

LangGraph 多 Agent 助手：意图识别后按领域走行程专家 / 办公专家，或闲聊。状态存在 PostgreSQL，CLI 和 FastAPI **共用同一套图和 checkpoint**，可以用同一个 `thread_id` 互相续上。

## 能做什么

- 查今天 / 明天天气（OpenWeather）
- 高德路线与行程规划（驾车 / 步行 / 公交 / 骑行，城市一日游）
- 按 `contacts.json` 白名单发信（SMTP），发送前需确认；没有成功回执时不能说已经发出
- 一句话里「先查天气，条件成立再发信」
- 关键信息缺失或地点含糊时会先反问，而不是猜测
- 路由失败、模型异常、步骤超限或没有有效回复时，固定说明「这次没办成」和原因
- 进程中断后从断点续跑；发信用回执避免重复投递

## 准备

需要 Python 3.13+、[uv](https://docs.astral.sh/uv/)、本机 PostgreSQL。

```powershell
Copy-Item .env.example .env
Copy-Item contacts.example.json contacts.json
```

编辑 `.env`：填入通义千问、OpenWeather、高德 Web 服务 Key、SMTP、Postgres 密码，以及 **`API_TOKEN`（调 FastAPI 用的口令）**。  
高德 Key 必须是开放平台里的 **Web 服务** 类型（不要用 JS/小程序 Key）。若控制台开了数字签名，再填 `AMAP_SECRET`。  
编辑 `contacts.json`：登记称呼和邮箱。未登记的地址不会发送。

```powershell
uv sync
```

`.env`、`contacts.json`、`.email_receipts.json`、本地 `skill.py` 已在 `.gitignore`，不要提交密钥。用 Docker 时不必本机安装 Python / PostgreSQL。

## 方式一：CLI

```powershell
uv run python -m cursor_myagent_base
```

或：

```powershell
uv run cursor-myagent-base
```

会话 id 默认是 `.env` 里的 `AGENT_THREAD_ID`（如 `cli-local`）。输入 `quit` 退出。

发信前会停在「确认发信:」，输入 **确认** 才 SMTP。在确认处 `Ctrl+C` 不会当成取消，下次启动会再问。

改完代码后必须先退出再启动，新逻辑才会生效。

## 方式二：FastAPI

```powershell
uv run python -m cursor_myagent_base.api
```

或：

```powershell
uv run cursor-myagent-api
```

默认 `http://127.0.0.1:8000`（可用 `API_HOST` / `API_PORT` 改）。浏览器打开首页即聊天页；填入 `.env` 的 `API_TOKEN` 后即可对话，左侧可点 **通讯录** 查看 `contacts.json`。接口调试仍可用 `/docs`，点 **Authorize** 填入同一 Token。API 用异步调用大模型，并用 `MAX_CONCURRENT_RUNS`（默认 8）限制同时跑的轮次，避免把通义接口打满。

业务接口需要请求头（`.env` 里的 `API_TOKEN`，相当于门口暗语）：

```http
Authorization: Bearer 你的API_TOKEN
```

`GET /health` 不需要令牌，方便探活。CLI 不走 HTTP，也不需要这个头。

### 健康检查与目录

```powershell
curl http://127.0.0.1:8000/health
curl -H "Authorization: Bearer 你的API_TOKEN" http://127.0.0.1:8000/v1/skills
curl -H "Authorization: Bearer 你的API_TOKEN" http://127.0.0.1:8000/v1/contacts
```

### 对话

`thread_id` 可省略，默认与 CLI 相同（`AGENT_THREAD_ID`）。若要和 CLI **分开测**，换一个 id，例如 `api-local`。

```powershell
curl -X POST http://127.0.0.1:8000/v1/chat -H "Authorization: Bearer 你的API_TOKEN" -H "Content-Type: application/json" -d "{\"message\":\"北京明天天气怎么样\",\"thread_id\":\"api-local\"}"
```

PowerShell 示例：

```powershell
$headers = @{ Authorization = "Bearer 你的API_TOKEN"; "Content-Type" = "application/json" }
Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/v1/chat -Headers $headers -Body '{"message":"北京明天天气怎么样","thread_id":"api-local"}'
```

### 发信确认

若返回 `status` 为 `needs_confirmation`，先看 `confirm` 里的收件人 / 主题 / 正文，再确认或取消：

```powershell
curl -X POST http://127.0.0.1:8000/v1/confirm -H "Authorization: Bearer 你的API_TOKEN" -H "Content-Type: application/json" -d "{\"thread_id\":\"api-local\",\"decision\":\"确认\"}"
```

取消：`"decision":"取消"`，或 `"approve": false`。

若该会话正等确认时又 `POST /v1/chat`，会返回 **409**，请先确认或取消。

### 断点续跑

中途停掉 API 后：

```powershell
curl -H "Authorization: Bearer 你的API_TOKEN" http://127.0.0.1:8000/v1/threads/api-local
curl -X POST http://127.0.0.1:8000/v1/resume -H "Authorization: Bearer 你的API_TOKEN" -H "Content-Type: application/json" -d "{\"thread_id\":\"api-local\"}"
```

`POST /v1/chat` 默认 `auto_resume=true`：若有未完成任务会先续跑，再处理新消息。若已在等发信确认，仍须走 `/v1/confirm`。

CLI 和 API 只要 `thread_id` 相同、连的是同一 Postgres，就能接着同一份状态。

## 方式三：Docker（本机或 ECS）

先有 `.env` 和 `contacts.json`（不要提交到 GitHub）。`POSTGRES_PASSWORD` 必须填；Compose 会起 Postgres，并把应用的 `POSTGRES_HOST` 指到容器名 `postgres`，不必改成 `127.0.0.1`。

本机安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/) 后：

```powershell
docker compose up -d --build
```

打开 `http://127.0.0.1:8000`。日志：`docker compose logs -f api`。停止：`docker compose down`（加 `-v` 会删掉数据库卷，会话会丢）。

ECS：`git clone` 仓库，在服务器上放好 `.env` 和 `contacts.json`，同样 `docker compose up -d --build`。若 8000 已被占用，在 ECS 的 `.env` 设 `API_PUBLISH_PORT=9876`，安全组放行 **9876**。Postgres 只在 Compose 内部网络给 API 用，不映射到主机 5432，以免和本机 PostgreSQL 抢端口。

## 测试

```powershell
uv run python tests/test_safety.py
uv run python tests/test_weather_geo.py
uv run python tests/test_amap.py
uv run python tests/test_domains.py
uv run python tests/test_clarify.py
uv run python tests/test_fallback.py
uv run python tests/test_email_idempotent.py
uv run python tests/test_email_claim.py
uv run python tests/test_guard_duplicate.py
uv run python tests/test_postgres_checkpoint.py
uv run python tests/test_api.py
uv run python tests/test_stream_events.py
```

`test_api.py` 会启动 FastAPI 生命周期并连 Postgres，不调用大模型。

推送到 GitHub 后，`.github/workflows/test.yml` 会自动跑上述测试（CI）。本机改代码不会自动测，需要 push / 开 PR，或自己先跑上面的命令。

本地只想测发信、跳过确认时，可在 `.env` 设 `EMAIL_SKIP_CONFIRM=1`（生产不要开）。

## 结构要点

- `graph.py`：意图识别 → 行程专家 / 办公专家 / 闲聊 / 反问 / 系统兜底；跨领域时先行程后办公
- `agents/`：`intent_Agent.py` 路由，`chat_Agent.py` 闲聊，`trip_Agent.py` 行程，`office_Agent.py` 办公，`clarify_Agent.py` 反问，`fallback_Agent.py` 系统兜底；共用装配在 `worker.py`
- 领域白名单：行程只能调 `weather`、`amap`；办公只能调 `email`（代码强制，不靠提示词）
- Skill 渐进披露：系统提示只有该领域目录，`load_skill` 再读 `SKILL.md`
- 高德行程：地理编码后走驾车/步行/公交/骑行，或按城市 POI 串联一日游
- PostgreSQL Checkpointer（`durability=sync`）做会话持久和断点续跑
- 发信：通讯录白名单 + 人工确认 + `.email_receipts.json` 幂等
- FastAPI：`API_TOKEN` Bearer 鉴权；GitHub Actions 在 push/PR 时跑测试
- Docker：`Dockerfile` + `docker-compose.yml` 同时起 API 与 PostgreSQL；密钥仍用本机 `.env`，不进镜像
