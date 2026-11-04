# 投标文件特殊标记功能

> 三级条款识别、暗标硬格式、签章校验、采购政策识别

## 快速开始

### 1. 运行测试

```bash
python -m pytest tests/test_tender_special_marks.py -v
```

预期输出：
```
✓ test_three_level_marks PASSED
✓ test_signature_check PASSED
✓ test_procurement_policy PASSED
✓ test_signature_reminders PASSED
```

### 2. 查看示例

```bash
python examples/special_marks_demo.py
```

### 3. 集成到项目

```python
# 三级条款识别
from api.services.tenders.classifier import _requirement_strength
strength = _requirement_strength("★ 必须响应")  # "mandatory"

# 签章校验
from api.services.tenders.signature_check import check_signature_positions
result = check_signature_positions(doc_text)

# 采购政策识别
from api.services.tenders.signature_check import extract_procurement_policy
policy = extract_procurement_policy(invitation_text)

# 暗标格式
from api.services.tenders.format_rules import apply_anonymous_tech_format
notes = apply_anonymous_tech_format(doc, brief)
```

## 功能概览

### ★▲* 三级条款

- **mandatory（★/※）** - 必须完全响应，负偏离=废标
- **important（▲）** - 尽量响应，负偏离扣分
- **general** - 一般要求

### 暗标硬格式

- 宋体 14 磅黑字白底
- 行距固定 28 磅
- 无加粗/斜体/下划线
- 无页眉页脚
- 严禁投标人信息

### 签章校验

- 投标人盖章
- 法定代表人/委托代理人签字
- 日期
- 自动提醒缺失项

### 采购政策

- ★ 节能产品认证
- ★ 环境标志认证（十环）
- ★ 本国产品声明

## 文档

- **SPECIAL_MARKS_IMPLEMENTATION.md** - 完整实现文档（1500+ 字）
- **IMPLEMENTATION_SUMMARY.md** - 实现总结
- **examples/special_marks_demo.py** - 集成示例

## 测试

```bash
# 运行所有特殊标记测试
python -m pytest tests/test_tender_special_marks.py -v

# 运行特定测试
python -m pytest tests/test_tender_special_marks.py::test_three_level_marks -v
```

## 代码变更

### 修改的文件
- `apps/api/services/tenders/classifier.py` - 三级条款识别
- `apps/api/services/tenders/gen_biz_resp.py` - 商务响应补充 ▲
- `apps/api/services/tenders/gen_tech_plan.py` - 技术方案补充 ▲
- `apps/api/services/tenders/format_rules.py` - 暗标硬格式
- `apps/api/services/tenders/pdf_format.py` - 暗标封面

### 新增的文件
- `apps/api/services/tenders/signature_check.py` - 签章校验+采购政策
- `tests/test_tender_special_marks.py` - 功能测试

## 验收标准

✅ ★▲*三级条款分级识别并分级响应  
✅ 暗标技术标无投标人信息且符合盲评硬格式  
✅ 关键签章位齐全并标注  
✅ 强制采购/本国产品要求附证书/声明函提示  

## 技术栈

- Python 3.12+
- python-docx（Word 文档处理）
- pytest（测试框架）
- dataclasses（数据结构）

## 注意事项

1. 暗标模式仅用于技术标
2. 签章校验不修改文档，仅提示
3. 采购政策识别基于关键词匹配
4. 三级条款识别支持自定义扩展

## 后续优化

- [ ] 占位符自动填充
- [ ] 质量检查报告
- [ ] 更多采购政策识别
- [ ] 签章位模板插入
- [ ] 前端界面集成

---

**状态**: ✅ 生产就绪  
**测试**: ✅ 全部通过  
**文档**: ✅ 完整  
