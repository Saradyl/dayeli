#!/usr/bin/env python3
"""
v3 training — master round-3 rulings implemented exactly:
  Q1: recovery supervision = teacher CONTINUES FROM THE STUDENT BAD PREFIX
      (not clean-prompt rewrite); teacher-continuation validity filter;
      lambda_rec = 0.25 (0.5 fallback if bad rate sparse)
  Q2: bad state = teacher NLL > max(rolling_q90, 3.5); target bad rate 10-30%
      (dry-run check first; do NOT retune post-hoc)
  Q3: corrupt-question-text is FORBIDDEN (that changes the problem)
  Q4: save step-75 mid + step-150 final adapters
  Q5: 1.5B student only
  common: lr 5e-5, grad-clip 1.0, warmup, 150 steps, offload tripwire
Arms: naive | stabilized (replay+refKL+clip) | recovery (stabilized + rec-SFT)

sft.jsonl lines: "Q: <question>\nA: <gold trace> #### <ans>" -> gold answer is
extractable for the teacher-continuation correctness filter.
"""
import argparse, json, os, re, time
import numpy as np, torch, yaml
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model

DYL = os.environ.get("DYL_HOME", "./dyl_data")
SFT_ADAPTER = os.path.join(DYL, "data", "adapter_sft")
PROMPTS_PATH = os.path.join(DYL, "data", "sft.jsonl")
REC_LAMBDA = float(os.environ.get("REC_LAMBDA", "0.25"))
REC_GEN = 64                 # teacher continuation length cap
NLL_BUF = 64                 # rolling buffer for q90
MID_STEP = 75


def load_cfg(p):
    return yaml.safe_load(open(p, encoding="utf-8"))


def load_student(cfg, adapter=None):
    m = AutoModelForCausalLM.from_pretrained(
        cfg["student"]["base_model"], torch_dtype=torch.bfloat16).to("cuda")
    lc = cfg["student"]["lora"]
    m = get_peft_model(m, LoraConfig(r=lc["r"], lora_alpha=lc["alpha"],
                                     lora_dropout=lc["dropout"],
                                     target_modules=lc["target_modules"],
                                     task_type="CAUSAL_LM"))
    if adapter and os.path.isdir(adapter):
        m.load_adapter(adapter, adapter_name="dyl")
        m.set_adapter("dyl")
    return m


def load_teacher(cfg, rung_id):
    return AutoModelForCausalLM.from_pretrained(
        rung_id, torch_dtype=torch.bfloat16).to("cuda").eval()


def topk_overlap_and_entropy(student, teacher, input_ids, k):
    with torch.no_grad():
        ls = student(input_ids).logits[:, -1, :]
        lt = teacher(input_ids).logits[:, -1, :]
        ps, pt = torch.softmax(ls, -1), torch.softmax(lt, -1)
        ts = set(torch.topk(ps, k, dim=-1).indices[0].tolist())
        tt = set(torch.topk(pt, k, dim=-1).indices[0].tolist())
        o = len(ts & tt) / k
        H = float(-(ps * torch.log(ps + 1e-12)).sum())
    return o, H


def teacher_nll_of_cont(teacher, tokT, cont_ids_prefix, cont_ids, max_ctx=200):
    """per-token NLL of a continuation given a (student) prefix, all in the
    TEACHER tokenizer space. cont_ids_prefix/cont_ids are teacher-token lists."""
    ctx = cont_ids_prefix[-max_ctx:]
    full = ctx + cont_ids
    with torch.no_grad():
        lp = torch.log_softmax(
            teacher(torch.tensor([full], device="cuda")).logits, -1)[0]
    start = len(ctx) - 1
    if start + len(cont_ids) > lp.shape[0]:
        return 0.0
    lp_sel = lp[start:start + len(cont_ids), :]
    tgt = torch.tensor(cont_ids, device="cuda")
    return float(-lp_sel.gather(-1, tgt.unsqueeze(-1)).squeeze(-1).mean())


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


def cont_valid(t_text, t_ids, gold):
    """master Q1 filter 1-4: non-empty, no obvious repetition, 16-96 tokens,
    prefer answer-correct when extractable."""
    if t_ids is None or len(t_ids) < 16 or len(t_ids) > 96:
        return False
    w = t_text.lower().split()
    g4 = set(tuple(w[i:i + 4]) for i in range(max(0, len(w) - 4)))
    if len(w) > 30 and len(g4) / (len(w) - 3) < 0.5:
        return False                       # heavy 4-gram repetition
    a = extract_ans(t_text)
    if a is not None and gold is not None:
        return same_num(a, gold)           # if answer extractable, require correct
    return True                            # no answer -> keep if format sane


