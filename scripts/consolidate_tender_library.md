# 投标资料库一次性归并

此脚本独立于业务服务，默认只读。标准材料类别由通用映射识别；历史容器编号只存在于迁移映射中，不进入生成规则。

在后端虚拟环境运行：

```powershell
.venv/Scripts/python.exe scripts/consolidate_tender_library.py
```

核对“将迁移/将删除”明细及 `planHash` 后执行：

```powershell
.venv/Scripts/python.exe scripts/consolidate_tender_library.py --commit --expected-plan <planHash> --mysql-container <MySQL容器名> --backup-dir <独立备份目录>
```

提交前备份完整数据库和 storage，并校验文件哈希。备份目录必须位于 storage 外；存在活动入库任务、不可读原件、目标冲突或计划变化时停止执行。先复制并验证文件，再提交数据库事务，最后清理不再引用的旧路径和向量。

向量库不可用时保留 `pending-cleanup.json`，数据库和已校验原件不丢失。服务恢复后重放清理：

```powershell
.venv/Scripts/python.exe scripts/consolidate_tender_library.py --commit --resume <原备份目录>
```

重放前再次核对数据库与文件状态；向量清理结果写入 `cleanup-result.json`。完成后再次默认运行，create/update/delete/move 应全部为 0。保留备份，避免误删其他资料。

归并只修正存储类别，不把文件名当作内容证据。所属公司、信用代码及材料类型仍在生成时校验；类型错误、其他公司的原件不会因迁移而自动采用。身份证缺公司证据时使用证件槽位和人员身份选择；多人候选不得任意选人。

2026-11-01 本次执行备份：`data/backups/tender-library/20261101-standard-materials`。原 279 个 parent（271 个空项）整理为 6 个有文件标准 parent；原 9 条原件记录中一对内容、摘要及身份标签完全相同，由既有去重逻辑保留一份，现为 8 条不同内容原件。完整迁移前记录及原件均保留在备份中。
