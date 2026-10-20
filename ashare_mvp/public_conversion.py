"""Auditable public ratios adapter. Never certifies unarchived financial vintages."""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

from .data import Dataset, month_ends, fundamentals_asof
from .factors import default_library, technical_panel
from .pipeline import forward_labels
from .public_data import is_ashare


def _verified_read(path, manifest, name):
    if Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("public file name escapes staging directory")
    if name not in manifest["files"]:
        raise ValueError(f"missing verified public file: {name}")
    file = path / name
    if hashlib.sha256(file.read_bytes()).hexdigest() != manifest["files"][name]["sha256"]:
        raise ValueError(f"public input hash mismatch: {name}")
    return pd.read_csv(file, dtype=str)


def _numeric(series):
    result = pd.to_numeric(series.replace("", np.nan), errors="raise")
    if np.isinf(result).any():
        raise ValueError("infinite public numeric value")
    return result


def convert_public(directory, out):
    """Normalize without inventing absolute market cap/equity or missing ratios.

    Inputs with missing daily coverage fail: no disappearance of delisted assets.
    Available dates conservatively use the next session after all dependencies.
    The provider's original-vs-revised vintage remains explicitly unverified.
    """
    path, target = Path(directory), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("public conversion output must be empty")
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    symbols = manifest.get("downloaded_symbols", [])
    if not symbols:
        raise ValueError("no downloaded price symbols in public input")
    if not all(isinstance(s, str) and is_ashare(s) for s in symbols) or len(symbols) != len(set(symbols)):
        raise ValueError("invalid public symbol selection")
    if not isinstance(manifest.get("synthetic"), bool):
        raise ValueError("public manifest must declare synthetic boolean")
    calendar_raw = _verified_read(path, manifest, "trade_dates.csv")
    calendar = pd.DatetimeIndex(pd.to_datetime(calendar_raw.loc[calendar_raw.is_trading_day == "1", "calendar_date"]))
    if calendar.has_duplicates or calendar.hasnans or not calendar.is_monotonic_increasing:
        raise ValueError("invalid public exchange calendar")
    prices, financials, missing, coverage = [], [], [], []
    for symbol in symbols:
        key = symbol.replace(".", "_")
        basic = _verified_read(path, manifest, f"basic_{key}.csv")
        if len(basic) != 1 or basic.code.iloc[0] != symbol:
            raise ValueError(f"{symbol}: ambiguous security basic record")
        listing = pd.Timestamp(basic.ipoDate.iloc[0])
        if pd.isna(listing):
            raise ValueError(f"{symbol}: missing listing date")
        raw = _verified_read(path, manifest, f"prices_raw_{key}.csv")
        adjusted = _verified_read(path, manifest, f"prices_adjusted_{key}.csv")
        required = {"date", "code", "open", "close", "preclose", "volume", "tradestatus", "isST", "peTTM", "pbMRQ"}
        if not required.issubset(raw) or not required.issubset(adjusted):
            raise ValueError(f"{symbol}: incomplete daily schema")
        for frame in [raw, adjusted]:
            frame["date"] = pd.to_datetime(frame.date, errors="raise")
            if frame.date.isna().any() or frame.date.duplicated().any() or not frame.code.eq(symbol).all():
                raise ValueError(f"{symbol}: ambiguous price keys")
            frame.sort_values("date", inplace=True)
        expected = calendar[calendar >= listing]
        for label, frame in [("raw", raw), ("adjusted", adjusted)]:
            if not pd.DatetimeIndex(frame.date).equals(expected):
                coverage.append(dict(symbol=symbol, basis=label, expected=len(expected), observed=len(frame),
                                     missing_sessions=int((~expected.isin(frame.date)).sum())))
        if coverage:
            continue
        p = adjusted[["date"]].copy()
        p["symbol"] = symbol
        p["close"] = _numeric(adjusted.close)
        p["raw_close"] = _numeric(raw.close)
        p["raw_open"] = _numeric(raw.open)
        p["volume"] = _numeric(raw.volume)
        status = raw.tradestatus
        if not status.isin(["0", "1"]).all() or not raw.isST.isin(["0", "1"]).all():
            raise ValueError(f"{symbol}: invalid historical status")
        adjusted_open = _numeric(adjusted.open)
        adjusted_preclose = _numeric(adjusted.preclose)
        p["total_return_open"] = adjusted_open.where(adjusted_open > 0, adjusted_preclose)
        p["can_buy"] = p["can_sell"] = status.eq("1") & p.raw_open.gt(0)
        p["is_st"] = raw.isST.eq("1")
        p["eligible"] = ~p.is_st & p.close.gt(0)
        p["listing_date"] = listing
        # The provider publishes historical valuation ratios. Do not manufacture
        # absolute capitalization or financial statement equity from them.
        for source, destination in [("peTTM", "earnings_yield"), ("pbMRQ", "book_yield")]:
            value = _numeric(raw[source])
            p[destination] = 1.0 / value.where(value != 0)
        if p[["close", "raw_close", "volume", "total_return_open"]].isna().any().any():
            raise ValueError(f"{symbol}: missing daily price/valuation data")
        if (p[["close", "raw_close", "volume", "total_return_open"]] < 0).any().any():
            raise ValueError(f"{symbol}: negative price/volume")
        prices.append(p)
        profit_rows = []
        for name in manifest["files"]:
            if name.startswith(f"profit_{key}_") and name.endswith(".csv"):
                f = _verified_read(path, manifest, name)
                if len(f):
                    f["origin_file"] = name
                    profit_rows.append(f)
        if not profit_rows:
            missing.append(dict(symbol=symbol, issue="no_profit_reports"))
            continue
        profit = pd.concat(profit_rows, ignore_index=True)
        if not {"code", "statDate", "pubDate", "roeAvg", "MBRevenue"}.issubset(profit):
            raise ValueError(f"{symbol}: incomplete profit schema")
        if not profit.code.eq(symbol).all() or profit.duplicated(["statDate", "pubDate"]).any():
            raise ValueError(f"{symbol}: ambiguous profit records")
        profit["statDate"] = pd.to_datetime(profit.statDate)
        profit["pubDate"] = pd.to_datetime(profit.pubDate)
        profit["MBRevenue"] = _numeric(profit.MBRevenue)
        for record in profit.itertuples(index=False):
            suffix = record.origin_file[len(f"profit_{key}_"):-4]
            companions = {}
            for kind in ["growth", "balance"]:
                name = f"{kind}_{key}_{suffix}.csv"
                if name in manifest["files"]:
                    frame = _verified_read(path, manifest, name)
                    match = frame[(frame.code == symbol) & (pd.to_datetime(frame.statDate) == record.statDate)] if len(frame) else frame
                    if len(match) > 1:
                        raise ValueError(f"{symbol}: ambiguous {kind} version")
                    companions[kind] = match.iloc[0] if len(match) else None
                else:
                    companions[kind] = None
            previous = profit[(profit.statDate == record.statDate - pd.DateOffset(years=1))
                              & (profit.pubDate <= record.pubDate)].sort_values("pubDate").tail(1)
            dates = [record.pubDate]
            if pd.isna(record.pubDate) or record.pubDate <= record.statDate:
                raise ValueError(f"{symbol}: invalid profit publication date")
            if len(previous):
                dates.append(previous.pubDate.iloc[0])
            for companion in companions.values():
                if companion is not None:
                    published = pd.Timestamp(companion.pubDate)
                    if pd.isna(published) or published <= record.statDate:
                        raise ValueError(f"{symbol}: invalid companion publication date")
                    dates.append(published)
            publication = max(dates)
            if publication <= record.statDate or pd.isna(publication):
                raise ValueError(f"{symbol}: invalid publication date")
            pos = calendar.searchsorted(publication, side="right")
            if pos >= len(calendar):
                continue  # it was never available within the input window
            prev_revenue = previous.MBRevenue.iloc[0] if len(previous) else np.nan
            revenue_growth = record.MBRevenue / prev_revenue - 1 if pd.notna(prev_revenue) and prev_revenue > 0 else np.nan
            growth, balance = companions["growth"], companions["balance"]
            row = dict(symbol=symbol, report_date=record.statDate, available_date=calendar[pos],
                       roe=float(record.roeAvg) if pd.notna(record.roeAvg) else np.nan,
                       revenue_growth=revenue_growth,
                       earnings_growth=pd.to_numeric(growth.get("YOYNI"), errors="raise") if growth is not None else np.nan,
                       debt_ratio=pd.to_numeric(balance.get("liabilityToAsset"), errors="raise") if balance is not None else np.nan,
                       publication_date=publication, origin_file=record.origin_file)
            financials.append(row)
            for field in ["roe", "revenue_growth", "earnings_growth", "debt_ratio"]:
                if pd.isna(row[field]):
                    missing.append(dict(symbol=symbol, report_date=str(record.statDate.date()), issue=f"missing_{field}"))
    target.mkdir(parents=True, exist_ok=True)
    if coverage:
        pd.DataFrame(coverage).to_csv(target / "coverage_failures.csv", index=False)
        raise ValueError("incomplete daily coverage; terminal delisting valuations must be supplied, not dropped")
    if not prices or not financials:
        raise ValueError("no usable public price/financial observations")
    p = pd.concat(prices, ignore_index=True).sort_values(["date", "symbol"])
    f = pd.DataFrame(financials).sort_values(["symbol", "report_date", "available_date"])
    if f.duplicated(["symbol", "report_date", "available_date"]).any():
        raise ValueError("multiple publication versions collapse to one available session")
    metadata = dict(source=f"{manifest['source']} raw staging", synthetic=manifest["synthetic"], schema="public_ratios_v1",
                    point_in_time_status="provider_dates_unverified_versions", price_basis="provider_adjusted_unverified",
                    universe_policy="explicit downloaded symbols; not certified whole historical market",
                    availability_policy="next supplied session after maximum dependency publication date",
                    execution_policy="non-suspended positive open only; limit states unverified",
                    input_manifest_sha256=hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(),
                    certified=False, missing_policy="native_nan", missing_observations=len(missing),
                    blockers=manifest["blockers"])
    p.to_csv(target / "prices.csv", index=False)
    f.to_csv(target / "fundamentals.csv", index=False)
    pd.DataFrame({"date": calendar}).to_csv(target / "calendar.csv", index=False)
    pd.DataFrame(missing).to_csv(target / "missing_fields.csv", index=False)
    metadata["converted_hashes"] = {name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                                     for name in ["prices.csv", "fundamentals.csv", "calendar.csv"]}
    (target / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def load_public_data(directory, asof=None):
    path = Path(directory)
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("schema") != "public_ratios_v1" or metadata.get("certified") is not False:
        raise ValueError("expected explicitly unverified public ratios conversion")
    for name in ["prices.csv", "fundamentals.csv", "calendar.csv"]:
        if hashlib.sha256((path / name).read_bytes()).hexdigest() != metadata.get("converted_hashes", {}).get(name):
            raise ValueError(f"converted public file hash mismatch: {name}")
    p = pd.read_csv(path / "prices.csv", dtype={"symbol": str}, parse_dates=["date", "listing_date"])
    f = pd.read_csv(path / "fundamentals.csv", dtype={"symbol": str}, parse_dates=["report_date", "available_date"])
    calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(path / "calendar.csv").date))
    end = pd.Timestamp(asof) if asof is not None else calendar[-1]
    if end not in calendar:
        raise ValueError("asof outside public calendar")
    return Dataset(p[p.date <= end], f[f.available_date <= end], calendar[calendar <= end], metadata)


