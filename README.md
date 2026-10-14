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
