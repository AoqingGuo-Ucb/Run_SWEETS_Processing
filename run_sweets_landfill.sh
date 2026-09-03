#!/usr/bin/env bash
#
# run_sweets_landfill.sh
#
# Reusable SWEETS / burst2safe workflow:
#   1. Create SWEETS config
#   2. Preflight Sentinel-1 acquisitions
#   3. Reconstruct GOOD SAFEs one acquisition at a time
#   4. Validate reconstructed SAFEs (calibration XML)
#   5. Download EOF orbit files
#   6. Run SWEETS from its step 2
#
# Features:
#   - START_STEP lets you resume without repeating finished stages.
#   - S1D is excluded by default because the currently installed sentineleof
#     client does not recognize mission "S1D".
#   - Existing S1D SAFE folders are moved to a separate quarantine directory,
#     not deleted.
#   - Per-acquisition burst download retries prevent one timeout from killing
#     the complete multi-year stack.
#

set -u
set -o pipefail

###############################################
############### USER SETTINGS #################
###############################################

SITE="Chiquita"

# BBox order: WEST SOUTH EAST NORTH
WEST=-118.659
SOUTH=33.4433
EAST=-118.6317
NORTH=34.4203

START_DATE="2016-01-01"
END_DATE="2026-07-01"

TRACK=71

SWEETS_REPO="$HOME/Bhaltos/AoqingShare/sfw/sweets"
PROJECT_ROOT="$HOME/Bhaltos/AoqingShare/CA_Landfill"

# ------------------------------------------------------------
# Resume control
# ------------------------------------------------------------
# 1 = create config
# 2 = preflight
# 3 = reconstruct SAFEs
# 4 = validate reconstructed SAFEs
# 5 = download EOF files
# 6 = run SWEETS from SWEETS step 2
#
# Recommended usage:
#   ./run_sweets_landfill.sh 4
# starts at SAFE validation and continues through EOF + SWEETS.
#
#   ./run_sweets_landfill.sh 6
# skips everything except SWEETS.
#
# If no positional argument is supplied, workflow starts from DEFAULT_START_STEP.
DEFAULT_START_STEP=1
START_STEP="${1:-$DEFAULT_START_STEP}"

# ------------------------------------------------------------
# Mission filtering
# ------------------------------------------------------------
# Current sentineleof in this environment supports S1A/S1B/S1C but raises
# KeyError: 'S1D' when it finds an S1D SAFE.
#
# Keep this true until your eof/sentineleof installation supports S1D.
EXCLUDE_S1D=true

###############################################
############ ADVANCED SETTINGS ################
###############################################

POL="VV"
MODE="IW"
MIN_BURSTS=1
EOF_WORKERS=8

BURST_MAX_RETRIES=4
BURST_RETRY_DELAY=30

# Leave empty for AUTO/all intersecting swaths.
# Only set e.g. SWATH="IW2" if intentionally restricting the AOI.
SWATH=""

###############################################
############### DERIVED PATHS #################
###############################################

SITE_DIR="$PROJECT_ROOT/$SITE"
WORK_DIR="$SITE_DIR/work"
CONFIG_FILE="$SITE_DIR/sweets_config.yaml"
ORBIT_DIR="$SWEETS_REPO/orbits"

CHECK_SCRIPT="$SWEETS_REPO/check_bad_absorbits.py"
CALIBRATION_CHECK_SCRIPT="$SWEETS_REPO/check_bad_calibration_safes.py"

PREFLIGHT_DIR="$SITE_DIR/burst_preflight"
ORBIT_REPORT="$PREFLIGHT_DIR/orbit_check.csv"
BAD_ORBITS="$PREFLIGHT_DIR/bad_absorbits.txt"
GOOD_RANGES="$PREFLIGHT_DIR/good_date_ranges.txt"
GOOD_ACQUISITIONS="$PREFLIGHT_DIR/good_acquisitions.tsv"

LOG_DIR="$SITE_DIR/logs"
CHECK_LOG="$LOG_DIR/check_bad_absorbits.log"
CALIBRATION_CHECK_LOG="$LOG_DIR/check_bad_calibration_safes.log"
BURST_LOG="$LOG_DIR/burst2stack_per_acquisition.log"
FAILED_DOWNLOADS="$LOG_DIR/failed_downloads.tsv"
EOF_LOG="$LOG_DIR/eof.log"
SWEETS_LOG="$LOG_DIR/sweets_step2.log"

