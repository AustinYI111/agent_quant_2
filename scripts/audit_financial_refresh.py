"""Independently select financial dependencies using version indexes, not joins."""
from pathlib import Path
from datetime import datetime,timezone
from bisect import bisect_right
import argparse,hashlib,json,math
import pandas as pd
from ashare_mvp.workspace_paths import WorkspacePaths
from ashare_mvp.phase2_protocol import sha256


def audit(project,work_root,refresh,inputs,out):
    paths=WorkspacePaths(project,work_root);source=paths.resolve(refresh);config_file=paths.resolve(inputs);target=paths.resolve(out)
    if target.exists() or not target.is_relative_to(paths.work) or target.is_relative_to(source) or source.is_relative_to(target):raise ValueError('fresh separate financial audit required')
    proof=json.loads((source/'refresh.json').read_text(encoding='utf8'));recipe=json.loads(config_file.read_text(encoding='utf8'))
    assert proof['status']=='dependencies_refreshed_before_join'
    assert proof['source_hashes'][paths.name(config_file)]==sha256(config_file)
    for name,digest in proof['source_hashes'].items():assert sha256(paths.resolve(name))==digest
    for name,digest in proof['output_hashes'].items():assert sha256(paths.resolve(paths.name(source)+'/'+name))==digest
    for name,digest in proof['code_hashes'].items():
        actual=hashlib.sha256((paths.project/'ashare_mvp'/name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest();assert actual==digest
    changes=pd.read_json(paths.resolve(recipe['code_changes']),convert_dates=False);parents=dict(zip(changes.new_symbol,changes.old_symbol))
    def canonical(symbol):
        visited=set()
        while symbol in parents:
            assert symbol not in visited;visited.add(symbol);symbol=parents[symbol]
        return symbol
    families={};columns={'earnings':['roe','revenue_growth','earnings_growth'],'balances':['debt_ratio']}
    for family,file in [('earnings','financial_panel.csv'),('balances','balance_panel.csv')]:
        frames=[pd.read_csv(paths.resolve(row[family])/file,float_precision='round_trip') for row in recipe['history']]
        frames.append(pd.read_csv(source/'normalized'/family/file,float_precision='round_trip'))
        raw=pd.concat(frames,ignore_index=True)
        for name in ['report_date','available_date']:raw[name]=pd.to_datetime(raw[name],format='mixed')
        raw['first_observed_at_utc']=pd.to_datetime(raw.first_observed_at_utc,utc=True,format='mixed')
        raw=raw[raw.symbol.notna()&raw.available_date.notna()]
        assert raw.first_observed_at_utc.notna().all() and raw.first_observed_at_utc.le(pd.Timestamp.now(tz='UTC')).all()
        assert raw.available_date.ge(raw.first_observed_at_utc.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()).all()
        # First choose the latest actual observation at each provider-key date.
        latest={}
        for r in raw.to_dict('records'):
            key=(r['symbol'],r['report_date'],r['available_date']);old=latest.get(key)
            if old is None or r['first_observed_at_utc']>old['first_observed_at_utc']:latest[key]=r
            elif r['first_observed_at_utc']==old['first_observed_at_utc']:
                for c in columns[family]:assert r[c]==old[c] or pd.isna(r[c]) and pd.isna(old[c])
        mapped={}
        for r in latest.values():
            key=(canonical(r['symbol']),r['report_date'],r['available_date']);old=mapped.get(key)
            if old is not None:
                assert old['first_observed_at_utc']==r['first_observed_at_utc']
                for c in columns[family]:assert r[c]==old[c] or pd.isna(r[c]) and pd.isna(old[c])
            else:mapped[key]=r
        index={}
        for (symbol,period,available),r in mapped.items():index.setdefault((symbol,period),[]).append((available,r))
        families[family]={key:(sorted(v,key=lambda p:p[0])) for key,v in index.items()}
    expected=set()
    for key,income in families['earnings'].items():
        dates={d for d,r in income}|{d for d,r in families['balances'].get(key,[])}
        for d in dates:
            if d>=income[0][0]:expected.add((*key,d))
    panel=pd.read_csv(source/'history/combined/financial_panel.csv',float_precision='round_trip')
    for c in ['report_date','available_date','earnings_available_date','balance_available_date']:panel[c]=pd.to_datetime(panel[c])
    for c in ['first_observed_at_utc','earnings_first_observed_at_utc','balance_first_observed_at_utc']:panel[c]=pd.to_datetime(panel[c],utc=True)
    actual=set(zip(panel.symbol,panel.report_date,panel.available_date))
    assert len(actual)==len(panel) and actual==expected
    maximum=0.;missing_balance=0
    for r in panel.to_dict('records'):
        key=(r['symbol'],r['report_date']);selected={}
        for family in ['earnings','balances']:
            versions=families[family].get(key,[]);dates=[d for d,item in versions];ix=bisect_right(dates,r['available_date'])-1
            selected[family]=None if ix<0 else versions[ix][1]
        income=selected['earnings'];debt=selected['balances'];assert income is not None
        assert income['available_date']==r['earnings_available_date'] and income['first_observed_at_utc']==r['earnings_first_observed_at_utc']
        if debt is None:
            missing_balance+=1;assert pd.isna(r['balance_available_date']) and pd.isna(r['debt_ratio']) and pd.isna(r['balance_first_observed_at_utc'])
        else:assert debt['available_date']==r['balance_available_date'] and debt['first_observed_at_utc']==r['balance_first_observed_at_utc']
        observed=max(income['first_observed_at_utc'],debt['first_observed_at_utc']) if debt else income['first_observed_at_utc']
        assert observed==r['first_observed_at_utc']
        for family,fields in columns.items():
            item=selected[family]
            for c in fields:
                wanted=float(item[c]) if item is not None else math.nan;got=float(r[c])
                if math.isnan(wanted):assert math.isnan(got)
                else:
                    error=abs(wanted-got);assert math.isfinite(wanted) and math.isfinite(got) and error<=1e-12;maximum=max(maximum,error)
    raw_counts={}
    for family in ['earnings','balances']:
        m=json.loads((source/'raw'/family/'manifest.json').read_text(encoding='utf8'))
        raw_counts[family]={period:dict(rows=info['rows'],pages=info['pages'],observed_at_utc=info.get('complete_observed_at_utc',info.get('retrieved_at'))) for period,info in m['quarters'].items()}
    result=dict(schema='independent_observed_financial_refresh_audit_v1',status='all_combined_versions_independently_selected',audited_at_utc=datetime.now(timezone.utc).isoformat(),
                refresh_manifest_sha256=sha256(source/'refresh.json'),records=len(panel),symbols=int(panel.symbol.nunique()),debt_records=int(panel.debt_ratio.notna().sum()),
                versions_without_balance_dependency=missing_balance,maximum_factor_value_error=maximum,raw_requests=raw_counts,
                versions_visible_by_signal=int(panel.available_date.le(proof['signal_date']).sum()),versions_available_after_signal=int(panel.available_date.gt(proof['signal_date']).sum()),
                verification_code_sha256=sha256(Path(__file__)),prediction_generated=False,model_selection_performed=False,
                limitations=['Checks all four financial factor values, exact same-period version keys and dependency/observation clocks; not every descriptive source column.',
                             'Does not independently certify original supplier vintages, audited accounting figures or all-issuer reporting coverage.'])
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(result,ensure_ascii=False));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['project','work-root','refresh','inputs','out']:parser.add_argument('--'+name,required=True)
    audit(**vars(parser.parse_args()))
