# 知识库视频：字段与状态契约（阶段 0）

一期路线：**ASR 转写文本检索 + 命中后播原片**。不做视频多模态向量检索。

后续阶段实现时以前后端口径与本文件及代码常量为准：

- 后端：`apps/api/services/knowledge/video_contract.py`
- 前端：`weitaiAgentui/src/lib/kb-video-contract.ts`

---

## 1. 范围

| 项 | 一期约定 |
|----|----------|
| 扩展名 | `mp4`、`webm` |
| MIME | `video/mp4`、`video/webm` |
| 文档 `kind` | `video`（与现有 `doc` / `drawing` / `3d` 并列） |
| `source` | 上传仍为 `file` |
| 检索方式 | 转写文本 → 现有切块 / Embedding / Qdrant |
| 对话回放 | refs 命中后前端播原片；不向模型塞视频二进制 |

非目标（一期不做）：画面语义搜、仅落盘不转写、整文件 blob 拉进前端再播。

---

## 2. 配置

| 环境变量 | 配置字段 | 默认 | 说明 |
|----------|----------|------|------|
| `KB_UPLOAD_MAX_BYTES` | `kb_upload_max_bytes` | 200MB | 文档/图片等现有上限，不变 |
| `KB_VIDEO_UPLOAD_MAX_BYTES` | `kb_video_upload_max_bytes` | 512MB | **仅视频**上传上限 |
| `KB_VIDEO_ASR_TIMEOUT_SECONDS` | `kb_video_asr_timeout_seconds` | 7200 | 单视频 ASR 软超时参考；总 parsing 仍受 `KB_PARSE_TIMEOUT_SECONDS` 约束 |
| `ASR_PROVIDER` | `asr_provider` | `none` | `none` / `openai`（OpenAI 兼容 `/v1/audio/transcriptions`） |
| `ASR_API_BASE` | `asr_api_base` | 空 | 如 `https://api.openai.com/v1` 或本地 Whisper HTTP 的 `/v1` 根 |
| `ASR_API_KEY` | `asr_api_key` | 空 | Bearer；本地服务可空 |
| `ASR_MODEL` | `asr_model` | `whisper-1` | 模型名 |
| `ASR_LANGUAGE` | `asr_language` | `zh` | 提示语言，可空 |

密钥 / ASR Provider 走环境变量或模型管理，不写进代码与本契约正文。

---

## 3. 存储与文档元数据

落盘路径沿用现有：

`{storage_root}/knowledge/{basePublicId}/{docPublicId}.{ext}`

`KnowledgeDocument`（对外 JSON camelCase）：

| 字段 | 视频约定 |
|------|----------|
| `id` | `public_id` |
| `baseId` | 所属库 |
| `name` | 展示名（默认同文件名） |
| `source` | `file` |
| `kind` | **`video`** |
| `fileType` | `mp4` / `webm` |
| `size` | 字节 |
| `storagePath` / `fileKey` | 相对路径 |
| `charCount` | 转写正文有效字符数；转写前为 0 |
| `chunks` / `chunkCount` | 向量块数；转写并向量化完成后 >0 |
| `status` | 见状态机 |
| `errorMsg` | 失败时中文 |
| `reviewStatus` | 与现网文档一致：入库成功后默认 `approved`（后续若改审核策略，视频同步） |
| `tags` | 可选；可含业务标签，不强制 |

不新增表列也可一期落地（时间戳放 Qdrant payload）。若二期要资料详情展示字幕轨，再考虑旁路文件或表字段。

---

## 4. 状态机

```
上传落盘成功
    → status = parsing
    →（阶段 2）ASR 转写
    →（现有）切块 + Embedding + Qdrant upsert
    → status = ready，chunkCount > 0
         或
    → status = failed，errorMsg = 中文原因
```

| status | 含义 | 对话检索 |
|--------|------|----------|
| `parsing` | 上传后转写/向量化进行中 | 不参与 |
| `ready` | 可检索（且 review 未 reject） | 可命中 |
| `failed` | 转写失败 / 无有效语音文本 / 向量化失败等 | 不参与 |

建议 `errorMsg`（实现阶段使用，不必一次写死全部）：

