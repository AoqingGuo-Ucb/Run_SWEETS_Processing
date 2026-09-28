#!/usr/bin/env bash
# NISAR GSLC -> SWEETS/Dolphin. Independent of the Sentinel-1 SAFE workflows.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'HELP'
Usage: bash run_sweets_nisar.sh [1|2|3|config] [trusted_site_settings.sh]
  1       Create a NEW configuration and run the full NISAR workflow (default).
  2       Use existing configuration; run SWEETS from step 1 (download/preparation).
  3       Use existing configuration and downloaded VRTs; run SWEETS from step 3.
  config  Create a NEW configuration only, without downloading/processing.
  -h, --help  Show this help.
These wrapper modes differ from the Sentinel-1 script's six stages.
New configurations default to NISAR_L2_GSLC_PROVISIONAL_V1.
Set NISAR_COLLECTION in your settings file to select BETA instead.
Resume modes preserve the existing YAML collection; use a new SITE to switch.
HELP
}
case "${1:-1}" in -h|--help) usage; exit 0;; esac
MODE="${1:-1}"
case "$MODE" in 1|2|3|config) ;; *) usage >&2; exit 2;; esac
[[ $# -le 2 ]] || { usage >&2; exit 2; }

# Edit these defaults or supply a trusted shell settings file as argument 2.
SITE="Prima_NISAR"
WEST=-117.646
SOUTH=33.4704
EAST=-117.5842
NORTH=33.5084
# Example dates only: verify GSLC availability for your AOI in ASF/CMR.
START_DATE="2026-01-01"
END_DATE="2026-09-26"
# NISAR track/frame, NOT Sentinel-1 relative orbit. Empty = unpinned search.
NISAR_TRACK=""
NISAR_FRAME=""
NISAR_FREQUENCY="A"
NISAR_POLARIZATION="HH"
NISAR_COLLECTION="NISAR_L2_GSLC_PROVISIONAL_V1"
# Decimation of the NISAR input grid; do not inherit S1's (2,4) blindly.
SWEETS_STRIDES=(1 1)
SWEETS_REPO="$HOME/Bhaltos/AoqingShare/sfw/sweets"
# Optional absolute path; empty searches SWEETS_REPO, then this script's directory.
NISAR_CHECK_SCRIPT=""
PROJECT_ROOT="$HOME/Bhaltos/AoqingShare/NISAR_Projects"

if [[ -n "${2:-}" ]]; then source "$2"; fi
case "$NISAR_COLLECTION" in
    NISAR_L2_GSLC_PROVISIONAL_V1|NISAR_L2_GSLC_BETA_V1) ;;
    *) echo 'ERROR: NISAR_COLLECTION must be NISAR_L2_GSLC_PROVISIONAL_V1 or NISAR_L2_GSLC_BETA_V1' >&2; exit 2;;
