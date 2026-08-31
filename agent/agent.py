#!/usr/bin/env python3
"""
TCG-specific wrapper around algolia-agent.

Handles the two things unique to TCG:
  1. Verify the event exists in tcg_events before creating anything
  2. Write agent_id back to the tcg_events record after creation

All agent config (index names, descriptions, prompt) lives in agent-config.json
and PROMPT.md, using {{event_id}}, {{event_name}}, and {{booth}} template vars.

Usage:
  python agent.py create <event_id> "<Event Name>" <booth> [--dry-run] [--publish]
  python agent.py publish <agent_id>
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from algolia_agent.cli import build_tool, load_config, resolve_vars
from algolia_agent.client import AgentAPIError, AlgoliaAgentClient
from algolia_agent.template import render

load_dotenv(Path(__file__).parent / ".env")

APP_ID = os.getenv("ALGOLIA_APP_ID")
API_KEY = os.getenv("ALGOLIA_API_KEY")
EVENTS_INDEX = os.getenv("ALGOLIA_EVENTS_INDEX", "tcg_events")

AGENT_DIR = Path(__file__).parent


def algolia_index_request(path, method="GET", body=None, allow_404=False):
    """Make a request to the Algolia Search REST API."""
    url = f"https://{APP_ID}.algolia.net{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("x-algolia-application-id", APP_ID)
    req.add_header("x-algolia-api-key", API_KEY)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "algolia-tcg-cli/1.0")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if allow_404 and e.code == 404:
            return None
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)


def _render_config(event_id, event_name, booth):
    """Load agent-config.json and PROMPT.md, render all template vars, return (config, instructions)."""
    config_path = AGENT_DIR / "agent-config.json"
    raw_config = load_config(str(config_path))

    instructions_path = AGENT_DIR / raw_config["instructions"]
    instructions_template = instructions_path.read_text()

    # booth_phrase lets PROMPT.md omit the parenthetical entirely for events with no
    # booth number yet; a populated booth renders the same sentence as before.
    variables = {
        "event_id": event_id,
        "event_name": event_name,
        "booth": booth,
        "booth_phrase": f" (booth {booth})" if booth else "",
    }

    # Render config JSON and instructions together so missing vars are caught in one pass
    config_json = json.dumps(raw_config)
    resolved = resolve_vars(config_json + "\n" + instructions_template, variables)

    config = json.loads(render(config_json, resolved))
    instructions = render(instructions_template, resolved)
    return config, instructions


def _tools_payload(config, tool):
    """Assemble the agent's tools array: the search tool first, followed by any
    extra tools declared in agent-config.json (e.g. algolia_display_results).

    Sending only [tool] would overwrite (delete) any additional tools already
    registered on the live agent, so extra_tools must be reproduced from config.
    """
    extra = config.get("extra_tools") or []
    return [tool, *extra]


def cmd_create(args):
    if not APP_ID or not API_KEY:
        print("ERROR: Missing ALGOLIA_APP_ID or ALGOLIA_API_KEY in agent/.env", file=sys.stderr)
        sys.exit(1)

    # Verify event exists in tcg_events
    event = algolia_index_request(
        f"/1/indexes/{EVENTS_INDEX}/{args.event_id}", allow_404=True
    )
    if event is None:
        print(f"ERROR: Event '{args.event_id}' not found in {EVENTS_INDEX}.", file=sys.stderr)
        sys.exit(1)
    booth_note = f" (booth {event.get('booth')})" if event.get("booth") else ""
    print(f"Event: {event.get('name')}{booth_note}")

    config, instructions = _render_config(args.event_id, args.event_name, args.booth)
    tool = build_tool(config)

    if args.dry_run:
        print("=== DRY RUN ===")
        print(f"\nAgent name: {config['name']}")
        print(f"Provider:   {config['provider']}")
        print(f"Model:      {config['model']}")
        print(f"\nTools payload:\n{json.dumps(_tools_payload(config, tool), indent=2)}")
        if config.get("config"):
            print(f"\nAgent config:\n{json.dumps(config['config'], indent=2)}")
        print(f"\n--- Rendered instructions ---\n{instructions}")
        return

    client = AlgoliaAgentClient()

    try:
        provider_id = client.resolve_provider_id(config["provider"])
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    payload = {
        "name": config["name"],
        "providerId": provider_id,
        "model": config["model"],
        "instructions": instructions,
        "status": "draft",
        "tools": _tools_payload(config, tool),
    }
    if config.get("config"):
        payload["config"] = config["config"]

    try:
        agent = client.create_agent(payload)
    except AgentAPIError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    agent_id = agent["id"]
    print(f"Created agent: {agent['name']}")
    print(f"Agent ID:      {agent_id}")
    print(f"Status:        {agent['status']}")

    # Write agent_id back to tcg_events record
    try:
        algolia_index_request(
            f"/1/indexes/{EVENTS_INDEX}/{args.event_id}/partial?createIfNotExists=false",
            method="POST",
            body={"agent_id": agent_id},
        )
        print(f"\nUpdated tcg_events record '{args.event_id}' with agent_id.")
    except SystemExit:
        print(
            f"\nWARNING: Failed to update tcg_events record '{args.event_id}'.\n"
            f"Agent ID: {agent_id}\n"
            f"Update manually.",
            file=sys.stderr,
        )

    if args.publish:
        _publish(client, agent_id)
    else:
        print(f"\nAgent created (draft). To publish:\n  python agent.py publish {agent_id}")


def _browse_events_with_agents():
    """Yield all tcg_events records that have an agent_id set."""
    cursor = None
    while True:
        body = {"query": "", "hitsPerPage": 100}
        if cursor:
            body["cursor"] = cursor
        result = algolia_index_request(
            f"/1/indexes/{EVENTS_INDEX}/browse", method="POST", body=body
        )
        for hit in result.get("hits", []):
            if hit.get("agent_id"):
                yield hit
        cursor = result.get("cursor")
        if not cursor:
            break


def _update_event_agent(client, event, dry_run=False, publish=False):
    """Re-render and update the agent for a single tcg_events record."""
    event_id = event["objectID"]
    event_name = event.get("name", event_id)
    booth = str(event.get("booth", ""))
    agent_id = event["agent_id"]

    config, instructions = _render_config(event_id, event_name, booth)
    tool = build_tool(config)

    from algolia_agent.cli import _diff

    current = client.get_agent(agent_id)
    provider_id = client.resolve_provider_id(config["provider"])

    new_payload = {
        "name": config["name"],
        "providerId": provider_id,
        "model": config["model"],
        "instructions": instructions,
        "status": current.get("status", "draft"),
        "tools": _tools_payload(config, tool),
    }
    if config.get("config"):
        new_payload["config"] = config["config"]

    if dry_run:
        changes = _diff(current, new_payload)
        print(f"\n--- {event_id} ({agent_id}) ---")
        if changes:
            print("\n".join(changes))
        else:
            print("  No changes.")
        return

    try:
        agent = client.update_agent(agent_id, new_payload)
    except AgentAPIError as e:
        print(f"  ERROR updating {event_id}: {e}", file=sys.stderr)
        return

    print(f"  Updated: {agent['name']} ({agent_id})")

    if publish and current.get("status") != "published":
        _publish(client, agent_id)


def cmd_update(args):
    if not APP_ID or not API_KEY:
        print("ERROR: Missing ALGOLIA_APP_ID or ALGOLIA_API_KEY in agent/.env", file=sys.stderr)
        sys.exit(1)

    client = AlgoliaAgentClient()

    if args.all:
        print("Browsing tcg_events for agents to update...")
        events = list(_browse_events_with_agents())
        if not events:
            print("No events with agent_id found.")
            return
        print(f"Found {len(events)} event(s) with agents.")
        if args.dry_run:
            print("=== DRY RUN ===")
        for event in events:
            _update_event_agent(client, event, dry_run=args.dry_run, publish=args.publish)
        if not args.dry_run:
            print(f"\nDone. {len(events)} agent(s) updated.")
    else:
        event = algolia_index_request(
            f"/1/indexes/{EVENTS_INDEX}/{args.event_id}", allow_404=True
        )
        if event is None:
            print(f"ERROR: Event '{args.event_id}' not found in {EVENTS_INDEX}.", file=sys.stderr)
            sys.exit(1)
        if not event.get("agent_id"):
            print(f"ERROR: Event '{args.event_id}' has no agent_id.", file=sys.stderr)
            sys.exit(1)
        if args.dry_run:
            print("=== DRY RUN ===")
        _update_event_agent(client, event, dry_run=args.dry_run, publish=args.publish)


def cmd_publish(args):
    client = AlgoliaAgentClient()
    _publish(client, args.agent_id)


def cmd_delete(args):
    if not APP_ID or not API_KEY:
        print("ERROR: Missing ALGOLIA_APP_ID or ALGOLIA_API_KEY in agent/.env", file=sys.stderr)
        sys.exit(1)

    event = algolia_index_request(
        f"/1/indexes/{EVENTS_INDEX}/{args.event_id}", allow_404=True
    )
    if event is None:
        print(f"ERROR: Event '{args.event_id}' not found in {EVENTS_INDEX}.", file=sys.stderr)
        sys.exit(1)

    agent_id = event.get("agent_id")
    if not agent_id:
        print(f"  No agent_id on record for '{args.event_id}' — nothing to delete.")
        return

    client = AlgoliaAgentClient()
    try:
        client.delete_agent(agent_id)
    except AgentAPIError as e:
        if e.status_code == 404:
            print(f"  Agent {agent_id} already deleted — skipping.")
        else:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"  ✓ Deleted agent: {agent_id}")


def _publish(client, agent_id):
    try:
        agent = client.publish_agent(agent_id)
    except AgentAPIError as e:
        if e.status_code == 409:
            print(f"  (already published: {agent_id})")
            return
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Published agent: {agent['name']}")
    print(f"Agent ID:        {agent['id']}")
    print(f"Status:          {agent['status']}")


def main():
    parser = argparse.ArgumentParser(description="TCG Event Agent Manager")
    sub = parser.add_subparsers(dest="command")

    create_p = sub.add_parser("create", help="Create a draft agent for a TCG event")
    create_p.add_argument("event_id", help="Event ID slug (e.g. etail-palm-springs-2026)")
    create_p.add_argument("event_name", help='Event display name (e.g. "eTail Palm Springs 2026")')
    create_p.add_argument("booth", help="Booth number (e.g. 701)")
    create_p.add_argument("--dry-run", action="store_true",
                          help="Preview without making API calls")
    create_p.add_argument("--publish", action="store_true",
                          help="Publish immediately after creation")

    update_p = sub.add_parser("update", help="Update agent(s) from current template")
    update_p.add_argument("event_id", nargs="?",
                          help="Event ID to update (omit with --all)")
    update_p.add_argument("--all", action="store_true",
                          help="Update all events that have an agent_id")
    update_p.add_argument("--dry-run", action="store_true",
                          help="Show what would change without making API calls")
    update_p.add_argument("--publish", action="store_true",
                          help="Publish after updating")

    pub_p = sub.add_parser("publish", help="Publish a draft agent")
    pub_p.add_argument("agent_id", help="Agent ID (UUID)")

    del_p = sub.add_parser("delete", help="Delete the agent for a TCG event")
    del_p.add_argument("event_id", help="Event ID slug")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "update":
        if not args.all and not args.event_id:
            update_p.error("provide an event_id or --all")
        cmd_update(args)
    elif args.command == "publish":
        cmd_publish(args)
    elif args.command == "delete":
        cmd_delete(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
