"""Prepare sealed whole-market public ratios without inventing valuation amounts."""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import re
import numpy as np
import pandas as pd
from .data import Dataset
from .public_data import is_ashare
from .public_conversion import _seal_public_events
from .price_action_audit import audit_price_actions


def _read(path, expected=None, allow_unverified=False, **kwargs):
    if expected is None and not allow_unverified: raise ValueError("source hash required")
    if expected is not None and not re.fullmatch(r"[0-9a-f]{64}",str(expected)): raise ValueError("invalid source hash")
    raw=Path(path).read_bytes()
    checksum=hashlib.sha256(raw).hexdigest()
    if expected is not None and checksum!=expected:
        raise ValueError(f"source hash mismatch: {Path(path).name}")
    kwargs.setdefault("low_memory",False)
    return pd.read_csv(BytesIO(raw),**kwargs),checksum


def prepare_market_ratios(market_directory, financial_directory, master_directory,
                          action_directory, out, audit_out, reference_tick_size=None,
                          code_changes=None, reference_overrides=None, non_listing_evidence=None, instrument_evidence=None):
    market,finance,master,events=map(Path,[market_directory,financial_directory,master_directory,action_directory])
    target,audit=Path(out),Path(audit_out)
    if target.resolve()==audit.resolve() or target.resolve() in audit.resolve().parents or audit.resolve() in target.resolve().parents: raise ValueError("dataset and audit outputs must be separate, non-nested directories")
    for path in [target,audit]:
        if path.exists() and any(path.iterdir()): raise ValueError("preparation outputs must be empty")
    snapshots={}
    def metadata(directory,name,key):
        raw=(directory/name).read_bytes();snapshots[key]=raw
        return json.loads(raw)
    m=metadata(market,"manifest.json","market_manifest")
    fmeta=metadata(finance,"summary.json","financial_summary")
    smeta=metadata(master,"manifest.json","master_manifest")
    emeta=metadata(events,"summary.json","action_summary")
    if m.get("schema")!="market_daily_raw_v1" or not isinstance(m.get("synthetic"),bool):
        raise ValueError("market raw schema and synthetic declaration required")
    if smeta.get("schema")!="requested_security_master_snapshot_v1":
        raise ValueError("sealed security-master snapshot required")
    calendar,_=_read(market/"calendar.csv",m["files"]["calendar.csv"]["sha256"],dtype=str)
    cal=pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_trading_day.eq("1"),"calendar_date"]))
    if cal.empty or cal.hasnans or cal.has_duplicates or not cal.is_monotonic_increasing or cal.tz is not None or not cal.equals(cal.normalize()):
        raise ValueError("ordered normalized exchange sessions required")
    if cal.min()<pd.Timestamp(m["start"]) or cal.max()>pd.Timestamp(m["end"]):
        raise ValueError("exchange calendar outside market request")
    stocks,master_hash=_read(master/"security_master.csv",smeta["master_sha256"],dtype=str)
    requested,targets_hash=_read(master/"input_targets.csv",smeta["targets_sha256"],dtype=str)
    if not {"code","ipoDate","outDate","type"}.issubset(stocks) or stocks.code.isna().any() or stocks.code.duplicated().any() or requested.code.isna().any() or requested.code.duplicated().any():
        raise ValueError("ambiguous security-master keys/schema")
    fundamentals,financial_hash=_read(finance/"financial_panel.csv",fmeta["panel_sha256"],dtype={"symbol":str})
    fields=["symbol","report_date","available_date","publication_date","provider_update_date","roe","revenue_growth","earnings_growth","debt_ratio"]
    if not set(fields).issubset(fundamentals): raise ValueError("financial ratios or provenance clocks missing")
    for col in ["report_date","available_date","publication_date","provider_update_date","provider_ingest_date","earnings_available_date","balance_available_date"]:
        if col not in fundamentals: continue
        fundamentals[col]=pd.to_datetime(fundamentals[col])
        known=fundamentals[col].dropna()
        if known.dt.tz is not None or not known.eq(known.dt.normalize()).all(): raise ValueError("normalized financial clocks required")
    if fundamentals[["symbol","report_date","available_date","publication_date","provider_update_date"]].isna().any().any():
        raise ValueError("visible financial records require complete publication clocks")
    if fundamentals.duplicated(["symbol","report_date","available_date"]).any() or fundamentals.available_date.le(fundamentals.report_date).any():
        raise ValueError("ambiguous or premature financial availability")
    for col in ["publication_date","provider_update_date","provider_ingest_date"]:
        if col in fundamentals and (fundamentals[col]>=fundamentals.available_date).any(): raise ValueError("financial publication must precede availability session")
    within=fundamentals.available_date.between(cal.min(),cal.max())
    if not fundamentals.loc[within,"available_date"].isin(cal).all(): raise ValueError("financial availability outside exchange sessions")
    for col in ["earnings_available_date","balance_available_date"]:
        if col in fundamentals and (fundamentals[col]>fundamentals.available_date).any(): raise ValueError("future financial dependency")
    for col in ["roe","revenue_growth","earnings_growth","debt_ratio"]:
        fundamentals[col]=pd.to_numeric(fundamentals[col],errors="raise")
        if np.isinf(fundamentals[col]).any(): raise ValueError("infinite financial ratio")
    actions,action_hash=_read(events/"actions.csv",emeta["actions_sha256"],dtype={"symbol":str},parse_dates=["ex_date","available_date","pay_date"])
    coverage,coverage_hash=_read(events/"coverage.csv",emeta.get("coverage_sha256"),allow_unverified="coverage_sha256" not in emeta,dtype=str)
    if not {"security","status"}.issubset(coverage): raise ValueError("event coverage schema missing")
    observed=coverage.status.value_counts().to_dict()
    if observed!=emeta["status_counts"] or len(actions)!=emeta["matched_events"]:
        raise ValueError("event coverage disagrees with summary")
    if pd.Timestamp(emeta["evaluation_start"])>cal.min() or pd.Timestamp(emeta["evaluation_end"])<cal.max():
        raise ValueError("event evidence does not cover requested calendar")
    for label,source_metadata,directory in [('event',emeta,events),('financial',fmeta,finance)]:
        if source_metadata.get('code_changes_sha256') is None:continue
        identity_raw=(directory/'input_code_changes.json').read_bytes()
        if hashlib.sha256(identity_raw).hexdigest()!=source_metadata['code_changes_sha256']:
            raise ValueError(f'{label} code-change evidence hash mismatch')
        from .security_identity import code_change_payload
        if code_changes is None or hashlib.sha256(code_change_payload(code_changes).encode()).hexdigest()!=source_metadata['code_changes_sha256']:
            raise ValueError(f'matching {label} code-change policy required')
        snapshots[label+'_code_changes']=identity_raw
    scope_exclusions,scope_policy=set(),None
    if non_listing_evidence is not None:
        from .universe_evidence import reviewed_non_listing_bounds
        scope_exclusions,scope_policy=reviewed_non_listing_bounds(non_listing_evidence,cal.max(),requested.code,stocks)
    instrument_audit,instrument_policy=None,None
    if instrument_evidence is not None:
        from .instrument_scope import classify_instrument_scope
        coverage,instrument_audit,instrument_policy=classify_instrument_scope(coverage,actions,instrument_evidence,cal)
    for key,frame in [("code_changes",code_changes),("reference_overrides",reference_overrides),("non_listing_evidence",non_listing_evidence),("instrument_evidence",instrument_evidence)]:
        if frame is not None: snapshots[key]=frame.to_json(orient="records",date_format="iso").encode()
    hashes={key:hashlib.sha256(raw).hexdigest() for key,raw in snapshots.items()}
    hashes.update(master=master_hash,targets=targets_hash,financial_panel=financial_hash,actions=action_hash,event_coverage=coverage_hash)
    issues=[]
    def issue(kind,symbol=None,date=None,count=1,detail=None):
        issues.append(dict(issue=kind,symbol=symbol,date=date,count=int(count),detail=detail))
    for date in cal:
        if f"daily_{date:%Y%m%d}.csv" not in m["files"]: issue("missing_daily_cross_section",date=date)
    for code in sorted({c for c in requested.code if is_ashare(c)}-set(stocks.code)-scope_exclusions): issue("unresolved_security_identity",symbol=code)
    def event_code(value):
        match=re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)",str(value))
        return f"{match[2].lower()}.{match[1]}" if match else None
    if emeta.get("partial_market_source",True): issue("incomplete_event_plan_source")
    for row in coverage[coverage.status.isin(["missing_payment_evidence","unresolved"])].itertuples():
        code=event_code(row.security)
        if code and is_ashare(code): issue("unresolved_event_evidence",symbol=code,detail=row.status)
        elif code is None or code.startswith(("sh.","sz.")):
            execution=pd.to_datetime(getattr(row,"ex_date",None),errors="coerce")
            if pd.isna(execution) or cal.min()<=execution<=cal.max():
                issue("unresolved_event_identity",symbol=code or str(row.security),detail=row.status)
    audit.mkdir(parents=True,exist_ok=True)
    report={"status":"blocked","validation_stage":"source_inventory","synthetic":m["synthetic"],"certified":False,"model_ready":False,
            "source_hashes":hashes,"requested_sessions":len(cal),"listed_daily_files":sum(f"daily_{d:%Y%m%d}.csv" in m["files"] for d in cal),
            "financial_partial_source":bool(fmeta.get("partial_source",True)),"raw_files_hash_checked":0,"not_implemented_plans_ignored":int(coverage.status.eq("not_implemented").sum()),"event_coverage_prior_hash_verified":"coverage_sha256" in emeta}
    report["reviewed_non_listing_policy"]=scope_policy
    report["instrument_scope_policy"]=instrument_policy
    if instrument_audit is not None:
        instrument_audit.to_csv(audit/"instrument_scope.csv",index=False)
        report["instrument_scope_audit_sha256"]=hashlib.sha256((audit/"instrument_scope.csv").read_bytes()).hexdigest()
    for key,raw in snapshots.items(): (audit/f"input_{key}.json").write_bytes(raw)
    def checkpoint():
        pd.DataFrame(issues,columns=["issue","symbol","date","count","detail"]).to_csv(audit/"blocking_issues.csv",index=False)
        report["issue_counts"]={kind:sum(x["count"] for x in issues if x["issue"]==kind) for kind in sorted({x["issue"] for x in issues})}
        (audit/"readiness.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    checkpoint()
    if issues: return report
    stocks=stocks[stocks.code.map(is_ashare)&stocks.type.eq("1")].copy()
    stocks["listing_date"]=pd.to_datetime(stocks.ipoDate)
    stocks["terminal_date"]=pd.to_datetime(stocks.outDate.replace("",pd.NA))
    if stocks.listing_date.isna().any() or stocks.terminal_date.lt(stocks.listing_date).any(): raise ValueError("invalid security lifetimes")
    active=stocks[stocks.listing_date.le(cal.max())&(stocks.terminal_date.isna()|stocks.terminal_date.ge(cal.min()))]
    frames=[];excluded=0
    for date in cal:
        name=f"daily_{date:%Y%m%d}.csv";p,_=_read(market/name,m["files"][name]["sha256"],dtype=str)
        if len(p)!=m["files"][name]["rows"]: raise ValueError("market daily row count mismatch")
        fields={"date","code","open","close","preclose","volume","adjustflag","tradestatus","isST","peTTM","pbMRQ"}
        if not fields.issubset(p) or p.code.isna().any() or p.code.duplicated().any() or not pd.to_datetime(p.date).eq(date).all(): raise ValueError("market daily keys/schema invalid")
        if not p.adjustflag.eq("3").all() or not p.tradestatus.isin(["0","1"]).all() or not p.isST.isin(["0","1"]).all(): raise ValueError("market daily historical state/basis invalid")
        selected=p.code.map(is_ashare);excluded+=int((~selected).sum());p=p[selected].rename(columns={"code":"symbol"})
        p["date"]=pd.to_datetime(p.date)
        for col in ["open","close","preclose","volume","peTTM","pbMRQ"]:
            p[col]=pd.to_numeric(p[col],errors="raise")
            if np.isinf(p[col]).any(): raise ValueError("infinite market value")
        if p.symbol.isin(scope_exclusions).any(): raise ValueError("observed quote contradicts reviewed non-listing bound")
        p["listing_date"]=p.symbol.map(stocks.set_index("code").listing_date)
        if p.listing_date.isna().any(): raise ValueError("observed security has no verified stock identity")
        p["raw_open"],p["raw_close"]=p.open,p.close
        p["total_return_open"]=p.open
        p["can_buy"]=p["can_sell"]=p.tradestatus.eq("1")&p.open.gt(0)
        p["is_st"]=p.isST.eq("1");p["eligible"]=~p.is_st&p.close.gt(0)
        for source,dest in [("peTTM","earnings_yield"),("pbMRQ","book_yield")]:p[dest]=1/p[source].where(p[source].ne(0))
        frames.append(p);report["raw_files_hash_checked"]+=1
    prices=pd.concat(frames,ignore_index=True)
    metadata=dict(source="verified market daily, financial dependency and lifetime snapshots",synthetic=m["synthetic"],schema="public_ratios_v1",certified=False,
                  point_in_time_status="provider_dates_unverified_versions",price_basis="raw_pending_event_reconstruction",missing_policy="native_nan",
                  universe_policy="all supported Shanghai/Shenzhen A-share lifetime records in supplied request; dated ST excluded only from candidates",
                  availability_policy="normalized publication/dependency clocks; original vintages unverified",execution_policy="non-suspended positive open; limit states unverified",
                  blockers=["Original financial vintages, event completeness, terminal settlement and executable fills remain unverified"],input_hashes=hashes,
                  financial_partial_source=report["financial_partial_source"])
    if scope_policy: metadata["reviewed_non_listing_policy"]=scope_policy
    if instrument_policy: metadata["instrument_scope_policy"]=instrument_policy
    data=Dataset(prices,fundamentals,cal,metadata)
    roots={}
    if code_changes is not None:
        from .security_identity import apply_identity_dataset,code_change_maps,canonical_event_symbols
        roots,_,_=code_change_maps(code_changes)
        data=apply_identity_dataset(data,code_changes)
        actions=canonical_event_symbols(actions,code_changes)
    # Apply the existing dated code policy before validating active values.
    # Providers may backfill an inactive new alias with a blank volume; it is
    # quarantined by identity evidence, never repaired or used for selection.
    value_fields=["open","close","preclose","volume"]
    missing_values=data.prices[value_fields].isna()
    nontrading_unknown_volume=missing_values.volume & data.prices.tradestatus.eq("0")
    # A declared nontrading session needs a valid valuation, not invented volume.
    # Retain its NaN volume and prohibit execution through the existing status.
    missing_values.loc[nontrading_unknown_volume,"volume"]=False
    volume_audit_columns=[col for col in ["date","symbol","trading_symbol","tradestatus","volume"] if col in data.prices]
    data.prices.loc[nontrading_unknown_volume,volume_audit_columns].to_csv(audit/"nontrading_unknown_volume.csv",index=False)
    report["nontrading_unknown_volume_rows"]=int(nontrading_unknown_volume.sum())
    report["nontrading_unknown_volume_sha256"]=hashlib.sha256((audit/"nontrading_unknown_volume.csv").read_bytes()).hexdigest()
    metadata_policy="NaN volume retained only with explicit tradestatus=0; no imputation; candidate volume filter and execution status still apply"
    data.metadata["nontrading_volume_policy"]=metadata_policy
    negative_values=data.prices[value_fields].lt(0)
    for mask,kind in [(missing_values,"missing_active_market_value"),(negative_values,"negative_active_market_value")]:
        for index in mask.index[mask.any(axis=1)]:
            row=data.prices.loc[index]
            fields=",".join(mask.columns[mask.loc[index]])
            source=row.get("trading_symbol",row.symbol)
            issue(kind,symbol=row.symbol,date=row.date,detail="trading_symbol="+str(source)+"; fields="+fields)
    report["quarantined_quote_rows"]=data.metadata.get("security_identity_policy",{}).get("quarantined_quote_rows",0)
    if issues:
        report.update(validation_stage="active_price_values",price_rows=len(data.prices),symbols=int(data.prices.symbol.nunique()))
        checkpoint()
        return report
    if reference_overrides is not None:
        from .reference_overrides import apply_reference_overrides
        # Normalize reviewed event keys to the same verified issuer identity.
        if code_changes is not None:
            from .security_identity import canonical_event_symbols
            reference_overrides=canonical_event_symbols(reference_overrides,code_changes)
        actions=apply_reference_overrides(actions,reference_overrides,cal)
    active=active.copy();active["symbol"]=active.code.map(lambda x:roots.get(x,x))
    listing=active.groupby("symbol").listing_date.min()
    from .lifetime_coverage import audit_lifetime_coverage
    lifetime_rows,lifetime_summary=audit_lifetime_coverage(data.prices,active,cal)
    lifetime_path=audit/"lifetime_coverage.csv"
    lifetime_rows.to_csv(lifetime_path,index=False)
    report["lifetime_coverage"]={**lifetime_summary,"file":lifetime_path.name,
        "sha256":hashlib.sha256(lifetime_path.read_bytes()).hexdigest()}
    data.prices["listing_date"]=data.prices.symbol.map(listing)
    for symbol in sorted(set(listing.index)-set(data.prices.symbol)): issue("missing_security_history",symbol=symbol)
    for symbol,p in data.prices.groupby("symbol",sort=False):
        expected=cal[cal>=listing.get(symbol,p.date.min())]
        missing=expected.difference(pd.DatetimeIndex(p.date))
        if len(missing):issue("missing_sessions_or_terminal_valuation",symbol=symbol,date=missing[0],count=len(missing))
        if p.date.lt(listing.get(symbol,p.date.min())).any():issue("quote_before_listing",symbol=symbol)
    data.fundamentals=data.fundamentals[data.fundamentals.symbol.isin(data.prices.symbol.unique())].copy()
    actions=actions[actions.symbol.isin(data.prices.symbol.unique())].copy()
    if data.fundamentals.empty:issue("no_matching_financial_events")
    discontinuities,price_audit=audit_price_actions(data.prices,actions,cal,reference_tick_size)
    discontinuities.to_csv(audit/"price_reference_issues.csv",index=False)
    for row in discontinuities[discontinuities.issue.ne("missing_sessions_or_terminal_valuation")].itertuples():issue(row.issue,symbol=row.symbol,date=row.date,count=row.count)
    report.update(validation_stage="price_and_lifetime_coverage",price_audit=price_audit,excluded_non_ashare_quote_rows=excluded,
                  price_rows=len(data.prices),symbols=int(data.prices.symbol.nunique()))
    checkpoint()
    if issues:return report
    parent=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    try:
        _seal_public_events(data,data.prices.copy(),actions,target,reference_tick_size,parent)
    except ValueError as exc:
        issue("event_reconstruction_failed",detail=str(exc))
        report["validation_stage"]="event_reconstruction"
        checkpoint()
        return report
    report.update(status="prepared_exploratory",validation_stage="event_reconstruction_completed",dataset_metadata_sha256=hashlib.sha256((target/"metadata.json").read_bytes()).hexdigest())
    checkpoint()
    return report
