# 低内存全池账户：2026-10-28

已实现按交易日读取的原始股份账户，历史订单直接写研究盘，不在内存累计全部行情或订单。原排名引擎、模型、样本、股东实际现金及股份不改写。

## 实际构建与测试

真实行情缓存已构建：8,609,196行、5,401个发行人，545,738,752字节；本次构建进程测得峰值工作集117.69 MB。缓存首次生成，后续公告版本只创建小型参考校验库，不重复复制行情。[资源记录](evidence/DISK_QUOTE_RESOURCE_20261028.json)。独立读取原CSV与SQLite后，全860.9万行的报价、日期、发行人、终止日和执行标志指纹一致；旧质量标签不沿用，参考校验由七事件版本重新计算。三周期实际尝试已结束，但均在未知持仓处停止，不能声称完整等权绩效。

新增43项测试已通过：最初相关54项通过，新增独立核账相关51项及前缀保护2项通过。正常交易、分红、送转、停牌、阻止卖出、缺失新买和未知正持仓场景的新旧净值、现金和逐笔订单完全一致；完整三周期生产流程与旧完整／停止账本一致。拒绝重复或不可见键、非A股新候选、不等权、杠杆、无效行情、缓存改动和跨越原日历。12个月账户保留完整原结束日期，无期末强行卖出。

未知持仓停止时保存有效前缀、实际股份和原因，不计算全周期收益。股票池仍来自原始合格键，不按未来收益、缺失因子或估值成功与否筛除股票。行情缓存保留所有源发行人，不因此扩大选股池。

## 命令

要求已有本检查点的原始研究数据、第四轮等权输出和审核输入。先将PQR_WORK_ROOT设为选定研究目录；所有输出目录须为新目录，复跑时改名。缓存与参考审核可以分别构建：

```powershell
if (-not $env:PQR_WORK_ROOT) { throw '请先设置研究目录 PQR_WORK_ROOT' }
python scripts/build_disk_cache.py quotes --raw artifacts/market_ratios_causal_quality1/event_raw_prices.csv --calendar artifacts/market_ratios_causal_quality1/calendar.csv --out "$env:PQR_WORK_ROOT/research/disk_quote_store_2018_2025_v1"
python scripts/build_disk_cache.py references --store "$env:PQR_WORK_ROOT/research/disk_quote_store_2018_2025_v1" --raw artifacts/market_ratios_causal_quality1/event_raw_prices.csv --actions "$env:PQR_WORK_ROOT/research/disk_reference_inputs_20261028_v1/actions.csv" --out "$env:PQR_WORK_ROOT/research/disk_reference_audit_7_20261028_v1"
python -m ashare_mvp.streamed_equal_weight --project . --work-root "$env:PQR_WORK_ROOT" --original-control research/eligible_equal_weight_20261028_v4 --quote-store research/disk_quote_store_2018_2025_v1 --reference-audit research/disk_reference_audit_7_20261028_v1 --ranking-accounts artifacts/phase2/full_family_ranking_accounts_20261026_v2 --dataset artifacts/market_ratios_causal_quality1 --experiment artifacts/three_horizon_real_causal_quality1 --seal-directory artifacts/phase2/protocol_20261020 --reference-evidence config/reviewed_equal_weight_refs_20261028_v5.json --out research/streamed_equal_weight_20261028_v1
```

actions.csv是用既有原公告审核函数从原第四轮事件生成的七事件版本，不是未审核的原始分红计划。生产流程会再次应用原证据并比较全部事件，核对原始样本逐日成员指纹及两种树数基线的完整日历。缓存、参考校验、原始数据和生产代码均绑定哈希。

七项补证后的实际三周期尝试及其独立有效前缀核账已完成，公司行动和终值没有因计算方式改变而放宽。完整等权账户仍未完成。行业择时、最终家族选择和独立长期验证保持待完成。备份恢复按用户要求暂缓，公开Git仅保存代码、测试和轻量证据；历史研究与测试通过不代表真实盈利。

## 七项补证后的真实结果

三周期均推进至2021-12-02，在`sz.002142`正持仓参考价校验处停止；原请求结束日期分别仍为2025-12-25、2025-12-18、2025-09-02。没有缩短12个月期限，没有删除该持仓，也没有计算全区间收益。[逐账户终态](evidence/STREAMED_EQUAL_VERIFIED_20261028.json)。

本次真实三周期进程的峰值工作集为233.75 MB；完整行情缓存只生成一份约520 MB，七项公告审核使用小型故障库。所有大文件与临时文件在研究盘，C盘本轮仍约25 GB可用。[实际资源记录](evidence/STREAMED_EQUAL_RESOURCE_20261028.json)；[全行独立报价核验](evidence/DISK_QUOTE_INDEPENDENT_20261028.json)。这只证明本次工作负载的实际用量，不保证任意未来输入都使用相同内存。

独立重建以观察到的成交而非目标权重为输入，核对原始股份、现金、分红应收、日期税率、逐日损益、执行汇总及停止时的全部实际持仓。有效前缀分别为79、79、81个交易日；核查58,465、14,569、3,537条订单，最大重建误差5.79e-14。此前第四轮截至2021-11-22的三份有效净值和订单前缀数值完全一致。[独立核账](evidence/STREAMED_EQUAL_AUDIT_20261028.json)；[旧前缀保护](evidence/STREAMED_EQUAL_PREFIX_20261028.json)。这些仍是前缀核验，不能当作完整周期回测通过。

下一项需明确处理原公告所示的普通股配股及缴款／股份可售时点，不能套用优先股分红公告、当作免费送股、虚构现金或放宽参考误差。完整历史参考审核标记1,549个报价事件，该数量覆盖全部原始历史，不等同于真实持仓错误数量。新财务面板接入、择时行业归因和固定家族最终审查继续待完成，冻结模型不自动升级。
