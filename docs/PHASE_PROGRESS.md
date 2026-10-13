# 四阶段研究系统：当前证据与未完成项

本轮继续保持完整产品目标：自主筛选全市场、基本面与估值研究、中长期排序、买卖观察区间、财报迭代和个人持仓日报。不能以合成样本替代真实A股完成证据。

## 本轮实际进展

本轮最终全套测试53项通过，日报命令实际运行成功；前轮旧模块5项离线回归通过。新增测试覆盖公开源失败留档、分页截断、原始报价、财务/技术未来扰动、预测台账幂等及不可覆盖、持仓字段校验、同配置因子对照。测试证明上述工程行为，不能替代真实全市场数据验证。

| 目标要求 | 当前实现与证据 | 尚缺的完成证据 |
|---|---|---|
| 历史A股股票池 | BaoStock公开下载器，历史日期证券列表及证券生命周期主表入口，查询隔离硬超时 | 两个历史股票池查询与证券主表实际失败；缺北交所覆盖及可靠历史退市处理 |
| 真实行情/财务 | 浦发银行2024年1月22条原始日线、22条调整日线；2023年四季度财务指标实际取得 | 尚未转换为经过对账的模型数据；公告原始/修订版本、缺字段处理、总回报价格方法需验证 |
| LightGBM排序与样本外回测 | 原滚动模型已实现并运行；同期限因子对照新增并运行 | 目前模型验证仍使用合成数据，真实历史全市场样本外回测未完成 |
| 欧奈尔、趋势、量价及价位 | 技术观察模块输出20/50/200日均线、成交量扩张、回调/突破/支撑观察区间 | 真实行情上的完整窗口及参考条件收益评估未完成；区间不是预测置信区间 |
| 自动解读财报与行业 | 按可用日期生成结构化财务事实、上一报告变化及有时点字段的行业同业比较；公开行业数据5558条实际取得 | 尚无原始年报正文、审计意见自动抽取；当前行业文件更新日为2026-10-12，不能回填历史行业 |
| 扩展理论因子并测试 | 注册式因子库可传入run；同一6个月标签/费用/窗口下对照默认与欧奈尔特征 | 真实样本外因子效果、统计检验与独立验证集尚缺 |
| 手动个人持仓及日报 | CSV模板、原始报价估值、不可覆盖预测台账、成熟3/6/12月真实结果记录、中文Markdown日报均实现并测试 | 用户尚未录入实际持仓；日报当前演示为明确标注的虚构证券；没有自动获取每日新数据的完整调度 |
| 远程发布 | 首轮本地提交09cddfe保留，后续源代码继续在功能分支开发 | 本机仍无Git凭据；连接器写API此前403，尚未推送成功 |

## 公开数据的实测边界

使用BaoStock 0.9.4；接口依据[SDK文档](https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md)。原始数据位于artifacts/public_probe3/，manifest.json保存每项查询状态、文件字段和SHA256，readiness_audit.json保存缺失值及日期核对。

交易日历、单证券基础资料、原始/调整价格、profit/growth/balance季度指标和当前行业成功返回。证券生命周期总表及2024-01-02、2024-01-31股票池查询失败，整体状态明确为partial，training_ready=false。SDK的接收循环可能停滞，现每次查询隔离到独立进程并设硬截止时间；分页在完整页异常结束时拒绝悄悄接受截断结果。

浦发银行2023Q3数据公告日期为2023-10-28，报告期为2023-09-30；12份季度财务文件公告/报告日期校验均无异常，哈希核对无变化。但部分MBRevenue等字段为空，不能以利润增长率冒充收入增长率。当前行业5558条中334条行业为空。拥有公告日期并不能证明数值保存了历史公告版本，因此不能宣称已消除财报修订回填偏差。

## 同期限因子对照：仅合成工程实验

两组均为6个月标签、60个月滚动窗口、默认费用，每组40期月度Top20；评价起点2021-10-18，终点2023-10-18。

| 持有期 | 配对完整组合 | 加欧奈尔后的平均毛收益变化 |
|---|---:|---:|
| 3个月 | 21 | +0.137个百分点 |
| 6个月 | 18 | -0.120个百分点 |
| 12个月 | 12 | +0.002个百分点 |

该数据包含人为信号；组合重叠，没有统计显著性推断或自动晋升因子。不同期限表现不一致，不能据此宣称欧奈尔因子有效。

## 新增入口

```powershell
python -m pip install -e '.[test,public]'
python -m ashare_mvp.cli download-public --start 2024-01-02 --end 2024-01-31 --symbols sh.600000 --out artifacts/public_sample
python -m ashare_mvp.cli audit-public --data artifacts/public_sample
python -m ashare_mvp.cli compare --data artifacts/demo_data --out artifacts/factor_comparison --horizon 6
python -m ashare_mvp.cli report --data artifacts/demo_data --predictions artifacts/demo_run/latest_top20.csv --holdings examples/holdings_template.csv --asof 2023-10-18 --out artifacts/report --ledger artifacts/history.csv
```

