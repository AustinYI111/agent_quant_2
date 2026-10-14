# Personal Quant Research：云端管理与迁移

## 当前存储布局

- `AustinYI111/agent_quant_2`：公开源代码、依赖锁、测试、脚本和轻量验证报告；开发分支为`feature/quant-mvp`。main仅由用户确认合并。
- `AustinYI111/agent_quant_2-backups`：已创建并验证为私有。历史行情、财务、模型与预测记录以**加密压缩快照放在Releases资产**，不反复提交数据库到普通Git。备份密码不上传到此仓库。
- 公开代码仓库的模型Releases：仅用于经过数据与样本外验证、经所有者批准且允许公开的正式模型。目前没有这种模型，也未发布合成模型冒充正式模型。
- `artifacts/`、`private/`、`backups/`、`restored/`和临时备份目录不进入公开Git；API凭据通过本机Git Credential Manager或服务的秘密管理配置，`.env`、密钥文件及备份密码拒绝进入备份选择。

[GitHub Releases官方限制](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)：单个资产必须小于2 GiB，每个Release最多1000资产。当前实现拒绝大于等于2 GiB的单文件；超出时需分卷（尚未实现）或迁往私有对象存储。定期全量压缩快照适合当前规模，更新频繁时宜周度快照与增量数据目录，不应每天重复上传整库。不把GitHub容量说明当成无限免费服务承诺。公开源数据的再分发许可尚未核验，当前只放私有仓库。

## 已实现的模型持久化

标准`run`每个滚动训练完成后均保存LightGBM文本模型和同名JSON元数据：模型内容版本、训练时间、训练特征截止、标签截止、预测时点、全数据截止、完整特征顺序、预处理规则及实现源码、超参数、Python/依赖版本、数据哈希、代码提交与未提交状态，以及整个滚动实验的评价指标。该评价不等于最后一个模型已经有未来实盘或样本外收益。

```powershell
.venv/Scripts/python -m ashare_mvp.model_registry export --run artifacts/model_run --out artifacts/model_bundle
.venv/Scripts/python -m ashare_mvp.model_registry check --model artifacts/model_bundle
.venv/Scripts/python -m ashare_mvp.model_registry predict --model artifacts/model_bundle --data artifacts/validated_dataset --out artifacts/current_top20.csv
```

加载时核对模型哈希、LightGBM特征顺序、因子源码哈希和记录的依赖版本；不同因子代码或依赖不会被静默接受。因子哈希规范化换行，避免Windows/Linux换行差异误判。合成模型不能用于真实数据。预测只加载Booster，不重新训练。当前适配标准Dataset；公开`public_ratios_v1`探索模型的便携推理适配仍待开发。

正式发布脚本`publish-model.ps1`要求非合成、所有者批准、干净代码提交和明确公开确认；Release标签绑定训练代码提交，不默认指向旧main。当前所有模型为候选/合成验证模型，审批字段默认false。今后必须先完成真实数据校验和候选模型评价，再由用户决定是否修改批准状态及升级正式模型。

## Windows换电脑恢复

首次新电脑下载`feature/quant-mvp`代码或克隆：

```powershell
git clone --branch feature/quant-mvp https://github.com/AustinYI111/agent_quant_2.git
cd agent_quant_2
powershell -ExecutionPolicy Bypass -File setup.ps1
```

若先下载ZIP解压，也可直接运行setup.ps1。安装脚本使用脚本所在目录，创建项目`.venv`并安装锁定依赖；缺Python/Git时通过winget尝试安装，不写死用户目录。安装可能需系统许可、网络或重启终端；干净新电脑尚未实机测试，已做PowerShell语法检查。若机器已安装Python/Git，则不会因模型恢复要求重新训练。

私有仓库第一次访问需在新电脑登录GitHub一次：

```powershell
git credential-manager github login --username AustinYI111 --browser
```

从私有Releases下载并恢复：

```powershell
./scripts/restore-cloud.ps1 -Tag backup-20261014-initial -Asset pqr-state-20261014.pqr -Out restored
```

