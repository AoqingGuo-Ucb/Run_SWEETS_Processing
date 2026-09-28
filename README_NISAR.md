# NISAR GSLC Workflow

`run_sweets_nisar.sh` is a separate entry point that leaves `run_sweets.sh` and the Sentinel-1 shared-cache scripts unchanged. It uses the SWEETS `nisar-gslc` source to download already geocoded NISAR GSLC products and then run Dolphin. It does not process NISAR raw data or RSLC products.

## Differences from the Sentinel-1 workflow

- Skips burst2stack, SAFE checks, S1D exclusion, Sentinel-1 EOF downloads, and COMPASS.
- Uses NISAR track and frame identifiers. Do not reuse a Sentinel-1 relative orbit number.
- Defaults to the `NISAR_L2_GSLC_PROVISIONAL_V1` collection for new configurations.
- Defaults to frequency group A and HH polarization. Confirm these against the available products. Some SWEETS versions may select another available frequency or polarization when the requested combination is unavailable; check the download logs for the actual selection.
- Defaults to strides `(1, 1)`, meaning no additional decimation of the input grid. Sentinel-1 strides `(2, 4)` do not necessarily produce the same ground spacing with NISAR inputs.
- Writes to a separate `NISAR_Projects` root without mixing existing SAFE data or Sentinel-1 GSLC caches.

