# 财务版本刷新

`financial_refresh`复用现有分页采集、实际观察时间门禁、依赖版本合并和同报告期关联。不会训练、选参、生成预测或替换旧文件；新输出必须写入显式工作盘的新目录。

先准备一个JSON输入文件，所有路径均相对项目，`@work/`指向`PQR_WORK_ROOT`。例如29日刷新三季报：

```json
{
  "schema": "observed_financial_refresh_inputs_v1",
  "signal_date": "2026-10-29",
  "report_start": "2026-09-30",
  "report_end": "2026-09-30",
  "calendar": "@work/incoming-20261029/close_capture_20261028_reobserved_v1/market/exchange_calendar.csv",
  "code_changes": "config/same_issuer_code_changes.json",
  "history": [{
    "earnings": "@work/incoming-20261028/financial_history_20261028_v1/earnings",
    "balances": "@work/incoming-20261028/financial_history_20261028_v1/balances"
  }]
}
```

```powershell
python -m ashare_mvp.financial_refresh `
  --work-root $env:PQR_WORK_ROOT `
  --inputs '@work/incoming-20261029/financial_refresh_inputs_20261029_v1/inputs.json' `
  --out '@work/incoming-20261029/financial_refresh_20261029_v1'
```

实际29日结果已经存在，不能重复使用该输出目录。后续日期要修改`signal_date`为真正采集当天，选择已结束的报告季度，并把`history`指向上一次成功输出的`history/earnings`和`history/balances`。可以提供多个既有完整依赖快照，但重复路径、部分快照、哈希变更或未知时钟会被拒绝。不要只保留最终合并面板而丢弃两类依赖版本。

输出包含`raw`原始响应、`normalized`本次规范化数据、`history`合并后的依赖及最终财务面板、`refresh.json`时钟与哈希。任一源请求不完整时标记`partial_raw_no_normalization`，不生成可用历史；失败文件保留，重新采集另用新目录。底层采集器另有经校验的续采入口。

财务刷新允许日内进行，因此不会冒称当天最终快照。每条数据的可用日期不能早于实际完整观察的上海日期，公告／更新／入库的原下一交易日规则继续生效。两类依赖先各自保留历史版本，再按同发行人、同报告期、当时可用日期关联；迟到负债不会填入早期利润版本，明确的新缺失值不会被旧值填补。当前供应商记录不等于历史原始版本认证。

独立复核使用`scripts/audit_financial_refresh.py --help`查看参数；它使用版本索引逐条选择依赖，不调用生产合并或关联函数，检查四个财务因子、键集合和各依赖时钟。来源描述性字段和审计报表真实性不由此认证。

成功的`history/combined`可作为下一次冻结推理配方的`financials`，原财务快照仍留在`financial_history`。仍须先完成当天收盘行情、公司行动和缺失覆盖复核，再准备推理配方；本入口不自动启动这些步骤。备份恢复暂缓。
