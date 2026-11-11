# TASK.md

更新时间：2026-11-11
仓库：`E:\code\python\weitaiAgent`
解释器：`E:\code\python\weitaiAgent\.venv\Scripts\python.exe`

## 1. 当前任务目标与完成标准

当前轮次为资料槽位匹配修复。抽取须保留动作词之前的材料类别主语，标题统一清洗；明确标题类别优先，标题无类别时从整句依据补足。不同排他类别及不同细分来源不得互相顶替。两个不同投标人的存量商务标都要复跑匹配和装配，原填充文件归属须保持，匹配重复执行不得漂移。缺件及原主体校验拒绝必须保留并记录。

当前长期任务是维护统一标书生成引擎的装配、排版和交付验证，重点覆盖技术卷、商务卷和综合卷：

- 目录 PAGEREF 页码必须来自真实 Word 渲染结果；按正文标题回填，匹配失败必须保留原值并报告。
- 技术、商务、综合三卷封面必须按首个分节及目录/分页结构定位，统一居中，清除缩进和制表位。
- Markdown 表格必须在原段落位置转为真正的 Word 表格，进度表与结构化表不能重复。
- 章号、目录条目、正文标题和书签必须来自同一 outline 顺序；导语、事实块和实施流程必须同步。
- 落款标签和值使用全篇统一的实测版心定位；日期使用 CENTER 制表位；日期和注文需要可逆拆段。
- 标题样式和标题 run 的显式颜色必须统一为 `000000`，删除 `themeColor`、`themeShade`、`themeTint`；正文、表格、页眉页脚的显式颜色也只能是黑色。
- 交付前必须完成定向 pytest、保护基线、静态检查、幂等检查和 DOCX/PDF 回读。

完成标准：相关产物回读数字、代码路径、哈希和遗留问题都记录在本文件；没有用固定章名、公司名、段号、页码或观测坐标作为判据。

## 2. 已完成和待完成内容

### 已完成

- 真实分页目录回填主路径已接入 `assemble.finalize_word_layout`：Word COM 导出 PDF，PyMuPDF 按去编号标题文本匹配页面，再写回 PAGEREF result；settings.xml 写入 `<w:updateFields w:val="true"/>` 作为兜底。
- 结构化封面定位已统一到 `_center_cover_structure`，技术、商务、综合三卷产物回读均为 CENTER；封面判据使用首个分节、分页符和目录结构。
- 技术卷 Markdown 表格/进度表处理位于 `technical_render.py`，使用 `Table Grid`，保留原位置并统一进度表路径。
- outline 重编号、技术导语生成、事实块去重、实施流程和工期来源逻辑已落在现有 outline/technical_render/assemble 路径中。
- 主体校验已接入 `company_identity.guard_draft_subjects`，生成前拦截未知主体。
- 商务卷落款已实现：全篇扫描落款块、按最长值文本计算统一 pos、标签内部空格清理、日期 CENTER 制表位、日期/注文拆段、封面排除和幂等。
- 当前落款几何回读：Word 14 个日期块最大 `|Xd-Xc|=2.22pt`；WPS 14 个日期块最大 `|Xd-Xc|=2.24pt`。
- 标题颜色改动已完成：
  - `document.normalize_document_colors` 统一样式色和所有显式 run 色；
  - `assemble._run` 作为共用标题写入函数；
  - `technical_render.py` 子标题改为调用 `assemble._run`。
- 标题颜色产物统计：
  - 技术卷 Heading 段 29，显式黑色 29，无显式色 0；全文非黑色显式颜色 0；
  - 商务卷 Heading 段 36，显式黑色 36，无显式色 0；全文非黑色显式颜色 0；
  - 综合卷 Heading 段 59，显式黑色 59，无显式色 0；全文非黑色显式颜色 0。
- 幂等：标题颜色归一化从原始产物首次变更 505 处，再跑变更 0 处；`document.xml` 和 `styles.xml` 均一致。落款逻辑首次 57 处、再次 0 处，`document.xml` 一致。
- `py_compile` 通过；`scripts/protected_baseline.py --check` 通过，60 个受保护文件未变动；cablepkg 静态 import 检查为 `[]`。

### 待完成

