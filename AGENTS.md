# AGENTS.md · 电缆参数型标书（cablepkg）

本文件约束所有在本仓库工作的 AI 代理与开发者。**与其它需求冲突时，本文件优先。**

## 1. 任务边界

新功能只做**电缆（10kV 电力电缆）参数型标书**，产出止于「端口文件 + 填报数据」，不做平台自动上传，不做通用品类。

## 2. 受保护模块（禁止修改）

| 路径 | 说明 |
|---|---|
| `apps/api/services/tenders/**` | 通用标书生成逻辑（范本驱动） |
| `apps/api/services/tender_package/**` | 压缩包标书既有实现（解包/分类/SSOT/货物适配/向导） |
| `apps/api/routers/tenders.py` | 通用版路由 |
| `E:/code/python/weitaiAgentui/src/views/TendersView.vue` | 通用版前端（外部仓库） |

任何一次改动前后都必须通过：

```powershell
E:\code\python\weitaiAgent\.venv\Scripts\python.exe scripts\protected_baseline.py --check
```

有差异即视为违规。确需修改受保护文件时，必须先在提交信息中写明理由并刷新基线，且不得由代理自行决定。

## 3. 新代码位置

```
apps/api/services/cablepkg/
├── model.py        Requirement / Fact / Response / EvidenceLevel
├── semantics.py    需求值解析（电缆专用语法）
├── derive.py       R1–R5 取值规则
├── gate.py         可投标闸门（证据等级）
├── consistency.py  否决项自查
├── ports.py        端口产物 + manifest
└── adapters.py     唯一的跨模块出口（见第 5 节）
```

## 4. import 规则

- `cablepkg/**` 内**禁止** import `api.services.tenders.*` 与 `api.services.tender_package.*`
- **唯一例外**：`cablepkg/adapters.py`
- 违规判定用静态检查，不靠人工看：

```powershell
E:\code\python\weitaiAgent\.venv\Scripts\python.exe -c "import ast,pathlib,sys; bad=[(str(p),n.module) for p in pathlib.Path('apps/api/services/cablepkg').rglob('*.py') if p.name!='adapters.py' for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,ast.ImportFrom) and n.module and ('services.tenders' in n.module or 'services.tender_package' in n.module)]; print(bad); sys.exit(1 if bad else 0)"
```

## 5. 适配器白名单（只读）

`adapters.py` 只允许包装以下入口，且必须以**只读**方式调用：

| 模块 | 允许入口 | 签名 |
|---|---|---|
| `tender_package.unpack` | `unpack` | `unpack(source, destination, *, limits=Limits(), password=None)` |
| `tender_package.formats` | `unpack_extended` | `unpack_extended(source, destination, *, limits=Limits(), password=None)` |
| `tender_package.classify` | `classify` | `classify(manifest, root, *, overrides=None, content_reader=read_content)` |
| `tender_package.ssot` | `build_ssot` / `read_ssot` / `digest` | `build_ssot(manifest, root, module2_source, *, rules=None)`、`read_ssot(source)`、`digest(value)` |

**白名单之外一律禁止**，包括但不限于：`web.py`、`wizard.py`、`goods_adapter.py`、`review.*`、`completion.py`、`closure.py`、`tenders.*` 的任何函数。

任务状态从 `storage/` 下的 JSON 直接读取，不通过 `web.PackageStore`。

三条契约（缺一即视为不合规）：

1. **不改行为**：适配器不得给被调用方传入会改变其持久化或全局状态的参数；解包不得写入原始输入目录。
2. **可关闭**：`adapters.ENABLED = False` 时，`cablepkg` 必须在缺省数据集上仍能跑通单元测试（证明不依赖旧模块也可运行）。
3. **可对拍**：同一输入连续调用两次，输出字节一致；调用前后 `protected_baseline.py --check` 必须通过。

## 6. 证据等级红线

| 级别 | 含义 | 可标记可投标 |
|---|---|---|
| L0 | 真实检测报告（有报告编号、页码） | 是 |
| L1 | 企业型式试验 / 产品主数据 | 是 |
| L2 | 标准限值 / 需求照录 | 否 |
| L3 | 结构设计计算 | 否 |
| L4 | 示例占位 | 否 |

- L2–L4 的行**不得**使任务进入可投标状态
- 保证值列**禁止**出现「响应」「完全响应」「满足」「满足要求」等空洞表述
- **禁止**生成或修改检测报告本体；只允许生成结构骨架，且不得包含机构名称、CMA/CNAS 编号、报告编号、签章
- 关键参数（参数名称带 `※`）在 L2 及以下时必须阻断

## 7. 数据来源

| 文件 | 用途 | 可写？ |
|---|---|---|
| `storage/cable-seed/parameters.json` | 769 行参数基准数据（L2/L3/L4） | **只读** |
| `storage/cable-seed/detection_matrix.xlsx` | 检测项目对照 | 只读 |
| `storage/cable-seed/report_skeleton.docx` | 报告骨架模板 | 只读 |

需要新数据时新建文件，不覆盖 seed。

## 8. 工作方式

1. 一轮只做一个模块，模块自带测试；不要一次铺开多个模块
2. 提交前必须：定向 pytest 通过 + 基线检查通过 + import 静态检查通过
3. 不要重构受保护模块，即使认为它重复或可简化
4. 不要"顺手"修改无关文件
5. **测试金标准必须独立于被测实现**：不得用同一段逻辑生成期望值。`storage/cable-seed/` 只能作为回归基线，不能作为正确性证明；新增判定规则时，必须另附人工标注或来自招标原表的独立用例，且断言到字段级（算子、数值、条件、限定词），不能只比算子。

## 9. 验证命令

```powershell
$py = "E:\code\python\weitaiAgent\.venv\Scripts\python.exe"
& $py -m pytest apps/api/services/cablepkg -q
& $py scripts\protected_baseline.py --check
```

`apps/api/services/cablepkg` 尚不存在时，第一步工作是新建该目录，而不是修改任何现有模块。
