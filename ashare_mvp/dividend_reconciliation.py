"""Reconcile full-market distribution plans against sealed payment evidence."""
from pathlib import Path
import hashlib
import json
import re

import numpy as np
import pandas as pd

from .dividend_normalization import _terms
from .public_data import is_ashare


def reconcile_dividend_sources(market_data, payment_data, calendar_path, out, reviewed_events=None, code_changes=None):
    market, payments, target = Path(market_data), Path(payment_data), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("dividend reconciliation output must be empty")
    manifest_bytes = (market / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != "market_dividend_plans_v1":
        raise ValueError("market dividend source schema mismatch")
    payment_summary_bytes = (payments / "summary.json").read_bytes()
    payment_summary = json.loads(payment_summary_bytes)
    action_bytes = (payments / "actions.csv").read_bytes()
    if hashlib.sha256(action_bytes).hexdigest() != payment_summary.get("actions_sha256"):
        raise ValueError("normalized payment event hash mismatch")
    actions = pd.read_csv(payments / "actions.csv", dtype={"symbol": str},
                          parse_dates=["ex_date", "available_date", "pay_date", "announcement_date"])
    if actions.duplicated(["symbol", "ex_date"]).any():
        raise ValueError("ambiguous payment events")
    if actions[["symbol", "ex_date", "available_date", "announcement_date"]].isna().any().any() or (pd.to_numeric(actions.cash_per_share, errors="raise").gt(0) & actions.pay_date.isna()).any():
        raise ValueError("missing payment event clock/security")
    roots={}
    if code_changes is not None:
        from .security_identity import code_change_maps,canonical_event_symbols
        roots,_,_=code_change_maps(code_changes)
        actions=canonical_event_symbols(actions,code_changes).rename(columns={"source_symbol":"payment_provider_symbol"})
    lookup = actions.set_index(["symbol", "ex_date"])
    calendar_bytes = Path(calendar_path).read_bytes()
    calendar = pd.read_csv(calendar_path, dtype=str)
    dates = calendar.loc[calendar.is_trading_day == "1", "calendar_date"] if {"calendar_date", "is_trading_day"}.issubset(calendar) else calendar["date"]
    cal = pd.DatetimeIndex(pd.to_datetime(dates))
    if cal.empty or cal.has_duplicates or cal.hasnans or not cal.is_monotonic_increasing or cal.tz is not None or not cal.equals(cal.normalize()):
        raise ValueError("invalid dividend reconciliation calendar")
    frames = []
    for period, info in manifest["periods"].items():
        if info.get("status") != "ok":
            continue
        day = pd.Timestamp(period)
        directory = market / f"{day.year}Q{day.quarter}"
        for name, digest in info["hashes"].items():
            if Path(name).name != name or hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                raise ValueError("market dividend evidence hash mismatch")
        frame = pd.read_csv(directory / "records.csv", dtype={"SECUCODE": str})
        if len(frame) != info["rows"] or frame.duplicated(["SECUCODE", "REPORT_DATE"]).any():
            raise ValueError("market dividend row/key mismatch")
        if not pd.to_datetime(frame.REPORT_DATE).dt.normalize().eq(day).all():
            raise ValueError("market dividend period mismatch")
        frame["source_period"] = period
        frames.append(frame)
    if not frames:
        raise ValueError("no verified market dividend periods")
    raw = pd.concat(frames, ignore_index=True)
    reviewed={}
    if reviewed_events is not None:
        from .reviewed_dividends import reviewed_distribution_lookup
        reviewed=reviewed_distribution_lookup(reviewed_events,raw,cal,code_changes)
    classified, matched = [], []
    for row in raw.itertuples(index=False):
        record = dict(security=row.SECUCODE, report_date=row.REPORT_DATE, source_period=row.source_period,
                      progress=row.ASSIGN_PROGRESS, ex_date=row.EX_DIVIDEND_DATE,
                      status="not_implemented", reason="provider does not mark plan implemented")
        exchange = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", str(row.SECUCODE))
        if exchange and exchange[2] == "BJ":
            record.update(status="excluded_exchange", reason="Beijing exchange excluded from research universe")
            classified.append(record)
            continue
        notice=reviewed.get((row.SECUCODE,pd.Timestamp(row.source_period)))
        if notice is not None:
            from .reviewed_dividends import check_reviewed_independent_payment
            notice_symbol=roots.get(notice.symbol,notice.symbol)
            prior=lookup.loc[(notice_symbol,notice.ex_date)] if (notice_symbol,notice.ex_date) in lookup.index else None
            check_reviewed_independent_payment(notice,prior)
            record.update(symbol=notice_symbol,provider_symbol=notice.provider_symbol,ex_date=str(notice.ex_date.date()),cash_per_share=notice.cash_per_share,
                          share_multiplier=notice.share_multiplier,available_date=str(notice.available_date.date()),
                          status="matched_reviewed_notice",reason="exact raw provider terms reviewed against official implementation notice")
            matched.append(dict(symbol=notice_symbol,provider_symbol=notice.provider_symbol,payment_provider_symbol=prior.get("payment_provider_symbol",notice_symbol) if prior is not None else None,ex_date=notice.ex_date,available_date=notice.available_date,pay_date=notice.pay_date,
                                cash_per_share=notice.cash_per_share,share_multiplier=notice.share_multiplier,
                                stock_market_date=getattr(notice,"stock_market_date",None),share_credit_date=getattr(notice,"share_credit_date",None),source_period=row.source_period,event_vintage_certified=False,
                                source_notice_url=notice.source_url,source_notice_sha256=notice.source_sha256,source_notice_page=notice.source_page,
                                ordinary_cash_per_share=getattr(notice,"ordinary_cash_per_share",None),special_cash_per_share=getattr(notice,"special_cash_per_share",None),
                                reviewed_payout_scope=getattr(notice,"payout_scope",None),
                                independent_provider_cash_before_review=prior.cash_per_share if prior is not None else None))
            classified.append(record)
            continue
        if row.ASSIGN_PROGRESS == "实施分配":
            try:
                code = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", str(row.SECUCODE))
                provider_symbol = f"{code[2].lower()}.{code[1]}" if code else ""
                symbol=roots.get(provider_symbol,provider_symbol)
                if not is_ashare(symbol):
                    raise ValueError("unsupported historical security identity/exchange")
                execution = pd.Timestamp(row.EX_DIVIDEND_DATE)
                if pd.notna(execution) and (execution < cal.min() or execution > cal.max()):
                    record.update(status="outside_evaluation_window", reason="execution outside supplied calendar interval")
                    classified.append(record)
                    continue
                terms = _terms(pd.Series(dict(dividCashStock=row.IMPL_PLAN_PROFILE,
                    dividCashPsBeforeTax=row.PRETAX_BONUS_RMB / 10 if pd.notna(row.PRETAX_BONUS_RMB) else np.nan,
                    dividStocksPs=row.BONUS_RATIO / 10 if pd.notna(row.BONUS_RATIO) else np.nan,
                    dividReserveToStockPs=row.IT_RATIO / 10 if pd.notna(row.IT_RATIO) else np.nan)))
                ex_date, notice = pd.Timestamp(row.EX_DIVIDEND_DATE), pd.Timestamp(row.NOTICE_DATE)
                if pd.isna(ex_date) or pd.isna(notice) or ex_date not in cal:
                    raise ValueError("missing or out-of-calendar execution/notice date")
                position = cal.searchsorted(notice, side="right")
                if position >= len(cal) or cal[position] > ex_date:
                    raise ValueError("latest notice not visible before execution")
                record.update(symbol=symbol, provider_symbol=provider_symbol, ex_date=str(ex_date.date()), cash_per_share=terms["dividCashPsBeforeTax"],
                              share_multiplier=1 + terms["dividStocksPs"] + terms["dividReserveToStockPs"],
                              available_date=str(cal[position].date()), status="missing_payment_evidence",
                              reason="no independently normalized payment event for security/ex-date")
                if (symbol, ex_date) in lookup.index:
                    payment = lookup.loc[(symbol, ex_date)]
                    if not np.isclose(payment.cash_per_share, record["cash_per_share"], atol=1e-6, rtol=1e-6) or not np.isclose(payment.share_multiplier, record["share_multiplier"], atol=1e-6, rtol=1e-6):
                        raise ValueError("sources disagree on cash/share terms")
                    if payment.pay_date < ex_date or payment.available_date > ex_date:
                        raise ValueError("invalid independent payment clock")
                    record.update(status="matched_payment_evidence", reason="terms/ex-date agree; original vintages remain unverified")
                    matched.append(dict(symbol=symbol, provider_symbol=provider_symbol, payment_provider_symbol=payment.get("payment_provider_symbol",symbol), ex_date=ex_date, available_date=max(cal[position], payment.available_date),
                                        pay_date=payment.pay_date, cash_per_share=record["cash_per_share"],
                                        share_credit_date=payment.get("share_credit_date", ""),
                                        share_multiplier=record["share_multiplier"], stock_market_date=payment.get("stock_market_date", ""),
                                        source_period=row.source_period, event_vintage_certified=False))
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status="unresolved", reason=str(exc))
        classified.append(record)
    result = pd.DataFrame(classified)
    output = pd.DataFrame(matched, columns=["symbol", "provider_symbol", "payment_provider_symbol", "ex_date", "available_date", "pay_date", "cash_per_share",
                                           "share_multiplier", "stock_market_date", "share_credit_date", "source_period", "event_vintage_certified",
                                           "source_notice_url","source_notice_sha256","source_notice_page","ordinary_cash_per_share","special_cash_per_share","reviewed_payout_scope","independent_provider_cash_before_review"])
    if output.duplicated(["symbol", "ex_date"]).any():
        raise ValueError("multiple implemented report periods map to one payment event")
    target.mkdir(parents=True, exist_ok=True)
    result.to_csv(target / "coverage.csv", index=False)
    output.to_csv(target / "actions.csv", index=False)
    (target / "input_market_manifest.json").write_bytes(manifest_bytes)
    (target / "input_payment_summary.json").write_bytes(payment_summary_bytes)
    (target / "input_payment_actions.csv").write_bytes(action_bytes)
    (target / "input_calendar.csv").write_bytes(calendar_bytes)
    summary = dict(records=len(raw), in_scope_records=int((~result.status.isin(["excluded_exchange","outside_evaluation_window"])).sum()), excluded_exchanges=["BJ"],
                   evaluation_start=str(cal.min().date()), evaluation_end=str(cal.max().date()),
                   status_counts=result.status.value_counts().to_dict(), matched_events=len(output),
                   partial_market_source=manifest.get("status") != "downloaded_raw", certified=False, model_ready=False,
                   market_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                   payment_actions_sha256=hashlib.sha256(action_bytes).hexdigest(),
                   actions_sha256=hashlib.sha256((target / "actions.csv").read_bytes()).hexdigest(),
                   limitations=["unmatched plans never receive assumed payment dates", "matched subset is not a complete market event history",
                                "original event vintages unverified; economic entitlement is not tradable share settlement"])
    summary["coverage_sha256"]=hashlib.sha256((target/"coverage.csv").read_bytes()).hexdigest()
    if reviewed_events is not None:
        payload=reviewed_events.to_json(orient="records",date_format="iso")
        (target/"input_reviewed_distributions.json").write_text(payload,encoding="utf-8")
        summary["reviewed_distributions_sha256"]=hashlib.sha256(payload.encode()).hexdigest()
        summary["reviewed_notice_events"]=len(reviewed)
    if code_changes is not None:
        from .security_identity import code_change_payload
        payload=code_change_payload(code_changes)
        (target/"input_code_changes.json").write_text(payload,encoding="utf-8")
        summary["code_changes_sha256"]=hashlib.sha256(payload.encode()).hexdigest()
        summary["event_identity_policy"]="stable verified issuer; original provider symbols retained; never a predictive feature"
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
