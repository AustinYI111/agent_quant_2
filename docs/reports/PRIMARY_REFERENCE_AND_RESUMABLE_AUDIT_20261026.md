# 除权参考补证与可恢复会计核验（2026-10-26）

**20项预定持仓缺口均已有研究补证；新一轮30账户回放仍待全部结束及独立核验。** 其中17项只影响参考校验（16项原公告直接给出、1项由原公告股本分摊计算），2项补入遗漏现金，1项补齐既有事件的现金分红组件。模型、特征、标签及选择协议保持冻结。

## 000157如何处理

这里没有把实际股东每股0.32元的现金改成0.30元。按[权益分派原公告](https://static.cninfo.com.cn/finalpage/2023-07-14/1217291719.PDF)所列的参与分红A股数量，与[回购完成原公告](https://static.cninfo.com.cn/finalpage/2023-07-20/1217331842.PDF)明确的非参与分红A股数量，计算A股参考分摊：

`0.32 × 6,672,070,922 ÷ (6,672,070,922 + 423,956,766) = 0.3008813929306641`

H股不混入A股分母，各类股数同时核对至发行人总股本。此数是**派生参考参数**，不是实施公告直接公布的现金金额，实际现金／股份权益不变。两份源公告均在除权日前公开；日期型信息按下一真实交易日可用，组合来源的最后可用日为2023-07-21。真实抓取时间保留在2026年。

另用[深交所原月报](https://docs.static.szse.cn/www/market/periodical/month/W020230808524522886945.html)事后核对：月报中的原收盘价和除权报价，与上述计算按报价精度所得结果一致。原HTML用实际编码读取，两套独立解析器核对表头和股票整行。月报发布日期未认证，文件名日期及全月范围不作为除权日前已知信息；它不参与信号、特征、训练标签或参数选择。[来源、计算及交叉核对证据](evidence/PRIMARY_REFERENCE_000157_20261026.json)。历史原始字节版本和实际成交仍未认证。

## 会计核验恢复

旧核验进程停止在22个账户检查点，原因未记录，原目录保留。新工具曾在算完27个完整账户后因收尾变量名冲突报错，未计为验证完成；错误版本及结果保持原样。该错误已修正，端到端测试实际执行股份／现金重建、最终代码校验及完成后的恢复读取。

现在一次加载共同原始行情，各账户完成后立即保存三个输出文件的哈希，并原子保存检查点。恢复必须逐项验证原账户、核验代码和全部已完成输出；拒绝变动来源、代码、文件或重复账户。三个旧不完整账户不会被转换成完整绩效。

完整回归857项通过（424.58秒）。此检查点新回放已尝试11个账户，完整11个；此前27个完整账户的独立核验记录为28项，最终状态以新核验manifest为准。[时点记录](evidence/PRIMARY_REFERENCE_AND_AUDIT_20261026.json)。不根据局部结果选模。

## 重建与运行

先恢复原公告、历史行情、旧账户和新候选模型，再在项目根目录使用新的输出名：

```powershell
python scripts/assemble_industry_valuation_updates.py --out artifacts/phase2/industry_valuation_updates_new --reference-evidence config/reviewed_industry_valuation_refs_20261026_v2.json --reference-corroboration artifacts/phase2/primary_reference_derivation_000157_20261026_v1/manifest.json
python -m ashare_mvp.ranking_account_audit --project . --source artifacts/phase2/full_family_ranking_accounts_20261026_v2 --dataset artifacts/market_ratios_causal_quality1 --benchmark artifacts/benchmark_csi300_price_2018_2025 --out artifacts/phase2/full_family_accounting_verification_new
```

账户源必须已结束全部30次尝试。核验中断后，同一命令增加`--resume`；输出或代码已改变时会拒绝接续。回放命令仍见[候选运行说明](../CANDIDATE_RESEARCH.md)，替换为新的估值输入目录即可。

本批代码、补证配置和轻量证据推送开发分支；最新完整数据、模型、原件和账户仍未进入下一云快照。未升级正式模型、未合并main；择时、等权对照、行业归因与独立成熟验证仍待完成，测试不证明盈利。