def convert_public_events(directory, raw_prices, corporate_actions, out, reference_tick_size=None):
    """Seal a public-ratio dataset with audited event-based research prices.

    This does not certify provider financial vintages or event completeness.
    No absolute market cap, earnings or equity values are manufactured.
    """
    data = load_public_data(directory)
    parent = hashlib.sha256((Path(directory)/"metadata.json").read_bytes()).hexdigest()
    return _seal_public_events(data, raw_prices, corporate_actions, out, reference_tick_size, parent)


def _seal_public_events(data, raw_prices, corporate_actions, out, reference_tick_size=None, parent_metadata_sha256=None):
    from .corporate_actions import economic_return_prices
    target = Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("public event conversion output must be empty")
    if data.metadata.get("event_return_inputs"):
        raise ValueError("public dataset already contains event inputs")
    raw, actions = raw_prices.copy(), corporate_actions.copy()
    raw["date"] = pd.to_datetime(raw.date)
    for col in ["ex_date", "available_date", "pay_date"]:
        actions[col] = pd.to_datetime(actions[col])
    keys = ["date", "symbol"]
    original = data.prices.sort_values(keys).reset_index(drop=True)
    raw = raw.sort_values(keys).reset_index(drop=True)
    if not raw[keys].equals(original[keys]):
        raise ValueError("event raw quote keys differ from public feature keys")
    if not set(actions.symbol).issubset(set(raw.symbol)):
        raise ValueError("event security outside public quote universe")
    for name in ["open", "close"]:
        if not np.allclose(pd.to_numeric(raw[name]), original["raw_"+name], rtol=0, atol=1e-10):
            raise ValueError("event raw quotes differ from original public source")
    raw_tradable = raw.tradestatus.astype(str).eq("1") & raw.open.gt(0)
    if not raw_tradable.equals(original.can_buy) or not raw_tradable.equals(original.can_sell):
        raise ValueError("event tradability differs from original public source")
    economic = economic_return_prices(raw, actions, data.calendar, reference_tick_size)
    prices = original.copy()
    prices["close"] = economic.close.to_numpy()
    prices["total_return_open"] = economic.total_return_open.to_numpy()
    metadata = dict(data.metadata)
    metadata.update(price_basis="gross_entitlement_index_reinvest_after_payment",
                    certified=False, event_return_inputs={"raw_prices":"event_raw_prices.csv",
                    "corporate_actions":"event_corporate_actions.csv", "reference_tick_size":reference_tick_size},
                    event_limitation="Gross event research index; event coverage, terminal settlement and original financial vintages remain unverified",
                    parent_metadata_sha256=parent_metadata_sha256)
    target.mkdir(parents=True, exist_ok=True)
    for name, frame in [("prices.csv", prices), ("fundamentals.csv", data.fundamentals),
                        ("calendar.csv", pd.DataFrame({"date":data.calendar})),
                        ("event_raw_prices.csv",raw),("event_corporate_actions.csv",actions)]:
        frame.to_csv(target/name,index=False)
    metadata["converted_hashes"] = {name:hashlib.sha256((target/name).read_bytes()).hexdigest()
        for name in ["prices.csv","fundamentals.csv","calendar.csv","event_raw_prices.csv","event_corporate_actions.csv"]}
    (target/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    return metadata


def _public_event_inputs(directory, data):
    policy = data.metadata.get("event_return_inputs")
    if not policy:
        if data.metadata.get("price_basis") in ["gross_entitlement_index_reinvest_after_payment","causal_segment_feature_index"]:
            raise ValueError("public event prices require sealed event inputs")
        return None, None, None
    basis=data.metadata.get("price_basis")
    causal=(basis=="causal_segment_feature_index" and data.metadata.get("quality_policy",{}).get("schema")=="causal_quality_research_v1")
    if basis != "gross_entitlement_index_reinvest_after_payment" and not causal:
        raise ValueError("public event inputs require verified event price basis/policy")
    expected = {"raw_prices":"event_raw_prices.csv","corporate_actions":"event_corporate_actions.csv"}
    if any(policy.get(key) != name for key,name in expected.items()):
        raise ValueError("unexpected public event input names")
    frames = []
    for key,name in expected.items():
        file = Path(directory)/name
        if hashlib.sha256(file.read_bytes()).hexdigest() != data.metadata["converted_hashes"].get(name):
            raise ValueError("public event input hash mismatch")
        dates = ["date"] if key == "raw_prices" else ["ex_date","available_date","pay_date"]
        frame = pd.read_csv(file,dtype={"symbol":str},parse_dates=dates)
        frame = frame[frame[dates[0]] <= data.calendar[-1]].copy()
        frames.append(frame)
    return *frames, policy.get("reference_tick_size")


def public_snapshot(data, technical, date):
    """Shared native-NaN public-ratio eligibility for training and reload."""
    snap = technical[(technical.date == date) & technical.eligible & ~technical.is_st & technical.volume.gt(0)
                     & ((date - technical.listing_date).dt.days >= 365)]
    snap = snap[snap.symbol.map(is_ashare)]
    snap = snap.merge(fundamentals_asof(data, date), on="symbol", how="inner", validate="one_to_one")
    snap = snap[(date - snap.report_date).dt.days <= 550]
    causal=data.metadata.get("quality_policy",{}).get("schema")=="causal_quality_research_v1"
    if not causal:
        snap = snap.dropna(subset=["momentum_63", "momentum_126", "momentum_252_skip21", "volatility_63"])
    return snap.set_index("symbol").sort_index()


def public_samples(directory, config, asof=None):
    data = load_public_data(directory, asof)
    end = data.calendar[-1]
    dates = month_ends(data.calendar).union(pd.DatetimeIndex([end]))
    technical = technical_panel(data)
    library = default_library()
    frames = []
    for date in dates:
        snap = public_snapshot(data, technical, date)
        if snap.empty:
            continue
        values = library.compute(snap, config.oneil)
        values["date"] = date
        values["available_date"] = snap.available_date
        if "trading_symbol" in snap: values["trading_symbol"] = snap.trading_symbol
        frames.append(values.rename_axis("symbol").reset_index())
    if not frames:
        raise ValueError("public input needs longer history and visible financial reports")
    features = [n for n, item in library.factors.items() if config.oneil or item.family != "oneil"]
    raw, actions, tick = _public_event_inputs(directory, data)
    if raw is None:
        labels = forward_labels(data, dates)
    else:
        from .corporate_actions import entry_forward_labels
        labels = entry_forward_labels(raw, actions, data.calendar, dates, reference_tick_size=tick)
    targets = labels[labels.horizon == config.train_horizon].drop(columns="horizon")
    samples = pd.concat(frames, ignore_index=True).merge(targets, on=["date", "symbol"], how="left", validate="one_to_one")
    return data, samples, labels, features


def run_public_research(directory, out, config, asof=None, exploratory=False):
    from dataclasses import asdict
    from .pipeline import rolling_rank, cohort_performance, simulate_monthly, nav_metrics
    if not exploratory:
        raise ValueError("public vintages/price basis are unverified; explicit exploratory mode is required")
    path = Path(out)
    if path.exists() and any(path.iterdir()):
        raise ValueError("public research output must be empty")
    data, samples, labels, features = public_samples(directory, config, asof)
    path.mkdir(parents=True, exist_ok=True)
    picks, audit, importance = rolling_rank(samples, features, data.calendar, config, path / "models")
    # A mid-month asof score must not become an artificial month-end rebalance.
    full_calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(Path(directory)/"calendar.csv").date))
    monthly = picks[picks.date.isin(month_ends(full_calendar))]
    cohort = cohort_performance(monthly, labels, samples, data, config)
    raw, actions, tick = _public_event_inputs(directory, data)
    nav, orders = simulate_monthly(data, monthly, config, raw, actions, tick)
    coverage = samples.groupby("date")[features].agg(lambda x: x.notna().mean()).reset_index()
    for name, frame in [("monthly_top20", monthly), ("latest_top20", picks[picks.date == data.calendar[-1]]),
                        ("training_audit", audit), ("factor_importance", importance), ("feature_coverage", coverage),
                        ("cohort_performance", cohort), ("daily_nav", nav), ("orders", orders)]:
        frame.to_csv(path / f"{name}.csv", index=False)
    result = dict(mode="exploratory_unverified_public_source", certified=False, data=data.metadata,
                  config=asdict(config), asof=str(data.calendar[-1].date()), features=features,
                  trained_months=int((audit.status == "trained").sum()),
                  monthly_predictions=int(monthly.date.nunique()), latest_selection_count=int((picks.date == data.calendar[-1]).sum()),
                  nav=nav_metrics(nav),
                  interpretation="No claim of original financial vintage, audited total-return prices, executable fills or full-market coverage")
    result["input_hashes"] = data.metadata["converted_hashes"]
    result["label_return_basis"] = "gross_new_entry_entitlements" if raw is not None else "provider_adjusted_unverified_index_ratio"
    result["account_nav_basis"] = "raw_share_account_gross_dividends" if raw is not None else "provider_adjusted_unverified_fractional_shares"
    result["price_reconciliation"] = {"reference_tick_size":tick}
    result["cohorts"] = {str(h): {"complete_cohorts":int(g.complete.sum()),
                                "mean_gross_return":float(g.gross_return.mean()) if g.gross_return.notna().any() else None}
                         for h,g in cohort.groupby("horizon_months")}
    (path / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    from .model_registry import record_models
    record_models(path, result)
    return result
