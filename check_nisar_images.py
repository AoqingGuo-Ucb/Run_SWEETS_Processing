"""Read-only RSLC/GSLC visual inspection; never downloads or changes products."""
import argparse
import json
from pathlib import Path


def read_preview(path, product, frequency, pol, max_size):
    import h5py
    import numpy as np

    group = 'swaths' if product == 'RSLC' else 'grids'
    key = f'/science/LSAR/{product}/{group}/frequency{frequency}'
    with h5py.File(path, 'r') as hf:
        band = hf[key]
        ds = band[pol]
        if ds.ndim != 2:
            raise ValueError(f'Expected a 2-D complex image: {ds.name}')
        step = max(1, int(np.ceil(max(ds.shape) / max_size)))
        data = ds[::step, ::step]
        # NISAR complex32 may be exposed as a compound real/imaginary dtype.
        if data.dtype.fields:
            if not {'r', 'i'} <= set(data.dtype.fields):
                raise ValueError(f'Unsupported compound complex dtype: {data.dtype}')
            data = data['r'].astype('float32') + 1j * data['i'].astype('float32')
        if not np.iscomplexobj(data):
            raise ValueError(f'Expected complex samples, got {data.dtype}')
        valid = np.isfinite(data) & (np.abs(data) > 0)
        db = np.full(data.shape, np.nan)
        db[valid] = 20 * np.log10(np.abs(data[valid]))
        meta = dict(file=str(path.resolve()), dataset=ds.name,
                    full_shape=list(ds.shape), preview_stride=step,
                    sampled_valid_fraction=float(valid.mean()))
        extent = None
        if product == 'GSLC':
            x = band['xCoordinates']
            y = band['yCoordinates']
            dx = float(band['xCoordinateSpacing'][()])
            dy = float(band['yCoordinateSpacing'][()])
            extent = [float(x[0])-dx/2, float(x[-1])+dx/2,
                      float(y[-1])+dy/2, float(y[0])-dy/2]
            meta.update(epsg=int(band['projection'][()]), extent=extent,
                        pixel_spacing=[dx, dy])
        return db, extent, meta


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rslc', type=Path, required=True)
    p.add_argument('--gslc', type=Path, required=True)
    p.add_argument('--frequency', choices=['A', 'B'], default='A')
    p.add_argument('--pol', choices=['HH', 'HV', 'VH', 'VV'], default='HH')
    p.add_argument('--output', type=Path, required=True, help='New inspection directory')
    p.add_argument('--max-size', type=int, default=1200)
    args = p.parse_args()
    if args.max_size < 32:
        p.error('--max-size must be at least 32')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    images = [read_preview(path, product, args.frequency, args.pol, args.max_size)
              for path, product in [(args.rslc, 'RSLC'), (args.gslc, 'GSLC')]]
    args.output.mkdir(parents=True, exist_ok=False)
    for product, (db, extent, meta) in zip(['RSLC', 'GSLC'], images):
        fig, ax = plt.subplots(figsize=(10, 8), constrained_layout=True)
        finite = db[np.isfinite(db)]
        if finite.size:
            lo, hi = np.percentile(finite, [2, 98])
            im = ax.imshow(np.ma.masked_invalid(db), cmap='gray', vmin=lo,
                           vmax=max(hi, lo + 1e-6), extent=extent, aspect='auto')
            fig.colorbar(im, ax=ax, label='20 log10(|DN|), display only')
        else:
            ax.text(.5, .5, 'No valid sampled pixels', transform=ax.transAxes, ha='center')
        ax.set_title(f'{product} {args.frequency}/{args.pol} — intensity preview')
        ax.set_xlabel('Map X' if extent else 'Subsampled range column')
        ax.set_ylabel(f'Map Y (EPSG:{meta["epsg"]})' if extent else 'Subsampled azimuth row')
        fig.savefig(args.output / f'{product.lower()}_intensity.png', dpi=160)
        plt.close(fig)
    report = {'images': [item[2] for item in images], 'limitations': [
        'RSLC preview covers the full input; GSLC may cover only the requested AOI.',
        'Images use separate contrast stretches; brightness is not a calibrated comparison.',
        'Subsampled previews can miss small defects. Invalid/zero samples are masked.',
        'These plots do not validate absolute geolocation or subpixel coregistration.',
        'Compare the GSLC with an independent map and other dates before interpreting interferograms.'
    ]}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Inspection images and report: {args.output.resolve()}')


if __name__ == '__main__':
    main()
