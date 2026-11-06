# 原始股份账户的收益归因

有实际有效净值时，模拟账户自动生成`open_account_attribution.csv`、`position_history.csv`及`asset_attribution.csv`。尚无可执行前瞻预测时，不生成收益曲线。

逐日独立重建实际研究份额、现金和分红应收，核对：

```text
净值变动 = 股价变动（含实际送转份额）+ 除息日分红权益 - 佣金 - 滑点 - 印花税
```

已有股东的现金权益在除息日确认；到账时仅从应收转入现金。除息日新买方不领取此前权益。送转调整份额，价格同步调整不产生凭空利润。交易费用按原账户已记录成交和固定假设拆分，受阻订单不计成交费用。缺失持仓价格、现金或权益对不上时拒绝生成完整归因。

每日收益贡献以当日初始净值为分母，累计资金变动以初始资金归一化为1计算；不能将逐日收益率简单相加当复合总收益。每日和单股项均保留，最终持仓市值、现金、应收必须与净值一致。

可选沪深300同日开盘价格指数参照：市场项=上一期投资市值×指数开盘涨幅，剩余价格项=价格变动－市场项。它采用单位贝塔，不估计因果选股alpha；行业归因需要另外的历史可见成员和行业收益证据，当前保持待完成。

对原封存历史账户运行：

```powershell
python scripts/audit_horizon_accounting.py --project . --replay artifacts/three_horizon_valuation_replay5 --data artifacts/market_ratios_causal_quality1 --benchmark artifacts/benchmark_csi300_price_2018_2025 --out artifacts/phase2/my_historical_accounting_audit
```

输入须已恢复，输出目录必须全新。命令核验原估值审计、报价、权益及基准哈希，逐块读取原始报价以控制内存，不重训或改写账户。复用可分割股份、日期印花税和未强制清仓的原研究假设；涨跌停、整手、分红税及送转可卖时点尚未实盘认证。

真实三周期各711日完成对账，最大误差低于9×10⁻¹⁶；两个独立入口生成的CSV完全相同。[结果与限制](reports/CLOSE_REFRESH_AND_ACCOUNTING_20261022.md)。这项审计不替代候选因子增量、独立到期收益或实盘检验。
