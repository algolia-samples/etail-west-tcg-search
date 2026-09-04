#!/usr/bin/env python3
"""
Run the golden-set evaluation against a live event agent.

Every case in golden.json is a bug that actually reached a customer. The agent is
stochastic, so each case runs N times and passes on a rate rather than a single
result — a case that passes 4 of 5 runs at pass_rate 0.8 is green.

Usage:
    .venv/bin/python3 eval/run_eval.py <event_id> [--runs N] [--case NAME] [--json OUT]

    <event_id>   e.g. retailclub-ai-festival-2026
    --runs N     override every case's run count (use 1 for a quick smoke test)
    --case NAME  run a single case
    --json OUT   also write the full per-run detail to OUT

Credentials come from agent/.env: ALGOLIA_APP_ID plus ALGOLIA_SEARCH_API_KEY if
present, otherwise ALGOLIA_API_KEY.

Note this always sends cache=false. A cached completion replays a stored response
and drops messageMetadata, so evaluating against the cache would measure the cache
rather than the agent.
"""

import argparse
import json
import os
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ENV = Path(__file__).parent.parent / ".env"
GOLDEN = Path(__file__).parent / "golden.json"
load_dotenv(ENV)

APP_ID = os.getenv("ALGOLIA_APP_ID")
API_KEY = os.getenv("ALGOLIA_SEARCH_API_KEY") or os.getenv("ALGOLIA_API_KEY")
EVENTS_INDEX = os.getenv("ALGOLIA_EVENTS_INDEX", "tcg_events")

# Agent Studio sits behind Cloudflare, which returns 403 code 1010 to a request
# without browser headers. These are required, not cosmetic.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Origin": "https://algolia-tcg-search.vercel.app",
    "Referer": "https://algolia-tcg-search.vercel.app/",
}


