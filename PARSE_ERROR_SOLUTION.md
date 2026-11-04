# 标书识别异常问题 - 完整解决方案

## 问题概述

标书生成在识别阶段遇到服务器异常，导致识别流程无法继续。之前功能正常，最近开始出现问题。

## 根本原因分析

通过代码审查和日志分析，发现可能的原因有：

### 1. **数据库连接池配置缺失**（最可能）
- **问题**: `session.py` 中没有配置连接池大小，使用 SQLAlchemy 默认值（pool_size=5）
- **影响**: 并发识别请求超过5个时会等待连接，超时后报错
- **症状**: 间歇性出现 "connection timeout" 或识别卡住

### 2. **LLM 服务调用失败**
- **问题**: API Key 失效、余额不足、网络超时、模型不可用
- **影响**: 识别核心步骤失败，返回 500 或 401 错误
- **症状**: "服务器异常"、"鉴权失败"

### 3. **asyncmy 缓冲区问题**（Windows特有）
- **问题**: asyncmy 0.2.14 在 Windows 上处理大结果集时 BufferError
- **影响**: 查询历史记录或知识库时崩溃
- **症状**: BufferError: Existing exports of data

### 4. **OCR/文档转换超时**
- **问题**: 扫描 PDF 文件过大，OCR 处理超时
- **影响**: 文件解析失败，无法提取文本
- **症状**: OCR timeout，识别时间过长

### 5. **知识库服务异常**
- **问题**: Qdrant 向量数据库或 Redis 缓存不可用
- **影响**: 知识库检索失败，导致整个识别流程中断
- **症状**: Connection refused, Service unavailable

## 已实施的修复

### 修复 1: 增加数据库连接池配置

**文件**: `packages/db/session.py`

```python
engine = create_async_engine(
    _url,
    echo=_settings.debug,
    pool_pre_ping=True,
    pool_size=10,  # 增加默认连接池大小（原为 5）
    max_overflow=20,  # 允许临时创建最多 20 个额外连接
    pool_timeout=30,  # 获取连接的超时时间 30 秒
    pool_recycle=3600,  # 每小时回收连接，避免 MySQL 超时
)
```

**效果**:
- 支持最多 30 个并发数据库连接（10 + 20）
- 避免连接池耗尽导致的超时
- 自动检测断开的连接并重连

### 修复 2: 创建错误处理模块

**文件**: `apps/api/services/tenders/error_handling.py`

新增了统一的错误处理和日志记录：
- `log_parsing_step` 装饰器：记录每个步骤的执行时间和错误
- `handle_parsing_error` 函数：将技术错误转换为用户友好的提示
- `log_parse_context` 函数：记录识别上下文信息

## 需要你立即执行的操作

### 第一步：重启 API 服务

```bash
# 1. 停止当前 API 进程（Windows: Ctrl+C）
# 2. 重新启动
cd E:\code\python\weitaiAgent
python -m api.main
```

### 第二步：检查基础服务

```bash
# 检查 MySQL
mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "SELECT 1;"

# 检查 Redis
redis-cli -h 127.0.0.1 -p 26379 PING

# 检查 Qdrant
curl http://127.0.0.1:16333/health
```

### 第三步：查看错误日志

```bash
# 查看最新错误
tail -100 E:\code\python\weitaiAgent\logs\weitai-agent.2026-10-27.log | grep -E "ERROR|Exception"

# 实时监控日志
tail -f E:\code\python\weitaiAgent\logs\weitai-agent.2026-10-27.log
```

### 第四步：检查 LLM 配置

1. 打开前端「模型管理」页面
2. 检查 API Key 是否正确
3. 测试模型连接是否正常
4. 确认账户余额充足

### 第五步：测试识别功能

1. 使用一个小的 Word 文档（< 10 页）测试
2. 暂时取消勾选「知识库」
3. 观察是否能成功识别
4. 如果成功，说明问题已解决

## 建议的配置优化

### 1. 调整超时设置

在 `.env` 文件中增加超时配置：

```bash
# OCR 超时（秒），建议从 60 增加到 120
OCR_TIMEOUT_SECONDS=120

# 文档智能超时（秒），建议从 360 增加到 600
DOCMIND_TIMEOUT_SECONDS=600

# 知识库解析超时（秒）
KB_PARSE_TIMEOUT_SECONDS=7200
```

### 2. 优化数据库配置（可选）

如果数据库记录很多，建议定期清理：

```sql
-- 查看记录数
SELECT COUNT(*) FROM tender_records;

-- 删除 30 天前的测试记录
DELETE FROM tender_records 
WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY) 
AND status = 'draft';
```

### 3. 使用文字版文档

- **推荐**: 使用文字版 Word (.docx) 文档
- **避免**: 扫描版 PDF（OCR 慢且容易超时）
- **替代**: 如必须用 PDF，先用 WPS 或 Word 另存为 .docx

## 常见错误及解决方案

### 错误 1: "服务器异常" 或 500 错误

**可能原因**: LLM 调用失败、数据库超时、OCR 异常

