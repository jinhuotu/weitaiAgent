# 本地规则优化验收测试

## 测试目标
验证本地规则优化后性能显著提升，且提取结果与优化前一致。

## 测试环境
- 操作系统：Windows/Linux
- Python 版本：3.9+
- 数据库：PostgreSQL
- 测试文件：144,869 字招标文件

---

## 测试步骤

### Phase 1: 性能追踪验证

#### 1.1 验证性能追踪器
```bash
python test_tracker_standalone.py
```

**预期输出：**
```
PARSE INVITATION PERFORMANCE SUMMARY
================================================================================
File: test_file.docx (144869 chars)

TOTAL TIME: 1.70 seconds

BREAKDOWN:
  1. LLM Extract:         0.50s ( 29.4%) - 16 chunks
  2. Document Parse:      0.05s (  2.9%)
  3. Local Rules:         0.65s ( 38.2%)
       - Outline:          0.20s
       - Format:           0.10s
       - Requirements:     0.15s
       - Performance:      0.08s
       - Materials:        0.12s
  4. DB Queries:          0.50s ( 29.4%) - 5 queries
  5. KB Recall:           0.10s (  5.9%)

[TEST PASSED] Performance tracker works correctly!
```

**验收标准：**
- ✅ 各阶段耗时正确统计
- ✅ 百分比加总约 100%
- ✅ 测试通过

---

#### 1.2 验证文档预解析
创建测试文件 `test_document_parser.py`：

```python
import sys
import importlib.util

spec = importlib.util.spec_from_file_location(
    "document_parser",
    "apps/api/services/tenders/document_parser.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

parse_document_structure = module.parse_document_structure

# 测试文本
text = """第一章 招标公告

一、项目概况
项目名称：某某项目
项目编号：ABC-2024-001

二、投标人资格要求
1. 具有独立法人资格
2. 具有有效的营业执照
3. 具有ISO9001认证

第二章 投标文件格式

1.1 投标文件封面
1.2 投标函
1.3 资格证明文件"""

# 解析
structure = parse_document_structure(text)

# 验证
print(f"段落数: {len(structure.paragraphs)}")
print(f"标题数: {len(structure.headings)}")

# 测试缓存
tender_no = module.extract_tender_no_from_structure(structure)
print(f"招标编号: {tender_no}")
assert tender_no == "ABC-2024-001", f"Expected 'ABC-2024-001', got '{tender_no}'"

print("\n[TEST PASSED] Document parser works correctly!")
```

运行：
```bash
python test_document_parser.py
```

**预期输出：**
```
document_structure_parsed: paragraphs=10 headings=6 chars=156 seconds=0.001
段落数: 10
标题数: 6
招标编号: ABC-2024-001

[TEST PASSED] Document parser works correctly!
```

**验收标准：**
- ✅ 段落识别正确
- ✅ 标题提取正确
- ✅ 招标编号提取准确
- ✅ 耗时 <0.01s（小文本）

---

### Phase 2: 集成测试

#### 2.1 准备测试数据
1. 准备 144,869 字招标文件（原测试文件）
2. 备份优化前的提取结果：
   ```bash
   # 保存到 baseline_result.json
   ```

#### 2.2 运行优化版本
```bash
# 启动服务
python -m uvicorn api.main:app --reload

# 上传测试文件
curl -X POST http://localhost:8000/api/tenders/parse \
  -F "file=@test_invitation.docx" \
  -F "bidderName=测试公司" \
  > optimized_result.json
```

#### 2.3 监控日志
```bash
tail -f logs/api.log | grep -E "PARSE INVITATION|document_structure|extraction_summary"
```

**关键日志：**
```
document_structure_parsed: paragraphs=1234 headings=56 chars=144869 seconds=0.182
invitation chunk=1 range=0:9876 chunk_chars=9876 prompt_chars=10923 ratio=1.11
extraction_summary: chunks=16 first_success_rate=93.8% retries=1 subdivisions=0
PARSE INVITATION PERFORMANCE SUMMARY
  TOTAL TIME: 78.52 seconds
  1. LLM Extract:        58.23s ( 74.2%)
  2. Document Parse:      0.18s (  0.2%)
  3. Local Rules:         8.34s ( 10.6%)
  4. DB Queries:          9.12s ( 11.6%)
```

**验收标准：**
- ✅ 文档预解析 <0.5s
- ✅ 本地规则 <15s
- ✅ 总耗时 <90s
- ✅ LLM 抽取占比 >50%

---

#### 2.4 对比提取结果
```bash
# 对比关键字段
python compare_results.py baseline_result.json optimized_result.json
```

