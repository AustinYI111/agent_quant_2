# 公司行动补证与全家族重跑（2026-10-26）

20项预定持仓参考差异中，19项已完成原公告核对并生成新估值版本：16项仅修正除权参考参数、2项补入原事件表遗漏的现金分红、1项将只含年度分红的既有事件补齐为公告明确列出的年度及特别分红合计。三种操作分开校验，不能用参考价修正掩盖实际股东权益变化。

原公告、原事件表和旧回测均保留。补证保留真实2026年观察时间，历史公开日期后的下一交易日才视为研究可用；没有银行到账确认，不宣称认证了原始历史字节版本。训练模型、特征、标签及选参协议未改变。[可检查的补证及运行状态](evidence/PRIMARY_CASH_COMPLETION_20261026.json)。

## 为什么重跑基线

实际现金发生变化，不能只修候选账户、复用旧基线净值。全家族模式在共同的27,659条估值事件下重跑3周期×2树数×5组，含6个原因子基线、市场状态、箱体、行业和组合共30账户。函数拒绝在新增估值输入时复用旧基线；样本键、成熟周期、成本和原始价格保持对照一致。

此文生成时完成了16个账户尝试，其中16个完整账户；完整家族尚在运行时不按局部结果选模。每个账户是否完整须看其逐项状态，最终会计重建与配对复核另行交付。此前12个账户全部停止的首次回放保留不改写。

000157在2023-07-21的参考差异仍未应用补证。已取得权益分派和A股回购完成公告，但实施公告没有直接列出除权参考参数；不从观察到的价格反推数值，不删除股票或缩短账户来绕过它。

## 验证与复现

完整回归826项通过（318.91秒）。新增测试覆盖现金组件精确求和、既有部分事件锚点、重复／含糊来源、真实观察时间、支付日期、禁止改股份，以及估值输入变动后强制重跑全家族。公开构建脚本在新目录实际执行，产生的估值事件文件与本轮V2逐字节相同。

先恢复全部原件、旧研究产物及新60模型，再在项目根目录执行；输出目录必须不存在：

```powershell
python scripts/assemble_industry_valuation_updates.py --out artifacts/phase2/industry_valuation_updates_new
python -m ashare_mvp.industry_portfolios --project . --training artifacts/phase2/matched_industry_models_20261023_v1 --baseline-accounts artifacts/phase2/candidate_ranking_accounts_20261023_v4 --controls artifacts/phase2/matched_candidate_models_20261022_v2 --verification artifacts/phase2/industry_training_verification_20261026_v1.json --dataset artifacts/market_ratios_causal_quality1 --benchmark artifacts/benchmark_csi300_price_2018_2025 --seal-directory artifacts/phase2/protocol_20261020 --valuation-inputs artifacts/phase2/industry_valuation_updates_new --full-family --out artifacts/phase2/full_family_ranking_accounts_new
```

本批代码、补证配置与轻量报告上传开发分支；完整模型、原件、数据库及账户结果仍仅本地保存，下一私有云快照尚未完成。测试通过不证明策略盈利。择时、等权对照、行业归因、完整家族选模与独立成熟验证继续推进；不升级正式模型，不合并main。
