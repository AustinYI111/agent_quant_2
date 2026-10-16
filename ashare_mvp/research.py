"""Historical technical observations, structured financial notes and holdings."""
from pathlib import Path
import json

import numpy as np
import pandas as pd

from .data import fundamentals_asof
from .pipeline import forward_labels


def technical_observations(data, asof, symbols):
    asof = pd.Timestamp(asof)
    rows = []
    for symbol in symbols:
        history = data.prices[(data.prices.symbol == symbol) & (data.prices.date <= asof)].sort_values("date")
        if history.empty or history.date.iloc[-1] != asof:
            rows.append(dict(symbol=symbol, date=asof, status="missing_quote"))
            continue
        last = history.iloc[-1]
        close = history.close
        if len(close) < 252 or not (close.tail(252) > 0).all():
            rows.append(dict(symbol=symbol, date=asof, status="insufficient_history"))
            continue
        raw = last.get("raw_close", np.nan)
        scale = raw / last.close if pd.notna(raw) and raw > 0 else 1.0
        basis = "current_raw_price" if pd.notna(raw) and raw > 0 else "adjusted_units_only"
        current = last.close * scale
        sigma = close.pct_change(fill_method=None).tail(20).std(ddof=1) * current
        ma20, ma50, ma200 = [close.tail(n).mean() * scale for n in [20, 50, 200]]
        prior_high = close.iloc[-253:-1].max() * scale
        support = close.tail(63).quantile(0.10) * scale
        avg_volume = history.volume.tail(50).mean()
        rows.append(dict(symbol=symbol, date=asof, status="observed", price_basis=basis,
                         current_price=current, ma20=ma20, ma50=ma50, ma200=ma200,
                         trend="up" if current > ma50 > ma200 else "down" if current < ma50 < ma200 else "mixed",
                         volume_ratio=last.volume / avg_volume if avg_volume > 0 else np.nan,
                         pullback_low=max(0, ma50 - sigma), pullback_high=ma50 + sigma,
                         breakout_low=max(0, prior_high - 0.5 * sigma), breakout_high=prior_high + 0.5 * sigma,
                         support_observation=support, risk_observation=max(0, min(support, current - 2 * sigma)),
                         range_method="MA50 +/- 20-session close-return volatility; prior 252 close high +/- half volatility"))
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["symbol","date","status"])


def financial_research(data, asof, symbols):
    asof = pd.Timestamp(asof)
    latest = fundamentals_asof(data, asof).set_index("symbol")
    prices = data.prices[data.prices.date == asof].set_index("symbol")
    rows = []
    for symbol in symbols:
        if symbol not in latest.index or symbol not in prices.index:
            rows.append(dict(symbol=symbol, status="missing_financials"))
            continue
        record = latest.loc[symbol]
        market_cap = prices.at[symbol, "market_cap"]
        previous = data.fundamentals[(data.fundamentals.symbol == symbol)
                                    & (data.fundamentals.available_date <= asof)
                                    & (data.fundamentals.report_date < record.report_date)]
        previous = previous.sort_values(["report_date", "available_date"]).tail(1)
        roe_change = record.roe - previous.roe.iloc[0] if len(previous) else np.nan
        debt_change = record.debt_ratio - previous.debt_ratio.iloc[0] if len(previous) else np.nan
        industry = record.get("industry", "unclassified")
        peers = latest[latest.industry == industry] if "industry" in latest and industry != "unclassified" else pd.DataFrame()
        notes = [f"净资产收益率 {record.roe:.2%}；收入增长 {record.revenue_growth:.2%}；利润增长 {record.earnings_growth:.2%}",
                 f"资产负债率 {record.debt_ratio:.2%}"]
        if record.earnings_ttm <= 0:
            notes.append("近12个月利润非正，不适用正市盈率估值")
        if pd.notna(roe_change):
            notes.append(f"净资产收益率较上一份当时已知报告变动 {roe_change:+.2%}")
        rows.append(dict(symbol=symbol, status="structured_financial_review", report_date=record.report_date,
                         available_date=record.available_date, industry=industry, peer_count=len(peers),
                         peer_roe_percentile=float((peers.roe <= record.roe).mean()) if len(peers) else np.nan,
                         roe=record.roe, roe_change=roe_change, debt_ratio=record.debt_ratio, debt_change=debt_change,
                         earnings_yield=record.earnings_ttm / market_cap if market_cap > 0 else np.nan,
                         book_yield=record.book_equity / market_cap if market_cap > 0 else np.nan,
                         notes="; ".join(notes)))
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["symbol","status","report_date","available_date"])


