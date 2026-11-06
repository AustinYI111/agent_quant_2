# 冻结预测的模拟账户跟踪

`python -m ashare_mvp.paper_tracking` 将预测账本连接到现有的原始股份账户。当前已完成代码、合成场景和真实旧预测排除检查，**尚无真实前瞻成交或收益记录**。

## 账户规则

- 逐批核验封存协议、模型、分数内容及预测实际创建时间。事后预测不执行；不能拿后来计算的排名回填早先开盘。
- 5日、20日调仓网格从2026年第一个实际交易日固定起算；12个月按同一锚点的周年日取下一真实交易日。不会因首次收到预测而平移网格；拒绝缺少研究边界的截断日历，调用方必须提供完整、经来源校验的交易日历。
- 下一交易日开盘执行，Top20等权，3bp佣金、5bp滑点，卖出印花税沿用已固定的日期表；使用可分割股份的研究假设，未认证涨跌停、整手或实际成交能力。复用账户在除权日计入股份权益，尚未分别模拟送转股份到账及可卖日期。
- 买入受阻的份额留现金，不按未来收益替换股票。已有持仓不会因ST、退出新名单或数据缺失而消失。
- 股份、现金和分红应收单独计算，送转股改变实际份额。持仓价格、参考价或终值不明时，保存最后有效净值、持仓及异常，不补零。
- 缺少规定调仓日的前瞻预测时，仅保存缺口之前的有效前缀。没有可执行前瞻预测时，不生成虚假的零收益曲线。

## 输入与运行

需要预测SQLite、封存协议、完整交易日历、原始不复权报价和公司行动。报价须使用已核实的统一发行人代码，字段至少为 `date,symbol,open,close,preclose,tradestatus`。**必须覆盖账户期间所有曾持有的股票和每个实际交易日，不能只传当前Top20。** 公司行动格式与已校验的 `actions.csv` 一致。

建议使用预测账本的一致性备份作为输入，避免同时写入。输入与输出路径均相对项目根目录；每次输出使用新目录。可选 `--benchmark` 接入沪深300价格指数的同日开盘价；不能把价格指数对照称为含分红超额收益。

以下为本机已执行的10月20日研究快照排除检查。输入导出文件仍在本地，恢复代码不会自动恢复它们：

```powershell
$snapshot = 'artifacts/phase2/frozen_daily_research_20261020_v3'
$inputs = 'artifacts/phase2/paper_inputs_20261020_v1'
$batches = (Get-Content "$snapshot/manifest.json" -Raw | ConvertFrom-Json).receipts
$paperArgs = @('-m','ashare_mvp.paper_tracking','--project','.',
  '--ledger',"$snapshot/prediction_backup.sqlite",'--seal','artifacts/phase2/protocol_20261020',
  '--prices',"$inputs/raw_quotes.csv",'--actions',"$inputs/actions.csv",'--calendar',"$inputs/calendar.csv",
  '--asof','2026-10-20','--out','artifacts/phase2/paper_tracking_repeat_20261020')
foreach ($batch in $batches) { $paperArgs += @('--batch-id',$batch.batch_id) }
python @paperArgs
```

## 输出与验证

`manifest.json`区分每个周期的等待、有效前缀、缺少预测或持仓估值异常状态。存在可执行记录时，另存 `scheduled_top20.csv`、`open_nav.csv`、`orders.csv`、`positions.csv`；最后有效日的份额市值、现金、分红应收必须与净值对账。可选基准输出 `benchmark_comparison.csv`。净值按同日开盘计量，不当作收盘收益。

真实10月20日的三个快照均于21日开盘后生成，因此全部排除；三个周期没有成交、没有净值、没有收益结论。[真实排除检查证据](reports/evidence/PAPER_TRACKING_GATE_20261021.json)。分红、送转、持仓异常、调仓缺口及交易成本的计算测试使用明确的合成数据，不代表实盘验证。

[多日预测统一归档](PREDICTION_ARCHIVE.md)已实现，并验证真实单日三周期的备份恢复和账户排除检查；多日衔接目前为合成场景验证。连续实时日更、真实前瞻成交跟踪、完整收益归因、行业等权对照及候选因子增量检验仍待完成。第二阶段新增数据和跟踪输出仍仅在本地，尚未进入私有云备份。

本提交最终本地全量671项通过（161.43秒），账户与预测账本相关34项通过。云端结果以该提交Checks为准。

有效净值现在同时生成逐日及单股会计归因、每日持仓历史，详见[会计归因](ACCOUNTING_ATTRIBUTION.md)。已在原真实历史账户对账；21日新增排名仍为事后记录，无前瞻成交。行业归因和候选增量检验继续待完成。
