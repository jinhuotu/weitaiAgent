# 标书生成统一规范实现计划

## 总目标
标书生成统一为"单一数据源 + 全局去重 + 顺序锁定"，严格按规范：封面 → 目录 → 正文

## 实现任务

### 一、目录锁定原版，不重排（native_format.py）

**文件**: `apps/api/services/tenders/native_format.py`
**函数**: `ensure_toc_rows` (L1048)

**修改点**:
1. 直接提取原版格式章目录，保持原有顺序和层级
2. 主条目编号一次遍历生成（一到十），不重置计数器
3. 子条目（一）（二）挂在对应主条目下
4. 每条 PAGEREF 指向正文，页码随实际页重算

**当前状态**: ✅ 已实现连续编号逻辑
**需要检查**: 是否保持原版顺序，不重排

### 二、目录后紧接正文

**修改点**:
- 占位框只允许在章节内部或文末"待补充"区域
- 不得插在目录与正文第一章之间
- 目录后紧接正文第一章

**实现位置**: `pdf_format.py`, `placeholders.py`

### 三、贴图全局去重（pdf_format.py）

**文件**: `apps/api/services/tenders/pdf_format.py`
**函数**: `_paste_id_pages`, `place_unmatched_slots`, `_fill_slice_sheets`

**修改点**:
1. 建立全局已贴集合 `global_pasted_keys`
2. `_paste_id_pages` 主循环与各兜底函数共用该集合
3. 同一资料键（如 id_legal, id_agent）全局只贴一次
4. 身份证正反面并排，只贴一份
5. 营业执照、资质等资料同样只贴一份
6. 无占位框时在章节标题后新建空白段贴一份

**实现步骤**:
