#!/usr/bin/env python3
"""
v3 evaluation (master round-3 spec):
  FULL mode      : 32 GSM8K-train questions (rows 40-71) x 4 samples
                   -> pass@1 / pass@4, unique answers, distinct-2,
                      self-4gram overlap, repetition, mean length,
                      teacher off-policy NLL
  RECOVERY mode  : 3 prefix types on disjoint train rows:
                   - 32 shared NAIVE bad prefixes (data/v3_eval/shared_bad.json)
                   - 12 gold-partial prefixes (train rows 24-35, first 50% of
                     gold trace)
                   - 12 corrupted-trace prefixes (same rows, ONE intermediate
                     '= n' result altered; question text untouched [Q3])
                   x 4 continuations -> RecoveryRate per type + Delta R
  BASE mode      : --adapter BASE = SFT-init checkpoint (pre-OPD reference)
GSM8K train rows: 0-23 badpool / 24-35 gold+corrupt / 40-71 full-eval.
"""
import json, os, re, time
import numpy as np, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE = "Qwen/Qwen2.5-1.5B-Instruct"
TEACHER = "Qwen/Qwen2.5-7B-Instruct"
OUT = os.environ.get("EVAL_OUT", os.path.expanduser("./eval_out"))
MAX_NEW = 256
N_FULL, K_FULL = int(os.environ.get("EVAL_FULL_N", "16")), int(os.environ.get("EVAL_FULL_K", "3"))
N_BAD = int(os.environ.get("EVAL_BAD", "24"))
N_GC = int(os.environ.get("EVAL_GC", "8"))   # gold+corrupt each
N_REC, K_REC = 4, int(os.environ.get("EVAL_KREC", "2"))


def load_train_rows(a, b):
    from datasets import load_dataset
    try:
        ds = load_dataset("openai/gsm8k", split="train")
    except Exception:
        ds = load_dataset("openai/gsm8k", "main", split="train")
    rows = []
    for i in range(a, b + 1):
        r = ds[i]
        gold = r["answer"].split("####")[-1].strip()
        trace = r["answer"].split("####")[0].strip()
        rows.append((r["question"], gold, trace))
    return rows


def extract_ans(text):
    m = re.findall(r"####\s*(-?\d+(?:\.\d+)?)", text)
    if m:
        return m[-1].strip()
    m = re.findall(r"-?\d+(?:\.\d+)?", text)
    return m[-1].strip() if m else None


def same_num(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except Exception:
        return str(a) == str(b)


def ngrams(text, n=4):
    w = text.lower().split()
    return set(tuple(w[i:i + n]) for i in range(len(w) - n + 1))


def self_overlap(texts):
    sets = [ngrams(t) for t in texts]
    tot, cnt = 0.0, 0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            a, b = sets[i], sets[j]
            if a and b:
                tot += len(a & b) / len(a | b); cnt += 1
    return tot / cnt if cnt else 0.0


def repetition_rate(texts):
    tot, cnt = 0.0, 0
    for t in texts:
        g = ngrams(t, 8); wl = len(t.split())
        if g:
            tot += max(0, wl - 8 - len(g)) / max(wl, 1); cnt += 1
    return tot / cnt if cnt else 0.0


def make_model(adapter, dev="cuda"):
    m = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16).to(dev)
    if adapter and adapter != "BASE" and os.path.isdir(os.path.expanduser(adapter)):
        m = PeftModel.from_pretrained(m, os.path.expanduser(adapter))
    m.eval()
    return m


def gen(student, tok, text, k, temp=0.9):
    ids = tok(text, add_special=False)["input_ids"]
    out = []
    for _ in range(k):
        with torch.no_grad():
            y = student.generate(torch.tensor([ids], device="cuda"),
                                 max_new_tokens=MAX_NEW, do_sample=True,
                                 temperature=temp, top_p=0.95)
        out.append(tok.decode(y[0][len(ids):], skip_special_tokens=True))
    return out


