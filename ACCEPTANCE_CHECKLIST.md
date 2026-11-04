# 招标文件抽取优化验收清单

## ✅ 代码完成情况

### 核心文件（新增）
- [x] `apps/api/services/tenders/extract_chunks_optimized.py` - 优化核心逻辑
  - [x] 可配置并发（6 并发）
  - [x] JSON 修复逻辑
  - [x] 智能重试策略
  - [x] 详细指标收集
  - [x] 细粒度分块（4000 字）

- [x] `apps/api/services/tenders/prompts/extract_system_optimized.md` - 精简 prompt
  - [x] 明确 JSON schema
  - [x] 禁止思考链和额外文本
  - [x] 强制必须为空的字段

- [x] `apps/api/services/tenders/extraction_config.py` - 配置管理
  - [x] 可配置并发数
  - [x] 可配置分块大小
  - [x] 可启用/禁用优化

### 核心文件（修改）
- [x] `apps/api/services/tenders/extract.py` - 集成优化版本
  - [x] 第 691 行：`use_optimized = True`
  - [x] 第 699-738 行：轻量级 chunk prompt
  - [x] 第 127-137 行：增强错误日志

- [x] `apps/api/services/tenders/material_intents.py` - 增强错误日志
  - [x] 第 379-391 行：ValueError 详细日志

### 文档（新增）
- [x] `EXTRACTION_OPTIMIZATION.md` - 详细技术文档
- [x] `IMPLEMENTATION_SUMMARY.md` - 实施总结
- [x] `QUICK_REFERENCE.md` - 快速参考
- [x] `FIX_VARIABLE_SHADOWING.md` - 问题记录（已存在）
- [x] `FIX_PARSE_ERRORS.md` - 问题记录（已存在）

### 测试文件
- [x] `test_json_repair.py` - JSON 修复测试（通过 6/6）

---

## 🎯 验收指标

### 性能指标
| 指标 | 优化前 | 目标 | 验收标准 |
|-----|-------|-----|---------|
| 首次成功率 | 75% | ≥95% | `first_success_rate ≥ 0.95` |
| 总耗时 | 248s | <150s | `total_seconds < 150` |
| 单块 prompt | ~15K | ~11K | `avg_prompt_chars ≈ 11000` |
| Prompt/Chunk 比率 | ~1.5 | ~1.1 | `ratio ≈ 1.1` |
| 并发数 | 3 | 6 | 配置中 `extraction_concurrency=6` |
| 重试次数 | ≥4 | ≤1 | `retries ≤ 1` |
| 分块次数 | 未知 | 0 | `subdivisions = 0` |

### 质量指标
- [ ] 关键字段无遗漏（projectName, tenderer, bidContent）
- [ ] requiredMaterials 完整性
- [ ] performanceRequirement 准确性
- [ ] deviationLines 覆盖率
- [ ] 与原版抽取结果一致性 ≥95%

---

## 🧪 测试计划

### Phase 1: 单元测试（已完成）
- [x] JSON repair 测试：6/6 通过
- [ ] Chunking 逻辑测试
- [ ] Config 管理测试

### Phase 2: 集成测试（待执行）
- [ ] 上传 144,869 字测试文件
- [ ] 查看 `extraction_summary` 日志
- [ ] 验证性能指标达标
- [ ] 对比抽取结果与原版

### Phase 3: 场景测试（待执行）
- [ ] 小型招标文件（<50K 字）
- [ ] 中型招标文件（50-100K 字）
- [ ] 大型招标文件（>100K 字）
- [ ] 复杂格式（多表格、多章节）
- [ ] 边界情况（纯文字、纯表格、图文混合）

### Phase 4: 生产验证（待执行）
- [ ] 小规模试运行 1-2 天
- [ ] 监控错误率
- [ ] 监控耗时分布
- [ ] 收集用户反馈

---

## 📋 执行步骤

### Step 1: 启用优化（默认已启用）
```python
# apps/api/services/tenders/extract.py 第 691 行
use_optimized = True  # ✅ 已设置
```

