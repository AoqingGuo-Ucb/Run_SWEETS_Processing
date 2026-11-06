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

The runner uses dates with exactly one VRT input to infer the normal processing version (for example, `P05023`). If one version occurs on more than half of these dates, duplicate dates keep that version automatically. Dates with only one input remain unchanged, even when their version differs from the majority. This can leave mixed processing versions in the resulting time series; it is a duplicate-removal rule, not a scientific compatibility assessment. Every duplicate group must differ only in the processing-version field and contain exactly one matching input. No strict majority (including ties), no reference dates, different observation identifiers, and missing pairs stop the run without moving files.

Excluded HDF5/VRT pairs are moved together to a unique `nisar_duplicate_backup_*` directory beside `data`, with a JSON manifest. Existing `work/dolphin` results are backed up there too so stale results are not reused. Inputs for non-duplicate dates remain unchanged. Nothing is permanently deleted. This matches the existing time series; it does not establish that a version has better scientific quality.

Existing duplicates are resolved before processing starts. If duplicates are found only after a fresh download, they are backed up and the run stops; resume with mode `3` to rebuild from the corrected list. Copy the updated `run_nisar_checked.py` to the helper location printed by the shell script. HDF5 companions are not counted as separate observations. The final input list is checked before Dolphin starts.

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

## Experimental pair from Level-1 RSLC (no GSLC download)

Use `run_nisar_rslc.py` for a separate two-date experiment. It searches the
`NISAR_L1_RSLC_PROVISIONAL_V1` collection, downloads two explicitly selected
full-frame RSLC HDF5s, runs the ISCE3 NISAR GSLC processor for each date on
one fixed AOI grid, and uses the SWEETS displacement-config builder and Dolphin
to form a wrapped interferogram. No downloaded Level-2 product is used.
This is geometry-based geocoding with the supplied DEM and embedded orbits,
not image-based residual offset estimation. Grid checks cannot prove subpixel
registration. Compare stable terrain, coherence and the same date pair before
drawing conclusions about a published GSLC.

Copy the Python script into the Ubuntu SWEETS repository. In a Pixi environment
with Earthdata credentials, earthaccess, the ISCE3 **nisar.workflows** package,
GDAL, pyproj, h5py, PyYAML and the NISAR-capable SWEETS/Dolphin versions:

```bash
cd "$HOME/Bhaltos/AoqingShare/sfw/sweets"
area=(--bbox 85.3894 28.1588 85.6635 28.3509 --track 98 --frame 16 --dates 2026-09-12 2026-09-24)
pixi run python run_nisar_rslc.py search "${area[@]}"
```

Select two printed IDs with different dates and the same processing release.
Replace both sample ID placeholders and the DEM path below. Use a DEM with
ellipsoidal heights and sufficient coverage for the ISCE3 processor. The
bounding box limits the output grid, not the raw downloads. Allow space for two
full RSLCs, scratch files and intermediate products.

```bash
pixi run python run_nisar_rslc.py pair "${area[@]}" \
  --ids FIRST_RSLC_ID SECOND_RSLC_ID --frequency A --pol HH \
  --dem /absolute/path/to/ellipsoidal_dem.tif \
  --project "$HOME/Bhaltos/AoqingShare/NISAR_Projects/Langtung_RSLC_pair01"
```

The project must be new. Raw downloads are under `rslc/`, per-date runconfigs
and local geocoded intermediates are in per-granule directories, and Dolphin
outputs are under `dolphin/`. The program prints the wrapped interferogram path.
Local GSLC filenames follow the source acquisition naming convention for
SWEETS compatibility; these are user-generated products, not ASF releases.

### Multiple wrapped interferograms from RSLCs

Use the same Ubuntu environment and AOI settings as the pair example, extending
the date range as needed. From the SWEETS repository:

```bash
area=(--bbox 85.3894 28.1588 85.6635 28.3509 --track 98 --frame 16 --dates 2026-06-17 2026-09-26)
pixi run python run_nisar_rslc.py search "${area[@]}" | grep '^NISAR_L1_PR_RSLC_' > rslc_ids.txt
nano rslc_ids.txt
pixi run python run_nisar_rslc.py stack "${area[@]}" --ids-file rslc_ids.txt \
  --max-bandwidth 2 --frequency A --pol HH --dem /absolute/path/to/ellipsoidal_dem.tif \
  --project "$HOME/Bhaltos/AoqingShare/NISAR_Projects/Langtung_RSLC_stack01"
```

Keep one compatible product per date and a consistent processing release.
Each selected full RSLC is downloaded and geocoded once within the new project.
Dolphin processes the stack together. Bandwidth K connects each acquisition to
the next K acquisitions in time order, regardless of the gap in days. For 10
dates, K=1 requests 9 pairs, K=2 requests 17, and K=9 requests all 45.
The project records requested_pairs.txt and checks for those date pairs in the
stitched outputs. No unwrapping or time-series inversion is requested. Existing
pair projects are not reused by this mode.

### Bash wrapper with a temporal baseline in days

Keep the shell launcher in `Cases/` and place the **updated** `run_nisar_rslc.py` in the SWEETS repository root (`$SWEETS_REPO/run_nisar_rslc.py`). To use a different helper location, set an absolute `RSLC_HELPER` in the site settings.
Edit the shell settings or supply a trusted settings file as argument 2 to
every command. The included AOI and track/frame are Langtung examples.
Set `MAX_TEMPORAL_BASELINE_DAYS=36` for all pairs at most 36 calendar days
apart (inclusive). Leave `MAX_BANDWIDTH=""` for no acquisition-count limit;
if set, both limits must pass. Leave `DEM=""` to have `run` call SWEETS `create_dem` with a 0.25-degree AOI buffer before processing. It checks raster readability and AOI coverage, then reuses the terrain file under `nisar_rslc_dem_cache/<bounds-key>/dem.tif` beside the project directory. Search and plan never download a DEM. For a known ellipsoidal DEM, set `DEM` to its absolute path instead. Horizontal grid checks do not certify the vertical datum; confirm the height convention of your installed SWEETS terrain provider for scientific use.
Use different SITE/IDS_FILE/PROJECT settings for different stacks.

```bash
bash run_nisar_rslc_stack.sh search   # Creates a candidate ID list; does not overwrite an existing list.
# Review the printed file; select one compatible product per date.
bash run_nisar_rslc_stack.sh plan     # Offline preview of exact pairs; no imagery download.
bash run_nisar_rslc_stack.sh run      # New project; downloads selected RSLCs and generates wrapped IFGs.
```

A date separated from every other date by more than the limit has no output
pair, although it is still a stack input. Review the plan and remove any
unneeded IDs before running. The original Python pair and stack commands
remain supported.

The RSLC driver fills required GSLC debug, blocksize, flatten and posting
fields from the installed ISCE3 workflow defaults. The unused frequency's
posting entry does not enable processing of that frequency.

For a failed project, preserve the old directory. Set `RSLC_REUSE_PROJECT`
in the environment to its absolute path, and `PROJECT` to a new directory
(or change the site script's PROJECT setting). The driver reads existing
RSLCs in place if the filename and recorded source URL match, and downloads
the remaining products. Keep the old directory available. The driver does
not reuse partial geocoding outputs.
