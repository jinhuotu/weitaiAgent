# 模块四真实数据回填

本入口为独立本地结构化接口，不注册应用路由，不修改正式前端，也不启动或重启项目。
所有请求调用现有 confirm_offer / confirm_price / confirm_packages / read_product_source。
输入会话是接口产物，禁止手工修改；每次操作输出新快照，旧快照保留。

## 提供真实数据

企业自己的参数 DOCX 可放到项目 inputs/supplier_products，也可直接提供任意可读的绝对路径。
同时提供每份文件对应的固化ID、绑定确认人和依据。没有明确对应关系时允许登记，保持未绑定，
不自动提供保证值。参数名、实际/标称值、单位必须来自原表，名称与单位精确匹配；
不同值进入候选冲突，不拼接、不换算、不从招标需求推导保证值。

当用户授权先自动识别时，product 请求可指定 infer_bindings=true，不必提交人工绑定人或依据。
读取器按 DOCX 正文顺序，从参数表所属章节中的单一固化ID建立局部对应；不会把多物料文件
的全部参数混用于每个ID。来源记录 binding_mode=document_section、原段落定位和原文，
人工字段保持为空，操作主体明确为自动识别。无ID、多ID、未知ID均待确认，不靠文件名猜配。
保证值列被标记为 supplier_declared，只代表所提供文件声明的数值，不代表检测报告或人工审批。
与模块二已有人工确认参数不一致时进入冲突，不覆盖原确认。

实际标包需要指定 SSOT 内 lot_key、用户确认的实际 package_code 及 material_ids，
不能把压缩包名称当作确认。报价需要 package_key、原表 document_id/table/row/column、
实际 amount、currency、unit、actor 和 basis。未确认范围的报价保留历史但扣留不写。

## 结构化接口

在仓库根目录设置当前命令的 PYTHONPATH 为 apps;packages 后运行：

```powershell
$env:PYTHONPATH = 'apps;packages'
'{"operation":"create"}' | .venv/Scripts/python -m api.services.tender_package.goods_workflow --ssot tmp/real-package-ssot-v1-validated.json --output tmp/new-inputs-000.json
```

后续请求使用 --input 指向上次输出，--output 指向新文件；请求可以通过标准输入，
或 --request 指向单独的请求文件（请求文件是命令，不是会话 JSON）。
请求统一为 {"operation": 操作名, "arguments": 参数对象}。

- product：path、document_id、solidified_ids、binding_actor、binding_basis，读取真实文件并追加来源历史。
  用户授权自动关联时可改为 path、document_id、infer_bindings=true，省略人工绑定字段。
- match：无参数，重新校验来源文件与哈希，输出 automatic_matches、conflict_targets、
  unbound_sources、invalid_sources、pending_targets 和完整 matches 来源清单。
- offer：code、target、guarantee、actor、basis，可选 status（响应/偏离），追加人工记录。
- price：package_key、document_id、table、row、column、amount、actor、basis、currency、unit。
- packages：packages、actor、basis；每个包有 key、lot_key、package_code、material_ids。
- review_rows：decisions、actor、basis；每项须有 solidified_id、target、guarantee 与明确的
  status（响应/偏离）。只处理待核对行，不改变已自动通过行；保留核验前完整来源与裁决历史。
- quote_source：path、actor、basis。只读批准的实际报价 XLSX，记录文件哈希、工作表、
  单元格、原始行和批准时间；不会据文件名或工作簿正文自动确认标包。
- source_price：source_sha256、quote_key、package_key、document_id、table、row、column、
  actor、basis。从已批准且哈希仍一致的文件取金额，通过 confirm_price 追加记录；页面还会
  校验原表位置为空白价格列、报价类型及目标行固化ID。未确认标包、物料归属不符或来源变更均扣留。

从接口输出会话调用 compile_inputs，或交给 goods_adapter 的 --reviewed-inputs，
即可使用已登记产品资料的自动匹配与人工记录；人工记录优先，产品来源历史仍完整保留。
每次编译重新读取原产品文件，调用方伪造参数列表、缺失文件、哈希变化均不能成为回填依据。

## 渲染及逐页验收

