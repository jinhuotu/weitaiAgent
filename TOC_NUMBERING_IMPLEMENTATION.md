# 目录统一编号实现说明

## 修改概述

实现了目录条目的统一连续编号功能，确保所有主条目（level=1）使用统一的中文序号（一、二、三...十），子条目（level=2）使用统一的括号中文序号（（一）（二）（三）...）。

## 修改文件

### 1. `apps/api/services/tenders/native_format.py`

修改了 `ensure_toc_rows` 函数（L1048），在写入目录行之前统一为所有条目添加编号。

#### 主要修改点

1. **添加编号计数器**（L1184-1188）：
```python
# 统一编号：level=1 用「一、二、三」，level=2 用「（一）（二）（三）」
from api.services.tenders.format_rules import cn_ordinal
level1_counter = 0
level2_counters = {}  # 每个 level1 父节点有独立的 level2 计数
current_level1_idx = -1
```

2. **第一轮计数**（L1190-1204）：
   - 遍历所有条目，为每个 level1 和 level2 条目准备编号
   - level2 的编号在其所属的 level1 条目下独立计数

3. **写入目录行时应用编号**（L1214-1235）：
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
```

4. **传递编号给写入函数**（L1278）：
```python
_write_toc_line(para, 0, shown, bookmark, "1", label=toc_label, level=normalized.level)
```

## 功能特点

1. **统一连续编号**：
   - 主条目（level=1）按出现顺序统一编号：一、二、三、四、五、六、七、八、九、十
   - 不依赖原版的 numbering 属性，避免编号丢失或不连续

2. **标题剥离**：
   - 使用 `peel_toc_lead` 函数剥离标题中已有的任何编号
   - 避免出现"前半无编号、后半从一重新开始"的断裂问题

3. **子条目编号**：
   - level=2 子条目统一使用括号中文序号：（一）（二）（三）...
   - 每个 level1 主条目下的子条目独立计数

4. **编号顺序**：
   - 编号顺序与原版格式章目录保持一致
   - 标准顺序：一、投标函及投标函附录；二、法定代表人身份证明；三、授权委托书；四、投标保证金；五、已标价工程量清单；六、项目管理机构；七、拟分包项目情况表；八、资格审查资料；九、其他资料；十、施工组织格式

## 验证要点（郑州商务标）

- [x] 目录主条目连续编号一到十，无断裂
- [x] 子条目（一）（二）齐全、缩进正确
- [x] "施工组织格式"等所有条目均在列
- [x] 编号、层级、页码与正文一致

## 兼容性

- 保留了已有的功能：
  - 只一个目录
  - 扫描件按常规尺寸不占整页
  - 函件清单不删
  - 未匹配条款只给一行提示
  - 商务/技术/综合三卷同样适用

## 依赖函数

- `cn_ordinal(n: int) -> str`：将数字转换为中文序号（定义在 `format_rules.py`）
- `peel_toc_lead(text: str) -> str`：剥离标题中的编号前缀
- `_write_toc_line(..., label: str, ...)`：写入目录行，支持自定义编号标签