- 默认会话的 pytest 临时目录仍有 ACL 问题。2026-11-11 使用工作区新建目录运行同一套测试，能够完成断言回归。
- 2026-11-10 默认会话的 Word COM 启动失败。2026-11-11 已在 Windows 桌面会话验证 Word 16.0 可导出，标题字号、编号和目录阶段的两套 PDF 均已留存。
- 用完整生产装配路径重新生成最终技术、商务、综合交付包，确认标题颜色归一化步骤在最终保存点执行，而不只是在验证副本上执行。
- 重新执行完整回归并更新最终交付清单；不得修改 `tests/` 或断言。

## 3. 关键技术决策及原因

- 目录页码使用真实 Word 分页而不是行数估算：目录结果必须反映 Word 的实际分页；`updateFields` 只作为用户拒绝更新域时的兜底。
- 封面使用结构定位：避免依赖固定页码、固定段号或具体标题，兼容不同卷和不同模板。
- 落款 pos 全篇只计算一次：使用版心宽度、最长值文本、冒号宽度和右侧留白，保证所有落款冒号形成同一竖线；异常时记录 geometry fallback warning。
- 日期只在命中的落款块内处理：避免正文日期被误改；日期与注文拆分时保留正文字符序列，清理的仅是版式填充空白和标签字间空格。
- Markdown 表格按连续管道行识别并在原位置插入 Word 表格：避免表格被挪到章末或把前后普通段落吸入表格。
- 标题颜色分两层处理：样式层清除主题色继承，run 层显式写入黑色；这样同时覆盖模板污染和技术卷直接 `add_run` 的遗漏。
- 全文显式颜色统一黑色：当前没有招标文件明确要求彩色的例外，因此不保留任何非黑显式颜色。
- 统一复用 `assemble._run`：避免技术、商务、综合三卷出现不同标题写入逻辑。

## 4. 修改过的文件和路径

与当前长期任务直接相关的代码文件：

- `apps/api/services/tenders/document.py`
  - 目录/正文装配、落款对齐、颜色归一化、最终保存前处理。
- `apps/api/services/tenders/assemble.py`
  - Word 实际分页回填、统一封面处理、统一标题 run 写入、最终装配保存。
- `apps/api/services/tenders/technical_render.py`
  - 技术卷子标题、Markdown/进度表和技术正文渲染。
- `apps/api/services/tenders/company_identity.py`
  - 生成前主体校验。
- `apps/api/services/tenders/outline.py`
  - outline 重编号和结构顺序。
- `apps/api/services/tenders/generate.py`
- `apps/api/services/tenders/native_format.py`
- `apps/api/services/tenders/pdf_format.py`
- `apps/api/services/tenders/preview_pdf.py`
- `apps/api/services/tenders/extract_chunks_optimized.py`
- `apps/api/services/tenders/format_rules.py`
- `apps/api/services/tenders/placeholders.py`
- `apps/api/services/tenders/records.py`

验证产物：

- `tmp/signoff-verification/technical-color-final.docx`
- `tmp/signoff-verification/business-color-final.docx`
- `tmp/signoff-verification/full-color-final.docx`
- `tmp/signoff-verification/technical-color-wps2.pdf`
- `tmp/signoff-verification/technical-color-wps.png`
- `tmp/shared-volume-verification/final-output/technical-delivery-cover.docx`
- `tmp/shared-volume-verification/final-output/business-delivery-cover.docx`
- `tmp/shared-volume-verification/final-output/full-verified-cover.docx`

本记录只描述相关代码和产物；工作区中还存在更早阶段的其他未提交改动，继续修改前必须先检查 `git status`，不得覆盖。

## 5. 当前遗留问题

1. 默认执行会话启动 Word COM 仍返回 `CO_E_SERVER_EXEC_FAILURE`。Windows 桌面会话已成功导出本轮三卷。
2. 默认 pytest 临时目录仍拒绝访问。用工作区内新建目录跑完当前回归。
3. 当前标题颜色 DOCX 是对已验收产物的颜色归一化验证副本；需要在 Word COM 恢复后通过完整生成/装配路径重新产出最终交付文件。
4. 保护基线、静态 import 检查和 Python 编译目前通过，但不能替代上述两个环境阻断项。

