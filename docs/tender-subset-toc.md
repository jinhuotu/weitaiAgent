# 子集目录规整：入口、改动与回归

实施日期：2026-10-24。

## 实际入口与隔离范围

`assemble_bid_docx` 在 `layoutMode="outline"` 时首先调用
`pdf_format.assemble_from_word_format`。因此本次同时接入以下两个新建目录入口：

1. **有格式章的技术子集**：`assemble_from_word_format` → `_keep_technical_only`
   → `_install_tech_front` → `assemble._write_toc(..., subset=True)`。
   目录直接引用已定位的正文标题，不再让 `_link_toc_body` 用重新编号后的目录文本反查正文。
2. **没有格式章副本的大纲生成**：`assemble_bid_docx` → `assemble._write_toc`。
   只有技术卷，或显式 `splitSlice=True` 的分卷商务卷，启用 `subset=True`。

有格式章的分卷商务路径仅删除技术正文，保留格式章原目录；该路径没有重建目录，仍沿用原有
`_link_toc_body`。商务单卷、未指定卷别及整卷调用保持原路径和默认参数，不启用子集归一。
源格式文件只读，修改发生在输出副本。

## 改动清单

- `outline.py`：两条提取路径共用 `_extracted_item_level`，均识别字母子标题及 `_SUB_MARK`。
  新增 `normalize_subset_outline`，结合原始编号、正文首行编号及显式 level 推断父子关系，
  根据实际父节点深度赋级。中文顶级编号构成边界；同级括号编号纠正错误 level；点分编号、
  数字括号及显式三级节点保留真实嵌套。裁掉父章产生的层级跳跃按实际深度压缩。
  归一返回大纲副本，不回写输入。`subset_toc_titles` 按父节点重新计数，仅用于新目录。
- `assemble.py`：显式子集开关连接层级归一、唯一书签和新目录编号；
  `_anchor_section(..., subset=True)` 重复调用时复用已有锚点，纯表格前只补一次标题。
  找不到标题的文字正文也补独立标题，不将正文内容段冒充标题。
  `_fill_toc_pages(..., subset=True)` 在估页前修复空缓存，估页失败保留“待更新”。
- `document.py`：增加仅供子集调用的书签名称分配和空缓存兜底。
  空 ID、重复 ID、字符归一产生的名称碰撞，以及已有同名书签，均分配唯一名称。
  缩进仍仅由 `_apply_toc_indent` 的 level 决定，页码回填不修改段落缩进、制表位。
- `pdf_format.py`：技术子集目录复用 `assemble._write_toc` 和 `_fill_toc_pages`。
  优先用完整原始编号匹配正文，避免同名的括号子项与中文顶级项互相覆盖。
  表内标题对应的锚点放到表前独立标题；缺标题时，仅对已选技术材料按唯一表头定位。
  表头有歧义时不猜锚点，保留该目录子项和“待更新”，并在生成说明中提示。
  重复安装已生成的技术目录不会再次增加封面、目录或标题。
- `tests/test_tender_subset_toc.py`：新增 20 项验收测试。

目录统一使用右对齐点线制表位；正文标题不重新编号。技术卷总题在没有对应大纲条目时不与
实际章节混用编号计数。仅发现于正文的标题也参与新目录的统一层级与编号。

## 验证结果

以下回归共 **235 项通过**：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_tender_subset_toc.py tests/test_tender_outline.py `
  tests/test_tender_assemble.py tests/test_tender_format.py `
  tests/test_tender_volume_format.py tests/test_tender_toc_slots.py `
  tests/test_tender_slice.py -q `
  --basetemp=storage/toc_regression/pytest-release `
  -o cache_dir=storage/toc_regression/cache
```

新增验收包含：表类二/三级混杂、真实三级节点、连续点分兄弟、数字括号三级、父章裁除、
`七、拟分包项目情况表` 顶级边界、按父节点重新计数、正文不重编号、同层缩进、右对齐点线、
表内标题/纯表格补标题、多次生成和重复安装、原始大纲不变、源格式文件不变、重复 ID、
已有书签冲突、完全/部分估页失败、空域缓存修复，以及表头歧义和商务材料隔离。

新增测试文件通过 Ruff 检查。源文件全量 Ruff 仍有导入顺序、长行等告警，本次未进行全文件整理。

## 商务单卷及整卷基线

实施前保存了 6 组本地构造样例：有格式章/无格式章，分别调用显式商务卷、默认单卷及整卷。
实施后重新生成，按 ZIP 内条目名称逐一比较内容 SHA-256，并比较生成提示，忽略 ZIP 容器时间戳。

**6/6 组完全一致，变化条目为 0。** XML、关系文件及资源均纳入比较，不只比较可见文字。

本地证据保存在忽略提交的 `storage/toc_regression/`：

- `check_baseline.py`：基线生成和比较脚本；
- `before.json`、`after.json`：各包全部条目的 SHA-256 和生成提示；
- `before-*.docx`、`after-*.docx`：修改前后样例；
- `pytest-final.txt`：完整回归输出。

复查命令：`.\.venv\Scripts\python.exe storage/toc_regression/check_baseline.py after`。

页码验证覆盖生成的 DOCX 域结果及估页逻辑，尚未通过 Word/WPS 实际排版核验。估算页码可能与
最终排版不同；无法安全估算时显示“待更新”。本次基线为构造样例，不代表所有历史业务标书。
