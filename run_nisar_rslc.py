"""Pair or stack: ASF Level-1 RSLC -> local ISCE3 geocoding -> SWEETS/Dolphin."""
from datetime import datetime
import argparse
import importlib.util
import os
import inspect
import json
import math
from pathlib import Path
import re
import subprocess
import sys

COLLECTION = "NISAR_L1_RSLC_PROVISIONAL_V1"
PATTERN = re.compile(r"NISAR_L1_PR_RSLC_(\d+)_(\d+)_([AD])_(\d+)_(\w+?)_(\w+?)_(\w+?)_(\d{8})T\d{6}_\d{8}T\d{6}_(\w+)_(\w+)_(\w+)_(\w+)_(\d+)")


def parse_id(name):
    m = PATTERN.fullmatch(name.removesuffix(".h5"))
    if not m:
        raise ValueError(f"Expected a production NISAR L1 RSLC granule ID: {name}")
    return m.groups()


def check_pair(names):
    a, b = map(parse_id, names)
    if a[1:7] != b[1:7] or a[8] != b[8]:
        raise ValueError("Choose matching track, direction, frame, mode and processing release.")
    if a[7] == b[7]:
        raise ValueError("Choose two different acquisition dates.")


def plan_stack(names, bandwidth=None, max_days=None):
    ids = sorted((v.removesuffix('.h5') for v in names), key=lambda v: parse_id(v)[7])
    dates = [parse_id(v)[7] for v in ids]
    if len(ids) < 2 or len(set(dates)) != len(ids):
        raise ValueError("Select at least two acquisitions, one product per date.")
    if bandwidth is not None and bandwidth < 1:
        raise ValueError("Bandwidth must be positive.")
    for other in ids[1:]:
        check_pair([ids[0], other])
    if max_days is not None and max_days < 1:
        raise ValueError("Maximum temporal baseline must be positive.")
    if bandwidth is None:
        bandwidth = len(ids) if max_days is not None else 1
    parsed = {day: datetime.strptime(day, "%Y%m%d").date() for day in dates}
    pairs = [(day, later) for i, day in enumerate(dates)
             for later in dates[i+1:i+1+bandwidth]
             if max_days is None or (parsed[later]-parsed[day]).days <= max_days]
    if not pairs:
        raise ValueError("No date pairs satisfy the selected baseline limits.")
    return ids, pairs


