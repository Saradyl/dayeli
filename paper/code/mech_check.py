#!/usr/bin/env python3
"""
Synthetic mechanism check for the Capacity-Gap Hypothesis (paper Sec. 4 / Sec. 7).

WHAT IT IS: a controlled, reproducible NumPy simulation of reverse-KL on-policy
distillation from a distant, multimodal teacher to a capacity-limited student.
It isolates one mechanism: a large teacher-student capacity gap under reverse KL
drives entropy collapse / loss of mode coverage, and a teacher LADDER (curriculum
of increasingly sharp teachers) mitigates it AT MATCHED STUDENT COMPUTE.

WHAT IT IS NOT: an LLM experiment, and NOT a test of the sequential top-k-overlap
criterion. This is a single-state (bandit) reduction; in this reduction the
overlap metric does not behave as the sequential hypothesis predicts (we report
that negative sub-result honestly rather than hide it). The overlap criterion
(H1/H3) requires the sequential student-rollout setting and is left to the
LLM-scale validation specified in Sec. 7.2.

Reverse-KL gradient (closed form; p = softmax(z), q = teacher):
    KL(p||q) = sum_v p_v (log p_v - log q_v)
    dKL/dz_j = p_j [ (log p_j - log q_j) - KL(p||q) ]
Student logits z = B w, B fixed (V x d) random projection, w trainable -> the
capacity gap is controlled by d (smaller d = larger gap).
"""
import numpy as np, json

V, K, N_MODES, SHARP = 40, 5, 5, 6.0
MODE_POS  = np.linspace(3, V - 4, N_MODES).astype(int)
ALPHAS    = [0.15, 0.35, 0.55, 0.75, 1.0]   # ladder rungs (uniform-mixture weight)
TOTAL_STEPS, LR, COV_TH = 1500, 0.10, 0.05
SEEDS     = list(range(40))                  # statistical power

def softmax(z): z = z - z.max(); e = np.exp(z); return e / e.sum()
def target_teacher(sharp=SHARP):
    lo = np.zeros(V); lo[MODE_POS] = sharp; return softmax(lo)
def ladder_teachers(p):
    uni = np.full(V, 1.0 / V); return [(1 - a) * uni + a * p for a in ALPHAS]
def entropy(p):    return float(-np.sum(p * np.log(p + 1e-12)))
def rev_kl(p, q):  return float(np.sum(p * (np.log(p + 1e-12) - np.log(q + 1e-12))))
def topk_overlap(p, q, k=K): return len(set(np.argsort(p)[-k:]) & set(np.argsort(q)[-k:])) / k
def coverage(p):   return int(np.sum(p[MODE_POS] >= COV_TH))
def grad_z(p, q, kl): return p * ((np.log(p + 1e-12) - np.log(q + 1e-12)) - kl)

P_STAR = target_teacher()
H_T    = entropy(P_STAR)                     # teacher entropy (reference)
COLLAPSE_TH = 0.5 * H_T                      # "collapsed" if final H < 50% of teacher H

def make_student(d, seed):
    rng = np.random.RandomState(1000 + seed)
    return rng.randn(V, d) / np.sqrt(d), rng.randn(d) * 0.1

def train(w, B, q, steps, hist=None, tag=""):
    for t in range(steps):
        p = softmax(B @ w); kl = rev_kl(p, q)
        w = w - LR * (B.T @ grad_z(p, q, kl))
        if hist is not None and t % 10 == 0:
            hist.append(dict(step=len(hist) * 10, H=entropy(p), o=topk_overlap(p, P_STAR), tag=tag))
    return w

def run(d, seed, mode, record=False):
    B, w = make_student(d, seed); hist = [] if record else None
    if mode == "direct":
        w = train(w, B, P_STAR, TOTAL_STEPS, hist, "direct")
    else:
        sp = TOTAL_STEPS // len(ALPHAS)
        for a, q in zip(ALPHAS, ladder_teachers(P_STAR)):
            w = train(w, B, q, sp, hist, f"ladder{a}")
    p = softmax(B @ w)
    return dict(d=d, seed=seed, mode=mode, H=entropy(p), o=topk_overlap(p, P_STAR),
                cov=coverage(p), kl=rev_kl(p, P_STAR),
                collapsed=int(entropy(p) < COLLAPSE_TH), hist=hist)

def summarize(rows):
    H = np.array([r["H"] for r in rows]); cov = np.array([r["cov"] for r in rows])
    return dict(collapse_rate=float(np.mean([r["collapsed"] for r in rows])),
                H_median=float(np.median(H)), H_iqr=[float(np.percentile(H,25)), float(np.percentile(H,75))],
                cov_mean=float(cov.mean()), cov_std=float(cov.std()),
                kl_median=float(np.median([r["kl"] for r in rows])),
                o_mean=float(np.mean([r["o"] for r in rows])))

