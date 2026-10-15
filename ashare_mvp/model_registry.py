"""Portable LightGBM manifests and prediction using the original feature contract."""
from pathlib import Path
from datetime import datetime, timezone
from importlib.metadata import version
import hashlib
import json
import platform
import shutil
import subprocess
import lightgbm as lgb
import pandas as pd
from .factors import default_library, technical_panel, snapshot


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def factor_digest():
    raw = Path(__file__).with_name("factors.py").read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def code_reference():
    root=Path(__file__).resolve().parent.parent
    base=["git","-c",f"safe.directory={root}","--no-optional-locks","-C",str(root)]
    try:
        head=subprocess.run(base+["rev-parse","HEAD"],capture_output=True,text=True,check=False)
        dirty=subprocess.run(base+["status","--porcelain"],capture_output=True,text=True,check=False)
        return {"code_git_commit":head.stdout.strip() if head.returncode==0 else None,"code_dirty":bool(dirty.stdout.strip()) if dirty.returncode==0 else None}
    except OSError:
        return {"code_git_commit":None,"code_dirty":None}


def record_models(run_directory, summary):
    root = Path(run_directory)
    audit = pd.read_csv(root / "training_audit.csv")
    contract = {"version": 1, "factor_source_sha256": factor_digest(),
                "preprocessing": {"cross_section": "same prediction date eligible universe", "winsor_quantiles": [0.01, 0.99],
                                  "standardization": "same-date mean/std ddof=0", "missing": "native NaN"},
                "eligibility": {"min_listing_days":365,"max_fundamental_age_days":550},
                "feature_source":Path(__file__).with_name("factors.py").read_text(encoding="utf-8-sig"),
                "features": summary["features"], "oneil": summary["config"]["oneil"]}
    contract["snapshot_schema"] = summary["data"].get("schema", "standard_dataset")
    if contract["snapshot_schema"] == "public_ratios_v1":
        source = Path(__file__).with_name("public_conversion.py").read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        contract["snapshot_source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
        contract["eligibility"]["fundamental_missing_policy"] = "native NaN; technical lookbacks required"
    reference=code_reference()
    for row in audit[audit.status.eq("trained")].itertuples():
        model = root / "models" / ("ranker_" + row.date.replace("-", "") + ".txt")
        booster = lgb.Booster(model_file=str(model))
        if booster.feature_name() != summary["features"]:
            raise ValueError("saved model feature order mismatch")
        metadata = dict(schema="quant_model_v1", model_version=model.stem + "-" + digest(model)[:12],
                        trained_at=datetime.fromtimestamp(model.stat().st_mtime, timezone.utc).isoformat(),
                        prediction_date=row.date, training_cutoff=row.cutoff, training_feature_end=row.train_end,
                        training_label_end=row.max_label_end, observed_data_cutoff=summary["asof"],
                        role="candidate", synthetic=summary["data"]["synthetic"], feature_contract=contract,
                        label_return_basis=summary.get("label_return_basis","total_return_index_ratio"),
                        account_nav_basis=summary.get("account_nav_basis","fractional_adjusted_shares"),
                        price_reconciliation=summary.get("price_reconciliation"),
                        hyperparameters=summary["config"], booster_parameters=booster.params,
                        dependencies={p:version(p) for p in ["lightgbm", "numpy", "pandas", "scikit-learn"]},
                        python=platform.python_version(), dataset=summary["data"], dataset_hashes=summary["input_hashes"],
                        model_sha256=digest(model), evaluation={"scope":"whole rolling run; not future evaluation of this latest model",
                            "nav":summary["nav"], "cohorts":summary["cohorts"]},
                        formal_release_approved=False)
        metadata.update(reference)
        model.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def check_model(directory):
    root = Path(directory)
    metadata = json.loads((root / "model.json").read_text(encoding="utf-8"))
    if metadata.get("schema") != "quant_model_v1" or digest(root / "model.txt") != metadata["model_sha256"]:
        raise ValueError("model schema/hash mismatch")
    for package, expected in metadata["dependencies"].items():
        if version(package) != expected:
            raise ValueError(f"{package} version differs; install bundle recorded dependencies")
    if platform.python_version().split(".")[:2] != metadata["python"].split(".")[:2]:
        raise ValueError("Python major/minor differs from trained environment")
    if metadata["feature_contract"]["factor_source_sha256"] != factor_digest():
        raise ValueError("factor implementation differs from trained model")
    if metadata["feature_contract"].get("snapshot_source_sha256"):
        source = Path(__file__).with_name("public_conversion.py").read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        if hashlib.sha256(source.encode()).hexdigest() != metadata["feature_contract"]["snapshot_source_sha256"]:
            raise ValueError("public snapshot implementation differs from trained model")
    booster = lgb.Booster(model_file=str(root / "model.txt"))
    if booster.feature_name() != metadata["feature_contract"]["features"]:
        raise ValueError("model feature contract mismatch")
    return booster, metadata


def export_model(run_directory, out):
    root, target = Path(run_directory), Path(out)
    if target.exists() and any(target.iterdir()):
        raise ValueError("model export output must be empty")
    files = sorted((root / "models").glob("ranker_*.json"))
    if not files:
        raise ValueError("run has no persisted model metadata; rerun or record_models first")
    manifest = files[-1]
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest, target / "model.json")
    shutil.copyfile(manifest.with_suffix(".txt"), target / "model.txt")
    for name in ["summary.json", "training_audit.csv", "cohort_performance.csv", "factor_importance.csv"]:
        shutil.copyfile(root / name, target / name)
    _, metadata = check_model(target)
    return {"model_version":metadata["model_version"], "role":metadata["role"], "synthetic":metadata["synthetic"]}


