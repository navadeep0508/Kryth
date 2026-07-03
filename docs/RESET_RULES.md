# KRYTH Reset Rules

## Mandatory Engineering Rules

1. **No feature additions during reset** — zero new capabilities until all phases complete.
2. **No new subsystems** — no new directories, no new abstractions, no new frameworks.
3. **No speculative architecture** — every line must serve a current, verified need.
4. **Every change must reduce complexity** — lines removed > lines added in every commit.
5. **Benchmark after every major phase** — verify nothing broke.

## Priorities

Reliability > Features
Simplicity > Cleverness
Deletion > Abstraction
Measurement > Opinion

## What Stays

Only code that directly supports these tasks:

- read project
- create file
- fix syntax
- run project
- debug errors

Everything else is deleted or quarantined.

## Deletion Policy

- No archiving.
- No commenting out.
- No "keep for later".
- If it's not on the hot path, it goes.

## Hot Path Only

The only permitted import chains after reset:

```
user_input → agent_loop → llm → tool → result → done
```

No detours through:
- task classification
- self-evaluation
- experience engines
- reflection pipelines
- orchestration routers
- multi-agent schedulers
- production telemetry
- memory graphs
- mission planners
- browser automation
- persistent shells
