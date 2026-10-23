# Personal Quant Research

个人 A 股选股研究系统：用基本面、估值和动量因子训练 LightGBM，输出股票排名、Top 20 和回测报告。

**当前交付为历史研究 MVP，研究数据截至 2025-12-31。2026 年日更与模拟跟踪正在开发，尚未验证实时使用和实盘适用性。回测结果不代表未来盈利。**

## 能做什么

- **选股**：沪深 A 股，按选股日排除 ST／*ST 和北交所股票，支持扩展因子。
- **验证**：5 交易日、20 交易日、12 个月三周期滚动样本外回测，检查数据可用时点与未来信息泄漏。
- **留存**：保存研究模型、历史排名和报告；恢复已备份模型无需重新训练。

历史 Top 20 截至 **2025-12-31**，不是当前买入名单。财报历史版本、部分估值和成交假设仍有限制，详见[真实研究结果与限制](docs/reports/REAL_MODELS_AND_VALUATION_REPLAY_20261020.md)。

## 开始使用（Windows）

已安装 Git 时，在 PowerShell 执行：

```powershell
git clone --branch main https://github.com/AustinYI111/agent_quant_2.git
cd agent_quant_2
.\setup.ps1
```

没有 Git：点击 **Code → Download ZIP**，解压后在项目文件夹运行 `setup.ps1`。脚本安装依赖并运行测试；干净新电脑上的完整安装流程尚未实测。

接下来按用途进入：

| 你要做什么 | 操作说明 |
| --- | --- |
| 换电脑，恢复已有数据和模型 | [历史 MVP](docs/MIGRATION.md)／[第二阶段](docs/PHASE2_MIGRATION.md)：安装 → 登录 GitHub → 输入备份密码 → 检查恢复结果 |
| 加载模型进行预测 | [模型使用说明](docs/HORIZON_MODEL_PORTABILITY.md) |
| 采集数据、训练、运行回测 | [运行指南](docs/MVP.md) |
| 查看后续开发进度 | [第二阶段进度与验证协议](docs/PHASE2_RESEARCH_PROTOCOL.md) |

恢复需要**单独保管的备份密码**，仅登录 GitHub 无法解密。第二阶段已验证两层恢复、93 个模型重载及最新密文完整重下载；基础备份的全量重下载、干净新电脑安装仍未实测。

## 文件保存在哪里

| 位置 | 内容 |
| --- | --- |
| 本仓库 | 代码、配置、依赖、测试、文档与轻量报告 |
| [私有备份仓库的 Releases](https://github.com/AustinYI111/agent_quant_2-backups/releases) | 已封存的加密数据、研究模型和结果快照 |
| 本地忽略目录 | 原始数据、运行结果、个人持仓及尚未备份的新文件 |

数据库不提交普通 Git；密码、Token、API Key 和个人持仓不上传公开仓库。公开 Releases 尚未发布经批准的正式模型。

`main` 保存经确认的研究版本；开发在 `feature/quant-mvp` 上进行，测试后推送，通过 PR 由仓库所有者确认合并。
