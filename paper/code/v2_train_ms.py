#!/usr/bin/env python3
"""
OPD v2 — stability-focused rerun per distillation-guru Phase-1 guidance.

Changes vs v1 (multiseed_train_ms.py):
  - lr 5e-5 (was 1e-4); 300 steps (was 120); grad-clip norm=1.0
  - optional replay buffer (replay_ratio, past rollouts resampled)
  - optional reference-KL to frozen SFT checkpoint (ref_kl_beta > 0)
  - optional teacher logprob floor (clip_floor < 0 clamps log p_T from below)
  - new monitor metrics (every monitor_every):
      M10 : student prob mass on tokens where log p_T < metric_tau
      CT  : teacher surprisal  E_{v~p_S}[-log p_T(v)]
      dH  : entropy slope  H_t - H_{t-10windows}
      d1  : distinct-1 over last ~64 generated tokens
  - teacher loaded ONCE (same single rung for all Phase-1 arms)
  - offload tripwire: any param on meta device -> clean SystemExit(4)

Isolation: writes only ~/multiseed_v2/data; reads model cache + shared SFT
adapter + prompt bank (all read-only).
"""
import argparse, json, os, time
import numpy as np, torch, yaml
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model

DYL = os.environ.get("DYL_HOME", "./dyl_data")
SFT_ADAPTER = os.path.join(DYL, "data", "adapter_sft")
PROMPTS = os.path.join(DYL, "data", "sft.jsonl")


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


def compute_new_metrics(ls_full, lt_full, sel, logp_T, anc_s, anc_t, tau=-10.0):
    """M_tau on the shared anchor-token projection (vocab sizes differ between
    Qwen2.5-1.5B and -7B; anchors are the 1:1-mapped tokens, same scheme as the
    v1 probing term). C_T = mean(-log p_T) over the STUDENT-SAMPLED tokens, an
    unbiased Monte-Carlo estimate of E_{v~pS}[-log p_T(v)] and vocab-agnostic."""
    with torch.no_grad():
        ps_anc = torch.softmax(ls_full, -1)[:, sel, :][:, :, anc_s]
        log_pt_anc = torch.log_softmax(lt_full, -1)[:, sel, :][:, :, anc_t]
        M_tau = float((ps_anc * (log_pt_anc < tau).float()).sum(dim=-1).mean())
        C_T = float((-logp_T).mean())
    return M_tau, C_T


