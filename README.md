# Magic_Hook by DYL — 会“精打细算”的 Claude Code 模型路由器
# Magic_Hook by DYL — the model router that thinks before it spends

---

> A dynamic, cost-aware model router for Claude Code.
> 一个会“精打细算”的成本型模型路由器。

> It runs every prompt on a cheap local model by default, watches the reply
> quality in real time, and silently escalates to a strong cloud model only
> when the local one looks like it's going to disappoint you — then drops you
> back down the moment the conversation returns to normal.
> 默认用便宜的本地模型跑每个提问，实时盯着回答质量，只有当本地模型“快撑不住”
> 时才悄悄升级到云端强模型；等对话回到正轨，它立刻把你降回本地。

---

# 中文简介 — Why you need this

## 一个每个重度 AI 用户都会遇到的痛点
## The pain every heavy AI user hits

一天几百次地跟 AI 对话，几乎每个工具都会把**每一次对话**都交给最贵的模型。
Every AI workflow means talking to a model **hundreds of times a day** — and
almost every tool bills **every single turn** to the most expensive model.

可事实是：你的 `「几点了？」「帮我改这段` 和你辛苦憋出来的
`「把整个 API 重构一下`，花的钱**完全一样**。
Yet the cost of a *"what time is it?"* and a *"refactor the entire API"*
comes out **exactly the same**.

- 强模型慢。几十次快速提问里，那点延迟一点点吃掉你的节奏。
- The strong cloud model is slow — those delays pile up and ruin your flow.
- 本地模型快、便宜，但“笨”，一遇到难题就卡壳，还没地方逃生。
- The local model is fast and cheap but dumb — it stumbles on anything hard, with nowhere to escape.

市面上常见做法，要么手动切模型（每个回合都要你操心），要么像
`cc-switch` / `Switchyard` 这类只能“切换”、却不会“决定何时切换”。
The usual workarounds force you to either switch manually every turn, or rely
on tools like `cc-switch` / `Switchyard` that can *switch* models but never
*decide when*.

> **Magic_Hook 帮你把“决定”这件事做了。** 你只管说话，它负责判断：默认走
> 便宜本地模型，模型快不行的时**悄悄升级**，聊回正轨后**自动降回**本地。
> **Magic_Hook removes the decision.** You keep talking. The router decides:
> local by default, upgraded on demand, dropped automatically.

---

# 英文简介 — How it works

## The problem
## 痛点

Local models are fast and cheap but they stumble on anything non-trivial, and
the usual workarounds can *switch* models but never *decide when*.
本地模型快、便宜，但“笨”，而常见做法只能“切换”、不会“判断时机”。

## The loop
## 升降级闭环

```
        ┌───────────────────────────────────────────────┐
        │              你继续照常说话                     │
        │              You keep talking                 │
        └───────────────┬───────────────────────────────┘
                        │
        ┌──────────────────────────────────────────────┐
        │   UserPromptSubmit 钩子：判断本次走哪条路      │
        │   hook: decide the route (from last turn score)│
        └───────────────┬───────────────────────────────┘
                        │
        ┌──────────────────────────────────────────────┐
        │         本轮回答打分（异步，不耽误你）          │
        │         score the reply (async, zero delay)   │
        │   工具错误 · 严重失败 · 含糊措辞 · 负反馈      │
        │   tool errors · severe failures · vague wording│
        └───────────────┬───────────────────────────────┘
                        │
        ┌──────────────────────────────────────────────┐
        │   分数 < 7  → 便宜本地模型（快、省钱）          │
        │   score < 7   →  cheap LOCAL model            │
        │   分数 ≥ 7  →  悄悄升级云端强模型              │
        │   score ≥ 7   →  escalate to CLOUD model      │
        │   在云端待够 3 轮 → 冷却后自动降回本地         │
        │   stayed on 3 turns →  cool down & drop down  │
        └────────────────────────────────────────────────┘
```

## What you see
## 实际体验

| 你说… You say… | 发生什么 What happens |
|----------|--------------|
| *"hi"* | 本地模型秒回。钱省了，速度也够了。 | Local model answers instantly. No cost, no latency. |
| *"重写这段，报错一直回来，我没法确认…"* | 本地模型栽在工具错误上。分数升高，**下一轮**悄悄切到云端。 | Local model trips over tool errors. Score climbs. Next turn routes to cloud. |
| *"还是不对啊！"* | 负反馈加码，分数升。留在云端。 | More negative feedback. Score climbs. Stays on cloud. |
| （云端连答 3 轮都好用） | 冷却结束，**自动拉回本地**，分数清零。 | After cooldown, it **drops you back** to local. Score resets. |

