# 日更输入配方

`daily_input_recipe`把上一份冻结快照、已完成收盘采集、独立验证的财务及现金暂存衔接为`frozen_inputs.json`。它只准备研究输入，不采集、不训练、不生成预测、不入档、不估值持仓，也不创建备份。

## 准备顺序

先使用`close_capture`取得明确交易日的完整行情、日历及证券列表；财务须有`audit_financial_refresh.py`记录，现金暂存须有`audit_company_action_intake.py`记录。新配方只写工作目录的新文件夹。

下例是**11月4日通过实际17:20门禁并完成收盘采集后**的操作示例，目前不表示4日数据已完成：

```powershell
python -m ashare_mvp.daily_input_recipe `
  --work-root $env:PQR_WORK_ROOT `
  --previous-snapshot '@work/research/frozen_daily_research_20261103_v1' `
  --previous-snapshot-sha256 8e4c2b3bd13c5d63f2059ca7cfcc75e0d109ec985325be41e6855e7f62d0c0ee `
  --previous-inputs '@work/incoming-20261103/daily_inputs_20261103_v1/frozen_inputs.json' `
  --capture '@work/incoming-20261104/close_capture_20261104_v1' `
  --financial-refresh '@work/incoming-20261104/financial_refresh_20261104_v1' `
  --financial-audit '@work/research/financial_refresh_verification_20261104_v1.json' `
  --actions '@work/incoming-20261104/cash_events_staged_20261104_v1' `
  --actions-audit '@work/research/company_action_intake_verification_20261104_v1.json' `
  --signal 2026-11-04 `
  --out '@work/incoming-20261104/daily_inputs_20261104_v1' --exploratory
```

按实际日期及已核对版本修改参数。多天中断时可重复传`--additional-markets`，补齐上一信号日至当前信号日的全部交易日；额外历史财务可重复传`--additional-financial-history`。缺日、重复日期或回放旧日文件冒充新日都会拒绝。

## 检查与输出

- 校验上一快照及原配方哈希，保留冻结模型和旧输入来源。原封存日历与旧采集日历一起核对，允许来源起始年份不同，拒绝改写已知交易日。
- 新采集必须完整且对应最新已完成交易日；逐文件核对哈希、真实获取时间、价格基础、证券范围、交易状态及OHLC／成交量。停牌缺失成交量保留缺失。
- 财务及现金独立核验记录必须绑定同一版本，拒绝未来财务观察或改写旧现金字段。新增窗口内已复核现金事件必须有相邻原始报价且参考价一致。
- 未解释的其他断点留在`quality_issues.csv`，不猜测分红、不删股票、不替换现金。原冻结分段／原生缺失值规则继续由推理入口执行。

输出为`frozen_inputs.json`、`quality_issues.csv`与带输入／代码哈希的`preparation.json`。即使配方准备成功，`model_ready=false`；这是研究输入衔接，不是完整数据、终值或实盘认证。已有输出不会覆盖，所有失败检查在创建输出目录前完成。

成功后才按[工作盘日更](WORKSPACE_DAILY.md)运行冻结推理、验证并入档、生成当日风险名单及模拟跟踪；使用`--exploratory --skip-ledger-copy`。预测及名单保留真实创建时间，晚于对应开盘则不能补授权历史交易。

## 已完成的真实验证

11月4日上午用原28日快照、29／30日补采、2／3日行情及对应财务／现金核验，重建3日配方。结果与原来实际用于3日预测的JSON配置和文件SHA-256完全一致；18处断点仍保留，两项已复核现金参考价匹配。没有重跑模型或生成预测。实际请求4日配方也被17:20门禁拦截且未创建输出。[验证结果](reports/DAILY_INPUT_RECIPE_20261104.md)。
