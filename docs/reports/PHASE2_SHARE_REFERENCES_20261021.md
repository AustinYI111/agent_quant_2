# 第二阶段：现金与送转参考比例复核（2026-10-21）

在[178条现金参考价复核](PHASE2_CASH_REFERENCES_20261021.md)之后，本轮逐项阅读85个送转事件的官方公式，同时核对参考现金和股份变动比例。证券、执行日期、公告时间及文件哈希已检查，代表性页面已做视觉核验；其余自动提取候选仍未放行。

## 实际结果

- 85条成对参考比例已应用。实际股息、实际持股倍数、付款日及股份到账字段逐列保持不变，没有用除权参考比例替代股东权益。
- 在5,244个历史发行人、988,159条2026年A股报价上重跑连续性检查：差异从152降至67。消除的事件键与85条配置完全一致，没有新增差异或放宽价格容差。
- 23项相关检查通过，包括复核链连续应用、旧参考值保留、股数与参考分母分离，以及哈希／日期／条款冲突拒绝导出。全量CI见本提交Checks。

[执行证据](evidence/PHASE2_SHARE_REFERENCES_20261021.json)。本轮只推进公司行动数据准确性；剩余67个差异、最新模型排名、模拟跟踪、收益归因及候选增量检验仍未完成，继续保持`model_ready=false`。

## 复现

准备上一轮现金参考价快照、审查配置、日历和对应公告后，在项目根目录执行。输出目录必须不存在：

```powershell
.\.venv\Scripts\python.exe scripts/apply_reviewed_references.py `
  --data artifacts/phase2/normalized/payments_cash_references_20261021 `
  --calendar artifacts/phase2/raw/market_capture_20261021/exchange_calendar.csv `
  --review config/reviewed_share_reference_terms_20261021.json `
  --notice-root artifacts/phase2 `
  --out artifacts/phase2/normalized/payments_share_references_new_run
```

复核入口接受已补充现金的快照或上一次参考价复核输出，逐批绑定父快照及公告哈希。它不会覆盖旧目录、删除未知资产、训练模型或自动升级正式模型。遇到与既有参考值冲突的条款仍拒绝导出。

公开Git只保存代码、配置、测试和轻量证据；原始公告与完整运行结果仍在本地忽略目录，尚未纳入第二阶段私有备份。原始历史版本、真实成交、投资者特定税费和账户分币处理仍未认证，不能把校验通过当作盈利证明。
