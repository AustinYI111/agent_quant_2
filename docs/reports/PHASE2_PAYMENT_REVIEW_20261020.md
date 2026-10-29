# 第二阶段：付款事件复核（2026-10-20）

这是最新行情特征计算的准备步骤，不是第二阶段完成报告，也未证明模型有增量收益。

## 本轮变化

- 将 2025 年二季度至 2026 年三季度的六份季度计划合并：5,901 条原始记录。合并要求来源完成、季度连续、无版本重叠，逐文件核验哈希并保留原始字节。
- 用九份官方公告复核八只 A 股的现金分派及一只 CDR 的事件范围；审查配置单独存放，不改第一阶段封存配置。
- 跨报告期同日分派只生成一个现金事件；保留各原始报告期锚点，不能重复计入收入。
- 实际股东现金与除息参考现金分别记录。公告中的舍位精度与持有人类别差异必须明确，不能通过价格拟合反推股息。
- 区间之外的执行记录单独分类；未知证券在区间内仍须核验，不自动放行。
- 标有重整、破产、债权人或抵债的记录不再自动变成普通股东送转权益，进入拒绝清单等待专门审查。

## 实际进度与边界

首轮官方公告复核得到 4,327 个事件：4,319 条计划匹配独立付款、12 条计划经公告核实（合并为 8 个事件），1 条 CDR 事件排除。979 条记录在校验区间之外、305 条属北交所、285 条尚未实施。

独立付款来源另有未匹配记录，含特别分红及重整记录。它们必须显式保留，不能因计划来源没列出就漏算，也不能直接并入账户。修复后的标准化结果与最终计数见[执行证据](evidence/PHASE2_PAYMENT_REVIEW_20261020.json)。

公告原文和完整数据库保存在本地忽略目录；公开 Git 只保存代码、审查配置和轻量证据。原始公告历史版本仍未认证；本轮使用毛股息，不模拟投资者特定税率或账户分币舍位。**当前 `model_ready=false`：尚需补齐独立事件、完成原始报价参考价连续性校验，才能接入最新排名与模拟账户。**

## 复现

准备完整季度计划、标准化付款、交易日历、官方 PDF 原文及代码映射后，在项目根目录执行；各目录对应本地实际封存版本，输出目录必须不存在：

```powershell
.\.venv\Scripts\python.exe scripts/reconcile_phase2_payments.py `
  --plans artifacts/phase2/raw/dividend_six_quarters_20261020 `
  --payments artifacts/phase2/normalized/payments_no_reorganization_20261020 `
  --calendar artifacts/phase2/raw/market_capture_20261020/exchange_calendar.csv `
  --review config/phase2_payment_review_20261020.json `
  --notices artifacts/phase2/official_payments_20261020 `
  --code-changes config/same_issuer_code_changes.json `
  --out artifacts/phase2/normalized/payments_reviewed_new_run
```

流程验证原始输入及公告哈希、精确条款锚点、可用日期和付款日期；输出事件、覆盖表、合并审计、范围审计、未匹配独立付款清单及输入输出哈希。原始数据及旧结果不覆盖。
