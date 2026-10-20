# Personal Quant Research

A 股多因子选股研究系统：采集数据 → 计算因子 → LightGBM 排序 → 滚动回测 → 输出 Top 20 与研究报告。

**当前版本是历史研究 MVP，数据截至 2025-12-31。实时日更与实盘适用性尚未验证；回测和测试通过不代表未来盈利。**

## 现在能做什么

| 已完成 | 第二阶段开发中 |
| --- | --- |
| 2018—2025 年真实历史数据研究流程 | 2026 年数据补齐与持续更新 |
| 5 交易日、20 交易日、12 个月三周期模型与滚动样本外回测 | 冻结模型每日排名、预测留档与模拟跟踪 |
| 历史 Top 20、模型保存、备份及本地恢复验证 | 基准归因、行业强弱、市场状态与箱体指标增量检验 |

选股限定沪深 A 股，按选股日排除 ST／*ST 和北交所股票；无法卖出的历史持仓仍保留估值。基本面、估值和动量为基础因子，欧奈尔相关指标可选。

历史样本外区间 **2023-02-01—2025-12-31**，三周期累计净收益分别为 **−13.80%、+40.37%、+27.32%**，最大回撤均超过 33%。历史 Top 20 截至 **2025-12-31**，不是当前买入名单。数据范围、成本与基准口径见[真实研究报告](docs/reports/REAL_MODELS_AND_VALUATION_REPLAY_20261020.md)。

## 第一次使用（Windows）

下载代码，在项目文件夹打开 PowerShell：

```powershell
git clone --branch main https://github.com/AustinYI111/agent_quant_2.git
cd agent_quant_2
.\setup.ps1
```

未安装 Git 时，可在 GitHub 点击 **Code → Download ZIP**，解压后运行 `setup.ps1`。脚本安装 Python 3.12 环境、锁定依赖并运行测试；全新 Windows 的完整安装过程尚未实测。

想先检查软件能否运行，可使用合成示例，无须下载历史数据库：

```powershell
.\.venv\Scripts\python.exe -m ashare_mvp.release_check --out artifacts/release_check
```

**示例输出使用合成数据，不是真实选股。** 已有真实数据和模型需按下面步骤恢复。

## 换电脑：恢复数据和模型，不必重训

先运行 `setup.ps1`，再登录 GitHub、下载并解密私有备份：

```powershell
git credential-manager github login --username AustinYI111 --browser
.\scripts\restore-cloud.ps1 -Tag backup-real-research-20261020 -Asset pqr-real-research-20261020.pqr -Out restored/real-20261020
.\.venv\Scripts\python.exe scripts/check_saved_research_models.py --root restored/real-20261020 --out artifacts/restored_saved_model_check
```

恢复时需输入**单独保管的备份密码**，仅登录 GitHub 无法解密。目标与检查输出目录须为空或不存在；不要覆盖已有运行。

已验证同哈希本地备份的完整恢复、9 个模型加载及历史预测一致性；完整云端重下载与全新电脑安装尚未验证。直接加载模型预测、后续备份与失败处理见[完整迁移教程](docs/MIGRATION.md)。

## 代码、模型和数据存在哪里

| 位置 | 保存内容 |
| --- | --- |
| 本仓库 | 源码、配置、依赖、测试、文档与轻量报告 |
| [私有备份仓库](https://github.com/AustinYI111/agent_quant_2-backups)的 Releases | 已封存的加密数据、研究模型和结果快照 |
| 本地 `artifacts/`、`private/` 等忽略目录 | 原始数据、运行结果、持仓及尚未备份的新文件 |

大型数据库不反复提交普通 Git。密码、Token、API Key 和个人持仓不进入公开仓库。公开 Releases 尚未发布经批准的正式模型；私有备份也不代表模型已正式升级。

## 详细文档

- [真实回测结果与研究边界](docs/reports/REAL_MODELS_AND_VALUATION_REPLAY_20261020.md)
- [安装、备份与跨电脑迁移](docs/MIGRATION.md)
- [模型导出、检查与直接预测](docs/HORIZON_MODEL_PORTABILITY.md)
- [数据处理与运行命令](docs/MVP.md)
- [第二阶段验证协议与当前进度](docs/PHASE2_RESEARCH_PROTOCOL.md)
- [历史研发记录](docs/reports/README_HISTORY_20261020.md)

`main` 保存经确认合并的研究版本；开发在 `feature/quant-mvp` 上进行，测试通过后推送并更新 PR，合并由仓库所有者确认。第二阶段以 2018—2025 年研究数据训练和选参，2026 年独立数据不参与选参。