# Unsupported or failed data is moved here instead of deleted.
S1D_QUARANTINE_DIR="$PROJECT_ROOT/${SITE}_excluded_S1D"
FAILED_SAFE_QUARANTINE_DIR="$PROJECT_ROOT/${SITE}_failed_SAFEs"
BAD_CALIBRATION_QUARANTINE_DIR="$PROJECT_ROOT/${SITE}_bad_calibration_SAFEs"

mkdir -p \
    "$SITE_DIR" \
    "$WORK_DIR" \
    "$ORBIT_DIR" \
    "$PREFLIGHT_DIR" \
    "$LOG_DIR"

###############################################
############### BASIC VALIDATION ##############
###############################################

if [[ ! "$START_STEP" =~ ^[1-6]$ ]]; then
    echo "ERROR: START_STEP must be 1, 2, 3, 4, 5, or 6."
    exit 1
fi

if [[ ! -d "$SWEETS_REPO" ]]; then
    echo "ERROR: SWEETS repository not found:"
    echo "  $SWEETS_REPO"
    exit 1
fi

# Absolute-orbit checker is required for steps 2-3.
if [[ "$START_STEP" -le 3 && ! -f "$CHECK_SCRIPT" ]]; then
    echo "ERROR: absolute-orbit checker not found:"
    echo "  $CHECK_SCRIPT"
    exit 1
fi

# SAFE calibration checker is required when step 4 will run.
if [[ "$START_STEP" -le 4 && ! -f "$CALIBRATION_CHECK_SCRIPT" ]]; then
    echo "ERROR: SAFE calibration checker not found:"
    echo "  $CALIBRATION_CHECK_SCRIPT"
    echo
    echo "Copy check_bad_calibration_safes.py into the SWEETS repository first."
    exit 1
fi

python3 - <<PY
from datetime import datetime

west, south, east, north = $WEST, $SOUTH, $EAST, $NORTH
if west >= east:
    raise SystemExit("ERROR: WEST must be smaller than EAST")
if south >= north:
    raise SystemExit("ERROR: SOUTH must be smaller than NORTH")

start = datetime.strptime("$START_DATE", "%Y-%m-%d").date()
end = datetime.strptime("$END_DATE", "%Y-%m-%d").date()
if start > end:
    raise SystemExit("ERROR: START_DATE is later than END_DATE")

print("Input check: OK")
PY

cd "$SWEETS_REPO" || exit 1
PX="pixi run"

CHECK_SWATH_ARGS=()
BURST_SWATH_ARGS=()

if [[ -n "$SWATH" ]]; then
    CHECK_SWATH_ARGS=(--swath "$SWATH")
    BURST_SWATH_ARGS=(--swaths "$SWATH")
fi

###############################################
############### HELPER FUNCTIONS ##############
###############################################

step_enabled() {
    local step="$1"
    [[ "$START_STEP" -le "$step" ]]
}

