import hashlib
import json
import pandas as pd
import pytest

from ashare_mvp.dividend_normalization import normalize_dividends


def evidence(tmp_path, **changes):
    source = tmp_path / "source"
    source.mkdir()
    row = dict(code="sh.600000", dividPlanDate="2018-07-06", dividOperateDate="2018-07-13",
               dividPayDate="2018-07-13", dividStockMarketDate="", dividCashPsBeforeTax="0.1",
               dividStocksPs="0", dividReserveToStockPs="", dividCashStock="10派1元（含税）")
    row.update(changes)
    file = source / "sh.600000_2018.csv"
    pd.DataFrame([row]).to_csv(file, index=False)
    manifest = dict(schema="dividend_raw_v1", status="downloaded_raw",
                    request=dict(symbols=["sh.600000"], start_year=2018, end_year=2018, year_type="operate"),
                    files={file.name: dict(rows=1, sha256=hashlib.sha256(file.read_bytes()).hexdigest())})
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    calendar = tmp_path / "calendar.csv"
    pd.DataFrame({"date": pd.bdate_range("2018-07-02", "2018-07-20")}).to_csv(calendar, index=False)
    return source, calendar, tmp_path / "out"


def test_cash_terms_and_next_session_visibility(tmp_path):
    args = evidence(tmp_path)
    result = normalize_dividends(*args)
    assert result["accepted"] == 1 and result["rejected"] == 0
    assert result["certified"] is False
    row = pd.read_csv(args[2] / "actions.csv").iloc[0]
    assert row.available_date == "2018-07-09"
    assert row.cash_per_share == 0.1 and row.share_multiplier == 1


def test_bonus_entitlement_and_tradability_date_are_distinct(tmp_path):
    args = evidence(tmp_path, dividStocksPs="0.2", dividReserveToStockPs="0.3",
                    dividCashStock="10送2转3派1元", dividStockMarketDate="2018-07-16")
    normalize_dividends(*args)
    row = pd.read_csv(args[2] / "actions.csv").iloc[0]
    assert row.share_multiplier == 1.5 and row.stock_market_date == "2018-07-16"


@pytest.mark.parametrize("changes", [dict(dividCashPsBeforeTax=""), dict(dividCashStock="10配3股"),
                                     dict(dividCashPsBeforeTax="0.2"), dict(dividPlanDate="2018-07-13")])
def test_unproven_terms_or_late_disclosure_quarantined(tmp_path, changes):
    args = evidence(tmp_path, **changes)
    result = normalize_dividends(*args)
    assert result["status"] == "partial" and result["accepted"] == 0 and result["rejected"] == 1
    assert len(pd.read_csv(args[2] / "rejected.csv")) == 1


def test_source_tamper_rejected_before_export(tmp_path):
    args = evidence(tmp_path)
    (args[0] / "sh.600000_2018.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        normalize_dividends(*args)
    assert not args[2].exists()


def test_explicit_transfer_increase_synonym_keeps_numeric_crosscheck(tmp_path):
    args=evidence(tmp_path,dividCashStock="10送2转增3派1元（含税）",dividStocksPs="0.2",dividReserveToStockPs="0.3")
    result=normalize_dividends(*args)
    assert result["accepted"]==1
    assert pd.read_csv(args[2]/"actions.csv").share_multiplier.iloc[0]==1.5


@pytest.mark.parametrize("description,transfer",[("10转增3派1元","0.4"),("10转2转增3派1元","0.5")])
def test_transfer_synonym_does_not_accept_conflicting_or_duplicate_terms(tmp_path,description,transfer):
    args=evidence(tmp_path,dividCashStock=description,dividReserveToStockPs=transfer)
    assert normalize_dividends(*args)["accepted"]==0


def replace_rows(args, frame):
    source=args[0];file=source/'sh.600000_2018.csv';frame.to_csv(file,index=False)
    meta=json.loads((source/'manifest.json').read_text(encoding='utf8'))
    meta['files'][file.name].update(rows=len(frame),sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    (source/'manifest.json').write_text(json.dumps(meta),encoding='utf8')


@pytest.mark.parametrize('missing_cash',[False,True])
def test_compatible_duplicate_versions_pay_once_and_keep_source_rows(tmp_path,missing_cash):
    args=evidence(tmp_path);row=pd.read_csv(args[0]/'sh.600000_2018.csv',dtype=str).iloc[0]
    rows=pd.DataFrame([row,row]).reset_index(drop=True)
    if missing_cash:rows.loc[0,'dividCashPsBeforeTax']=''
    replace_rows(args,rows)
    result=normalize_dividends(*args)
    assert result['accepted']==1 and result['rejected']==0
    assert result['duplicate_groups']==1 and result['duplicate_rows_coalesced']==1
    out=pd.read_csv(args[2]/'actions.csv').iloc[0]
    assert out.cash_per_share==.1 and json.loads(out.source_rows_json)==[0,1]
    assert out.source_row==(1 if missing_cash else 0)
    audit=pd.read_csv(args[2]/'duplicate_audit.csv')
    assert audit.status.iloc[0]=='coalesced_compatible_raw_versions'
    assert hashlib.sha256((args[2]/'duplicate_audit.csv').read_bytes()).hexdigest()==result['duplicate_audit_sha256']


@pytest.mark.parametrize('field,value',[
    ('dividCashPsBeforeTax','0.2'),('dividPayDate','2018-07-16'),
    ('dividPlanDate','2018-07-09'),('dividCashStock','10派2元'),
])
@pytest.mark.parametrize('reverse',[False,True])
def test_conflicting_duplicate_versions_quarantine_whole_key(tmp_path,field,value,reverse):
    args=evidence(tmp_path);row=pd.read_csv(args[0]/'sh.600000_2018.csv',dtype=str).iloc[0]
    rows=pd.DataFrame([row,row]).reset_index(drop=True);rows.loc[1,field]=value
    if reverse:rows=rows.iloc[::-1]
    replace_rows(args,rows)
    result=normalize_dividends(*args)
    assert result['accepted']==0 and result['rejected']==2 and result['status']=='partial'
    assert result['duplicate_rows_coalesced']==0
    assert pd.read_csv(args[2]/'rejected.csv').reason.eq('conflicting duplicate distribution records').all()


def test_complementary_incomplete_versions_are_not_joined_into_invented_event(tmp_path):
    args=evidence(tmp_path);row=pd.read_csv(args[0]/'sh.600000_2018.csv',dtype=str).iloc[0]
    rows=pd.DataFrame([row,row]).reset_index(drop=True);rows.loc[0,'dividCashPsBeforeTax']='';rows.loc[1,'dividPayDate']=''
    replace_rows(args,rows);result=normalize_dividends(*args)
    assert result['accepted']==0 and result['rejected']==2


def test_identically_missing_cash_still_does_not_become_valid(tmp_path):
    args=evidence(tmp_path,dividCashPsBeforeTax='')
    frame=pd.read_csv(args[0]/'sh.600000_2018.csv',dtype=str)
    replace_rows(args,pd.concat([frame,frame],ignore_index=True))
    result=normalize_dividends(*args)
    assert result['accepted']==0 and result['rejected']==1 and result['status']=='partial'
