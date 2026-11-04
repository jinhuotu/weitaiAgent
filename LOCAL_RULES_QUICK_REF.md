# 本地规则处理优化 - 快速参考

## 🎯 优化目标

**实测问题：**
- LLM 抽取：58 秒 ✅
- 整体 parse：9 分钟以上 ❌
- **瓶颈：本地规则处理（extract.py L740+）**

**目标：**
- 总耗时从 9 分钟降到 <90 秒
- 本地规则耗时从 8 分钟降到 <15 秒

---

## ✅ 已完成优化

### 1. 文档预解析（DocumentStructure）
**文件：** `apps/api/services/tenders/document_parser.py`

**功能：**
- 一次性解析 14 万字文档结构（段落、标题、层级）
- 正则匹配结果自动缓存
- 提取结果缓存（chapter, tender_no, unmashed_text）

**使用：**
```python
doc_structure = parse_document_structure(invitation)
tender_no = extract_tender_no_from_structure(doc_structure)
unmashed = unmash_text_from_structure(doc_structure)
```

**性能：**
- 预解析：0.1-0.3s
- 避免重复全文正则：节省 5-10s+

---

### 2. 性能追踪（PerformanceTracker）
**文件：** `apps/api/services/tenders/performance_tracker.py`

**功能：**
- 追踪各步骤耗时
- 识别性能瓶颈
- 输出详细报告

**使用：**
```python
tracker = PerformanceTracker()
tracker.start_total()

with tracker.track("llm_extract"):
    await extract_chunks(...)

tracker.finish_total()
tracker.log_summary()
```

**输出示例：**
```
PARSE INVITATION PERFORMANCE SUMMARY
TOTAL TIME: 78.52 seconds

BREAKDOWN:
  1. LLM Extract:        58.23s ( 74.2%) - 16 chunks
  2. Document Parse:      0.18s (  0.2%)
  3. Local Rules:         8.34s ( 10.6%)
       - Outline:          2.11s
       - Format:           1.03s
       - Requirements:     3.42s
       - Performance:      0.98s
       - Materials:        0.80s
  4. DB Queries:          9.12s ( 11.6%) - 12 queries
  5. KB Recall:           1.23s (  1.6%)
```

---

### 3. DB 查询并行化
**优化前：**
```python
company_profile = await latest_company_profile(db, ...)
catalog = await catalog_placeholders(db)
catalog_rows = await list_library_items(db)
```

**优化后：**
```python
import asyncio
company_profile, catalog, catalog_rows = await asyncio.gather(
    latest_company_profile(db, ...),
    catalog_placeholders(db),
    list_library_items(db),
)
```

**性能：**
- 3 个串行查询（每个 2-5s）→ 1 个并行查询（最慢的那个）
- 节省 4-10s

---

### 4. 消除重复查询
**优化前：**
```python
catalog_rows = await list_library_items(db)  # 查询 1
# ... 一些处理 ...
catalog_rows = await list_library_items(db)  # 查询 2（重复）
```

**优化后：**
```python
# 一次查询，全程复用
catalog_rows = await list_library_items(db)
# ... 后续直接使用 catalog_rows ...
```

---

## 📊 预期性能对比

| 阶段 | 优化前 | 优化后 | 提升 |
|-----|-------|-------|-----|
| LLM 抽取 | 58s | 58s | - |
| 文档预解析 | - | 0.2s | 新增 |
| 本地规则 | ~480s | ~10s | 98% |
| DB 查询 | ~30s | ~15s | 50% |
| **总耗时** | **~570s** | **<90s** | **84%** |

---

## 🚀 使用方法

### 自动启用
优化已默认启用，重启服务即生效。

### 查看性能报告
```bash
tail -f logs/api.log | grep "PARSE INVITATION"
```

### 关键日志
```
document_structure_parsed: paragraphs=1234 headings=56 chars=144869 seconds=0.182
PARSE INVITATION PERFORMANCE SUMMARY
```

---

## 🔍 监控指标

### 健康状态
- ✅ Total Time: <90s
- ✅ Local Rules: <15s
- ✅ Document Parse: <0.5s
- ✅ Local Rules < LLM Extract

### 需要调优
- ⚠️ Local Rules > LLM Extract
- ⚠️ Total Time > 120s
- ⚠️ DB Queries > 20s

---

## 🛠️ 故障排查

### 性能没有明显提升
1. 检查是否启用文档预解析：
   ```bash
   grep "document_structure_parsed" logs/api.log
   ```

2. 查看性能报告识别瓶颈：
   ```bash
   grep "PARSE INVITATION PERFORMANCE" logs/api.log -A 20
   ```

3. 检查是否有重复全文正则：
   ```bash
   grep -E "extract_(outline|format|requirements)" logs/api.log
   ```

### 提取结果不一致
1. 对比优化前后日志
2. 检查缓存是否正确
3. 单独测试有问题的函数

### 内存占用过高
1. 检查 `_regex_cache` 大小
2. 文档过大时限制缓存

---

## 📋 验收清单

### 性能指标
- [ ] 总耗时 <90s（14 万字文档）
- [ ] 文档预解析 <0.5s
- [ ] 本地规则 <15s
- [ ] DB 查询 <20s

### 功能指标
- [ ] 所有提取字段与优化前一致
- [ ] 大纲、要求、格式无遗漏
- [ ] 多行业标书回归测试通过

### 代码质量
- [ ] 无重复全文正则
- [ ] 无 O(n²) 嵌套
- [ ] 重复 DB 查询已合并
- [ ] 性能报告输出完整

---

## 📂 相关文件

### 核心代码
1. `apps/api/services/tenders/document_parser.py` - 文档预解析
2. `apps/api/services/tenders/performance_tracker.py` - 性能追踪
3. `apps/api/services/tenders/extract.py` - 集成优化

### 文档
1. `LOCAL_RULES_OPTIMIZATION.md` - 详细技术方案
2. `LOCAL_RULES_QUICK_REF.md` - 本文件

### 测试
1. `test_tracker_standalone.py` - 性能追踪测试

---

## 🔄 回退方案

### 禁用文档预解析
```python
# extract.py 中注释掉：
# doc_structure = parse_document_structure(invitation)
# 使用原始函数：
# tender_no = extract_tender_no(invitation)
```

### 禁用性能追踪
```python
# extract.py 中注释掉：
# tracker = PerformanceTracker()
# tracker.start_total()
# ... 所有 with tracker.track() ...
# tracker.finish_total()
# tracker.log_summary()
```

---

**创建时间**：2024-10-31  
**版本**：v2.0  
**状态**：代码实现完成，等待集成测试
