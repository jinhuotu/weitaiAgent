# 材料锚点失效修复验证（2026-11-04）

例外理由：修复生成阶段的确定性 500，不改变归属与生成算法。

本轮只修改 apps/api/services/tenders/section_materials.py。placeholders.py 无需修改：删除锚点的三个入口由“缓存仅保存归属、边界每次重建”统一覆盖。前端、归属评分、章节识别、生成模板均未修改；未重启项目。

## 复现

原日志 logs/weitai-agent.2026-11-04.log 的请求 a5ca4d6712cb 与用户提供的堆栈相符。修改前源码备份位于 tmp/material-anchor-fix/section_materials.py。通过在独立 Python 进程内加载该源码，不改运行项目，执行新增 test_two_materials_share_empty_template_frame：第一个材料替换空框后，第二个材料在主插入和兜底插入中均抛出 Material anchor is not a complete body block。复现命令：.venv/Scripts/python.exe tmp/material-anchor-fix/repro_before.py。

## 修复与验证

- 统一 resolve_material_anchor：依次检查原锚点、来源块、所属标题、章节存活块、文末新段落；插入前记录 key、chapter、anchor_live、relocated。
- 合成兜底章节每个材料创建独立插入边界；所有结果仍由原共享 writer 渲染。
- 仅缓存归属和文本定位信息，同名章节保留序号；跨入口重新解析物理边界，不重新计算归属评分。
- 主插入和文字占位重试均有保护；最终失败写 notes，不阻断后续材料。失败输出回滚，避免重试产生重复书签。
- 定向：tests/test_tender_material_anchor_recovery.py + tests/tenders/test_material_anchor_aliases.py，24 passed。命令：.venv/Scripts/python.exe -m pytest tests/test_tender_material_anchor_recovery.py tests/tenders/test_material_anchor_aliases.py --basetemp=tmp/material-anchor-fix/targeted-final -p no:cacheprovider -q --log-file=tmp/material-anchor-fix/verification.log --log-file-level=INFO。
- 三次构造数据测试：相同采购说明、相同 138 项自定义材料、空 media，每次输出 138 项，未满足 0；每个材料恰好一个 result_*_box，未出现跳过。生成 DOCX 在上述 pytest 临时目录。此为故障路径的 DOCX 落位验证，不能当成用户原邀请书及原表单的完整 generate 接口重放。
- 真实图片、无文字空框共享、删除后重定位、重定位全失败的 notes、后续材料继续处理、缓存中空框升级图片、跨入口幂等均通过。
- 验证日志 tmp/material-anchor-fix/verification.log 中旧异常关键字为 0，存在 relocated=True 诊断日志。
- 扩大回归 53 项：41 passed、12 failed。用修改前源码运行相同既有测试：31 passed、同样 12 failed。失败集合完全一致，主要为既有落位文案及空 media 语义断言；本次没有修改这些断言或无关算法。不能声称既有整组测试全绿。

## 未完成验收

未定位到失败 generate 请求保存的完整表单，因此未完成“同一原邀请书、原表单连续生成 3 次”、原覆盖报告对比或浏览器正常响应验收。运行中的旧后端未重载，历史日志中的旧异常不会被清除。24 项定向测试通过不代表线上进程已更新。

## 基线与提交

本轮开始基线已有此前明确授权的 4 项差异（多文件入口及前端视图）。本轮新增受保护差异仅 section_materials.py。用户本次明确要求修复后刷新基线，刷新将纳入当前已有授权状态，不修改其他源码。刷新前快照：tmp/material-anchor-fix/protected-before.json。刷新后再执行 scripts/protected_baseline.py --check，结果与摘要存 tmp/material-anchor-fix/protected-after.json。

## P2 遗留

本标书 138 项材料为何全部降级到“其他资料（待核对归属）”，需另开任务排查归属匹配。本次只记录现象，不调整归属算法。
