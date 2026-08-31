#!/usr/bin/env python3
"""
Scaffold a new TCG event:
1. Creates tcg_cards_{event_id} primary index + virtual replica indices in Algolia
2. Applies algolia-config.json settings to the primary (replicas inherit automatically)
3. Sets price-sort ranking on each virtual replica
4. Inserts record into tcg_events index

Usage:
    python create_event.py <event_id> <event_name> <booth> [--venue <venue>] [--landing-sections <json>]
    python create_event.py <event_id> --patch --landing-sections <json>

Examples:
    python create_event.py shoptalk-2026 "Shoptalk 2026" 314 --venue "Mandalay Bay"
    python create_event.py adobe-summit-2026 --patch --landing-sections '[{"title":"Gold Cards","filter":"is_chase_card:true AND card_type:\"Gold\""}]'
"""

import os
import sys
import json
import argparse
from pathlib import Path
from dotenv import load_dotenv
from algoliasearch.search.client import SearchClientSync

load_dotenv(Path(__file__).parent.parent / ".env")

ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY")
EVENTS_INDEX = os.getenv("ALGOLIA_EVENTS_INDEX", "tcg_events")
CARD_CONFIG_FILE = Path(__file__).parent.parent / "algolia-config.json"


def main():
    parser = argparse.ArgumentParser(description="Scaffold a new TCG event")
    parser.add_argument("event_id", help="Event slug, e.g. shoptalk-2026")
    parser.add_argument("event_name", nargs="?", help='Human-readable name, e.g. "Shoptalk 2026"')
    parser.add_argument("booth", nargs="?", help="Booth number, e.g. 314")
    parser.add_argument("--venue", default="", help="Venue name (optional)")
    parser.add_argument("--landing-sections", dest="landing_sections", default=None,
                        help="JSON array of {title, filter} objects for landing page carousels")
    parser.add_argument("--patch", action="store_true",
                        help="Patch an existing event record (skips index creation)")
    args = parser.parse_args()

    # Compared against None, not falsiness: "" is a valid booth for events that have
    # no booth number assigned yet.
    if not args.patch and (args.event_name is None or args.booth is None):
        parser.error("event_name and booth are required unless --patch is used")

    if not ALGOLIA_APP_ID or not ALGOLIA_API_KEY:
        print("ERROR: Missing Algolia credentials in .env file")
        sys.exit(1)

    if not CARD_CONFIG_FILE.exists():
        print(f"ERROR: Config file not found: {CARD_CONFIG_FILE}")
        sys.exit(1)

    landing_sections = None
    if args.landing_sections:
        try:
            landing_sections = json.loads(args.landing_sections)
        except json.JSONDecodeError as e:
            print(f"ERROR: --landing-sections is not valid JSON: {e}")
            sys.exit(1)
        if not isinstance(landing_sections, list) or not all(
            isinstance(s, dict) and isinstance(s.get("title"), str) and isinstance(s.get("filter"), str)
            for s in landing_sections
        ):
            print("ERROR: --landing-sections must be a JSON array of {title, filter} objects with string values")
            sys.exit(1)

    client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_API_KEY)

    # Patch mode: update only the landing_sections field on an existing event record
    if args.patch:
        if landing_sections is None:
            print("ERROR: --patch requires --landing-sections")
            sys.exit(1)
        print(f"Patching event record '{args.event_id}' in {EVENTS_INDEX}...")
        client.partial_update_objects(
            index_name=EVENTS_INDEX,
            objects=[{"objectID": args.event_id, "landing_sections": landing_sections}],
        )
        print(f"  ✓ landing_sections updated ({len(landing_sections)} section(s))")
        return

    primary = f"tcg_cards_{args.event_id}"
    price_asc = f"{primary}_price_asc"
    price_desc = f"{primary}_price_desc"

    with open(CARD_CONFIG_FILE) as f:
        card_config = json.load(f)

    # Create primary index with virtual replica references
    # Virtual replicas use the `replicas` key with virtual("name") modifier syntax
    print(f"Creating and configuring {primary}...")
    primary_config = {**card_config, "replicas": [f"virtual({price_asc})", f"virtual({price_desc})"]}
    client.set_settings(index_name=primary, index_settings=primary_config)
    print(f"  ✓ {primary}")

    # Set price-sort ranking on each virtual replica (all other settings inherited from primary)
    for replica_name, sort_direction in [(price_asc, "asc"), (price_desc, "desc")]:
        print(f"Configuring {replica_name}...")
        client.set_settings(
            index_name=replica_name,
            index_settings={"customRanking": [f"{sort_direction}(estimated_value)"]},
        )
        print(f"  ✓ {replica_name}")

    # Insert event record into tcg_events
    event_record = {
        "objectID": args.event_id,
        "event_id": args.event_id,
        "name": args.event_name,
        "booth": args.booth,
        "venue": args.venue,
        "current": False,
    }
    if landing_sections is not None:
        event_record["landing_sections"] = landing_sections

    print(f"\nAdding event record to {EVENTS_INDEX}...")
    client.save_objects(index_name=EVENTS_INDEX, objects=[event_record])
    print(f"  ✓ Event '{args.event_id}' created (current=false)")
    print(f"\nRun: python set_active_event.py {args.event_id}  — to make this the active event.")


if __name__ == "__main__":
    main()