def predict_model(directory, data, out, asof=None):
    booster, metadata = check_model(directory)
    if data.metadata["synthetic"] != metadata["synthetic"]:
        raise ValueError("cannot use a synthetic model on real data or vice versa")
    end = pd.Timestamp(asof) if asof else data.calendar[-1]
    if end not in data.calendar or end < pd.Timestamp(metadata["prediction_date"]):
        raise ValueError("prediction must be an observed session at or after model creation")
    schema = metadata["feature_contract"].get("snapshot_schema", "standard_dataset")
    if data.metadata.get("schema", "standard_dataset") != schema:
        raise ValueError("prediction dataset schema differs from model contract")
    if schema == "public_ratios_v1":
        from .public_conversion import public_snapshot
        snap = public_snapshot(data, technical_panel(data), end)
    else:
        snap = snapshot(data, technical_panel(data), end)
    if len(snap) < metadata["hyperparameters"]["top_k"]:
        raise ValueError("insufficient eligible prediction universe")
    values = default_library().compute(snap, metadata["feature_contract"]["oneil"])
    features = metadata["feature_contract"]["features"]
    result = pd.DataFrame({"symbol":values.index, "score":booster.predict(values[features]), "date":end})
    result = result.sort_values(["score","symbol"], ascending=[False,True]).head(metadata["hyperparameters"]["top_k"])
    result["rank"] = range(1,len(result)+1)
    output = Path(out)
    if output.exists(): raise ValueError("prediction output exists")
    output.parent.mkdir(parents=True,exist_ok=True); result.to_csv(output,index=False)
    return result


if __name__ == "__main__":
    import argparse
    from .data import load
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    export=sub.add_parser("export"); export.add_argument("--run",required=True); export.add_argument("--out",required=True)
    check=sub.add_parser("check"); check.add_argument("--model",required=True)
    predict=sub.add_parser("predict"); predict.add_argument("--model",required=True); predict.add_argument("--data",required=True); predict.add_argument("--out",required=True); predict.add_argument("--asof")
    predict.add_argument("--exploratory",action="store_true",help="required for unverified public-ratio models")
    args=parser.parse_args()
    if args.command == "export": print(json.dumps(export_model(args.run,args.out),ensure_ascii=False))
    elif args.command == "check":
        _, metadata=check_model(args.model); print(json.dumps({"compatible":True,"version":metadata["model_version"],"role":metadata["role"]}))
    else:
        _, metadata = check_model(args.model)
        if metadata["feature_contract"].get("snapshot_schema") == "public_ratios_v1":
            if not args.exploratory: parser.error("unverified public models require --exploratory")
            from .public_conversion import load_public_data
            data = load_public_data(args.data,args.asof)
        else:
            data = load(args.data)
        result=predict_model(args.model,data,args.out,args.asof); print(f"Saved {len(result)} predictions")
