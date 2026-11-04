# 综合标：已应用并验证

已按用户确认，将修改应用到正式前后端 8 个源文件，并新增 `tests/test_tender_full_bid.py`。原有未提交修改保留。

[查看最终已应用 diff](full-bid.applied.patch)。`full-bid.patch` 保留用户最初确认的版本；最终版本另外修复了本次验收发现的重复目录处理和浮动表格书签幂等问题。路径前缀对应两个仓库：

- `frontend/` → `E:\code\python\weitaiAgentui`
- `backend/` → `E:\code\python\weitaiAgent`

## 修改内容

- 前端增加显式生成模式：完整投标文件（综合标）、分册两本、单卷。商务/技术页签继续用于编辑内容；单卷模式按当前页签提交。
- full 提交 `generateVolume=full`、`generateBoth=false`；默认同时开启商务响应和技术方案起草，记录并尊重手动关闭。恢复历史表单时保留已保存的开关。
- split 显式提交 `generateBoth=true` 和 `splitSlice=true`，覆盖识别建议；business/technical 保留单卷路径。`judge_binding` 本身无需修改，已有 `keep_volume` 保留用户指定卷别。
- full 在两类正文填完后统一处理卷名、页眉、书签、目录页码和节页码重启；原始表格、字体和章节顺序沿用已有组装路径。
- QA 路由、归一化、报告存取、规则检查与模型提示支持 full。完整标记录即使收到旧前端传的 business/technical，也归入一份 full 报告。
- 打开历史记录时保留 generateVolume；从双卷重新生成为 full 时清除旧技术卷引用。
- 原有 records.py 的 full 双正文门控已正确，继续复用；不修改单卷门控。

## 最终目录处理

- 原格式章 full 路径推迟目录链接与表格目录修复，直到技术、商务两类正文均填充完毕，随后各执行一次。
- 固定模板 full 路径保留正常 `_fill_toc_pages`，不再执行原格式章专用目录函数。
- full 重复归一化时复用已修复的表格正文锚点，避免出现 `toc_organization_2` 等新增书签；单卷默认行为保持不变。

## 最终验证

- 综合标专项：17 passed，结果见 [full-bid-tests.xml](full-bid-tests.xml)。
- 全项目 pytest：896 项，892 passed、4 failed，结果见 [all-regressions.xml](all-regressions.xml)。四项均为 `test_layout_plan.py` 的现有布局问题，修改前基线上逐项复现，见 [layout-baseline.xml](layout-baseline.xml)。
- 目录断言：正文填完后链接与修复函数各一次；每目录行一个 PAGEREF；已知正文页码准确为 1、2；连续重复归一化两次后目录页码、书签名称/ID/起止配对、节页码和页脚保持稳定。覆盖普通目录、浮动表格及未匹配占位。
- 使用真实 DOCX 组装器验证 full 输出一本且含商务与技术正文、split 输出两本且正确裁分，并保留 business/technical 单卷。
- 前端实际页面 full/split/single 切换可用；提交逻辑验证默认起草、手动关闭、split 覆盖建议、技术单卷均通过。[页面截图](full-mode-verified.jpg)。
- Vite 生产构建通过，产物位于 `ui-build/`；关闭 noUnusedLocals 后前端类型检查通过。标准 vue-tsc 仍有原有 10 处 TS6133 未使用声明错误（原基线 12 处，无新增诊断），因此标准 `npm run build` 尚受其阻断。
- 最终后端和归档 Worker 已彻底重启；API 监听进程 PID 37128，健康接口 HTTP 200、`status=up`。早先自动审批服务的额度问题已在重置后恢复，最后一次重启执行成功。

未调用真实模型、未改动真实投标记录、未做 Word 排版引擎的逐页视觉验收。目录与生成文件验证使用测试构造的 DOCX。
