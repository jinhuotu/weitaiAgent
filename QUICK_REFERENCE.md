# 抽取优化快速参考

## 🎯 核心改进

| 项目 | 优化前 | 优化后 |
|-----|-------|-------|
| 首次成功率 | 75% | ≥95% |
| 总耗时 | 248s | <150s |
| 单块 prompt | ~15K | ~11K |
| 并发数 | 3 | 6 |
| 重试次数 | ≥4 | ≤1 |

## ⚙️ 配置调整

```python
# apps/api/services/tenders/extraction_config.py

# 提高并发（适合高性能 GPU）
set_extraction_config(extraction_concurrency=8)

# 降低并发（避免 429 错误）
set_extraction_config(extraction_concurrency=4)

# 调整分块大小
set_extraction_config(chunk_size=12000)

# 禁用优化（回退原版）
set_extraction_config(use_optimized_extraction=False)
```

## 📊 关键日志

### 正常输出
```
invitation chunk=1 range=0:9876 chunk_chars=9876 prompt_chars=10923 ratio=1.11
invitation chunk=1 chars=9876 attempts=1 seconds=12.456
extraction_summary: chunks=16 first_success_rate=93.8% retries=1 subdivisions=0
```

### 异常输出
```
# 422 错误
parse_extract_payload failed: 模型未能抽出结构化字段

# 429 错误（需降低并发）
llm request failed: rate limit exceeded

# 504 错误（会自动分块）
invitation chunk=9 subdividing=2 pieces chars=10234
```

## 🔧 故障排查

### 问题：还有 422 错误
1. 查看日志中的 `raw_preview`
2. 检查模型是否输出非 JSON 内容
3. 考虑切换到更稳定的模型

### 问题：耗时没有明显改善
1. 检查并发数是否生效：`extraction_concurrency`
2. 查看 `subdivisions` 是否过多
3. 检查网络延迟

### 问题：抽取结果质量下降
1. 对比关键字段（projectName, tenderer, quoteLines）
2. 检查 `requiredMaterials` 是否遗漏
3. 如有问题，回退原版：`use_optimized = False`

## 🚀 快速回退

### 代码回退
```python
# extract.py 第 691 行
use_optimized = False  # 改为 False
```

### 删除文件
```bash
rm apps/api/services/tenders/extract_chunks_optimized.py
```

## 📈 监控指标

### 健康状态
- ✅ `first_success_rate ≥ 95%`
- ✅ `retries ≤ 1`
- ✅ `subdivisions = 0`
- ✅ `avg_prompt_chars ≈ 11000`
- ✅ `total_seconds < 150`

### 需要调优
- ⚠️ `first_success_rate < 90%` → 检查 prompt 或模型
- ⚠️ `retries > 2` → 降低并发或调整 chunk_size
- ⚠️ `subdivisions > 1` → 减小 chunk_size
- ⚠️ `avg_prompt_chars > 13000` → 检查上下文去重是否生效

## 📞 获取帮助

1. **查看详细文档**：`EXTRACTION_OPTIMIZATION.md`
2. **查看实施总结**：`IMPLEMENTATION_SUMMARY.md`
3. **运行测试**：`python test_json_repair.py`
4. **检查日志**：搜索 `extraction_summary`

---

**更新时间**：2024-10-31  
**版本**：v1.0
