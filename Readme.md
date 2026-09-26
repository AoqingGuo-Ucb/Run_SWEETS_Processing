# SWEETS Processing Workflow

[`run_sweets.sh`](run_sweets.sh) automates a Sentinel-1 burst-based InSAR workflow using SWEETS, burst2safe, COMPASS, and Dolphin. It downloads bursts selected for a study area, reconstructs SAFE products, checks acquisitions and calibration metadata, prepares orbit files, and starts SWEETS processing.

The script supports restarting from six numbered stages, retries individual acquisition downloads, and preserves problematic SAFE products in separate quarantine directories.

## NISAR data

For NISAR GSLC inputs, use the separate [`run_sweets_nisar.sh`](run_sweets_nisar.sh) entry point and follow the [NISAR setup and usage guide](README_NISAR.md). It skips the Sentinel-1 SAFE/EOF/COMPASS stages and uses a separate output directory. Its mode numbers differ from the six Sentinel-1 stages below.

## Requirements

Use an existing, working SWEETS Pixi environment with:

- SWEETS and its COMPASS, ISCE3, s1reader, and Dolphin dependencies.
- `burst2stack` from burst2safe.
- The `eof` command for Sentinel-1 orbit downloads.
- Earthdata/ASF credentials configured for downloading data.
- Bash, Python 3, and Pixi available on the processing machine.

Place these two helper scripts in the directory configured as `SWEETS_REPO`:

```text
<SWEETS_REPO>/
├── check_bad_absorbits.py
├── check_bad_calibration_safes.py
└── ... existing SWEETS installation ...
```

The helpers are required by the workflow but are not included in this repository. In the original workspace they are provided in the sibling directories `Check_BadAbsorbits_for_Sweets` and `Check_BadCalibration_Safes_for_Sweets`.

Check the installed commands from your SWEETS directory:

```bash
pixi run sweets --help
pixi run burst2stack --help
pixi run eof --help
```

## Configuration

Edit the user settings near the top of `run_sweets.sh` before running:

```bash
SITE="Prima"

# Bounding box: west, south, east, north (longitude/latitude).
WEST=-117.646
SOUTH=33.4704
EAST=-117.5842
NORTH=33.5084

START_DATE="2016-01-01"
END_DATE="2026-07-01"
TRACK=71

# Output strides in (y, x) order.
SWEETS_STRIDES=(2 4)

SWEETS_REPO="$HOME/Bhaltos/AoqingShare/sfw/sweets"
PROJECT_ROOT="$HOME/Bhaltos/AoqingShare/CA_Landfill"
```

The paths above are the current example settings. Set them for your installation and study area. The `CA_Landfill` data-directory name is retained so existing projects continue to use the same location; it does not restrict the workflow to landfills.