def corrupt_trace(trace):
    """Q3: alter ONE intermediate 'a op b = n' result (n -> n+3), keep question
    semantics. Only the '= n' of an intermediate step, never the '#### gold'."""
    def repl(m):
        return f"{m.group(1)}={int(m.group(2)) + 3}"
    m = re.search(r"=\s*(\d+)(?![\d.])(?=\s*$|\s*\n)", trace, re.M)
    if m:
        return trace[:m.start()] + str(int(m.group(1)) + 3) + trace[m.end():]
    # fallback: first " = N" anywhere
    m = re.search(r"(=)\s*(\d+)", trace)
    if m:
        return repl(m)
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--mode", choices=["full", "recovery"], default="full")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    student = make_model(a.adapter)
    tok = AutoTokenizer.from_pretrained(BASE)
    teacher = make_model(None)
    tokT = AutoTokenizer.from_pretrained(TEACHER)
    print(f"[v3eval {a.name}/{a.mode}] loaded adapter={a.adapter}", flush=True)
    t0 = time.time()
    agg = {}

    if a.mode == "full":
        rows = load_train_rows(40, 71)[:N_FULL]
        S = []
        for i, (q, gold, _trace) in enumerate(rows):
            qf = q + "\nA:"
            conts = gen(student, tok, qf, K_FULL)
            ok = [same_num(extract_ans(qf + c), gold) for c in conts]
            uniq = len({extract_ans(qf + c) for c in conts})
            d2 = len({w for c in conts for w in ngrams(c, 2)}) / \
                 max(1, sum(len(ngrams(c, 2)) for c in conts))
            # teacher off-policy NLL (1 of K, cost control)
            nlls = []
            for c in conts[:1]:
                ctx = tokT(qf, add_special=False)["input_ids"][-200:]
                ct = tokT(c, add_special=False)["input_ids"]
                if len(ct) < 4:
                    continue
                full = ctx + ct
                with torch.no_grad():
                    lp = torch.log_softmax(
                        teacher(torch.tensor([full], device="cuda")).logits, -1)[0]
                st = len(ctx) - 1
                nlls.append(float(-lp[st:st + len(ct)].gather(
                    -1, torch.tensor(ct, device="cuda").unsqueeze(-1))
                    .squeeze(-1).mean()))
            S.append(dict(pass1=int(ok[0]), passK=int(any(ok)),
                          uniq_ans=uniq, d2=round(d2, 3),
                          self_ovl=round(self_overlap(conts), 3),
                          rep=round(repetition_rate(conts), 3),
                          avg_len=int(np.mean([len(c.split()) for c in conts])),
                          nll=round(float(np.mean(nlls)), 4) if nlls else None))
            if i % 8 == 0:
                print(f"[v3eval {a.name}] full {i}/{N_FULL} el={time.time()-t0:.0f}s",
                      flush=True)
        agg = dict(pass1=round(float(np.mean([s["pass1"] for s in S])), 3),
                   passK=round(float(np.mean([s["passK"] for s in S])), 3),
                   uniq_ans_mean=round(float(np.mean([s["uniq_ans"] for s in S])), 3),
                   d2_mean=round(float(np.mean([s["d2"] for s in S])), 3),
                   self_ovl_mean=round(float(np.mean([s["self_ovl"] for s in S])), 3),
                   rep_mean=round(float(np.mean([s["rep"] for s in S])), 3),
                   avg_len_mean=float(np.mean([s["avg_len"] for s in S])),
                   nll_mean=round(float(np.nanmean([s["nll"] for s in S])), 4))
        out = dict(name=a.name, adapter=a.adapter, mode="full", agg=agg, samples=S)

    else:  # recovery
        badpool = json.load(open(os.path.join(OUT, "shared_bad.json")))
        bads = badpool[:N_BAD]
        gcs = load_train_rows(24, 24 + N_GC - 1)
        rec = {"bad_shared": [], "gold": [], "corrupt": []}
        cases = []
        for (q, gold, prefix) in [(b["q"], b["gold"], b["prefix"]) for b in bads]:
            cases.append(("bad_shared", q, gold, prefix))
        for i, (q, gold, trace) in enumerate(gcs):
            half = trace[:int(len(trace) * 0.5)]
            cases.append(("gold", q, gold, q + "\nA: " + half))
            ctrace = corrupt_trace(trace[:int(len(trace) * 0.6)])
            if ctrace and ctrace != trace[:int(len(trace) * 0.6)]:
                cases.append(("corrupt", q, gold, q + "\nA: " + ctrace))
        for ci, (tag, q, gold, prefix) in enumerate(cases):
            rs = [int(same_num(extract_ans(prefix + c), gold))
                  for c in gen(student, tok, prefix, K_REC)]
            rec[tag].append(float(np.mean(rs)))
            cases[ci] = (tag, q[:60], gold, prefix[:120], rs)
            if ci % 12 == 0:
                print(f"[v3eval {a.name}] rec {ci}/{len(cases)} el={time.time()-t0:.0f}s",
                      flush=True)
        R = {t: round(float(np.mean(v)), 3) for t, v in rec.items() if v}
        agg = dict(**R,
                   delta_bad_gold=round(R.get("gold", 0) - R.get("bad_shared", 0), 3))
        out = dict(name=a.name, adapter=a.adapter, mode="recovery",
                   agg=agg, cases=[dict(tag=c[0], q=c[1], gold=c[2],
                                        prefix_head=c[3], scores=c[4]) for c in cases])

    out["agg"]["wall_s"] = int(time.time() - t0)
    path = os.path.join(OUT, f"v3_eval_{a.name}_{a.mode}.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    print(f"[v3eval {a.name}/{a.mode}] DONE -> {path} "
          f"agg={json.dumps(out['agg'], ensure_ascii=False)[:220]} "
          f"el={out['agg']['wall_s']}s", flush=True)


if __name__ == "__main__":
    main()
