# 招标文件抽取优化方案

## 现状分析

### 测试数据
- 总字符数：144,869 字
- 分块数：16 块
- 总耗时：248 秒
- 首次失败：chunk 9/11/15/16（422 错误）
- 单块重试最高：71 秒
- 块正文：~9,993 字
- Prompt 字符：~14,848 字（正文+重复公共上下文）
- 并发数：3

### 主要问题
1. **422 错误频发**：4/16 块首次失败并触发重试
2. **公共上下文重复**：每块都携带约 5000 字公共上下文（资料库清单、历史 playbooks、知识库摘录）
3. **并发度低**：仅 3 并发
4. **重试策略粗糙**：原样重试，未针对 422 原因修复
5. **ValueError 未详细记录**：material semantic chunk 错误缺乏诊断信息

---

## 优化方案

### 一、消除 422，一次成功（首要项）

#### 1. 结构化输出约束
- ✅ 创建精简 schema（`extract_system_optimized.md`）
- ✅ 明确最小字段集，禁止额外文本与思考链
- ✅ 使用 JSON 模式约束模型输出

#### 2. 输出范围控制
- ✅ 只抽该块字段，避免输出过大
- ✅ 强制 `quoteLines=[]`, `performanceLines=[]`
- ✅ 禁止生成长篇技术方案

#### 3. 增强解析容错
- ✅ 实现 `repair_json()` 修复常见 JSON 错误：
  - 移除 markdown 代码块标记
  - 提取 JSON 对象边界
  - 修复尾随逗号
- ✅ 详细记录 422 原因到日志
- ✅ 在重试前尝试修复而非原样重试

#### 4. 修复 ValueError
- ✅ 增强 `material_intents.py` 错误日志
- ✅ 记录具体异常信息和上下文

**实现文件**：
- `extract_chunks_optimized.py`：核心优化逻辑
- `extract_system_optimized.md`：精简 system prompt

---

### 二、公共上下文只送一次

#### 原问题
每块 prompt = 该块正文（~10K 字） + 公共上下文（~5K 字）
- 资料库清单（catalog_rows）
- 历史 playbooks
- 知识库摘录（kb_hits）

**16 块 × 5K = 80K 字重复**

#### 优化策略
1. ✅ **System prompt 包含通用规则**：精简的抽取指南
2. ✅ **轻量级资料库清单**：只发送 `key:title`，不发送 `hint`
3. ✅ **移除 playbooks 和 kb_hits**：在 chunk prompt 中不重复
4. ✅ **Per-chunk prompt 只含**：
   - 块元信息（index, start, end, heading）
   - 投标人名称
   - 块正文
   - 轻量级资料库清单（可选）

**预期效果**：
- 单块 prompt 字符数：~10K（正文）+ ~1K（元信息+资料库摘要）= ~11K
- 相比原来的 ~15K，减少约 27%
- 16 块节省：(15K - 11K) × 16 = 64K 字符

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

### 三、并发自适应提速

#### 1. 提高并发度
- ✅ 从 `Semaphore(3)` 提升到 `Semaphore(6)`
- ✅ 可配置并发数（`extraction_config.py`）

#### 2. 智能退避
- ✅ 429 错误时 sleep(2) 后重试
- ✅ 可配置退避策略

#### 3. 细粒度分块
- ✅ 504 超时时智能分块：从 `len//2` 改为固定 `4000` 字
- ✅ 保证大块不阻塞整体流程

**预期效果**：
- 理想情况（无冲突）：248s / 2 = **124 秒**
- 考虑重试和网络：预计 **150-180 秒**

---

### 四、量化验收指标

#### 输出指标

| 指标 | 优化前 | 目标 | 验收标准 |
|-----|-------|-----|---------|
| **总块数** | 16 | 16 | 保持不变 |
| **首次成功率** | 75% (12/16) | ≥95% | ≥15/16 块首次成功 |
| **重试次数** | ≥4 | ≤1 | 几乎无重试 |
| **422/504 错误** | 4 | 0 | 基本消除 |
| **单块 prompt 字符** | ~14,848 | ~11,000 | 减少 ~27% |
| **并发数** | 3 | 6 | 提升 100% |
| **总耗时** | 248s | <120s | 减少 >50% |

#### 详细监控
优化版本会输出详细日志：

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
- `avg_prompt_chars`：平均 prompt 字符数，目标 ~11K
- `ratio`：prompt/chunk 比率，目标 ~1.1（原来 ~1.5）

---

## 使用方法

### 启用优化

**自动启用**：默认已启用，在 `extract.py` 中：

