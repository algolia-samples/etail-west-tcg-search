#!/bin/bash
# Set up a new TCG event end-to-end:
#   1. Create Algolia indices and tcg_events record
#   2. Clear any existing card data in the new index
#   3. Ingest all card CSVs
#   4. Enrich chase cards
#   5. Create Agent Studio agent
#   6. Mark event as active (current:true)
#
# Usage:
#   ./setup_event.sh <event_id> <event_name> <booth> [venue] [--no-activate]
#
# Pass "" for <booth> when no booth number has been assigned yet — the header and
# the agent prompt then omit the booth entirely. Add it later with a partial update
# to the tcg_events record plus: agent.py update <event_id> --publish
#
# Example:
#   ./setup_event.sh etail-west-2026 "Etail West 2026" 701 "Long Beach Convention Center"
#   ./setup_event.sh shoptalk-2026 "Shoptalk 2026" 314 --no-activate

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_UTILS="$SCRIPT_DIR/data/data-utilities"
AGENT_DIR="$SCRIPT_DIR/agent"
DATA_PYTHON="$DATA_UTILS/.venv/bin/python"
AGENT_PYTHON="$AGENT_DIR/.venv/bin/python"

# ── Argument validation ────────────────────────────────────────────────────────

if [ $# -lt 3 ]; then
  echo "Usage: $0 <event_id> <event_name> <booth> [venue] [--no-activate]"
  echo ""
  echo "  event_id      Slug used for index names and URLs, e.g. etail-west-2026"
  echo "  event_name    Human-readable name (quote it), e.g. \"Etail West 2026\""
  echo "  booth         Booth number, e.g. 701 (pass \"\" if not yet assigned)"
  echo "  venue         (optional) Venue name, e.g. \"Long Beach Convention Center\""
  echo "  --no-activate Skip setting the event as active (step 6)"
  exit 1
fi

EVENT_ID="$1"
EVENT_NAME="$2"
BOOTH="$3"

# Parse remaining args for optional venue and --no-activate flag
VENUE=""
NO_ACTIVATE=false
for arg in "${@:4}"; do
  if [ "$arg" = "--no-activate" ]; then
    NO_ACTIVATE=true
  else
    VENUE="$arg"
  fi
done

# ── Env var check ──────────────────────────────────────────────────────────────

DATA_ENV_FILE="$SCRIPT_DIR/data/.env"
if [ ! -f "$DATA_ENV_FILE" ]; then
  echo "ERROR: data/.env not found — copy data/.env.example and fill in credentials"
  exit 1
fi

if ! grep -qE "^ALGOLIA_APP_ID=.+" "$DATA_ENV_FILE" || ! grep -qE "^ALGOLIA_API_KEY=.+" "$DATA_ENV_FILE"; then
  echo "ERROR: ALGOLIA_APP_ID and ALGOLIA_API_KEY must be set in data/.env"
  exit 1
fi

# Export event ID so ingest/configure/clear scripts pick it up
export ALGOLIA_EVENT_ID="$EVENT_ID"

# ── Summary ────────────────────────────────────────────────────────────────────

echo "========================================"
echo "  TCG Event Setup"
echo "========================================"
echo "  Event ID   : $EVENT_ID"
echo "  Event Name : $EVENT_NAME"
echo "  Booth      : ${BOOTH:-(none yet)}"
[ -n "$VENUE" ] && echo "  Venue      : $VENUE"
echo "  Index      : tcg_cards_${EVENT_ID}"
[ "$NO_ACTIVATE" = true ] && echo "  Activate   : skipped (--no-activate)"
echo "========================================"
echo ""

# ── Step 1: Create event record + Algolia indices ──────────────────────────────

echo "========================================"
echo "Step 1/6: Creating event record and Algolia indices"
echo "========================================"
if [ -n "$VENUE" ]; then
  (cd "$DATA_UTILS" && "$DATA_PYTHON" create_event.py "$EVENT_ID" "$EVENT_NAME" "$BOOTH" --venue "$VENUE")
else
  (cd "$DATA_UTILS" && "$DATA_PYTHON" create_event.py "$EVENT_ID" "$EVENT_NAME" "$BOOTH")
fi

# Create per-event data directory for CSV files
DATA_DIR="$SCRIPT_DIR/data/data-files/$EVENT_ID"
mkdir -p "$DATA_DIR"
echo "  ✓ Data directory: data/data-files/$EVENT_ID/"

# ── Step 2: Clear index ────────────────────────────────────────────────────────

echo ""
echo "========================================"
echo "Step 2/6: Clearing index tcg_cards_${EVENT_ID}"
echo "========================================"
(cd "$DATA_UTILS" && "$DATA_PYTHON" clear_index.py --yes)

# ── Step 3 guard: check for CSV or XLSX files ─────────────────────────────────

CSV_FILES=("$DATA_DIR"/*.csv)
XLSX_FILES=("$DATA_DIR"/*.xlsx)
if [ ! -e "${CSV_FILES[0]}" ] && [ ! -e "${XLSX_FILES[0]}" ]; then
  echo ""
  echo "  ⚠  No CSV or XLSX files found in data/data-files/$EVENT_ID/"
  echo ""
  echo "  Copy card data into that directory, then run:"
  echo "    data/data-utilities/reset_and_ingest.sh $EVENT_ID"
  echo ""
  echo "  Then activate the event when ready:"
  echo "    cd data/data-utilities && .venv/bin/python set_active_event.py set $EVENT_ID"
  echo ""
  echo "Algolia indices created:"
  echo "  tcg_cards_${EVENT_ID}"
  echo "  tcg_cards_${EVENT_ID}_price_asc"
  echo "  tcg_cards_${EVENT_ID}_price_desc"
  exit 0
fi

# ── Step 3: Ingest card data ───────────────────────────────────────────────────

echo ""
echo "========================================"
echo "Step 3/6: Ingesting card data"
echo "========================================"
(cd "$DATA_UTILS" && "$DATA_PYTHON" ingest.py)

# ── Step 4: Enrich chase cards (optional — requires XLSX) ─────────────────────

echo ""
echo "========================================"
echo "Step 4/6: Enriching chase cards"
echo "========================================"
XLSX_FILE="$DATA_DIR/TCG Search Website - Raw List.xlsx"
if [ -f "$XLSX_FILE" ]; then
  (cd "$DATA_UTILS" && "$DATA_PYTHON" enrich_chase_cards.py)
elif [ -e "${XLSX_FILES[0]}" ]; then
  echo "  Skipped — XLSX-only event (images sourced from column A hyperlinks)"
else
  echo "  Skipped — no XLSX found in data/data-files/$EVENT_ID/"
  echo "  To enrich later: copy the XLSX then run:"
  echo "    cd data/data-utilities && ALGOLIA_EVENT_ID=$EVENT_ID .venv/bin/python enrich_chase_cards.py"
fi

# ── Step 5: Create Agent Studio agent ─────────────────────────────────────────

echo ""
echo "========================================"
echo "Step 5/6: Creating Agent Studio agent"
echo "========================================"
(cd "$AGENT_DIR" && "$AGENT_PYTHON" agent.py create "$EVENT_ID" "$EVENT_NAME" "$BOOTH" --publish)

# ── Step 6: Set event as active ────────────────────────────────────────────────

echo ""
echo "========================================"
echo "Step 6/6: Setting '$EVENT_ID' as the active event"
echo "========================================"
if [ "$NO_ACTIVATE" = true ]; then
  echo "  Skipped (--no-activate). To activate when ready:"
  echo "    cd data/data-utilities && .venv/bin/python set_active_event.py set $EVENT_ID"
else
  (cd "$DATA_UTILS" && "$DATA_PYTHON" set_active_event.py set "$EVENT_ID")
fi

# ── Done ───────────────────────────────────────────────────────────────────────

echo ""
echo "========================================"
echo "  Event setup complete!"
echo "========================================"
echo ""
echo "Algolia indices created:"
echo "  tcg_cards_${EVENT_ID}"
echo "  tcg_cards_${EVENT_ID}_price_asc"
echo "  tcg_cards_${EVENT_ID}_price_desc"
echo ""
if [ "$NO_ACTIVATE" = true ]; then
  echo "To activate when ready:"
  echo "  cd data/data-utilities && .venv/bin/python set_active_event.py set $EVENT_ID"
else
  echo "The app at / will now redirect to /$EVENT_ID"
fi
