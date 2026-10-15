# agent_quant_2 — A股多因子选股 MVP

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

### 公司行动回测入口

标准 `run` 可同时指定 `--raw-prices raw_prices.csv --corporate-actions actions.csv`，将独立新买入收益标签与原始股数账户模拟用于同一次滚动运行。两份输入必须同时提供；运行按 asof 裁剪未来记录，并保存输入、哈希及模型收益口径。现金分红成为应收权益，支付后进入账户现金，等待再平衡；除权后卖出不取消既有权益，除权日新买入不继承此前分红。仍为税前分数股研究模拟，送转股可交易日、退市结算与原始事件版本需独立验证。

在确认来源的参考价报价精度后，可给公司行动运行添加 `--reference-tick-size 0.01`。该显式设置接受精确理论价或按指定单位四舍五入的参考报价，并写入模型元数据；它不四舍五入分红现金或持仓财富，也不补造缺失公司行动。默认保持原有校验，超出指定报价精度能解释的差异仍会拒绝。

### 同一发行人更换证券代码

`config/same_issuer_code_changes.json` 保存附公告链接的等股数代码变更记录。行情身份适配器保留 `trading_symbol`，按生效日衔接稳定发行人键，并把失效代码报价单独隔离；不会把代码更换当作资产归零。事件键冲突、公告晚于生效和非等股数交换会拒绝。当前已在真实行情审计验证 000022→001872、000043→001914；标准运行现可用 `--code-changes config/same_issuer_code_changes.json` 同步处理报价和财务身份，并在排名与回测订单中保留 `trading_symbol`。模型恢复会使用保存的映射配置。个人持仓导入和公开比率探索入口的身份衔接仍待接入，完整退市结算也尚未解决。
