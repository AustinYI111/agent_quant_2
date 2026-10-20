# Personal Quant Research：云端管理与换电脑恢复

本轮已完成真实5／20交易日、12自然月的9个滚动候选模型和三周期账户回测。数据截至2025-12-31，不能用于声称2026年实时选股。代码、数据库和模型分开保存，恢复原模型无需重新训练；GitHub登录与备份口令仍由所有者提供。

## 版本与存储

- 公开代码：[agent_quant_2，feature/quant-mvp](https://github.com/AustinYI111/agent_quant_2/tree/feature/quant-mvp)。源代码、依赖锁、测试、脚本、轻量报告及历史Top20进入Git。main仅由用户确认合并。
- 私有数据：[agent_quant_2-backups](https://github.com/AustinYI111/agent_quant_2-backups)。本次快照放在私有Releases资产，压缩并加密，不将数据库反复提交Git。
- 本次备份：`backup-real-research-20261020` / `pqr-real-research-20261020.pqr`。其上传、大小、哈希、本地独立恢复及云端下载未完成边界见[备份与恢复审计](reports/evidence/real_cloud_restore_audit_20261020.json)。实际资产1,503,141,560字节，SHA-256 `3dc6606fb7a5fd2b728896e3945382659b318c6d38081e09ddefb965ce014ff7`；GitHub完整资产digest和大小均已验证。使用相同SHA的本地密文独立恢复2,225文件，9模型41,087条预测误差0.0，60条历史Top20一致、未重训。云端重下载实际取得67,108,864字节后因速度慢停止，**完整云端下载未验证通过**。本页由上述审计成功后发布；旧2026-10-14合成候选快照保留，不能代表本轮真实成果。
- 兼容代码提交：`ee790211ea0a60b4fa0f0b702aa75647ed930a5b`。当前后续交付提交仅更新证据与文档，运行代码兼容；以后更改代码导致模型检查失败时，应检出此版本或使用对应独立环境，不能绕过检查。
- 9个模型为**研究候选**，备份不代表正式升级。公开模型Releases尚无经过用户批准的正式模型；未自动发布、升级或合并main。

快照包含：完整2018—2025原始行情及采集凭证、整理后的真实行情／财务／日历／公司行动、相关身份／分红／准备审计、沪深300价格指数、封存代码、原始因子／标签／排序／9个模型、最终三周期净值／订单／绩效、9个可加载候选包、历史Top20与11份估值公告原文和来源。完整相对路径清单见审计的`included_paths`；`artifacts/migration_real_20261020/state.json`记录版本与实际依赖。

选择性备份不包含冗余旧实验、早期失败重放、所有历史截图及未选中的中间目录。它们仍只在旧电脑。`private/`个人持仓未选择进入本次快照；用户尚未填写实际持仓。GitHub令牌、API Key、环境密钥、备份口令和虚拟环境均不上传。未来若填写持仓，需单独明确选择私有加密快照，不能放公开Git。

## 换电脑：一次安装、恢复后直接加载

**先在旧电脑将备份口令另存到密码管理器或其他安全位置。口令不在GitHub，只有GitHub账号不能解密。丢失口令无法恢复。** 本次口令文件保存在本任务本机私有输出中，由用户自行妥善保存；不要把它提交仓库或粘贴到公开问题。

1. 在新Windows电脑下载开发分支。已装Git可运行下方命令；未装Git可在上方分支网页用Code → Download ZIP，解压后在项目文件夹打开PowerShell。`setup.ps1`会尝试通过winget安装缺少的Git和Python3.12，并创建环境、安装锁定依赖及运行测试。安装器、网络和系统重启若要求人工处理，应按系统提示完成；尚未在干净新Windows电脑实测整个安装过程。

```powershell
git clone --branch feature/quant-mvp https://github.com/AustinYI111/agent_quant_2.git
cd agent_quant_2
.\setup.ps1
```

2. 登录有权访问私有备份仓库的账号。若刚安装Git，重新打开PowerShell再执行；账号会通过浏览器授权，不要求把Token写进配置。

```powershell
git credential-manager github login --username AustinYI111 --browser
```

3. 一条命令下载并恢复；交互输入另存的备份口令。目标必须不存在或为空，不覆盖已有数据。下载验证GitHub资产SHA-256与长度，解密后验证所有文件哈希／长度及路径安全，再写入目标。数据恢复到`restored/real-20261020/artifacts/`，不是项目根`artifacts/`；因此不需要手动搬动文件。

```powershell
.\scripts\restore-cloud.ps1 -Tag backup-real-research-20261020 -Asset pqr-real-research-20261020.pqr -Out restored/real-20261020
```

4. 先执行低内存恢复检查。它分块读取已封存因子，检查哈希及环境，加载全部9模型，核对41,087条原预测及60条历史Top20。不重训、不重算指标、不使用新行情；输出的股票键是稳定发行人键，不能当成当前可交易代码或实时买入指令。

```powershell
.\.venv\Scripts\python.exe scripts/check_saved_research_models.py --root restored/real-20261020 --out artifacts/restored_saved_model_check
```

5. 有足够可用内存时，再使用现有模型从原始特征输入直接预测。这里示范20日周期最新历史折；5日可用`5d/20250211`，12个月用`12m/20250228`。预测不会重新训练、自动下载新行情或升级正式模型。输出目录必须为空。

```powershell
.\scripts\check-environment.ps1 -Model restored/real-20261020/artifacts/portable_real_horizons_20261020/20d/20250325
.\.venv\Scripts\python.exe -m ashare_mvp.horizon_inference predict --model restored/real-20261020/artifacts/portable_real_horizons_20261020/20d/20250325 --data restored/real-20261020/artifacts/market_ratios_causal_quality1 --asof 2025-12-31 --out artifacts/restored_20d_prediction --exploratory
```

完整预测结果为`top20.csv`与含模型／数据／结果哈希的`prediction.json`。分数仅为排序值，不是预期收益率；2025-12-31结果只是历史快照。全历史特征计算仍需读取大数据，占用时间与内存，模型加载成功不代表任意配置电脑都能快速处理全历史。本轮执行前仅约2.6GB空闲内存，因此没有重跑完整原始行情特征预测入口；实际通过的是上述封存特征低内存检查，不将其当作新行情特征计算验证。

实际依赖：Python3.12.14、LightGBM4.6.0、NumPy2.3.5、pandas3.0.1；锁文件`requirements-tested.txt`，操作依赖`requirements-operations.txt`。模型检查要求Python主次版本、关键库版本、模型／特征合约哈希、因子实现及身份口径匹配。封存元信息内可能保留旧来源路径作为审计记录，运行脚本使用相对路径／项目目录，不依赖旧电脑绝对路径。

模型包含模型文本、模型版本哈希、训练时间、数据及标签截止、特征顺序、预处理规则、超参数、依赖与代码哈希、训练数据元信息。9折训练／验证记录和完整样本外评价位于恢复实验目录，不把组合全周期收益误归为最后一折的未来已实现收益。更多导出命令见[三周期模型迁移](HORIZON_MODEL_PORTABILITY.md)。

## 后续备份

先封存数据，再选择需要的数据库、模型、预测台账和报告；正在变化的SQLite应先导出一致性快照。备份前后及写入过程中核对源哈希，文件变化即失败。示例须替换成实际存在的目录，并为每次备份使用新名称／tag。

```powershell
.\scripts\backup.ps1 -Include artifacts/validated_dataset,artifacts/model_bundle,artifacts/prediction_ledger.csv -Out backups/state-next.pqr
.\scripts\upload-private-backup.ps1 -Archive backups/state-next.pqr -Tag backup-next
```

加密采用PBKDF2-SHA256随机盐及分块AES-GCM，Zip64压缩、逐文件SHA-256及认证终止帧；恢复拒绝损坏、截断、错误口令、目录逃逸及覆盖已有非空目录。上传只接受加密`.pqr`，先验证仓库属于当前账号且为私有；口令通过交互读取，GitHub凭据只留在凭据管理器中。

当前实现拒绝单资产大于等于2GiB，未实现任意分卷。若后续超限，可按可独立恢复的数据域制作多个加密资产，或改用用户控制的私有对象存储；不要反复提交数据库到普通Git。公开数据的再分发许可仍须核验，当前仅私有备份。不将GitHub当无限免费数据库；本次体积是否可行由上述实际上传／恢复证明确认。[GitHub Releases说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。

本轮压缩快照约1.50GB，在当前网络下上传耗时较长。建议按经过验证的版本或每周封存快照，日常预测与轻量报告单独留档；全量数据库不宜每个交易日重复上传。增量快照链尚未实现，不应把定期全量备份称作自动增量备份。

当前云端下载尚无断点续传，慢速或中断时可能需要重新下载；这次完整云端重新下载未跑完，只验证了部分传输，不能标记全量端到端迁移已通过。若通过浏览器完整下载资产，也可使用 `python -m ashare_mvp.backup restore --archive backups/pqr-real-research-20261020.pqr --out restored/real-20261020` 独立恢复；先按上述SHA-256核对下载文件。不要使用本机的`.partial`文件恢复。

## GitHub工作流和日常运行边界

GitHub Actions运行自动测试及合成工程验证；真实大规模研究在本地执行，轻量证据进Git，完整数据与模型入私有快照。每个经过验证的功能提交推送开发分支，更新草稿PR11，用户确认后才合并main。测试通过不证明策略盈利。

每日收盘后行情→特征→正式模型→Top20→日报→预测台账、月度候选训练，**尚未部署**。现有真实历史研究不是2026实时增量服务；还需可靠增量行情／公告／公司行动、交易日历、正式模型与失败告警。月度候选应在相同独立窗口比较，由用户批准后升级，不自动用一次测试结果替换。

GitHub定时工作流需进入默认分支，仍须用户批准main合并；调度可能延迟或停用，托管runner资源／时限及私有配额有限，数据接口可能限流，不能承诺稳定免费。备用方案是Windows任务计划或自托管runner，保持本地缓存并定期私有快照，机器需要持续运行。[schedule说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)、[Actions限制](https://docs.github.com/en/actions/reference/limits)。

未完成的研究限制包括财报原始版本认证、全库所有参考价／退市结算、真实券商成交约束和税费、行业及年报正文解读；个人持仓等待CSV。三周期结果、最大回撤与口径见[真实报告](reports/REAL_MODELS_AND_VALUATION_REPLAY_20261020.md)，不能把成功恢复或测试通过等同于真实盈利能力。
