# agent_quant_2 — A股多因子选股 MVP

当前第一阶段目标是分别训练未来 **5／20 个交易日及约 12 个月**的 LightGBM 排序模型，并按周期完成严格时间样本外的成本、指数收益、回撤与换手比较。三周期标签入口已实际运行；独立三模型及统一指数评估仍在开发，见[研究协议与标签入口](docs/reports/THREE_HORIZON_PROTOCOL_20261016.md)。

新增 `ashare_mvp/` 为独立研究模块，旧 `agent_quant-main/` 演示保留。代码检查与使用说明见 [MVP说明](docs/MVP.md)，运行结果见 [开发与验证报告](docs/IMPLEMENTATION_REPORT.md)，复查修复与测试见 [Debug报告](docs/DEBUG_REPORT.md)。

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e '.[test]'
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ashare_mvp.cli demo --out artifacts/demo_data
.venv/Scripts/python -m ashare_mvp.cli run --data artifacts/demo_data --out artifacts/demo_run
```

示例输入为**合成数据**，仅用于检验训练/回测工程。真实选股需要提供具备公告版本、历史股票池、退市和停牌记录的数据。

新增公开数据采集与审计、同期限因子对照、技术观察区间、CSV持仓和中文日报，见[四阶段进度与运行说明](docs/PHASE_PROGRESS.md)。公开源实测取得行情和财务样本，但完整股票池与财报版本仍待验证；当前不能宣称真实全市场回测完成。

本版完整运行检查与已知边界见 [MVP 0.1.0交付说明](docs/RELEASE_0_1_0.md)。运行 `python -m ashare_mvp.release_check --out artifacts/release_check` 可在离线环境生成并验证基准模型、可选指标模型和持仓日报；所有输入明确为合成数据。

## 云端版本管理与换电脑恢复

见[完整迁移教程与当前边界](docs/MIGRATION.md)。公开代码继续保存在`feature/quant-mvp`；独立私有仓库[agent_quant_2-backups](https://github.com/AustinYI111/agent_quant_2-backups)保存加密Release快照，数据库不反复提交普通Git。API凭据和备份密码不上传。

新电脑先运行`setup.ps1`，登录GitHub，再运行`scripts/restore-cloud.ps1`。用`scripts/check-environment.ps1`验证模型后可直接预测，不重训。当前可恢复模型为合成候选验证模型，真实全市场模型与自动日更尚未完成，完整状态及命令请按教程执行。

### 选股池范围

真实选股限定沪深 A 股，排除北交所股票，并按每个历史选股日的 ST／*ST 状态剔除风险警示股。保留原始历史记录供时点校验，不用当前 ST 名单回填历史。该规则用于训练样本与预测候选池，不强行将无法卖出的已有持仓从回测账本删除。

行情与资产负债表采集对短暂网络错误最多尝试两次，间隔 1 秒；HTTP 403、429、带 `Retry-After` 的响应，以及字段、身份、业务校验错误不自动重复请求。断点续跑仍校验已有原始文件的哈希。重试策略写入采集 manifest，重试耗尽后保留失败状态，不将缺失数据标记为完成。

### 公司行动回测入口

标准 `run` 可同时指定 `--raw-prices raw_prices.csv --corporate-actions actions.csv`，将独立新买入收益标签与原始股数账户模拟用于同一次滚动运行。两份输入必须同时提供；运行按 asof 裁剪未来记录，并保存输入、哈希及模型收益口径。现金分红成为应收权益，支付后进入账户现金，等待再平衡；除权后卖出不取消既有权益，除权日新买入不继承此前分红。仍为税前分数股研究模拟，送转股可交易日、退市结算与原始事件版本需独立验证。

在确认来源的参考价报价精度后，可给公司行动运行添加 `--reference-tick-size 0.01`。该显式设置接受精确理论价或按指定单位四舍五入的参考报价，并写入模型元数据；它不四舍五入分红现金或持仓财富，也不补造缺失公司行动。默认保持原有校验，超出指定报价精度能解释的差异仍会拒绝。

### 同一发行人更换证券代码

`config/same_issuer_code_changes.json` 保存附公告链接的等股数代码变更记录。行情身份适配器保留 `trading_symbol`，按生效日衔接稳定发行人键，并把失效代码报价单独隔离；不会把代码更换当作资产归零。事件键冲突、公告晚于生效和非等股数交换会拒绝。当前已在真实行情审计验证 000022→001872、000043→001914；标准运行现可用 `--code-changes config/same_issuer_code_changes.json` 同步处理报价和财务身份，并在排名与回测订单中保留 `trading_symbol`。模型恢复会使用保存的映射配置。个人持仓估值与日报已接入该映射；实际持仓仍等用户填写。公开比率探索入口的身份衔接和完整退市结算尚未完成。

日报命令同样可传 `--code-changes config/same_issuer_code_changes.json`。持仓 CSV 仍使用 `symbol,shares,average_cost` 三列；输出保留 `input_symbol`、稳定发行人键 `symbol` 和当日 `trading_symbol`。同一发行人的新旧代码重复填报会拒绝，不自动合并成本。持仓及日报应放在忽略的 `private/` 目录。

### 公告依据与参考除权参数

公司行动运行可添加 `--reference-overrides config/reviewed_reference_cash.json`，加载经核对的参考除息金额。证据必须包含公告日期、可用日期、原文 URL、PDF 哈希、页码及预期实际派息；现金冲突、时点不合规或未复核的记录会拒绝。该功能补充 `reference_cash_per_share`，也可用成对的 `reference_share_multiplier`／`expected_share_multiplier` 记录官方虚拟送转倍数；真实 `cash_per_share` 和 `share_multiplier` 保持原权益，证据与哈希保存到运行目录。当前累计有 15 条已核对记录，不能视为剩余公司行动问题已解决。


## 公开 PE/PB 因子与分红事件账本

公开比率数据可封存为事件价格研究数据，不需要虚构市值、绝对利润或净资产：

```powershell
python -m ashare_mvp.cli prepare-public-events --data artifacts/public_ratios --raw-prices artifacts/raw_prices.csv --corporate-actions artifacts/corporate_actions.csv --reference-tick-size 0.01 --out artifacts/public_events
python -m ashare_mvp.cli run-public --data artifacts/public_events --out artifacts/public_event_run --exploratory
```

输入公开比率目录须先通过 `convert-public` 的哈希与覆盖检查；原始行情必须与该目录的股票、日期、原始开收盘价和交易状态一致。分红事件字段沿用标准事件流程。未解释的除权价格跳变或缺失估值会阻止封存；不通过删股票解决。仅当原始报价采用分位精度时显式指定 `--reference-tick-size 0.01`。

该目录额外封存 `event_raw_prices.csv` 与 `event_corporate_actions.csv` 及其哈希。训练标签使用新买入者的实际权益，回测使用原始股份和分红应收款／现金账本；月中 `--asof` 只发布当前评分，不当作月末调仓。模型元数据记录收益口径，恢复预测拒绝混用供应商复权价与事件价格数据。备份时必须保留整个数据目录。

这仍是未认证的公开数据研究：原始财报版本、公司行动完整性、退市结算、交易限制、分红税和股份到账可交易时间仍需核实。当前全市场行情／财务汇总目录到该格式的转换还未完成；上述能力不等同于已完成真实全市场训练或盈利验证。


## 全市场汇总数据转换

`prepare-market-ratios` 将已采集的全市场原始行情、财务依赖面板、证券身份快照和分红对账结果接入同一 PE/PB 因子／事件账本流程：

```powershell
python -m ashare_mvp.cli prepare-market-ratios --market artifacts/market_history_2018_2024 --financials artifacts/financial_dependencies_progress4 --master artifacts/master_union1 --actions artifacts/dividend_payment_reconciliation_transfer1 --out artifacts/market_ratio_dataset --audit-out artifacts/market_ratio_readiness --reference-tick-size 0.01 --code-changes config/same_issuer_code_changes.json --reference-overrides config/reviewed_reference_cash.json
python -m ashare_mvp.cli run-public --data artifacts/market_ratio_dataset --out artifacts/market_ratio_run --exploratory
```

每次运行使用空的新输出目录，数据目录和审计目录必须彼此独立。输入目录名是示例，采用实际最新归一化／对账结果；不会自动把旧财务进度目录升级成完整数据。

命令先封存输入 manifest 和证据哈希。若交易日文件、证券身份或已实施分红的支付证据有缺口，退出码为 1，仅在审计目录输出 `readiness.json` 和 `blocking_issues.csv`，不生成可训练数据。未实施的分红计划不产生现金／股份权益，也不作为执行证据缺口。来源清单阶段尚未逐一验证行情文件哈希，不应将这一阶段通过的字段检查等同于行情数据完整性。

清单完整后，继续校验逐日原始行情哈希、行数、上市日以来完整估值、历史 ST 状态、代码变更和价格跳变，再重建事件收益。缺失财务比率保留原生 NaN，不伪造绝对市值、利润或净资产；未来依赖日期会被拒绝。代码变更使用审阅证据中的稳定发行人身份，排序结果同时保留实际交易代码。北交所不进入数据选股池，ST 历史行情保留供账户估值。

输出即使为 `prepared_exploratory`，`certified` 和 `model_ready` 仍为 false：它只表示可运行探索研究，不证明原始财报版本、完整市场覆盖、退市结算或真实盈利。真实全市场转换尚未成功，必须先处理审计缺口再运行第二条命令。


### IPO 申购代码与历史上市范围

全市场目标清单可能混入申购、暂缓发行或回测区间之后才首次上市的代码。转换命令可附加 `--non-listing-evidence config/reviewed_non_listing_bounds.json`，使用已人工核实的首次上市日期或官方 IPO 阶段记录，限定其在历史区间内不应有上市行情。已核实的 7 个深市代码见配置；3 个沪市代码仍未解除身份缺口。

证据必须具有明确日期边界、官方事实定位、来源 URL、内容哈希和审阅标记。空 API 响应、未审阅记录、未覆盖回测末日的旧证据均不能解除身份缺口；已知上市日期或真实历史行情与边界冲突时会拒绝转换，不删除报价。该证据仅用于重建历史上市范围，不作为历史可见的预测因子或交易信号，也不把暂缓 IPO 错填为退市／零回收股票。

2026-10-15 官方核对资料包括：[巨潮 IPO 阶段](https://www.cninfo.com.cn/eipo/index.html?stockid=301660)、[粤芯半导体首次上市公告](https://www.szse.cn/www/disclosure/notice/company/t20261012_623256.html)、[联亚药业首次上市公告](https://www.szse.cn/certificate/secondb/GEMdynamice/t20261015_623313.html)。原始网页／JSON 与查询脚本缓存保存在本地 `artifacts/official_ipo_scope_20261015`；Git 只提交结构化事实及来源哈希。官方接口返回空数据的其余代码仍须单独核实。


### 官方实施公告与特别股息

`reconcile-dividends` 可接入已审阅的实施公告事实：

```powershell
python -m ashare_mvp.cli reconcile-dividends --market artifacts/market_dividend_history_2017_2023 --payments artifacts/cninfo_complete_normalized_transfer1 --calendar artifacts/market_history_2018_2024/calendar.csv --out artifacts/dividend_payment_reconciliation_reviewed --reviewed-events config/reviewed_distributions.json --code-changes config/same_issuer_code_changes.json
```

全市场准备时将 `--actions` 指向该新对账目录。不要覆盖旧缓存或直接修改供应商原始行。审阅记录必须准确匹配证券、报告期、执行日、原现金／股份比例及完整原条款，并附公告日期、可用日期、实际支付日期、官方 URL、PDF 哈希和页码；未审阅、晚于执行才可见、来源变化或与独立支付记录冲突时拒绝处理。

首批核实 3 条：南威软件 2018 年支付日期补齐；青岛啤酒 2023 年每股普通 1.30 元加特别 0.50 元，合计 1.80 元；兖矿能源 2023 年每股普通 3.07 元加特别 1.23 元，合计 4.30 元，并送股 0.5 股。数值均为税前研究口径。汇总接口漏掉特别股息时不能按普通股息回测，也不能用价格反推金额。

新事件状态 `matched_reviewed_notice` 与普通来源匹配分开统计，事件文件保留来源 URL／哈希／页码、普通及特别股息，审阅配置也封存并计算哈希。对账覆盖表新增内容哈希。官方原 PDF、文本和核对渲染页仅保存在本地 `artifacts/official_payment_notices_20261015`，需随下一次私有快照备份。实际本轮支持范围内事件缺口 25→22；同一旧行情片段的价格差异 586→585，不能把该旧片段当成已经完成的全市场回测。


第二批继续核实同类漏项，累计审阅 6 条实施记录：兖矿能源（原兖州煤业）2021 年每股普通 0.60 元加特别 0.40 元，合计 1.00 元；2022 年普通 1.60 元加特别 0.40 元，合计 2.00 元；中海油服 2022 年普通 0.02 元加特殊 0.13 元，合计 0.15 元。金额、支付日和来源哈希已逐条与真实导出 CSV 对照；支持范围内分红证据缺口累计 25→19，仍未达到完整训练条件。

第二批原始 PDF 与核对渲染页保存在本地 `artifacts/official_payment_notices_batch2_20261015`；更新后的真实对账目录为 `artifacts/dividend_payment_reconciliation_reviewed2`。复制／恢复研究数据时应保留对应版本的源资料和配置，不能用旧普通股息文件替代新合计现金事件。


第三批核实中国石油 5 条 2018—2020 年支付记录，累计审阅 11 条，支持范围内分红证据缺口 25→14。部分供应商金额已经包含特别派息，本批保持原合计金额，只记录普通／特别构成和公告支付证据；不会再额外加一遍特别现金。新增回归覆盖这一重复计息风险。真实导出的全部 11 条金额、股份比例、支付日和来源哈希已核对。

本批原 PDF 和渲染页在本地 `artifacts/official_petrochina_notices_20261015`，对账目录更新为 `artifacts/dividend_payment_reconciliation_reviewed3`。同一封存旧行情片段的未解释价格调整进一步从 585→580；仍有价格、身份及覆盖缺口，未完成真实模型训练。

2026-10-15 行情连接诊断：官方主机 `public-api.baostock.com` DNS 可解析，但当前网络到 TCP 10030 连接被拒绝，隔离登录返回 10002007。行情缓存保持 1,089 日。不要把此错误当成因子转换问题或反复启动多个采集进程；接口恢复后再用原 `download-market --resume` 命令续跑。财务／公告来源是独立流程，其可用性分别检查。


第四批核实华能水电 2019／2020 年公众股东的差异化分红：实际每股现金为 0.178／0.18 元，官方除息参考扣减为 0.15022／0.15 元；原大股东的每股金额又不同。研究账户采用公众股东权益，除息校验采用独立的参考扣减，不能用平均扣减额作为实际到账现金。

2020 年独立归一化来源的旧值 0.15 元有明确审阅锚点：只有 `independent_conflict_reviewed=true`、`payout_scope=public_a_share` 且旧金额／股份比例／支付日均准确匹配时才允许改为公告中的公众股东金额。旧值保存在事件输出，缺失旧事件或后来来源变化会拒绝处理；其他未审阅冲突继续失败。原供应商文件不改写，旧 `0.15` 与新 `0.18` 的意义不会混为一谈。

累计审阅 13 条实施事件、3 条参考现金记录；当前支持范围内事件缺口 25→12，同一历史片段价格差异 586→578。最新本地对账为 `artifacts/dividend_payment_reconciliation_reviewed4`；两份原始公告及完整核对页在 `artifacts/official_huaneng_notices_20261015`，待后续私有快照备份。完整真实数据与训练仍未完成。


财务快照处理修复：如果某证券的后续采集失败，旧 legacy 原始响应仍保留并校验哈希／身份／分页；由于失败状态没有“完整记录数”，不会再把它误报为完整请求总数不一致。未完成证券仍列入 `rejected_securities.csv`，不使用其缓存来冒充可用财务记录；完整证券的总数不一致和任何缓存哈希变化仍会失败。此修复不恢复原始财报版本，也不把不完整来源标为完整。


### 第一阶段最新实测（2026-10-16）

逐日流式连接已实际核验 1,089 个行情文件并处理 4,304,493 行，连接新版财务依赖面板；相关 53 项测试通过。累计审阅 31 条实施公告、15 条参考金额及比例证据。行情接口已恢复并断点续采，但当前封存检查仍有日行情、身份、事件及价格核对缺口，尚未运行真实全市场训练与样本外回测。具体范围、哈希和剩余工作见[本次数据报告](docs/reports/PHASE1_DATA_20261016.md)。开发优先保持第一阶段，不扩展后续阶段功能。


纯转增事件允许 `cash_per_share=0` 且 `pay_date` 为空，保留 `share_credit_date` 与 `stock_market_date`；正现金分红仍要求支付日期。审阅配置的原始现金锚点为空时，只能用于有官方证据的零现金转增，不能与后续数字零混同。两条真实转增已接入，新代码回填的六条历史分红已用正式代码变更与逐条支付公告恢复。准备检查仍将区间内未解析事件列为阻塞，不据代码前缀排除冒充完整。


分红对账现支持 `--code-changes config/same_issuer_code_changes.json`。供应商最新代码与官方历史代码分别保留，数据准备要求事件与特征使用同一封存身份策略；新旧代码重复事件拒绝。当前包含 300114→302132 的正式变更证据。最新实测、字段含义与仍待修复的财务依赖连接见[历史证券代码与分红连接](docs/reports/EVENT_IDENTITY_20261016.md)。


财务依赖连接也可使用 `--code-changes config/same_issuer_code_changes.json`：先统一同一发行人，再按报告期和可见日期连接。完全一致的跨别名版本显式合并并保存全来源审计，字段冲突或同代码重复仍拒绝。真实相同输入已恢复 23 条负债率，完整 277 项测试通过；见[财务身份连接实测](docs/reports/FINANCIAL_IDENTITY_20261016.md)。

```powershell
python -m ashare_mvp.cli combine-financials --earnings artifacts/financial_panel_complete --balances artifacts/balance_normalized_progress5 --code-changes config/same_issuer_code_changes.json --out artifacts/financial_dependencies_identity1
```

以上目录是已完成本地实测的版本名，复用时采用新的空输出目录。准备训练数据时，财务、分红与特征须使用同一封存身份策略；新版本不自动替代旧快照。


差异化送转的官方参考倍数已接入，三条真实案例保持全部主事件的实际现金／股数不变，并解释对应价格差异。完整 286 项测试通过；新财务快照已扩至 5,216 个证券，仍未完成真实全市场训练。范围、来源分类与剩余工作见[参考比例与数据覆盖检查](docs/reports/REFERENCE_SHARES_20261016.md)。

历史分红可用 `merge-dividend-events` 以封存来源补齐，重复权益只计一次，冲突拒绝且保留原计划缺口。1,479 个请求交易日现已采集齐全；真实训练仍待数据核验。使用方式和边界见[历史分红与全量行情进展](docs/reports/HISTORICAL_DISTRIBUTION_UNION_20261016.md)。

公开分红接口的同日重复版本已支持保留原始行和一致性规范化；真实 573 条原始记录保留 563 条事件，8 条重复只计一次，2 条冲突隔离且不进入主事件库。规则与证据见[分红版本处理](docs/reports/DISTRIBUTION_VERSIONS_20261016.md)。

正式实施公告可通过原始文件哈希及全部冲突行锚点解决历史分红版本差异；本轮已实际新增 300 条事件并验证原有现金／股数不变。未解释调整在同一旧行情片段从 560 降至 321，仍待全量核验。命令、证据及边界见[公告审阅与历史事件合并](docs/reports/REVIEWED_HISTORICAL_EVENTS_20261016.md)。