- `不支持的视频格式`
- `视频超过大小上限`
- `语音转写失败`
- `未识别到有效语音内容`
- `向量化失败`

进度文案（阶段 6，走 `summary`，不进 status 枚举）：`正在语音转写…` → `正在写入检索…` → 转写正文摘要。仍只用 `parsing` 一个状态码。

---

## 5. Qdrant chunk payload

在现有字段上**可选**增加时间戳（无则整片播放）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `doc_id` | string | 已有 |
| `kb_id` | string | 已有 |
| `name` | string | 已有 |
| `source` | string | 已有；视频为 `file` |
| `chunk_index` | int | 已有 |
| `content` | string | 转写文本片段 |
| `tags` | string[] | 已有 |
| `review` | string | 已有 |
| `startMs` | int \| 缺省 | 本块对应转写起始毫秒 |
| `endMs` | int \| 缺省 | 本块对应转写结束毫秒 |

`startMs` / `endMs` 在检索命中后原样进入对话 refs（snake_case，与现有 `doc_id` 等一致）。

---

## 6. 检索与对话 refs

`search_chunks` / SSE `event: refs` 单条（与现网混用 snake_case）：

| 字段 | 视频约定 |
|------|----------|
| `content` | 转写片段 |
| `score` | 相似度 |
| `doc_id` | 文档 public_id |
| `kb_id` | 知识库 public_id |
| `name` | 资料名 |
| `chunk_index` | 块序号 |
| `file_type` | `mp4` / `webm` |
| `has_file` | `true`（原片在盘） |
| `preview_kind` | **`video`**（新增枚举值；现有：`pdf` / `image` / `file` / `""`） |
| `startMs` | 可选 |
| `endMs` | 可选 |

**不**在 refs 里下发 `playUrl` / 临时签名 URL。前端用 `doc_id` 调播放接口（阶段 4），避免 SSE 膨胀与鉴权分叉。

播放地址（阶段 4 已实现）：

- `GET /api/v1/knowledge/documents/{docId}/file?baseId=...`
- 鉴权：Bearer / `X-Access-Token`，或 `<video src>` 用 `?access_token=`（与投标库缩略图同策略）
- 权限：知识库 `view`
- 响应：`FileResponse` + `Accept-Ranges: bytes` + `Content-Disposition: inline`；Starlette 处理 `Range` → `206`
- MIME：`video/mp4` / `video/webm`（按扩展名）

---

## 7. 前端展示约定

| 场景 | 行为 |
|------|------|
| 知识库列表/详情 | `kind === 'video'` 或 `fileType` 为 mp4/webm：视频图标；`parsing` 显示转写中 |
| 对话 refs | `preview_kind === 'video'` 展示标签与时段；气泡下用 `ChatKbVideos` 播原片 |
| 定位 | 若有 `startMs`，`loadedmetadata` 后 `currentTime = startMs / 1000` |
| 原件范围 | 不限投标库；通用 KB 命中即可出播放条 |
| 去重 | 同一 `doc_id` 多条 chunk 只出一个播放器（取最早 `startMs`） |
| 播放 URL | `kbVideoFilePath(docId, { baseId: kb_id, withToken: true })` |

---

## 8. 阶段对照（实现顺序不变）

| 阶段 | 内容 | 相对本契约 | 状态 |
|------|------|------------|------|
| 0 | 本契约 + 常量/配置/类型 | 本文 | 已完成 |
| 1 | 上传白名单 + 落盘 + `kind=video` | §1–4 存储段 | 已完成（待转写，不进向量） |
| 2 | ASR → 文本向量化 | §4–5 | 已完成（ASR_PROVIDER=openai） |
| 3 | `preview_kind=video` + refs 时间戳 | §5–6 | 已完成（对话/检索试一下展示视频标签与时段） |
| 4 | Range 流式播放 | §6 播放地址 | 已完成 |
| 5 | 对话 `<video>` | §7 | 已完成 |
| 6 | 进度文案与运维 | §4 进度 | 未做 |

---

## 9. 兼容性

- 现有非视频文档行为不变。
- 旧 Qdrant 点无 `startMs`/`endMs`：整片播放即可。
- `kind` 列已是 `String(16)`，写入 `video` **无需**为一期单独加列；仅更新模型注释即可。