def load_holdings(file):
    holdings = pd.read_csv(file, dtype={"symbol": str})
    required = {"symbol", "shares", "average_cost"}
    if not required.issubset(holdings):
        raise ValueError("holdings CSV requires symbol,shares,average_cost")
    if holdings.symbol.isna().any() or holdings.symbol.str.strip().eq("").any() or holdings.symbol.duplicated().any():
        raise ValueError("holdings symbols must be unique and nonblank")
    for field in ["shares", "average_cost"]:
        holdings[field] = pd.to_numeric(holdings[field], errors="raise")
        if not np.isfinite(holdings[field]).all() or (holdings[field] < 0).any():
            raise ValueError(f"invalid holdings {field}")
    return holdings


def canonical_holdings(data, asof, holdings):
    """Resolve verified equal-share aliases; never combine ambiguous input rows."""
    policy=data.metadata.get("security_identity_policy")
    result=holdings.copy()
    if not policy: return result
    from .security_identity import code_change_maps
    changes=pd.DataFrame(policy["changes"])
    roots,starts,_=code_change_maps(changes)
    day=pd.Timestamp(asof)
    for code,start in starts.items():
        if day<start and result.symbol.eq(code).any(): raise ValueError("holding code not active at report date")
    if "input_symbol" not in result: result["input_symbol"]=result.symbol
    result["symbol"]=result.symbol.map(lambda x:roots.get(x,x))
    if result.symbol.duplicated().any(): raise ValueError("multiple holdings rows resolve to same issuer")
    forward={row.old_symbol:(row.new_symbol,pd.Timestamp(row.effective_date)) for row in changes.itertuples(index=False)}
    current={}
    for root in set(roots.values()):
        code=root
        while code in forward and forward[code][1]<=day: code=forward[code][0]
        current[root]=code
    result["trading_symbol"]=result.symbol.map(lambda x:current.get(x,x))
    return result


def value_holdings(data, asof, holdings):
    holdings=canonical_holdings(data,asof,holdings)
    prices = data.prices[data.prices.date == pd.Timestamp(asof)].set_index("symbol")
    rows = []
    for h in holdings.itertuples(index=False):
        raw_price = prices.at[h.symbol, "raw_close"] if "raw_close" in prices and h.symbol in prices.index else np.nan
        observed = pd.notna(raw_price) and raw_price >= 0
        rows.append(dict(date=pd.Timestamp(asof), symbol=h.symbol, shares=h.shares, average_cost=h.average_cost,
                         raw_price=raw_price, status="valued" if observed else "missing_raw_quote",
                         cost_basis=h.shares * h.average_cost,
                         market_value=h.shares * raw_price if observed else np.nan,
                         unrealized_pnl=h.shares * (raw_price - h.average_cost) if observed else np.nan))
    result=pd.DataFrame(rows, columns=["date", "symbol", "shares", "average_cost", "raw_price", "status",
                                       "cost_basis", "market_value", "unrealized_pnl"])
    if "input_symbol" in holdings:
        result=result.merge(holdings[["symbol","input_symbol","trading_symbol"]],on="symbol",how="left",validate="one_to_one")
    return result


def update_prediction_history(data, picks, asof, ledger):
    """Idempotent immutable predictions; mature outcomes may be updated."""
    file = Path(ledger)
    keys = ["date", "symbol", "horizon"]
    incoming = []
    for pick in picks.itertuples(index=False):
        if pd.Timestamp(pick.date) > pd.Timestamp(asof):
            raise ValueError("future prediction in report input")
        for h in [3, 6, 12]:
            incoming.append(dict(date=pd.Timestamp(pick.date), symbol=pick.symbol, horizon=h,
                                 score=pick.score, rank=pick.rank, synthetic=data.metadata["synthetic"]))
    columns = keys + ["score", "rank", "synthetic"]
    new = pd.DataFrame(incoming, columns=columns)
    if file.exists():
        old = pd.read_csv(file, dtype={"symbol": str}, parse_dates=["date"])
        old = old[columns]
        if (old.date > pd.Timestamp(asof)).any():
            raise ValueError("historical report requires a separate ledger; existing ledger contains future predictions")
        if old.synthetic.ne(data.metadata["synthetic"]).any():
            raise ValueError("cannot mix synthetic and real prediction history")
        joined = old.merge(new, on=keys, suffixes=("_old", "_new"))
        if len(joined) and (not np.allclose(joined.score_old, joined.score_new, rtol=0, atol=1e-12) or not joined.rank_old.equals(joined.rank_new)):
            raise ValueError("cannot overwrite an existing prediction")
        new = pd.concat([old, new]).drop_duplicates(keys, keep="first")
    observed = type(data)(data.prices[data.prices.date <= pd.Timestamp(asof)], data.fundamentals,
                          data.calendar[data.calendar <= pd.Timestamp(asof)], data.metadata)
    if new.empty:
        return pd.DataFrame(columns=columns + ["entry_date", "label_end", "target"])
    labels = forward_labels(observed, pd.DatetimeIndex(new.date.unique()))
    history = new.merge(labels, on=keys, how="left", validate="one_to_one")
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".pending.csv")
    history.sort_values(keys).to_csv(temporary, index=False)
    temporary.replace(file)
    return history