本地 → 升级 → 冷却 → 降回。这就是完整闭环。
Local → upgrade → cool down → return. That's the full loop.

---

# 针对 dgx spark + switchyard 设计，也可用于任何相似场景

## Built for `dgx-spark` + `switchyard`, but not bound to it

Magic_Hook 最初是为 **`dgx-spark` + `switchyard`**（本地模型路由）这套组合
量身打造的 —— 它正好解决了 switchyard “能路由、却不判断”的问题。
Magic_Hook was originally tuned for the **`dgx-spark` + `switchyard`** combo —
where switchyard can *route* but never *decides when*.

但它**不绑定任何特定模型或网关**。
It doesn't lock you into a specific model or gateway.

只要你有一个“便宜本地模型”和一个“云端强模型”，并通过 `cc-switch` 把它们注册
成带名字的 provider，这套逻辑就能套用。
As long as you register a cheap local model and a strong cloud model as named
providers through `cc-switch`, the same loop applies.

---

# 技术版原理 — How it works (technical)

Magic_Hook 构建在 Claude Code 的 **hook 机制**之上 —— 两个钩子，一个状态文件。
Magic_Hook is built on **Claude Code's hook system** — two hooks, one state file.

| 钩子 | 触发时机 | 脚本 | 职责 |
|------|------|------|------|
| `UserPromptSubmit` | 模型回答前 | `magic_submit.py` | 读上一轮分数；达阈值就切到云端模型。 |
| `Stop` | 本轮结束后 | `magic_score.py` | 异步重打分并更新状态。完全不影响你的等待。 |

评分信号打分表：

| Signal | 触发 Example | 分值 |
|--------|--------------|:----:|
| `tool_error` | `Traceback`、`Error:`、`Permission denied` | 5 |
| `severe_error` | `I cannot`、`无法`、`做不到` | 5 |
| `low_confidence` | `maybe`、`不确定`、`也许` | 2 |
| `user_negative_feedback` | `wrong`、`还是不行`、`错了` | 4 |

状态机：

```
state {
  score, last_route, upgrade_next_turn,
  cloud_cooldown_turns, consecutive_local_failures
}

on each turn:
    score   = base_score + signals(...)
    score  -= natural_decay_per_turn          # 每轮都衰减
    if route == cloud:
        score = 0                             # 云端成功就清零
    if score >= upgrade_threshold:
        set upgrade_next_turn = True          # 触发升级
    on upgrade:
        switch_provider(cloud)
        cloud_cooldown_turns = 3              # 在云端待几轮
    on cooldown:
        cloud_cooldown_turns -= 1
    if cooldown == 0 and not is_local:
        switch_provider(local)                # 降回本地
```

旁边还配了一个 **HTTP 层动态代理**（`magic_proxy.py`），思路完全一致：
本地为主模型，`context_length` 超限时自动升级，主模型挂了自动试备用。
A companion **HTTP proxy** (`magic_proxy.py`) applies the same idea at the
request layer: local primary, auto-escalate on `context_length`, auto-fallback
if the primary dies.

完整流程、评分表、状态机见 [docs/architecture.md](docs/architecture.md)。
Full flow, scoring table, and state machine live in
[docs/architecture.md](docs/architecture.md).

---

## 🚀 安装 Install

### 1. 把钩子复制到你的 Claude Code 配置

Copy the hook scripts into your Claude Code config:

```bash
cp hooks/magic_score.py     ~/.claude/hooks/
cp hooks/magic_submit.py    ~/.claude/hooks/
cp hooks/magic_router.py    ~/.claude/hooks/
cp hooks/magic_state.py     ~/.claude/hooks/

# 可选：每请求的 HTTP 代理 (optional per-request HTTP proxy)
cp hooks/magic_proxy.py         ~/.claude/hooks/
cp hooks/magic_proxy_cli.py     ~/.claude/hooks/
cp config/magic_proxy_config.json  ~/.claude/
```

### 2. 注册钩子 Register the hooks

把下面内容加进你的**用户级** `~/.claude/settings.json`（顶层，跟
`"tools"`、`"env"` 同级）：

Add this to your **user-level** `~/.claude/settings.json` (top level, alongside
`"tools"`, `"env"` …):

