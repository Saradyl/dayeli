# 🪄 Magic_Hook

### Touchless, user-driven model routing for Claude Code
### 零触碰、由你做主的 Claude Code 模型路由器

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/built%20for-Claude%20Code-orange.svg)](https://www.anthropic.com/claude-code)
[![hooks](https://img.shields.io/badge/hooks-UserPromptSubmit%20%7C%20Stop-purple.svg)]()

> 🆕 **v2 headline: *touchless* model switching.** No menus, no config screens.
> Just slip a secret code (`dylup` / `dylgo`) into the tail of your prompt and
> the model changes underneath you — *while you keep typing.*
> 🆕 **v2 主打：零触碰换模型。** 不用点菜单、不用开设置——把暗号 `dylup` / `dylgo`
> 往你的提问末尾一塞，模型就在你脚下悄悄换了，**你连键盘都不用离开**。

> **Stop paying frontier-model prices for "what time is it?".** Magic_Hook runs
> every turn on your cheap local model by default, watches reply quality in real
> time, and silently escalates to a strong cloud model only when the local one
> starts to struggle — then drops you back the moment things are normal again.
> **别再拿旗舰模型的价格去问"现在几点了"。** Magic_Hook 默认用便宜的本地模型跑
> 每一轮，实时盯着回答质量；只有本地模型快扛不住了，才悄悄升级到云端强模型，
> 一旦回归正常，又立刻把你降回本地省钱。

---

## 🧨 The problem / 痛点

You talk to an AI **hundreds of times a day**. Most turns are trivial — *reformat
this*, *what's in this file*, *try again*. Yet the default setup bills **every
single one** to the most expensive model, and makes you wait for the slowest model
on your fastest questions.

你一天要跟 AI 对话**几百次**，其中绝大多数都是小事——*改个格式*、*看看这文件*、
*再试一遍*。可默认配置却把**每一句**都丢给最贵的模型，还让你用最慢的模型去等
那些最简单的问题。

| | Strong cloud / 云端强模型 | Local / 本地模型 |
|---|---|---|
| **Hard-task quality / 难题质量** | ✅ great 强 | ❌ struggles 吃力 |
| **Speed / 速度** | 🐢 slow 慢 | ⚡ instant 秒回 |
| **Cost / 成本** | 💸💸💸 贵 | 🆓 nearly free 几乎免费 |

You want **both**: local for the easy 80%, cloud for the hard 20%. Existing tools
(`cc-switch`, `Switchyard`, …) can *switch* models — but never decide ***when***.
**Magic_Hook makes the decision for you.**

你**两个都想要**：80% 的简单活儿给本地，20% 的硬骨头给云端。现有工具
（`cc-switch`、`Switchyard`……）只会"切换"，却从不判断"**何时该切**"。
**Magic_Hook 替你做这个决定。**

---

## ✨ Features / 特性

- 🪄 **Touchless secret-code control.** Slip `dylup` / `dylgo` into the end of your prompt (within the last 30 characters will suffice) to escalate or drop back *instantly* — no menus, no config, no breaking flow.
- 🪄 **零触碰暗号控制。** 把 `dylup` / `dylgo` 塞进提问 (最后三十个字符以内即可)，立刻升级或降回——没有  菜单、没有设置、不打断心流。

- 🔍 **Peek anytime, no model switch.** Toss `dylwh` into any prompt to pop a little yellow sticky note that tells you exactly where you are right now — which model is driving, whether Magic_Hook is active. Fully read-only: it never touches routing, it just *shows* you.
- 🔍 **随时一眼看状态。** 任何提问里塞一句 `dylwh`，就会弹出一张黄色便签，告诉你现在是谁在跑、Magic_Hook 激活与否——纯只读，绝不改路由，只把状态摆到你眼前。

- ☁️ **Two-tier cloud.** Upgrade is a staircase, not a cliff: `local → Cloud-1 (primary) → Cloud-2 (backup)`. `dylup` walks up one step at a time so a weak local model and a strong cloud can both live side by side.
- ☁️ **双云阶梯。** 升级不是纵身一跃，而是阶梯：`本地 → 云-1(主) → 云-2(备)`。`dylup` 一步步往上走，让弱本地模型和强云端模型各司其职。

- 🤖 **Auto-pilot.** Scores every reply on objective signals (tool errors, "I cannot"
  failures, hedging, your negative feedback) and escalates when the local model is
  clearly out of its depth.
- 🤖 **自动驾驶。** 用客观信号给每轮回答打分（工具报错、"我做不到"、含糊其辞、你的
  负面反馈），本地模型明显不行时就升级。

- 💰 **Local-first.** Cheap local model handles the easy majority; you only pay for
  cloud when it actually matters.
- 💰 **本地优先。** 便宜的本地模型扛下大多数简单活儿，只在真正需要时才为云端付费。

- 🛡️ **Never stuck.** Primary cloud → backup cloud → auto-fallback to local. A dead
  endpoint never leaves you hanging.
- 🛡️ **永不卡死。** 主云挂了试备用云，备用也不行自动退回本地——任何端点失效都不会
  把你晾在半空。

- 🔄 **Auto cooldown.** After a cloud stint it returns to local automatically.
- 🔄 **自动冷却。** 云端跑完几轮，自动降回本地，无需手动收拾。

- 🧠 **Context-overflow aware.** If the local model runs out of context, it escalates
  automatically.
- 🧠 **上下文溢出感知。** 本地模型上下文不够用时，自动升级。

- 👥 **Multi-session safe.** Pauses auto-switching while several sessions run; your
  passphrases still work.
- 👥 **多会话安全。** 多个会话同时跑时暂停自动切换，但你的暗号照样有效。

- 🔌 **Provider-agnostic.** Any local model + any cloud model, wired through
  [`cc-switch`](https://github.com/farion1231/cc-switch).
- 🔌 **不挑模型。** 任意本地模型 + 任意云端模型，经
  [`cc-switch`](https://github.com/farion1231/cc-switch) 接入即可。

---

## 🎮 Touchless control — a secret code in your prompt
## 🎮 零触碰控制——藏在提问里的暗号

*(the killer feature / 杀手锏)*

Every router makes *you* stop, open a menu, and pick a model. Magic_Hook flips that:
the smartest signal for "should I switch?" isn't an algorithm — **it's you.** So we
made switching **touchless**. You never leave the keyboard. You just bury a secret
code at the end of whatever you were already typing:

所有路由器都要你*停下来*、打开菜单、挑模型。Magic_Hook 反其道而行：判断"该不该切"
最聪明的信号不是算法——**是你本人**。所以我们把切换做成了**零触碰**：你不用离开键盘，
只需把暗号埋在你本来就要打的那句话末尾：

| You type / 你输入 | What happens / 发生什么 |
|---|---|
| `…any message **dylup**` | ⬆️ Escalate to the cloud model — *this very turn* ⬆️ 立刻升级云端强模型 |
| `…any message **dylgo**` | ⬇️ Drop back to the local model — *this very turn* ⬇️ 立刻降回本地模型 |
| `…any message **dylwh**` | 🔍 Pop a yellow sticky note showing your current model & status — no switch at all 🔍 弹一张黄色便签,显示当前模型与状态——完全不切模型 |

**Why it feels like magic / 为什么它像魔法：**

- 🪄 **Touchless / 零触碰** — no menu, no shortcut. The code rides along in the prompt
  you were writing anyway. 没有菜单、没有快捷键，暗号就搭在你正在写的那句话上。
- 🧿 **Secret / 隐蔽** — only the last **30 characters** are scanned, case-insensitive,
  so ordinary words never trigger it. 只扫描末尾 **30 个字符**、不分大小写，普通措辞
  绝不会误触发。
- ⚡ **Instant / 即时** — the model under your *current* reply changes immediately.
  你*当前*这条回答底下的模型，立刻就换。
- 🛡️ **Always on / 始终在线** — works even while auto-routing is paused. 即使自动路由
  被暂停（多会话），暗号依然生效。

The auto-pilot handles the routine; the secret code is your override whenever *you*
know better.

自动驾驶负责日常，暗号则是"你比算法更懂"时的终极否决权。

---

## 🔁 The loop / 升降级闭环

```
            ┌─────────────────────────────────────────┐
            │      you keep talking … 你继续说话 …      │
            └──────────────────┬──────────────────────┘
                               │
            ┌──────────────────▼──────────────────────┐
            │  UserPromptSubmit hook                  │
            │  · passphrase? 暗号?  dylup / dylgo      │
            │  · else: last turn's score 上一轮得分     │
            └──────────────────┬──────────────────────┘
                               │
            ┌──────────────────▼──────────────────────┐
            │  Stop hook — score the reply (async)    │
            │  tool errors · "I cannot" · hedging     │
            │  报错 · 认怂 · 含糊 · 你的负反馈           │
            └──────────────────┬──────────────────────┘
                               │
        ┌──────────────────────▼──────────────────────┐
        │  score < threshold → stay LOCAL (cheap/省)  │
        │  score ≥ threshold → escalate to CLOUD      │
        │  cloud for N turns → cool down → LOCAL      │
        └─────────────────────────────────────────────┘
```

**local → escalate → cool down → return.** Fully automatic — or override it any time
with a passphrase.

**本地 → 升级 → 冷却 → 降回。** 全自动——或者，随时用一个暗号夺回控制权。

---

## 🏗️ How it works / 技术原理

Magic_Hook is a handful of Python scripts on Claude Code's **hook system**, plus a
tiny **HTTP proxy** that does the per-request routing:

Magic_Hook 是几个挂在 Claude Code **钩子机制**上的 Python 脚本，外加一个做逐请求
路由的**迷你 HTTP 代理**：

| Piece / 组件 | Hook / 角色 | Job / 职责 |
|---|---|---|
| `magic_submit.py` | `UserPromptSubmit` | Reads score & passphrases, decides route 读分数与暗号、定路由 |
| `magic_score.py` | `Stop` (async) | Scores the finished reply 给回答打分 |
| `magic_proxy.py` | HTTP proxy `:15666` | Hot-reads routing decision per request 逐请求热读路由决策 |
| `magic_router.py` | library | Activation gate + switching helpers 激活门 + 切换辅助 |
| `magic_state.py` | library | Per-session state (multi-session safe) 按会话存状态 |
| `magic_status.py` | CLI | `python magic_status.py` — live status 查看实时状态 |

**Activation gate.** Magic_Hook only activates when you select its *dedicated* provider
in `cc-switch` (the one whose `base_url` points at the proxy on `:15666`). Pick a plain
local provider and it stays resident-local; pick any cloud provider and Magic_Hook stays
out of the way entirely.

**激活门。** 只有当你在 `cc-switch` 里选中那个*专用* provider（`base_url` 指向 `:15666`
代理的那个），Magic_Hook 才激活。选普通本地 provider 就常驻本地，选任何云模型它就完全
不插手。

**Routing, not switching.** Escalating doesn't yank your `cc-switch` provider — it writes
a one-line decision file (`magic_target.json`) that the proxy reads on every request. Your
provider never changes mid-session, so nothing breaks.

**是路由，不是切换。** 升级不会去动你的 `cc-switch` provider——它只写一个一行的决策文件
（`magic_target.json`），代理每个请求热读它。你的 provider 全程不变，所以什么都不崩。

**Resilience.** Upgrade endpoint errors → try backup cloud → still failing → fall back to
local. You are never left without a model.

**韧性。** 升级端点报错 → 试备用云 → 还不行 → 退回本地。你永远不至于无模型可用。

Full flow, scoring table and state machine: [`docs/architecture.md`](docs/architecture.md).
完整流程、评分表与状态机见 [`docs/architecture.md`](docs/architecture.md)。

---

## 🚀 Quick start / 快速上手

**0. Prereqs / 前置** — [Claude Code](https://www.anthropic.com/claude-code),
[`cc-switch`](https://github.com/farion1231/cc-switch), Python 3.10+,
`aiohttp` (`pip install aiohttp`).

**1. Copy the hooks / 复制钩子** into your Claude Code hooks folder：
```bash
cp hooks/magic_submit.py hooks/magic_score.py hooks/magic_router.py \
   hooks/magic_state.py hooks/magic_session.py hooks/magic_status.py \
   ~/.claude/hooks/

# optional per-request proxy / 可选的逐请求代理
cp hooks/magic_proxy.py hooks/magic_proxy_cli.py ~/.claude/hooks/
```

**2. Register the hooks / 注册钩子** in your user-level `~/.claude/settings.json`：
```jsonc
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command",
          "command": "python ~/.claude/hooks/magic_submit.py",
          "timeout": 30 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command",
          "command": "python ~/.claude/hooks/magic_score.py",
          "async": true, "timeout": 30 } ] }
    ]
  }
}
```

**3. Create a dedicated provider / 建一个专用 provider** in `cc-switch`：
- name it anything (e.g. `my-local-model-dynamic`)，
- set its `base_url` to `http://127.0.0.1:15666/v1`，
- then set `MAGIC_DEDICATED_PROVIDER` in `magic_router.py` to that exact name.
- 名字随意（比如 `my-local-model-dynamic`），`base_url` 填 `http://127.0.0.1:15666/v1`，
  再把 `magic_router.py` 里的 `MAGIC_DEDICATED_PROVIDER` 改成这个确切的名字。

**4. Configure models / 配置模型.** Copy the examples, fill in your own endpoints/keys：
```bash
cp config/magic_proxy_config.example.json  hooks/magic_proxy_config.json
cp config/magic_router_config.example.json hooks/magic_router_config.json
```

**5. Start the proxy / 启动代理** and go：
```bash
python ~/.claude/hooks/magic_proxy.py &
```

**6. Verify / 验证**：
```bash
python ~/.claude/hooks/magic_status.py
```
Then select your dedicated provider and type a message ending in `dylup`.
然后选中你的专用 provider，发一条以 `dylup` 结尾的消息。

> 🔒 **Your keys never leave your machine.** Real config files are git-ignored; only
> `.example.json` templates are committed.
> 🔒 **你的密钥永远不出本机。** 真实配置被 git 忽略，只有 `.example.json` 模板入库。

---

## ⚙️ Tuning / 调参

All knobs live in `magic_router_config.json` / `magic_proxy_config.json`：

| Setting / 设置 | Meaning / 含义 |
|---|---|
| `scoring.upgrade_threshold` | Score that triggers auto-escalation 触发升级的分数（默认 **7**） |
| `scoring.cooldown_turns_after_cloud` | Cloud turns before dropping back 降回前在云端待几轮（默认 **3**） |
| `scoring.max_consecutive_local_failures` | Forced escalation after N failed local turns 连续失败几次强制升级（默认 **2**） |
| `signals.*` | Weight of each scoring signal 各信号权重 |
| `upgrade.primary` / `upgrade.backup` | Your cloud models; backup tried if primary fails 主云/备用云 |

---

## 🗂️ Layout / 目录

```
magic-hook/
├── README.md
├── LICENSE                        # MIT
├── hooks/
│   ├── magic_submit.py            # UserPromptSubmit hook
│   ├── magic_score.py             # Stop hook (async scoring)
│   ├── magic_proxy.py             # HTTP routing proxy (:15666)
│   ├── magic_router.py            # activation gate + helpers
│   ├── magic_state.py             # per-session state
│   ├── magic_session.py           # session helpers
│   ├── magic_status.py            # diagnostic CLI
│   └── def.json                   # hook definitions
├── config/
│   ├── magic_proxy_config.example.json
│   └── magic_router_config.example.json
└── docs/architecture.md
```

---

## 🙋 Who is this for? / 适合谁

- 🧑‍💻 **Heavy Claude Code users** who want speed + savings without giving up quality.
- 🧑‍💻 **重度 Claude Code 用户**——既要快、又要省，还不肯牺牲质量。
- 🏢 **Teams on a budget** running lots of agentic turns and need a cost ceiling.
- 🏢 **预算有限的团队**——大量 agent 调用，需要一个不会爆表的成本上限。
- 🔧 **Local-model tinkerers** with DGX Spark / vLLM / Ollama / LM Studio who want an
  escape hatch to the cloud when it counts.
- 🔧 **本地模型玩家**——手上跑着 DGX Spark / vLLM / Ollama / LM Studio，想要一个
  关键时刻能逃去云端的舱门。

> The local model is *fast*, the cloud model is *strong* — **Magic_Hook decides which
> one you need, so you don't have to.**
> 本地模型负责*快*，云端模型负责*强*——**Magic_Hook 负责判断此刻该用谁，让你什么都不
> 用管。**

---

## 🗺️ Roadmap / 路线图

- [ ] Retry-loop detection 重试循环检测（最强的"该升级"信号）
- [ ] Repeated-resubmission detection 重复提交检测（"模型没听懂"）
- [ ] Web UI for live route/score status 实时路由/分数可视化面板
- [ ] More scoring signals 更多评分信号（忠实度、工具遥测）

## 🤝 Contributing / 参与贡献

Issues and PRs welcome. If Magic_Hook saves you money, **drop a ⭐** — it helps more
people find it.

欢迎 Issue 和 PR。如果 Magic_Hook 帮你省了钱，**点个 ⭐**——让更多人看到它。

## 📜 License / 许可

MIT — see [LICENSE](LICENSE).
