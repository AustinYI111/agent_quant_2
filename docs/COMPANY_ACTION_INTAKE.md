# 公司行动候选与现金暂存

流程分为原始计划采集、待复核分类、公告原页核对、现金事件暂存、真实行情校验。供应商标记“实施分配”仍不是个人账户已到账的证明；缺少支付日期时不会默认等于除权日。

## 候选采集

配方示例，所有路径可移植：普通相对路径对应代码项目，`@work/`对应工作盘。

```json
{
  "schema": "company_action_intake_inputs_v1",
  "signal_date": "2026-10-29",
  "report_start": "2025-12-31",
  "report_end": "2026-09-30",
  "window_start": "2026-10-28",
  "window_end": "2026-11-06",
  "parent_actions": "@work/incoming-20261028/daily_inputs_20261028_v1/payments_reviewed",
  "calendar": "@work/incoming-20261029/close_capture_20261028_reobserved_v1/market/exchange_calendar.csv",
  "code_changes": "config/same_issuer_code_changes.json"
}
```

```powershell
python -m ashare_mvp.company_action_intake `
  --work-root $env:PQR_WORK_ROOT `
  --inputs '@work/incoming-20261029/company_action_intake_inputs_20261029_v1/inputs.json' `
  --out '@work/incoming-20261029/company_action_intake_20261029_v1'
```

29日实际输出已存在，重试须用新目录。后续日期修改为真实采集日期，选择已结束的报告期及明确的执行日期窗口。`raw_plans`保留分页响应，`coverage.csv`逐条说明范围外、未实施、窗口外、旧条款一致或需要复核，`notice_review_queue.csv`保留新增、冲突、未知及重叠事件。同一股票同一除权日跨报告期的多条计划不自动相加或择优；原事件未在本次窗口观察到也不会被删除。部分请求失败时不生成可用于接入的队列。

候选范围排除北交所和CDR等非普通A股；ST新买过滤仍由选股视图执行。公司行动不能因为股票变成ST就抹掉原持仓权益。

## 原公告复核与现金暂存

目前现金扩展入口只支持现金分红。送转、股份到账和配股保留各自的结算／权益规则，不能套用现金模板。

结构化复核记录必须包含候选对应股票与除权日、原PDF和采集记录的路径／哈希／URL／实际观察时钟、人工视觉复核页码及每项金额和日期所在页。实际股东分红与参考扣减额分开保存。可用日期不得早于公告下一交易日和实际观察日期；不能只改一个`reviewed`标记跳过公告核对。

```powershell
python -m ashare_mvp.reviewed_cash_extension `
  --work-root $env:PQR_WORK_ROOT `
  --parent-actions '@work/incoming-20261028/daily_inputs_20261028_v1/payments_reviewed' `
  --intake '@work/incoming-20261029/company_action_intake_20261029_v1' `
  --review '@work/incoming-20261029/reviewed_company_actions_20261029_v1.json' `
  --calendar '@work/incoming-20261029/close_capture_20261028_reobserved_v1/market/exchange_calendar.csv' `
  --out '@work/incoming-20261029/cash_events_staged_20261029_v2'
```

实际v2输出已存在，不能覆盖。新表保留旧事件全部实际值，统一日期文本写法；不能在“扩展”步骤改写旧金额或支付日。分红实际到账、历史原版和当天报价未由这个命令认证。摘要状态是`cash_terms_staged_raw_quote_check_pending`，`prediction_ready=false`；不能把它直接当成已通过真实行情门禁的输入。

`scripts/audit_company_action_intake.py --help`提供独立源行分类及暂存值核对。它支持本次实际、简单且唯一的现金键；不声称能够独立认证所有未来的未知／重叠事件。

尚需当天真实收盘行情，核对前收盘参考字段、缺失覆盖和待复核差异，再准备冻结推理配方。除权参考校验失败继续停止相应流程，不拟合价格或改写股东现金。备份恢复暂缓。
