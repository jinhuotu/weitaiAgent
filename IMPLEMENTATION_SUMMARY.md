# 招标文件抽取优化实施总结

## 📊 优化成果预期

| 指标 | 优化前 | 优化后（目标） | 提升 |
|-----|-------|---------------|------|
| 首次成功率 | 75% (12/16) | ≥95% (≥15/16) | +20% |
| 422/504 错误 | 4 次 | ≤1 次 | -75% |
| 单块 prompt 字符 | ~14,848 | ~11,000 | -27% |
| 并发数 | 3 | 6 | +100% |
| 总耗时 | 248 秒 | <150 秒 | -40%+ |
| 重复输入字符 | 80K | ~16K | -80% |

## ✅ 已完成的优化

### 1. 消除 422 错误（首要项）

#### ✅ 结构化输出约束
- 创建精简 system prompt（`extract_system_optimized.md`）
- 明确 JSON schema，禁止额外文本
- 强制必须为空的字段（`quoteLines=[]`, `performanceLines=[]`）
- 禁止生成长篇技术方案

#### ✅ 增强解析容错
- 实现 `repair_json()` 函数：
  - 移除 markdown 代码块标记
  - 提取 JSON 对象边界
  - 修复尾随逗号
- 测试通过 6/6 用例
- 增强日志：记录 422 原因和 raw 预览

#### ✅ 修复 ValueError
- 增强 `material_intents.py` 错误日志
- 记录具体异常信息和 chunk_size

**文件**：
- `apps/api/services/tenders/extract_chunks_optimized.py`
- `apps/api/services/tenders/prompts/extract_system_optimized.md`
- `apps/api/services/tenders/extract.py`（已修改）
- `apps/api/services/tenders/material_intents.py`（已修改）

---

### 2. 公共上下文只送一次

#### ✅ 轻量级 per-chunk prompt
原问题：每块 prompt = 块正文(~10K) + 公共上下文(~5K)
- 资料库清单（catalog_rows）
- 历史 playbooks
- 知识库摘录（kb_hits）

**16 块 × 5K = 80K 字重复输入**

优化策略：
- System prompt 包含通用规则
- 轻量级资料库清单：只发 `key:title`，不发 `hint`
- 移除 playbooks 和 kb_hits（在 chunk prompt 中）
- Per-chunk prompt 只含：块元信息 + 投标人 + 块正文 + 资料库摘要

**预期节省**：
- 单块：15K → 11K（-27%）
- 总计：64K 字符减少

**实现**：
```python
def make_chunk_prompt(chunk_text: str) -> str:
    bidder = (current.bidderName if current else "") or "【待补充】"
    parts = [
        f"投标人（bidderName）：{bidder}",
        "",
        "【邀请书正文片段】",
        chunk_text,
    ]
    if catalog_rows:
        parts.append("")
        parts.append("【资料库清单·复用 key 或保留 title】")
        catalog_summary = ", ".join(
            f"{row.get('key', '')}:{row.get('title', '')[:20]}"
            for row in catalog_rows[:30]
        )
        parts.append(catalog_summary)
    return "\n".join(parts)
```

---

### 3. 并发自适应提速

#### ✅ 提高并发度
- 从 `Semaphore(3)` → `Semaphore(6)`
- 可配置并发数

#### ✅ 智能退避
- 429 错误时 sleep(2) 后重试
- 可配置退避策略

#### ✅ 细粒度分块
- 504 超时时智能分块：从 `len//2` → 固定 `4000` 字
- 大块不阻塞整体流程

**预期效果**：
- 理想：248s / 2 = 124s
- 现实（考虑重试和网络）：150-180s

**文件**：
- `apps/api/services/tenders/extraction_config.py`（配置管理）

---

### 4. 量化监控

#### ✅ 详细指标输出
优化版本会输出：

```
invitation chunk=1 range=0:9876 chunk_chars=9876 prompt_chars=10923 ratio=1.11
invitation chunk=1 chars=9876 attempts=1 seconds=12.456
...
extraction_summary: chunks=16 first_success_rate=93.8% retries=1 
  subdivisions=0 avg_prompt_chars=11234 total_seconds=124.56
```

**关键指标**：
- `first_success_rate`：首次成功率，目标 ≥95%
- `retries`：重试次数，目标 ≤1
- `subdivisions`：分块次数，目标 =0
- `avg_prompt_chars`：平均 prompt 字符，目标 ~11K
- `ratio`：prompt/chunk 比率，目标 ~1.1（原 ~1.5）

---

## 📦 交付清单