按提示输入私下保存的备份密码。下载校验GitHub返回的SHA-256/大小，解密验证每个文件哈希及安全路径，拒绝覆盖已有恢复目录。密码丢失不能恢复；不要放到公开或私有备份仓库内，应在个人密码管理器或离线安全位置另存。

首份快照的验证模型与输入明确为合成数据。验证恢复后不重训的路径：

```powershell
./scripts/check-environment.ps1 -Model restored/artifacts/candidate_model
.venv/Scripts/python -m ashare_mvp.model_registry predict --model restored/artifacts/candidate_model --data restored/artifacts/demo_validation_data --out restored/reloaded_top20.csv
```

恢复后的真实历史缓存同样保留在`restored/artifacts/`，它们仍是部分数据，不可直接用于宣称真实全市场收益。后续正式模型可用相同check/predict命令，指定正式模型及真实已验证数据目录。已保存环境配置、模型和数据不需要手动重建；GitHub登录、私下密码和其他服务凭据必须由所有者提供。

## 自己制作后续备份

先生成模型导出目录，再选择**已封存、不再写入**的数据、模型、预测台账和报告；不要备份正在变化的数据库文件。备份在写入前/后检查源哈希，检测变化即失败。SQLite应先导出一致性快照并包含必要元数据。

```powershell
./scripts/model-backup.ps1 -Run artifacts/model_run -Out artifacts/model_bundle
./scripts/backup.ps1 -Include artifacts/model_bundle,artifacts/validated_dataset,artifacts/prediction_ledger.csv -Out backups/state-next.pqr
./scripts/upload-private-backup.ps1 -Archive backups/state-next.pqr -Tag backup-next
```

加密采用PBKDF2-SHA256随机盐及分块AES-GCM，压缩使用Zip64、逐文件SHA-256及认证终止帧；恢复拒绝损坏、截断、错密码、目录逃逸和已有目标覆盖。上传前再次确认目的仓库为私有，拒绝明文数据库。密码通过交互提示读取，不作为命令参数、不写日志；客户端复用Git登录，仅向GitHub官方API认证，不打印或保存GitHub令牌。

## GitHub工作流和日常运行

已有GitHub Actions运行MVP、旧代码离线回归及合成完整运行检查；新增加密备份/模型迁移测试属于同一套测试。开发分支成功测试后创建或更新草稿PR，说明测试范围与数据边界；没有自动合并步骤。仓库设置若限制Actions创建PR，直接通过所有者账号创建，后续工作流更新现有PR。

每日收盘后自动行情→特征→正式模型→Top20→报告→预测台账，以及月度候选训练，**尚未启用**：仍缺完整真实行情/公司行动/终值、当前交易日增量管线、正式模型和升级评价规则。配置定时任务前应先令本地完整流程通过，并采用交易所日历判定交易日，上海时区17:20对应09:20 UTC；不能用工作日代替A股交易日。

[GitHub官方schedule说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)明确可能延迟，公共仓库60天无活动可能停用，定时工作流需要进入默认分支，因此仍须用户批准main合并。[托管runner作业上限](https://docs.github.com/en/enterprise-cloud%40latest/actions/reference/limits)为6小时；[runner资源](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)与私有仓库账户配额有限。不能假设完整历史采集、长期数据库保存和训练全部稳定免费运行。

备用方案是用户电脑Windows任务计划或自托管runner，持久化缓存、本地训练及私有Release快照；机器需保持运行，接口限流/失败要断点恢复。云端仅跑测试、轻量日更、发布，重训练可在本地完成。月度新模型始终先作candidate，与当前模型在相同样本外窗口比较，再由用户批准升级；不能因一次测试通过自动替换正式模型。

## 当前不足

全市场真实训练/无泄漏绩效、退市终值和完整公司行动仍未完成；行业和原年报正文自动解读尚未实现；实际持仓等待用户填写CSV。正式模型Releases尚无资产。定时日更与月度训练升级未部署；大于2 GiB快照分卷未实现。新电脑安装尚未实际在干净系统验证。测试证明工程行为，不证明策略真实盈利。