`download-public`默认只取5个证券样本，`--all-symbols`选择历史并集，但当前历史证券列表故障仍需修复。原始公开数据目录不能直接输入run，需要先完成转换与时点/执行状态对账；不会自动声明为无泄漏训练集。

持仓CSV字段为symbol,shares,average_cost，实际价格估值需要prices.csv的可选raw_close字段。预测台账一套模型/数据一份；相同日期/股票/持有期重复写入是幂等的，改变已记录分数或混合真假数据会拒绝。历史日报不能覆盖包含未来预测的台账。成本基准由用户提供，当前没有现金余额、已实现损益或分红流水，不能当作完整券商账户对账。

## 下一项优先工作

修复历史全市场股票池获取并交叉核对；补齐财务字段来源和时点版本证据，建立明确的真实数据转换与质量门槛。随后运行真实横截面排序与保留验证期的回测，再验证技术观察条件与扩展因子，并接入实际持仓每日数据更新。四阶段整体目标仍未完成。

## 后续历史股票池验证

已将证券元数据查询改为每页500条，并修复Windows进程间大表传输：schema与100行分块发送，保留硬截止时间。真实重试取得2024-01-02的6529条证券记录、2024-01-31的6545条证券记录；剔除指数等后分别为5095只、5103只沪深A股，合并共5105个不同证券。真实查询记录和哈希位于artifacts/universe_chunked/；构建产物位于artifacts/verified_universe1/。

新增`build-universe --data <公开采集目录> --out <新目录>`，逐文件验证哈希、唯一证券键及交易状态，保留停牌成员，不将缺失日期自动前向填充。若有完整生命周期主表，会对照上市/退市日期与历史快照，输出差异。主表实测疑似分页截断，因此拒绝接受，未报告已经完成生命周期对账。北交所和完整每日历史覆盖仍待接入。

新增股票池逻辑测试包含历史退市、上市前排除、快照差异、生命周期倒置、停牌保留、文件篡改拒绝。只证明这些边界和已采集快照，不能据此宣称真实全市场中长期回测完成。

## 公开数据转换与批量行情采集

新增`convert-public`：对原始文件哈希、日历覆盖、股票代码和财务日期进行检查，直接使用历史PE/PB倒数作为估值比率，不伪造缺失的总市值或账面权益。收入增长只能由同报告期上一年收入计算，不用利润增长替代；缺失值保留。财务可用日期取所有依赖发布日期之后的下一个已提供交易日，输入窗口之前的旧财报保守从窗口首日开始可用。转换后的行情、财务和日历再次以哈希封存。

浦发银行22个实际历史交易日与3份窗口内可见财报已成功转换，产物位于artifacts/public_normalized1/。由于仅一个月行情，不能训练中长期排序。财报原始/修订版本和调整价格的总回报方法仍未验证，metadata明确certified=false；不得把该结果当作已验证的无泄漏数据。

新增`run-public --exploratory`专用研究入口，默认拒绝未明确启用探索模式的公开数据。使用同一滚动训练、标签成熟日清除和次交易日调仓逻辑，输出缺失特征覆盖率、训练审计和结果；探索运行不声明财报原始版本、实盘可成交性或完整全市场范围。实际LightGBM端到端测试目前使用明确标记的合成公共格式夹具，而非真实市场收益证据。

已实际验证BaoStock 0.9.4的`query_daily_history_k_AStock`批量接口：2018-01-03返回3471条日线，2024-01-31返回5105条日线，均记录字段和哈希，位于artifacts/daily_market_probe/。这允许以交易日而非逐证券采集市场截面。

新增`download-market`逐日原始数据采集：原子保存每个文件和进度、验证已缓存文件哈希、参数一致的断点恢复、错误留档。后续运行连续3次查询失败会停止并保留缓存，避免无限失败请求。2018-01-02至2024-01-31的1479个交易日采集已启动，产物位于artifacts/market_history_2018_2024/；必须检查进程会话及manifest的实际进度，不能仅凭running字样认定仍活跃，也不能在会话存活时重启同一任务。

```powershell
python -m ashare_mvp.cli convert-public --data artifacts/public_sample --out artifacts/public_prepared
python -m ashare_mvp.cli run-public --data artifacts/public_prepared --out artifacts/public_research --horizon 6 --exploratory
python -m ashare_mvp.cli download-market --start 2018-01-02 --end 2024-01-31 --out artifacts/market_history
# 仅在已确认旧采集进程结束后恢复同一任务
python -m ashare_mvp.cli download-market --start 2018-01-02 --end 2024-01-31 --out artifacts/market_history --resume
```

全量原始行情仍在采集，批量财务及原始版本验证、原始价格的公司行动与退市清算、全市场真实样本外训练仍未完成。本轮全套64项测试通过；随后补充的采集停止条件另外验证。真实源采集与合成夹具验证分别报告，不能混称。