if __name__ == "__main__":
    print(f"# teacher: H={H_T:.3f} nats, coverage={coverage(P_STAR)}/{N_MODES}; collapse if final H<{COLLAPSE_TH:.3f}")
    print(f"# {len(SEEDS)} seeds/condition; matched student compute = {TOTAL_STEPS} steps; ladder rungs alpha={ALPHAS}\n")
    print(f"{'gap d':>6} {'cond':>7} {'collapse_rate':>14} {'H_median[IQR]':>20} {'coverage':>14} {'o_topk':>8} {'KL':>8}")
    summary = {}
    for d in (2, 3, 5):
        for mode in ("direct", "ladder"):
            rows = [run(d, s, mode) for s in SEEDS]
            sm = summarize(rows); summary[f"d{d}_{mode}"] = sm
            print(f"{d:>6} {mode:>7} {sm['collapse_rate']*100:>12.1f}% "
                  f"{sm['H_median']:>10.3f}{str([round(x,2) for x in sm['H_iqr']]):>10} "
                  f"{sm['cov_mean']:>7.2f}+/-{sm['cov_std']:<4.2f} {sm['o_mean']:>7.2f} {sm['kl_median']:>7.3f}")

    # representative collapsing seed for the figure (direct collapses, ladder mitigates)
    figseed = 0
    rd = run(3, figseed, "direct", record=True); rl = run(3, figseed, "ladder", record=True)
    print(f"\n# figure seed d=3 seed={figseed}: direct H={rd['H']:.3f} cov={rd['cov']} | ladder H={rl['H']:.3f} cov={rl['cov']}")

    # H1/H3 overlap sub-result (reported honestly, expected to be inconclusive here)
    o = np.array([h["o"] for h in rd["hist"]]); H = np.array([h["H"] for h in rd["hist"]])
    corr = float(np.corrcoef(o, H)[0, 1])
    direction = ("consistent with H1 (positive)" if corr > 0.3
                 else "contrary to H1 (negative)" if corr < -0.3 else "inconclusive")
    print(f"# overlap sub-result (bandit, d=3 direct, single seed): corr(top-k overlap, entropy) = {corr:+.3f} "
          f"-> {direction}; but single-state/single-seed, NOT conclusive. The sequential top-k-overlap "
          f"criterion (H1/H3) is deferred to the LLM-scale validation in Sec. 7.2.")

    summary["_meta"] = dict(V=V,K=K,N_MODES=N_MODES,SHARP=SHARP,ALPHAS=ALPHAS,TOTAL_STEPS=TOTAL_STEPS,
                            LR=LR,COV_TH=COV_TH,n_seeds=len(SEEDS),teacher_H=H_T,collapse_th=COLLAPSE_TH,
                            overlap_entropy_corr_d3direct=corr,
                            fig_direct_H=rd['H'], fig_ladder_H=rl['H'],
                            fig_direct_cov=rd['cov'], fig_ladder_cov=rl['cov'])
    json.dump(summary, open("mech_results.json","w"), indent=2)

    # ---- figure ----
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(9.2, 3.4))
        sd = [h["step"] for h in rd["hist"]]
        ax[0].plot(sd, [h["H"] for h in rd["hist"]], color="#c0392b", label="direct (reverse-KL)")
        ax[0].plot(sd, [h["H"] for h in rl["hist"]], color="#2471a3", label="teacher ladder")
        ax[0].axhline(H_T, ls="--", c="gray", lw=1, label=f"teacher H={H_T:.2f}")
        ax[0].axhline(COLLAPSE_TH, ls=":", c="black", lw=1, label="collapse threshold")
        ax[0].set_xlabel("student step"); ax[0].set_ylabel("policy entropy H (nats)")
        ax[0].set_title("Entropy collapse (d=3, one seed)"); ax[0].legend(fontsize=7); ax[0].set_ylim(0, 2.0)
        ds = [2,3,5]
        wd = 0.36; xs = np.arange(len(ds))
        cr_dir = [summary[f"d{d}_direct"]["collapse_rate"]*100 for d in ds]
        cr_lad = [summary[f"d{d}_ladder"]["collapse_rate"]*100 for d in ds]
        ax[1].bar(xs-wd/2, cr_dir, wd, color="#c0392b", label="direct")
        ax[1].bar(xs+wd/2, cr_lad, wd, color="#2471a3", label="ladder")
        ax[1].set_xticks(xs); ax[1].set_xticklabels([f"d={d}" for d in ds])
        ax[1].set_ylabel("collapse rate (%)"); ax[1].set_title("Collapse rate by capacity gap")
        ax[1].legend(fontsize=8); ax[1].set_ylim(0, 100)
        plt.tight_layout(); plt.savefig("mech_collapse.pdf"); plt.savefig("mech_collapse.png", dpi=150)
        print("# wrote mech_collapse.pdf / .png")
    except Exception as e:
        print("# figure skipped:", e)
    print("# wrote mech_results.json")
