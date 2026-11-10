"""Prepare a reproducible ID list and select an unused processing attempt."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from run_nisar_rslc import parse_id, plan_stack


def select_products(names):
    by_date = {}
    for name in sorted(set(names)):
        name = name.removesuffix('.h5')
        fields = parse_id(name)
        day = fields[7]
        if day in by_date:
            old = by_date[day]
            # Only the terminal revision counter may differ, including timestamps.
            if name.rsplit('_', 1)[0] != old.rsplit('_', 1)[0]:
                raise ValueError(f'Ambiguous products for {day}: {old}, {name}')
            if int(fields[-1]) < int(parse_id(old)[-1]):
                continue
        by_date[day] = name
    selected = sorted(by_date.values(), key=lambda v: parse_id(v)[7])
    plan_stack(selected)
    return selected


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--helper', type=Path, required=True)
    p.add_argument('--ids-file', type=Path, required=True)
    p.add_argument('search_args', nargs=argparse.REMAINDER)
    args = p.parse_args()
    query = args.search_args
    if query and query[0] == '--':
        query = query[1:]
    target = args.ids_file
    receipt = target.with_suffix('.query.json')
    if target.exists():
        if receipt.exists() and json.loads(receipt.read_text()) != query:
            raise ValueError('Search settings changed. Use a new IDS_FILE or review the existing list.')
        names = [s.strip() for s in target.read_text().splitlines()
                 if s.strip() and not s.lstrip().startswith('#')]
    else:
        result = subprocess.run([sys.executable, str(args.helper), 'search', *query],
                                check=True, text=True, stdout=subprocess.PIPE)
        print(result.stdout, end='')
        names = [s.strip() for s in result.stdout.splitlines() if s.startswith('NISAR_L1_PR_RSLC_')]
    selected = select_products(names)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and names != selected:
        backup = target.with_suffix('.before_selection.txt')
        with backup.open('x') as f:
            f.write(target.read_text())
    temporary = target.with_suffix('.tmp')
    temporary.write_text('\n'.join(selected) + '\n')
    os.replace(temporary, target)
    if not receipt.exists():
        receipt.write_text(json.dumps(query, indent=2))
    target.with_suffix('.selection.json').write_text(json.dumps(
        {'policy': 'Highest terminal revision only; other same-date differences require review.',
         'selected': selected, 'excluded': sorted(set(names) - set(selected))}, indent=2))
    print(f'Selected {len(selected)} acquisitions: {target}')


if __name__ == '__main__':
    main()
