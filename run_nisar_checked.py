"""Run SWEETS with a NISAR input check immediately before Dolphin starts."""
from collections import defaultdict
from functools import wraps
from pathlib import Path
import re
import sys
import json
import tempfile


def check_inputs(files):
    """Reject multiple inputs per calendar day, including processing variants.

    Only inspect the actual Dolphin input list; HDF5 companions are not counted.
    Dolphin's date-based ministacks cannot safely separate same-day inputs here.
    """
    by_date = defaultdict(list)
    for filename in files:
        name = Path(filename).name
        match = re.search(r"_(\d{8})T\d{6}_\d{8}T\d{6}_", name)
        if not name.startswith('NISAR_L2_') or '_GSLC_' not in name or not match:
            raise ValueError(f'Cannot verify NISAR acquisition date: {filename}')
        by_date[match.group(1)].append(str(filename))
    conflicts = {day: paths for day, paths in by_date.items() if len(paths) > 1}
    if conflicts:
        lines = ['Duplicate NISAR acquisition dates detected; Dolphin has not started.']
        for day, paths in sorted(conflicts.items()):
            lines.append(f'{day}: {len(paths)} inputs')
            lines.extend(f'  {path}' for path in paths)
        lines.append(
            'Review product versions, track/frame, frequency and polarization. '
            'Keep one compatible input per date in this workflow. Move excluded '
            'HDF5/VRT pairs outside the data directory; no files were changed by this check. '
            'After correcting inputs, back up the old work/dolphin directory outside '
            'its original path and resume with wrapper mode 3.'
        )
        raise ValueError('\n'.join(lines))


def match_normal_versions(files, data_dir, work_dir):
    """Quarantine duplicate variants only when all singleton dates agree."""
    files = [Path(p) for p in files]
    groups = defaultdict(list)
    parsed = {}
    for path in files:
        match = re.fullmatch(
            r"(NISAR_L2_PR_GSLC_.+?_(\d{8})T\d{6}_\d{8}T\d{6})_"
            r"([A-Z]\d{5})(_.+)\.(HH|HV|VH|VV)\.vrt", path.name
        )
        if not match:
            raise ValueError(f'Cannot safely identify product version: {path}')
        prefix, day, version, suffix, pol = match.groups()
        parsed[path] = (version, (prefix, suffix, pol))
        groups[day].append(path)
    duplicates = {d: paths for d, paths in groups.items() if len(paths) > 1}
    if not duplicates:
        check_inputs(files)
        return files, False
    references = {day: parsed[paths[0]][0] for day, paths in groups.items() if len(paths) == 1}
    versions = set(references.values())
    if len(versions) != 1:
        raise ValueError('Cannot infer one consistent version from non-duplicate dates: '
                         + json.dumps(references, sort_keys=True)
                         + '. No files were moved. Select a version explicitly after review.')
    preferred = next(iter(versions))
    excluded = []
    for day, paths in duplicates.items():
        keep = [p for p in paths if parsed[p][0] == preferred]
        if len(keep) != 1 or len({parsed[p][1] for p in paths}) != 1:
            raise ValueError(f'{day}: not a simple processing-version duplicate with one '
                             f'{preferred} product. No files were moved.')
        excluded.extend(p for p in paths if p != keep[0])
    # Validate every pair before any move. Reject shared, missing or linked inputs.
    data_dir = Path(data_dir).resolve()
    moves = []
    for vrt in excluded:
        h5 = vrt.with_name(vrt.name.rsplit('.', 2)[0] + '.h5')
        if {p.resolve() for p in data_dir.glob(h5.stem + '.*.vrt')} != {vrt.resolve()}:
            raise ValueError(f'Unexpected VRT companions for {h5}; no files were moved.')
        for path in (vrt, h5):
            if path.is_symlink() or not path.is_file() or path.parent.resolve() != data_dir:
                raise ValueError(f'Unsafe or missing input pair: {path}; no files were moved.')
            moves.append(path)
    dolphin = Path(work_dir) / 'dolphin'
    if dolphin.is_symlink():
        raise ValueError('Refusing to move a symlinked Dolphin work directory')
    backup = Path(tempfile.mkdtemp(prefix='nisar_duplicate_backup_', dir=data_dir.parent))
    (backup / 'data').mkdir()
    manifest = {'kept_version': preferred, 'reference_dates': references,
                'moved_inputs': [str(p) for p in moves],
                'dolphin_backup': str(dolphin) if dolphin.exists() else None}
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    completed = []
    try:
        for path in moves:
            target = backup / 'data' / path.name
            path.rename(target)
            completed.append((path, target))
        if dolphin.exists():
            target = backup / 'dolphin'
            dolphin.rename(target)
            completed.append((dolphin, target))
    except Exception:
        for original, target in reversed(completed):
            target.rename(original)
        raise
    print(f'Kept version {preferred}, matching {len(references)} non-duplicate dates. '
          f'Backed up {len(excluded)} excluded HDF5/VRT pairs to {backup}', flush=True)
    remaining = [p for p in files if p not in excluded]
    check_inputs(remaining)
    return remaining, True


def install_guard(workflow_class, auto_match=False):
    original = workflow_class._run_dolphin

    @wraps(original)
    def checked(self, gslc_files, *args, **kwargs):
        files = list(gslc_files)
        if auto_match:
            files, changed = match_normal_versions(files, self.search.out_dir, self.work_dir)
            if changed:
                raise ValueError('Duplicate versions were backed up after downloading. '
                                 'Run wrapper mode 3 to start with the clean input list.')
        check_inputs(files)
        print(f'NISAR date check passed: {len(files)} unique-date inputs', flush=True)
        return original(self, files, *args, **kwargs)

    workflow_class._run_dolphin = checked


def main():
    from sweets.core import Workflow
    from sweets.download import NisarGslcSearch
    from sweets.cli import main as sweets_main

    config = sys.argv[1]
    wf = Workflow.from_yaml(config)
    if not isinstance(wf.search, NisarGslcSearch):
        raise SystemExit('ERROR: this runner requires a NISAR configuration')
    # Catch already-downloaded duplicates before any processing or download work.
    match_normal_versions(wf.search.existing_files(), wf.search.out_dir, wf.work_dir)
    # Also check the final list after a fresh download, before PS/phase linking.
    install_guard(Workflow, auto_match=True)
    sys.argv = ['sweets', 'run', *sys.argv[1:]]
    return sweets_main()


if __name__ == '__main__':
    sys.exit(main())