quarantine_s1d_safes() {
    if [[ "$EXCLUDE_S1D" != true ]]; then
        return 0
    fi

    mkdir -p "$S1D_QUARANTINE_DIR"

    local found=0
    local base dest stem n
    shopt -s nullglob

    for safe in "$SITE_DIR"/S1D*.SAFE; do
        found=1
        base="$(basename "$safe")"
        dest="$S1D_QUARANTINE_DIR/$base"

        # A previous run may already have quarantined a SAFE with the same name.
        # Preserve both copies and still clear S1D from the active SITE_DIR.
        if [[ -e "$dest" ]]; then
            stem="${base%.SAFE}"
            n=2
            dest="$S1D_QUARANTINE_DIR/${stem}_duplicate${n}.SAFE"
            while [[ -e "$dest" ]]; do
                n=$((n + 1))
                dest="$S1D_QUARANTINE_DIR/${stem}_duplicate${n}.SAFE"
            done
            echo "S1D SAFE already exists in quarantine; preserving both copies."
        fi

        echo "Moving unsupported S1D SAFE out of active site directory:"
        echo "  $base"
        echo "  -> $(basename "$dest")"

        if ! mv "$safe" "$dest"; then
            echo "WARNING: could not quarantine:"
            echo "  $safe"
            echo "Continuing instead of terminating the workflow."
        fi
    done

    for zipfile in "$SITE_DIR"/S1D*.zip; do
        found=1
        base="$(basename "$zipfile")"
        dest="$S1D_QUARANTINE_DIR/$base"

        if [[ -e "$dest" ]]; then
            stem="${base%.zip}"
            n=2
            dest="$S1D_QUARANTINE_DIR/${stem}_duplicate${n}.zip"
            while [[ -e "$dest" ]]; do
                n=$((n + 1))
                dest="$S1D_QUARANTINE_DIR/${stem}_duplicate${n}.zip"
            done
            echo "S1D ZIP already exists in quarantine; preserving both copies."
        fi

        echo "Moving unsupported S1D ZIP out of active site directory:"
        echo "  $base"
        echo "  -> $(basename "$dest")"

        if ! mv "$zipfile" "$dest"; then
            echo "WARNING: could not quarantine:"
            echo "  $zipfile"
            echo "Continuing instead of terminating the workflow."
        fi
    done

    shopt -u nullglob

    if [[ "$found" -eq 1 ]]; then
        echo "S1D files were preserved in:"
        echo "  $S1D_QUARANTINE_DIR"
    fi

    return 0
}


quarantine_failed_safes() {
    # A failed burst2stack attempt may still leave a partial .SAFE directory.
    # COMPASS/S1Reader will discover it later and can fail with errors such as:
    #   ValueError: burst iw1-slc-vv not in SAFE
    #
    # Use failed_downloads.tsv as the authoritative list and move every matching
    # SAFE out of SITE_DIR before EOF/SWEETS. Never delete the data.

    if [[ ! -f "$FAILED_DOWNLOADS" ]]; then
        return 0
    fi

    mkdir -p "$FAILED_SAFE_QUARANTINE_DIR"

    local acq_date platform abs_orbit exit_code
    local mission date_compact orbit6 safe base dest stem n
    local moved=0

    while IFS=$'\t' read -r acq_date platform abs_orbit exit_code; do
        [[ -z "${acq_date:-}" ]] && continue
        [[ "$acq_date" == \#* ]] && continue

        case "$platform" in
            SENTINEL-1A|S1A) mission="S1A" ;;
            SENTINEL-1B|S1B) mission="S1B" ;;
            SENTINEL-1C|S1C) mission="S1C" ;;
            SENTINEL-1D|S1D) mission="S1D" ;;
            *)
                echo "WARNING: unknown platform in failed_downloads.tsv: $platform"
                continue
                ;;
        esac

        date_compact="${acq_date//-/}"

        # SAFE names store absolute orbit zero-padded, e.g. orbit 12743 -> 012743.
        if [[ "$abs_orbit" =~ ^[0-9]+$ ]]; then
            printf -v orbit6 "%06d" "$abs_orbit"
        else
            orbit6="$abs_orbit"
        fi

        shopt -s nullglob
        matches=(
            "$SITE_DIR"/${mission}_IW_SLC__*_${date_compact}T*_${orbit6}_*.SAFE
        )
        shopt -u nullglob

        if [[ "${#matches[@]}" -eq 0 ]]; then
            echo "No active SAFE found for failed acquisition:"
            echo "  $acq_date $platform orbit=$abs_orbit"
            continue
        fi

        for safe in "${matches[@]}"; do
            moved=1
            base="$(basename "$safe")"
            dest="$FAILED_SAFE_QUARANTINE_DIR/$base"

            if [[ -e "$dest" ]]; then
                stem="${base%.SAFE}"
                n=2
                dest="$FAILED_SAFE_QUARANTINE_DIR/${stem}_duplicate${n}.SAFE"
                while [[ -e "$dest" ]]; do
                    n=$((n + 1))
                    dest="$FAILED_SAFE_QUARANTINE_DIR/${stem}_duplicate${n}.SAFE"
                done
            fi

            echo "Moving SAFE from failed acquisition out of active directory:"
            echo "  $base"
            echo "  reason: $acq_date $platform orbit=$abs_orbit exit=$exit_code"
            echo "  -> $dest"

            if ! mv "$safe" "$dest"; then
                echo "WARNING: could not quarantine failed SAFE:"
                echo "  $safe"
            fi
        done

    done < "$FAILED_DOWNLOADS"

    if [[ "$moved" -eq 1 ]]; then
        echo "Failed/partial SAFEs were preserved in:"
        echo "  $FAILED_SAFE_QUARANTINE_DIR"
    fi

    return 0
}

