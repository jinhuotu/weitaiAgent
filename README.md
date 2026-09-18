# weitaiAgent

微泰 / 优祺智能体后端（FastAPI `:8100`）。平台能力参考 `zhongjiAgent` 迁移；**窑炉 / 铸造 / SCADA / 数据治理等行业业务未迁入**。

配套前端：[`weitaiAgentui`](../weitaiAgentui)（Vue 3 + Vite，开发默认 `:5173`，经代理访问本 API）。

接口约定：`{ code, msg, data }`，成功 `code === 0`；JSON 字段 **camelCase**。密钥只放本机 `.env`，勿提交仓库。

---

## 能力一览

| 模块 | 状态 | 说明 |
|---|---|---|
| 登录 / JWT / 用户角色 / 菜单 ACL | 已有 | 角色可绑菜单白名单；投标资料库按 ACL 控制 |
| 会话 + Redis 热窗口 + Stream 归档 | 已有 | 会话锁、限流、冷启动 |
| 模型管理 | 已有 | OpenAI 兼容；运行时不读 `.env` 的 LLM Key |
| 提示词管理 | 已有 | Admin CRUD；对话选 `promptId` 注入基座；业务话术写在页面，代码只追加本轮检索标记 |
| MCP / 场景智能体 | 已有 | stdio / SSE / HTTP；智能体可绑提示词、知识库、工具 |
| 工作流 | 已有 | 含 knowledge / llm / agent / mcp / vision / layout 等节点 |
| 知识库 RAG | 已有 | 文档 OCR、切块、Embedding、Qdrant、关键词重排、审核与入库队列 |
| 知识库视频 | 已有（一期） | mp4/webm → Whisper ASR → 文本向量；无有效语音则失败 |
| 投标文件 / 资料库 / 审批 / 商务·技术标分卷 | 已有 | 见 `apps/api/services/tenders/` |
| AI 报价 | 已有 | 规划图识别 + 价目知识库 + Excel；记录表 `quote_records` |
| 充电站布局 / CAD 导出 | 已有 | 对话/工作流布局；DXF 等 |
| 铸造良率 / 窑炉 SCADA | **不要迁** | zhongji 行业业务 |

数据库迁移当前 head：`0013_quote_records`（Alembic）。

---

## 仓库结构

```
weitaiAgent/
├── apps/api/           # FastAPI 主 API（:8100）
├── packages/common/    # 配置 / JWT / Redis / 统一响应 / 日志
├── packages/db/        # SQLAlchemy 模型 + Alembic
├── workers/            # 对话 Stream 归档 + 审计/日志滚动
├── scripts/            # seed_admin / seed_mcp_utility / seed_agents 等
├── infra/              # docker-compose：MySQL / Redis / Qdrant / OnlyOffice / Whisper
├── docs/               # 契约文档（如 kb-video-contract.md）
├── storage/            # 本地上传与旁路正文（默认，勿提交敏感内容）
└── logs/               # 按天应用日志（默认）
```

导入约定：顶层包名 `api` / `common` / `db` / `workers`（Poetry `packages.from`）。

IDE 里 `from api.xxx` 报未解析，多半是源码根未配对，**不等于代码写错**。`weitai-dev` 会把 `apps`、`packages` 加入 `PYTHONPATH`。

- Cursor / VS Code：已有 `pyrightconfig.json`（`extraPaths: apps, packages`），解释器选 `.venv` 后重载
- PyCharm：把 `apps`、`packages` 标成 Sources

---

## 接口一览

前缀 `/api/v1`（健康检查除外）。除登录/刷新/健康外需 `Authorization: Bearer <access>`（前端另带 `X-Access-Token`）。

| 模块 | 路径 |
|---|---|
| 健康 | `GET /health` |
| 登录 | `POST /auth/login`、`POST /auth/refresh`、`GET /auth/me` |
| 用户 / 角色 | `/users`、`/roles`（Admin） |
| 模型 | `/models` |
| 提示词 | `/prompts` |
| MCP | `/mcp-servers` |
| 智能体 | `/agents` |
| 知识库 | `/knowledge` |
| 布局 | `/layouts` |
| 投标 | `/tenders` |
| AI 报价 | `/quotes` |
| 工作流 | `/workflows` |
| 审计 | `/audit/...` |
| 对话 | `/ai/sessions`、`POST /ai/chat`（SSE） |

默认管理员：`admin` / `Admin@123456`（`scripts/seed_admin.py`）。

---

## 对话链路

热路径不在请求里写 `chat_messages`：

1. 用户 / 助手消息 `RPUSH chat:session:{id}`，并 `XADD agent:chat:stream`
2. Worker（`weitai-chat-worker`）消费 Stream，幂等写入 MySQL
3. 打开历史：MySQL 全量 + Redis 未入库尾部按 id 去重
4. Redis miss 时从 MySQL 冷启动最近 N 轮
5. 生成中用 `lock:chat:session:{id}`；停止对话强制删锁

本轮还会：

