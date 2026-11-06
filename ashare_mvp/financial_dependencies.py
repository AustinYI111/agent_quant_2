"""Combine same-period financial dependencies without backfilling later revisions."""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import pandas as pd


def _coalesce_verified_alias_versions(frame, source_name):
    """Only equal payloads from different proven aliases form one version."""
    keys=['symbol','report_date','available_date']
    visible=frame.symbol.notna() & frame.available_date.notna()
    duplicated=visible & frame.duplicated(keys,keep=False)
    if not duplicated.any():return frame,[]
    identity_columns={source_name,source_name+'s'}
    payload=[column for column in frame if column not in identity_columns]
    merged,audit=[],[]
    for key,group in frame.loc[duplicated].groupby(keys,sort=False):
        if group[source_name].nunique()!=len(group) or len(group[payload].drop_duplicates())!=1:
            raise ValueError('ambiguous financial dependency version')
        aliases=json.dumps(sorted(group[source_name].tolist()))
        row=group.iloc[0].copy();row[source_name]=None;row[source_name+'s']=aliases;merged.append(row)
        audit.append(dict(dependency=source_name.removesuffix('_provider_symbol'),symbol=key[0],report_date=key[1],available_date=key[2],provider_symbols=aliases,duplicate_rows_removed=len(group)-1))
    return pd.concat([frame.loc[~duplicated],pd.DataFrame(merged)],ignore_index=True),audit


def combine_financial_dependencies(earnings, balances, code_changes=None):
    keys = ["symbol", "report_date"]
    earnings, balances = earnings.copy(), balances.copy()
    alias_audit=[];normalized=[]
    if code_changes is not None:
        from .security_identity import code_change_maps
        roots,_,_=code_change_maps(code_changes)
        for frame,source_name in [(earnings,'earnings_provider_symbol'),(balances,'balance_provider_symbol')]:
            if source_name in frame:raise ValueError('financial dependency already contains identity mapping')
            frame[source_name]=frame.symbol
            frame[source_name+'s']=frame.symbol.map(lambda symbol:json.dumps([symbol]) if pd.notna(symbol) else None)
            frame['symbol']=frame.symbol.map(lambda symbol:roots.get(symbol,symbol))
    for frame,source_name in [(earnings,"earnings_provider_symbol"),(balances,"balance_provider_symbol")]:
        if not set(keys + ["available_date"]).issubset(frame):
            raise ValueError("financial dependency keys missing")
        for col in ["report_date", "available_date"]:
            frame[col] = pd.to_datetime(frame[col])
            if frame[col].dt.tz is not None or not frame[col].dropna().eq(frame[col].dropna().dt.normalize()).all():
                raise ValueError("normalized Shanghai financial dates required")
        visible = frame[frame.symbol.notna() & frame.available_date.notna()]
        if visible[keys].isna().any().any() or (code_changes is None and visible.duplicated(keys + ["available_date"]).any()):
            raise ValueError("ambiguous financial dependency version")
        if frame.available_date.dropna().le(frame.loc[frame.available_date.notna(), "report_date"]).any():
            raise ValueError("dependency visible before report end")
        if code_changes is not None:
            frame,coalesced=_coalesce_verified_alias_versions(frame,source_name)
            alias_audit.extend(coalesced)
        normalized.append(frame)
    earnings,balances=normalized
    earnings = earnings[earnings.symbol.notna() & earnings.available_date.notna()]
    balances = balances[balances.symbol.notna() & balances.available_date.notna()]
    if earnings.empty:
        raise ValueError("no visible earnings records")
    events = pd.concat([earnings[keys + ["available_date"]], balances[keys + ["available_date"]]]).drop_duplicates()
    events = events.sort_values("available_date")
    income = earnings.drop(columns=["debt_ratio"], errors="ignore").rename(columns={"available_date": "earnings_available_date"})
    panel = pd.merge_asof(events, income.sort_values("earnings_available_date"), by=keys,
                          left_on="available_date", right_on="earnings_available_date", direction="backward")
    panel = panel[panel.earnings_available_date.notna()].copy()
    if balances.empty:
        panel["balance_available_date"] = pd.NaT
        panel["debt_ratio"] = float("nan")
    else:
        debt_columns=keys+["available_date","debt_ratio"]+(["balance_provider_symbol","balance_provider_symbols"] if "balance_provider_symbol" in balances else [])
        if 'first_observed_at_utc' in balances:debt_columns+=['first_observed_at_utc']
        debt = balances[debt_columns].rename(columns={"available_date": "balance_available_date",'first_observed_at_utc':'balance_first_observed_at_utc'})
        panel = pd.merge_asof(panel.sort_values("available_date"), debt.sort_values("balance_available_date"), by=keys,
                              left_on="available_date", right_on="balance_available_date", direction="backward")
    if not panel.earnings_available_date.le(panel.available_date).all() or not panel.balance_available_date.dropna().le(panel.loc[panel.balance_available_date.notna(), "available_date"]).all():
        raise AssertionError("future financial dependency")
    result=panel.sort_values(keys + ["available_date"]).reset_index(drop=True)
    if 'first_observed_at_utc' in earnings or 'first_observed_at_utc' in balances:
        missing=pd.Series(pd.NaT,index=result.index,dtype='datetime64[ns, UTC]')
        income_clock=pd.to_datetime(result['first_observed_at_utc'],utc=True) if 'first_observed_at_utc' in result else missing.copy()
        balance_clock=pd.to_datetime(result['balance_first_observed_at_utc'],utc=True) if 'balance_first_observed_at_utc' in result else missing.copy()
        result['earnings_first_observed_at_utc']=income_clock
        result['balance_first_observed_at_utc']=balance_clock
        known=income_clock.notna()&(~result.balance_available_date.notna()|balance_clock.notna())
        result['first_observed_at_utc']=pd.concat([income_clock,balance_clock],axis=1).max(axis=1).where(known)
    result.attrs["identity_alias_coalescing"]=alias_audit
    return result