build_good_acquisition_list() {
    if [[ ! -f "$ORBIT_REPORT" ]]; then
        echo "ERROR: required preflight report does not exist:"
        echo "  $ORBIT_REPORT"
        echo
        echo "Run with START_STEP=2 first."
        exit 1
    fi

    python3 - "$ORBIT_REPORT" "$GOOD_ACQUISITIONS" "$EXCLUDE_S1D" <<'PY'
import csv
import sys
from datetime import datetime

report, output, exclude_s1d_text = sys.argv[1:4]
exclude_s1d = exclude_s1d_text.lower() == "true"

rows = []
seen = set()
excluded = []

with open(report, newline="") as f:
    for row in csv.DictReader(f):
        if row.get("status") != "GOOD":
            continue

        date = (row.get("acquisition_date") or "").strip()
        platform = (row.get("platform") or "").strip().upper()
        orbit = (row.get("absolute_orbit") or "").strip()

        if not date or date == "UNKNOWN":
            continue

        datetime.strptime(date, "%Y-%m-%d")

        normalized_platform = platform.replace("SENTINEL-", "S")
        if exclude_s1d and normalized_platform == "S1D":
            excluded.append((date, platform, orbit))
            continue

        key = (date, platform, orbit)
        if key not in seen:
            seen.add(key)
            rows.append(key)

def orbit_key(value):
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)

rows.sort(key=lambda x: (x[0], x[1], orbit_key(x[2])))

with open(output, "w") as f:
    f.write("# acquisition_date\tplatform\tabsolute_orbit\n")
    for date, platform, orbit in rows:
        f.write(f"{date}\t{platform}\t{orbit}\n")

print(f"Wrote {len(rows)} supported GOOD acquisitions to {output}")

if excluded:
    print(f"Excluded {len(excluded)} S1D acquisition(s):")
    for date, platform, orbit in excluded:
        print(f"  {date} {platform} orbit={orbit}")
PY

    N_ACQUISITIONS=$(
        awk 'NF >= 3 && $1 !~ /^#/ {n++} END {print n+0}' "$GOOD_ACQUISITIONS"
    )

    if [[ "$N_ACQUISITIONS" -eq 0 ]]; then
        echo
        echo "ERROR: no supported GOOD Sentinel-1 acquisitions were found."
        exit 1
    fi
}

###############################################
############### WORKFLOW HEADER ###############
###############################################

echo
echo "============================================================"
echo "              SWEETS LANDFILL WORKFLOW"
echo "============================================================"
echo "Site          : $SITE"
echo "BBox          : $WEST $SOUTH $EAST $NORTH"
echo "Dates         : $START_DATE -> $END_DATE"
echo "Track         : $TRACK"
echo "Polarization  : $POL"
echo "Mode          : $MODE"
echo "Swath         : ${SWATH:-AUTO (all swaths)}"
echo "Start step    : $START_STEP"
echo "Exclude S1D   : $EXCLUDE_S1D"
echo "Project dir   : $SITE_DIR"
echo
echo "STEP DEFINITIONS"
echo "  1 = Create SWEETS config"
echo "  2 = Preflight / check bad acquisitions"
echo "  3 = Download bursts + reconstruct SAFEs"
echo "  4 = Validate reconstructed SAFEs"
echo "  5 = Download EOF orbit files"
echo "  6 = Run SWEETS from SWEETS step 2"
echo
case "$START_STEP" in
    1) START_STEP_NAME="Create SWEETS config" ;;
    2) START_STEP_NAME="Preflight / check bad acquisitions" ;;
    3) START_STEP_NAME="Download bursts + reconstruct SAFEs" ;;
    4) START_STEP_NAME="Validate reconstructed SAFEs" ;;
    5) START_STEP_NAME="Download EOF orbit files" ;;
    6) START_STEP_NAME="Run SWEETS from SWEETS step 2" ;;
esac
echo "Starting workflow from STEP $START_STEP:"
echo "  $START_STEP_NAME"
echo "============================================================"

