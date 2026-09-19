#!/usr/bin/env python3
"""
v3 shared bad-prefix pool (master round-3: 'shared bad prefixes from naive').
Runs the NAIVE FINAL adapter on GSM8K-TRAIN questions; keeps rollouts whose
final answer is WRONG or whose teacher NLL is high; truncates to 64 tokens.
All arms are then evaluated for recovery on the SAME pool -> fair comparison.
Writes ./eval_out/shared_bad.json  (q, prefix, gold, nll).
"""
import json, os, re, time
import numpy as np, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE = "Qwen/Qwen2.5-1.5B-Instruct"
TEACHER = "Qwen/Qwen2.5-7B-Instruct"
NAIVE_ADAPTER = os.environ.get("NAIVE_ADAPTER",
                               "~/multiseed_v2/data/adapter_v3_naive_s42")
OUT = os.environ.get("EVAL_OUT", os.path.expanduser("./eval_out"))
N_Q = int(os.environ.get("BAD_NQ", "24"))
K = 4
MAX_NEW = 128


def load_gsm8k_train(n):
    from datasets import load_dataset
    try:
        ds = load_dataset("openai/gsm8k", split="train")
    except Exception:
        ds = load_dataset("openai/gsm8k", "main", split="train")
    rows = []
    for r in ds:
        gold = r["answer"].split("####")[-1].strip()
        rows.append((r["question"], gold, r["answer"]))
        if len(rows) >= n:
            break
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


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = load_gsm8k_train(N_Q)
    student = PeftModel.from_pretrained(
        AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16),
        os.path.expanduser(NAIVE_ADAPTER)).to("cuda").eval()
    tok = AutoTokenizer.from_pretrained(BASE)
    teacher = AutoModelForCausalLM.from_pretrained(
        TEACHER, torch_dtype=torch.bfloat16).to("cuda").eval()
    tokT = AutoTokenizer.from_pretrained(TEACHER)
    print(f"[badpool] naive adapter = {NAIVE_ADAPTER}", flush=True)

    pool = []
    t0 = time.time()
    for i, (q, gold, trace) in enumerate(rows):
        qf = q + "\nA:"
        for kk in range(K):
            with torch.no_grad():
                y = student.generate(torch.tensor([tok(qf, add_special=False)["input_ids"]],
                                                  device="cuda"),
                                     max_new_tokens=MAX_NEW, do_sample=True,
                                     temperature=0.9, top_p=0.95)
            cont = tok.decode(y[0][len(tok(qf, add_special=False)["input_ids"]):],
                              skip_special_tokens=True)
            wrong = not same_num(extract_ans(qf + cont), gold)
            # teacher NLL of the continuation
            ctx = tokT(qf, add_special=False)["input_ids"][-200:]
            ct = tokT(cont, add_special=False)["input_ids"]
            if len(ct) < 4:
                continue
            full = ctx + ct
            with torch.no_grad():
                lp = torch.log_softmax(
                    teacher(torch.tensor([full], device="cuda")).logits, -1)[0]
            st = len(ctx) - 1
            nll = float(-lp[st:st + len(ct)].gather(
                -1, torch.tensor(ct, device="cuda").unsqueeze(-1)).squeeze(-1).mean())
            if wrong or nll > 4.0:
                prefix = cont[:int(len(cont) * 0.6)] or cont
                pool.append(dict(q=q, gold=gold, prefix=qf + " " + prefix,
                                 nll=round(nll, 3), wrong=int(wrong)))
        if (i + 1) % 6 == 0:
            print(f"[badpool] {i+1}/{N_Q} pool={len(pool)} el={time.time()-t0:.0f}s", flush=True)

    out = os.path.join(OUT, "shared_bad.json")
    json.dump(pool, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"[badpool] DONE {len(pool)} shared bad prefixes -> {out}", flush=True)


if __name__ == "__main__":
    main()
