# 有界证券类型与完整真实输入检查（2026-10-19）

增加 `prepare-market-ratios --instrument-scope`，以明确审阅的首次上市公告区分普通A股和存托凭证。原有 `is_ashare` 已不将689代码纳入普通A股报价，但事件覆盖检查此前把689009的3条记录当作未知身份；本轮统一这一范围口径，并保留所有原始数据。

## 证据和边界

[九号公司首次上市公告](https://static.cninfo.com.cn/finalpage/2020-10-28/1208634455.PDF) 于2020-10-28披露，PDF第20页（印刷第19页）载明证券代码689009、存托凭证上市批准和首次上市时间2020-10-29。原PDF SHA-256：`527b37a7ce65a24ba52d85ef5789b49bc2a92fe13879100a7479ecff9b881442`；原文件已下载并渲染查看第1和20页。

`config/reviewed_instrument_scope.json` 只声明这一明确代码，生效于2020-10-29，当前应用上限为2025-12-31。不是按2026年的风险状态回溯过滤，也不是将所有未知代码都忽略；普通A股不能通过这个接口排除。窗口内的3条事件在准备审计中分类为 `excluded_instrument`，原始事件覆盖CSV仍保留4条未支持记录，其中1条位于2026年、超出当前窗口。

没有证据、未知代码、范围日期之外或日期不明的记录仍然未解决；已有支付资产与排除证据冲突时直接失败，不能移除权益。原始报价和支付表不被此步骤改写，风险状态和退市终值不从证券类型推断。审计保存原始状态、除息日、原始报告期、来源URL／哈希／页码及策略快照。

## 完整真实运行

```powershell
python -m ashare_mvp.cli prepare-market-ratios --market artifacts/market_history_union_2018_2025_complete1 --financials artifacts/financial_dependencies_extended_identity2 --master artifacts/master_union2 --actions artifacts/dividend_extended_history_union4 --out artifacts/market_ratios_extended_bound3 --audit-out artifacts/market_ratio_readiness_extended_bound3 --reference-tick-size 0.01 --code-changes config/same_issuer_code_changes.json --reference-overrides config/reviewed_reference_cash.json --non-listing-evidence config/reviewed_non_listing_bounds.json --instrument-scope config/reviewed_instrument_scope.json
```

真实2018—2025数据已实际运行到 `price_and_lifetime_coverage`：

- 全部 **1,942个日行情文件**已核验哈希；保留 **8,609,196条报价、5,401个稳定发行人**。
- **3条CDR事件范围问题已分类**，没有残留支付证据或普通证券身份阻断项。
- **1,607项价格调整仍未解释**，恰好对应旧区间751项与扩展区间856项；没有放宽参考价校验。
- **154,432个缺失估值期间**全部位于 **220个发行人的退市后期间**；存续期缺行情为 **0**。这是期间数，不是股票数或漏采交易日数。
- 受影响终止日期中，**0个不在所给交易日历**；终止日原始非交易记录的计数不能直接当作全部受影响发行人数。
- 2,443条已核实非生效代码别名记录被原有身份规则隔离；1,258条非普通A股报价按既有范围处理。12,265条停牌但成交量未知的记录保留未知值，未伪造为零。

准备命令因价格／终值证据门禁主动退出1，未生成训练数据目录。这不是运行崩溃，也不能表述为数据已可回测。

## 测试与距目标的差距

完整测试 **452 passed，170.04秒**。新增17项覆盖日期边界、未知或普通股票不得排除、已有权益不得删除、输入不变、审计哈希和数据准备集成；相关39项测试也已单独通过。

三周期LightGBM训练、滚动样本外及净成本指数比较工程已具备，但真实全市场三周期绩效仍未产出。剩余重点是价格／权益证据和退市结算，以及在研究流程中明确区分“历史已知数据质量”与“未来结果是否可得”，避免以未来可得性筛掉预测候选。任何后续改造都不能填造终值或按未来是否退市选择证券。

完整数据及原PDF仍在本地；本次普通Git交付代码、配置、测试和轻量审计，尚未创建包含新增数据的私有加密备份Release。main保持未合并，测试通过不等于真实投资盈利。
