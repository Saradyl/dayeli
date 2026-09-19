#!/usr/bin/env python3
"""
Multi-seed OPD rerun for paper Sec 7.3 (lifts single-seed 'preliminary' to solid).

ISOLATION (do not touch ~/dyl or ~/dyl-venv):
  - new dir ~/multiseed, new output files opd_monitor_{arm}_s{seed}.json
  - reads ONLY: model HF cache (~/.cache/huggingface) and the shared SFT start
    $DYL_HOME/data/adapter_sft  (read-only, never written)
  - run with the EXISTING dyl-venv interpreter (read-only use; no new pip installs)

Protocol (identical to Sec 7.3):
  student Qwen2.5-1.5B-Instruct (LoRA r=32) on top of shared SFT warm-start
  arms:
    direct : reverse-KL policy-grad to 7B teacher, 120 steps
    ladder : 3B -> 7B, 120 steps total (60/rung), shared budget
    probe  : direct + finite-difference geometry term (probe_every=10, lambda=0.5)
  monitored every 10 steps: o (top-k overlap vs current teacher), H (policy entropy)
  'final rev-KL' column in the paper == last logged surrogate loss
  SEEDING: _rng (prompt order) AND torch/cuda/np all seeded per run -> distinct,
           reproducible trajectories from the SAME SFT start.
"""
import argparse, json, math, os, time
import numpy as np, torch, yaml
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

DYL = os.environ.get("DYL_HOME", "./dyl_data")
SFT_ADAPTER = os.path.join(DYL, "data", "adapter_sft")      # shared SFT warm-start (read-only)
PROMPTS = os.path.join(DYL, "data", "sft.jsonl")            # prompt bank (read-only)


def load_cfg(p):
    return yaml.safe_load(open(p, encoding="utf-8"))


def bnb4bit():
    return BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                              bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)


def load_student(cfg, adapter=None):
    if cfg["student"]["quant"] == "4bit":
        m = AutoModelForCausalLM.from_pretrained(cfg["student"]["base_model"],
                                                 quantization_config=bnb4bit(), device_map="auto",
                                                 torch_dtype=torch.bfloat16)
        m = prepare_model_for_kbit_training(m)
    else:
        # GB10 unified mem: force .to("cuda"); device_map mis-reads and disk-offloads
        m = AutoModelForCausalLM.from_pretrained(cfg["student"]["base_model"],
                                                 torch_dtype=torch.bfloat16).to("cuda")
    lc = cfg["student"]["lora"]
    m = get_peft_model(m, LoraConfig(r=lc["r"], lora_alpha=lc["alpha"], lora_dropout=lc["dropout"],
                                     target_modules=lc["target_modules"], task_type="CAUSAL_LM"))
    if adapter and os.path.isdir(adapter):
        m.load_adapter(adapter, adapter_name="dyl"); m.set_adapter("dyl")
    return m


def load_teacher(cfg, rung_id):
    if cfg["student"]["quant"] == "4bit":
        return AutoModelForCausalLM.from_pretrained(rung_id, quantization_config=bnb4bit(),
                                                    device_map="auto", torch_dtype=torch.bfloat16).eval()
    return AutoModelForCausalLM.from_pretrained(rung_id, torch_dtype=torch.bfloat16).to("cuda").eval()


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