```jsonc
{
  "hooks": {
    "UserPromptSubmit": [
      { "command": "python ~/.claude/hooks/magic_submit.py",
        "statusMessage": "动态路由：评估本地/云端模型…",
        "timeout": 30, "type": "command" }
    ],
    "Stop": [
      { "command": "python ~/.claude/hooks/magic_score.py",
        "statusMessage": "异步评分中…",
        "async": true, "timeout": 30, "type": "command" }
    ]
  }
}
```

### 3. 给你的 provider 命名 Name your providers

`magic_score.py` 读的是 **provider 名字**（不是 key）。
`magic_score.py` reads **provider names** (not keys).

在 `cc-switch` 里按你想用的名字注册，然后写进 `magic_router_config.json`：
Register them in your `cc-switch` UI, then set them in
`magic_router_config.json`:

```jsonc
{
  "local":  { "primary": "switchyard-smart-spark" },
  "cloud":  { "primary": "OpenRouter",
              "backup": "Aliyun-backup" }
}
```

### 4. 配置代理（可选） Configure the proxy (optional)

复制示例，填你自己的值。 Copy the example, fill in your own values:

```bash
cp .env.example .env        # 真实密钥/URL 放这里（别提交）
cp .env.example .env        # your real keys/URLs live here (do not commit)
# 然后编辑 .env
# then edit .env
```

> 🔒 **你的密钥永远留在本机。** 脚本从 `cc-switch` 读 provider 名字、从你自己的
> `.env` 读密钥。不会上传任何东西。
> 🔒 **Your secrets never leave your machine.** The scripts read provider names
> from `cc-switch` and keys from your own `.env`. Nothing is uploaded.

## ✅ Quick check

```bash
python ~/.claude/hooks/magic_status.py
```

`Config / State / Audit: True` 就说明装好了。用几句
*"I can't verify this, it might be wrong, error: Permission denied"*
把分推到 7 分，然后观察 `last_route` 变成 `cloud`。
`Config / State / Audit: True` means it's wired up. Trigger a real escalation
loop with a few turns of *"I can't verify this, it might be wrong"* until the
score hits 7 — then watch `last_route` flip to `cloud`.

## ⚙️ 调参 Tuning

所有开关都在 [magic_router_config.json](config/magic_router_config.json)：
All knobs live in [magic_router_config.json](config/magic_router_config.json):

| 设置项 | 作用 |
|---------|------|
| `scoring.upgrade_threshold` | 触发升级所需分数（默认 **7**） |
| `scoring.natural_decay_per_turn` | 每轮扣除的分数 |
| `scoring.max_consecutive_local_failures` | 连续失败强制升级次数（默认 **2**） |
| `scoring.cooldown_turns_after_cloud` | 降回本地前在云端待几轮（默认 **3**） |
| `signals.*` | 各信号权重 — 调大可让模型更早升级 |
| `*_patterns` | 触发各信号的关键词 — 按你的风格改 |

## 🗂️ Layout

```
magic-hook-github/
├── README.md            # you are here
├── LICENSE              # MIT
├── .env.example         # your secrets live here (do not commit)
├── .gitignore
├── hooks/               # the 7 hook scripts
├── config/              # placeholder config templates
└── docs/architecture.md # full flow + scoring + state machine
```

---

# 适合谁 Who this is for

- 🧑‍💻 **重度 AI 用户** —— 想省钱、想快，又不想在质量上妥协。
- 🏢 **团队 & 机构** —— 给固定预算跑批量任务，需要一个“不会爆表”的成本控制层。
- 🔧 **折腾本地模型的人** —— 手上有 dgx-spark / vLLM / Switchyard 之类本地网关。

- 🧑‍💻 **Heavy AI users** — save cost, keep speed, don't compromise on quality.
- 🏢 **Teams & orgs** — batch jobs on a fixed budget, need a cost ceiling that never blows up.
- 🔧 **Local model tinkerers** — running dgx-spark / vLLM / Switchyard or similar gateways.

## 一句话总结

> 本地模型负责“快”，云端模型负责“强”，**Magic_Hook 负责在两者之间做那个不用你操心的决定**。
> The local model is *fast*, the cloud model is *strong* — **Magic_Hook makes the
> decision you don't have to.**

---

## 🤝 Credits

Built by the DYL team. Inspired by the gap in `cc-switch` and `Switchyard`:
**they can switch models, but they don't decide *when*.** Magic_Hook adds the
decision, silently, based on reply quality.

## 📜 License

MIT — see [LICENSE](LICENSE).