def daily_report(data, picks, holdings, out, asof, ledger=None):
    asof = pd.Timestamp(asof)
    if asof not in data.calendar:
        raise ValueError("report asof must be an observed exchange session")
    if picks.duplicated(["date", "symbol"]).any() or (pd.to_datetime(picks.date) > asof).any():
        raise ValueError("duplicate/future predictions")
    holdings=canonical_holdings(data,asof,holdings)
    # A daily report must not quietly relabel an old monthly prediction as today.
    latest_date = pd.to_datetime(picks.date).max() if len(picks) else pd.NaT
    latest = picks[pd.to_datetime(picks.date) == latest_date]
    symbols = sorted(set(latest.symbol) | set(holdings.symbol))
    technical = technical_observations(data, asof, symbols)
    financial = financial_research(data, asof, symbols)
    positions = value_holdings(data, asof, holdings)
    if "trading_symbol" in data.prices:
        codes=data.prices.loc[data.prices.date==asof].set_index("symbol").trading_symbol
        for frame in [technical,financial]:
            frame["trading_symbol"]=frame.symbol.map(codes)
    def display_code(row):
        value=row.get("trading_symbol")
        return value if pd.notna(value) else row["symbol"]
    path = Path(out)
    if path.exists() and any(path.iterdir()):
        raise ValueError("daily report output must be empty")
    path.mkdir(parents=True, exist_ok=True)
    history = update_prediction_history(data, picks, asof, ledger) if ledger else pd.DataFrame()
    for name, frame in [("technical_observations", technical), ("financial_research", financial),
                        ("holdings", positions), ("prediction_history", history)]:
        frame.to_csv(path / f"{name}.csv", index=False)
    complete = positions.status.eq("valued").all()
    total = float(positions.market_value.sum()) if complete else None
    summary = dict(asof=str(asof.date()), synthetic=data.metadata["synthetic"],
                   prediction_date=str(latest_date.date()) if pd.notna(latest_date) else None,
                   stale_prediction=bool(pd.notna(latest_date) and latest_date < asof),
                   position_count=len(positions), valuation_complete=bool(complete), market_value=total)
    (path / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    lines = [f"# 个人量化研究日报 — {asof:%Y-%m-%d}", "",
             f"数据：{'合成演示，非真实持仓' if summary['synthetic'] else data.metadata['source']}",
             f"排序观察日：{summary['prediction_date'] or '无排序'}；{'历史排序，今日未重新预测' if summary['stale_prediction'] else '当日排序'}", "",
             f"持仓数量：{len(positions)}；估值{'完整' if complete else '不完整，缺失原始股价'}；合计市值：{format(total, '.2f') if total is not None else '未计算'}", "",
             "## 股票观察", "", "| 股票 | 价格单位 | 趋势 | 回调观察区间 | 突破观察区间 |", "|---|---|---|---|---|"]
    for row in technical.to_dict("records"):
        if row["status"] == "observed":
            unit = "实际报价" if row["price_basis"] == "current_raw_price" else "调整价格单位"
            trend = {"up": "上涨", "down": "下跌", "mixed": "混合"}[row["trend"]]
            lines.append(f"| {display_code(row)} | {unit} | {trend} | {row['pullback_low']:.2f}–{row['pullback_high']:.2f} | {row['breakout_low']:.2f}–{row['breakout_high']:.2f} |")
        else:
            status = {"missing_quote": "缺失报价", "insufficient_history": "历史不足"}.get(row["status"], row["status"])
            lines.append(f"| {display_code(row)} | {status} | — | — | — |")
    lines += ["", "区间由历史均线、高点和收盘波动率计算；表示观察条件，不是预测置信区间或自动下单指令。缺失原始股价时，调整价格单位不能当作券商报价。", "",
              "## 财报事实", ""]
    for row in financial.to_dict("records"):
        lines.append(f"- {display_code(row)}：{row.get('notes', row['status'])}")
    lines += ["", "财报研究当前是结构化指标解释；尚未自动抽取原始年报正文、审计意见与行业叙事。"]
    (path / "daily_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