See the SWEETS implementations for [CLI options](https://github.com/isce-framework/sweets/blob/main/src/sweets/cli.py) and [NISAR downloads](https://github.com/isce-framework/sweets/blob/main/src/sweets/download.py). Actual product releases determine GSLC coverage and available dates. The dates in the script are examples, not a guarantee of coverage.

## Requirements

Use Ubuntu with Bash, Python 3, `flock`, and a working Pixi/SWEETS environment. Your SWEETS installation must support `nisar-gslc`, `NisarGslcSearch`, and `opera_utils.nisar`. Earthdata/ASF download authentication must also be configured.

The script checks these interfaces and stops if they are unavailable. It does not upgrade your environment or fall back to Sentinel-1 processing. Copy the shell script and settings to your Ubuntu script directory. Place `run_nisar_checked.py` in `SWEETS_REPO` (alongside your other SWEETS helpers), or beside the shell script as a fallback. The default lookup checks `SWEETS_REPO` first.

To keep the helper anywhere else, set an absolute path in your site settings:

```bash
NISAR_CHECK_SCRIPT="/absolute/path/to/run_nisar_checked.py"
```

An explicit path takes precedence and must exist; a typo stops the run instead of silently falling back. The selected path is printed before processing. Configuration-only mode does not require the helper.

## Duplicate acquisition dates

The runner checks existing NISAR VRT inputs and checks the actual input list again immediately before Dolphin starts, including after a fresh download. More than one input on the same calendar day stops processing and reports all conflicting paths in the run log. HDF5 companions are not counted separately. Different processing versions, polarizations, or frames on the same day must be resolved before using this single-stack workflow. Unrecognized filenames also stop processing rather than bypassing the check.

This check does not select a preferred version, move files, or delete data. Review product metadata and keep a compatible, consistent time series. Move excluded HDF5/VRT pairs outside `data`. If Dolphin has already run on the old input list, back up `work/dolphin` outside its original path before restarting with mode `3`; keep `data`, the DEM, and the water mask. The check prevents duplicate-date inputs but does not guarantee other aspects of processing compatibility.

The helper wraps the installed `Workflow._run_dolphin` method only within the current process, then runs the normal SWEETS CLI. It does not edit the SWEETS installation. Changes to that upstream interface may require an adapter update.

## Configure a study area

From the script directory, run:

```bash
cp site_nisar.example.sh site_nisar.sh
nano site_nisar.sh
```

Set `SITE`, WEST/SOUTH/EAST/NORTH, START_DATE/END_DATE, SWEETS_REPO, and PROJECT_ROOT. Use absolute paths. Leaving `NISAR_TRACK` and `NISAR_FRAME` empty allows an unpinned search. For production time-series processing, identify the appropriate NISAR track/frame and set them explicitly. Do not copy Sentinel-1 track 71 into these fields without checking the NISAR identifiers.

Bash executes the settings file using `source`, so use only a trusted file.

## Select Provisional or Beta products

Set the collection in your site settings file:

```bash
NISAR_COLLECTION="NISAR_L2_GSLC_PROVISIONAL_V1"
```

For early Beta products, use `NISAR_L2_GSLC_BETA_V1` instead. The wrapper writes this selection to `search.short_name` in the generated YAML through the SWEETS model API, because the flat configuration CLI does not expose this setting. No manual YAML edit is needed. Check the printed `CMR short_name` before interpreting search results.

To switch an existing Beta project to Provisional, choose a new `SITE` in your settings file, set `NISAR_COLLECTION`, verify your NISAR track/frame and dates, and run mode `1`. Alternatively, run mode `config` to review the new configuration before running mode `2`. Existing Beta files and results remain in their original project.

Modes `2` and `3` preserve the existing YAML, including its collection. A mismatch between the shell collection and YAML produces a warning; it does not switch collections or re-download data. This also applies when resuming a manually configured YAML.

Both collections contain GSLC products. Beta products are not fully calibrated; Provisional products are calibrated and partially validated. Provisional coverage generally starts on June 17, 2026, with selected earlier time series being added. Availability depends on the location and acquisition. Processing differences can affect comparisons between maturities, so process them in separate projects unless you have assessed compatibility. See the [ASF availability overview](https://nisar-docs.asf.alaska.edu/availability-overview/) and [Provisional known issues](https://nisar-docs.asf.alaska.edu/provisional-known-issues/).

## Review the generated configuration first

```bash
bash run_sweets_nisar.sh config "$PWD/site_nisar.sh"
```

This creates `<PROJECT_ROOT>/<SITE>/sweets_config.yaml` without downloading or processing observation data. After reviewing the configuration, run:

```bash
bash run_sweets_nisar.sh 2 "$PWD/site_nisar.sh"
```

If a configuration has not yet been created, you can run the complete workflow directly:

```bash
bash run_sweets_nisar.sh 1 "$PWD/site_nisar.sh"
```

| Mode | Behavior |
| --- | --- |
| `1` | Create a new configuration, then run SWEETS from internal step 1 |
| `config` | Create a new configuration only |
| `2` | Use the existing configuration and run download, preparation, and processing from SWEETS internal step 1 |
| `3` | Use the existing configuration and complete downloaded inputs to run Dolphin from SWEETS internal step 3 |
| `--help` | Show usage |

These modes differ from the Sentinel-1 script's six stages. Modes `1` and `config` do not overwrite an existing configuration; use mode `2` or `3` to resume. The existing YAML is authoritative when resuming: changing the bounding box or dates in the shell settings does not update it. Use a new SITE/project directory when changing parameters or extending the time range.

Mode `3` checks for existing VRT inputs and auxiliary files, but does not guarantee enough compatible observations for a time series. SWEETS may skip downloads when it detects existing data, so mode `2` is not a missing-product repair tool either. If a previous download was incomplete, inspect its logs and files rather than assuming a resume command guarantees completeness.

## Outputs

```text
NISAR_Projects/<SITE>/
├── sweets_config.yaml
├── data/                  # NISAR GSLC HDF5 files and SWEETS-generated VRTs
├── work/
│   ├── dem.tif
│   ├── watermask.tif
│   └── dolphin/
└── logs/
    ├── config.log
    └── nisar_<timestamp>_<pid>.log
```

Keep VRTs together with their referenced HDF5 files, and check their references before relocating them. This script does not implement a shared NISAR cache across study areas. The Sentinel-1 SAFE/COMPASS GSLC cache cannot be used as a direct substitute.

## Validation scope

The script has passed Bash syntax checks and offline tests covering argument routing, configuration creation, resume modes, rejection of unsupported versions, failure exit codes, and protection of existing configurations. These tests replace Pixi/SWEETS commands with test doubles; they do not validate actual NISAR downloads, Dolphin outputs, or complete compatibility with your installed environment.

```bash
python3 -m unittest discover -s tests -v
bash -n run_sweets_nisar.sh
```
