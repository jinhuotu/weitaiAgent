# 标书识别异常问题排查清单

## 1. 检查日志
```bash
tail -100 logs/weitai-agent.$(date +%Y-%m-%d).log | grep -E "ERROR|Exception"
```

## 2. 检查数据库连接
```bash
# 测试 MySQL 连接
mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "SELECT 1;"

# 检查数据库连接数
mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "SHOW PROCESSLIST;"
```

## 3. 检查 LLM 服务
- 打开「模型管理」页面，检查模型配置是否正常
- 测试模型 API Key 是否有效
- 检查余额是否充足

## 4. 检查 Redis 连接
```bash
redis-cli -h 127.0.0.1 -p 26379 PING
```

## 5. 检查 Qdrant 向量数据库
```bash
curl http://127.0.0.1:16333/health
```

## 6. 检查 OCR 服务配置
在 .env 文件中确认：
```
OCR_PROVIDER=aliyun  # 或 rapidocr
OCR_ACCESS_KEY_ID=<你的AK>
OCR_ACCESS_KEY_SECRET=<你的SK>
```

## 7. 常见错误及解决方案

### BufferError (Windows asyncmy 问题)
**症状**: 识别大文件时报错 "Existing exports of data"
**解决**: 已自动安装补丁，如仍有问题请重启 API

### 数据库连接池耗尽
**症状**: "too many connections" 或 "connection timeout"
**解决**: 
1. 重启 API 服务
2. 增加数据库连接池大小（见下方修改）

### LLM 鉴权失败
**症状**: "unauthorized" 或 "authentication failed"
**解决**: 
1. 检查模型管理中的 API Key
2. 确认余额充足
3. 尝试取消勾选知识库后再识别

### OCR 超时
**症状**: "OCR timeout" 或识别时间过长
**解决**: 
1. 检查网络连接
2. 提高 OCR_TIMEOUT_SECONDS 配置
3. 使用 Word 文档而非扫描 PDF

## 8. 紧急恢复步骤
```bash
# 1. 重启 API 服务
# Windows: Ctrl+C 停止，然后重新运行
python -m api.main

# 2. 清理数据库连接
mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "SHOW PROCESSLIST;" | \
  grep "Sleep" | awk '{print $1}' | xargs -I {} mysql -h 127.0.0.1 -P 13306 -u weitai -pweitai_dev -e "KILL {};"

# 3. 清理 Redis 缓存（如果怀疑缓存问题）
redis-cli -h 127.0.0.1 -p 26379 FLUSHDB
```