## 全市场季度财报与时点连接

按[AKShare官方源码中的公开请求结构](https://raw.githubusercontent.com/akfamily/akshare/main/akshare/stock_feature/stock_yjbb_em.py)实测季度报表接口。2023-09-30返回5936条、12页；原始首条记录公告日期为2023-10-27，但UPDATE_DATE已到2026-08-06。不能直接将当前返回值视为2023年的原始财报版本。

新增`download-financials`，完整核对总页数、总行数、重复证券/报告期、缓存文件哈希，保存逐页JSON与原始records.csv，并可断点恢复。2017Q1至2023Q4共28期采集已启动，产物位于artifacts/financial_history_2017_2023/。连续三期失败后停止而非持续失败请求。

新增`normalize-financials`保留公告、更新及入库日期，按其最晚日期的下一交易日作为可用上界；百分比按官方单位转换为小数，季度收益不冒充TTM收益。本接口不提供资产负债率，明确保留缺失，不由其他指标拼造。每次规范化封存输入manifest快照和输出哈希，后续采集追加不会改变已有产物的证据边界。

新增`align-market`，按证券与可用日向后连接，只使用当天及之前记录；晚到的旧报告修订不能覆盖已可见的新报告。使用真实采集数据运行连接，输出205961条、3508个价格代码，行情范围2018-01-02至2018-04-10；这次财报快照最早可用日期为2019-04-30，所以2018年连接结果没有财报记录。该结果是时间门槛生效的证据，不是财报覆盖完成或模型准备就绪。原始版本证据仍未解决。

```powershell
python -m ashare_mvp.cli download-financials --start 2017-03-31 --end 2023-12-31 --out artifacts/financial_history
python -m ashare_mvp.cli normalize-financials --data artifacts/financial_history --calendar artifacts/market_history/calendar.csv --out artifacts/financial_panel
python -m ashare_mvp.cli align-market --market artifacts/market_history --financials artifacts/financial_panel --out artifacts/aligned
```

本轮全套71项测试通过，时点连接与新日期校验相关6项另行通过。采集任务仍需依据实际会话与进度核验。用户选择稍后填写真实持仓；不能把空模板当作无持仓确认。整体目标仍包括真实训练/回测、原始财报版本核验、公司行动与退市处理及真实持仓日报，未以这轮数据接入代替完成。

## 财报完整采集与分红收益核验

2017Q1—2023Q4的28期财报采集进程已成功结束，原始记录218850条。完整规范化产物覆盖5666个可识别证券代码，106819条记录在给定交易日历中具备可用时点；205395条记录的更新时间晚于公告日。此代码数不是已验证的可投资股票池数量。原始历史版本仍未认证，不能将当前修订数据回填公告日用于历史训练。

新增`corporate_actions.economic_return_prices`：从原始价格和已知公司行动构造税前经济收益指数，将除息日应收股息与到账现金分开，到账后的下一可交易日才再投资；核对前收盘与除权除息理论价格，遇到缺失事件、配股外部资金、行情缺口或未解决的退市终值拒绝计算。该函数尚未接入完整市场训练流程，也不等同于含税费、红股可交易日及退市结算的真实账户账本。

实际抓取浦发银行2018-07-11至2018-07-20共8个交易日原始行情，核对2018-07-13每股0.1元现金分红：除息日原始开盘9.57元，含应收股息的经济开盘9.67元。产物位于artifacts/real_dividend_reconciliation/；这里只验证现金分红样本，不据此认证全市场收益。最新全套77项离线测试通过（40.88秒），涵盖分红、拆股、缺失事件、时点和未来价格扰动。

历史行情采集仍在运行。Git凭据管理器当前没有列出已登录账户，远程推送尚未完成。持仓按用户“稍后填写”处理，保留录入入口，不生成个人持仓建议。

新增可重跑入口`reconcile-prices`，封存原始价格、事件和交易日历的输入字节及哈希，导出economic_prices.csv及限制说明。给定日历定义核验区间，不能按最后一条价格自动截短而掩盖停牌或退市缺口。输入code列可转换为symbol，但不会将未知事件版本认证为历史真实版本，输出仍标记model_ready=false。独立交易日历截取2018-07-11至2018-07-20后，真实现金分红样本端到端运行通过，结果位于artifacts/dividend_cli_exchange_verified/。

```powershell
python -m ashare_mvp.cli reconcile-prices --prices raw_prices.csv --actions actions.csv --calendar exchange_calendar.csv --out reconciled_prices
```

该命令是模型数据准备中的收益核对步骤，尚未完成全市场公司行动采集及向训练数据转换。完整日历、分红事件及退市终值都仍须分别取得证据，不允许由已存在价格行推导日历后宣称完整覆盖。

本轮完整78项离线测试通过（38.61秒），包括证据封存与交易日缺口拒绝的文件级流程测试。