# Do this even when resuming from later steps, because unsupported S1D or
# known partial SAFEs can make validation/EOF/downstream processing fail.
quarantine_s1d_safes
quarantine_failed_safes

###############################################
########### 1. CREATE SWEETS CONFIG ###########
###############################################

if step_enabled 1; then
    echo
    echo "=== [1/6] Creating SWEETS config ==="

    $PX sweets config \
        --bbox "$WEST" "$SOUTH" "$EAST" "$NORTH" \
        --start "$START_DATE" \
        --end "$END_DATE" \
        --track "$TRACK" \
        --out-dir "$SITE_DIR" \
        --work-dir "$WORK_DIR" \
        --output "$CONFIG_FILE"

    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "ERROR: config was not created: $CONFIG_FILE"
        exit 1
    fi
else
    echo
    echo "=== [1/6] SKIP: config creation ==="
    if [[ ! -f "$CONFIG_FILE" && "$START_STEP" -ge 6 ]]; then
        echo "ERROR: existing config required for START_STEP=$START_STEP:"
        echo "  $CONFIG_FILE"
        exit 1
    fi
fi

###############################################
###### 2. CHECK ALL ABSOLUTE ORBITS FIRST #####
###############################################

CHECK_STATUS=0

if step_enabled 2; then
    echo
    echo "=== [2/6] Preflight checking search + consecutive burst IDs ==="

    set +e

    $PX python "$CHECK_SCRIPT" \
        --start "$START_DATE" \
        --end "$END_DATE" \
        --track "$TRACK" \
        --bbox "$WEST" "$SOUTH" "$EAST" "$NORTH" \
        --pol "$POL" \
        --mode "$MODE" \
        --min-bursts "$MIN_BURSTS" \
        "${CHECK_SWATH_ARGS[@]}" \
        --output-dir "$PREFLIGHT_DIR" \
        2>&1 | tee "$CHECK_LOG"

    CHECK_STATUS=${PIPESTATUS[0]}
    set -e

    # 0 = all good
    # 10 = checker succeeded but one or more BAD acquisitions were found
    if [[ "$CHECK_STATUS" -ne 0 && "$CHECK_STATUS" -ne 10 ]]; then
        echo
        echo "ERROR: orbit preflight failed with exit code $CHECK_STATUS"
        echo "See: $CHECK_LOG"
        exit "$CHECK_STATUS"
    fi
else
    echo
    echo "=== [2/6] SKIP: preflight ==="
fi

# Step 3 always needs a current/reusable GOOD acquisition list.
if [[ "$START_STEP" -le 3 ]]; then
    build_good_acquisition_list

    echo
    echo "Supported GOOD acquisitions: $N_ACQUISITIONS"

    if [[ "$CHECK_STATUS" -eq 10 && -f "$BAD_ORBITS" ]]; then
        echo
        echo "Bad acquisitions found and excluded:"
        grep -v '^[[:space:]]*#' "$BAD_ORBITS" || true
    fi
fi

###############################################
###### 3. BURST2STACK ONE ACQUISITION AT A TIME
###############################################

