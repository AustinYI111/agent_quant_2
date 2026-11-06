# 第二阶段：换电脑恢复

先把备份密码另存到密码管理器。GitHub 登录与解密密码是两件事；仓库中不保存密码。

## 三步恢复（Windows）

1. 在准备保存项目的位置打开 PowerShell，克隆开发分支并安装环境。main 仍是经确认的第一阶段版本。

```powershell
git clone --single-branch --branch feature/quant-mvp https://github.com/AustinYI111/agent_quant_2.git
cd agent_quant_2
.\setup.ps1
```

2. 用能访问私有备份仓库的账号登录。

```powershell
git credential-manager github login --username AustinYI111 --browser
```

3. 执行固定版本恢复清单，按提示输入备份密码一次。

```powershell
.\scripts\restore-research.ps1 -Plan config/restore_plans/research_20261023_v1.json
```

脚本分别下载基础和第二阶段两个加密资产，检查私有仓库、云端大小／哈希、清单指定哈希、解密认证及全部文件哈希，再装入项目的 `artifacts/`。现有同哈希文件保留；有冲突则拒绝覆盖。不会重训，也不覆盖代码或凭据。建议预留至少 30 GB 空间，包含约 9.34 GB 数据、密文及认证恢复临时空间。

## 这次保存了什么

基础备份保留第一阶段真实研究数据和模型；新增第二阶段资产为 `backup-phase2-research-20261023-v1` / `pqr-phase2-research-20261023-v1.pqr`，948,279,235 字节。固定资产和完整校验值见[恢复清单](../config/restore_plans/research_20261023_v1.json)。

第二阶段快照包含截至 2026-10-22 的行情／财务观察历史、原始采集凭证、冻结预测输入与结果、9 批预测账本、一致备份、90 个最新历史候选拟合（`matched_candidate_models_20261022_v2`）、18 个连续排名账户及会计核验。旧失败实验与未审核原件也作为审计材料保留，不能把所有目录都当作合格模型。

快照形成之后新增的旧行业代码表、修正后的行业提取 v2 和后续实验尚未包含在此资产内，将随下一检查点备份。密码、Token、API Key、虚拟环境和真实个人持仓未包含；用户尚未录入持仓。

## 实际验证与边界

- 从 GitHub 新克隆的 Windows 项目中，认证并恢复两个同哈希本地密文：15,959 文件全部通过。恢复暴露的临时路径过长问题已经修复；使用修复提交 `6935f29` 或其后版本。
- 重载 3 个冻结模型，14,808 条分数与原预测一致；重载最新 90 个研究候选模型，抽查 1,800 条分数一致。没有重新训练。
- 9 批、44,415 条预测的原始时间和记录与一致备份完全相同。
- 新增 948 MB 第二阶段密文已从私有 GitHub 全量重新下载，哈希一致；基础 1.50 GB 资产的全量重新下载尚未验证。真实恢复使用的是与云端哈希相同的本地密文。
- 773 项回归通过。干净新 Windows 电脑的安装，以及两份资产从零下载到一次恢复的完整命令，尚未在另一台电脑实测。

[完整核验记录](reports/evidence/PHASE2_PRIVATE_RECOVERY_20261023.json)。这些结果证明本次保存和加载的一致性，不证明策略盈利、实时日更稳定或实盘适用。
