#!/usr/bin/env python3
"""
Ingest Pokemon TCG card data into Algolia index.
Reads CSV files, enriches with TCGdex API data, and uploads to Algolia.
"""

import os
import sys
import re
import time
import argparse
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import openpyxl
import pandas as pd
import requests
from algoliasearch.search.client import SearchClientSync

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

# Configuration
ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY")
ALGOLIA_EVENT_ID = os.getenv("ALGOLIA_EVENT_ID")
if not ALGOLIA_EVENT_ID:
    print("ERROR: ALGOLIA_EVENT_ID is not set. Set it in data/.env or export it before running.")
    sys.exit(1)
ALGOLIA_INDEX_NAME = f"tcg_cards_{ALGOLIA_EVENT_ID}"
DATA_DIR = Path(__file__).parent.parent / "data-files" / ALGOLIA_EVENT_ID
TCGDEX_BASE_URL = "https://api.tcgdex.net/v2/en"

# File name pattern to extract card set
FILE_PATTERN = r"TCG Search Website - Raw List - (.+) \((\d+)\)\.csv"


def normalize_pokemon_name(name) -> str:
    """Normalize a card name for cross-sheet matching.

    The chase tab and the set sheets are maintained by hand, so the same card can
    differ by case, padding, or apostrophe style ("Ethan's" vs "Ethan’s").
    """
    if pd.isna(name):
        return ""
    text = str(name).replace("\u2019", "'").replace("\u02bc", "'")
    return " ".join(text.lower().split())


def parse_chase_tab(xl: pd.ExcelFile) -> tuple:
    """
    Find and parse the chase/summary tab in an XLSX file.
    Scans all sheets for one containing a 'top 10' section header —
    so this works regardless of tab name or position.
    Cards in the 'top 10' section → top_10_keys (also chase).
    Cards in any other section → chase_keys only.

    Returns (top_10_keys, chase_keys) — sets of (normalized_name, number) tuples,
    or two empty sets if no matching tab is found.

    The key includes the name because the number alone is ambiguous: the set-size
    denominator is stripped ("173/165" -> "173"), so Pikachu 173/165 and Drasna
    173/191 collide and a 9-cent common inherits a chase flag.
    """
    required_columns = {'Pokemon Name', 'Number'}

    for sheet_name in xl.sheet_names:
        try:
            df = pd.read_excel(xl, sheet_name=sheet_name)
            df.columns = df.columns.str.strip()
        except Exception:
            continue

        if not required_columns.issubset(set(df.columns)):
            continue

        # Check if this sheet has a 'top 10' section header row (Number is empty)
        header_mask = df['Number'].isna()
        header_names = df.loc[header_mask, 'Pokemon Name'].astype(str).str.strip().str.lower()
        if not header_names.str.contains('top 10').any():
            continue

        # Found the chase tab — parse it
        print(f"  Found chase tab: '{sheet_name}'")
        top_10_keys = set()
        chase_keys = set()
        current_section = None

        for _, row in df.iterrows():
            name_val = row.get('Pokemon Name')
            num_val = row.get('Number')

            if pd.isna(num_val):
                if not pd.isna(name_val) and str(name_val).strip():
                    label = str(name_val).strip().lower()
                    if 'top 10' in label:
                        current_section = 'top_10'
                    else:
                        current_section = 'chase'
            elif current_section:
                # Normalize number the same way as process_xlsx_file to ensure overlay matches
                if isinstance(num_val, float):
                    num_str = str(int(num_val))
                else:
                    num_str = str(num_val).strip()
                if '/' in num_str:
                    num_str = num_str.split('/')[0]
                if num_str and num_str.lower() not in ('nan', '---'):
                    key = (normalize_pokemon_name(name_val), num_str)
                    if current_section == 'top_10':
                        top_10_keys.add(key)
                    else:
                        chase_keys.add(key)

        return top_10_keys, chase_keys

    return set(), set()


