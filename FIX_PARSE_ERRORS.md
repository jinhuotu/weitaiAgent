# 标书识别异常修复方案

## 问题分析

根据代码分析，标书识别流程可能在以下几个环节出现异常：

1. **数据库连接池耗尽** - 并发请求时连接数不足
2. **LLM 调用失败** - API Key 失效、余额不足、网络超时
3. **OCR/文档转换超时** - 文件过大或服务不稳定
4. **知识库检索异常** - Qdrant 或 Redis 服务问题
5. **asyncmy 缓冲区错误** (Windows) - 已有补丁但可能仍有问题

## 已实施的修复

### 1. 增加数据库连接池配置

**文件**: `packages/db/session.py`

```python
engine = create_async_engine(
    _url,
    echo=_settings.debug,
    pool_pre_ping=True,
    pool_size=10,  # 默认连接池大小
    max_overflow=20,  # 最多额外创建的连接数
    pool_timeout=30,  # 获取连接的超时时间（秒）
    pool_recycle=3600,  # 连接回收时间（秒）
)
```

**作用**: 避免高并发时连接池耗尽导致的超时错误

## 需要你手动检查的项目

### 1. 检查 LLM 配置

打开前端「模型管理」页面，确认：

- [ ] API Key 是否正确
- [ ] 模型是否可用
- [ ] 账户余额是否充足

### 2. 检查 .env 配置

确认以下配置正确：

```bash
# 数据库连接
DATABASE_URL=mysql+asyncmy://weitai:weitai_dev@127.0.0.1:13306/weitai_agent?charset=utf8mb4

# Redis 连接
REDIS_URL=redis://127.0.0.1:26379/0

# Qdrant 向量数据库
QDRANT_URL=http://127.0.0.1:16333

# OCR 服务（如果使用阿里云）
OCR_PROVIDER=aliyun
OCR_ACCESS_KEY_ID=<你的AccessKeyId>
OCR_ACCESS_KEY_SECRET=<你的AccessKeySecret>

# 文档智能（可选，用于 PDF 转 Word）
DOCMIND_ACCESS_KEY_ID=<你的AccessKeyId>
DOCMIND_ACCESS_KEY_SECRET=<你的AccessKeySecret>
```

### 3. 查看实时日志

```bash
# 查看今天的日志
tail -f logs/weitai-agent.$(date +%Y-%m-%d).log

# 只看错误
tail -f logs/weitai-agent.$(date +%Y-%m-%d).log | grep -E "ERROR|Exception"
```

### 4. 测试基础服务

```bash
# 测试 MySQL
mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "SELECT 1;"

# 测试 Redis
redis-cli -h 127.0.0.1 -p 26379 PING

# 测试 Qdrant
curl http://127.0.0.1:16333/health
```

## 增强错误处理（建议添加）

### 添加更详细的日志

在 `apps/api/services/tenders/extract.py` 的 `parse_invitation` 函数中添加错误捕获：

