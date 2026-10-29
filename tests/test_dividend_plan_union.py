import json
import pandas as pd
import pytest
from ashare_mvp.dividend_plan_union import merge_dividend_plan_captures
from ashare_mvp.phase2_protocol import sha256


def source(tmp_path,name,date,synthetic=True):
    root=tmp_path/name;period=pd.Timestamp(date);folder=root/f'{period.year}Q{period.quarter}';folder.mkdir(parents=True)
    pd.DataFrame(dict(SECUCODE=['600000.SH'],REPORT_DATE=[date])).to_csv(folder/'records.csv',index=False)
    info=dict(status='ok',rows=1,hashes={'records.csv':sha256(folder/'records.csv')})
    manifest=dict(schema='market_dividend_plans_v1',status='downloaded_raw',synthetic=synthetic,start=date,end=date,periods={date:info})
    (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    return root


def test_adjacent_periods_are_combined_without_changing_raw_files(tmp_path):
    a=source(tmp_path,'a','2025-06-30');b=source(tmp_path,'b','2025-09-30')
    proof=merge_dividend_plan_captures([a,b],tmp_path/'union')
    assert proof['requested_periods']==2 and proof['synthetic']
    assert sha256(tmp_path/'union/2025Q2/records.csv')==sha256(a/'2025Q2/records.csv')


def test_overlap_is_not_implicitly_preferred_or_rewritten(tmp_path):
    a=source(tmp_path,'a','2025-06-30')
    with pytest.raises(ValueError,match='overlapping'):
        merge_dividend_plan_captures([a,a],tmp_path/'union')


def test_quarter_gap_cannot_be_labelled_complete(tmp_path):
    a=source(tmp_path,'a','2025-06-30');b=source(tmp_path,'b','2025-12-31')
    with pytest.raises(ValueError,match='missing raw plan quarter'):
        merge_dividend_plan_captures([a,b],tmp_path/'union')


def test_changed_source_and_mixed_dataset_kind_are_rejected(tmp_path):
    a=source(tmp_path,'a','2025-06-30');b=source(tmp_path,'b','2025-09-30',False)
    with pytest.raises(ValueError,match='different dividend'):
        merge_dividend_plan_captures([a,b],tmp_path/'union')
    (a/'2025Q2/records.csv').write_text('changed')
    with pytest.raises(ValueError,match='source hash'):
        merge_dividend_plan_captures([a],tmp_path/'union')