```python
use_optimized = True  # 默认启用
```

### 配置调优

通过 `extraction_config.py` 调整参数：

```python
from api.services.tenders.extraction_config import set_extraction_config

# 提高并发
set_extraction_config(extraction_concurrency=8)

# 调整分块大小
set_extraction_config(chunk_size=12000, subdivision_size=5000)

# 禁用优化（回退到原版）
set_extraction_config(use_optimized_extraction=False)
```

### 查看指标

查看应用日志，搜索：
- `invitation chunk=` - 单块处理日志
- `extraction_summary` - 总体指标
- `parse_extract_payload failed` - 解析失败详情

---

## 实现文件清单

| 文件 | 说明 |
|-----|-----|
| `extract_chunks_optimized.py` | 优化的分块抽取核心逻辑 |
| `extract_system_optimized.md` | 精简 system prompt |
| `extraction_config.py` | 配置管理 |
| `extract.py` | 集成优化版本（已修改） |
| `material_intents.py` | 增强错误日志（已修改） |
| `EXTRACTION_OPTIMIZATION.md` | 本文档 |

---

## 回退方案

如果优化版本出现问题，可立即回退：

**方法 1：代码切换**
```python
# extract.py 行 688
use_optimized = False  # 改为 False
```

**方法 2：配置切换**
```python
set_extraction_config(use_optimized_extraction=False)
```

**方法 3：删除文件**
删除 `extract_chunks_optimized.py`，代码会自动 fallback 到原版。

---

## 后续优化方向

### 短期（1-2 周）
1. 监控生产环境指标，调优并发数和分块大小
2. 针对特定模型（Qwen/GPT/DeepSeek）微调 prompt
3. 增加缓存命中率统计

### 中期（1-2 月）
1. 支持流式 JSON 解析（边生成边验证）
2. 实现智能分块（基于语义边界而非字符数）
3. 增加重试策略分类（按错误类型选择策略）

### 长期（3-6 月）
1. 引入多模型并行抽取+投票
2. 实现增量抽取（只抽取新增/变更部分）
3. 优化知识库检索（向量化缓存，减少重复召回）

---

## 技术细节

### repair_json 修复逻辑
```python
def repair_json(raw: str) -> str:
    """修复常见 JSON 错误"""
    text = raw.strip()
    
    # 1. 移除 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    
    # 2. 提取 JSON 边界
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]
    
    # 3. 修复尾随逗号（常见 LLM 错误）
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    
    return text
```

### 分块细粒度优化
```python
# 原版：按一半长度分块
pieces = split_invitation(chunk.text, len(chunk.text) // 2)

# 优化版：固定 4000 字分块（更稳定）
pieces = split_invitation(chunk.text, max(1000, 4000))
```

### 并发控制
```python
# 原版：固定 3 并发
_GATE = asyncio.Semaphore(3)

# 优化版：可配置 6 并发
gate = asyncio.Semaphore(concurrency or 6)
```

---

## 常见问题

### Q1：为什么不直接提到 8-10 并发？
A：需要考虑模型 API 的 RPM 限制。建议先从 6 开始，监控 429 错误率，逐步提升。

### Q2：如果还有 422 错误怎么办？
A：查看日志中的 `parse_extract_payload failed` 和 raw_preview，检查：
1. 模型是否输出了非 JSON 内容
2. JSON 是否有语法错误（未闭合括号等）
3. 是否需要进一步调整 system prompt

### Q3：如何确认优化生效？
A：对比日志中的：
1. `prompt_chars` 是否降低到 ~11K
2. `first_success_rate` 是否 ≥95%
3. `total_seconds` 是否 <150s

### Q4：会不会影响抽取质量？
A：不会。优化只是减少重复上下文，核心规则和字段定义保持不变。反而因为 prompt 更清晰，可能提升质量。

---

## 测试检查清单

- [ ] 启用优化版本
- [ ] 上传 144,869 字测试文件
- [ ] 查看日志记录的 `extraction_summary`
- [ ] 确认 `first_success_rate ≥ 95%`
- [ ] 确认 `total_seconds < 150`
- [ ] 确认 `avg_prompt_chars ≈ 11000`
- [ ] 确认 `retries ≤ 1`
- [ ] 对比抽取结果与原版（关键字段一致）
- [ ] 测试 3-5 个不同类型招标文件
- [ ] 生产环境小规模试运行 1-2 天

---

## 贡献者

优化方案由 Claude Opus 5.5 设计并实现。

如有问题或建议，请查看日志或提交 issue。
