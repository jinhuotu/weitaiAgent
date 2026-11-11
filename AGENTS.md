# AGENTS.md · 统一标书生成引擎

本文件是本仓库的最高优先级开发规则。后续所有对话、代理和开发者在修改本仓库前必须先读取并遵守本文件。

本文件约束所有在本仓库工作的 AI 代理与开发者。**与其它需求冲突时，本文件优先。**

## 1. 产品目标与任务边界

本项目构建统一标书生成引擎，保留现有商务标和技术标主流程，并逐步支持普通文件、单层压缩包、压缩包套压缩包以及包含分包的行业标书。

电缆标书是统一引擎的一种复杂输入结构，不得为了电缆场景新建一套与现有技术标流程完全割裂的生成器。主流程应保持：上传、解析、分类、资料匹配、生成、装配和交付验证。

当前重点是技术标优化；商务标已有流程默认保持兼容。产出止于端口文件、填报数据和交付清单，不做平台自动上传。

## 2. 总体架构原则

- 将单层文件输入扩展为可递归的文档包输入。
- 压缩包、目录、文件和嵌套压缩包必须形成统一 `PackageTree`。
- 分包、父级压缩包、原始路径、产品范围和资料范围必须作为 `DocumentContext` 传递给现有生成逻辑。
- 输出必须依据 `DeliveryManifest` 回装原始目录和压缩包层级。
- 行业差异通过 profile、规则和配置扩展，不复制整套生成流程。
- 普通标书、单层压缩包和嵌套压缩包默认向后兼容。

## 3. 输入能力与阻断规则

- 生成前必须执行输入预检。
- 当前支持 Word 格式投标邀请书；PDF 投标邀请书当前不可自动解析。
- 关键文件格式不受支持时，必须在生成前阻断，不得静默跳过、猜测填充或标记为成功。
- 阻断信息必须包含文件路径、原因和建议处理方式。
- 非关键的不支持文件可以进入 `warning`，但必须保留原件。
- 统一状态为 `ready`、`warning`、`blocked`。

## 4. 兼容性与变更原则

- 未有独立回归证据前，不得改变现有普通技术标和商务标的输出行为。
- 新增嵌套压缩包能力必须扩展现有流程，不得破坏单层输入。
- 每轮只改一个明确模块；不得同时重构商务标、技术标、模板和资料库。
- 修改前记录基线结果，修改后比较状态、文件数量、路径、内容、分包关系和资料来源。
- 跨对话工作必须先检查工作区已有变更，不得覆盖其他对话的修改。

## 5. 受保护模块（禁止修改）

| 路径 | 说明 |
|---|---|
| `apps/api/services/tender_package/**` | 压缩包标书既有实现（解包/分类/SSOT/货物适配/向导） |
| `apps/api/routers/tenders.py` | 通用版路由 |
| `E:/code/python/weitaiAgentui/src/views/TendersView.vue` | 通用版前端（外部仓库） |

tenders/** 现已开放，修改时仍须在交付说明中列出变更文件与理由；
tender_package/**、routers/tenders.py、TendersView.vue 保持保护，需要改动时由用户逐次授权。

任何一次改动前后都必须通过：

```powershell
D:\code\LLM\weitaiAgent\.venv\Scripts\python.exe scripts\protected_baseline.py --check
```

有差异即视为违规。上述任务例外由用户明确授权，修改后必须复核基线并在交付说明中列出变更文件与理由。

## 6. 新代码位置

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

## 7. import 规则

- `cablepkg/**` 内**禁止** import `api.services.tenders.*` 与 `api.services.tender_package.*`
- **唯一例外**：`cablepkg/adapters.py`
- 违规判定用静态检查，不靠人工看：

```powershell
E:\code\python\weitaiAgent\.venv\Scripts\python.exe -c "import ast,pathlib,sys; bad=[(str(p),n.module) for p in pathlib.Path('apps/api/services/cablepkg').rglob('*.py') if p.name!='adapters.py' for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,ast.ImportFrom) and n.module and ('services.tenders' in n.module or 'services.tender_package' in n.module)]; print(bad); sys.exit(1 if bad else 0)"
```

## 8. 适配器白名单（只读）

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

## 9. 证据等级红线

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

## 10. 数据来源

| 文件 | 用途 | 可写？ |
|---|---|---|
| `storage/cable-seed/parameters.json` | 769 行参数基准数据（L2/L3/L4） | **只读** |
| `storage/cable-seed/detection_matrix.xlsx` | 检测项目对照 | 只读 |
| `storage/cable-seed/report_skeleton.docx` | 报告骨架模板 | 只读 |

需要新数据时新建文件，不覆盖 seed。

## 11. 工作方式

1. 一轮只做一个模块，模块自带测试；不要一次铺开多个模块
2. 提交前必须：定向 pytest 通过 + 基线检查通过 + import 静态检查通过
3. 不要重构受保护模块，即使认为它重复或可简化
4. 不要"顺手"修改无关文件
5. **测试金标准必须独立于被测实现**：不得用同一段逻辑生成期望值。`storage/cable-seed/` 只能作为回归基线，不能作为正确性证明；新增判定规则时，必须另附人工标注或来自招标原表的独立用例，且断言到字段级（算子、数值、条件、限定词），不能只比算子。

## 12. 验证命令

```powershell
$py = "E:\code\python\weitaiAgent\.venv\Scripts\python.exe"
& $py -m pytest apps/api/services/cablepkg -q
& $py scripts\protected_baseline.py --check
```

`apps/api/services/cablepkg` 尚不存在时，第一步工作是新建该目录，而不是修改任何现有模块。