### Step 2: 调整配置（可选）
```python
from api.services.tenders.extraction_config import set_extraction_config

# 根据实际情况调整
set_extraction_config(
    extraction_concurrency=6,  # 可改为 4-8
    chunk_size=10000,
    enable_json_repair=True,
)
```

### Step 3: 重启服务
```bash
# 重启 API 服务以加载新代码
systemctl restart weitai-api
# 或
docker-compose restart api
```

### Step 4: 执行测试
```bash
# 上传测试文件并观察日志
tail -f logs/api.log | grep -E "extraction_summary|invitation chunk"
```

### Step 5: 分析日志
查找关键指标：
```
extraction_summary: chunks=16 first_success_rate=93.8% retries=1 
  subdivisions=0 avg_prompt_chars=11234 total_seconds=124.56
```

### Step 6: 验收判断
- ✅ `first_success_rate ≥ 95%` → 通过
- ✅ `total_seconds < 150` → 通过
- ✅ `avg_prompt_chars ≈ 11000` → 通过
- ✅ `retries ≤ 1` → 通过
- ✅ `subdivisions = 0` → 通过

---

## 🚨 回退计划

### 触发条件
- `first_success_rate < 85%`
- `total_seconds > 200`
- 抽取结果质量明显下降
- 生产环境频繁报错

### 回退方法

#### 方法 1：代码回退（最快）
```python
# extract.py 第 691 行
use_optimized = False
```
重启服务即可。

#### 方法 2：配置回退
```python
set_extraction_config(use_optimized_extraction=False)
```

#### 方法 3：文件回退
```bash
# 备份优化文件
mv extract_chunks_optimized.py extract_chunks_optimized.py.bak

# 代码自动 fallback 到原版
```

---

## 📊 监控要点

### 日志关键字
```bash
# 成功率
grep "first_success_rate" logs/api.log

# 耗时
grep "total_seconds" logs/api.log

# 错误
grep -E "422|504|parse_extract_payload failed" logs/api.log

# Prompt 大小
grep "avg_prompt_chars" logs/api.log
```

### 指标面板（建议）
1. **首次成功率**：每小时统计，应 ≥95%
2. **平均耗时**：每次抽取，应 <150s
3. **422 错误率**：每小时统计，应 <2%
4. **504 错误率**：每小时统计，应 =0%
5. **重试率**：每次抽取，应 <10%

---

## ✅ 最终验收

### 必须项
- [ ] 所有测试文件通过
- [ ] 性能指标达标（5/5）
- [ ] 质量指标达标（5/5）
- [ ] 生产试运行无重大问题
- [ ] 回退方案验证通过

### 可选项
- [ ] 性能超过目标 20%+
- [ ] 用户反馈积极
- [ ] 错误日志明显减少
- [ ] Token 使用成本降低

---

## 📝 签字确认

| 角色 | 姓名 | 签字 | 日期 |
|-----|-----|-----|-----|
| 开发者 | | | |
| 测试者 | | | |
| 技术负责人 | | | |
| 产品负责人 | | | |

---

## 📎 附录

### 相关文档
- `EXTRACTION_OPTIMIZATION.md` - 详细技术方案
- `IMPLEMENTATION_SUMMARY.md` - 实施总结
- `QUICK_REFERENCE.md` - 快速参考卡

### 配置示例
```python
# 高性能配置（适合独享 GPU）
set_extraction_config(
    extraction_concurrency=8,
    chunk_size=12000,
    subdivision_size=5000,
)

# 保守配置（避免 API 限流）
set_extraction_config(
    extraction_concurrency=4,
    chunk_size=8000,
    enable_backoff=True,
)

# 调试配置（详细日志）
set_extraction_config(
    log_chunk_metrics=True,
    log_final_summary=True,
)
```

---

**创建时间**：2024-10-31  
**版本**：v1.0  
**状态**：等待测试验收
