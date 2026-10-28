# 候选模型：怎样验证新增指标

目的：区分“重新训练带来的变化”和“新增指标的变化”。现有冻结模型继续用于每日排名；本流程生成的模型都是研究候选，不自动替换正式模型。

## 本轮范围

- 原因子重训对照、增加市场状态、增加欧奈尔风格箱体，三个特征组使用相同的股票、训练标签和截止日。
- 5d／20d／12m 三周期 × 五个预定窗口 × 30／60 棵树 × 三组，共 90 次拟合。
- 只使用 2018–2025 研究数据。2026 年既不训练，也不用于选参。行业及完整组合另有60次拟合，档案研究输入及重载核验已完成；扣费增量比较仍待完成。
- 规则来自已封存的[验证协议](PHASE2_RESEARCH_PROTOCOL.md)，不能根据结果更换窗口、缩短周期或收窄原定 24 项比较范围。

## 三步运行

在项目根目录、已恢复真实研究数据和协议封存文件的环境运行。每次输出目录必须是新目录，已有结果不会覆盖。以下目录名是本次检查点；复跑请改输出名称，并让后续步骤指向新目录。

```powershell
python -m ashare_mvp.candidate_dataset --project . --dataset artifacts/market_ratios_causal_quality1 --experiment artifacts/three_horizon_real_causal_quality1 --benchmark artifacts/benchmark_csi300_price_2018_2025 --seal-directory artifacts/phase2/protocol_20261020 --out artifacts/phase2/research_candidate_inputs_20261022_v2

python -m ashare_mvp.rolling_candidates --project . --inputs artifacts/phase2/research_candidate_inputs_20261022_v2 --experiment artifacts/three_horizon_real_causal_quality1 --seal-directory artifacts/phase2/protocol_20261020 --out artifacts/phase2/matched_candidate_models_20261022_v2

python -m ashare_mvp.candidate_portfolios --project . --training artifacts/phase2/matched_candidate_models_20261022_v2 --dataset artifacts/market_ratios_causal_quality1 --replay artifacts/three_horizon_valuation_replay5 --benchmark artifacts/benchmark_csi300_price_2018_2025 --seal-directory artifacts/phase2/protocol_20261020 --reference-evidence config/reviewed_candidate_valuation_refs_20261022_v1.json --out artifacts/phase2/candidate_ranking_accounts_20261022_v3
```

只有前一步的 manifest 显示成功，才能执行下一步。训练结束状态为 `candidate_fits_complete_selection_pending`，表示拟合完成、选模仍待完成。

## 防止看见未来

1. 特征逐发行人读取，核对原始文件、代码、样本和协议哈希。保持原选股样本键，不因未来收益缺失而删股票。
2. 箱体边界及历史成交量均不含信号日；新质量分段不沿用旧段历史。股票特征按同日截面标准化，市场共同状态保留原值，缺失值不补零。
3. 每折只训练标签结束日在隔离日之前的样本；五个窗口开始日之前的 60 个月为最大训练范围，至少 12 个训练信号日。所有特征组保持相同训练键和预测键。
4. 保存全部树数候选的模型、完整排名、特征顺序、预处理、参数、依赖及哈希。重载后逐项验证预测分数，不能只检查文件存在。

## 扣费对照的边界

排名账户跨训练窗口连续运行，下一交易日开盘执行，不在窗口末人为清仓。只安排日历上已经走完的完整持有周期；12m 不以不足一年的收益替代。原始股份、现金及分红应收使用既有账户引擎，佣金 3bp、滑点 5bp，卖出印花税按日期变化，期末不额外假设清仓。

未知的实际持仓价格、公司行动参考价或终值会停止该账户，保存有效前缀和持仓；不能只比较“活到最后”的共同前缀。完整账户才计算相同日期的扣费后配对差异。日历块抽样固定 63 日、2,000 次、种子 42，保留 24 项比较的家族阈值；极端尾部的模拟精度有限。

当前排名对照不替代择时账户、等权可选池对照、行业归因及最终选模审查。配对差异和区块区间是研究诊断，不能据此自动推广模型。沪深300为价格指数，不含分红；原始历史版本、涨跌停成交及部分终值仍未认证。

`--reference-evidence` 为可选的原公告参考参数证据。它验证原始PDF、采集记录、实际观察时间和真实现金／股份锚点，只补除权参考参数。原股东权益、模型、特征和训练标签都不改写。新持仓可能暴露新的历史缺口，已有六项证据不表示全市场所有年份均已校准。实际检查点见[本轮进展](reports/CANDIDATE_RESEARCH_PROGRESS_20261022.md)。

## 保存在哪里

普通 Git 保存代码、测试、配置和轻量证据；完整特征、逐日择时标记数据库、模型及全部分数位于本地忽略目录 `artifacts/phase2/`。只有实际完成备份和恢复核验后，才能声称这些新增产物已可从私有云恢复。

## 原估值事件遗漏的现金分红

可选参数 `--missing-payment-evidence config/reviewed_candidate_missing_cash_20261023_v1.json` 将经原公告复核的遗漏现金事件加到估值输入，再应用参考参数补证。它校验父文件哈希、原PDF与采集身份、公告可见日期及支付日期，拒绝覆盖既有事件。它与仅修正除权参考参数的 `--reference-evidence` 分开记录，不修改训练样本或原始文件，不将公告支付安排冒充银行到账证明。实际状态见[最新检查点](reports/FIRST_PROSPECTIVE_FORECAST_20261023.md)。