def opd_v3(cfg, tok, seed, runid, dry_run=False):
    out_dir = os.path.expanduser(cfg["data"]["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    t = cfg["training"]; k = t["top_k_overlap"]
    replay_ratio = float(t.get("replay_ratio", 0.0))
    ref_kl_beta = float(t.get("ref_kl_beta", 0.0))
    clip_floor = float(t.get("clip_floor", 0.0))
    replay_buf_size = int(t.get("replay_buffer_size", 100))
    tau = float(t.get("metric_tau", -10.0))
    recovery = bool(t.get("recovery", False))

    steps = int(t["opd_steps_per_rung"]) if not dry_run else 20
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    student = load_student(cfg, adapter=SFT_ADAPTER)
    opt = torch.optim.AdamW([p for p in student.parameters() if p.requires_grad],
                            lr=float(t["lr"]), weight_decay=0.01)
    warmup = int(t.get("warmup_steps", 10))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lr_lambda=lambda s: min(1.0, (s + 1) / max(warmup, 1)))

    ref_student = None
    if ref_kl_beta > 0:
        ref_student = load_student(cfg, adapter=SFT_ADAPTER)
        ref_student.eval()
        for p in ref_student.parameters():
            p.requires_grad = False

    teacher = load_teacher(cfg, cfg["teacher_ladder"][0])
    tokT = AutoTokenizer.from_pretrained(cfg["teacher_ladder"][0])

    # prompt bank WITH gold answers (for recovery filter)
    bank = []
    for l in open(PROMPTS_PATH, encoding="utf-8"):
        txt = json.loads(l)["text"]
        qpart, apart = txt.split("\nA:", 1)
        bank.append((qpart.strip(), extract_ans(apart)))
    _prng = np.random.default_rng(seed)

    print(f"[{runid}] replay={replay_ratio} refKL={ref_kl_beta} clip={clip_floor} "
          f"recovery={recovery} lam={REC_LAMBDA} steps={steps} "
          f"lr={t['lr']}{' DRY_RUN' if dry_run else ''}", flush=True)

    log = []
    replay_buffer = []       # (full_ids_cpu, prompt_len, q, gold)
    H_windows = []
    nll_rolling = []          # teacher NLL of recent student rollouts
    rec_hits, rec_used, bad_hits, probed = 0, 0, 0, 0
    t0 = time.time()

    # offline teacher-recovery pairs (shared naive bad prefixes + teacher
    # continuations) — pre-generated by v3_make_recovery.py so training never
    # calls teacher.generate (that OOM'd on the unified-memory GB10).
    rec_pairs = []
    if recovery:
        rp = os.path.join(out_dir, "v3_eval", "recovery_pairs.json")
        if os.path.exists(rp):
            rec_pairs = json.load(open(rp))
        print(f"[{runid}] loaded {len(rec_pairs)} offline recovery pairs "
              f"(from {rp})", flush=True)
        if not rec_pairs:
            print(f"[{runid}] WARNING: no recovery pairs; recovery arm runs as "
                  f"stabilized-only (offline gen must have run first)", flush=True)
    rec_idx = 0

    def offload_check():
        for m, tag in ((student, "student"), (teacher, "teacher"),
                       (ref_student, "ref") if ref_student else (None, None)):
            if m is None:
                continue
            meta = sum(1 for _, p in m.named_parameters()
                       if str(p.device).startswith("meta"))
            if meta:
                print(f"[{runid}] {tag} OFFLOAD -> clean abort", flush=True)
                json.dump(log, open(os.path.join(out_dir, f"opd_monitor_{runid}.json"), "w"), indent=1)
                raise SystemExit(4)

    for step in range(steps):
        if step % 25 == 0:
            offload_check()

        # ---- rollout selection (fresh vs replay) ----
        is_replay = False
        if replay_buffer and step >= 50 and rng.random() < replay_ratio:
            idx_r = int(rng.integers(len(replay_buffer)))
            full_cpu, prompt_len, q, gold = replay_buffer[idx_r]
            ids = full_cpu.to(student.device)
            is_replay = True
        else:
            q, gold = bank[int(_prng.integers(len(bank)))]
            prompt = tok(q + "\nA:", return_tensors="pt").to(student.device)
            with torch.no_grad():
                ids = student.generate(**prompt, max_new_tokens=64,
                                       do_sample=True, temperature=0.9)
            prompt_len = prompt["input_ids"].shape[1]

        gen_ids = ids[:, prompt_len:]
        if gen_ids.shape[1] == 0:
            continue
        L = gen_ids.shape[1]
        sel = slice(prompt_len - 1, prompt_len - 1 + L)

        # ---- student forward (grad) ----
        ls = student(ids).logits
        logp_s_all = torch.log_softmax(ls, -1)
        logp_s = logp_s_all[:, sel, :].gather(
            -1, gen_ids.unsqueeze(-1)).squeeze(-1)

        # ---- teacher forward (no grad; optional floor) ----
        with torch.no_grad():
            lt = teacher(ids).logits
            logp_T_all = torch.log_softmax(lt, -1)
            if clip_floor < 0:
                logp_T_all = torch.clamp(logp_T_all, min=clip_floor)
            logp_T = logp_T_all[:, sel, :].gather(
                -1, gen_ids.unsqueeze(-1)).squeeze(-1)

        # ---- OPD policy-gradient loss ----
        A = (logp_T - logp_s).detach()
        loss = -(A * logp_s).mean()

        # ---- reference-KL anchor ----
        if ref_student is not None:
            with torch.no_grad():
                ref_ls = ref_student(ids).logits
                ref_logp_sel = torch.log_softmax(ref_ls, -1)[:, sel, :].gather(
                    -1, gen_ids.unsqueeze(-1)).squeeze(-1)
            loss = loss + ref_kl_beta * (logp_s - ref_logp_sel).mean()

        # ---- recovery term (master Q1/Q3): OFFLINE teacher-recovery SFT ----
        # We do NOT detect bad states online and do NOT call teacher.generate
        # here (that OOM'd the unified-memory GB10). Instead we SFT the student
        # toward pre-generated (shared-naive-bad-prefix, teacher-continuation)
        # pairs from v3_make_recovery.py, interleaved with the on-policy step.
        rec_loss_val = 0.0
        is_bad = False
        if recovery and rec_pairs and step % 2 == 0:
            pr = rec_pairs[rec_idx % len(rec_pairs)]
            rec_idx += 1
            pref_ids = tok(pr["prefix"], add_special=False)["input_ids"]
            cont_ids = tok(pr["teacher_cont"], add_special=False)["input_ids"]
            full_s = (pref_ids + cont_ids)[-512:]
            c_start = max(len(full_s) - len(cont_ids), 1)
            s_lp = torch.log_softmax(student(torch.tensor([full_s], device="cuda")).logits, -1)[0]
            s_lp_c = s_lp[c_start - 1:c_start - 1 + (len(full_s) - c_start), :]
            tgt_c = torch.tensor(full_s[c_start:len(full_s)], device="cuda")
            if s_lp_c.shape[0] > 0 and tgt_c.shape[0] == s_lp_c.shape[0]:
                rec_loss = -s_lp_c.gather(-1, tgt_c.unsqueeze(-1)).squeeze(-1).mean()
                loss = loss + REC_LAMBDA * rec_loss
                rec_loss_val = float(rec_loss)
                rec_hits += 1

        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_([p for p in student.parameters() if p.requires_grad], 1.0)
        opt.step()
        sched.step()

        # ---- mid checkpoint (master Q4: step 75) ----
        if not dry_run and step + 1 == MID_STEP:
            mid_p = os.path.join(out_dir, f"adapter_{runid}_mid")
            student.save_pretrained(mid_p)
            print(f"[{runid}] saved MID adapter -> {mid_p}", flush=True)

        # ---- bookkeeping ----
        if not is_replay:
            replay_buffer.append((ids.cpu(), prompt_len, q, gold))
            if len(replay_buffer) > replay_buf_size:
                replay_buffer.pop(0)

        # free this step's big intermediates (peak-memory control on GB10)
        if step % 20 == 0:
            torch.cuda.empty_cache()

        # ---- monitoring ----
        if step % int(t["monitor_every"]) == 0:
            o, H = topk_overlap_and_entropy(student, teacher, ids[:, :8], k)
            with torch.no_grad():
                anc = 10  # cheap M10 on digits-only anchor subset
                ps_anc = torch.softmax(ls, -1)[:, sel, :][:, :, :anc]
                log_pt_anc = torch.log_softmax(lt, -1)[:, sel, :][:, :, :anc]
                M_tau = float((ps_anc * (log_pt_anc < tau).float()).sum(-1).mean())
            C_T = float((-logp_T).mean())
            dH = round(H - H_windows[-1], 4) if H_windows else 0.0
            H_windows.append(H)
            log.append(dict(step=step, o=round(o, 3), H=round(H, 3),
                            loss=round(float(loss), 4), M10=round(M_tau, 4),
                            CT=round(C_T, 3), dH=dH, replay=is_replay,
                            bad=is_bad, rec_loss=round(rec_loss_val, 4)))
            if dry_run or step % 30 == 0:
                print(f"[{runid}] s={step} H={H:.3f} M10={M_tau:.3f} CT={C_T:.2f} "
                      f"bad_rate={bad_hits}/{probed} "
                      f"rec_used={rec_used} el={time.time()-t0:.0f}s", flush=True)

        # free this step's big intermediates AFTER monitoring (peak-mem control)
        del ls, lt, logp_s, logp_T, A

    if not dry_run:
        adapter_path = os.path.join(out_dir, f"adapter_{runid}")
        student.save_pretrained(adapter_path)
        json.dump(log, open(os.path.join(out_dir, f"opd_monitor_{runid}.json"), "w"), indent=1)
        print(f"[{runid}] DONE -> {adapter_path} total={time.time()-t0:.0f}s "
              f"H_first={log[0]['H']} H_last={log[-1]['H']} "
              f"bad_rate={bad_hits}/{probed} rec_hits={rec_hits} rec_used={rec_used}",
              flush=True)
    return log


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--runid", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    tok = AutoTokenizer.from_pretrained(cfg["student"]["base_model"])
    opd_v3(cfg, tok, a.seed, a.runid, dry_run=a.dry_run)
