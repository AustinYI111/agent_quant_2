import argparse
import json

from .data import load
from .demo import make_demo, save
from .model import Config
from .pipeline import run


def main():
    parser = argparse.ArgumentParser(description="A-share monthly factor ranker research MVP")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="generate explicitly synthetic input data")
    demo.add_argument("--out", required=True)
    demo.add_argument("--stocks", type=int, default=40)
    demo.add_argument("--sessions", type=int, default=1512)
    demo.add_argument("--seed", type=int, default=42)
    public = sub.add_parser("download-public", help="download raw public data; does not certify PIT")
    public.add_argument("--out", required=True)
    public.add_argument("--start", required=True)
    public.add_argument("--end", required=True)
    public.add_argument("--symbols", nargs="+")
    public.add_argument("--max-symbols", type=int, default=5)
    public.add_argument("--all-symbols", action="store_true")
    audit = sub.add_parser("audit-public", help="inspect raw public download hashes, fields and dates")
    audit.add_argument("--data", required=True)
    universe = sub.add_parser("build-universe", help="build verified dated public A-share universe")
    universe.add_argument("--data", required=True)
    universe.add_argument("--out", required=True)
    conversion = sub.add_parser("convert-public", help="convert verified raw public inputs with unverified-vintage flags")
    conversion.add_argument("--data", required=True)
    conversion.add_argument("--out", required=True)
    research = sub.add_parser("run-public", help="explicitly exploratory public-data walk-forward")
    research.add_argument("--data", required=True)
    research.add_argument("--out", required=True)
    research.add_argument("--asof")
    research.add_argument("--horizon", choices=[3, 6, 12], type=int, default=6)
    research.add_argument("--oneil", action="store_true")
    research.add_argument("--exploratory", action="store_true")
    market = sub.add_parser("download-market", help="resumable daily full-cross-section raw-price acquisition")
    market.add_argument("--out", required=True)
    market.add_argument("--start", required=True)
    market.add_argument("--end", required=True)
    market.add_argument("--resume", action="store_true")
    finance = sub.add_parser("download-financials", help="resumable public quarterly financial cross-sections")
    finance.add_argument("--out", required=True)
    finance.add_argument("--start", required=True)
    finance.add_argument("--end", required=True)
    finance.add_argument("--resume", action="store_true")
    normalize_finance = sub.add_parser("normalize-financials", help="normalize quarterly records with publication/update visibility clocks")
    normalize_finance.add_argument("--data", required=True)
    normalize_finance.add_argument("--calendar", required=True)
    normalize_finance.add_argument("--out", required=True)
    alignment = sub.add_parser("align-market", help="join verified daily prices and visible financial events")
    alignment.add_argument("--market", required=True)
    alignment.add_argument("--financials", required=True)
    alignment.add_argument("--out", required=True)
    reconciliation = sub.add_parser("reconcile-prices", help="reconcile supplied raw prices and known cash/share events")
    reconciliation.add_argument("--prices", required=True)
    reconciliation.add_argument("--actions", required=True)
    reconciliation.add_argument("--calendar", required=True)
    reconciliation.add_argument("--out", required=True)
    dividends = sub.add_parser("download-dividends", help="resumable raw dividend evidence by security and execution year")
    dividends.add_argument("--out", required=True)
    dividends.add_argument("--symbols", nargs="+", required=True)
    dividends.add_argument("--start-year", type=int, required=True)
    dividends.add_argument("--end-year", type=int, required=True)
    dividends.add_argument("--resume", action="store_true")
    normalize_actions = sub.add_parser("normalize-dividends", help="normalize verified cash/share event records with visibility dates")
    normalize_actions.add_argument("--data", required=True)
    normalize_actions.add_argument("--calendar", required=True)
    normalize_actions.add_argument("--out", required=True)
    market_actions = sub.add_parser("download-market-dividends", help="complete paginated dividend plans by report period")
    market_actions.add_argument("--start", required=True)
    market_actions.add_argument("--end", required=True)
    market_actions.add_argument("--out", required=True)
    market_actions.add_argument("--resume", action="store_true")
    join_actions = sub.add_parser("reconcile-dividends", help="match market plans to independently normalized payment events")
    join_actions.add_argument("--market", required=True)
    join_actions.add_argument("--payments", required=True)
    join_actions.add_argument("--calendar", required=True)
    join_actions.add_argument("--out", required=True)
    issuer_actions = sub.add_parser("download-cninfo-dividends", help="issuer dividend histories with explicit payment dates")
    issuer_actions.add_argument("--symbols", nargs="+", required=True)
    issuer_actions.add_argument("--out", required=True)
    issuer_actions.add_argument("--resume", action="store_true")
    report = sub.add_parser("report", help="technical and financial research plus manually entered holdings")
    report.add_argument("--data", required=True)
    report.add_argument("--predictions", required=True)
    report.add_argument("--holdings", required=True)
    report.add_argument("--out", required=True)
    report.add_argument("--asof", required=True)
    report.add_argument("--ledger")
    compare = sub.add_parser("compare", help="matched baseline versus optional Oneil factors")
    compare.add_argument("--data", required=True)
    compare.add_argument("--out", required=True)
    compare.add_argument("--asof")
    compare.add_argument("--evaluation-start")
    compare.add_argument("--horizon", choices=[3, 6, 12], type=int, default=6)
    backtest = sub.add_parser("run", help="walk-forward training, rankings and performance")
    backtest.add_argument("--data", required=True)
    backtest.add_argument("--out", required=True)
    backtest.add_argument("--asof")
    backtest.add_argument("--horizon", type=int, choices=[3, 6, 12], default=6)
    backtest.add_argument("--oneil", action="store_true")
    backtest.add_argument("--top-k", type=int, default=20)
    backtest.add_argument("--train-months", type=int, default=60)
    backtest.add_argument("--min-train-groups", type=int, default=12)
    backtest.add_argument("--commission-bps", type=float, default=3)
    backtest.add_argument("--slippage-bps", type=float, default=5)
    backtest.add_argument("--sell-tax-bps", type=float, default=5)
    args = parser.parse_args()
    if args.command == "demo":
        save(make_demo(args.seed, args.stocks, args.sessions), args.out)
        print(f"SYNTHETIC data saved to {args.out}")
    elif args.command == "download-public":
        from .public_data import download_public
        result = download_public(args.out, args.start, args.end, args.symbols,
                                 None if args.all_symbols else args.max_symbols)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] in ["failed", "partial"]:
            raise SystemExit(1)
    elif args.command == "audit-public":
        from .public_data import readiness_audit
        print(json.dumps(readiness_audit(args.data), ensure_ascii=False, indent=2))
    elif args.command == "build-universe":
        from .universe import build_public_universe
        print(json.dumps(build_public_universe(args.data, args.out), ensure_ascii=False, indent=2))
    elif args.command == "convert-public":
        from .public_conversion import convert_public
        print(json.dumps(convert_public(args.data, args.out), ensure_ascii=False, indent=2))
    elif args.command == "run-public":
        from .public_conversion import run_public_research
        print(json.dumps(run_public_research(args.data, args.out, Config(train_horizon=args.horizon, oneil=args.oneil),
                                             args.asof, args.exploratory), ensure_ascii=False, indent=2))
    elif args.command == "download-market":
        from .market_download import download_market_daily
        result = download_market_daily(args.out, args.start, args.end, args.resume)
        print(json.dumps({k: v for k, v in result.items() if k not in ["files", "queries"]}, ensure_ascii=False, indent=2))
        if result["status"] != "downloaded_raw":
            raise SystemExit(1)
    elif args.command == "download-financials":
        from .financial_batch import download_financials
        result = download_financials(args.out, args.start, args.end, args.resume)
        print(json.dumps({k: v for k, v in result.items() if k != "quarters"}, ensure_ascii=False, indent=2))
        if result["status"] != "downloaded_raw":
            raise SystemExit(1)
    elif args.command == "normalize-financials":
        import pandas as pd
        from .financial_batch import normalize_financial_batch
        frame = pd.read_csv(args.calendar, dtype=str)
        if {"calendar_date", "is_trading_day"}.issubset(frame):
            dates = frame.loc[frame.is_trading_day == "1", "calendar_date"]
        elif "date" in frame:
            dates = frame.date
        else:
            raise ValueError("calendar requires date or calendar_date/is_trading_day")
        print(json.dumps(normalize_financial_batch(args.data, pd.to_datetime(dates), args.out), ensure_ascii=False, indent=2))
    elif args.command == "align-market":
        from .market_alignment import align_market_financials
        print(json.dumps(align_market_financials(args.market, args.financials, args.out), ensure_ascii=False, indent=2))
    elif args.command == "reconcile-prices":
        from .corporate_actions import reconcile_price_files
        print(json.dumps(reconcile_price_files(args.prices, args.actions, args.calendar, args.out), ensure_ascii=False, indent=2))
    elif args.command == "download-dividends":
        from .dividend_download import download_dividends
        result = download_dividends(args.out, args.symbols, args.start_year, args.end_year, args.resume)
        print(json.dumps({k: v for k, v in result.items() if k not in ["files", "queries"]}, ensure_ascii=False, indent=2))
        if result["status"] != "downloaded_raw":
            raise SystemExit(1)
    elif args.command == "normalize-dividends":
        from .dividend_normalization import normalize_dividends
        result = normalize_dividends(args.data, args.calendar, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "normalized":
            raise SystemExit(1)
    elif args.command == "download-market-dividends":
        from .dividend_market import download_dividend_market
        result = download_dividend_market(args.out, args.start, args.end, args.resume)
        print(json.dumps({k: v for k, v in result.items() if k != "periods"}, ensure_ascii=False, indent=2))
        if result["status"] != "downloaded_raw":
            raise SystemExit(1)
    elif args.command == "reconcile-dividends":
        from .dividend_reconciliation import reconcile_dividend_sources
        print(json.dumps(reconcile_dividend_sources(args.market, args.payments, args.calendar, args.out), ensure_ascii=False, indent=2))
    elif args.command == "download-cninfo-dividends":
        from .cninfo_dividends import download_cninfo_dividends
        result = download_cninfo_dividends(args.out, args.symbols, args.resume)
        print(json.dumps({k: v for k, v in result.items() if k not in ["securities", "symbols"]}, ensure_ascii=False, indent=2))
        if result["status"] != "downloaded_raw":
            raise SystemExit(1)
    elif args.command == "report":
        import pandas as pd
        from .research import daily_report, load_holdings
        picks = pd.read_csv(args.predictions, dtype={"symbol": str}, parse_dates=["date"])
        print(json.dumps(daily_report(load(args.data), picks, load_holdings(args.holdings), args.out,
                                     args.asof, args.ledger), ensure_ascii=False, indent=2))
    elif args.command == "compare":
        from .experiments import compare_oneil
        result = compare_oneil(load(args.data), args.out, Config(train_horizon=args.horizon), args.asof, args.evaluation_start)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        config = Config(train_horizon=args.horizon, oneil=args.oneil, top_k=args.top_k,
                        train_months=args.train_months, min_train_groups=args.min_train_groups,
                        commission_bps=args.commission_bps, slippage_bps=args.slippage_bps,
                        sell_tax_bps=args.sell_tax_bps)
        print(json.dumps(run(load(args.data), args.out, config, args.asof), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
