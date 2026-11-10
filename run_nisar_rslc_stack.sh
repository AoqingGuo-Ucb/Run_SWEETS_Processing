#!/usr/bin/env bash
# NISAR Level-1 RSLC stack: search, preview or process.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode=${1:-help}
case "$mode" in
    help|-h|--help) echo "Usage: bash run_nisar_rslc_stack.sh {search|plan|run} [trusted_site_settings.sh]"; exit 0 ;;
    search|plan|run) ;;
    *) echo "Unknown mode: $mode" >&2; exit 2 ;;
esac

# Example settings for Langtung. Change these for a different study area.
SITE=Langtung_NISAR
BBOX=(85.3894 28.1588 85.6635 28.3509)
TRACK=98
FRAME=16
START_DATE=2026-06-17
END_DATE=2026-09-26
MAX_TEMPORAL_BASELINE_DAYS=36
MAX_BANDWIDTH=""   # Optional: also restrict to the next K acquisitions.
FREQUENCY=A
POL=HH
POSTING=10
DEM=""             # Empty: prepare a DEM with SWEETS. Otherwise use an existing ellipsoidal DEM.
SWEETS_REPO="$HOME/Bhaltos/AoqingShare/sfw/sweets"
if [[ -n "${2:-}" ]]; then source "$2"; fi
RSLC_HELPER=${RSLC_HELPER:-"$SWEETS_REPO/run_nisar_rslc.py"}
# One named root contains the ID list, terrain and processing outputs.
STACK_ROOT=${STACK_ROOT:-"$HOME/Bhaltos/AoqingShare/NISAR_Projects/${SITE}_stack"}
IDS_FILE=${IDS_FILE:-"$STACK_ROOT/${SITE}_rslc_ids.txt"}
PROJECT=${PROJECT:-"$STACK_ROOT/processing"}
DEM_CACHE=${DEM_CACHE:-"$STACK_ROOT/dem"}
[[ "$STACK_ROOT" == /* && "$DEM_CACHE" == /* ]] || { echo 'Use absolute root/cache paths.' >&2; exit 2; }
[[ "$IDS_FILE" == /* && "$PROJECT" == /* && "$RSLC_HELPER" == /* ]] || { echo 'Use absolute paths in settings.' >&2; exit 2; }
cd "$SWEETS_REPO"
[[ -f "$RSLC_HELPER" && -r "$RSLC_HELPER" ]] || { echo "Missing or unreadable RSLC helper: $RSLC_HELPER" >&2; exit 2; }
base=(--bbox "${BBOX[@]}" --track "$TRACK" --frame "$FRAME" --dates "$START_DATE" "$END_DATE")
# Hold one lock across list preparation, downloads and processing.
mkdir -p -- "$STACK_ROOT"
exec 9>"$STACK_ROOT/.run.lock"
flock -n 9 || { echo "Another run is active in $STACK_ROOT" >&2; exit 2; }
pixi run python "$SWEETS_REPO/prepare_nisar_stack.py" --helper "$RSLC_HELPER" --ids-file "$IDS_FILE" -- "${base[@]}"
[[ "$mode" != search ]] || exit 0
# Preserve previous attempts. Reuse their RSLCs, but regenerate unverified GSLCs.
if [[ "$mode" == run && -e "$PROJECT" ]]; then
    previous_project="$PROJECT"
    attempt=2
    while [[ -e "${previous_project}_$attempt" ]]; do
        previous_candidate="${previous_project}_$attempt"
        attempt=$((attempt + 1))
    done
    export RSLC_REUSE_PROJECT="${previous_candidate:-$previous_project}"
    PROJECT="${previous_project}_$attempt"
fi
echo "ID list: $IDS_FILE"
echo "Processing directory: $PROJECT"
args=("${base[@]}" --ids-file "$IDS_FILE" --max-days "$MAX_TEMPORAL_BASELINE_DAYS")
if [[ -n "$MAX_BANDWIDTH" ]]; then args+=(--max-bandwidth "$MAX_BANDWIDTH"); fi
if [[ "$mode" == plan ]]; then
    exec python3 "$RSLC_HELPER" plan "${args[@]}"
fi
if [[ -n "$DEM" ]]; then
    [[ "$DEM" == /* && -f "$DEM" ]] || { echo 'Explicit DEM must be an existing absolute file path.' >&2; exit 2; }
else
    [[ ! -e "$PROJECT" ]] || { echo "Project already exists: $PROJECT" >&2; exit 2; }
    # Reject an invalid date network before downloading terrain data.
    python3 "$RSLC_HELPER" plan "${args[@]}" >/dev/null
    # Keep terrain inside STACK_ROOT but outside the new processing directory.
    dem_result=$(mktemp)
    trap 'rm -f -- "$dem_result"' EXIT
    pixi run python - "$dem_result" "$DEM_CACHE" "${BBOX[@]}" <<'PYDEM'
import contextlib
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from osgeo import gdal, osr
from sweets.dem import create_dem

result = Path(sys.argv[1])
cache_root = Path(sys.argv[2])
w, s, e, n = map(float, sys.argv[3:])
bounds = (max(-180., w-.25), max(-90., s-.25), min(180., e+.25), min(90., n+.25))
key = hashlib.sha256(json.dumps(bounds).encode()).hexdigest()[:20]
cache = cache_root / key
cache.mkdir(parents=True, exist_ok=True)
target = cache / "dem.tif"

def check(path):
    ds = gdal.Open(str(path))
    if not ds or ds.RasterCount < 1:
        raise SystemExit(f"Unreadable DEM: {path}")
    gt = ds.GetGeoTransform()
    srs = ds.GetSpatialRef()
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    if not srs or not srs.IsSameGeogCS(wgs) or not srs.IsGeographic() or gt[2] or gt[4] or gt[1]<=0 or gt[5]>=0:
        raise SystemExit(f"Unexpected geographic DEM grid: {path}")
    if not (gt[0] <= w < e <= gt[0]+ds.RasterXSize*gt[1] and
            gt[3]+ds.RasterYSize*gt[5] <= s < n <= gt[3]):
        raise SystemExit(f"DEM does not cover the requested AOI: {path}")

with (cache / ".lock").open("a") as lock, contextlib.redirect_stdout(sys.stderr):
    fcntl.flock(lock, fcntl.LOCK_EX)
    if target.exists():
        check(target)
    else:
        print(f"Preparing SWEETS DEM for buffered bounds {bounds}", file=sys.stderr)
        with tempfile.TemporaryDirectory(dir=cache) as tmp:
            staged = Path(tmp) / "dem.tif"
            create_dem(staged, bounds)
            check(staged)
            staged.replace(target)
result.write_text(str(target) + "\n")
PYDEM
    IFS= read -r DEM < "$dem_result"
    rm -f -- "$dem_result"
    trap - EXIT
fi
printf '%s\n' "$DEM" > "$STACK_ROOT/dem_path.txt"
echo "RSLC geocoding DEM: $DEM"
exec pixi run python "$RSLC_HELPER" stack "${args[@]}" --dem "$DEM" --project "$PROJECT" --frequency "$FREQUENCY" --pol "$POL" --posting "$POSTING"
