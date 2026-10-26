# 完整排名账户与行业归因核验（2026-10-26）

**30组完整排名账户、30组独立核账、24项排名配对复核及30组行业归因均已完成。** 此结果替代上一份报告中的运行中状态，但不改变选模结论或表示第二阶段结束。[检查点与哈希](evidence/FULL_RANKING_INDUSTRY_VERIFIED_20261026.json)。

使用最新V3审核估值输入，保留原150个候选模型、原特征及训练划分。独立按原始股份、现金、应收分红与实际费用重建，最大账户误差1.4e-15。已实际执行完成检查点的`--resume`，重新校验来源、代码及全部输出哈希后成功返回30组结果。

行业归因使用收益区间开始时已公开的历史完整快照。每组实际价格损益、毛分红权益、佣金、滑点、印花税、净损益、市场参考及余项均逐日加总核对；最大误差1.6e-15。独立脚本没有调用生产归因或历史分类函数，重新按完整公告目录与发行人成员表选择每条标签，并核对所有逐日账户汇总。[独立核验收据](evidence/FULL_INDUSTRY_INDEPENDENT_VERIFICATION_20261026.json)。

未知行业保留为UNKNOWN且保留真实权益和收益；新完整快照中缺失的发行人不会继承旧分类。最长行业公告年龄820天，来源实际抓取于2026年，因此分类陈旧性与历史原始字节版本未认证仍是限制。行业损益汇总不是行业指数回报；单位贝塔沪深300价格参考的余项不是因果alpha。

[30组扣费指标](evidence/FULL_RANKING_METRICS_20261026.csv)属于已用于研究的历史区间。部分长期账户累计收益为正，但排名配对调整区间均未排除零，没有证明候选增量有效，也没有选参或升级正式模型。当前冻结模型不因这次结果变化。

本模块相关47项测试通过；前一提交完整857项回归通过，当前代码提交的CI状态单独在PR中记录。公开Git仅保存代码、测试、轻量指标和核验摘要；原始研究数据、模型与逐日账本仍只在本地，新云快照及恢复尚未验证。备份密码仍单独保存在本机，未加入Git。

尚待完成：合格池等权对照、买卖择时账户、预定家族的最终选择复核、未参与选参的新数据验证、稳定日更，以及新版私有云备份与跨电脑恢复。不能把账本对上或测试通过等同于盈利能力。

## 复核

恢复数据后，在项目根目录运行。核验脚本针对本检查点固定相对路径；已有核验收据不会覆盖，重跑前应在新工作副本中更改输出名称。

```powershell
python -m ashare_mvp.ranking_account_audit --project . --source artifacts/phase2/full_family_ranking_accounts_20261026_v2 --dataset artifacts/market_ratios_causal_quality1 --benchmark artifacts/benchmark_csi300_price_2018_2025 --out artifacts/phase2/full_family_accounting_verification_20261026_v4 --resume
python scripts/verify_industry_attribution.py
```

生产归因的可复跑命令及输出说明见[归因模块说明](INDUSTRY_ATTRIBUTION_20261026.md)。上一份27组归因与3组跳过记录保持原样；最新目录`industry_account_attribution_20261026_v2`为全部30组。