def opd_v2(cfg, tok, seed, runid):
    out_dir = os.path.expanduser(cfg["data"]["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    t = cfg["training"]; k = t["top_k_overlap"]
    replay_ratio = float(t.get("replay_ratio", 0.0))
    ref_kl_beta = float(t.get("ref_kl_beta", 0.0))
    clip_floor = float(t.get("clip_floor", 0.0))
    replay_buf_size = int(t.get("replay_buffer_size", 100))
    tau = float(t.get("metric_tau", -10.0))

    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    student = load_student(cfg, adapter=SFT_ADAPTER)
    opt = torch.optim.AdamW([p for p in student.parameters() if p.requires_grad],
                            lr=float(t["lr"]), weight_decay=0.01)
    # linear warmup over the first `warmup_steps` updates (guru: 3-5%), then hold
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
    # shared anchor-token projection for M_tau (vocab sizes differ: 151936 vs 152064)
    anchor_strs = [str(i) for i in range(10)] + ["the","a","is","of","and","to","in","it","for","on",
                   "with","as","by","at","from","or","an","be","this","which","not","are","was","step",
                   "answer","let","we","compute","thus","therefore","equal","sum","total","value"]
    tokT = AutoTokenizer.from_pretrained(cfg["teacher_ladder"][0])
    s_ids, t_ids = [], []
    for s in anchor_strs:
        i1 = tok(s, add_special=False)["input_ids"]
        i2 = tokT(s, add_special=False)["input_ids"]
        s_ids.append(i1[0] if len(i1) == 1 else None)
        t_ids.append(i2[0] if len(i2) == 1 else None)
    idx = [i for i in range(len(anchor_strs)) if s_ids[i] is not None and t_ids[i] is not None]
    anc_s = torch.tensor([s_ids[i] for i in idx], device="cuda")
    anc_t = torch.tensor([t_ids[i] for i in idx], device="cuda")
    print(f"[{runid}] replay={replay_ratio} refKL={ref_kl_beta} clip={clip_floor} "
          f"lr={t['lr']} steps={t['opd_steps_per_rung']} tau={tau} "
          f"k={k}", flush=True)

    log = []
    replay_buffer = []          # (full_ids_cpu, prompt_len)
    H_windows = []              # H at monitor points (for dH slope)
    recent_tokens = []          # generated tokens (for distinct-1)
    t0 = time.time()
    prompts = [json.loads(l)["text"].split("\nA:")[0].strip()
               for l in open(PROMPTS, encoding="utf-8")]
    _prng = np.random.default_rng(seed)

    def offload_check():
        for m, tag in ((student, "student"), (teacher, "teacher"),
                       (ref_student, "ref") if ref_student else (None, None)):
            if m is None:
                continue
            meta = sum(1 for _, p in m.named_parameters()
                       if str(p.device).startswith("meta"))
            if meta:
                print(f"[{runid}] {tag} OFFLOAD meta={meta} -> clean abort", flush=True)
                json.dump(log, open(os.path.join(out_dir,
                                                  f"opd_monitor_{runid}.json"), "w"), indent=1)
                raise SystemExit(4)

    for step in range(int(t["opd_steps_per_rung"])):
        if step % 25 == 0:
            offload_check()

        # ---- rollout selection (fresh vs replay) ----
        is_replay = False
        if replay_buffer and step >= 50 and rng.random() < replay_ratio:
            idx = int(rng.integers(len(replay_buffer)))
            full_cpu, prompt_len = replay_buffer[idx]
            ids = full_cpu.to(student.device)
            is_replay = True
        else:
            p = prompts[int(_prng.integers(len(prompts)))]
            prompt = tok(p + "\nA:", return_tensors="pt").to(student.device)
            with torch.no_grad():
                ids = student.generate(**prompt, max_new_tokens=64,
                                       do_sample=True, temperature=0.9)
            prompt_len = prompt["input_ids"].shape[1]

        gen_ids = ids[:, prompt_len:]
        if gen_ids.shape[1] == 0:
            continue
        L = gen_ids.shape[1]
        # rows predicting the L generated tokens: [prompt_len-1, prompt_len+L-1)
        sel = slice(prompt_len - 1, prompt_len - 1 + L)

        # ---- student forward (grad through policy) ----
        ls = student(ids).logits
        logp_s_all = torch.log_softmax(ls, -1)
        logp_s = logp_s_all[:, sel, :].gather(
            -1, gen_ids.unsqueeze(-1)).squeeze(-1)

        # ---- teacher forward (no grad; optional logprob floor) ----
        with torch.no_grad():
            lt = teacher(ids).logits
            logp_T_all = torch.log_softmax(lt, -1)
            if clip_floor < 0:
                logp_T_all = torch.clamp(logp_T_all, min=clip_floor)
            logp_T = logp_T_all[:, sel, :].gather(
                -1, gen_ids.unsqueeze(-1)).squeeze(-1)

        # ---- OPD policy-gradient loss (same surrogate as v1) ----
        A = (logp_T - logp_s).detach()
        loss = -(A * logp_s).mean()

        # ---- reference-KL anchor (vs frozen SFT checkpoint) ----
        if ref_student is not None:
            with torch.no_grad():
                ref_ls = ref_student(ids).logits
                ref_logp_sel = torch.log_softmax(ref_ls, -1)[:, sel, :].gather(
                    -1, gen_ids.unsqueeze(-1)).squeeze(-1)
            loss = loss + ref_kl_beta * (logp_s - ref_logp_sel).mean()

        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_([p for p in student.parameters() if p.requires_grad], 1.0)
        opt.step()
        sched.step()

        # ---- bookkeeping ----
        if not is_replay:
            replay_buffer.append((ids.cpu(), prompt_len))
            if len(replay_buffer) > replay_buf_size:
                replay_buffer.pop(0)
            recent_tokens.extend(gen_ids[0].tolist())
            if len(recent_tokens) > 1280:
                recent_tokens = recent_tokens[-1280:]

        # ---- monitoring (same cadence as v1) ----
        if step % int(t["monitor_every"]) == 0:
            o, H = topk_overlap_and_entropy(student, teacher, ids[:, :8], k)
            M_tau, C_T = compute_new_metrics(ls.detach(), lt.detach(), sel,
                                             logp_T.detach(), anc_s, anc_t, tau=tau)
            dH = round(H - H_windows[-1], 4) if H_windows else 0.0
            d1 = round(len(set(recent_tokens[-64:])) / max(len(recent_tokens[-64:]), 1), 3) \
                if recent_tokens else 0.0
            H_windows.append(H)
            log.append(dict(step=step, o=round(o, 3), H=round(H, 3),
                            loss=round(float(loss), 4),
                            M10=round(M_tau, 4), CT=round(C_T, 3),
                            dH=dH, d1=d1, replay=is_replay))
            print(f"[{runid}] s={step} H={H:.3f} o={o:.2f} M10={M_tau:.3f} "
                  f"CT={C_T:.2f} dH={dH:+.3f} d1={d1:.2f} loss={float(loss):.2f} "
                  f"{'[R]' if is_replay else ''} el={time.time()-t0:.0f}s", flush=True)

    out = os.path.join(out_dir, f"opd_monitor_{runid}.json")
    json.dump(log, open(out, "w"), indent=1)
    first, last = log[0], log[-1]
    print(f"[{runid}] DONE {len(log)} rows -> {out} total={time.time()-t0:.0f}s "
          f"H_first={first['H']} H_last={last['H']} M10_last={last['M10']} "
          f"CT_last={last['CT']}", flush=True)
    return log


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--runid", required=True)
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    tok = AutoTokenizer.from_pretrained(cfg["student"]["base_model"])
    opd_v2(cfg, tok, a.seed, a.runid)
