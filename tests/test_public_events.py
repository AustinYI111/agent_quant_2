import hashlib
import json
import shutil
import numpy as np
import pandas as pd
import pytest
from test_public_conversion import staging
from ashare_mvp.data import month_ends
from ashare_mvp.model import Config
from ashare_mvp.public_conversion import convert_public, convert_public_events, load_public_data, public_samples, run_public_research


@pytest.fixture(scope="module")
def event_data(tmp_path_factory):
    root=tmp_path_factory.mktemp("public_events")
    source,prepared,sealed=root/"source",root/"prepared",root/"sealed"
    demo,manifest=staging(source,stocks=20,periods=850)
    ex_date,pay_date=demo.calendar[400],demo.calendar[440]
    for prefix in ["prices_raw_","prices_adjusted_"]:
        name=prefix+"sh_600000.csv"; file=source/name
        frame=pd.read_csv(file,parse_dates=["date"])
        frame.loc[frame.date>=ex_date,["open","close"]]-=0.8
        frame["preclose"]=frame.close.shift().fillna(frame.close.iloc[0])
        frame.loc[frame.date==ex_date,"preclose"]-=0.8
        frame.to_csv(file,index=False)
        manifest["files"][name]["sha256"]=hashlib.sha256(file.read_bytes()).hexdigest()
    (source/"manifest.json").write_text(json.dumps(manifest))
    convert_public(source,prepared)
    frames=[]
    for file in sorted(source.glob("prices_raw_*.csv")):
        frame=pd.read_csv(file,parse_dates=["date"]).rename(columns={"code":"symbol"})
        frames.append(frame[["date","symbol","open","close","preclose","tradestatus"]])
    raw=pd.concat(frames,ignore_index=True)
    actions=pd.DataFrame([dict(symbol="sh.600000",ex_date=ex_date,available_date=demo.calendar[399],pay_date=pay_date,cash_per_share=0.8,share_multiplier=1.)])
    convert_public_events(prepared,raw,actions,sealed)
    return root,prepared,sealed,raw,actions


def test_public_event_labels_use_new_entry_entitlements(event_data):
    _,_,sealed,raw,actions=event_data
    data,samples,labels,_=public_samples(sealed,Config())
    date=month_ends(data.calendar)[(month_ends(data.calendar)>actions.ex_date.iloc[0])&(month_ends(data.calendar)<actions.pay_date.iloc[0])][0]
    result=labels[(labels.date==date)&(labels.symbol=="sh.600000")&(labels.horizon==3)].iloc[0]
    quotes=raw[raw.symbol=="sh.600000"].set_index("date")
    expected=quotes.loc[result.label_end,"open"]/quotes.loc[result.entry_date,"open"]-1
    assert result.target==pytest.approx(expected)
    index=data.prices[data.prices.symbol=="sh.600000"].set_index("date").total_return_open
    assert abs(result.target-(index.loc[result.label_end]/index.loc[result.entry_date]-1))>1e-6
    assert data.metadata["certified"] is False
    assert "market_cap" not in data.prices and "earnings_ttm" not in data.fundamentals
    _,cut,_,_=public_samples(sealed,Config(),data.calendar[420])
    assert cut.date.max()<=data.calendar[420]


def test_unreconciled_event_and_different_raw_quotes_fail_before_output(event_data,tmp_path):
    _,prepared,_,raw,actions=event_data
    wrong=actions.copy();wrong["cash_per_share"]=0.7
    with pytest.raises(ValueError,match="unreconciled"):
        convert_public_events(prepared,raw,wrong,tmp_path/"wrong_event")
    assert not (tmp_path/"wrong_event").exists()
    changed=raw.copy();changed.loc[0,"close"]+=1
    with pytest.raises(ValueError,match="differ from original"):
        convert_public_events(prepared,changed,actions,tmp_path/"wrong_quote")
    assert not (tmp_path/"wrong_quote").exists()
    blocked=raw.copy();blocked.loc[0,"tradestatus"]=0
    with pytest.raises(ValueError,match="tradability differs"):
        convert_public_events(prepared,blocked,actions,tmp_path/"wrong_state")
    assert not (tmp_path/"wrong_state").exists()


def test_event_input_tamper_rejected_before_training(event_data,tmp_path):
    _,_,sealed,_,_=event_data
    bad=tmp_path/"bad";shutil.copytree(sealed,bad)
    with (bad/"event_corporate_actions.csv").open("a") as stream:stream.write("changed")
    with pytest.raises(ValueError,match="event input hash mismatch"):
        run_public_research(bad,tmp_path/"run",Config(),exploratory=True)
    assert not (tmp_path/"run").exists()


def test_event_public_training_export_and_reload_predictions_match(event_data,tmp_path):
    from dataclasses import replace
    from ashare_mvp.model_registry import export_model,predict_model
    _,_,sealed,_,_=event_data
    full=load_public_data(sealed)
    asof=full.calendar[-3]
    assert asof not in month_ends(full.calendar)
    result=run_public_research(sealed,tmp_path/"run",Config(min_train_groups=3,n_estimators=3),asof=asof,exploratory=True)
    monthly=pd.read_csv(tmp_path/"run/monthly_top20.csv",parse_dates=["date"])
    assert asof not in set(monthly.date)
    assert result["trained_months"]>0 and result["latest_selection_count"]==20
    assert result["label_return_basis"]=="gross_new_entry_entitlements"
    assert result["account_nav_basis"]=="raw_share_account_gross_dividends"
    export_model(tmp_path/"run",tmp_path/"portable")
    data=load_public_data(sealed,asof)
    restored=predict_model(tmp_path/"portable",data,tmp_path/"prediction.csv")
    original=pd.read_csv(tmp_path/"run/latest_top20.csv")
    assert restored.symbol.tolist()==original.symbol.tolist()
    np.testing.assert_allclose(restored.score,original.score,rtol=0,atol=1e-12)
    wrong=replace(data,metadata={**data.metadata,"price_basis":"provider_adjusted_unverified"})
    with pytest.raises(ValueError,match="price basis differs"):
        predict_model(tmp_path/"portable",wrong,tmp_path/"wrong.csv")
