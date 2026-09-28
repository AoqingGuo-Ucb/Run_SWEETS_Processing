"""Run SWEETS with a NISAR input check immediately before Dolphin starts."""
from collections import defaultdict
from functools import wraps
from pathlib import Path
import re
import sys


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


def install_guard(workflow_class):
    original = workflow_class._run_dolphin

    @wraps(original)
    def checked(self, gslc_files, *args, **kwargs):
        files = list(gslc_files)
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
    check_inputs(wf.search.existing_files())
    # Also check the final list after a fresh download, before PS/phase linking.
    install_guard(Workflow)
    sys.argv = ['sweets', 'run', *sys.argv[1:]]
    return sweets_main()


if __name__ == '__main__':
    sys.exit(main())
