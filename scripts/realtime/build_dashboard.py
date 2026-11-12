#!/usr/bin/env python3
"""
Bundle the real-time run + the frozen validation results into the single-file dashboard.

In : 08_results/realtime/dashboard_data.json   (run_realtime.py)
     08_results/realtime/case_layers.json      (export_case_layers.py, run once)
     08_results/stage1/*.csv, 08_results/stage2_connected/*.csv
     scripts/realtime/dashboard_template.html
Out: 08_results/realtime/FloodGuard_Live.html  (self-contained, opens offline)
"""
import json, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.config import p  # noqa: E402


def csv_records(path, **kw):
    return json.loads(pd.read_csv(path, **kw).round(4).to_json(orient="records"))


def ensemble_scores():
    """Operational Stage-1 ensemble (mean of Ridge, RF, XGBoost) on the same 2022-2025 test rows."""
    import numpy as np
    import yaml
    thr = yaml.safe_load(open(p("config", "floodguard.yaml")))["thresholds"]
    out = {}
    for st in ["Naraj", "Kishan Nagar", "Tarpur"]:
        d = pd.read_csv(p("08_results", "stage1", f"predictions_{st.lower().replace(' ', '_')}.csv"))
        y, ens, per = d.observed, (d.Ridge + d.RandomForest + d.XGBoost) / 3, d.Persistence
        w = thr[st]["warning"]
        o, f = y > w, ens > w
        tp, fp, fn = int((o & f).sum()), int((~o & f).sum()), int((o & ~f).sum())
        out[st] = dict(RMSE=round(float(np.sqrt(((ens - y) ** 2).mean())), 4),
                       MAE=round(float((ens - y).abs().mean()), 4),
                       skill_vs_persistence=round(float(1 - ((ens - y) ** 2).mean() / ((per - y) ** 2).mean()), 4),
                       CSI=round(tp / (tp + fp + fn), 4) if tp + fp + fn else None, n=int(len(d)))
    return out


def main():
    d = json.loads(p("08_results", "realtime", "dashboard_data.json").read_text())
    cases = json.loads(p("08_results", "realtime", "case_layers.json").read_text())
    s2 = p("08_results", "stage2_connected")
    d["validation"] = dict(
        stage1=csv_records(p("08_results", "stage1", "metrics_all_targets.csv")),
        lead_time=csv_records(p("08_results", "stage1", "lead_time_all_targets.csv")),
        stage2_cv=csv_records(s2 / "cv_summary.csv").copy(),
        stage2_tuned=pd.read_csv(s2 / "cv_tuned_summary.csv", index_col=0)["value"].to_dict(),
        stage2_ablation=csv_records(s2 / "ablation.csv"),
        shap_global=csv_records(p("08_results", "stage2_improved", "shap_global_importance.csv")),
        cases=cases, ensemble=ensemble_scores())
    d["s1_2026"] = json.loads(p("08_results", "realtime", "s1_2026.json").read_text())
    imp = p("08_results", "stage2_improved")
    d["improvement"] = dict(
        stage1=csv_records(p("08_results", "stage1", "operational_vs_frozen.csv")),
        stage1_2026=csv_records(p("08_results", "realtime", "stage1_2026_frozen_vs_operational.csv")),
        stage2=json.loads((imp / "summary.json").read_text()),
        stage2_variants=csv_records(imp / "variants_summary.csv"),
        stage2_events=csv_records(imp / "per_event.csv"))
    for r, name in zip(d["validation"]["stage2_cv"], pd.read_csv(s2 / "cv_summary.csv", index_col=0).index):
        r["model"] = name
    for r, name in zip(d["validation"]["stage2_ablation"], pd.read_csv(s2 / "ablation.csv", index_col=0).index):
        r["step"] = name
    blob = json.dumps(d, separators=(",", ":"), default=str).replace("</", "<\\/")
    tpl = (Path(__file__).parent / "dashboard_template.html").read_text(encoding="utf-8")
    engine = (Path(__file__).parent / "engine.js").read_text(encoding="utf-8")
    app = (Path(__file__).parent / "dashboard_app.js").read_text(encoding="utf-8")
    assert "</script" not in engine.lower() and "</script" not in app.lower()
    html = tpl.replace("__FG_ENGINE__", engine).replace("__FG_DATA__", blob).replace("__FG_APP__", app)
    out = p("08_results", "realtime", "FloodGuard_Live.html")
    out.write_text(html, encoding="utf-8")
    print("wrote", out, round(out.stat().st_size / 1e6, 2), "MB")
    # the shareable / served copy: page, model file and the live server's static data
    import shutil
    dash = p("09_dashboard"); dash.mkdir(exist_ok=True)
    for name in ("FloodGuard_Live.html", "engine_assets.json", "live_static.json"):
        shutil.copyfile(p("08_results", "realtime", name), dash / name)
    print("copied page, engine_assets.json and live_static.json to", dash)


if __name__ == "__main__":
    main()
