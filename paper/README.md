# The Distillation Budget

**Information Accounting and Budget-Aware Personalization of Language Models**

*By Saradyl Dayeli Nida* — [`paper.pdf`](paper.pdf) (19 pp.) · [DOCX](paper.docx) · [LaTeX source](latex/distillation_budget.tex)

---

## What this is

A position/protocol paper on knowledge distillation (KD). It reframes KD not as
compression but as **estimating a black-box teacher over a rate-limited query
channel, under an information-accounting budget**. From that view it derives three
operational principles, a measurable capacity-gap collapse hypothesis, and a
structured-probing objective (MIDAS), then specifies a one-week, sub-$200
personal-model recipe with a **pre-registered evaluation protocol**.

The paper ships with two completed, clearly-labelled experiments:

- **Synthetic mechanism check** — a single-state bandit reverse-KL reduction
  reproducing entropy collapse and the ladder's diversity/fidelity trade-off.
- **Multi-seed, three-phase LLM-scale study** — 30+ open-weight runs
  (Qwen2.5 1.5B ← 7B, public GSM8K) on a single DGX Spark.

## Headline findings

| # | Finding | Direction |
|---|---|---|
| 1 | Distillation lowers the student's **off-policy NLL** under the teacher by **2.5–3×**, while preserving task accuracy and trace diversity | confirms predicted information flow |
| 2 | A population of **student-induced states the 7B teacher cannot recover from** by direct continuation | localizes the residual collapse |
| 3 | **Top-$k$ overlap** and **teacher-low-probability mass** are both **null** as leading indicators of collapse (15-seed) | falsified |
| 4 | **Last-position token entropy is non-identifiable** on low-conditional-entropy tasks | measurement corrected |
| 5 | A **recovery-aware** arm (offline teacher-recovery supervision) reaches the lowest off-policy NLL; its recovery-rate gain does not clear seed noise on a 24-prefix probe | confirmed + reported |

Nulls and negative results are reported in place, not suppressed.

## Repository layout

```
paper/
├── paper.pdf / paper.docx        # the paper (real-name version)
├── latex/                        # LaTeX source (single \tex, std packages only)
├── figures/mech_collapse.png     # synthetic mechanism figure
├── code/                         # all training/eval/analysis scripts
│   ├── mech_check.py             #   synthetic bandit mechanism (§7.2)
│   ├── v1_train_ms.py            #   Phase 1: 3 arms × 5 seeds (matched-compute ladder)
│   ├── v2_train_ms.py            #   Phase 2: 5 stabilizer arms × 3 seeds
│   ├── v3_train.py               #   Phase 3: naive / stabilized / recovery × 3 seeds
│   ├── v3_make_badpool.py        #   shared naive bad-prefix pool
│   ├── v3_make_recovery.py       #   offline teacher-recovery pairs (peak-mem-safe)
│   ├── v3_eval.py                #   recovery test + off-policy NLL + trace diversity
│   └── analyze_ms.py / analyze_v2.py   # seed aggregation
└── results/                      # raw per-seed / per-checkpoint numbers
    ├── synthetic_mech_results.json
    ├── v1_multiseed_results.json + _per_seed.csv
    ├── v2_stabilization_results.json + _per_seed.csv + v2_table.md
    └── v3_eval_*.json            # Phase 3: 3 arms × 3 seeds, recovery + full endpoints
```

## Reproducing

Hardware used: one **NVIDIA DGX Spark (GB10, 121 GB unified memory)**, all models
open-weight and resident on-box. No API calls, no private data. Models:
`Qwen/Qwen2.5-1.5B-Instruct` (student), `Qwen/Qwen2.5-3B`/`7B-Instruct`
(teachers / ladder). Data: public `openai/gsm8k`.

Point the scripts at your local data + model cache:

```bash
export DYL_HOME=/path/to/your/dyl   # dir with data/adapter_sft + data/sft.jsonl
export EVAL_OUT=/path/to/eval_out
python code/v3_train.py --config <arm>.yaml --seed 42 --runid v3_recovery_s42
```

Effective batch was bounded to 2 by the unified-memory constraint (shared box);
this is stated as a hardware assumption in the paper, not claimed otherwise.

## License

Repository code: MIT (see top-level `LICENSE`). The paper PDF is released under
CC BY 4.0.