创建 `compare_results.py`：
```python
import json
import sys

def compare_results(baseline_file, optimized_file):
    with open(baseline_file) as f:
        baseline = json.load(f)
    with open(optimized_file) as f:
        optimized = json.load(f)

    # 关键字段
    key_fields = [
        "projectName",
        "tenderer",
        "tenderNo",
        "bidContent",
        "documentFormat",
        "outlineChapter",
        "performanceRequirement",
    ]

    diffs = []
    for field in key_fields:
        b_val = baseline.get("brief", {}).get(field)
        o_val = optimized.get("brief", {}).get(field)
        if b_val != o_val:
            diffs.append(f"{field}: {b_val} -> {o_val}")

    if diffs:
        print("❌ FAILED - Differences found:")
        for diff in diffs:
            print(f"  {diff}")
        return False
    else:
        print("✅ PASSED - All key fields match!")
        return True

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python compare_results.py baseline.json optimized.json")
        sys.exit(1)
    
    passed = compare_results(sys.argv[1], sys.argv[2])
    sys.exit(0 if passed else 1)
```

**验收标准：**
- ✅ 所有关键字段一致
- ✅ outlineItems 数量一致
- ✅ requiredMaterials 完整
- ✅ bidRequirements 无遗漏

---

### Phase 3: 回归测试

#### 3.1 多样本测试
准备不同类型的标书：
1. 小型标书（<50K 字）
2. 中型标书（50-100K 字）
3. 大型标书（>100K 字）
4. 复杂格式（多表格、多章节）

对每个样本：
1. 运行优化版本
2. 记录性能指标
3. 验证提取准确性

#### 3.2 性能基线
建立性能基线表：

| 文件大小 | 字符数 | 总耗时 | LLM | 本地规则 | DB查询 |
|---------|-------|-------|-----|---------|--------|
| 小型 | 30K | 25s | 18s | 3s | 4s |
| 中型 | 80K | 55s | 42s | 6s | 7s |
| 大型 | 145K | 85s | 58s | 12s | 15s |
| 超大 | 200K+ | 120s | 80s | 18s | 22s |

**验收标准：**
- ✅ 所有样本总耗时 <2 分钟
- ✅ 本地规则耗时 <总耗时的 20%
- ✅ 提取准确率 >95%

---

### Phase 4: 压力测试

#### 4.1 并发测试
```bash
# 10 个并发请求
for i in {1..10}; do
  curl -X POST http://localhost:8000/api/tenders/parse \
    -F "file=@test_invitation.docx" \
    > result_$i.json &
done
wait
```

**验收标准：**
- ✅ 所有请求成功
- ✅ 平均响应时间 <2 分钟
- ✅ 无内存泄漏
- ✅ 无死锁

#### 4.2 长时间运行
```bash
# 连续运行 100 次
for i in {1..100}; do
  echo "Run $i"
  curl -X POST http://localhost:8000/api/tenders/parse \
    -F "file=@test_invitation.docx" \
    > /dev/null
  sleep 5
done
```

**验收标准：**
- ✅ 所有请求成功
- ✅ 性能稳定无退化
- ✅ 内存使用稳定
- ✅ 无资源泄漏

---

## 验收标准总结

### 必须项（P0）
- [x] 性能追踪器测试通过
- [x] 文档预解析测试通过
- [ ] 总耗时 <90s（144K 字）
- [ ] 本地规则 <15s
- [ ] 提取结果与优化前一致
- [ ] 回归测试全部通过

### 重要项（P1）
- [ ] 文档预解析 <0.5s
- [ ] DB 查询并行化生效
- [ ] 重复查询已消除
- [ ] 性能报告输出完整

### 可选项（P2）
- [ ] 并发测试通过
- [ ] 长时间运行稳定
- [ ] 内存占用无明显增加
- [ ] 性能基线建立

---

## 验收结论

### 通过标准
- 所有 P0 项通过
- P1 项通过 ≥80%
- P2 项通过 ≥50%

### 当前状态
- **代码实现**：✅ 完成
- **单元测试**：✅ 通过
- **集成测试**：⏳ 待执行
- **回归测试**：⏳ 待执行
- **压力测试**：⏳ 待执行

---

## 下一步

1. **准备测试环境**：部署优化版本
2. **执行集成测试**：上传 144K 字文件
3. **收集性能数据**：记录各阶段耗时
4. **验证功能一致性**：对比提取结果
5. **执行回归测试**：测试多样本
6. **生产验证**：小范围试运行

---

**创建时间**：2024-10-31  
**版本**：v1.0  
**状态**：等待测试执行