```python
async def parse_invitation(
    db: AsyncSession,
    upload: UploadFile,
    *,
    kb_ids: list[str] | None = None,
    # ... 其他参数
) -> dict[str, Any]:
    try:
        # 现有代码...
        path = await _save_invitation(upload)
        logger.info(f"Saved invitation file: {path}")
        
        # 文本提取
        extracted = await extract_text_from_file(path, ext=path.suffix.lower().lstrip("."))
        invitation = (extracted.text or "").strip()
        logger.info(f"Extracted text length: {len(invitation)} chars")
        
        # 知识库检索
        kb_hits, kb_notes = await _recall_knowledge(db, invitation, kb_ids or [])
        logger.info(f"Knowledge base hits: {len(kb_hits)}")
        
        # LLM 调用
        client = await build_llm_client(db, "fast")
        logger.info("LLM client built successfully")
        
        raw = await client.complete(messages, mode="fast")
        logger.info(f"LLM response length: {len(raw or '')} chars")
        
        # ... 继续处理
        
    except Exception as e:
        logger.error(f"Parse invitation failed: {type(e).__name__}: {str(e)}", exc_info=True)
        # 根据异常类型返回更友好的错误信息
        if "timeout" in str(e).lower():
            raise AppError(
                ErrorCode.INTERNAL,
                "识别超时，请尝试：1) 使用文字版 Word 而非扫描 PDF；2) 确认网络连接正常；3) 检查 LLM 服务状态",
                status_code=500,
            )
        elif "connection" in str(e).lower():
            raise AppError(
                ErrorCode.INTERNAL,
                "服务连接失败，请检查：1) 数据库是否正常；2) Redis 是否正常；3) Qdrant 是否正常",
                status_code=500,
            )
        elif "unauthorized" in str(e).lower() or "authentication" in str(e).lower():
            raise AppError(
                ErrorCode.UNAUTHORIZED,
                "模型鉴权失败，请检查「模型管理」中的 API Key 配置",
                status_code=401,
            )
        else:
            raise AppError(
                ErrorCode.INTERNAL,
                f"识别失败：{str(e)}。请查看日志或联系管理员",
                status_code=500,
            )
```

## 常见错误场景及解决方案

### 场景1: "服务器异常" 或 500 错误

**可能原因**:
1. LLM API 调用失败（最常见）
2. 数据库连接超时
3. OCR 服务异常

**解决步骤**:
1. 查看日志确认具体错误: `tail -50 logs/weitai-agent.*.log`
2. 如果是 LLM 错误，检查「模型管理」配置
3. 如果是数据库错误，重启 API 服务
4. 如果是 OCR 错误，使用 Word 文档而非 PDF

### 场景2: "知识库或模型鉴权失败"

**解决**:
1. 打开「模型管理」，检查 API Key
2. 测试模型连接是否正常
3. 确认账户余额充足
4. 临时方案：取消勾选知识库后再识别

### 场景3: 识别卡住不动

**可能原因**:
1. OCR 处理大文件超时
2. LLM 调用超时
3. 数据库查询慢

**解决**:
1. 检查文件大小，大文件尝试分段或使用 Word 格式
2. 查看日志中最后一步操作
3. 重启 API 服务

### 场景4: Windows 上出现 BufferError

**解决**:
1. 已自动安装补丁（`asyncmy_compat.py`）
2. 如果仍有问题，尝试升级到 asyncmy 0.3.x
3. 或者改用 aiomysql：`DATABASE_URL=mysql+aiomysql://...`

## 性能优化建议

### 1. 增加超时配置

在 `.env` 中：

```bash
# OCR 超时（秒）
OCR_TIMEOUT_SECONDS=120  # 从 60 增加到 120

# 文档智能超时（秒）
DOCMIND_TIMEOUT_SECONDS=600  # 从 360 增加到 600

# 知识库解析超时（秒）
KB_PARSE_TIMEOUT_SECONDS=7200
```

### 2. 调整 LLM 参数

如果使用 DeepSeek-R1 等推理模型导致响应截断，改用对话模型：
- DeepSeek-V3
- Qwen-Turbo
- GPT-4

### 3. 数据库优化

如果识别历史记录很多，定期清理：

```sql
-- 删除 30 天前的识别记录
DELETE FROM tender_records WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY);
```

## 监控检查清单

定期检查以下指标：

- [ ] 数据库连接数: `SHOW PROCESSLIST;`
- [ ] Redis 内存使用: `redis-cli INFO memory`
- [ ] Qdrant 健康状态: `curl http://127.0.0.1:16333/health`
- [ ] API 日志大小: `du -sh logs/`
- [ ] 磁盘空间: `df -h`

## 需要进一步帮助？

如果问题仍然存在，请提供：

1. 完整的错误日志（最近100行）
2. `.env` 配置（隐藏敏感信息）
3. 数据库和 Redis 的连接状态
4. 使用的文件类型和大小
5. 前端控制台的错误信息
