"""
Flask REST API backend for Agent Quant dashboard.

Endpoints:
  GET  /                 - serve dashboard.html
  GET  /dashboard.html   - serve dashboard.html
  GET  /scripts/*        - serve static JS files
  GET  /styles/*         - serve static CSS files
  POST /api/backtest     - run a backtest with given parameters
  GET  /api/results      - return the latest backtest results (or sample data)
  GET  /api/health       - health check

Start with:
  python api_server.py
  # or: python api_server.py --port 5000 --debug
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS

# ── Make multi_agent_quant importable ────────────────────────────────────
_ROOT = Path(__file__).parent
_MAQ  = _ROOT / "multi_agent_quant"
if str(_MAQ) not in sys.path:
    sys.path.insert(0, str(_MAQ))

from agents.data_agent import DataAgent                        # noqa: E402
from agents.trend_agent import TrendAgent                      # noqa: E402
from agents.mean_reversion_agent import MeanReversionAgent     # noqa: E402
from agents.ml_agent import MLAgent                            # noqa: E402
from agents.coordinator_agent import CoordinatorAgent          # noqa: E402
from backtest.backtest_engine import BacktestEngine            # noqa: E402

# ── Flask app ─────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)   # allow all origins so the HTML file can call us directly

# ── In-memory cache for the latest result ────────────────────────────────
_latest_result: Optional[Dict[str, Any]] = None
_SAMPLE_PATH = _ROOT / "data" / "sample-trades.json"


# ════════════════════════════════════════════════════════════════════════════
#  Backtest helpers
# ════════════════════════════════════════════════════════════════════════════

def _slice_df(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """Slice df to [start_date, end_date] and log the resulting coverage."""
    if df is None or len(df) == 0:
        return df
    s = pd.to_datetime(start_date, format="%Y%m%d")
    e = pd.to_datetime(end_date,   format="%Y%m%d")
    result = df.sort_index().loc[s:e]
    if len(result) > 0:
        print(f"[_slice_df] requested {start_date}~{end_date}, "
              f"got {result.index[0].date()}~{result.index[-1].date()} "
              f"({len(result)} rows)")
    else:
        print(f"[_slice_df] WARNING: no rows in {start_date}~{end_date}")
    return result


def _pair_trades(raw_trades: list, strategy: str, symbol: str) -> List[Dict[str, Any]]:
    """
    Convert a list of Trade objects (buy/sell events) into closed-round-trip
    records that the dashboard expects:
      { id, time, symbol, strategy, direction, quantity, entryPrice,
        exitPrice, pnl, returnPct, status }
    Strategy: accumulate buys; each sell closes the whole stack.
    """
    paired: List[Dict[str, Any]] = []
    pending_buys: List[Any] = []  # list of Trade objects waiting for a sell

    for tr in raw_trades:
        if tr.action == "buy":
            pending_buys.append(tr)
        elif tr.action == "sell" and pending_buys:
            total_shares = sum(b.size for b in pending_buys)
            if total_shares <= 0:
                pending_buys = []
                continue
            avg_entry = sum(b.price * b.size for b in pending_buys) / total_shares
            exit_price = tr.price
            gross_revenue = tr.size * exit_price
            gross_cost    = total_shares * avg_entry
            fees          = sum(b.fee for b in pending_buys) + tr.fee
            pnl           = gross_revenue - gross_cost - fees
            ret_pct       = pnl / gross_cost if gross_cost > 0 else 0.0

            paired.append({
                "id":          len(paired) + 1,
                "time":        tr.dt.strftime("%Y-%m-%d"),
                "symbol":      symbol,
                "strategy":    strategy,
                "direction":   "买入",          # round-trip = bought then sold
                "quantity":    int(round(total_shares)),
                "entryPrice":  round(avg_entry, 4),
                "exitPrice":   round(exit_price, 4),
                "pnl":         round(pnl, 2),
                "returnPct":   round(ret_pct, 6),
                "status":      "已平仓",
            })
            pending_buys = []

    # Unclosed buys → mark as "持仓中"
    for tr in pending_buys:
        paired.append({
            "id":          len(paired) + 1,
            "time":        tr.dt.strftime("%Y-%m-%d"),
            "symbol":      symbol,
            "strategy":    strategy,
            "direction":   "买入",
            "quantity":    int(round(tr.size)),
            "entryPrice":  round(tr.price, 4),
            "exitPrice":   None,
            "pnl":         0.0,
            "returnPct":   0.0,
            "status":      "持仓中",
        })

    return paired


def _build_response(
    symbol: str,
    start_date: str,
    end_date: str,
    strategy_results: Dict[str, Tuple[Dict[str, Any], list]],
) -> Dict[str, Any]:
    """
    Format strategy_results into the JSON shape the dashboard consumes.
    strategy_results = { name: (metrics_dict, [Trade, ...]) }
    """
    strategies_summary = []
    equity_curves: Dict[str, Any] = {}
    all_trades: List[Dict[str, Any]] = []

    # Build per-strategy date→equity mappings first
    strategy_labels: Dict[str, List[str]] = {}
    strategy_ec: Dict[str, List[float]] = {}

    for name, (metrics, trades) in strategy_results.items():
        strategies_summary.append({
            "name":        name,
            "totalReturn": round(metrics.get("total_return", 0.0), 6),
            "annualReturn": round(metrics.get("annual_return", 0.0), 6),
            "maxDrawdown": round(metrics.get("max_drawdown", 0.0), 6),
            "sharpe":      round(metrics.get("sharpe", 0.0), 6),
            "numTrades":   int(metrics.get("num_trades", 0)),
        })

        dates = metrics.get("dates", [])
        ec    = metrics.get("equity_curve", [])
        labels = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                  for d in dates]

        strategy_labels[name] = labels
        strategy_ec[name]     = [round(float(v), 2) for v in ec]

        paired = _pair_trades(trades, name, symbol)
        all_trades.extend(paired)

    # Use the longest date list as the common labels axis so all strategies
    # share the same x-axis regardless of minor length differences
    common_labels: List[str] = max(strategy_labels.values(), key=len) if strategy_labels else []

    for name in strategy_labels:
        labels = strategy_labels[name]
        ec     = strategy_ec[name]
        if labels == common_labels:
            equity_curves[name] = ec
        else:
            # Align this strategy's curve to common_labels by date lookup;
            # missing dates get None which JSON-serialises to null (Chart.js renders as a gap)
            ec_dict = dict(zip(labels, ec))
            equity_curves[name] = [
                ec_dict.get(lbl) for lbl in common_labels
            ]

    # Re-number ids after merging all strategies
    for idx, tr in enumerate(all_trades, start=1):
        tr["id"] = idx

    return {
        "symbol":    symbol,
        "startDate": start_date,
        "endDate":   end_date,
        "summary": {
            "initialCapital": 100000,
            "strategies": strategies_summary,
        },
        "equityCurves": {
            "labels": common_labels,
            **equity_curves,
        },
        "trades": all_trades,
    }


def run_backtest(params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a full backtest using the quantitative agents."""
    symbol      = str(params.get("symbol",     "600519")).strip()
    start_date  = str(params.get("start_date", "20200101")).strip()
    end_date    = str(params.get("end_date",   "20241231")).strip()
    adjust      = str(params.get("adjust",     "qfq"))

    # ── Server-side input validation ──────────────────────────────────────
    import re
    if not re.match(r"^\d{6}$", symbol):
        raise ValueError(f"Invalid symbol '{symbol}': expected 6-digit code")
    if not re.match(r"^\d{8}$", start_date):
        raise ValueError(f"Invalid start_date '{start_date}': expected YYYYMMDD")
    if not re.match(r"^\d{8}$", end_date):
        raise ValueError(f"Invalid end_date '{end_date}': expected YYYYMMDD")
    if start_date >= end_date:
        raise ValueError("start_date must be before end_date")
    if adjust not in ("", "qfq", "hfq"):
        adjust = "qfq"

    # Engine params (clamped to sane ranges)
    init_cash    = max(1000.0,  min(1e9,  float(params.get("init_cash",    100000.0))))
    fee_rate     = max(0.0,     min(0.01, float(params.get("fee_rate",     0.0003))))
    slippage_bps = max(0.0,     min(100,  float(params.get("slippage_bps", 5.0))))

    # Strategy params (clamped)
    trend_short  = max(2,  min(100,  int(params.get("trend_short",  5))))
    trend_long   = max(5,  min(500,  int(params.get("trend_long",   20))))
    mr_window    = max(5,  min(200,  int(params.get("mr_window",    20))))
    mr_num_std   = max(0.1, min(10,  float(params.get("mr_num_std", 1.2))))
    ml_lookback  = max(2,  min(60,   int(params.get("ml_lookback",  10))))
    ml_min_train = max(20, min(1000, int(params.get("ml_min_train", 60))))
    ml_prob_thr  = max(0.5, min(1.0, float(params.get("ml_prob_threshold", 0.55))))

    if trend_short >= trend_long:
        raise ValueError("trend_short must be less than trend_long")

    # Coordinator params
    w_trend  = max(0.0, min(1.0, float(params.get("w_trend", 0.60))))
    w_mr     = max(0.0, min(1.0, float(params.get("w_mr",    0.40))))
    w_ml     = max(0.0, min(1.0, float(params.get("w_ml",    0.25))))
    min_edge = max(0.0, min(0.5, float(params.get("min_edge", 0.01))))

    # Which strategies to run (default: all four)
    _allowed = {"Trend-only", "MeanRev-only", "Fusion(no-ML)", "Fusion(+ML-veto)"}
    run_strategies: List[str] = [
        s for s in params.get(
            "strategies",
            ["Trend-only", "MeanRev-only", "Fusion(no-ML)", "Fusion(+ML-veto)"],
        )
        if s in _allowed
    ]
    if not run_strategies:
        raise ValueError("No valid strategies specified")

    # ── Data ────────────────────────────────────────────────────────────
    data_dir = str(_MAQ / "data" / "raw")
    da = DataAgent(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
        data_dir=data_dir,
    )
    df = da.get_feature_data(use_cache=True, force_refresh=False, add_indicators=True)
    df = _slice_df(df, start_date, end_date)

    if df is None or len(df) == 0:
        raise ValueError(f"No data returned for symbol={symbol} {start_date}~{end_date}")

    # Warn if the data coverage ends significantly earlier than the requested end_date
    actual_end = df.index[-1]
    requested_end = pd.to_datetime(end_date, format="%Y%m%d")
    # Allow up to 45 calendar days gap (weekends/holidays at period end)
    if (requested_end - actual_end).days > 45:
        print(
            f"[run_backtest] WARNING: data for {symbol} only covers up to "
            f"{actual_end.date()}, but {end_date} was requested. "
            f"The equity curve will be shorter than expected."
        )

    # ── Agents ──────────────────────────────────────────────────────────
    trend_agent = TrendAgent(trend_short, trend_long)
    mr_agent    = MeanReversionAgent(mr_window, mr_num_std, float(params.get("mr_conf_scale", 1.0)))
    ml_agent    = MLAgent(ml_lookback, ml_min_train, ml_prob_thr)

    engine = BacktestEngine(
        init_cash=init_cash,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
    )

    results: Dict[str, Tuple[Dict[str, Any], list]] = {}

    if "Trend-only" in run_strategies:
        m, t = engine.run_single_agent(df, trend_agent, "Trend-only")
        results["Trend-only"] = (m, t)

    if "MeanRev-only" in run_strategies:
        m, t = engine.run_single_agent(df, mr_agent, "MeanRev-only")
        results["MeanRev-only"] = (m, t)

    if "Fusion(no-ML)" in run_strategies:
        coord_no_ml = CoordinatorAgent(
            agent_weights={"trend": w_trend, "mean_reversion": w_mr},
            ml_veto_enabled=False,
            min_edge=min_edge,
        )
        m, t = engine.run_fusion(
            df,
            {"trend": trend_agent, "mean_reversion": mr_agent},
            coord_no_ml,
            "Fusion(no-ML)",
        )
        results["Fusion(no-ML)"] = (m, t)

    if "Fusion(+ML-veto)" in run_strategies:
        coord_ml = CoordinatorAgent(
            agent_weights={"trend": w_trend, "mean_reversion": w_mr, "ml": w_ml},
            ml_veto_enabled=True,
            min_edge=min_edge,
        )
        m, t = engine.run_fusion(
            df,
            {"trend": trend_agent, "mean_reversion": mr_agent, "ml": ml_agent},
            coord_ml,
            "Fusion(+ML-veto)",
        )
        results["Fusion(+ML-veto)"] = (m, t)

    return _build_response(symbol, start_date, end_date, results)


