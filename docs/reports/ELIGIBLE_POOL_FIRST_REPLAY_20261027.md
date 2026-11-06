# 合格池等权对照：首次真实回放

**三周期尝试均保存了明确的估值中断，完整对照尚未完成，没有输出完整周期收益或配对推断。** [检查点](evidence/ELIGIBLE_POOL_FIRST_REPLAY_20261027.json)。

新对照使用原始合格样本的全部股票，不按分数、因子缺失或目标收益删股；沿用30/60树基线相同的决策日与原定结束日、原始股份、毛分红权益、3bp佣金、5bp滑点及按日期的卖出印花税。每次决策对合格池等权配置，停牌买不进的份额保留现金，不对剩余股票重新归一化。期末不强制清仓。

## 实际运行

5日与20日账户均在2021-08-23持有`sh.600690`时触发参考价校验；12个月账户在2021-08-04持有`sh.600208`时触发同类校验。原计划终点分别为2025-12-25、2025-12-18、2025-09-02，没有缩短或改写。已持有的正股份、有效前缀净值及订单保留在D盘，不删除问题股票、不填零、不把前缀当完整结果。

最初计算窗口覆盖63个交易日。若未发生估值中断，程序会从头扩大至126、252日直至原终点；只有到达原终点并与两份基线逐日匹配后，才保存完整指标。这是降低峰值内存的计算方式，不改变调仓或持有期限。首次窗口的实际原始行情分别为269,071、269,071及268,461行。

## 验证与下一步

新模块17项测试、连同调仓与会计相关共32项测试通过。端到端测试实际运行三周期账户，覆盖完整12个月期限、跨目录输出、分红、停牌现金及未知持仓停止；未重新跑全套回归，GitHub CI单独记录。模型、因子和2026独立验证边界保持不变。

下一步按原实施公告补证上述两项参考参数，在新目录重跑；若后续还有缺口继续明确保留。等权控制覆盖数千股票，会暴露Top20账户未持有股票的额外数据问题，不能据此自动排除这些股票。

```powershell
python -m ashare_mvp.eligible_equal_weight --project . --accounts artifacts/phase2/full_family_ranking_accounts_20261026_v2 --dataset artifacts/market_ratios_causal_quality1 --experiment artifacts/three_horizon_real_causal_quality1 --benchmark artifacts/benchmark_csi300_price_2018_2025 --seal-directory artifacts/phase2/protocol_20261020 --output-root $env:PQR_RESEARCH_ROOT --out research/eligible_equal_weight_new
```

先将`PQR_RESEARCH_ROOT`设为容量充足的研究盘。`--out`相对于该目录，禁止覆盖既有结果；省略`--output-root`时仍支持项目内相对路径。本轮实际结果写D盘，未运行备份或恢复。