### 新增文件
1. ✅ `apps/api/services/tenders/extract_chunks_optimized.py` - 优化核心逻辑
2. ✅ `apps/api/services/tenders/prompts/extract_system_optimized.md` - 精简 prompt
3. ✅ `apps/api/services/tenders/extraction_config.py` - 配置管理
4. ✅ `EXTRACTION_OPTIMIZATION.md` - 详细文档
5. ✅ `IMPLEMENTATION_SUMMARY.md` - 本文件
6. ✅ `test_json_repair.py` - 测试脚本

### 修改文件
1. ✅ `apps/api/services/tenders/extract.py` - 集成优化版本
2. ✅ `apps/api/services/tenders/material_intents.py` - 增强错误日志

---

## 🚀 使用方法

### 启用优化（默认已启用）

在 `extract.py` 第 691 行：
```python
use_optimized = True  # 默认启用
```

### 配置调优

```python
from api.services.tenders.extraction_config import set_extraction_config

# 提高并发
set_extraction_config(extraction_concurrency=8)

# 调整分块大小
set_extraction_config(chunk_size=12000)

# 禁用优化（回退）
set_extraction_config(use_optimized_extraction=False)
```

### 查看指标

查看日志，搜索：
- `invitation chunk=` - 单块处理
- `extraction_summary` - 总体指标
- `parse_extract_payload failed` - 解析失败详情

---

## 🔄 回退方案

### 方法 1：代码切换（推荐）
```python
# extract.py 第 691 行
use_optimized = False
```

### 方法 2：配置切换
```python
set_extraction_config(use_optimized_extraction=False)
```

### 方法 3：删除文件
删除 `extract_chunks_optimized.py`，自动 fallback。

---

## ✅ 测试检查清单

### 功能测试
- [x] JSON repair 测试通过（6/6）
- [ ] 上传 144,869 字测试文件
- [ ] 查看 `extraction_summary` 日志
- [ ] 确认 `first_success_rate ≥ 95%`
- [ ] 确认 `total_seconds < 150`
- [ ] 确认 `avg_prompt_chars ≈ 11000`
- [ ] 确认 `retries ≤ 1`

### 质量测试
- [ ] 对比抽取结果与原版（关键字段一致）
- [ ] 测试 3-5 个不同类型招标文件
- [ ] 验证业绩门槛、偏离表、资料清单无遗漏

### 生产验证
- [ ] 小规模试运行 1-2 天
- [ ] 监控错误率和耗时
- [ ] 收集用户反馈

---

## 📈 预期收益

### 性能收益
- **耗时减少 40%+**：248s → <150s
- **重复输入减少 80%**：80K → 16K 字符
- **首次成功率提升 20%**：75% → 95%+
- **并发度翻倍**：3 → 6

### 成本收益
- **Token 使用减少 ~25%**：prompt 平均缩短 27%
- **API 调用减少 ~75%**：重试从 4 次 → ≤1 次
- **用户体验提升**：2 分钟内完成，前端不再转圈

### 可维护性收益
- **详细监控**：可量化的成功率、耗时、prompt 大小
- **灵活配置**：可随时调整并发、分块策略
- **快速回退**：一行代码切换新旧版本

---

## 🔍 技术亮点

### 1. 上下文去重
通过将公共上下文（资料库清单、playbooks、KB 摘录）从 per-chunk prompt 移到 system prompt 或精简化，实现：
- 单块 prompt 从 15K → 11K
- 16 块节省 64K 字符

### 2. JSON 修复
`repair_json()` 自动修复 LLM 常见错误：
- 移除 markdown 标记
- 提取 JSON 边界
- 修复尾随逗号
- 测试通过率 100%

### 3. 智能重试
- 422 错误：尝试修复后重试
- 429 错误：退避后重试
- 504 错误：智能分块
- 其他错误：一次重试

### 4. 细粒度监控
- 每块：chars, prompt_chars, ratio, attempts, seconds
- 总体：first_success_rate, retries, subdivisions, avg_prompt_chars

---

## 🛠️ 后续优化方向

### 短期（1-2 周）
1. 监控生产环境，调优并发和分块
2. 针对特定模型微调 prompt
3. 增加缓存命中率统计

### 中期（1-2 月）
1. 支持流式 JSON 解析
2. 基于语义边界的智能分块
3. 重试策略分类（按错误类型）

### 长期（3-6 月）
1. 多模型并行抽取+投票
2. 增量抽取（只抽变更部分）
3. 优化知识库检索（向量化缓存）

---

## 📞 联系与支持

如有问题或建议：
1. 查看详细文档：`EXTRACTION_OPTIMIZATION.md`
2. 检查日志：搜索 `extraction_summary` 和 `invitation chunk=`
3. 回退到原版：`use_optimized = False`

---

**优化完成时间**：2024-10-31  
**优化版本**：v1.0  
**测试状态**：JSON repair 测试通过，等待集成测试  
**推荐行动**：启用优化 → 监控指标 → 根据实测调优
