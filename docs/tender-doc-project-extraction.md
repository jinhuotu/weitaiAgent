# 旧版 Word 与采购询价项目事实提取

实施日期：2026-10-24。

## 入口定位

- 招标上传：`POST /api/v1/tenders/parse-invitation` → `parse_invitation`
  → `_save_invitation` → `_doc_to_docx`。成功返回 `.docx`，后续文本解析和格式章截取共用这个路径。
- 知识库上传：`POST /api/v1/knowledge/documents/upload` → `ingest_upload`
  → `assert_supported`，后台 `_load_or_extract_text` 再调用 `extract_text_from_file`。
  修改前前端和后端均明确拒绝 `.doc`。
- 日志 `logs/weitai-agent.2026-10-24.log` 在 10:25:35 记录了招标接口的 Word COM
  “远程过程调用失败”，随后该接口返回 422。这证明有实际招标上传转换失败，不能归因于
  `parsers` 拒绝扩展名。该历史日志未保存文件名，无法仅凭它证明失败请求就是天津静海文件；
  本次已给转换失败日志补充文件名，便于后续关联。
- 桌面原件核实：`招标文件 - 天津静海20141201.doc` 签名为 `d0cf11e0a1b11ae1`，
  确为旧版 OLE；精细氧化铝询价 `.docx` 为正常 OOXML。

## 改动清单

- 新增 `apps/api/services/word_convert.py`：共享 `.doc → .docx` 转换工具，不依赖招标或知识库模块。
  保留 LibreOffice 路径及隔离的临时配置目录；Windows 使用独立 Python 工作进程调用 Word COM。
  初始化、释放 COM，按只读方式打开原件，关闭文档和应用，设置转换超时并清理临时输出。
  仅识别本次新增的 Office 进程用于异常清理。转换结果须能被 `python-docx` 正常打开，
  验证通过后才原子替换目标，不把非空文件或 `PK` 前缀视为成功。
- 本机 Word 接口实测不支持 `Application.Hwnd`，实现已改为识别新增 Office 进程；
  同时兼容 `SaveAs2` 和 `SaveAs`。未调用阿里云转换服务，`aliyun_doc.py` 的 PDF 转 Word 行为不变。
- `knowledge/parsers.py`：增加 `.doc`、`application/msword` 和解析分支，转换后复用 `_read_docx`，
  读取结束清理临时文件。转换失败继续返回 422，保留“请在 Word 里另存为 .docx 后再上传”的明确提示。
- `tenders/extract.py`：招标上传复用共享工具；原始上传文件名通过可选参数进入事实补全，
  不使用 UUID 落盘名推断项目。项目名称依次取已有有效值、标准条款、采购/询价标签、封面标题、
  附件本项目引用、原始文件名。封面相邻项目名与包号合并，文件名只去除文种、扩展名及明确版本尾缀。
- 采购人、采购单位、询价人/询价单位补到 `tenderer`；项目单位仅在明确标注采购主体角色时使用，
  不作为项目名称。补充询价/采购项目编号标签，合同号和附件号不作为编号兜底。
- 明确识别空值和 `（项目名称）`、`（采购人名称）` 等占位符，取消“括号开头全部拒绝”。
  已有有效模型/条款结果不被补缺覆盖。模型 `tenderNo` 可以补空，后续标准编号扫描不会覆盖有效编号。
- `routers/knowledge.py`：上传接口说明增加 `.doc`。
- 前端 `weitaiAgentui/src/lib/read-file-smart.ts`：同步放开知识库 `.doc` 类型校验与提示，避免页面先行拦截。
- 更新原有“拒绝 .doc”的过期测试；新增 `tests/test_word_convert.py` 和
  `tests/test_tender_project_extraction.py`，共 56 项专项测试。

## 桌面原件验证

天津静海原始 OLE `.doc` 通过共享工具实际调用本机 Word 转换，转换后提取正文 58,014 字符：

- 项目名称：`静海县环境监控与应急指挥中心建设项目`（沿用文件内明确条款）；
- 招标人：`天津市静海县环境保护局`；
- 招标编号：`FZK2014-3-639`；
- 使用同一转换文件成功进入格式章截取，生成有效 DOCX。

精细氧化铝桌面原件提取正文 6,128 字符：

- 项目名称：`精细氧化铝工业试验线建设项目包二十三：数智化运营管理系统`；
- 采购/询价主体：`河南华慧有色工程设计有限公司`；
- 单独只给原始文件名（含“发出版.2026.7.19”尾缀）也能得到相同完整项目名；
- 未提取到明确采购/询价编号，保留空值；格式章截取成功，含 2 张表。

两份桌面原件在转换、解析及格式章截取后重新比较 SHA-256，均未变化。
原件验证未调用在线模型、云转换或云 OCR；嵌入图 OCR 在本次离线文字验收中禁用，生产 OCR 行为不变。
HTTP 上传编排、原文件名传递和同一 DOCX 共用由固定模型及模拟外围服务的集成测试覆盖，
本次没有向在线知识库写入测试资料，也未宣称完成浏览器端到端上传测试。

## 基线与回归

- 实施前保存标准施工 `.docx` 及空模型、部分字段模型、完整正确模型 3 组固定返回基线。
  实施后逐字段比较全文提取结果、全部 `BidBrief` 字段及 `filledKeys` 集合：**3/3 组零字段差异**。
- 后端解析、提取、格式章、目录、分卷及大纲等相关回归：**370 项通过**。
- 新增共享模块、两个专项测试文件通过 Ruff；既有解析/提取文件未做全文件风格整理。
- 知识库前端上传校验定向执行 8 项，通过 `.doc`/`.DOC`、`.docx`、PDF、空文件、超限及不支持类型检查。
- `npm run build` 未通过：当前工作区 `HistoryBidsView.vue` 存在 `string | undefined` 类型报错，
  `TendersView.vue` 存在未使用声明及 `generateVolume` 类型不匹配。它们均在本次改动范围之外，未修改。

本地证据位于忽略提交的 `storage/extract_regression/`：

- `check_baseline.py`、`before.json`、`after.json`：标准施工基线与逐字段比较；
- 修改前 `extract.py`、`parsers.py`、`aliyun_doc.py` 的源码快照；
- `check_real_files.py`、`real-files.json`：桌面原件、文件签名、事实及格式章结果、原件 SHA-256；
- `tianjin-original-converted.docx`、`ad0c000001.format.docx`、`ad0c000002.format.docx`：测试转换与截取输出；
- `check_frontend.cjs`、`frontend-check.json`：执行实际前端类型校验函数的定向结果；
- `pytest-final.txt`：完整回归输出。

转换仍依赖运行环境提供 LibreOffice 或 Windows Word COM；没有可用转换器时保留明确的另存提示。