def apply_gslc_defaults(config, defaults):
    """Use the installed ISCE3 release's required values and both posting slots."""
    groups = config['runconfig']['groups']
    base = defaults['runconfig']['groups']
    groups['debug_level_group'] = base['debug_level_group']
    processing = groups['processing']
    for key in ('blocksize', 'flatten'):
        processing.setdefault(key, base['processing'][key])
    geo = processing['geocode']
    geo['output_posting'] = {
        **base['processing']['geocode']['output_posting'], **geo['output_posting']
    }
    return config


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["search", "pair", "stack", "plan"])
    p.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W","S","E","N"))
    p.add_argument("--dates", nargs=2, required=True, metavar=("START","END"))
    p.add_argument("--track", type=int, required=True)
    p.add_argument("--frame", type=int, required=True)
    p.add_argument("--ids", nargs=2, help="Two exact IDs printed by search")
    p.add_argument("--ids-file", type=Path, help="Stack mode: one exact RSLC product ID per line")
    p.add_argument("--max-bandwidth", type=int, help="Optional additional limit: next K acquisitions (default 1 without a day limit)")
    p.add_argument("--max-days", type=int, help="Inclusive maximum separation in calendar days")
    p.add_argument("--dem", type=Path, help="Local ellipsoidal-height DEM for ISCE3")
    p.add_argument("--project", type=Path, help="New, unused output directory")
    p.add_argument("--frequency", choices=["A","B"], default="A")
    p.add_argument("--pol", choices=["HH","HV","VH","VV"], default="HH")
    p.add_argument("--posting", type=float, default=10, help="Output UTM pixel size in metres")
    args = p.parse_args()
    w,s,e,n = args.bbox
    if not (-180 <= w < e <= 180 and -80 <= s < n <= 84 and 0 < args.posting < 1000):
        p.error("Invalid local UTM AOI or posting.")
    if args.action != "search":
        if args.action != "plan" and (not args.project or not args.dem or not args.dem.is_file()):
            p.error("Processing requires --project and an existing --dem")
        if args.action == "pair":
            if not args.ids or args.ids_file or args.max_bandwidth not in (None, 1):
                p.error("pair requires two --ids and uses bandwidth 1")
            names = args.ids
        else:
            if args.ids or not args.ids_file or not args.ids_file.is_file():
                p.error("stack requires --ids-file (one RSLC ID per line)")
            names = [line.strip() for line in args.ids_file.read_text().splitlines()
                     if line.strip() and not line.lstrip().startswith('#')]
        try:
            ids, pairs = plan_stack(names, args.max_bandwidth, args.max_days)
        except ValueError as exc:
            p.error(str(exc))
        print(f"Selected {len(ids)} acquisitions; requested {len(pairs)} wrapped interferograms.", flush=True)
        if args.action == "plan":
            for a,b in pairs:
                print(f"{a}_{b}")
            return
        if args.project.exists():
            p.error("Project already exists. Choose a new directory.")
        if not importlib.util.find_spec("nisar.workflows.gslc"):
            p.error("Install the ISCE3 NISAR GSLC workflow in this environment.")
        import yaml
        from nisar.workflows.helpers import WORKFLOW_SCRIPTS_DIR
        defaults_path = Path(WORKFLOW_SCRIPTS_DIR) / 'defaults' / 'gslc.yaml'
        defaults = yaml.safe_load(defaults_path.read_text())
        import h5py
        import numpy as np
        from pyproj import Transformer
        from osgeo import gdal
        from sweets.download import _nisar_h5_to_vrts
        from sweets._dolphin import DolphinOptions, build_displacement_config
        from dolphin.workflows.displacement import run
        if 'wavelength' not in inspect.signature(build_displacement_config).parameters:
            p.error("Installed SWEETS lacks the NISAR-compatible Dolphin adapter.")
    import earthaccess
    hits = earthaccess.search_data(short_name=COLLECTION, bounding_box=tuple(args.bbox),
                                   temporal=(args.dates[0]+'T00:00:00Z',args.dates[1]+'T23:59:59Z'))
    urls = {}
    for hit in hits:
        for url in hit.data_links():
            name = url.split('?')[0].rsplit('/',1)[-1]
            m = PATTERN.fullmatch(name.removesuffix('.h5'))
            if name.endswith('.h5') and m and int(m[2]) == args.track and int(m[4]) == args.frame:
                urls[name[:-3]] = url
    if args.action == 'search':
        for name in sorted(urls):
            print(name)
        print(f"{len(urls)} RSLC products; no files downloaded.")
        return
    if any(v not in urls for v in ids):
        p.error("Selected IDs not found in the requested RSLC collection, dates, AOI and track/frame.")
    previous = os.environ.get('RSLC_REUSE_PROJECT')
    previous_sources = {}
    if previous:
        previous = Path(previous).expanduser().resolve()
        previous_sources = json.loads((previous/'sources.json').read_text())['sources']
    # Discover prior attempts within this study, plus an explicitly supplied legacy project.
    reuse_projects = list(args.project.resolve().parent.glob('*/sources.json'))
    if previous:
        reuse_projects.insert(0, previous / 'sources.json')
    reusable = {}
    for manifest in reuse_projects:
        try:
            sources = json.loads(manifest.read_text())['sources']
            for name, url in sources.items():
                candidate = manifest.parent / 'rslc' / (name + '.h5')
                if candidate.is_file() and urls.get(name) == url:
                    reusable.setdefault(name, candidate)
        except (OSError, ValueError, KeyError):
            continue
    earthaccess.login()
    root = args.project.resolve()
    root.mkdir(parents=True, exist_ok=False)
    raw = root/'rslc'
    raw.mkdir()
    (root/'requested_pairs.txt').write_text(''.join(f'{a}_{b}\n' for a,b in pairs))
    epsg = (32600 if (s+n)/2 >= 0 else 32700) + min(60, int(((w+e)/2+180)//6)+1)
    transform = Transformer.from_crs(4326, epsg, always_xy=True)
    left,bottom,right,top = transform.transform_bounds(w,s,e,n,densify_pts=21)
    d = args.posting
    left,bottom,right,top = (math.floor(left/d)*d, math.floor(bottom/d)*d,
                             math.ceil(right/d)*d, math.ceil(top/d)*d)
    freq, pol = args.frequency,args.pol
    (root/'sources.json').write_text(json.dumps(dict(collection=COLLECTION, sources={v:urls[v] for v in ids},
        dem=str(args.dem.resolve()), bbox=args.bbox, epsg=epsg, posting=d), indent=2))
    vrts, centers = [], []
    expected_grid = None
    common_valid = None
    for name in ids:
        src = raw/(name+'.h5')
        cached = reusable.get(name)
        if cached:
            # Check that the required complex dataset is readable before reuse.
            with h5py.File(cached) as cached_hf:
                cached_ds = cached_hf[f'/science/LSAR/RSLC/swaths/frequency{freq}/{pol}']
                cached_ds[0:1, 0:1]
                cached_ds[-1:, -1:]
            src = cached
            print(f'Reusing RSLC: {src}', flush=True)
        else:
            files = earthaccess.download([urls[name]], str(raw))
            if not files or not src.is_file():
                raise RuntimeError(f"Download incomplete: {src}")
        with h5py.File(src) as hf:
            band = hf[f'/science/LSAR/RSLC/swaths/frequency{freq}']
            if pol not in band:
                raise ValueError(f'{src.name}: requested {freq}/{pol} not available')
            centers.append(float(band['processedCenterFrequency'][()]))
        if not np.isclose(centers[-1],centers[0],rtol=1e-8):
            raise ValueError("RSLC center frequencies differ")
        folder = root/name
        folder.mkdir()
        out = folder/(name.replace('_L1_PR_RSLC_', '_L2_PR_GSLC_',1)+'.h5')
        config = {'runconfig': {'name':'gslc_workflow','groups':{
            'pge_name_group':{'pge_name':'GSLC_L_PGE'},
            'input_file_group':{'input_file_path':str(src)},
            'dynamic_ancillary_file_group':{'dem_file':str(args.dem.resolve())},
            'product_path_group':{'product_path':str(folder),'scratch_path':str(folder),'sas_output_file':str(out)},
            'primary_executable':{'product_type':'GSLC'},
            'processing':{'input_subset':{'list_of_frequencies':{freq:[pol]}},
                'geocode':{'output_epsg':epsg,'output_posting':{freq:{'x_posting':d,'y_posting':d}},
                           'top_left':{'x_abs':left,'y_abs':top},'bottom_right':{'x_abs':right,'y_abs':bottom}}}
        }}}
        yml = folder/'geocode.yaml'
        yml.write_text(yaml.safe_dump(apply_gslc_defaults(config, defaults)))
        subprocess.run([sys.executable,'-m','nisar.workflows.gslc',str(yml)],cwd=folder,check=True)
        paths = _nisar_h5_to_vrts(out,freq,[pol])
        if len(paths)!=1:
            raise ValueError(f'Expected one complex VRT for {out}')
        ds=gdal.Open(str(paths[0]))
        if ds is None or not gdal.DataTypeIsComplex(ds.GetRasterBand(1).DataType):
            raise ValueError('Invalid complex GSLC raster')
        grid=(ds.RasterXSize, ds.RasterYSize, ds.GetGeoTransform(), ds.GetProjection())
        if expected_grid is not None and grid != expected_grid:
            raise ValueError('Local GSLC grids differ; refusing pixel-wise interferometry.')
        dx, dy = grid[2][1], grid[2][5]
        if not (np.allclose(grid[2], (left,d,0,top,0,-d), atol=1e-5,rtol=0)
                and abs(grid[0]*dx-(right-left)) < 1e-4
                and abs(grid[1]*dy-(bottom-top)) < 1e-4
                and ds.GetSpatialRef() and ds.GetSpatialRef().GetAuthorityCode(None)==str(epsg)):
            raise ValueError('Local GSLC does not match the requested AOI grid.')
        sample=ds.GetRasterBand(1).ReadAsArray(buf_xsize=min(128,grid[0]),buf_ysize=min(128,grid[1]))
        valid=np.isfinite(sample) & (np.abs(sample)>0)
        common_valid = valid if common_valid is None else common_valid & valid
        if not common_valid.any():
            raise ValueError('No common valid complex samples on the AOI grid.')
        expected_grid=grid
        vrts.append(Path(paths[0]))
        ds=None
        preview = Path(__file__).with_name('check_nisar_images.py')
        if preview.is_file():
            result = subprocess.run([sys.executable, str(preview), '--rslc', str(src),
                '--gslc', str(out), '--frequency', freq, '--pol', pol,
                '--output', str(folder / 'inspection')])
            if result.returncode:
                print(f'WARNING: Preview failed for {name}; processing continues.', flush=True)
    cfg=build_displacement_config(vrts,root/'dolphin',options=DolphinOptions(strides=(1,1),max_bandwidth=min(args.max_bandwidth or 1,len(ids)-1),
        run_timeseries=False),subdataset='/unused-for-raster-inputs',wavelength=299792458.0/centers[0])
    if args.max_days is not None:
        # Specify the exact intersection of limits. Do not combine Dolphin
        # network selectors, which can introduce additional pairs.
        idx = {parse_id(name)[7]: i for i, name in enumerate(ids)}
        net = cfg.interferogram_network
        net.reference_idx = net.max_bandwidth = net.max_temporal_baseline = None
        net.indexes = [(idx[a], idx[b]) for a,b in pairs]
    cfg.unwrap_options.run_unwrap=False
    cfg.to_yaml(root/'dolphin_config.yaml')
    outputs=run(cfg)
    paths=outputs.stitched_ifg_paths
    if not paths or not all(Path(f).is_file() for f in paths):
        raise RuntimeError('No stitched wrapped interferogram found.')
    actual = {m.group() for path in paths for m in [re.search(r'\d{8}_\d{8}', Path(path).name)] if m}
    missing = {f'{a}_{b}' for a,b in pairs} - actual
    unexpected = actual - {f'{a}_{b}' for a,b in pairs}
    if args.max_days is not None and (unexpected or len(paths) != len(pairs)):
        raise RuntimeError(f"Output network differs from requested pairs: extra={sorted(unexpected)}, missing={sorted(missing)}")
    if missing:
        raise RuntimeError(f"Requested date pairs not found in stitched outputs: {sorted(missing)}")
    print('Wrapped interferograms:',  *paths, sep='\n')


if __name__ == '__main__':
    main()