def extract_card_set_from_sheet_name(sheet_name: str) -> str:
    """
    Extract card set name from XLSX sheet name.
    Strips trailing (NNN) count and any leading prefixes separated by ' - '
    so that annotated names like "NEW - Ascended Heroes (217)" still resolve correctly.
    Examples:
      "Ascended Heroes (217)"            -> "Ascended Heroes"
      "NEW - Ascended Heroes (217)"      -> "Ascended Heroes"
      "Mega Evolution_ Phantasmal Flames" -> "Mega Evolution: Phantasmal Flames"
    """
    # Strip trailing (NNN) or truncated (NNN without closing paren (Excel 31-char tab limit)
    name = re.sub(r'\s*\(\d+\)?\s*$', '', sheet_name).strip()
    # Take the last segment after any ' - ' prefixes (e.g. "NEW - ")
    parts = re.split(r'\s+-\s*', name)
    card_set = parts[-1].strip()
    card_set = card_set.replace("_", ": ")
    return " ".join(card_set.split())


def extract_card_set_from_filename(filename: str) -> str:
    """
    Extract card set name from filename.
    Example: "TCG Search Website - Raw List - Mega Evolution (132).csv" -> "Mega Evolution"
    """
    match = re.match(FILE_PATTERN, filename)
    if match:
        card_set = match.group(1)
        # Clean up underscores - replace with colon for sub-sets
        card_set = card_set.replace("_", ": ")
        # Clean up multiple spaces
        card_set = " ".join(card_set.split())
        return card_set.strip()
    return filename


