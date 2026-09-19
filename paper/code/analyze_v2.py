#!/usr/bin/env python3
"""Aggregate v2 Phase-1 stabilization arms.
Key question (per guru): does naive collapse while replay+KL+clip recovers non-collapse?
Metrics per arm (across seeds): median H_last [IQR], H_mean, H_min, M10_last,
CT_last, mean corr(M10, H)  <- new leading-indicator test (replaces top-k overlap null),
final loss, collapse-frac (H_min<0.1), d1_last.
Writes v2_table.md + v2_results.json + v2_seeds.csv under ~/multiseed_v2/data.
"""
import glob, json, os
import numpy as np

DATA = os.environ.get("MS_DATA", "./results")
ARMS = ["naive", "replay", "kl", "replay_kl", "stabilized"]

def pearson(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if a.std() == 0 or b.std() == 0: return float("nan")
    return float(np.corrcoef(a, b)[0, 1])

def analyze(rows):
    H = np.array([r["H"] for r in rows], float)
    M = np.array([r["M10"] for r in rows], float)
    return dict(
        n=int(len(rows)),
        H_first=float(H[0]), H_last=float(H[-1]), H_mean=float(H.mean()), H_min=float(H.min()),
        M10_last=float(M[-1]), M10_mean=float(M.mean()),
        CT_last=float(rows[-1]["CT"]),
        d1_last=float(rows[-1]["d1"]),
        corrMH=pearson(M, H),            # does low-prob mass predict entropy? (guru metric #1)
        final_loss=float(rows[-1]["loss"]),
        collapsed=int(H.min() < 0.1),
    )

def main():
    per_seed = {}
    for arm in ARMS:
        for f in sorted(glob.glob(os.path.join(DATA, f"opd_monitor_v2_{arm}_s*.json"))):
            seed = f.split("_s")[-1].split(".")[0]
            per_seed[f"v2_{arm}_s{seed}"] = analyze(json.load(open(f)))

    # per-arm aggregate
    agg = {}
    for arm in ARMS:
        runs = [v for k, v in per_seed.items() if k.startswith(f"v2_{arm}_s")]
        if not runs:
            agg[arm] = None; continue
        def s(key):
            x = np.array([r[key] for r in runs], float)
            return dict(median=float(np.median(x)),
                        iqr=[float(np.percentile(x,25)), float(np.percentile(x,75))], n=int(len(x)))
        agg[arm] = dict(H_last=s("H_last"), H_mean=s("H_mean"), M10_last=s("M10_last"),
                        CT_last=s("CT_last"), d1_last=s("d1_last"),
                        corrMH=dict(mean=float(np.nanmean([r["corrMH"] for r in runs])), n=int(len(runs))),
                        collapse_frac=dict(v=float(np.mean([r["collapsed"] for r in runs])), n=int(len(runs))))

    json.dump(dict(per_seed=per_seed, per_arm=agg),
              open(os.path.join(DATA, "v2_results.json"), "w"), indent=2)

    lines = ["| arm | seeds | H_last med[IQR] | H_mean | M10_last | CT_last | d1 | corr(M10,H) | collapse-frac |",
             "|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        a = agg[arm]
        if a is None:
            lines.append(f"| {arm} | - | no runs yet | | | | | | |"); continue
        n = a["H_last"]["n"]
        def f(x): return f"{x['median']:.2f}[{x['iqr'][0]:.2f},{x['iqr'][1]:.2f}]"
        lines.append(f"| {arm} | {n} | {f(a['H_last'])} | {a['H_mean']['median']:.2f} "
                     f"| {a['M10_last']['median']:.3f} | {a['CT_last']['median']:.2f} "
                     f"| {a['d1_last']['median']:.2f} | {a['corrMH']['mean']:+.2f} "
                     f"| {a['collapse_frac']['v']*100:.0f}% |")
    md = "\n".join(lines)
    open(os.path.join(DATA, "v2_table.md"), "w").write(md + "\n")

    csv = ["seed,arm,n,H_first,H_last,H_mean,H_min,M10_last,CT_last,d1,corrMH,final_loss,collapsed"]
    for k, v in per_seed.items():
        arm, seed = k[len("v2_"):].split("_s")
        csv.append(f"{seed},{arm},{v['n']},{v['H_first']:.3f},{v['H_last']:.3f},{v['H_mean']:.3f},"
                   f"{v['H_min']:.3f},{v['M10_last']:.3f},{v['CT_last']:.2f},{v['d1_last']:.2f},"
                   f"{v['corrMH']:.3f},{v['final_loss']:.2f},{v['collapsed']}")
    open(os.path.join(DATA, "v2_seeds.csv"), "w").write("\n".join(csv) + "\n")

    print(f"runs found: {len(per_seed)}/15")
    print(md)

if __name__ == "__main__":
    main()
