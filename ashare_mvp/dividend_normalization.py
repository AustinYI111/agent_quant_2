"""Normalize sealed dividend evidence without inventing absent event terms."""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import re

import numpy as np
import pandas as pd


def _terms(row):
    text = str(row.get("dividCashStock", "")).split("（")[0].split("(")[0].strip().replace("转增", "转")
    if not re.fullmatch(r"10(?:(?:送|转|派)[0-9]+(?:\.[0-9]+)?(?:股|元)?)+", text):
        raise ValueError("unsupported or ambiguous distribution description")
    terms = re.findall(r"(送|转|派)([0-9]+(?:\.[0-9]+)?)", text)
    if len({key for key, _ in terms}) != len(terms):
        raise ValueError("duplicate distribution terms")
    described = {key: float(value) / 10 for key, value in terms}
    values = {}
    for field, key in [("dividCashPsBeforeTax", "派"), ("dividStocksPs", "送"), ("dividReserveToStockPs", "转")]:
        raw = row[field]
        if pd.isna(raw) or str(raw).strip() == "":
            if key in described:
                raise ValueError(f"missing numeric distribution term: {field}")
            value = 0.0  # Zero only when the explicit description also has no such term.
        else:
            value = float(raw)
        if not np.isfinite(value) or value < 0 or not np.isclose(value, described.get(key, 0), atol=1e-6, rtol=1e-6):
            raise ValueError("numeric distribution differs from explicit description")
        values[field] = value
    return values


def normalize_dividends(data, calendar_path, out):
    source, target = Path(data), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("normalized dividend output must be empty")
    manifest_bytes = (source / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != "dividend_raw_v1" or manifest.get("status") != "downloaded_raw":
        raise ValueError("complete requested dividend acquisition required")
    request = manifest["request"]
    if request["year_type"] != "operate":
        raise ValueError("execution-year dividend source required")
    expected = {f"{symbol}_{year}.csv": (symbol, year) for symbol in request["symbols"]
                for year in range(request["start_year"], request["end_year"] + 1)}
    if set(manifest["files"]) != set(expected):
        raise ValueError("dividend acquisition file coverage differs from request")
    calendar_bytes = Path(calendar_path).read_bytes()
    calendar = pd.read_csv(BytesIO(calendar_bytes), dtype=str)
    dates = calendar.loc[calendar.is_trading_day == "1", "calendar_date"] if {"calendar_date", "is_trading_day"}.issubset(calendar) else calendar["date"]
    cal = pd.DatetimeIndex(pd.to_datetime(dates))
    if cal.empty or cal.has_duplicates or cal.hasnans or not cal.is_monotonic_increasing or cal.tz is not None or not cal.equals(cal.normalize()):
        raise ValueError("invalid dividend exchange calendar")
    accepted, rejected = [], []
    for name, (symbol, year) in sorted(expected.items()):
        if Path(name).name != name:
            raise ValueError("invalid dividend evidence filename")
        raw = (source / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["files"][name]["sha256"]:
            raise ValueError("dividend evidence hash mismatch")
        frame = pd.read_csv(BytesIO(raw), dtype=str)
        if len(frame) != manifest["files"][name]["rows"]:
            raise ValueError("dividend source row count mismatch")
        for index, row in frame.iterrows():
            try:
                if row.code != symbol:
                    raise ValueError("dividend security mismatch")
                terms = _terms(row)
                announcement, ex_date = pd.Timestamp(row.dividPlanDate), pd.Timestamp(row.dividOperateDate)
                cash = terms["dividCashPsBeforeTax"]
                pay_date = pd.Timestamp(row.dividPayDate) if cash > 0 else ex_date
                if any(pd.isna(day) or day.tz is not None or day != day.normalize() for day in [announcement, ex_date, pay_date]):
                    raise ValueError("missing or invalid event date")
                visible_index = cal.searchsorted(announcement, side="right")
                if visible_index >= len(cal) or ex_date not in cal or ex_date.year != year or pay_date < ex_date:
                    raise ValueError("event outside exchange calendar or invalid execution/payment date")
                available_date = cal[visible_index]
                if available_date > ex_date:
                    raise ValueError("event not visible before execution")
                accepted.append(dict(symbol=symbol, ex_date=ex_date, available_date=available_date, pay_date=pay_date,
                    cash_per_share=cash, share_multiplier=1 + terms["dividStocksPs"] + terms["dividReserveToStockPs"],
                    announcement_date=announcement, stock_market_date=row.get("dividStockMarketDate", ""),
                    source_file=name, source_row=int(index), event_vintage_certified=False))
            except (ValueError, TypeError, KeyError) as exc:
                rejected.append(dict(source_file=name, source_row=int(index), symbol=symbol, reason=str(exc)))
    columns = ["symbol", "ex_date", "available_date", "pay_date", "cash_per_share", "share_multiplier",
               "announcement_date", "stock_market_date", "source_file", "source_row", "event_vintage_certified"]
    actions = pd.DataFrame(accepted, columns=columns)
    if actions.duplicated(["symbol", "ex_date"]).any():
        raise ValueError("duplicate normalized corporate event")
    target.mkdir(parents=True, exist_ok=True)
    actions.sort_values(["symbol", "ex_date"]).to_csv(target / "actions.csv", index=False)
    pd.DataFrame(rejected, columns=["source_file", "source_row", "symbol", "reason"]).to_csv(target / "rejected.csv", index=False)
    (target / "input_manifest.json").write_bytes(manifest_bytes)
    (target / "input_calendar.csv").write_bytes(calendar_bytes)
    summary = dict(accepted=len(actions), rejected=len(rejected), certified=False, model_ready=False,
                   status="normalized" if not rejected else "partial",
                   input_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                   calendar_sha256=hashlib.sha256(calendar_bytes).hexdigest(),
                   actions_sha256=hashlib.sha256((target / "actions.csv").read_bytes()).hexdigest(),
                   limitations=["source vintage unverified", "gross economic share entitlements; stock tradability date retained separately",
                                "no independent full-market company-action/terminal coverage certification"])
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