if step_enabled 3; then
    echo
    echo "=== [3/6] Reconstructing SAFEs one acquisition at a time ==="

    : > "$BURST_LOG"
    printf "# acquisition_date\tplatform\tabsolute_orbit\texit_code\n" > "$FAILED_DOWNLOADS"

    ACQ_INDEX=0
    SUCCESS_COUNT=0
    FAIL_COUNT=0

    while IFS=$'\t' read -r ACQ_DATE PLATFORM ABS_ORBIT; do

        [[ -z "${ACQ_DATE:-}" ]] && continue
        [[ "$ACQ_DATE" == \#* ]] && continue

        # Extra guard in case the list was edited manually.
        if [[ "$EXCLUDE_S1D" == true && ( "$PLATFORM" == "S1D" || "$PLATFORM" == "SENTINEL-1D" ) ]]; then
            echo "SKIP unsupported S1D acquisition: $ACQ_DATE orbit=$ABS_ORBIT"
            continue
        fi

        ACQ_INDEX=$((ACQ_INDEX + 1))

        # Current burst2stack date selection uses an exclusive upper bound.
        NEXT_DATE=$(
            python3 - "$ACQ_DATE" <<'PY'
from datetime import datetime, timedelta
import sys

d = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
print((d + timedelta(days=1)).isoformat())
PY
        )

        echo
        echo "------------------------------------------------------------"
        echo "Acquisition $ACQ_INDEX / $N_ACQUISITIONS"
        echo "Date           : $ACQ_DATE"
        echo "Platform       : $PLATFORM"
        echo "Absolute orbit : $ABS_ORBIT"
        echo "Swath          : ${SWATH:-AUTO (all swaths)}"
        echo "------------------------------------------------------------"

        ATTEMPT=1
        ACQ_OK=0
        BURST_STATUS=1

        while [[ "$ATTEMPT" -le "$BURST_MAX_RETRIES" ]]; do
            echo
            echo "burst2stack attempt $ATTEMPT / $BURST_MAX_RETRIES"
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] date=$ACQ_DATE platform=$PLATFORM orbit=$ABS_ORBIT attempt=$ATTEMPT" \
                >> "$BURST_LOG"

            set +e

            $PX burst2stack \
                --rel-orbit "$TRACK" \
                --start-date "$ACQ_DATE" \
                --end-date "$NEXT_DATE" \
                --extent "$WEST" "$SOUTH" "$EAST" "$NORTH" \
                --pols "$POL" \
                "${BURST_SWATH_ARGS[@]}" \
                --all-anns \
                --output-dir "$SITE_DIR" \
                2>&1 | tee -a "$BURST_LOG"

            BURST_STATUS=${PIPESTATUS[0]}
            set -e

            if [[ "$BURST_STATUS" -eq 0 ]]; then
                ACQ_OK=1
                SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
                echo
                echo "SUCCESS: $ACQ_DATE $PLATFORM orbit=$ABS_ORBIT"
                break
            fi

            if [[ "$ATTEMPT" -lt "$BURST_MAX_RETRIES" ]]; then
                echo
                echo "WARNING: acquisition failed with exit code $BURST_STATUS."
                echo "Retrying in ${BURST_RETRY_DELAY} seconds..."
                sleep "$BURST_RETRY_DELAY"
            fi

            ATTEMPT=$((ATTEMPT + 1))
        done

        if [[ "$ACQ_OK" -ne 1 ]]; then
            FAIL_COUNT=$((FAIL_COUNT + 1))

            printf "%s\t%s\t%s\t%s\n" \
                "$ACQ_DATE" "$PLATFORM" "$ABS_ORBIT" "$BURST_STATUS" \
                >> "$FAILED_DOWNLOADS"

            echo
            echo "WARNING: giving up on this acquisition after $BURST_MAX_RETRIES attempts."
            echo "Recorded in: $FAILED_DOWNLOADS"
            echo "Continuing with the next acquisition..."
        fi

    done < "$GOOD_ACQUISITIONS"

    echo
    echo "============================================================"
    echo "SAFE reconstruction summary"
    echo "============================================================"
    echo "Successful acquisitions : $SUCCESS_COUNT"
    echo "Failed acquisitions     : $FAIL_COUNT"
    echo "Failure list            : $FAILED_DOWNLOADS"
    echo "============================================================"

    if [[ "$SUCCESS_COUNT" -eq 0 ]]; then
        echo "ERROR: no acquisitions were reconstructed successfully."
        exit 1
    fi

    if [[ "$FAIL_COUNT" -gt 0 ]]; then
        echo
        echo "WARNING: some acquisitions could not be downloaded/reconstructed."
        echo "The workflow will continue with successfully created SAFEs."
        cat "$FAILED_DOWNLOADS"
    fi

    # Move unsupported S1D products and any partial SAFEs left by failed
    # acquisitions out of the active processing directory.
    quarantine_s1d_safes
    quarantine_failed_safes
else
    echo
    echo "=== [3/6] SKIP: SAFE reconstruction ==="
fi

###############################################
####### 4. VALIDATE RECONSTRUCTED SAFES #######
###############################################

if step_enabled 4; then
    echo
    echo "=== [4/6] Validating reconstructed SAFE calibration XML ==="

    # Remove known unsupported/partial products first so the calibration
    # validator only evaluates active candidate SAFEs.
    quarantine_s1d_safes
    quarantine_failed_safes

    set +e

    $PX python "$CALIBRATION_CHECK_SCRIPT" \
        "$SITE_DIR" \
        --move-bad-to "$BAD_CALIBRATION_QUARANTINE_DIR" \
        2>&1 | tee "$CALIBRATION_CHECK_LOG"

    CALIBRATION_STATUS=${PIPESTATUS[0]}
    set -e

    # 0  = all SAFEs passed
    # 10 = checker completed and moved one or more BAD SAFEs to quarantine
    if [[ "$CALIBRATION_STATUS" -ne 0 && "$CALIBRATION_STATUS" -ne 10 ]]; then
        echo
        echo "ERROR: SAFE calibration validation failed with exit code $CALIBRATION_STATUS"
        echo "See:"
        echo "  $CALIBRATION_CHECK_LOG"
        exit "$CALIBRATION_STATUS"
    fi

    if [[ "$CALIBRATION_STATUS" -eq 10 ]]; then
        echo
        echo "WARNING: one or more BAD calibration SAFEs were found."
        echo "They were preserved outside the active site directory:"
        echo "  $BAD_CALIBRATION_QUARANTINE_DIR"
        echo "Continuing with the remaining GOOD SAFEs."
    fi
else
    echo
    echo "=== [4/6] SKIP: SAFE calibration validation ==="
fi

###############################################
############ 5. DOWNLOAD EOF ##################
###############################################

if step_enabled 5; then
    echo
    echo "=== [5/6] Downloading precise EOF files ==="

    # Keep unsupported/known-bad products outside the active project directory.
    quarantine_s1d_safes
    quarantine_failed_safes

    set +e

    $PX eof \
        --search-path "$SITE_DIR" \
        --save-dir "$ORBIT_DIR" \
        --orbit-type precise \
        --force-asf \
        --max-workers "$EOF_WORKERS" \
        2>&1 | tee "$EOF_LOG"

    EOF_STATUS=${PIPESTATUS[0]}
    set -e

    if [[ "$EOF_STATUS" -ne 0 ]]; then
        echo
        echo "ERROR: EOF download failed. See:"
        echo "  $EOF_LOG"
        exit "$EOF_STATUS"
    fi
else
    echo
    echo "=== [5/6] SKIP: EOF download ==="
fi

###############################################
########## 6. SWEETS FROM STEP 2 ##############
###############################################

if step_enabled 6; then
    echo
    echo "=== [6/6] Running SWEETS from SWEETS step 2 ==="

    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "ERROR: SWEETS config does not exist:"
        echo "  $CONFIG_FILE"
        echo "Run with starting step 1 first."
        exit 1
    fi

    # Final safety cleanup before COMPASS scans every active SAFE directory.
    quarantine_s1d_safes
    quarantine_failed_safes

    set +e

    $PX sweets run \
        "$CONFIG_FILE" \
        --starting-step 2 \
        2>&1 | tee "$SWEETS_LOG"

    SWEETS_STATUS=${PIPESTATUS[0]}
    set -e

    if [[ "$SWEETS_STATUS" -ne 0 ]]; then
        echo
        echo "ERROR: SWEETS failed. See:"
        echo "  $SWEETS_LOG"
        exit "$SWEETS_STATUS"
    fi
else
    echo
    echo "=== [6/6] SKIP: SWEETS processing ==="
fi

###############################################
################### DONE ######################
###############################################

echo
echo "============================================================"
echo "WORKFLOW FINISHED"
echo "============================================================"
echo "Start step : $START_STEP"
echo "Preflight  : $PREFLIGHT_DIR"
echo "GSLCs      : $WORK_DIR/gslcs"
echo "Dolphin    : $WORK_DIR/dolphin"
echo "Logs       : $LOG_DIR"

if [[ "$EXCLUDE_S1D" == true ]]; then
    echo "S1D archive : $S1D_QUARANTINE_DIR"
fi
echo "Failed SAFE archive : $FAILED_SAFE_QUARANTINE_DIR"
echo "Bad calibration archive : $BAD_CALIBRATION_QUARANTINE_DIR"
echo "Calibration check log    : $CALIBRATION_CHECK_LOG"

echo
echo "Bowser:"
echo "  cd \"$SITE_DIR\""
echo "  bowser setup-dolphin work/dolphin"
echo "  bowser run"
