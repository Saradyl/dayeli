# Architecture — Magic_Hook

Magic_Hook is a small, dependency-light **model router** that sits on top of
Claude Code's hook system. It runs every turn on a cheap local model by
default and escalates to a strong cloud model only when the last reply looked
bad — then drops you back after a short cooldown.

```
                         You (Claude Code session)
                                    │
        ┌───────────────────────────┴────────────────────────────┐
        │  UserPromptSubmit hook   →   magic_submit.py            │
        │    (before the model answers)                          │
        │    • read state score / upgrade_next_turn / cooldown    │
        │    • score >= threshold  →  switch_provider(cloud)      │
        │    • cooldown > 0        →  stay on cloud               │
        │    • else & not local     →  switch_provider(local)     │
        └───────────────────────────┬────────────────────────────┘
                                     │ model answers
                                     ▼
        ┌───────────────────────────┴────────────────────────────┐
        │  Stop hook             →   magic_score.py (async)       │
        │    (after the turn completes)                          │
        │    • parse payload, extract last assistant + user text  │
        │    • score the reply (see table below)                  │
        │    • apply natural decay / cloud reset                  │
        │    • write state + audit, set upgrade_next_turn         │
        └────────────────────────────────────────────────────────┘
                                     │
                                     ▼
                          state/magic_router_state.json   (shared file)
                                     │
                                     ▼
                          ~/.cc-switch (cc-switch UI, read-only)
                        reads name→UUID, only changes currentProviderClaude
```

## Two hooks, one state file, one router module

| File | Role |
|------|------|
| `magic_score.py` | Stop-hook scorer. Runs the rule engine, decays score, sets `upgrade_next_turn`. |
| `magic_submit.py` | UserPromptSubmit executor. Reads the score and actually switches the cc-switch provider. |
| `magic_router.py` | `cc-switch` bridge. `resolve_provider_uuid()` and `set_claude_provider()` — **read-only** on the database, only the `currentProviderClaude` field is changed. |
| `magic_state.py` | File-locked JSON state accessor (lock degrades to a no-op on Windows). |
| `magic_status.py` | Human-friendly status reader. |

## Scoring rule engine

Each turn the reply is scored against **signals**. The weights are tuned in
`magic_router_config.json` (`signals.*`).

| Signal | Trigger example | Points |
|--------|-----------------|:------:|
| `tool_error` | `Traceback`, `Error:`, `Permission denied`, `Failed to` | 5 |
| `severe_error` | `I cannot`, `I am unable to`, `无法`, `做不到` | 5 |
| `low_confidence` | `maybe`, `I may be wrong`, `不确定`, `也许` | 2 |
| `user_negative_feedback` | `wrong`, `still broken`, `不对`, `错了` | 4 |
| `chitchat` | `hi`, `thanks`, `好` | −1 (and never upgrades) |

```
raw_score        = sum(signal weights that matched)
score            = raw_score - natural_decay_per_turn   # floor at 0
if last_route == cloud:
    score = 0                                          # cloud success resets
new_score        = old_score + score
need_upgrade     = new_score >= upgrade_threshold
                 OR consecutive_local_failures >= threshold
```

## Upgrade / cooldown / downgrade state machine

```
        score >= 7 ? ──yes──► set upgrade_next_turn = True
              │
   no         ▼
              │
   ┌──────────┴───────────┐
   │ upgrade_next_turn?   │
   ├─ yes ─► set_cloud_provider()          (escalate)
   │         cloud_cooldown_turns = 3      (stay up a few turns)
   │         last_route = "cloud"
   │
   ├─ no ──► if cloud_cooldown_turns > 0   (cool down)
   │         cloud_cooldown_turns -= 1
   │
   └─ no ──► if not is_local provider ─► set_local_provider() (drop down)
              last_route = "local"
```

### Why the cooldown exists

Without a cooldown, the instant the cloud model answers, the next turn's
score logic (which resets on cloud success) would flip `last_route` back to
local immediately — so you'd never "stay up" long enough to actually finish a
hard turn. The cooldown gives the strong model a few turns to do its job
before the router gently returns you to the cheap one.

## The HTTP proxy (optional companion)

`magic_proxy.py` is the same idea, applied at the request layer. It listens on
`127.0.0.1:PORT` (configurable) and:

1. Reads the model from each request.
2. If the model matches `activation.model_patterns`, it checks the routing
   decision written by the hooks and forwards to the **base** (local) provider.
3. On `context_length` / `maximum context` / `too many tokens` errors, it
   auto-escalates to the **upgrade** (cloud) provider and persists that
   decision.
4. If the primary provider fails, it tries the backup.

Config lives in `magic_proxy_config.json`. **All secrets come from the
environment / your own `.env`** — see `.env.example`.

## Why it's safe

- The hooks read provider **names** from `cc-switch`; they never hardcode or
  transmit keys.
- Only the `currentProviderClaude` field of `cc-switch`'s `settings.json` is
  ever modified — the database itself is read-only.
- The proxy reads keys from `.env`, never from committed files.
- `magic_state.py` uses a file lock on POSIX and degrades to no lock on
  Windows (so it works on Windows without crashing).