def _get(url):
    req = urllib.request.Request(
        url,
        headers={
            "x-algolia-application-id": APP_ID,
            "x-algolia-api-key": API_KEY,
            **BROWSER_HEADERS,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _post(url, body):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "x-algolia-application-id": APP_ID,
            "x-algolia-api-key": API_KEY,
            "x-algolia-agent": "Algolia for Python (eval harness)",
            "Content-Type": "application/json",
            **BROWSER_HEADERS,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def resolve_agent_id(event_id):
    """Fetch the event record directly. event_id is the objectID, so this is an exact
    lookup — a relevance-ranked query could rank the record you asked for out of view
    and report it missing."""
    url = (
        f"https://{APP_ID}-dsn.algolia.net/1/indexes/{EVENTS_INDEX}/"
        f"{urllib.parse.quote(event_id, safe='')}"
    )
    try:
        record = _get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SystemExit(
                f"ERROR: no event {event_id!r} in {EVENTS_INDEX}"
            ) from exc
        raise
    agent_id = record.get("agent_id")
    if not agent_id:
        raise SystemExit(f"ERROR: event {event_id!r} has no agent_id")
    return agent_id


def complete(agent_id, query, run_index):
    url = (
        f"https://{APP_ID}.algolia.net/agent-studio/1/agents/{agent_id}/completions"
        f"?compatibilityMode=ai-sdk-5&stream=false&cache=false"
    )
    # A conversation id must be unique per request. Reusing one whose stored content
    # differs returns 409 Conflict, which silently reds every case's first run.
    token = uuid.uuid4().hex
    body = {
        "id": f"eval-{run_index}-{token}",
        "messages": [
            {
                "id": f"eval-msg-{run_index}-{token}",
                "role": "user",
                "parts": [{"type": "text", "text": query}],
            }
        ],
    }
    return _post(url, body)


def observe(response):
    """Reduce one completion to the facts the golden set asserts on."""
    obs = {
        "search_calls": 0,
        "tools_used": [],
        "text_parts": 0,
        "displayed": False,
        "groups": 0,
        "total_cards": 0,
        "fields": [],
        "hit_names": [],
        "error": None,
    }
    for part in response.get("parts", []) or []:
        ptype = part.get("type", "")
        if ptype == "text":
            if (part.get("text") or "").strip():
                obs["text_parts"] += 1
        elif ptype.startswith("tool-"):
            name = ptype[len("tool-"):]
            obs["tools_used"].append(name)
            if name.startswith("algolia_search_index"):
                obs["search_calls"] += 1
                for hit in ((part.get("output") or {}).get("hits") or []):
                    if hit.get("pokemon_name") not in obs["hit_names"]:
                        obs["hit_names"].append(hit.get("pokemon_name"))
            elif name == "algolia_display_results":
                obs["displayed"] = True
                payload = part.get("input") or {}
                groups = payload.get("groups") or []
                obs["groups"] = len(groups)
                obs["fields"].append(payload.get("intro") or "")
                for group in groups:
                    obs["fields"] += [group.get("title") or "", group.get("why") or ""]
                    obs["total_cards"] += len(group.get("results") or [])
    # A failed turn arrives as an error part rather than an HTTP error.
    blob = json.dumps(response)
    for marker in ("MaxStepsPerCompletionError", "\"type\": \"error\""):
        if marker in blob:
            obs["error"] = marker
            break
    return obs


MARKDOWN_TOKENS = ("**", "__", "`")


def check(expect, obs):
    """Return the list of failed assertions for one run."""
    fails = []
    if expect.get("must_display") is True and not obs["displayed"]:
        if not expect.get("may_decline"):
            fails.append("expected a display call, got none")
    if expect.get("must_display") is False and obs["displayed"]:
        fails.append("display call not expected")
    if "max_search_calls" in expect and obs["search_calls"] > expect["max_search_calls"]:
        fails.append(f"{obs['search_calls']} searches > max {expect['max_search_calls']}")
    if "max_text_parts" in expect and obs["text_parts"] > expect["max_text_parts"]:
        fails.append(f"{obs['text_parts']} text parts > max {expect['max_text_parts']}")
    if "min_text_parts" in expect and obs["text_parts"] < expect["min_text_parts"]:
        fails.append(f"{obs['text_parts']} text parts < min {expect['min_text_parts']}")
    if "groups_between" in expect and obs["displayed"]:
        low, high = expect["groups_between"]
        if not low <= obs["groups"] <= high:
            fails.append(f"{obs['groups']} groups outside {low}-{high}")
    if "max_total_cards" in expect and obs["total_cards"] > expect["max_total_cards"]:
        fails.append(f"{obs['total_cards']} cards > max {expect['max_total_cards']}")
    if "min_cards_if_displayed" in expect and obs["displayed"]:
        if obs["total_cards"] < expect["min_cards_if_displayed"]:
            fails.append(
                f"displayed only {obs['total_cards']} card(s), "
                f"min {expect['min_cards_if_displayed']} — likely an irrelevant keyword match"
            )
    for tool in expect.get("forbidden_tools", []):
        if any(t == tool for t in obs["tools_used"]):
            fails.append(f"called forbidden tool {tool}")
    if expect.get("no_markdown_in_fields"):
        for field in obs["fields"]:
            if any(tok in field for tok in MARKDOWN_TOKENS):
                fails.append(f"markdown in a display field: {field[:60]!r}")
                break
    if expect.get("no_error") and obs["error"]:
        fails.append(f"turn errored: {obs['error']}")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("event_id")
    ap.add_argument("--runs", type=int, help="override every case's run count")
    ap.add_argument("--case", help="run a single case by name")
    ap.add_argument("--json", dest="json_out", help="write full per-run detail here")
    args = ap.parse_args()

    if not APP_ID or not API_KEY:
        raise SystemExit(f"ERROR: set ALGOLIA_APP_ID and an API key in {ENV}")

    cases = json.loads(GOLDEN.read_text())["cases"]
    if args.case:
        cases = [c for c in cases if c["name"] == args.case]
        if not cases:
            raise SystemExit(f"ERROR: no case named {args.case!r}")

    agent_id = resolve_agent_id(args.event_id)
    print(f"Evaluating {args.event_id} ({agent_id})")
    print(f"{len(cases)} case(s), cache bypassed\n")

    detail, failed_cases = [], []
    for case in cases:
        runs = args.runs or case.get("runs", 3)
        passes = 0
        for i in range(runs):
            try:
                obs = observe(complete(agent_id, case["query"], i))
                fails = check(case["expect"], obs)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                obs, fails = {"error": str(exc)}, [f"request failed: {exc}"]
            if not fails:
                passes += 1
            detail.append({"case": case["name"], "run": i + 1, "obs": obs, "fails": fails})
            flag = "ok  " if not fails else "FAIL"
            summary = (
                f"searches={obs.get('search_calls')} groups={obs.get('groups')} "
                f"cards={obs.get('total_cards')} text={obs.get('text_parts')}"
            )
            print(f"  {flag} {case['name']:<26} run {i+1}/{runs}  {summary}")
            for f in fails:
                print(f"       - {f}")
        rate = passes / runs
        threshold = case.get("pass_rate", 1.0)
        verdict = "PASS" if rate >= threshold else "FAIL"
        if verdict == "FAIL":
            failed_cases.append(case["name"])
        print(f"  --> {verdict} {case['name']}: {passes}/{runs} (needs {threshold:.0%})\n")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(detail, indent=2))
        print(f"Detail written to {args.json_out}")

    if failed_cases:
        print(f"FAILED: {', '.join(failed_cases)}")
        sys.exit(1)
    print("All cases passed.")


if __name__ == "__main__":
    main()
