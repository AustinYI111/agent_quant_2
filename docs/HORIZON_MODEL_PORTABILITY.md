# 三周期候选模型：保存、恢复和直接选股

独立入口 `ashare_mvp.horizon_inference` 支持5交易日、20交易日、12自然月研究模型；没有训练调用。真实9模型和完整三周期绩效现已完成；它们仍是研究候选，未批准为正式模型。恢复路径和备份版本见[MIGRATION.md](MIGRATION.md)。

## 从实际训练折导出

在项目根目录执行；将模型目录替换为本次运行实际生成的折目录。5d可替换为20d或12m。不要覆盖已有输出。

```powershell
python -m ashare_mvp.horizon_inference export --model artifacts/three_horizon_real_causal_quality1/models/5d/20230201 --training-data artifacts/market_ratios_causal_quality1 --out artifacts/portable_horizon_5d
python -m ashare_mvp.horizon_inference check --model artifacts/portable_horizon_5d
```

导出保存 `model.txt`、`metadata.json`、`feature_input_policy.json` 和 `training_dataset_metadata.json`。不重新训练，不把候选自动升级为正式模型。训练元数据必须与原模型绑定的数据版本一致；来源哈希不同则拒绝。训练目录包含多个历史折时，应按明确的预测时点选择当时已经存在的模型，不能用后来的折回填过去。

## 新电脑恢复后直接预测

先用项目既有安装／私有备份恢复流程取得匹配的代码、依赖、封存数据和模型包。普通Git只包含程序与说明，完整数据和模型需另外恢复；本轮真实备份及下载恢复证明以迁移教程的版本记录为准。

```powershell
.\setup.ps1
.\scripts\check-environment.ps1 -Model artifacts/portable_horizon_5d
.\.venv\Scripts\python.exe -m ashare_mvp.horizon_inference predict --model artifacts/portable_horizon_5d --data artifacts/market_ratios_causal_quality1 --out artifacts/restored_horizon_5d_prediction --exploratory
```

省略`--asof`时使用恢复数据的最后一个交易日；可明确传入已观察日期，但不得早于模型的历史训练时点。不会下载行情或在幕后训练。输出 `top20.csv` 和 `prediction.json`，后者记录模型／数据／输出哈希、周期、信号日、特征和 `retrained=false`。若证券存在历史代码变更，保留当日 `trading_symbol`，不直接以最新代码回填。

检查要求LightGBM、NumPy、pandas版本与训练记录一致，Python主次版本一致，模型与特征合约哈希一致，当前因子/公开数据快照/因果质量代码与训练规则一致。预测数据还必须保持真实／合成类型、价格口径、质量规则及证券身份映射一致。特征和财务仅使用信号日及以前的数据，继续使用原同日横截面预处理；缺失技术特征不以未来收益是否可得来筛选。

恢复旧模型时，如果代码或依赖已更新导致拒绝，应恢复该模型对应的版本与环境，不能跳过校验强行加载。原有月度模型仍由 `model_registry` 检查，Windows检查脚本自动识别两种目录结构。

这仍是未认证公开数据上的候选排序：分数不是预期收益率，输出Top20不证明盈利，不执行交易，不代表模型已通过正式升级审核。真实9模型共41,087条逐折首日预测已独立验证完全一致；这不是干净Windows新电脑安装实测。

## 本轮实际验证

新增9项模型导出／加载／预测测试通过（19.08秒），完整480项测试通过（278.79秒）。合成模型的独立`check`和`predict`命令也实际退出0，输出20条排序，`retrained=false`；保存后分数一致、未来输入不改过去预测、不同数据类型／价格／身份约定拒绝、数据元信息／特征实现／依赖／模型篡改拒绝、早于训练时点与覆盖已有结果拒绝均已验证。Windows脚本通过PowerShell语法解析。后续完整488项通过，真实9模型独立恢复最大误差0.0，三周期绩效见最新真实报告。

## 低内存的封存特征恢复检查

如果新电脑空闲内存不足以重算整库指标，可先运行：

```powershell
.\.venv\Scripts\python.exe scripts/check_saved_research_models.py --root restored/real-20261020 --out artifacts/restored_saved_model_check
```

该脚本分块读取封存因子，验证因子／原预测／历史Top20哈希与模型环境，加载9模型逐折对照原预测，再核对2025-12-31的三周期Top20。实际本机验证41,087条原预测最大误差0.0、60条Top20一致，见[审计](reports/evidence/saved_real_model_verification_20261020.json)。无训练调用，不重算技术指标；仅重放已封存研究特征，不下载新行情、不形成实时交易指令。输出中的symbol为稳定发行人键。

本轮资源检查仅约2.6GB空闲内存，因此没有再启动原始全历史输入的直接特征重算预测命令；不能把封存特征评分通过等同于新行情管线或干净新Windows安装已经验证。恢复后应先做此轻量检查，再按实际电脑资源运行完整入口。
