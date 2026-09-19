#!/usr/bin/env python3
"""Aggregate the multi-seed OPD monitors into the Sec 7.3 table (per-arm medians + spread).
Reproducible口径:
  H_first/H_last/H_mean = over monitor rows (H); H_min = min H (episodic collapse flag if <0.1)
  o_mean  = mean top-k overlap;  corr(o,H) = Pearson over the trajectory
  final_revKL = last logged surrogate loss (matches the paper's 'final rev-KL' column)
Per arm (across seeds): median [IQR], n, collapse_frac (runs with H_min<0.1), H_last vs H_mean.
Writes: ms_results.json + ms_table.md (paste-ready) + ms_seeds.csv.
"""
import glob, json, os, math
import numpy as np

DATA = os.environ.get("MS_DATA", "./results")
ARMS = ["direct", "ladder", "probe"]

def pearson(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if a.std() == 0 or b.std() == 0: return float("nan")
    return float(np.corrcoef(a, b)[0, 1])

def analyze_one(rows):
    H = np.array([r["H"] for r in rows], float)
    o = np.array([r["o"] for r in rows], float)
    return dict(
        n=int(len(rows)),
        H_first=float(H[0]), H_last=float(H[-1]), H_mean=float(H.mean()), H_min=float(H.min()),
        o_mean=float(o.mean()), corrOH=pearson(o, H),
        final_revKL=float(rows[-1]["loss"]),
        collapsed=int(H.min() < 0.1),
    )

def main():
    per_seed = {}
    for arm in ARMS:
        files = sorted(glob.glob(os.path.join(DATA, f"opd_monitor_{arm}_s*.json")))
        for f in files:
            seed = f.split("_s")[-1].split(".")[0]
            rows = json.load(open(f))
            per_seed[f"{arm}_s{seed}"] = analyze_one(rows)

    # per-arm aggregates
    agg = {}
    for arm in ARMS:
        runs = [v for k, v in per_seed.items() if k.startswith(arm + "_s")]
        if not runs:
            agg[arm] = None; continue
        def stat(key):
            x = np.array([r[key] for r in runs], float)
            return dict(median=float(np.median(x)),
                        iqr=[float(np.percentile(x, 25)), float(np.percentile(x, 75))],
                        mean=float(x.mean()), n=int(len(x)))
        agg[arm] = dict(
            H_last=stat("H_last"), H_mean=stat("H_mean"), H_min=stat("H_min"),
            o_mean=stat("o_mean"), final_revKL=stat("final_revKL"),
            corrOH=dict(mean=float(np.nanmean([r["corrOH"] for r in runs])),
                        n=int(len(runs))),
            collapse_frac=dict(med=float(np.mean([r["collapsed"] for r in runs])), n=int(len(runs))),
        )

    out = dict(per_seed=per_seed, per_arm=agg)
    json.dump(out, open(os.path.join(DATA, "ms_results.json"), "w"), indent=2)

    # ---- markdown table (aggregate, paste-ready) ----
    lines = ["| arm | seeds | n/run | H last med [IQR] | H mean med [IQR] | o mean | corr(o,H) | final rev-KL med [IQR] | collapse-frac (Hmin<0.1) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        a = agg[arm]
        if a is None:
            lines.append(f"| {arm} | — | — | no runs yet | | | | | |"); continue
        n = a["H_last"]["n"]; run_n = next(iter(per_seed.values()))["n"]
        def f(x): return f"{x['median']:.2f} [{x['iqr'][0]:.2f},{x['iqr'][1]:.2f}]"
        lines.append(
            f"| {arm} | {n} | {run_n} | {f(a['H_last'])} | {f(a['H_mean'])} | {a['o_mean']['median']:.2f} "
            f"| {a['corrOH']['mean']:+.2f} | {f(a['final_revKL'])} | {a['collapse_frac']['med']*100:.0f}% |")
    md = "\n".join(lines)
    open(os.path.join(DATA, "ms_table.md"), "w").write(md + "\n")

    # ---- per-seed csv ----
    csv = ["seed,arm,n,H_first,H_last,H_mean,H_min,o_mean,corrOH,final_revKL,collapsed"]
    for k, v in per_seed.items():
        arm, seed = k.split("_s")
        csv.append(f"{seed},{arm},{v['n']},{v['H_first']:.3f},{v['H_last']:.3f},{v['H_mean']:.3f},"
                   f"{v['H_min']:.3f},{v['o_mean']:.3f},{v['corrOH']:.3f},{v['final_revKL']:.2f},{v['collapsed']}")
    open(os.path.join(DATA, "ms_seeds.csv"), "w").write("\n".join(csv) + "\n")

    print(f"runs found: {len(per_seed)}")
    print(md)

if __name__ == "__main__":
    main()