- 勾选知识库 → 仅在所选 `knowledgeBaseIds` 内检索（**不会跨库**）
- 选中提示词 / 智能体 → 基座提示词；智能体可覆盖 mode / 知识库 / 工具
- MCP → 工具循环（SSE：`delta` / `tool` / `refs` 等）
- 投标资料库命中且与问句相关时，可附原件缩略图给多模态模型（与用户上传图区分）

`POST /ai/chat` 关键字段：`content`、`sessionId`、`modelId`、`promptId`、`knowledgeBaseIds`、`agentId`、图片等。

提示词约定：

- **页面「提示词管理」**：人设与作答规则（含未命中话术）
- **代码 `build_system_prompt`**：只追加本轮标记与参考片段（如「未检索到匹配片段」「知识库参考片段」）

---

## 知识库

### 文档

`POST /api/v1/knowledge/documents/upload` multipart 上传，返回 `status=parsing`，后台入库后再可检索。

- 可复制 PDF / Word / Excel / PPT / 文本：本地抽取
- 扫描页 / 图片：`OCR_PROVIDER=aliyun` 或本机 `rapidocr`
- 正文旁路：`storage/knowledge/{baseId}/{docId}.txt`；失败可 `reparse`
- 同库相同内容哈希会去重；启动时会收回卡住的 parsing 任务

### 视频（一期：仅 ASR）

契约见 [`docs/kb-video-contract.md`](docs/kb-video-contract.md)。

- 支持 `mp4` / `webm`；转写依赖 Compose 里的 **Whisper**（默认宿主机 `19000`）与 `.env` 的 `ASR_*`
- 流程：落盘 →（可选 ffmpeg 抽音轨）→ `/v1/audio/transcriptions` → 切块 Embedding → Qdrant
- **无有效语音**（静音、无音轨、无人声）会失败，文案多为「未识别到有效语音内容」，**不会**进检索
- 同音不同字属于 ASR 模型误差；可换更大 Whisper 模型（`small`/`medium`，更吃 CPU/内存）或事后改稿重入库（改稿入口尚未做）
- 问答必须勾选**上传该视频的知识库**，只勾「投标资料库」搜不到别库视频

推荐本地：

```powershell
docker compose -f infra/docker-compose.yml up -d mysql redis qdrant whisper
# 本机安装 ffmpeg 可改善抽音轨体积与兼容性（可选但建议）
```

---

## 本地启动

需要 Python 3.12 + Poetry + Docker。

| 服务 | weitai 宿主机 | 说明 |
|---|---|---|
| API | **8100** | Windows 无 `--reload`，改代码需重启 |
| MySQL | **13306** | |
| Redis | **26379** | |
| Qdrant | **16333 / 16334** | |
| Whisper ASR | **19000** | 视频转写 |
| OnlyOffice | **8082** | 可选；投标预览默认 browser |

```powershell
copy .env.example .env
docker compose -f infra/docker-compose.yml up -d
poetry install
poetry run python -m alembic upgrade head
poetry run python scripts/seed_admin.py
poetry run python scripts/seed_mcp_utility.py   # 可选
poetry run python scripts/seed_agents.py        # 可选
poetry run weitai-dev
# 另开终端：poetry run weitai-chat-worker   # 对话归档（按需）
```

OpenAPI：http://127.0.0.1:8100/docs

对话前在「模型管理」：

1. 启用 **LLM**（快速对话；需要看图再配 **multimodal_vision**）
2. 启用 **Embedding** 并勾选用于知识库
3. 确认 Qdrant、（若用视频）Whisper 已启动

---

## 待办 / 路线图

以下为已知缺口，**尚未实现**，避免与一期 ASR 行为混淆。

### 知识库视频：关键帧视觉描述（多模态）— 暂缓

目标：无语音或弱语音视频也能入库检索。

建议路径（落地时再改代码）：

1. `ffmpeg` 均匀抽 N 帧（先 6～12 张，限分辨率）
2. 调用模型管理中已启用的 `multimodal_vision`，按帧生成中文画面描述（可读字幕、场景、物体；不编造对白）
3. 与 ASR 正文合并后走现有切块 / Embedding / Qdrant；chunk 可带 `startMs` 便于跳转
4. 触发策略优先 **ASR 文本过短时再兜底**，避免每条视频都打 VL 费 token
5. 可选增强：帧级 OCR 读大字字幕（成本通常低于纯 VL）

非目标（短期不做）：视频多模态向量检索、把整段视频二进制塞进对话上下文。

相关入口：`apps/api/services/knowledge/ingest.py`（`_process_video_document`）、`apps/api/services/knowledge/asr/`、`docs/kb-video-contract.md`。

### 其它可选增强

- 视频转写人工改稿后一键重向量化
- Whisper 模型档位与热词 / 转写后纠错
- 投标 Word 预览与永中 / OnlyOffice 生产化配置

---

## 配置与安全

- 以 `.env.example` 为模板复制为 `.env`
- 勿提交 `.env`、OCR/LLM/ASR 真实密钥、业务上传原件
- 生产请收紧 `CORS_ORIGINS`、`JWT_SECRET_KEY`

---

## 测试

```powershell
poetry run pytest -q
```

Windows 改 API 后请**重启**进程，否则新路由可能 404。
