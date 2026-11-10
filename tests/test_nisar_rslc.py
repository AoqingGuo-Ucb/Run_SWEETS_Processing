import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_nisar_rslc import check_pair, parse_id

A = 'NISAR_L1_PR_RSLC_030_098_A_016_4005_DHDH_A_20260912T233918_20260912T233952_P05023_N_F_J_001'
B = A.replace('_030_', '_031_').replace('20260912','20260924')

class PairTests(unittest.TestCase):
    def test_repeat(self):
        check_pair([A,B + '.h5'])
    def test_wrong_product(self):
        with self.assertRaises(ValueError):
            parse_id(A.replace('L1_PR_RSLC','L2_PR_GSLC'))
    def test_incompatible(self):
        for other in (A, B.replace('_098_', '_048_'), B.replace('P05023','X05026')):
            with self.subTest(other=other), self.assertRaises(ValueError):
                check_pair([A,other])

class StackTests(unittest.TestCase):
    def test_chronological_network(self):
        from run_nisar_rslc import plan_stack
        c = B.replace('20260924', '20261006')
        d = B.replace('20260924', '20261018')
        ids, pairs = plan_stack([d,c,A,B], 2)
        self.assertEqual(ids, [A,B,c,d])
        self.assertEqual(pairs, [('20260912','20260924'), ('20260912','20261006'),
                                 ('20260924','20261006'), ('20260924','20261018'),
                                 ('20261006','20261018')])
    def test_duplicate_and_mixed_release(self):
        from run_nisar_rslc import plan_stack
        for names in ([A,B,B], [A,B,B.replace('20260924','20261006').replace('P05023','X05026')]):
            with self.assertRaises(ValueError):
                plan_stack(names, 1)

    def test_calendar_day_limits(self):
        from run_nisar_rslc import plan_stack
        names = [A,B,B.replace('20260924','20260930'),B.replace('20260924','20261130')]
        self.assertEqual(plan_stack(names, max_days=18)[1],
                         [('20260912','20260924'),('20260912','20260930'),('20260924','20260930')])
        self.assertEqual(plan_stack(names, 1, 18)[1],
                         [('20260912','20260924'),('20260924','20260930')])
        with self.assertRaisesRegex(ValueError, 'No date pairs'):
            plan_stack(names, max_days=5)

    def test_required_gslc_defaults_and_selected_band(self):
        from run_nisar_rslc import apply_gslc_defaults
        base = {'runconfig': {'groups': {'debug_level_group': {'debug_switch': False},
                'processing': {'blocksize': 512, 'flatten': True, 'geocode': {
                    'output_posting': {'A': None, 'B': None}}}}}}
        for band in ('A', 'B'):
            post = {'x_posting': 10, 'y_posting': 10}
            user = {'runconfig': {'groups': {'processing': {'geocode': {
                'output_posting': {band: post}}, 'input_subset': {'list_of_frequencies': {band: ['HH']}}}}}}
            groups = apply_gslc_defaults(user, base)['runconfig']['groups']
            proc = groups['processing']
            self.assertEqual(set(proc['geocode']['output_posting']), {'A','B'})
            self.assertEqual(proc['geocode']['output_posting'][band], post)
            self.assertEqual(proc['input_subset']['list_of_frequencies'], {band: ['HH']})
            self.assertEqual((groups['debug_level_group'], proc['blocksize'], proc['flatten']),
                             ({'debug_switch': False}, 512, True))

class SelectionTests(unittest.TestCase):
    def test_revision_selection(self):
        from prepare_nisar_stack import select_products
        self.assertEqual(select_products([A, A[:-3]+'002', B, B]), [A[:-3]+'002', B])

    def test_ambiguous_release(self):
        from prepare_nisar_stack import select_products
        with self.assertRaises(ValueError):
            select_products([A, A.replace('P05023', 'X05026'), B])

    def test_changed_timestamp(self):
        from prepare_nisar_stack import select_products
        with self.assertRaises(ValueError):
            select_products([A, A.replace('T233918', 'T233919'), B])