继续任务时，先读取本文件，再核对 `git status`、现有产物和上一阶段验收数字，之后一次只修改一个明确模块。

## 6. 2026-11-11 目录净化、标题字号和编号

- 三卷共用 `document._title_layout`。逐分节计算版心和段落缩进，按最长标题降至能放入一行的整数档。若当前正文档仍放不下，保留正文档并返回警告。目录条目使用 `tocItemSizePt` 及与正文大纲级别对应的 TOC 样式。
- 文本编号与自动编号并存时保留文本，删除段落的 `numPr` 和 `ind`。继承自动编号时使用派生样式抑制该段的列表继承。仅有自动编号的标题保留原列表并报告。
- `assemble._toc_source_title` 只读 outline 标题字段。 `native_format.word_format_section` 为 `extract.py` 的 Word 大纲抽取排除表格段落。最终目录要求正文顶层标题书签和可用行宽，超长或无真实目标的条目进入待确认清单。 `generate.py` 将同一个文档格式配置传入 Word 分页定稿。
- 技术、商务、综合三个现有产物复验的目录分别为 26、33、34 条，均为每条一行且超限 0 条。技术卷从 27 条减为 26 条，因一条 39 字的服务承诺目录行在固定目录字号下超宽。正文标题仍在。Word 的缓存页码与书签页码分别一致 26/26、33/33、34/34。
- Word 16.0 和 WPS 12.0 中，三个产物分别验证 29、36、59 个标题，均命中单行且无超出版心的可见文本。双编号在三个真实样本中改前和改后均为 0。独立复现样本为 3 → 0，另有一段仅自动编号，已按要求保留。三卷重开再次处理均为 0 处，`document.xml` 和 `styles.xml` 一致。
- 产物位于 `tmp/title-layout-final/`，含三卷 DOCX、两套 PDF、目录页及一级标题页截图和 `audit.json`。这些文件来自既有三卷产物的复验副本，经过生产使用的 Word 分页定稿函数。
- 当前阶段修改 `apps/api/services/tenders/document.py`、`assemble.py`、`native_format.py`、`extract.py`、`generate.py`。保护基线 60 个文件通过，cablepkg 静态导入检查通过。测试和断言沿用原文件。
- `tests/test_tenders.py` 最终结果为 `6 failed, 63 passed in 73.15s`。目录分页用例由失败转为通过。剩余失败行号为 206、643、783、864、956、1972。
## 7. 2026-11-11 落款分类和同栏一致性

- `_align_business_signoffs` 先按结构形成候选栏，再判定签署块和信息块。字段标签不再决定归属。签署块必须同时有签章动作标注和日期收尾，信息块保持原样。表格单元格、带段落边框的粘贴框及其后一段不进入签署处理。
- 旧装配样本的相邻冒号字段锯齿为 14 处。新的原始装配样本为 0 处，分类后产物为 0 处。
- 当前原始装配样本分类日志为签署块 11、信息块 23、无法判定 0。资料区字段保持无制表位，签署区共用同一 pos，粘贴框后的字段不被重新加制表位。
- 落款几何仍为 usable 9979、Wv 3570、colon_width 210、right_pad 227、pos 5972。首次处理 45 处，重开再处理 0 处。
- Word 16.0 和 WPS 12.0 已导出 `tmp/signoff-verification/classified-word.pdf` 与 `classified-wps.pdf`，资料区页和签署区页截图已生成。
- 当前阶段只修改 `apps/api/services/tenders/document.py`。保护基线、编译和 cablepkg 静态检查通过。
- 回归使用工作区临时目录完成，结果为 `6 failed, 63 passed in 71.23s`。失败行与上一阶段相同，未修改 tests 或断言。

当前落款分类产物：

- `tmp/signoff-verification/business-classified.docx`
- `tmp/signoff-verification/classified-word.pdf`
- `tmp/signoff-verification/classified-wps.pdf`
- `tmp/signoff-verification/classified-word-info.png`
- `tmp/signoff-verification/classified-word-signoff.png`
- `tmp/signoff-verification/classified-wps-info.png`
- `tmp/signoff-verification/classified-wps-signoff.png`

