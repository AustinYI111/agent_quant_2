# 工作盘日更

设置`PQR_WORK_ROOT`为你的本地数据工作目录。配置中普通相对路径对应代码项目，`@work/`对应工作目录；换盘后修改环境变量即可，配置和预测来源不写死盘符。两个目录必须分开，路径越界会被拒绝。

## 收盘原始数据采集

通用入口不再需要每天复制并修改采集脚本。下例仅在29日通过当前封存规则的17:20门禁后运行；日期和输出目录按实际交易日修改：

```powershell
python -m ashare_mvp.close_capture `
  --work-root $env:PQR_WORK_ROOT `
  --seal artifacts/phase2/protocol_20261020 `
  --signal 2026-10-29 --calendar-end 2026-12-04 `
  --out '@work/incoming-20261029/close_capture_20261029_v1'
```

输出的`market/latest`为原始行情，`market/exchange_calendar.csv`为交易日历，`reference`包含证券简称、供应商行业版本及最近三个交易日的沪深300价格指数。必须是明确请求的最新已完成交易日，不能把其他日期的响应改标签；输出只能在显式工作盘的新目录。错误／部分响应留档，不能视作完整采集。需要重新请求时使用新的目录，原底层行情采集器另有独立的校验续采能力。

实际观察时间和全部文件哈希保留。次日重新查询历史日期的数据可以与原数据比较，但不能补授权前一日的证券状态视图。可用`scripts/audit_close_recapture.py --help`查看逐字段比较命令。

本入口不更新模型、不自动生成预测，也不代表数据已获准使用。财务依赖合并、公司行动采集及公告复核、价格质量和缺失股票核查仍须完成，再准备当天的冻结模型输入。完整无人值守日更尚未验证。

## 财务刷新

利润与资产负债可通过`python -m ashare_mvp.financial_refresh`统一采集并保留各自观察历史，详细配方、输出和失败处理见[财务刷新](FINANCIAL_REFRESH.md)。本入口可以日内运行，不冒称最终收盘财务快照；完成后仍需公司行动和行情质量复核，再接入冻结推理。

## 公司行动候选

`company_action_intake`统一采集计划并生成公告复核队列，`reviewed_cash_extension`在原公告核对后暂存现金事件。计划不是支付，实际现金与参考金额分别保存；暂存后仍需真实收盘行情门禁。详见[公司行动接入](COMPANY_ACTION_INTAKE.md)。

## 冻结模型与名单

原`frozen_daily.py`、模型和封存协议保持不变。新增入口复用相同因子、时点、原生缺失值和模型顺序，将新的状态库与快照写入工作目录。

在项目目录运行已准备好的28日输入：

```powershell
python -m ashare_mvp.frozen_daily_workspace `
  --work-root $env:PQR_WORK_ROOT `
  --inputs '@work/incoming-20261028/daily_inputs_20261028_v1/frozen_inputs.json' `
  --signal 2026-10-28 `
  --out '@work/research/daily_example_new' --exploratory
```

真实输出已保存到`@work/research/frozen_daily_research_20261028_v1`。再次运行须用新输出目录；已有预测不能覆盖或重新设置创建时间，过了次日开盘才生成的回填会被标记为事后研究。

原始研究排名还需生成当日证券状态过滤视图：

```powershell
python -m ashare_mvp.risk_selection `
  --work-root $env:PQR_WORK_ROOT `
  --snapshot '@work/research/frozen_daily_research_20261028_v1' `
  --snapshot-sha256 '<该快照manifest.json的SHA256>' `
  --reference-capture '@work/incoming-20261028/reference_capture_20261028_close' `
  --out '@work/research/selection_example_new'
```

状态来源必须是信号当日实际获取的证券列表。新视图保留原分数，以证券简称额外排除ST和退市整理身份，并记录自己的真实创建时间。创建晚于对应开盘的视图不能补授权历史交易。该运行规则并非原研究预注册规则，不得用于回看收益后选择历史策略。

入档使用`workspace_prediction_archive.archive_daily_snapshot`，要求原始快照哈希，保留全部周期、全部候选和原时钟；重复导入幂等，修改已有分数会被拒绝。`frozen_daily_workspace`也支持`--archive`指定已有总档的相对路径。

当前模拟使用`workspace_paper_tracking.replay_paper_books`，传入显式`work_root`以及`selection_views`映射：`{"2026-10-28":{"directory":"@work/research/risk_selection_20261028_v1","manifest_sha256":"<该视图的哈希>"}}`。28日起缺少对应视图即停止，不能退回原始Top20直接执行；既有历史账户仍保留旧入口作对照。只按固定调仓网格模拟，保留被阻止卖出的旧持仓。

这些入口已用于28日真实日更及测试，尚不是已验证的无人值守日更服务，也没有实盘盈利认证。数据采集、公告复核与输入准备仍需继续集成。备份恢复目前暂缓。
