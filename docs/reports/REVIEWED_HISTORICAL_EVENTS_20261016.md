# 公告审阅与补充历史事件合并（2026-10-16）

本轮已把原始版本冲突转为明确的公告证据处理，真实规范化 564 条补充事件并新增 300 条到主事件库。真实 LightGBM 训练、Top 20 和样本外绩效仍未完成。

## 可重放的原始冲突处理

新增 `config/reviewed_raw_dividend_versions.json` 与 `normalize-dividends --reviewed-versions`。每个例外必须锚定整个原始文件 SHA256、同一执行键全部行号、选中原始行、原始金额／股份／完整描述，及实际实施公告 URL、原件 SHA256、金额页和支付页。

审核记录要求公告后的交易日可见，且不晚于执行日；保留原始公告和支付日期，不能借审阅改写时点或改变送转股份。锚点不符、遗漏额外版本、未审阅、未使用记录、未来可见日期和篡改证据均拒绝。

300110／2018-05-22 的冲突已通过[当时的正式实施公告](https://static.cninfo.com.cn/finalpage/2018-05-15/1204945742.PDF)核对，原件 SHA256：`632a4a45865bc9ac8e808cefa2ea8ad059ca55864266dc4d63d02f57fb9b262f`。第 1—2 页完整提取、渲染并查看，区分原提案与按新总股本实际实施的条款，明确登记及现金派付日期。没有用后来的年报作为当时信号。两条原始响应保留，规范化采用明确实施条款；既有主库该键实际金额已正确，本轮不改主库权益。

## 有依据的小数精度处理

补充记录与既有事件对账时，五处金额／送转差异来自数字字段与描述条款的小数精度不同。新增逻辑仍先执行原有金额／描述交叉检查；只有描述每股值比原数字更精确，并能按原数字字段声明的小数位、十进制 HALF_UP 舍入回该字段，才保留描述的更精确值。

没有把统一金额容差放宽，也不会用低精度描述覆盖更精确的数字字段。精度不能这样证明时不作精度替换；原有实际冲突规则继续有效。每次替换保存原文字面量、描述每股值、字段、源行及规则至 `precision_audit.csv`。

## 实际运行结果

- 原始采集不变：1,092 次请求、573 条原始记录。规范化现保留 564 条事件，0 条拒绝；9 组重复中，8 组按兼容版本处理、1 组通过明确公告审阅，额外版本不再重复计为经济权益。
- 记录了 28 次字段精度细化，不代表 28 个新增事件；全部仍带原始历史版本未认证标志。
- 564 条中，264 条核对已有事件、300 条新增。`dividend_history_union5` 正式主库为 18,538 个唯一发行人／执行日键。
- 原有 18,238 条事件的真实现金、股份倍数逐值不变；可见日期相同或更晚，没有提前。原计划覆盖中的未解决项没有删掉。
- 同一封存旧行情片段 3,130,445 行、截止 2021-06-08：未解释价格调整从 560 降至 321，指定报价精度匹配数为 3,448。该输出 `price_action_audit_history_union3` 使用 union4；最终 union5 的事件字节哈希相同，额外封存审阅及精度证据。这不是完整新行情的价格审计。

| 关键产物 | SHA256 |
| --- | --- |
| 规范化补充 actions.csv | a2ab2aacd6ff6351c1cab372f402f907828b7d58dda54ee7ac4421253884fa89 |
| 正式合并 actions.csv | fbd0cac22e052cea390a60ce73d7d49b66168570a22078b610a0a0a6cf88b981 |
| 规范化 precision_audit.csv | 7570d38356b44d02469d9bd1b4e874e4982c79c19e8f84a0142c58fc931be4d5 |
| 封存 input_reviewed_versions.json | 797db62a125d3bbd4b936fe5b57ac2a6816b6781e88331ff63acbdef255d0540 |

合并器还会核对并复制补充来源的重复审计、精度审计及审阅证据，防止下游仅留下外部文件的哈希。上述输出哈希与原事件权益已经独立检查。

## 运行方式

输出目录必须尚不存在或为空，已有封存结果不覆盖：

```powershell
python -m ashare_mvp.cli normalize-dividends --data artifacts/historical_dividend_probe1 --calendar artifacts/market_history_2018_2024/calendar.csv --reviewed-versions config/reviewed_raw_dividend_versions.json --out artifacts/historical_dividend_normalized2
python -m ashare_mvp.cli merge-dividend-events --base artifacts/dividend_history_union3 --supplements artifacts/historical_dividend_normalized2 --calendar artifacts/market_history_2018_2024/calendar.csv --code-changes config/same_issuer_code_changes.json --out artifacts/dividend_history_union5
```

原始响应和公告文件在本地 artifacts 中，并非通过普通 Git 下载即可获得；恢复须包含对应数据快照。

## 范围、测试和下一步

完整上市范围复验 `full_market_universe_audit2` 已完成 1,479 日哈希／键／行数／日期核对：无 unknown_master、missing_lifetime_quote 或普通 outside_lifetime，另有 143 条终止当日非交易记录。该结果不证明发行人原始身份已全部认证，也不把非交易记录变成可执行终值；未来代码回填仍按代码生效政策隔离。

最新准备入口 `market_ratio_readiness_history_union5` 使用 master_union2 和封存 union5，在来源清单阶段仍有 3 个 IPO 身份缺口，尚未生成可训练目录。全量行情的流式连接 `aligned_market_dependencies6` 已完成，逐日核对 1,479 个文件，输出 6,236,285 行、5,247 个原始代码，最大单日截面 5,104 行。该诊断快照连接的是先前已封存 financial identity2，价格审计只读取其日期／代码／收盘／前收盘字段。

新版 `balance_normalized_progress7` 也已完成：5,469 个证券、130,186 条记录，118,243 条在日历中可见、165 条负债率缺失，2 个未完成采集证券隔离。面板 SHA256 为 `ba3744a2f5fca8731c1399de12da4edda5dc86ef95cac3d2f7a67043ae5be2c4`。

实际生成的新 `financial_dependencies_identity3` 为 107,013 条事件、97,325 条已知负债率、9,688 条保持 NaN；独立核验了面板哈希、唯一版本键、财报／余额依赖不晚于可见日期及发布时钟严格在可见日期之前。52 组跨别名相同版本继续留审计。面板 SHA256 为 `9ff890c759d2a5266b5846e068e64a11b95e25fadb45dbfafab4edcdd3370b18`。准备入口已使用该新财务版本生成 `market_ratio_readiness_financial_identity3`，仍为相同 3 个 IPO 身份缺口，未进入训练转换阶段。

完整行情价格审计 `price_action_audit_full_history1` 已完成：应用生效代码政策隔离 2,196 条无效别名报价后，6,234,089 行、5,244 个稳定发行人；指定精度匹配数 6,432，未解释调整 753 处。另有累计 67,679 个 missing_sessions_or_terminal_valuation 期间记录，该类是从首条报价延伸至日历末尾的估值连续性检查，须结合生命周期和终值证据，不等于原始采集少了这些交易日。完整上市范围检查已无存续期缺报价，终止后的估值处理仍未认证。完整价格结果不能用旧片段的 321 处替代。

最终完整 335 项测试通过（286.36 秒）。覆盖原始文件／全部冲突行锚定、公告金额与支付页、可见日期、未使用／篡改证据拒绝、精度不能证明时保持原数字，以及下游审阅和精度审计的哈希核验／封存。

继续解决上市范围证据，核验完整新行情剩余价格差异、外部出资公司行动和退市终值，再运行真实排序及 3／6／12 个月样本外评估。新原始响应、公告和完整数据输出仍仅在本地，尚未进入新一轮私有加密备份；公开分支保存代码、结构化审阅配置、测试及轻量说明。测试通过不证明盈利。
