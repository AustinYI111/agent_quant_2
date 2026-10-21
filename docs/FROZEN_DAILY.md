# 冻结模型的每日研究排名

入口 `python -m ashare_mvp.frozen_daily` 使用已封存的 5d、20d、12m 模型计算排名，不重新训练或升级模型。当前为研究工具，尚未接通可靠的自动日更服务或模拟账户。

## 运行

先恢复原研究数据、模型和第二阶段采集文件。仅下载代码不能运行真实推理；第二阶段数据目前还没有进入私有云备份。

在项目根目录执行，输出目录必须尚不存在：

```powershell
python -m ashare_mvp.frozen_daily --project . --inputs config/frozen_daily_inputs_20261021.json --signal 2026-10-20 --out artifacts/phase2/my_research_snapshot_20261020 --exploratory
```

需要自动统一归档时，在命令末尾增加`--archive artifacts/phase2/prediction_archive/predictions.sqlite`；三周期成功保存后原子追加，投递失败不改写成功快照。重试与备份见[统一归档说明](PREDICTION_ARCHIVE.md)。

这份输入配置绑定本轮本地快照，不能只改信号日期就宣称数据已更新。后续日期需要先采集、校验对应交易日行情，并登记财务、公司行动和证券变更来源。日历采用供应商实际交易日；17:20 前不接受当日收盘快照。

## 输出

| 文件 | 用途 |
| --- | --- |
| `top20_5d.csv`、`top20_20d.csv`、`top20_12m.csv` | 三周期前20名；分数是排序值，不是预期收益率 |
| `scores_*.csv`、`features.csv`、`universe.csv` | 完整候选分数、因子和筛选记录 |
| `predictions.sqlite`、`prediction_backup.sqlite` | 不可改写的预测记录及一致性备份 |
| `quality_breaks.csv`、`suspended_volume_audit.csv` | 参考价断点及停牌成交量空值审计 |
| `manifest.json`、`feature_receipt.json` | 状态、真实创建时间、模型/输入/代码校验信息 |
| `states.sqlite` | 特征计算状态，不能用于估值持仓或当作模拟账户 |

只有 `manifest.json` 状态为 `research_snapshot_saved` 才表示三个周期全部保存成功；失败目录保留错误，不复用、不覆盖。若没有质量断点，`quality_breaks.csv` 不生成。

## 数据与时间约束

- 按原冻结规则筛选沪深A股，排除选股日的ST、停牌、无正成交量和上市未满365天的股票；原模型允许的未知因子保留 NaN。
- 停牌日缺少成交量时保留原空值并记录审计，不补零；正常交易日未知成交量、负数及无穷值仍拒绝使用。
- 公司行动参考价不连续时，延续原模型的因果分段与缺失值规则，完整留痕；分段不改变投资者实际股份、现金或应收权益。
- 财务按可见的最新报告期与修订版本取值。新抓取版本不回填为过去已知；原供应商历史版本仍未认证。
- 保存实际创建时间；下一交易日开盘后才形成的记录标为 `retrospective_independent_snapshot`，不能用于声称前瞻模拟收益。2026数据不参与选参。

本轮真实执行、模型重新加载及账本备份读回结果见[执行记录](reports/FROZEN_DAILY_RESEARCH_20261021.md)。测试和保存成功均不证明盈利能力，也不表示模拟跟踪、收益归因或候选因子增量验证已经完成。
