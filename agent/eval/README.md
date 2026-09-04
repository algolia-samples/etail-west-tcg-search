# Golden-set evaluation

Regression tests for the chat agent's *behaviour*. Every case in `golden.json` is a
bug that actually reached a customer, so a failure here means something we already
fixed has come back.

```bash
cd agent
.venv/bin/python3 eval/run_eval.py retailclub-ai-festival-2026        # full set, ~3 min
.venv/bin/python3 eval/run_eval.py <event_id> --runs 1                # quick smoke, ~1 min
.venv/bin/python3 eval/run_eval.py <event_id> --case two-group-split  # one case
.venv/bin/python3 eval/run_eval.py <event_id> --json /tmp/eval.json   # keep per-run detail
```

Exits non-zero if any case fails.

## When to run it

Before `agent.py update --all`. The prompt and tool config are the only things these
cases exercise, and a prompt edit can regress behaviour that no unit test covers —
nothing in the frontend suite touches the agent at all.

Run it against one event first (the canary), not the whole fleet: it costs a real
completion per run and hits a live published agent.

## Why pass rates, not assertions

The agent is stochastic. The same query legitimately produces 3 or 4 searches, or one
group or two. Cases therefore run N times and pass on a rate — `fuzzy-no-exact-match`
needs 4 of 5. A case failing once is noise; a case dropping below its rate is a
regression. Raise `runs` before believing a single red result.

## Cache is always bypassed

Requests send `cache=false`. Agent Studio caches completions keyed on the message text,
and a cache HIT replays a stored response *without* `messageMetadata` — so evaluating
against the cache measures the cache, not the agent.

## Adding a case

Add to `golden.json` with a `regression` line naming the bug it guards, so a future
failure is legible without archaeology. Supported assertions live in `check()` in
`run_eval.py`: `must_display`, `may_decline`, `max_search_calls`, `max_text_parts`,
`min_text_parts`, `groups_between`, `max_total_cards`, `min_cards_if_displayed`,
`forbidden_tools`, `no_markdown_in_fields`, `no_error`.