默认通过 PATH 及本地标准安装目录自动探测 LibreOffice，显式配置路径具有优先权；
不自动安装、下载或依赖远程服务。通用 command 适配器仍可配置。
render_package 仅生成 rendered_pending_review，不能自行标为验收通过。
逐页核对后调用 review_page，记录 actor、basis 和 layout/pagination/alignment/
blank_regions/original_format 五项布尔检查；任何一项未通过，整包不能通过。
自动化代理核对必须如实记录代理身份，不冒充用户或业务签字人。
最终填入真实数据后需重新生成、渲染和逐页核对，草稿的核对不能沿用到新文件。
apply_visual_review 将逐页检查的未通过项汇入返回结果的 warnings，并保留原视觉报告历史，
不改正文。对同哈希且像素相同的页面可复用已查看图像作为核对依据，仍逐页调用 review_page。

单元回归隔离外部 Office 应用，真实渲染另行执行，不把模拟图片测试冒充视觉验收。

## 扫描后人工核验，不阻断草稿

先用现有独立接口完成识别、匹配与草稿生成，再启动本地核验页：

```powershell
$env:PYTHONPATH = 'apps;packages'
.venv/Scripts/python -m api.services.tender_package.goods_review --ssot SSOT文件 --input 接口输入快照 --root 解包工作目录 --output-dir 新的核验快照目录 --port 0
```

命令打印仅本机可访问的地址。页面支持按原因/参数类型筛选、查看双方证据、填写冲突最终值、
明确选择响应/偏离、确认实际标包和物料分配、选择已批准报价绑定原表空白位置、导出待核验清单。
未确认项保持待确认，不默认二选一，也不要求先全部签字才能继续现有草稿流程。
核验页独立于正式前端和应用路由，不启动或重启前后端项目。

核验收尾时可增加 --draft-result 指向本次草稿结果、--library-snapshot 指向资料库候选快照，
加载商务逐项核验。页面依次排列冲突裁决、响应分组、标包报价和商务原表。
冲突不会被“勾选当前分组”或分组批量结论带入。商务真实资料只允许从已匹配候选中选择，
保存时重新校验存在性与哈希；已有候选、未加载或尚未核实的资料不能确认“确无资料”。
原版商务表必须指定SSOT来源文档和有效非空段落/表行，记录原文及哈希；不凭空拟承诺正文。
商务选择与原表核验追加 business_review_history，纠正保留 prior；不会直接改已生成文档。
保存真实资料选择后可在页面导出接口生成的 reviewed-business-library.json，用作下一次
goods_adapter 的 --library 输入，继续复用真实贴图及仅真实命中文件的到期提示。
所有人工项完成后仍须另行取得用户同意补跑全量回归，并完成新整包的逐页视觉验收。
核验页始终不自动运行全量回归、不标记 ready_for_submission=true。

每次保存都经过正式确认函数，在 output-dir 生成新的 session-序号.json，原输入不变。
再次启动时恢复该目录最新接口快照；请勿手工改会话JSON，也勿同时从其他脚本修改同一核验目录。
CLI 操作也必须以最新接口快照为 --input，输出新文件，防止分叉覆盖人的核验结果。
确认后的快照交给 goods_adapter 的 --reviewed-inputs 重新生成。已生成草稿不因页面保存自动修改；
重新生成后的文件必须重新渲染和逐页核对。业务尚未核验与已完成核验严格区分，ready_for_submission
不会因“先走通流程”变为 true。

## 独立排版后处理

goods_layout.repair_goods_package 对已生成草稿另存修复版本，不改生成函数、原文件或原始模板。
所有表格行设置不可跨页拆分，保留原列宽；只调整生成器记录的已填位置的换行和字号属性。
字号不小于原字号75%且不小于配置可读下限；缺少原字号时须提供实际渲染测量值及依据，禁止猜测。
修复器反向恢复允许属性后比较完整XML，同时校验其他ZIP成员不变，证明没有改动文字和表格结构。
后处理自动重新渲染为 rendered_pending_review；逐页 review_page 后才能标记视觉通过。

```powershell
.venv/Scripts/python -m api.services.tender_package.goods_layout --input-package 原草稿目录 --output 新修复目录 --options 排版参数请求.json
```

本次真实参数来源共有769行，匹配768行，1行型号冲突；237项自动通过，532项保留人工核验。
后处理将10份技术表从9页恢复为每份8页，共80页，逐页核对完成。新增填写文字10pt，
实测原默认字号11pt，比例90.9%，高于75%和9pt下限。原版短页和注释页留白按原版保留。
商务附件缺少已核对的原版商务表，整包不能宣称正式商务格式或视觉验收全部通过。
用户已批准的报价表通过 quote_source 接口登记，10个物料共50个数值保留原单元格出处；
批准不代表实际标包分配及原表位置已确认，当前仍扣留。型号裁决和532项响应允许后续在页面继续核验。
