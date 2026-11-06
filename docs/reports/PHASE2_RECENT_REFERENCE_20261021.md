# 最新公告参考价复核（2026-10-21）

心脉医疗2026年半年度权益分派的实际毛现金为每股1.30元，除息参考价使用每股1.27元，两者因回购股份不参与分配而不同。已核对官方实施公告第1页的实际分派及日期、第3页的明确参考公式；没有从报价跌幅反推金额。[官方公告，第1及3页](https://static.cninfo.com.cn/finalpage/2026-10-14/1225603847.PDF#page=3)。

新快照仅补入这一条参考价条款，4,337个事件及全部实际现金、股份、支付和到账字段保持不变。对5,244个发行人、988,159条A股报价完整重跑后，参考价差异从65降至64，精确消除`sh.688016 / 2026-10-20`，没有新增差异或交易日缺口。[精确证据](evidence/PHASE2_RECENT_REFERENCE_20261021.json)。

原已保存的10月20日三周期排名、分数和预测账本不修改，仍对应当时65处差异的输入版本。本公告于10月21日首次抓取；公开披露日期不冒充本系统当时已观测的版本。新公司行动快照用于后续研究输入；当前财务历史版本、其余64处参考价差异及实时执行仍未认证。

复现：

```powershell
python scripts/apply_reviewed_references.py --data artifacts/phase2/normalized/payments_notice_cash_20261021 --calendar artifacts/phase2/raw/market_capture_20261021/exchange_calendar.csv --review config/reviewed_recent_cash_reference_20261021.json --notice-root artifacts/phase2/official_new_reference_fault_20261021 --out artifacts/phase2/normalized/my_recent_reference_review
```

原始公告、行情和新快照留在本地忽略目录，尚未进入第二阶段私有云备份。普通Git保存条款配置和轻量证据；这项数据修正不是投资收益验证。
