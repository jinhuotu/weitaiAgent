# weitaiAgent

微泰智能体交互系统后端。参考 `zhongjiAgent` 的平台层迁移，并在代码中加了中文注解。

窑炉 / 铸造 / SCADA / 数据治理等行业业务**没有**搬过来。

---

## 能否迁移（对照结论）

| 能力 | 结论 | 说明 |
|---|---|---|
| 登录 / JWT / 用户角色 | **已迁** | 接口约定 `{code,msg,data}` |
| 会话 CRUD + Redis 热会话 | **已迁** | List 热窗口 + Stream 归档 + 会话锁 + 限流 |
| 模型管理 | **已迁** | OpenAI 兼容网关，运行时不读 `.env` 的 LLM_* |
| 工作流 CRUD + 发布 + 试跑 | **已迁** | `start / knowledge / llm / agent / mcp / end` |
| 操作日志 / 登录日志 | **已迁** | 滚动保留（默认 7 天） |
| 知识库 RAG / Qdrant | **已迁** | 切块 + Embedding + 关键词重排 |
| 提示词 | **已迁** | Admin CRUD；对话按 `promptId` 注入 |
| MCP | **已迁** | stdio / SSE / streamable HTTP；对话最多 5 轮工具调用 |
| 场景智能体 | **已迁** | 绑定提示词 + 知识库 + 工具白名单；对话传 `agentId` |
| 铸造良率 / 窑炉 SCADA | **不要迁** | 属于 zhongji 行业业务 |

---

## 仓库结构

```
weitaiAgent/
├── apps/api/           # FastAPI 主 API（:8100）
├── packages/common/    # 配置 / JWT / Redis / 统一响应
├── packages/db/        # SQLAlchemy 模型 + Alembic
├── workers/            # 对话 Stream 归档 + 日志滚动删除
├── scripts/            # seed_admin / seed_mcp_utility / seed_agents
├── infra/              # docker-compose（MySQL + Redis + Qdrant）
└── docs 约定           # 注解写在代码模块顶部
```

导入约定：顶层包名为 `api` / `common` / `db` / `workers`（Poetry `packages.from`）。

---

## 接口一览

前缀均为 `/api/v1`，除登录/刷新/健康检查外需要 `Authorization: Bearer <access>`。

| 模块 | 路径 |
|---|---|
| 健康 | `GET /health` |
| 登录 | `POST /auth/login`、`POST /auth/refresh`、`GET /auth/me` |
| 用户/角色 | `/users`、`/roles`（Admin） |
| 模型 | `/models`（Admin 写；`/runtime` `/options` 登录可读） |
| 提示词 | `/prompts`（Admin 写；`/options` 登录可读） |
| MCP | `/mcp-servers`（Admin 写；`/runtime` 登录可读） |
| 智能体 | `/agents`（Admin 写；`/options` 登录可读） |
| 知识库 | `/knowledge` |
| 工作流 | `/workflows` |
| 日志 | `/audit/summary`、`/audit/operations`、`/audit/logins` |
| 会话 | `/ai/sessions`、`POST /ai/chat`（SSE） |

默认管理员：`admin` / `Admin@123456`（`scripts/seed_admin.py`）。

---

## 对话链路

热路径不在请求里写 `chat_messages`：

1. 用户消息 / 助手回复 `RPUSH chat:session:{id}`，并 `XADD agent:chat:stream`
2. Worker（`weitai-chat-worker`）消费 Stream，幂等写入 MySQL
3. 打开历史会话时：MySQL 全量 + Redis 未入库尾部按 id 去重
4. Redis miss 时从 MySQL 冷启动最近 N 轮
5. 同一会话生成中用 `lock:chat:session:{id}` 互斥；停止对话会强制删锁

本轮生成还会：

- 选了知识库 → Qdrant 检索，命中片段写入 system
- 选了提示词 / 智能体 → 注入基座提示词，智能体会覆盖 mode / 知识库 / 工具白名单
- 启用 MCP → 模型可循环调用工具（SSE 事件 `delta` / `tool`）

`POST /ai/chat` 请求体关键字段：`content`、`sessionId`、`modelId`、`promptId`、`knowledgeBaseIds`、`agentId`。

---

## 本地启动

需要 Python 3.12 + Poetry + Docker（MySQL / Redis / Qdrant）。

宿主机端口已与 zhongji 的 `deploy` 栈错开：

| 服务 | weitai | zhongji（占用中） |
|---|---|---|
| API | **8100** | 8000 |
| MySQL | **13306** | 3306 |
| Redis | **26379** | 16379 |
| Qdrant HTTP / gRPC | **16333 / 16334** | 6333 / 6334 |

```powershell
copy .env.example .env
docker compose -f infra/docker-compose.yml up -d
poetry install
poetry run python -m alembic upgrade head
poetry run python scripts/seed_admin.py
poetry run python scripts/seed_mcp_utility.py   # 可选：时间/天气 MCP
poetry run python scripts/seed_agents.py        # 可选：示例智能体
poetry run weitai-dev
```

OpenAPI：http://127.0.0.1:8100/docs

对话前请在「模型管理」：

1. 新增并启用 **LLM**，勾选「绑定快速对话」
2. 新增并启用 **Embedding**，勾选「用于知识库」（入库 / RAG 必需）
3. 确认 Qdrant 已启动（默认 `http://127.0.0.1:16333`）

Windows 下 API **不会**开 uvicorn `--reload`（避免旧进程占 8100），改代码后请重启。