## 8. 2026-11-11 生成链路提速

- `generate_pair` 使用两个独立线程提交商务卷和技术卷的 `generate_bid`。两个卷使用独立的 Brief 副本和各自的输出文件，结果仍按商务卷、技术卷顺序交给原有 `merge_generated`；异常在两个 Future 都回收后向上抛出。
- `ordered_generation` 的环境变量默认并发上限改为 8，仍受原有 1 至 16 边界、信号量、超时、重试和异常回收逻辑约束。同一事件循环内的并行批次按配置值共用一个信号量，避免 `gather` 将模型并发上限倍增。
- 评分叙述和技术包预生成在生产数据库会话存在时使用 `asyncio.gather`。由于 `AsyncSession` 带有可变事务状态，两个批次各自使用 `db.session.AsyncSessionLocal` 和深拷贝 Brief，完成后按原有顺序归并。测试和无数据库降级路径继续顺序执行。
- `generate_bid` 的授权/保证金、Word 资料套用和占位框清理复用同一个已打开的 `Document`，外部表格导入、邀请附件插入后仍重新打开最终文件，保持原有替换顺序。
- COM 预览路径本轮未复制实例。现有 `preview_pdf.py` 已提供 `TENDER_PREVIEW_CONVERTER=word|wps|auto`，使用全局锁串行化 COM，含 90/300 秒超时、文档关闭、应用退出和 ReleaseComObject 清理。
- `compare_scoring` 调用位于 `POST /records/{record_id}/qa` 的质检链路，不在 `/generate` 返回前同步执行，本轮没有改为后台任务。评分抽取缓存键包含源文 digest、模型身份和请求消息，评分结果校验仍使用当前稿件正文，未发现可缓存结果遗漏正文指纹。
- 基线日志 `tmp/perf-baseline-serial-20261111.log` 使用同一生成入口的串行执行器，组卷总耗时 2.543 秒；并行日志 `tmp/perf-after-20261111.log` 总耗时 2.034 秒，降幅 20.0%。商务卷组卷探针分别为 0.376 秒和 0.515 秒，技术卷为 0.366 秒和 0.684 秒，技术卷 TOC_FIX 为 0.019 秒和 0.016 秒。本次空资料输入没有评分叙述、技术评分叙述或其他承诺模型批次，因此这些阶段均未进入日志。
- 基线产物 `storage/tenders/64c83aa2556a.docx`、`storage/tenders/2b5fcf3161e1.docx` 与改后产物 `storage/tenders/ad7ecb2bf983.docx`、`storage/tenders/d319e810baa1.docx` 逐卷段落数均为 15、表格数均为 0、正文和单元格文本集合缺失数及新增数均为 0。
- 本阶段新增或修改的性能代码文件为 `apps/api/services/tenders/generate.py`、`apps/api/services/tenders/generation_parallel.py`、`apps/api/services/tenders/records.py`。`gen_tech_plan.py` 未保留本轮改动。
- 当前定向生成回归中，`tests/test_tender_generation_parallel.py` 为 `1 failed, 2 passed`，失败是既有证据模式下测试期待 5 次模型调用而实际生成 10 个单章批次，不由本轮代码改动引入。完整 `tests/test_tenders.py` 受当前会话临时目录 ACL 影响，实际结果为 `42 passed, 27 errors`，错误均为 `C:\Users\mouzh\AppData\Local\Temp\pytest-of-mouzh` 拒绝访问。

## 9. 2026-11-11 资料槽位匹配

