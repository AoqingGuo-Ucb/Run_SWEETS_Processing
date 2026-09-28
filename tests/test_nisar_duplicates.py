"""No SWEETS installation or satellite downloads required."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'nisar_guard', Path(__file__).resolve().parents[1] / 'run_nisar_checked.py'
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def product(day, version='P05023', pol='HH'):
    return f'NISAR_L2_PR_GSLC_030_098_A_016_4005_DHDH_A_{day}T233918_{day}T233952_{version}_N_F_J_001.{pol}.vrt'


class DuplicateTests(unittest.TestCase):
    def test_unique_dates_pass(self):
        guard.check_inputs([product('20260912'), product('20260924')])

    def test_processing_versions_report_both(self):
        with self.assertRaises(ValueError) as result:
            guard.check_inputs([product('20260912'), product('20260912', 'X05026')])
        self.assertIn('20260912: 2 inputs', str(result.exception))
        self.assertIn('P05023', str(result.exception))
        self.assertIn('X05026', str(result.exception))

    def test_mixed_polarizations_stop(self):
        with self.assertRaises(ValueError):
            guard.check_inputs([product('20260912'), product('20260912', pol='HV')])

    def test_unrecognized_names_fail_closed(self):
        with self.assertRaises(ValueError):
            guard.check_inputs(['unknown.vrt'])

    def test_final_download_list_blocked_before_dolphin(self):
        calls = []
        class Workflow:
            def _run_dolphin(self, files):
                calls.append(files)
                return 'processed'
        guard.install_guard(Workflow)
        with self.assertRaises(ValueError):
            Workflow()._run_dolphin([product('20260912'), product('20260912', 'X05026')])
        self.assertEqual(calls, [])
        self.assertEqual(Workflow()._run_dolphin([product('20260912'), product('20260924')]), 'processed')
        self.assertEqual(len(calls), 1)


class AutomaticVersionTests(unittest.TestCase):
    def setup_files(self, root, names):
        data = root / 'data'
        data.mkdir()
        work = root / 'work'
        (work / 'dolphin').mkdir(parents=True)
        (work / 'dolphin' / 'old-result').write_text('old')
        files = []
        for name in names:
            path = data / name
            path.write_text('vrt')
            path.with_name(name.rsplit('.', 2)[0] + '.h5').write_text('h5')
            files.append(path)
        return data, work, files

    def test_matches_singletons_and_backs_up_pairs_and_work(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, work, files = self.setup_files(root, [product('20260924'), product('20260912'), product('20260912', 'X05026')])
            remaining, changed = guard.match_normal_versions(files, data, work)
            self.assertTrue(changed)
            self.assertEqual(remaining, files[:2])
            self.assertEqual(len(list(data.iterdir())), 4)
            backup = next(root.glob('nisar_duplicate_backup_*'))
            self.assertEqual(len(list((backup / 'data').iterdir())), 2)
            self.assertTrue((backup / 'dolphin' / 'old-result').is_file())
            self.assertFalse((work / 'dolphin').exists())
            self.assertFalse(guard.match_normal_versions(remaining, data, work)[1])

    def test_mixed_singletons_leave_everything_unchanged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, work, files = self.setup_files(root, [product('20260924'), product('20260726', 'X05026'), product('20260912'), product('20260912', 'X05026')])
            with self.assertRaisesRegex(ValueError, 'Cannot infer'):
                guard.match_normal_versions(files, data, work)
            self.assertEqual(len(list(data.iterdir())), 8)
            self.assertTrue((work / 'dolphin' / 'old-result').exists())
            self.assertFalse(list(root.glob('nisar_duplicate_backup_*')))

    def test_missing_pair_leaves_everything_unchanged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, work, files = self.setup_files(root, [product('20260924'), product('20260912'), product('20260912', 'X05026')])
            files[-1].with_name(files[-1].name.rsplit('.', 2)[0] + '.h5').unlink()
            with self.assertRaisesRegex(ValueError, 'missing input pair'):
                guard.match_normal_versions(files, data, work)
            self.assertTrue(all(p.exists() for p in files))
            self.assertTrue((work / 'dolphin').exists())

    def test_majority_kept_and_minority_singleton_preserved(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, work, files = self.setup_files(root, [
                product('20260527', 'X05026'), product('20260608', 'X05026'),
                product('20260924', 'P05023'), product('20260912', 'P05023'),
                product('20260912', 'X05026'),
            ])
            remaining, changed = guard.match_normal_versions(files, data, work)
            self.assertTrue(changed)
            self.assertEqual(remaining, files[:3] + files[4:])
            self.assertTrue(files[2].exists())
            self.assertTrue(files[2].with_name(files[2].name.rsplit('.', 2)[0] + '.h5').exists())
            self.assertFalse(files[3].exists())
            manifest = json.loads(next(root.glob('nisar_duplicate_backup_*/manifest.json')).read_text())
            self.assertEqual(manifest['kept_version'], 'X05026')
            self.assertEqual(manifest['version_counts'], {'X05026': 2, 'P05023': 1})
