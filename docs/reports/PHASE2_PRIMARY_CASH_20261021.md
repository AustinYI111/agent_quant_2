# 第二阶段：供应商遗漏的官方现金分派（2026-10-21）

全市场报价诊断发现，两只证券的付款接口返回空记录、六季度计划表也未列出发行人，但官方实施公告明确披露了实际分派。空响应不能被解释成没有分红。本轮单独补入官方已确认的现金，明确标注缺乏供应商交叉确认。

## 实际结果

- 事件数从4,335增至4,337，旧事件全部字段保持不变。新事件只包含公告明确披露的普通A股毛现金分派，不合成供应商原始记录，不补造季度计划标签。
- 明确空响应的原始JSON、CSV及供应商请求清单逐项绑定哈希；六季度原始计划逐文件核验，确认对应发行人缺失。公告URL、哈希、证券及付款／除息日期单独复核。
- 对5,244个历史发行人、988,159条2026年A股报价重跑连续性诊断，差异从67降至65，精确消除两条新分派对应的差异，没有新增异常。
- 38项相关检查通过，包括供应商记录不为空时拒绝使用此分支、季度计划冲突、重复事件、股份分配误入、时点、路径和哈希错误。全量CI见本提交Checks。

新事件的`independent_payment_confirmation=false`，数据集继续保持`model_ready=false`。官方条款补充不等于全市场分红完整性认证，剩余65个参考价差异、最新排名、模拟账户和模型增量检验仍待完成。

[精确哈希与执行证据](evidence/PHASE2_PRIMARY_CASH_20261021.json)。前序结果见[送转股参考比例复核](PHASE2_SHARE_REFERENCES_20261021.md)。

## 复现

准备前序快照、原始供应商回执、季度计划、日历及对应公告后，在项目根目录执行。输出目录必须不存在：

```powershell
.\.venv\Scripts\python.exe -m ashare_mvp.notice_cash `
  --data artifacts/phase2/normalized/payments_share_references_20261021 `
  --provider artifacts/phase2/raw/payment_capture_20261020/histories `
  --plans artifacts/phase2/raw/dividend_six_quarters_20261020 `
  --calendar artifacts/phase2/raw/market_capture_20261021/exchange_calendar.csv `
  --review config/reviewed_notice_only_cash_20261021.json `
  --notice-root artifacts/phase2/official_reference_faults_20261020 `
  --out artifacts/phase2/normalized/payments_notice_cash_new_run
```

若发现供应商已有非空记录、计划表已有发行人或已有权益事件，此分支拒绝导出，必须回到锚定原始条款的复核流程。普通Git只保存代码、测试、审查配置与轻量证据；原文和完整数据仍在本地忽略目录，第二阶段私有云备份尚未完成。通过测试或回放不代表盈利或实盘可用。