def fetch_tcgdex_set_info(set_name: str) -> Optional[dict]:
    """
    Fetch set information from TCGdex API based on set name.
    Returns full set data if found, None otherwise.
    Implements retry logic for transient failures.
    """
    max_retries = 3
    retry_delay = 1  # seconds

    for attempt in range(max_retries):
        try:
            print(f"  Fetching TCGdex set list...")
            response = requests.get(f"{TCGDEX_BASE_URL}/sets", timeout=10)
            response.raise_for_status()
            sets = response.json()

            # Try exact match first
            for s in sets:
                if s.get("name", "").lower() == set_name.lower():
                    set_id = s.get("id")
                    # Fetch full set details
                    set_response = requests.get(f"{TCGDEX_BASE_URL}/sets/{set_id}", timeout=10)
                    set_response.raise_for_status()
                    return set_response.json()

            # Try without series prefix (e.g., "Surging Sparks" from "Scarlet & Violet: Surging Sparks")
            if ":" in set_name:
                set_name_without_prefix = set_name.split(":", 1)[1].strip()
                for s in sets:
                    if s.get("name", "").lower() == set_name_without_prefix.lower():
                        set_id = s.get("id")
                        set_response = requests.get(f"{TCGDEX_BASE_URL}/sets/{set_id}", timeout=10)
                        set_response.raise_for_status()
                        print(f"  Matched TCGdex set using name without prefix: '{set_name_without_prefix}'")
                        return set_response.json()

            # Try normalized partial match — strips punctuation so truncated names like
            # "Scarlet & Violet Destined" still match "Scarlet & Violet: Destined Rivals"
            def normalize(n):
                return re.sub(r'[^a-z0-9\s]', '', n.lower())

            set_name_norm = normalize(set_name)
            for s in sets:
                if set_name_norm in normalize(s.get("name", "")):
                    set_id = s.get("id")
                    set_response = requests.get(f"{TCGDEX_BASE_URL}/sets/{set_id}", timeout=10)
                    set_response.raise_for_status()
                    return set_response.json()

            # Suffix fallback for truncated tab names (Excel 31-char limit):
            # Try progressively shorter leading-word-stripped suffixes.
            # e.g. "Scarlet & Violet Destined" -> try "Violet Destined", "Destined"
            #      "Destined" matches TCGdex "Destined Rivals"
            words = set_name.split()
            for i in range(1, len(words)):
                suffix = " ".join(words[i:])
                suffix_norm = normalize(suffix)
                for s in sets:
                    if suffix_norm in normalize(s.get("name", "")):
                        set_id = s.get("id")
                        set_response = requests.get(f"{TCGDEX_BASE_URL}/sets/{set_id}", timeout=10)
                        set_response.raise_for_status()
                        print(f"  Matched TCGdex set using suffix '{suffix}'")
                        return set_response.json()

            return None

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                print(f"  ⚠ API error (attempt {attempt + 1}/{max_retries}): {e}")
                print(f"  Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                print(f"  ✗ Failed after {max_retries} attempts: {e}")
                return None
        except Exception as e:
            print(f"  ✗ Unexpected error fetching TCGdex set info: {e}")
            return None


def find_card_number_by_name(pokemon_name: str, tcgdex_cards: list) -> tuple:
    """
    Look up a card's localId from TCGdex by name.
    Returns (localId, tcgdex_name) or (None, None).
    Pass 1: exact case-insensitive match. Pass 2: substring match.
    """
    name_lower = pokemon_name.lower().strip()
    # Pass 1: exact case-insensitive match
    for card in tcgdex_cards:
        tcg_name = card.get("name")
        local_id = card.get("localId")
        if isinstance(tcg_name, str) and tcg_name.lower().strip() == name_lower and local_id is not None:
            return str(local_id), tcg_name
    # Pass 2: substring match
    for card in tcgdex_cards:
        tcg_name = card.get("name")
        local_id = card.get("localId")
        if not isinstance(tcg_name, str) or local_id is None:
            continue
        tcg_name_lower = tcg_name.lower().strip()
        if name_lower in tcg_name_lower or tcg_name_lower in name_lower:
            return str(local_id), tcg_name
    return None, None


def parse_estimated_value(value) -> Optional[float]:
    """Parse estimated value — handles native float (XLSX) or string like "$20.60" (CSV)."""
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    if pd.isna(value) or value == "":
        return None
    try:
        return float(str(value).replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


def parse_boolean(value) -> bool:
    """Parse boolean — handles native bool, 0/1 int (XLSX), or TRUE/FALSE string (CSV)."""
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    return str(value).strip().upper() == "TRUE"


TCGPLAYER_PRODUCT_RE = re.compile(r'/product/(\d+)/')


def extract_tcgplayer_images(url: str) -> dict:
    """Extract image_small and image_large from a TCGPlayer product URL."""
    m = TCGPLAYER_PRODUCT_RE.search(url)
    if not m:
        return {}
    pid = m.group(1)
    return {
        "image_small": f"https://tcgplayer-cdn.tcgplayer.com/product/{pid}_200w.jpg",
        "image_large": f"https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_1000x1000.jpg",
    }


def fetch_card_details(set_id: str, local_id: str) -> Optional[dict]:
    """
    Fetch full card details from TCGdex API.
    Returns card data including types, or None on failure.
    """
    max_retries = 3
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            response = requests.get(f"{TCGDEX_BASE_URL}/sets/{set_id}/{local_id}", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                print(f"  ✗ Failed to fetch card {set_id}/{local_id}: {e}")
                return None
        except Exception as e:
            print(f"  ✗ Unexpected error fetching card {set_id}/{local_id}: {e}")
            return None


def enrich_card_with_tcgdex(card_number: str, tcgdex_cards: list, set_id: Optional[str] = None, debug: bool = False) -> dict:
    """
    Find matching card from TCGdex cards list by card number.
    Returns dict with enriched fields: image_small, image_large, pokemon_types.
    Fetches full card details when set_id is provided to get type data.
    """
    enriched = {}

    # Normalize card number - try both with and without leading zeros
    card_number_normalized = str(card_number).strip()
    card_number_padded = card_number_normalized.zfill(3)  # Pad to 3 digits

    for card in tcgdex_cards:
        local_id = str(card.get("localId", ""))

        # Try matching both formats
        if local_id == card_number_normalized or local_id == card_number_padded:
            # Image URLs - already available in the cards list
            base_image_url = card.get("image")
            if base_image_url:
                enriched["image_small"] = f"{base_image_url}/low.webp"
                enriched["image_large"] = f"{base_image_url}/high.webp"

            # Fetch full card details for type data
            if set_id:
                card_details = fetch_card_details(set_id, local_id)
                if card_details:
                    enriched["pokemon_types"] = card_details.get("types") or []

            return enriched

    if debug:
        print(f"    Debug: No match for card number '{card_number_normalized}' (tried '{card_number_padded}')")

    return enriched


def process_csv_file(file_path: Path, client: SearchClientSync, index_name: str, enrich: bool = True):
    """
    Process a single CSV file and upload records to Algolia.

    Args:
        file_path: Path to CSV file
        client: Algolia SearchClientSync instance
        index_name: Name of the Algolia index
        enrich: Whether to enrich with TCGdex API data
    """
    print(f"\nProcessing: {file_path.name}")

    # Extract set name from filename
    set_name = extract_card_set_from_filename(file_path.name)
    print(f"  Set Name: {set_name}")

    # Get set info from TCGdex
    set_id = None
    tcgdex_cards = []

    if enrich:
        set_info = fetch_tcgdex_set_info(set_name)
        if set_info:
            set_id = set_info.get("id")
            tcgdex_cards = set_info.get("cards", [])
            print(f"  Found TCGdex set ID: {set_id}")
            print(f"  TCGdex cards available: {len(tcgdex_cards)}")
        else:
            print(f"  ✗ Could not find TCGdex set for '{set_name}'")
            print(f"  Continuing without enrichment...")

    # Read CSV with error handling
    try:
        df = pd.read_csv(file_path)
        # Strip column names to handle trailing spaces
        df.columns = df.columns.str.strip()
        print(f"  CSV records: {len(df)}")
    except Exception as e:
        print(f"  ✗ Error reading CSV file: {e}")
        return

    # Process records
    records = []
    enriched_count = 0
    skipped: dict[str, int] = {}

    for idx, row in df.iterrows():
        try:
            pokemon_name = str(row["Pokemon Name"]).strip()

            # Skip empty rows
            if not pokemon_name or pokemon_name.lower() == 'nan':
                skipped["blank name"] = skipped.get("blank name", 0) + 1
                continue

            # Handle card numbers that may be read as floats (e.g., 172.0)
            raw_number = row["Number"]
            if pd.isna(raw_number):
                if tcgdex_cards:
                    local_id, matched_name = find_card_number_by_name(pokemon_name, tcgdex_cards)
                    if local_id:
                        local_id_str = local_id.strip()
                        card_number = str(int(local_id_str)) if local_id_str.isdigit() else local_id_str
                        print(f"  AUTO-RESOLVED number for '{pokemon_name}': #{card_number} (matched '{matched_name}')")
                    else:
                        print(f"  WARN: Skipping '{pokemon_name}' — missing number, no TCGdex match")
                        skipped["missing number"] = skipped.get("missing number", 0) + 1
                        continue
                else:
                    print(f"  WARN: Skipping '{pokemon_name}' — missing number, no TCGdex data available")
                    skipped["missing number"] = skipped.get("missing number", 0) + 1
                    continue
            elif isinstance(raw_number, float):
                card_number = str(int(raw_number)).strip()
            else:
                card_number = str(raw_number).strip()

            # Create objectID using set_id-card_number pattern (like Supabase)
            if set_id:
                object_id = f"{set_id}-{card_number}"
            else:
                # Fallback if no set_id available
                object_id = f"{set_name.replace(' ', '-').replace(':', '').lower()}-{card_number}"

            raw_qty = row["# in Machine"]
            if pd.isna(raw_qty):
                print(f"  Skipping '{pokemon_name}' #{card_number} — not in machine")
                skipped["not in machine"] = skipped.get("not in machine", 0) + 1
                continue
            machine_qty = int(raw_qty)
            record = {
                "objectID": object_id,
                "pokemon_name": pokemon_name,
                "number": card_number,
                "card_type": str(row["Card Type"]).strip(),
                "machine_quantity": machine_qty,
                "initial_quantity": machine_qty,
                "estimated_value": parse_estimated_value(row["Estimated Value"]),
                "is_chase_card": parse_boolean(row["Is Chase Card?"]),
                "is_top_10_chase_card": parse_boolean(row["Is top 10 chase card?"]),
                "is_classic_pokemon": parse_boolean(row["Is classic Pokemon?"]),
                "is_full_art": parse_boolean(row["Is Full Art?"]),
                "set_name": set_name,
            }

            # Add set_id if available
            if set_id:
                record["set_id"] = set_id

            # Enrich with TCGdex card data (adds image_small, image_large, pokemon_types)
            if enrich and tcgdex_cards:
                debug = (idx < 5)  # Debug first 5 cards
                enriched_data = enrich_card_with_tcgdex(card_number, tcgdex_cards, set_id=set_id, debug=debug)
                record.update(enriched_data)  # Safe even if empty dict
                if enriched_data.get("image_small"):
                    enriched_count += 1

            records.append(record)

            # Progress logging (every 25 cards)
            if (idx + 1) % 25 == 0:
                print(f"  Progress: {idx + 1}/{len(df)} cards processed ({enriched_count} enriched)")

        except KeyError as e:
            print(f"  ⚠ Row {idx + 1}: Missing column {e}")
            skipped["error"] = skipped.get("error", 0) + 1
            continue
        except ValueError as e:
            print(f"  ⚠ Row {idx + 1}: Invalid data format - {e}")
            skipped["error"] = skipped.get("error", 0) + 1
            continue
        except Exception as e:
            print(f"  ⚠ Row {idx + 1}: Unexpected error - {e}")
            skipped["error"] = skipped.get("error", 0) + 1
            continue

    # Final progress report
    total_skipped = sum(skipped.values())
    skip_summary = ", ".join(f"{count} {reason}" for reason, count in skipped.items()) if skipped else "none"
    print(f"  Processed {len(records)} valid records ({enriched_count} enriched) — skipped {total_skipped} ({skip_summary})")

    # Upload to Algolia
    if records:
        print(f"  Uploading {len(records)} records to Algolia...")
        try:
            response = client.save_objects(index_name=index_name, objects=records)
            print(f"  ✓ Successfully uploaded {len(records)} records")
        except Exception as e:
            print(f"  ✗ Error uploading to Algolia: {e}")
    else:
        print(f"  ⚠ No valid records to upload")


def process_xlsx_file(file_path: Path, client: SearchClientSync, index_name: str, enrich: bool = True):
    """
    Process a single XLSX file and upload records to Algolia.
    Each sheet is treated as a separate card set. Sheets prefixed with (OLD) are skipped.
    Hyperlinks on column A (Pokemon Name) are extracted to override TCGdex images.
    """
    print(f"\nProcessing XLSX: {file_path.name}")

    try:
        wb = openpyxl.load_workbook(file_path)  # for hyperlinks
        xl = pd.ExcelFile(file_path)            # for data
    except Exception as e:
        print(f"  Error reading XLSX: {e}")
        return

    top_10_keys, chase_keys = parse_chase_tab(xl)
    print(f"  Chase tab: {len(top_10_keys)} top-10 cards, {len(chase_keys)} chase cards")

    # Chase-tab entries the overlay never matched. A card listed as chase but absent
    # from every set sheet (or spelled differently there) silently loses its flag, so
    # report it instead of leaving the Top 10 carousel quietly short.
    matched_chase_keys = set()

    for sheet_name in xl.sheet_names:
        if sheet_name.strip().upper().startswith("(OLD)"):
            print(f"  Skipping sheet: {sheet_name}")
            continue

        set_name = extract_card_set_from_sheet_name(sheet_name)
        print(f"\n  Sheet: {sheet_name}  ->  Set: {set_name}")

        # Build hyperlink map: {data_row_index: url} from column A
        # Row 1 is the header, data starts at row 2 → pandas index 0
        ws = wb[sheet_name]
        hyperlinks = {}
        for row in ws.iter_rows(min_row=2, max_col=1):
            cell = row[0]
            if cell.hyperlink:
                hyperlinks[cell.row - 2] = cell.hyperlink.target  # -2: header + 0-index

        try:
            df = pd.read_excel(xl, sheet_name=sheet_name)
            df.columns = df.columns.str.strip()
        except Exception as e:
            print(f"  Error reading sheet '{sheet_name}': {e}")
            continue

        # Skip non-card sheets (e.g. "Landing Page Sections", "Most expensive")
        required_columns = {'Pokemon Name', 'Number', '# in Machine', 'Card Type', 'Estimated Value'}
        if not required_columns.issubset(set(df.columns)):
            print(f"  Skipping sheet '{sheet_name}' — not a card set (missing expected columns)")
            continue

        print(f"  Sheet records: {len(df)}")

        # Get set info from TCGdex
        set_id = None
        tcgdex_cards = []

        if enrich:
            set_info = fetch_tcgdex_set_info(set_name)
            if set_info:
                set_id = set_info.get("id")
                tcgdex_cards = set_info.get("cards", [])
                print(f"  Found TCGdex set ID: {set_id}")
                print(f"  TCGdex cards available: {len(tcgdex_cards)}")
            else:
                print(f"  ✗ Could not find TCGdex set for '{set_name}'")
                print(f"  Continuing without enrichment...")

        # Process records
        records = []
        enriched_count = 0
        skipped: dict[str, int] = {}

        for idx, row in df.iterrows():
            try:
                pokemon_name = str(row["Pokemon Name"]).strip()

                if not pokemon_name or pokemon_name.lower() == 'nan':
                    skipped["blank name"] = skipped.get("blank name", 0) + 1
                    continue

                raw_number = row["Number"]
                if pd.isna(raw_number):
                    if tcgdex_cards:
                        local_id, matched_name = find_card_number_by_name(pokemon_name, tcgdex_cards)
                        if local_id:
                            local_id_str = local_id.strip()
                            card_number = str(int(local_id_str)) if local_id_str.isdigit() else local_id_str
                            print(f"  AUTO-RESOLVED number for '{pokemon_name}': #{card_number} (matched '{matched_name}')")
                        else:
                            print(f"  WARN: Skipping '{pokemon_name}' — missing number, no TCGdex match")
                            skipped["missing number"] = skipped.get("missing number", 0) + 1
                            continue
                    else:
                        print(f"  WARN: Skipping '{pokemon_name}' — missing number, no TCGdex data available")
                        skipped["missing number"] = skipped.get("missing number", 0) + 1
                        continue
                elif isinstance(raw_number, float):
                    card_number = str(int(raw_number)).strip()
                else:
                    card_number = str(raw_number).strip()
                # Strip set-size denominator (e.g. "289/217" -> "289")
                if '/' in card_number:
                    card_number = card_number.split('/')[0]

                if set_id:
                    object_id = f"{set_id}-{card_number}"
                else:
                    object_id = f"{set_name.replace(' ', '-').replace(':', '').lower()}-{card_number}"

                raw_qty = row["# in Machine"]
                if pd.isna(raw_qty):
                    print(f"  Skipping '{pokemon_name}' #{card_number} — not in machine")
                    skipped["not in machine"] = skipped.get("not in machine", 0) + 1
                    continue
                machine_qty = int(raw_qty)
                card_type = str(row["Card Type"]).strip()
                record = {
                    "objectID": object_id,
                    "pokemon_name": pokemon_name,
                    "number": card_number,
                    "card_type": card_type,
                    "machine_quantity": machine_qty,
                    "initial_quantity": machine_qty,
                    "estimated_value": parse_estimated_value(row["Estimated Value"]),
                    "is_chase_card": parse_boolean(row.get("Is Chase Card?", False)),
                    "is_top_10_chase_card": parse_boolean(row.get("Is top 10 chase card?", False)),
                    "is_classic_pokemon": parse_boolean(row.get("Is classic Pokemon?", False)),
                    "is_full_art": card_type.lower() != "double rare",
                    "set_name": set_name,
                }

                if set_id:
                    record["set_id"] = set_id

                if enrich and tcgdex_cards:
                    debug = (idx < 5)
                    enriched_data = enrich_card_with_tcgdex(card_number, tcgdex_cards, set_id=set_id, debug=debug)
                    record.update(enriched_data)
                    if enriched_data.get("image_small"):
                        enriched_count += 1

                # Override TCGdex images with TCGPlayer images from hyperlink if present
                url = hyperlinks.get(idx)
                if url:
                    tcgplayer_images = extract_tcgplayer_images(url)
                    if tcgplayer_images:
                        record.update(tcgplayer_images)

                records.append(record)

                if (idx + 1) % 25 == 0:
                    print(f"  Progress: {idx + 1}/{len(df)} cards processed ({enriched_count} enriched)")

            except KeyError as e:
                print(f"  ⚠ Row {idx + 1}: Missing column {e}")
                skipped["error"] = skipped.get("error", 0) + 1
                continue
            except ValueError as e:
                print(f"  ⚠ Row {idx + 1}: Invalid data format - {e}")
                skipped["error"] = skipped.get("error", 0) + 1
                continue
            except Exception as e:
                print(f"  ⚠ Row {idx + 1}: Unexpected error - {e}")
                skipped["error"] = skipped.get("error", 0) + 1
                continue

        total_skipped = sum(skipped.values())
        skip_summary = ", ".join(f"{count} {reason}" for reason, count in skipped.items()) if skipped else "none"
        print(f"  Processed {len(records)} valid records ({enriched_count} enriched) — skipped {total_skipped} ({skip_summary})")

        # Overlay top-10 and chase flags from chase tab (detected by content, not position)
        overlay_count = 0
        for record in records:
            key = (normalize_pokemon_name(record["pokemon_name"]), record["number"])
            if key in top_10_keys:
                record["is_top_10_chase_card"] = True
                record["is_chase_card"] = True
                matched_chase_keys.add(key)
                overlay_count += 1
            elif key in chase_keys:
                record["is_chase_card"] = True
                matched_chase_keys.add(key)
                overlay_count += 1
        if overlay_count:
            print(f"  Overlay: {overlay_count} records flagged from chase tab")

        if records:
            print(f"  Uploading {len(records)} records to Algolia...")
            try:
                client.save_objects(index_name=index_name, objects=records)
                print(f"  ✓ Successfully uploaded {len(records)} records")
            except Exception as e:
                print(f"  ✗ Error uploading to Algolia: {e}")
        else:
            print(f"  ⚠ No valid records to upload")

    unmatched = (top_10_keys | chase_keys) - matched_chase_keys
    if unmatched:
        print(f"\n  ⚠ {len(unmatched)} chase-tab card(s) matched no ingested record —")
        print(f"    not in any set sheet, spelled differently there, or not in the machine:")
        for name, num in sorted(unmatched):
            tier = "top-10" if (name, num) in top_10_keys else "chase"
            print(f"      #{num:>4}  {name}  ({tier})")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Pokemon TCG card data ingestion for Algolia"
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Skip enrichment with TCGdex API data"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Process only a specific file (CSV or XLSX)"
    )

    args = parser.parse_args()

    # Validate environment
    if not ALGOLIA_APP_ID or not ALGOLIA_API_KEY:
        print("=" * 60)
        print("ERROR: Missing Algolia credentials")
        print("=" * 60)
        print("\nSet these in your .env file:")
        print("  ALGOLIA_APP_ID=your-app-id")
        print("  ALGOLIA_API_KEY=your-admin-api-key")
        print("  ALGOLIA_EVENT_ID=your-event-slug  (e.g. etail-west-2026)")
        print("=" * 60)
        return

    if not DATA_DIR.exists():
        print(f"Error: Data directory {DATA_DIR} does not exist")
        return

    # Initialize Algolia
    print("Connecting to Algolia...")
    client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_API_KEY)
    print(f"✓ Connected to Algolia")
    print(f"✓ Target index: {ALGOLIA_INDEX_NAME}\n")

    enrich = not args.no_enrich

    # Process files
    if args.file:
        file_path = DATA_DIR / args.file
        if not file_path.exists():
            print(f"Error: File not found: {file_path}")
            return
        if file_path.suffix.lower() == ".xlsx":
            process_xlsx_file(file_path, client, ALGOLIA_INDEX_NAME, enrich=enrich)
        else:
            process_csv_file(file_path, client, ALGOLIA_INDEX_NAME, enrich=enrich)
    else:
        csv_files = list(DATA_DIR.glob("*.csv"))
        if csv_files:
            print(f"Found {len(csv_files)} CSV files")
            for csv_file in csv_files:
                process_csv_file(csv_file, client, ALGOLIA_INDEX_NAME, enrich=enrich)
        else:
            xlsx_files = list(DATA_DIR.glob("*.xlsx"))
            if not xlsx_files:
                print(f"No CSV or XLSX files found in {DATA_DIR}")
                return
            print(f"No CSVs found. Processing {len(xlsx_files)} XLSX file(s)")
            for xlsx_file in xlsx_files:
                process_xlsx_file(xlsx_file, client, ALGOLIA_INDEX_NAME, enrich=enrich)

    print("\n" + "=" * 60)
    print("Ingestion complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
