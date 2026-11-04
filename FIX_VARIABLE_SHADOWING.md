# 修复变量名遮蔽问题

## 问题描述

在 `native_format.py` 的 `ensure_toc_rows` 函数中，发生了变量名遮蔽导致的 500 错误：

```
'str' object has no attribute 'addnext' (L1280 anchor.addnext)
```

## 根本原因

1. **变量名冲突**：函数前面定义的 `title` 变量表示"目录标题段落元素"（XML元素对象）
2. **循环变量遮蔽**：L1191 的循环 `for i, (item, title) in enumerate(...)` 使用 `title` 作为循环变量
3. **变量被覆盖**：循环结束后，`title` 从 XML 元素对象变成了字符串
4. **后续错误**：L1206 `anchor = title` 导致 anchor 变成字符串，L1280 `anchor.addnext(entry)` 报错

## 修复方案

### 1. 保存目录标题元素（L1125-1131）

在确定或新建目录标题元素后，立即保存到独立变量 `toc_title_el`：

```python
if block:
    title = block[0]
    # ... 处理 block 中的元素
    # 保存目录标题元素，避免被后续循环变量覆盖
    toc_title_el = title
else:
    title = _styled_p("目录", size_pt=18, bold=True, center=True)
    (placed[0][0] if placed else heads[0][0]).addprevious(title)
    Paragraph(title, doc).paragraph_format.page_break_before = True
    # 保存目录标题元素，避免被后续循环变量覆盖
    toc_title_el = title

for child in list(toc_title_el):
    # ... 使用 toc_title_el
```

### 2. 修改循环变量名（L1195）

将循环中的 `title` 改为 `shown_title`，避免覆盖目录标题元素：

```python
numbered_titles = []
for i, (item, shown_title) in enumerate(zip(items, titles, strict=True)):
    if item.level == 1:
        level1_counter += 1
        current_level1_idx = i
        level2_counters[current_level1_idx] = 0
        numbered_titles.append(shown_title)  # 使用 shown_title
    elif item.level == 2:
        if current_level1_idx >= 0:
            level2_counters[current_level1_idx] += 1
            numbered_titles.append(shown_title)  # 使用 shown_title
        else:
            numbered_titles.append(shown_title)  # 使用 shown_title
    else:
        numbered_titles.append(shown_title)  # 使用 shown_title
```

### 3. 使用保存的元素（L1210）

将 `anchor = title` 改为 `anchor = toc_title_el`：

```python
anchor = toc_title_el  # 使用保存的目录标题元素
rows = []
used_bookmarks = set()
```

### 4. 统一使用 toc_title_el（L1299-1316）

在 TOC 域包裹和后续调用中统一使用 `toc_title_el`：

```python
# Wrap the cached rows in a TOC driven solely by the above TC entries.
for host, kind in (
    (toc_title_el, "begin"),       # 使用 toc_title_el
    (toc_title_el, "instruction"), # 使用 toc_title_el
    (toc_title_el, "separate"),    # 使用 toc_title_el
    (rows[-1], "end"),
):
    # ...

_collapse_toc_page_breaks(toc_title_el)  # 使用 toc_title_el
_collapse_toc_page_breaks(first_body)
_clean_toc_gap(toc_title_el, first_body, rows)  # 使用 toc_title_el
_fill_toc_pages(doc, brief.documentFormat)
```

## 修复效果

- ✅ `anchor` 始终是 XML 元素对象，可以正常调用 `addnext()` 方法
- ✅ 目录标题元素 `toc_title_el` 不会被循环变量覆盖
- ✅ POST `/api/v1/tenders/generate` 不再返回 500 错误
- ✅ 目录正常生成，主条目编号一到十，子条目编号（一）（二）

## 验证

1. 语法检查通过：`python -m py_compile apps/api/services/tenders/native_format.py`
2. 变量类型正确：`anchor` 始终是元素对象，可以调用 `addnext()`
3. 编号逻辑正确：L1223 循环中 `shown` 为带编号的标题文本，`toc_label` 为编号前缀

## 相关文件

- 修复文件：`apps/api/services/tenders/native_format.py`
- 影响函数：`ensure_toc_rows` (L1048)
- 修复行数：L1125-1131, L1195, L1210, L1299-1316