# ════════════════════════════════════════════════════════════════════════════
#  Flask routes
# ════════════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def index():
    """Serve the dashboard.html"""
    dashboard_path = _ROOT / "dashboard.html"
    if dashboard_path.exists():
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html"}
    return "<h1>❌ dashboard.html not found</h1>", 404


@app.route("/dashboard.html", methods=["GET"])
def dashboard():
    """Serve dashboard.html explicitly"""
    dashboard_path = _ROOT / "dashboard.html"
    if dashboard_path.exists():
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html"}
    return "<h1>❌ dashboard.html not found</h1>", 404


@app.route("/scripts/<path:filename>", methods=["GET"])
def serve_scripts(filename):
    """Serve JavaScript files from scripts/"""
    scripts_dir = _ROOT / "scripts"
    return send_from_directory(scripts_dir, filename)


@app.route("/styles/<path:filename>", methods=["GET"])
def serve_styles(filename):
    """Serve CSS files from styles/"""
    styles_dir = _ROOT / "styles"
    return send_from_directory(styles_dir, filename)


@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok"})


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    """Run a backtest with the given parameters"""
    global _latest_result
    params = request.get_json(silent=True) or {}
    try:
        result = run_backtest(params)
        _latest_result = result
        return jsonify(result)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/results", methods=["GET"])
def api_results():
    """Get the latest backtest results (or sample data)"""
    if _latest_result is not None:
        return jsonify(_latest_result)
    # Fall back to sample-trades.json
    if _SAMPLE_PATH.exists():
        with open(_SAMPLE_PATH, encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({"error": "No results available. Run a backtest first."}), 404


# ════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═════════════════════════════════════════════════════════════��══════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent Quant Dashboard API Server")
    parser.add_argument("--port", type=int, default=5000, help="Port to run on (default: 5000)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--debug", action="store_true", default=False, help="Enable debug mode")
    args = parser.parse_args()

    print(f"""
╔════════════════════════════════════════════════════════════════╗
║  🚀 Agent Quant Dashboard API Server                           ║
╠════════════════════════════════════════════════════════════════╣
║  ✓ Dashboard:   http://{args.host}:{args.port}                 
║  ✓ API Health:  http://{args.host}:{args.port}/api/health      
║  ✓ Backtest:    POST http://{args.host}:{args.port}/api/backtest
╚════════════════════════════════════════════════════════════════╝
    """)
    
    app.run(host=args.host, port=args.port, debug=args.debug)