def combine_financial_files(earnings_directory, balance_directory, out, code_changes=None):
    target = Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("combined financial output must be empty")
    panels, sources = [], []
    for directory, filename in [(earnings_directory, "financial_panel.csv"), (balance_directory, "balance_panel.csv")]:
        path = Path(directory)
        summary_bytes = (path / "summary.json").read_bytes()
        summary = json.loads(summary_bytes)
        raw = (path / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != summary.get("panel_sha256"):
            raise ValueError("financial dependency panel hash mismatch")
        panels.append(pd.read_csv(BytesIO(raw), dtype={"symbol": str}, parse_dates=["report_date", "available_date"]))
        sources.append(dict(directory=str(path), summary_sha256=hashlib.sha256(summary_bytes).hexdigest(), summary=summary))
    panel = combine_financial_dependencies(*panels,code_changes=code_changes)
    target.mkdir(parents=True, exist_ok=True)
    panel.to_csv(target / "financial_panel.csv", index=False)
    result = dict(records=len(panel), symbols=int(panel.symbol.nunique()), debt_records=int(panel.debt_ratio.notna().sum()),
                  partial_source=any(s["summary"]["partial_source"] for s in sources), certified=False, model_ready=False,
                  sources=sources, excluded_unidentified_or_invisible={name:int((frame.symbol.isna() | frame.available_date.isna()).sum()) for name,frame in zip(["earnings", "balances"],panels)}, panel_sha256=hashlib.sha256((target / "financial_panel.csv").read_bytes()).hexdigest(),
                  limitations=["Same-period native missing dependencies retained", "Original provider financial vintages remain unverified"])
    if code_changes is not None:
        from .security_identity import code_change_payload
        payload=code_change_payload(code_changes)
        (target/'input_code_changes.json').write_text(payload,encoding='utf-8')
        result['code_changes_sha256']=hashlib.sha256(payload.encode()).hexdigest()
        audit_columns=['dependency','symbol','report_date','available_date','provider_symbols','duplicate_rows_removed']
        alias_audit=pd.DataFrame(panel.attrs.get('identity_alias_coalescing',[]),columns=audit_columns)
        alias_audit.to_csv(target/'identity_alias_coalescing.csv',index=False)
        result['alias_coalesced_versions']=len(alias_audit)
        result['alias_duplicate_rows_removed']=int(alias_audit.duplicate_rows_removed.sum())
        result['alias_coalescing_sha256']=hashlib.sha256((target/'identity_alias_coalescing.csv').read_bytes()).hexdigest()
        result['financial_identity_policy']='same verified issuer before joining; original provider symbols and dependency clocks retained'
    (target / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
