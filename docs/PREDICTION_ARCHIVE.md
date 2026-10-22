# 每日预测统一归档

统一账本保存多个交易日的全部候选分数，供模拟跟踪和恢复使用。归档保留预测原来的时间、角色与排名，另记归档时间；不会把事后研究快照变成前瞻预测。

## 日常使用

在原每日推理命令末尾增加：

```powershell
--archive artifacts/phase2/prediction_archive/predictions.sqlite
```

每天传同一个归档路径，推理输出目录仍须每天新建。先完成三周期推理、保存独立快照，再将三个周期在同一事务中归档。推理成功但归档失败时，成功快照保留；修复投递问题后可以单独重试。

同一快照重复导入返回原归档收据，不增加记录。同一预测已有记录时，输入、分数或原创建时间有任何冲突均拒绝替换。已归档历史不随后续研究修订而改写；另起研究版本使用独立输出。

## 导入已有快照并备份

期望哈希取自原始运行收据或已验证备份。本轮值来自已提交的[原始快照证据](reports/evidence/FROZEN_DAILY_RESEARCH_20261021.json)，不能为绕过错误而替换成来历不明文件的当前哈希。

```powershell
python -m ashare_mvp.prediction_archive --project . --snapshot artifacts/phase2/frozen_daily_research_20261020_v3 --manifest-sha256 48c83a1865b1d8a92cfe2bcf07bfebd7d1e86b5c7acf2b088c9f77abf2b625cc --archive artifacts/phase2/prediction_archive/predictions.sqlite --seal artifacts/phase2/protocol_20261020 --calendar artifacts/phase2/frozen_daily_research_20261020_v3/forecast_calendar.csv --backup-out artifacts/phase2/prediction_archive/my_new_backup.sqlite
```

`--backup-out`必须为新文件，生成事务一致的SQLite备份。导入只接受完整的三周期成功快照，核验快照哈希、备份哈希、模型及协议、时间、候选数、交易代码、连续名次和全部分数内容。三个周期全部成功后才提交；失败整批回滚。误指向行情库或普通文件时拒绝写入。

## 与模拟账户及迁移衔接

模拟账户可将统一账本的一致性备份作为`--ledger`，显式传入需要的`--batch-id`。多日账户须包含各规定调仓日的预测和所有历史持仓报价，规则见[模拟跟踪说明](PAPER_TRACKING.md)。归档不改变其前瞻门禁。

换电脑继续既有归档时，必须一并恢复原封存目录`artifacts/phase2/protocol_20261020`、对应冻结模型及元信息、预测账本的一致性备份、所需行情和公司行动。**保留原封存文件的字节与哈希，不重新封存来替代旧协议。**在新项目相对路径下复制归档备份为新的工作账本后，可继续追加符合原合同的记录。

目前第二阶段新增文件仍仅在本地，尚未上传私有云备份；仅从GitHub下载代码，不能恢复它们。

## 已验证与未验证

真实10月20日三周期、共14,805条分数已导入；重复导入后数据库哈希不变。备份读回的原始收据、创建时间、角色、名次和分数逐项一致，恢复后的防改写检查通过。模拟账户读取恢复后的统一账本，仍排除三份事后预测，没有生成成交或收益。[精确验证证据](reports/evidence/PREDICTION_ARCHIVE_REAL_20261021.json)。

最终全量688项通过（176.19秒），归档、每日入口、账本与账户相关56项通过。多日衔接和推理后自动投递使用明确的合成场景测试；真实数据目前只有一个信号日完成归档，尚未验证连续多日运行。源数据质量、真实前瞻收益、完整归因及候选因子增量检验仍待完成。测试和传输完整性不证明盈利。

最新检查点：20日、21日真实三周期均已归档，共6批29,607条，当前均为事后快照；两日备份逐项一致。真实推理后自动投递已执行，仍无前瞻成交或收益。详见[22日验证记录](reports/CLOSE_REFRESH_AND_ACCOUNTING_20261022.md)。第二阶段云备份仍待完成。