**解决步骤**:
```bash
# 1. 查看日志确定具体错误
tail -50 logs/weitai-agent.*.log

# 2. 如果是 LLM 错误，检查模型配置
# 打开前端 -> 模型管理 -> 测试连接

# 3. 如果是数据库错误，重启 API
python -m api.main

# 4. 如果是 OCR 错误，使用 Word 文档
```

### 错误 2: "知识库或模型鉴权失败"

**原因**: LLM API Key 失效或余额不足

**解决**:
1. 打开「模型管理」检查 API Key
2. 测试模型连接
3. 确认账户余额
4. **临时方案**: 取消勾选知识库后再识别

### 错误 3: 识别卡住不动超过 1 分钟

**可能原因**: OCR 处理大文件、LLM 超时、数据库查询慢

**解决**:
1. 刷新页面重新上传
2. 使用较小的文件测试（< 50 页）
3. 使用文字版 Word 而非扫描 PDF
4. 重启 API 服务

### 错误 4: BufferError (Windows)

**症状**: 
```
BufferError: Existing exports of data: object cannot be re-sized
```

**解决**:
1. 已自动安装补丁（`asyncmy_compat.py`）
2. 如果仍有问题，升级 asyncmy：
```bash
pip install asyncmy==0.3.0
```
3. 或改用 aiomysql：
```bash
# 修改 .env
DATABASE_URL=mysql+aiomysql://weitai:weitai_dev@127.0.0.1:13306/weitai_agent
```

## 监控和预防

### 定期检查（每周）

```bash
# 1. 检查数据库连接数
mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "SHOW PROCESSLIST;" | wc -l

# 2. 检查 Redis 内存
redis-cli -h 127.0.0.1 -p 26379 INFO memory | grep used_memory_human

# 3. 检查 Qdrant 健康
curl http://127.0.0.1:16333/health

# 4. 检查日志大小
du -sh logs/

# 5. 检查磁盘空间
df -h
```

### 日志轮转

日志会自动按天轮转，保留 7 天。可在 `.env` 中调整：

```bash
# 日志保留天数
LOG_RETENTION_DAYS=7
```

### 性能监控

在识别高峰期观察：
- CPU 使用率（< 80%）
- 内存使用率（< 80%）
- 数据库连接数（< 20）
- 日志中的错误数量

## 进一步排查

如果以上方法都无法解决问题，请提供以下信息：

### 1. 完整错误日志

```bash
tail -200 logs/weitai-agent.2026-10-27.log > error_log.txt
```

### 2. 系统环境信息

```bash
# Python 版本
python --version

# 依赖版本
pip list | grep -E "asyncmy|sqlalchemy|fastapi|redis"

# 系统信息
systeminfo  # Windows
```

### 3. 配置信息（隐藏敏感内容）

```bash
# 显示 .env 配置（移除密钥）
cat .env | grep -v "KEY" | grep -v "SECRET"
```

### 4. 服务状态

```bash
# 数据库状态
mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "SHOW STATUS LIKE 'Threads_connected';"

# Redis 状态
redis-cli -h 127.0.0.1 -p 26379 INFO stats

# Qdrant 状态
curl http://127.0.0.1:16333/collections
```

### 5. 测试文件信息

- 文件类型（Word / PDF）
- 文件大小
- 页数
- 是否是扫描件

### 6. 前端错误

打开浏览器开发者工具（F12），复制 Console 和 Network 标签中的错误信息。

## 紧急恢复步骤

如果系统完全不可用，按以下步骤恢复：

```bash
# 1. 停止所有服务
# 停止 API（Ctrl+C）

# 2. 重启数据库和 Redis（如果使用 Docker Compose）
cd infra
docker compose restart mysql redis

# 3. 清理数据库连接
mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "SHOW PROCESSLIST;" | \
  grep "Sleep" | awk '{print $1}' | \
  xargs -I {} mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "KILL {};"

# 4. 清理 Redis 缓存（慎用，会清空所有缓存）
redis-cli -h 127.0.0.1 -p 26379 FLUSHDB

# 5. 重启 API
cd E:\code\python\weitaiAgent
python -m api.main

# 6. 测试基础功能
# 打开前端，登录，尝试识别一个小文件
```

## 预防措施

1. **定期重启**: 每周重启一次 API 服务，避免长时间运行导致的资源泄漏
2. **监控日志**: 每天检查错误日志，及时发现问题
3. **备份数据**: 定期备份数据库和知识库
4. **限制文件大小**: 建议单个文件 < 100 页或 < 50MB
5. **使用文字版文档**: 优先使用 Word 文档，避免 OCR 超时

## 联系支持

如果问题持续存在，请联系开发团队，并提供：

1. 完整错误日志（最近 200 行）
2. `.env` 配置（隐藏敏感信息）
3. 服务状态检查结果
4. 测试文件（如果可以分享）
5. 问题复现步骤

---

**文档版本**: 1.0  
**更新日期**: 2026-10-27  
**适用版本**: weitaiAgent main 分支