Additional settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_START_STEP` | `1` | First stage when no argument is supplied |
| `EXCLUDE_S1D` | `true` | Exclude S1D for the orbit-client limitation documented in the script |
| `POL` | `VV` | Burst polarization |
| `MODE` | `IW` | Preflight acquisition mode |
| `SWATH` | Empty | Use all intersecting swaths; set e.g. `IW2` to restrict |
| `BURST_MAX_RETRIES` | `4` | Maximum download attempts per acquisition |
| `BURST_RETRY_DELAY` | `30` | Seconds between attempts |
| `EOF_WORKERS` | `8` | Orbit-download workers |

Stage 1 passes `--dolphin.strides 2 4` to `sweets config`. Changing `SWEETS_STRIDES` applies when creating a configuration; it does not update an existing YAML when resuming from later stages.

## Usage

Run the complete workflow:

```bash
bash run_sweets.sh
```

Resume from a selected stage:

```bash
bash run_sweets.sh 4
```

Alternatively, make the script executable:

```bash
chmod +x run_sweets.sh
./run_sweets.sh 4
```

The script accepts a starting-stage number from **1 to 6**. It does not implement `--help` or `-h`; use the stage table below. A starting-stage argument runs that stage and all subsequent stages, not just one stage.

## Processing stages

| Stage | Action | Main outputs |
| --- | --- | --- |
| 1 | Create SWEETS configuration | `sweets_config.yaml` |
| 2 | Preflight acquisition search and consecutive burst IDs | `burst_preflight/orbit_check.csv` and acquisition lists |
| 3 | Reconstruct GOOD acquisitions one at a time with retries | Site SAFE directories and download logs |
| 4 | Check reconstructed SAFE calibration XML | Calibration report; bad SAFEs moved to quarantine |
| 5 | Download precise EOF orbit files | `<SWEETS_REPO>/orbits/` |
| 6 | Prepare DEM and water mask, then run SWEETS from its internal step 2 | `work/dem.tif`, `work/watermask.tif`, GSLC and Dolphin outputs |

The shell script's stage numbers and SWEETS' internal step numbers are different. For example:

```bash
bash run_sweets.sh 6
```

skips shell stages 1–5, prepares the auxiliary files, and then executes:

```bash
pixi run sweets run /path/to/sweets_config.yaml --starting-step 2
```

Resuming requires valid outputs from the skipped stages. Stage 3 needs the preflight report; stage 6 needs an existing configuration and usable SAFE inputs.

### Preflight and download failures

Preflight uses `find_group` and `Safe.check_group_validity` to identify invalid acquisitions before downloading burst imagery. Only GOOD, supported acquisitions are selected for reconstruction.

Stage 3 retries each acquisition independently. Failed downloads are recorded in `logs/failed_downloads.tsv`; the workflow continues with successful acquisitions. If none succeed, it stops.

### SAFE isolation

Problematic products are preserved outside the active site directory:

```text
<PROJECT_ROOT>/
├── <SITE>_excluded_S1D/
├── <SITE>_failed_SAFEs/
└── <SITE>_bad_calibration_SAFEs/
```

Calibration checks detect missing or inconsistent betaNought vectors. They do not guarantee that every downstream processing operation will succeed. Unsupported and known failed products are also checked when resuming.

## Output layout

```text
<PROJECT_ROOT>/<SITE>/
├── sweets_config.yaml
├── S1A*.SAFE/
├── S1B*.SAFE/
├── S1C*.SAFE/
├── burst_preflight/
│   ├── orbit_check.csv
│   ├── bad_absorbits.txt
│   ├── good_date_ranges.txt
│   └── good_acquisitions.tsv
├── logs/
│   ├── check_bad_absorbits.log
│   ├── check_bad_calibration_safes.log
│   ├── burst2stack_per_acquisition.log
│   ├── failed_downloads.tsv
│   ├── eof.log
│   └── sweets_step2.log
└── work/
    ├── dem.tif
    ├── watermask.tif
    ├── gslcs/
    └── dolphin/
```

The precise Dolphin output layout depends on your installed version and configuration. Orbit files are stored separately under `<SWEETS_REPO>/orbits/`.

## Restarting and troubleshooting

Inspect the log for the failed stage before restarting. For example, from the site directory:

```bash
tail -n 150 logs/sweets_step2.log
```

| Error | What to inspect |
| --- | --- |
| `All bursts must have consecutive burst IDs` | Preflight report and selected swaths/acquisition |
| `KeyError: 'S1D'` | Orbit-client support and `EXCLUDE_S1D` |
| `setting an array element with a sequence` in calibration reading | Calibration log and the affected SAFE XML |
| `burst iw1-slc-vv not in SAFE` | Required swath/polarization and incomplete SAFE products |
| `BrokenProcessPool` | Earlier log messages and system logs for memory kills, GPU errors, or native crashes |
| `Unrecognized options: --strides` | Use `--dolphin.strides`, as this script does |

To run the calibration checker manually from the SWEETS directory:

```bash
pixi run python check_bad_calibration_safes.py /path/to/site
```

For a process that exited abruptly on Ubuntu, inspect recent kernel messages:

```bash
sudo journalctl -k --since "30 minutes ago" --no-pager \
  | grep -Ei 'out of memory|oom|killed process|segfault|nvrm|xid'
```

Correct the identified problem, then resume from the appropriate shell stage. Do not restart from stage 1 unless you intend to recreate the configuration and repeat the earlier stages.

Unwrapping behavior is controlled by the installed SWEETS/Dolphin configuration, not a dedicated switch in this wrapper. Consult `pixi run sweets config --help` for the options supported by your installed version before changing those settings.

## Reuse and scope

Restarting at a later stage skips earlier download/reconstruction work. This script does **not** implement a shared SAFE cache across different study areas. The separate `Run_Sweets_Shared_Cache` workflow in the original workspace provides that functionality.

## Reproducibility and citation

Keep the script, generated configuration, logs, Pixi lock file, software versions, acquisition dates, and orbit-product information with the results. Cite the Sentinel-1 data and the software packages used in your analysis.
