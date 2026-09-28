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
