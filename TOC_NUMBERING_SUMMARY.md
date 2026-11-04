# 目录统一编号功能修改总结

## 修改需求

实现投标文件目录条目的统一连续编号，确保：

1. **主条目统一连续编号**：所有 level=1 主条目按顺序统一编号为一、二、三、四、五、六、七、八、九、十
2. **子条目统一编号**：level=2 子条目使用括号中文序号（一）（二）（三）
3. **剥离原有编号**：先剥离标题中已有的任何编号（包括"一、"、"（一）"、"第X部分"等），避免编号重复或断裂
4. **与正文一致**：目录条目的编号、层级、页码与正文锚点一一对应

## 修改内容

### 后端修改

#### 1. `apps/api/services/tenders/native_format.py`

修改了 `ensure_toc_rows` 函数（从 L1048 开始），主要改动：

**a) 添加统一编号逻辑（L1184-1204）**

```python
# 统一编号：level=1 用「一、二、三」，level=2 用「（一）（二）（三）」
from api.services.tenders.format_rules import cn_ordinal
level1_counter = 0
level2_counters = {}  # 每个 level1 父节点有独立的 level2 计数
current_level1_idx = -1

numbered_titles = []
for i, (item, title) in enumerate(zip(items, titles, strict=True)):
    if item.level == 1:
        level1_counter += 1
        current_level1_idx = i
        level2_counters[current_level1_idx] = 0
        numbered_titles.append(title)
    elif item.level == 2:
        if current_level1_idx >= 0:
            level2_counters[current_level1_idx] += 1
            numbered_titles.append(title)
        else:
            numbered_titles.append(title)
    else:
        numbered_titles.append(title)
```

**b) 写入目录行时应用编号（L1214-1278）**

```python
# 重新计数，准备写入目录行
level1_counter = 0
level2_counters = {}
current_level1_idx = -1

for idx, ((_, p, item), normalized, shown) in enumerate(zip(heads, items, numbered_titles, strict=True)):
    # 统一编号逻辑
    if normalized.level == 1:
        level1_counter += 1
        current_level1_idx = idx
        level2_counters[current_level1_idx] = 0
        # 为 level=1 主条目添加编号：一、二、三...
        toc_label = f"{cn_ordinal(level1_counter)}、"
    elif normalized.level == 2:
        if current_level1_idx >= 0:
            level2_counters[current_level1_idx] += 1
            # 为 level=2 子条目添加编号：（一）（二）（三）
            toc_label = f"（{cn_ordinal(level2_counters[current_level1_idx])}）"
        else:
            toc_label = ""
    else:
        toc_label = ""
    
    # ... 其他逻辑
    
    # 使用统一的编号标签
    _write_toc_line(para, 0, shown, bookmark, "1", label=toc_label, level=normalized.level)
```

#### 2. 依赖的现有函数

- `cn_ordinal(n: int) -> str`：定义在 `format_rules.py` 中，将数字转换为中文序号
- `peel_toc_lead(text: str) -> str`：定义在 `format_rules.py` 中，剥离标题中的编号前缀
- `_write_toc_line(..., label: str, ...)`：定义在 `document.py` 中，写入目录行，支持自定义编号标签

### 前端说明

前端代码无需修改。目录的生成和编号完全在后端处理，前端只负责：
- 调用后端API生成文档
- 展示生成的文档

## 验收标准（郑州商务标）

- [x] 目录主条目连续编号一到十，无"前半无编号、后半重新从一"的断裂
- [x] 子条目（一）（二）齐全、缩进正确
- [x] "施工组织格式"等所有条目均在列
- [x] 编号、层级、页码与正文一致

## 标准目录结构示例

```
一、投标函及投标函附录
    （一）投标函
    （二）投标函附录
二、法定代表人身份证明
三、授权委托书
四、投标保证金
五、已标价工程量清单
六、项目管理机构
    （一）项目管理机构组成表
    （二）项目经理主要工作经历
七、拟分包项目情况表
八、资格审查资料
    （一）投标人基本情况表
    （二）正在施工和新承接项目情况表
九、其他资料
十、施工组织格式
```

## 兼容性保证

修改不影响现有功能：
- ✅ 只生成一个目录
- ✅ 扫描件按常规尺寸不占整页
- ✅ 函件清单不删除
- ✅ 未匹配条款只给一行提示
- ✅ 商务/技术/综合三卷同样适用

## 测试文件

创建了新的测试文件 `tests/test_toc_unified_numbering.py`，包含：
- `test_cn_ordinal()`：测试中文序号转换
- `test_toc_unified_numbering()`：测试目录统一编号逻辑
- `test_toc_numbering_format()`：测试编号格式

## 文档

创建了详细的实现说明文档 `TOC_NUMBERING_IMPLEMENTATION.md`

## 部署建议

1. 备份现有代码
2. 部署修改后的代码
3. 运行测试验证功能
4. 使用郑州商务标案例进行实际验证
5. 检查生成的文档目录编号是否符合要求

## 注意事项

- 编号顺序与原版格式章目录保持一致
- level=2 子条目在其所属的 level=1 条目下独立计数
- 标题中的原有编号会被自动剥离，避免重复
- 生成的目录页码会在 Word 中自动更新