esac
[[ "$SITE" =~ ^[A-Za-z0-9_-]+$ ]] || { echo 'ERROR: invalid SITE name' >&2; exit 2; }
[[ -d "$SWEETS_REPO" ]] || { echo "ERROR: missing SWEETS_REPO: $SWEETS_REPO" >&2; exit 2; }
if [[ "$MODE" != config ]]; then
    if [[ -z "$NISAR_CHECK_SCRIPT" ]]; then
        if [[ -f "$SWEETS_REPO/run_nisar_checked.py" ]]; then
            NISAR_CHECK_SCRIPT="$SWEETS_REPO/run_nisar_checked.py"
        else
            NISAR_CHECK_SCRIPT="$SCRIPT_DIR/run_nisar_checked.py"
        fi
    fi
    [[ "$NISAR_CHECK_SCRIPT" == /* && -f "$NISAR_CHECK_SCRIPT" ]] || {
        echo "ERROR: NISAR_CHECK_SCRIPT must point to an existing absolute file path: $NISAR_CHECK_SCRIPT" >&2
        echo 'Place run_nisar_checked.py in SWEETS_REPO or beside this script, or set NISAR_CHECK_SCRIPT.' >&2
        exit 2
    }
    echo "NISAR input checker: $NISAR_CHECK_SCRIPT"
fi
command -v pixi >/dev/null || { echo 'ERROR: pixi is not available' >&2; exit 2; }
command -v flock >/dev/null || { echo 'ERROR: flock is required (run on Ubuntu)' >&2; exit 2; }
python3 - "$WEST" "$SOUTH" "$EAST" "$NORTH" "$START_DATE" "$END_DATE" \
    "$NISAR_TRACK" "$NISAR_FRAME" "$NISAR_FREQUENCY" "$NISAR_POLARIZATION" "${SWEETS_STRIDES[@]}" <<'PY'
import sys
from datetime import date
w,s,e,n=map(float,sys.argv[1:5])
assert -180 <= w < e <= 180 and -90 <= s < n <= 90, 'Invalid bbox'
assert date.fromisoformat(sys.argv[5]) < date.fromisoformat(sys.argv[6]), 'Start must precede end'
for value in sys.argv[7:9]:
    assert not value or (value.isdigit() and int(value)>0), 'Track/frame must be positive integers or empty'
assert sys.argv[9] in ('A','B'), 'Frequency must be A or B'
assert sys.argv[10] in ('HH','HV','VV','VH'), 'Invalid polarization'
assert len(sys.argv[11:]) == 2 and all(int(x)>0 for x in sys.argv[11:]), 'Two positive strides required'
PY
SITE_DIR="$PROJECT_ROOT/$SITE"
CONFIG_FILE="$SITE_DIR/sweets_config.yaml"
DATA_DIR="$SITE_DIR/data"
WORK_DIR="$SITE_DIR/work"
LOG_DIR="$SITE_DIR/logs"
if [[ -d "$SITE_DIR" && ! -f "$SITE_DIR/.nisar-workflow" ]]; then
    echo "ERROR: existing unmarked directory; use a new NISAR PROJECT_ROOT/SITE: $SITE_DIR" >&2
    exit 2
fi
mkdir -p "$SITE_DIR"
exec 9>"$SITE_DIR/.run.lock"
flock -n 9 || { echo 'ERROR: this NISAR site is already running' >&2; exit 2; }
touch "$SITE_DIR/.nisar-workflow"
mkdir -p "$DATA_DIR" "$WORK_DIR" "$LOG_DIR"
cd "$SWEETS_REPO"

HELP_TEXT="$(pixi run sweets config --help)"
if [[ "$HELP_TEXT" != *nisar-gslc* || "$HELP_TEXT" != *--polarizations* || "$HELP_TEXT" != *--dolphin.strides* ]]; then
    echo 'ERROR: installed SWEETS CLI lacks required NISAR options. No Sentinel-1 fallback will run.' >&2
    exit 2
fi
# Verify the installed model as well as CLI help.
pixi run python -c 'from sweets.download import NisarGslcSearch; from opera_utils.nisar import search; print("NISAR support: available")'

if [[ "$MODE" == 1 || "$MODE" == config ]]; then
    [[ ! -e "$CONFIG_FILE" ]] || { echo "ERROR: config exists; use mode 2 to resume, or a new SITE for new settings: $CONFIG_FILE" >&2; exit 2; }
    SELECTORS=(--source nisar-gslc)
    [[ -z "$NISAR_TRACK" ]] || SELECTORS+=(--track "$NISAR_TRACK")
    [[ -z "$NISAR_FRAME" ]] || SELECTORS+=(--frame "$NISAR_FRAME")
    pixi run sweets config \
        --bbox "$WEST" "$SOUTH" "$EAST" "$NORTH" \
        --start "$START_DATE" --end "$END_DATE" \
        "${SELECTORS[@]}" \
        --frequency "$NISAR_FREQUENCY" --polarizations "$NISAR_POLARIZATION" \
        --dolphin.strides "${SWEETS_STRIDES[@]}" \
        --out-dir "$DATA_DIR" --work-dir "$WORK_DIR" --output "$CONFIG_FILE" \
        2>&1 | tee "$LOG_DIR/config.log"
fi
[[ -f "$CONFIG_FILE" ]] || { echo 'ERROR: configuration missing; run mode config or 1 first' >&2; exit 2; }
# Existing YAML is authoritative on resume, not changed shell date/bbox settings.
pixi run python - "$CONFIG_FILE" "$DATA_DIR" "$WORK_DIR" "$MODE" "$NISAR_COLLECTION" <<'PY'
import sys
from pathlib import Path
from sweets.core import Workflow
from sweets.download import NisarGslcSearch
wf=Workflow.from_yaml(sys.argv[1])
if not isinstance(wf.search,NisarGslcSearch):
    raise SystemExit('ERROR: refusing a non-NISAR configuration')
if Path(wf.search.out_dir).resolve()!=Path(sys.argv[2]).resolve() or Path(wf.work_dir).resolve()!=Path(sys.argv[3]).resolve():
    raise SystemExit('ERROR: configuration points outside this NISAR site')
if wf.overwrite:
    raise SystemExit('ERROR: overwrite=true is not supported by this resume wrapper')
if 'short_name' not in NisarGslcSearch.model_fields:
    raise SystemExit('ERROR: installed SWEETS does not support a configurable NISAR collection')
requested_collection=sys.argv[5]
if sys.argv[4] in ('1','config'):
    # The flat SWEETS CLI does not expose the collection. Persist it through
    # the supported model API before any observation download can start.
    wf.search.short_name=requested_collection
    wf.to_yaml(sys.argv[1])
    wf=Workflow.from_yaml(sys.argv[1])
    if wf.search.short_name!=requested_collection:
        raise SystemExit('ERROR: SWEETS did not persist the requested NISAR collection')
elif wf.search.short_name!=requested_collection:
    print(f'WARNING: existing YAML uses {wf.search.short_name}; shell settings request {requested_collection}. '
          'Resuming the existing collection. Use a new SITE with mode 1 or config to switch collections.')
if sys.argv[4]=='3':
    files=wf.search.existing_files()
    if len(files)<2 or not all(Path(p).is_file() for p in files):
        raise SystemExit('ERROR: mode 3 needs downloaded NISAR VRT inputs; use mode 2 first')
    if not Path(wf.dem_filename).is_file() or not Path(wf.water_mask_filename).is_file():
        raise SystemExit('ERROR: missing auxiliary files; use mode 2 first')
print(f'NISAR config: {sys.argv[1]}')
print(f'CMR short_name: {wf.search.short_name}')
print(f'Source: {wf.search}')
PY
if [[ "$MODE" == config ]]; then
    echo "Configuration ready: $CONFIG_FILE"
    exit 0
fi
SWEETS_STEP=1
[[ "$MODE" != 3 ]] || SWEETS_STEP=3
# pipefail propagates SWEETS failures while preserving a log for every attempt.
LOG_FILE="$LOG_DIR/nisar_$(date +%Y%m%dT%H%M%S)_$$.log"
pixi run python "$NISAR_CHECK_SCRIPT" "$CONFIG_FILE" --starting-step "$SWEETS_STEP" 2>&1 | tee "$LOG_FILE"
echo "NISAR processing complete: $WORK_DIR/dolphin"
echo "Log: $LOG_FILE"