最新历史账户与会计核验见[18 个完整账户检查点](reports/CANDIDATE_ACCOUNTS_VERIFIED_20261023.md)。复跑可在上面的账户命令使用 `--reference-evidence config/reviewed_candidate_valuation_refs_20261023_v2.json --missing-payment-evidence config/reviewed_candidate_missing_cash_20261023_v1.json`，并指定新的输出目录。

第二阶段首份加密检查点已完成私有上传及新克隆恢复核验，覆盖最新 V2 的90个研究拟合和18个排名账户；后续行业修正另行保存。恢复命令和未实测边界见[分层迁移](PHASE2_MIGRATION.md)。

## 行业及完整组合检查点

先恢复原件及审核文件，运行历史输入构建，再构建缓存和拟合；以下路径已有结果，复跑必须更换输出名称并同步后续输入。

```powershell
python -m ashare_mvp.industry_candidate_dataset --project . --dataset artifacts/market_ratios_causal_quality1 --experiment artifacts/three_horizon_real_causal_quality1 --technical-cache artifacts/phase2/research_candidate_inputs_20261022_v2 --membership-inputs artifacts/phase2/archived_industry_memberships_20261023_v2 --seal-directory artifacts/phase2/protocol_20261020 --out artifacts/phase2/research_industry_inputs_20261023_v2
python -m ashare_mvp.rolling_industry_candidates --project . --inputs artifacts/phase2/research_industry_inputs_20261023_v2 --experiment artifacts/three_horizon_real_causal_quality1 --matched-training artifacts/phase2/matched_candidate_models_20261022_v2 --seal-directory artifacts/phase2/protocol_20261020 --out artifacts/phase2/matched_industry_models_20261023_v1
python scripts/verify_industry_training.py --project . --out artifacts/phase2/industry_training_verification_new.json
```

核验脚本针对此检查点固定路径，重载抽查1,200分数并核对全部排名与滚动样本键，不重新训练。60个新模型仅属研究候选，不能将其目录直接传给只接受旧90模型家族的账户命令。[当前进展与限制](reports/INDUSTRY_AND_CLOSE_PROGRESS_20261026.md)。

## 行业与组合连续扣费账户

复用原6个完整基线净值，不重训、不在每个窗口重置资金；需要全部旧账户和源文件已恢复。已有输出目录不可覆盖。

```powershell
python -m ashare_mvp.industry_portfolios --project . --training artifacts/phase2/matched_industry_models_20261023_v1 --baseline-accounts artifacts/phase2/candidate_ranking_accounts_20261023_v4 --controls artifacts/phase2/matched_candidate_models_20261022_v2 --verification artifacts/phase2/industry_training_verification_20261026_v1.json --dataset artifacts/market_ratios_causal_quality1 --benchmark artifacts/benchmark_csi300_price_2018_2025 --seal-directory artifacts/phase2/protocol_20261020 --out artifacts/phase2/industry_ranking_accounts_20261026_v1
```

本次12个账户均遇到参考价缺口停止，未生成完整周期绩效或配对推断。需要原公告补证及新目录重跑；详见[首次回放缺口](reports/INDUSTRY_ACCOUNT_GAPS_20261026.md)。`industry_ranking_accounts_evaluated_selection_pending`表示账户尝试流程已结束，是否完整须逐项检查`accounts[].status`，不能把顶层状态当成功回测。

## 实际现金补证后重跑全部对照

`--valuation-inputs <已审核估值目录> --full-family`会重跑全部30个排名账户，含6个基线；不沿用旧基线净值。输入同时绑定原始事件、原公告、真实观察时间及构建代码。只补参考参数与补实际现金分开记录。运行、测试及未解决项见[新检查点](reports/PRIMARY_CASH_COMPLETION_20261026.md)。第一轮12个停止账户保持原样，不覆写。

最新V3估值版本增加由两份除权前原公告计算的参考参数，独立月报仅作事后交叉核对。会计核验支持`--resume`及已完成输出哈希校验。[来源、命令及当前状态](reports/PRIMARY_REFERENCE_AND_RESUMABLE_AUDIT_20261026.md)。此为历史研究重建，不声称认证当年原始字节或实际成交。

## 行业收益归因

按前一交易日开盘已公开的行业快照拆分实际损益，未知分类单列，并核对至独立账户总账。最新30组回放与上一轮27组归因的范围不同；[结果、限制与复跑命令](reports/INDUSTRY_ATTRIBUTION_20261026.md)。

最新30组独立核账、24项配对复核及全30组行业归因已完成，另有独立脚本重算全部历史标签和逐日贡献。[完整核验与剩余工作](reports/FULL_RANKING_INDUSTRY_VERIFIED_20261026.md)。

## 合格池等权对照

已实现并实际尝试三周期，全部因已持有股票的参考校验缺口停止，尚无完整对照收益。全部合格键保留，分红、费用、调仓与完整期限不变；支持独立研究盘及逐步扩大计算窗口。[实际缺口、测试及命令](reports/ELIGIBLE_POOL_FIRST_REPLAY_20261027.md)。

等权对照可用`--reference-evidence config/reviewed_equal_weight_refs_20261028_v3.json`加载新增原公告参考证据；原股东现金、股份和支付日不变。补证后的实际结果、全池风险清单及27日冻结预测见[核心进展](reports/CORE_PROGRESS_20261027.md)，仍未获得完整等权绩效。

下一开盘择时执行与原协议后补细则已实现，18组完整账户及独立会计／意图／配对核验已完成，完整等权和最终选模仍待完成。[规则、测试与限制](reports/TIMING_EXECUTION_20261028.md)。
