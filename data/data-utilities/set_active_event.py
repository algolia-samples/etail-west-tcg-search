#!/usr/bin/env python3
"""
Manage the active event in the tcg_events index.

Usage:
    python set_active_event.py list
    python set_active_event.py set <event_id>

Examples:
    python set_active_event.py list
    python set_active_event.py set shoptalk-2026
"""

import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
from algoliasearch.search.client import SearchClientSync

load_dotenv(Path(__file__).parent.parent / ".env")

ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY")
EVENTS_INDEX = os.getenv("ALGOLIA_EVENTS_INDEX", "tcg_events")


def fetch_all_events(client):
    events = []

    def collect(response):
        for hit in response.hits:
            events.append(hit.to_dict())

    client.browse_objects(index_name=EVENTS_INDEX, aggregator=collect)
    return events


def cmd_list(client):
    events = fetch_all_events(client)
    if not events:
        print(f"No events found in {EVENTS_INDEX}")
        return
    for event in sorted(events, key=lambda e: e.get("objectID", "")):
        active = " (active)" if event.get("current") else ""
        name = event.get("name", "")
        booth = event.get("booth", "")
        location = f", Booth {booth}" if booth else ""
        print(f"  {event['objectID']}{active}  —  {name}{location}")


def cmd_set(client, event_id):
    events = fetch_all_events(client)
    all_ids = [e["objectID"] for e in events]

    if not all_ids:
        print(f"ERROR: No events found in {EVENTS_INDEX}")
        sys.exit(1)

    if event_id not in all_ids:
        print(f"ERROR: Event '{event_id}' not found in {EVENTS_INDEX}")
        print(f"Existing events: {all_ids}")
        sys.exit(1)

    updates = [
        {"objectID": eid, "current": eid == event_id}
        for eid in all_ids
    ]

    print(f"Updating {len(updates)} event(s)...")
    client.partial_update_objects(index_name=EVENTS_INDEX, objects=updates)
    print(f"✓ '{event_id}' is now the active event.")


def main():
    parser = argparse.ArgumentParser(description="Manage the active TCG event")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all events and which is active")

    set_p = sub.add_parser("set", help="Set the active event")
    set_p.add_argument("event_id", help="Event slug to activate, e.g. shoptalk-2026")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if not ALGOLIA_APP_ID or not ALGOLIA_API_KEY:
        print("ERROR: Missing Algolia credentials in .env file")
        sys.exit(1)

    client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_API_KEY)

    if args.command == "list":
        cmd_list(client)
    elif args.command == "set":
        cmd_set(client, args.event_id)


if __name__ == "__main__":
    main()
