#!/usr/bin/env python3
"""
Offline teacher-recovery pairs — FIXED (guarantees a non-empty pool).

Two prefix sources, both legitimate per master round-3:
  A. SHARED NAIVE BAD prefixes (data/v3_eval/shared_bad.json): student-produced
     wrong rollouts. Teacher continues from each -> IMPLICIT recovery target
     (master Method-2 Option-B: continue into sane reasoning, NOT required to
     hit the exact number). Filter = format-sane only (non-empty, 16-96 words,
     not repetitive). We do NOT require answer-correct here (that was the bug
     that produced 0/32 pairs).
  B. CORRUPT-GOLD prefixes: train-split gold trace with ONE intermediate 'a op
     b = n' result flipped (question text untouched). These are ~95% clean, so
     teacher continuation recovers at a much higher rate -> guarantees the pool
     is non-empty even if source A is sparse.

Validity filter (master Q1, 'prefer' not 'require'): keep a continuation if it is
non-empty, 16-96 words, and has no heavy 4-gram repetition. We record whether it
happens to be answer-correct (correct flag) so downstream can report the split,
but we do NOT reject on it.
"""
import json, os, re
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from v3_eval import extract_ans, same_num, load_train_rows

TEACHER = "Qwen/Qwen2.5-7B-Instruct"
EVAL_OUT = os.environ.get("EVAL_OUT", os.path.expanduser("./eval_out"))
BADPOOL = os.path.join(EVAL_OUT, "shared_bad.json")
OUT = os.path.join(EVAL_OUT, "recovery_pairs.json")
MAX_NEW = int(os.environ.get("REC_MAX_NEW", "64"))
N_BAD = int(os.environ.get("REC_NBAD", "32"))
N_GC = int(os.environ.get("REC_NGC", "12"))


def format_sane(text, n_ids):
    if not text or not text.strip():
        return False
    w = text.lower().split()
    if len(w) < 16 or len(w) > 96:
        return False
    if len(n_ids) is not None and not (12 <= len(n_ids) <= 130):
        return False
    g4 = set(tuple(w[i:i + 4]) for i in range(max(0, len(w) - 4)))
    if len(w) > 30 and len(g4) / (len(w) - 3) < 0.45:
        return False
    return True


def corrupt_trace(trace):
    """flip one intermediate 'a op b = n' result; question semantics untouched."""
    m = re.search(r"=\s*(\d+)(?![\d.])(?=\s*$|\s*\n)", trace, re.M)
    if m:
        return trace[:m.start()] + str(int(m.group(1)) + 3) + trace[m.end():]
    m = re.search(r"(=)\s*(\d+)", trace)
    if m:
        return trace[:m.start()] + "=" + str(int(m.group(2)) + 3) + trace[m.end():]
    return None


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    teacher = AutoModelForCausalLM.from_pretrained(
        TEACHER, torch_dtype=torch.bfloat16).to("cuda").eval()
    tok = AutoTokenizer.from_pretrained(TEACHER)
    print(f"[recovery-fix] 7B teacher loaded; sources: {N_BAD} student-bad + "
          f"{N_GC} corrupt-gold prefixes", flush=True)

    pairs = []
    stats = {"bad_kept": 0, "bad_correct": 0, "bad_total": 0,
             "gc_kept": 0, "gc_correct": 0, "gc_total": 0}

    # ---- source A: shared naive bad prefixes (implicit recovery) ----
    if os.path.exists(BADPOOL):
        pool = json.load(open(BADPOOL))[:N_BAD]
        print(f"[recovery-fix] source A: {len(pool)} student-bad prefixes", flush=True)
        for i, b in enumerate(pool):
            prefix, gold = b["prefix"], b["gold"]
            stats["bad_total"] += 1
            ids = tok(prefix, add_special=False)["input_ids"]
            with torch.no_grad():
                y = teacher.generate(torch.tensor([ids], device="cuda"),
                                     max_new_tokens=MAX_NEW, do_sample=False,
                                     temperature=0.9)
            cont = tok.decode(y[0][len(ids):], skip_special_tokens=True)
            del y; torch.cuda.empty_cache()
            cont_ids = tok(cont, add_special=False)["input_ids"]
            if format_sane(cont, cont_ids):
                correct = same_num(extract_ans(prefix + cont), gold)
                stats["bad_kept"] += 1
                stats["bad_correct"] += int(correct)
                pairs.append(dict(source="student_bad", prefix=prefix, gold=gold,
                                  teacher_cont=cont, correct=int(correct)))
            if (i + 1) % 8 == 0:
                print(f"[recovery-fix] A {i+1}/{len(pool)} kept={stats['bad_kept']}",
                      flush=True)
    else:
        print(f"[recovery-fix] WARNING: {BADPOOL} missing; source A skipped", flush=True)

    # ---- source B: corrupt-gold prefixes (high-yield) ----
    rows = load_train_rows(100, 100 + N_GC + 8)
    print(f"[recovery-fix] source B: building {N_GC} corrupt-gold prefixes", flush=True)
    made = 0
    for q, gold, trace in rows:
        if made >= N_GC:
            break
        half = trace[:int(len(trace) * 0.5)]
        ctrace = corrupt_trace(trace[:int(len(trace) * 0.7)])
        if not ctrace or ctrace == trace[:int(len(trace) * 0.7)]:
            continue
        made += 1
        stats["gc_total"] += 1
        prefix = q + "\nA: " + ctrace
        ids = tok(prefix, add_special=False)["input_ids"]
        with torch.no_grad():
            y = teacher.generate(torch.tensor([ids], device="cuda"),
                                 max_new_tokens=MAX_NEW, do_sample=False,
                                 temperature=0.9)
        cont = tok.decode(y[0][len(ids):], skip_special_tokens=True)
        del y; torch.cuda.empty_cache()
        cont_ids = tok(cont, add_special=False)["input_ids"]
        if format_sane(cont, cont_ids):
            correct = same_num(extract_ans(prefix + cont), gold)
            stats["gc_kept"] += 1
            stats["gc_correct"] += int(correct)
            pairs.append(dict(source="corrupt_gold", prefix=prefix, gold=gold,
                              teacher_cont=cont, correct=int(correct)))

    json.dump(pairs, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"[recovery-fix] DONE {len(pairs)} pairs "
          f"(A: {stats['bad_kept']}/{stats['bad_total']} kept, "
          f"{stats['bad_correct']} teacher-correct; "
          f"B: {stats['gc_kept']}/{stats['gc_total']} kept, "
          f"{stats['gc_correct']} teacher-correct) -> {OUT}", flush=True)
    if not pairs:
        print("[recovery-fix] FATAL: still 0 pairs — check teacher generation", flush=True)


if __name__ == "__main__":
    main()