def opd(cfg, tok, seed, runid):
    t = cfg["training"]; k = t["top_k_overlap"]
    probing = bool(t.get("probing", False))
    probe_lambda = float(t.get("probe_lambda", 0.5))
    probe_every = int(t.get("probe_every", 20))

    # ---- per-run seeding: prompt order + torch/cuda/np -> distinct reproducible traj ----
    rng = np.random.default_rng(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    student = load_student(cfg, adapter=SFT_ADAPTER)
    opt = torch.optim.AdamW([p for p in student.parameters() if p.requires_grad], lr=t["lr"])
    log = []; teacher_H = None; t0 = time.time()
    prompts = [json.loads(l)["text"].split("\nA:")[0].strip()
               for l in open(PROMPTS, encoding="utf-8")]
    _prng = np.random.default_rng(seed)  # prompt selection (independent, reproducible)

    anchor_strs = [str(i) for i in range(10)] + ["the","a","is","of","and","to","in","it","for","on",
                   "with","as","by","at","from","or","an","be","this","which","not","are","was","step",
                   "answer","let","we","compute","thus","therefore","equal","sum","total","value"]
    s_ids = []
    for s in anchor_strs:
        ids = tok(s, add_special=False)["input_ids"]
        s_ids.append(ids[0] if len(ids) == 1 else None)

    for rung in cfg["teacher_ladder"]:
        teacher = load_teacher(cfg, rung)
        tokT = AutoTokenizer.from_pretrained(rung)
        t_ids = []
        for s in anchor_strs:
            ids = tokT(s, add_special=False)["input_ids"]
            t_ids.append(ids[0] if len(ids) == 1 else None)
        idx = [i for i in range(len(anchor_strs)) if s_ids[i] is not None and t_ids[i] is not None]
        anc_s = torch.tensor([s_ids[i] for i in idx], device=student.device)
        anc_t = torch.tensor([t_ids[i] for i in idx], device=student.device)
        low_streak = 0
        for step in range(t["opd_steps_per_rung"]):
            p = prompts[int(_prng.integers(len(prompts)))]
            prompt = tok(p + "\nA:", return_tensors="pt").to(student.device)
            with torch.no_grad():
                y = student.generate(**prompt, max_new_tokens=64, do_sample=True,
                                     temperature=0.9)
            ids = y[:, prompt["input_ids"].shape[1]:]
            if ids.shape[1] == 0:
                continue
            full = y
            ls = student(full).logits
            with torch.no_grad():
                lt = teacher(full).logits
            logp_s = torch.log_softmax(ls, -1)[:, :-1, :].gather(-1, ids[:, :-1].unsqueeze(-1)).squeeze(-1) if ids.shape[1] > 1 else None
            if logp_s is None:
                continue
            logp_T = torch.log_softmax(lt, -1)[:, :-1, :].gather(-1, ids[:, :-1].unsqueeze(-1)).squeeze(-1).detach()
            A = (logp_T - logp_s).detach()
            loss = -(A * logp_s).mean()
            if probing and step % probe_every == 0:
                pp = p + "\n(Hint: solve step by step.)"
                ex = tok(p, return_tensors="pt").to(student.device)
                epp = tok(pp, return_tensors="pt").to(student.device)
                phi_s_x = torch.softmax(student(**ex).logits[:, -1, :], -1)[:, anc_s]
                phi_s_pp = torch.softmax(student(**epp).logits[:, -1, :], -1)[:, anc_s]
                with torch.no_grad():
                    phi_t_x = torch.softmax(teacher(**ex).logits[:, -1, :], -1)[:, anc_t]
                    phi_t_pp = torch.softmax(teacher(**epp).logits[:, -1, :], -1)[:, anc_t]
                dS = phi_s_pp - phi_s_x
                dT = (phi_t_pp - phi_t_x).detach()
                dS = dS / (dS.norm() + 1e-8); dT = dT / (dT.norm() + 1e-8)
                loss = loss + probe_lambda * (1.0 - (dS * dT).sum())
            opt.zero_grad(); loss.backward(); opt.step()
            if step % t["monitor_every"] == 0:
                o, H = topk_overlap_and_entropy(student, teacher, full[:, :8], k)
                log.append(dict(rung=rung, step=step, o=round(o, 3), H=round(H, 3),
                                loss=round(float(loss), 4)))
                print(f"[opd {runid}] rung={rung.split('/')[-1]} step={step} o={o:.3f} H={H:.3f} "
                      f"loss={loss:.4f} el={time.time()-t0:.0f}s", flush=True)
                if o < t["o_star"]:
                    low_streak += 1
                    if low_streak >= t["patience"]:
                        print(f"[opd {runid}] overlap below o_star {low_streak}x -> stop rung", flush=True)
                        break
                else:
                    low_streak = 0
        del teacher; torch.cuda.empty_cache()

    out = os.path.join(cfg["data"]["out_dir"], f"opd_monitor_{runid}.json")
    json.dump(log, open(out, "w"), indent=1)
    # do NOT save the per-run adapter (shared SFT start already exists; adapter is 147M each)
    print(f"[opd {runid}] DONE {len(log)} monitor rows -> {out} total={time.time()-t0:.0f}s", flush=True)
    return log


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--runid", required=True)   # e.g. direct_s1337
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    tok = AutoTokenizer.from_pretrained(cfg["student"]["base_model"])
    opd(cfg, tok, a.seed, a.runid)
