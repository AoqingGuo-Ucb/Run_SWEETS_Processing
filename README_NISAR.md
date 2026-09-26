# NISAR GSLC Workflow

`run_sweets_nisar.sh` is a separate entry point that leaves `run_sweets.sh` and the Sentinel-1 shared-cache scripts unchanged. It uses the SWEETS `nisar-gslc` source to download already geocoded NISAR GSLC products and then run Dolphin. It does not process NISAR raw data or RSLC products.

## Differences from the Sentinel-1 workflow

- Skips burst2stack, SAFE checks, S1D exclusion, Sentinel-1 EOF downloads, and COMPASS.
- Uses NISAR track and frame identifiers. Do not reuse a Sentinel-1 relative orbit number.
- Defaults to frequency group A and HH polarization. Confirm these against the available products. Some SWEETS versions may select another available frequency or polarization when the requested combination is unavailable; check the download logs for the actual selection.
- Defaults to strides `(1, 1)`, meaning no additional decimation of the input grid. Sentinel-1 strides `(2, 4)` do not necessarily produce the same ground spacing with NISAR inputs.
- Writes to a separate `NISAR_Projects` root without mixing existing SAFE data or Sentinel-1 GSLC caches.

See the SWEETS implementations for [CLI options](https://github.com/isce-framework/sweets/blob/main/src/sweets/cli.py) and [NISAR downloads](https://github.com/isce-framework/sweets/blob/main/src/sweets/download.py). Actual product releases determine GSLC coverage and available dates. The dates in the script are examples, not a guarantee of coverage.

## Requirements

Use Ubuntu with Bash, Python 3, `flock`, and a working Pixi/SWEETS environment. Your SWEETS installation must support `nisar-gslc`, `NisarGslcSearch`, and `opera_utils.nisar`. Earthdata/ASF download authentication must also be configured.

The script checks these interfaces and stops if they are unavailable. It does not upgrade your environment or fall back to Sentinel-1 processing. Copy `run_sweets_nisar.sh` and `site_nisar.example.sh` to your Ubuntu script directory.

## Configure a study area

From the script directory, run:

```bash
cp site_nisar.example.sh site_nisar.sh
nano site_nisar.sh
```

Set `SITE`, WEST/SOUTH/EAST/NORTH, START_DATE/END_DATE, SWEETS_REPO, and PROJECT_ROOT. Use absolute paths. Leaving `NISAR_TRACK` and `NISAR_FRAME` empty allows an unpinned search. For production time-series processing, identify the appropriate NISAR track/frame and set them explicitly. Do not copy Sentinel-1 track 71 into these fields without checking the NISAR identifiers.

Bash executes the settings file using `source`, so use only a trusted file.

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