- 已修改 `apps/api/services/tenders/match.py`，新增统一标题归一、短材料类别主语继承、来源集合及拒绝原因函数。标题有明确排他类别时不将同句并列的其他材料类别混入；标题无类别才从依据句继承。体系证书与企业资质拆为排他族。结构动作只使用提供、提交、出具、应附、须附及扫描等词，未加入项目或公司观测坐标。
- 已修改 `material_intents.py`，结构和语义路径共同经过 `_demand` 恢复主语，依据句写入 `PlaceholderItem.hint`；模型建议 key 也必须通过类别/来源校验。多来源资料库容器对应的单来源需求使用归一标题与来源派生 key。
- 已修改 `material_identity.py`，标准化允许依据句补类别，别名分组共用归一标题并携带来源集合及类别。已有明确标题类别不被并列依据句中的其他材料名覆盖。
- 已修改 `library_kb.py`，直接 key、别名及借用路径统一校验类别和来源；多来源分组按子文件的名称及 material-title 元数据细分，来源不明时保留缺件；每个槽位输出归一标题、候选标题、文件数、命中方式与拒绝原因日志。
- 已修改 `section_materials.py`，装配别名与来源共用上述规则；多来源不再由第一条正则决定落位。装配日志保留主体校验后的实际文件数。`company_materials.py` 和既有主体校验未修改。
- 已保存两个不同投标人的真实数据库 Brief、资料库快照及存量商务标副本到 `tmp/material-matching-20261111/`。两个原文分别有 418、439 个正文段，Heading 1 段分别为 15、0；第二份沿用模板原样式，Heading 1 为零不等于没有章节。
- 最近一次 `verify.ps1` 实测，原有已填槽位分别为 6、3，文件归属变动均为 0，原嵌入图片哈希丢失均为 0。每份重复匹配结果与 Brief 均一致。产物为 `sample_1-after.docx`、`sample_2-after.docx`，逐槽审计为 `sample_1-audit.json`、`sample_2-audit.json`，日志为 `verification.log`。
- 人工标注的五类排他对照均拒绝错误类别候选；四种归一边界及结构/语义接收路径通过。多来源容器以显式标注的两种子文件来源做独立验证，分别只返回对应文件。该来源探针元数据为独立验证输入，不冒充生产资料库元数据。
- 第一份原句中的“信用中国查询”类别主语已恢复。真实信用/公示资料仍未全部填入，两份原信用槽当前均为 0 文件。资料库信用分组同时标记两个来源，子文件名称为无来源标识的短标识，material-title 仍是混合来源；已有公共信用报告也被原主体核验以不一致或尚未核验拒绝。缺少可确认的公示系统来源文件，不能用另一来源扫描件填入。验收 b、c 的“原缺件变填充”条件尚未达到。
- 修改前用 Git 原版五个匹配模块在内存运行对拍，`tests/test_tenders.py` 为 6 个已有失败，连同两组定向测试共 `8 failed, 101 passed`。主回归单独实测为 `6 failed, 63 passed in 70.77s`。主回归连同需求、主体 OCR、版面测试为 `26 failed, 83 passed in 88.10s`；额外 20 个失败也在原版模块复现。来源归位和材料绑定最终为 `2 failed, 38 passed in 5.49s`，两项也在原版复现。失败用例名对拍无新增。信用 PDF 用例从 956 行未找到标题变为 954 行缺件框数量断言，原因是混合来源需求保留了独立缺件框。未修改 tests 或断言。
- 最终保护基线 60 个文件通过，五个修改模块编译通过，cablepkg 静态 import 检查为 `[]`。完整代码改前改后、判据说明、原始未命中日志样本及验收细节在 `tmp/material-matching-20261111/REPORT.md`；复跑命令为 `& .\tmp\material-matching-20261111\verify.ps1`。最终单独按用户原命令执行的 pytest 日志另存 `requested-pytest.log`。
- 产物 `tmp/material-matching-20261111/sample_1-after.docx`，8378863 字节，修改时间 `2026-11-11T10:48:42+08:00`，SHA256 `02442c88728098d27f68a9ce00875399abb53547313d899f9de5dba5d156a1fd`。
- 产物 `tmp/material-matching-20261111/sample_2-after.docx`，373151 字节，修改时间 `2026-11-11T10:48:46+08:00`，SHA256 `1a586f901ae20715adc7917b2ac8d680fba1e235506b20c6301b39106e04137b`。
- 本轮仍未达到全部验收。新增信用/公示需求要实际填入文件，需要资料库具备来源可细分且能通过原主体核验的原件；本轮没有修改资料库、主体校验或用其他类别扫描件顶替。
- 按用户原命令单独执行的最终主回归为 `6 failed, 63 passed in 76.29s (0:01:16)`，日志 `tmp/material-matching-20261111/requested-pytest.log`。该命令在可访问临时目录的执行上下文运行，未更改测试文件。
