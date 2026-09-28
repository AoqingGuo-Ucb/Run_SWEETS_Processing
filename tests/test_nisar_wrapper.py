"""Offline command-routing tests; does not simulate NISAR science processing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'run_sweets_nisar.sh'

class NisarWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.bin=self.root/'bin'; self.bin.mkdir()
        self.repo=self.root/'sweets'; self.repo.mkdir()
        self.log=self.root/'commands.jsonl'
        self.config=self.root/'settings.sh'
        self.config.write_text(f'SWEETS_REPO="{self.repo}"\nPROJECT_ROOT="{self.root}/projects"\nSITE="Test"\n')
        pixi=self.bin/'pixi'
        pixi.write_text('#!'+sys.executable+'\n'+'''import json,os,sys
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['TEST_COMMANDS'],'a') as f:f.write(json.dumps(a)+'\\n')
if a[:3]==['run','sweets','config']:
 if '--help' in a:
  print('unsupported' if os.environ.get('OLD_SWEETS') else 'nisar-gslc --polarizations --dolphin.strides')
  raise SystemExit(0)
 if os.environ.get('FAIL_CONFIG'):raise SystemExit(7)
 Path(a[a.index('--output')+1]).write_text('mock configuration')
elif a[:2]==['run','python']:
 if len(a)>2 and a[2].endswith('run_nisar_checked.py'):
  raise SystemExit(int(os.environ.get('FAIL_RUN','0')))
 if '-c' not in a:sys.stdin.read()
elif (a[:2]==['run','python'] and a[2].endswith('run_nisar_checked.py')):
 raise SystemExit(int(os.environ.get('FAIL_RUN','0')))
else:raise SystemExit(99)
''')
        pixi.chmod(0o755)
        flock=self.bin/'flock';flock.write_text('#!/bin/sh\nexit 0\n');flock.chmod(0o755)
        self.env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH'],TEST_COMMANDS=str(self.log))

    def tearDown(self):self.tmp.cleanup()

    def run_script(self,mode):
        return subprocess.run(['bash',str(SCRIPT),str(mode),str(self.config)],env=self.env,capture_output=True,text=True)

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_config_only_has_nisar_defaults_no_sentinel_track(self):
        r=self.run_script('config');self.assertEqual(r.returncode,0,r.stderr)
        call=next(a for a in self.calls() if '--source' in a)
        self.assertEqual(call[call.index('--source')+1],'nisar-gslc')
        self.assertEqual(call[call.index('--polarizations')+1],'HH')
        self.assertNotIn('--track',call)
        self.assertEqual(call[call.index('--dolphin.strides')+1:call.index('--dolphin.strides')+3],['1','1'])
        self.assertFalse(any((a[:2]==['run','python'] and a[2].endswith('run_nisar_checked.py')) for a in self.calls()))

    def test_full_run_uses_sweets_step_one(self):
        r=self.run_script(1);self.assertEqual(r.returncode,0,r.stderr)
        call=next(a for a in self.calls() if (a[:2]==['run','python'] and a[2].endswith('run_nisar_checked.py')))
        self.assertEqual(call[-2:],['--starting-step','1'])

    def test_resume_three_uses_existing_config(self):
        self.assertEqual(self.run_script('config').returncode,0)
        self.log.write_text('')
        r=self.run_script(3);self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse(any('--source' in a for a in self.calls()))
        self.assertEqual(self.calls()[-1][-2:],['--starting-step','3'])

    def test_resume_two_preserves_config(self):
        self.assertEqual(self.run_script('config').returncode,0)
        self.log.write_text('')
        r=self.run_script(2);self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse(any('--source' in a for a in self.calls()))
        self.assertEqual(self.calls()[-1][-2:],['--starting-step','1'])

    def test_old_version_stops(self):
        self.env['OLD_SWEETS']='1'
        self.assertNotEqual(self.run_script(1).returncode,0)
        self.assertFalse(any('--source' in a for a in self.calls()))

    def test_config_failure_stops_before_processing(self):
        self.env['FAIL_CONFIG']='1'
        self.assertEqual(self.run_script(1).returncode,7)
        self.assertFalse(any((a[:2]==['run','python'] and a[2].endswith('run_nisar_checked.py')) for a in self.calls()))

    def test_processing_failure_propagates(self):
        self.env['FAIL_RUN']='8'
        self.assertEqual(self.run_script(1).returncode,8)

    def test_existing_config_not_overwritten(self):
        self.assertEqual(self.run_script('config').returncode,0)
        self.log.write_text('')
        self.assertNotEqual(self.run_script(1).returncode,0)
        self.assertFalse(any('--source' in a for a in self.calls()))

    def test_invalid_collection_stops_before_pixi(self):
        with self.config.open('a') as f:f.write('NISAR_COLLECTION="invalid"\n')
        self.assertNotEqual(self.run_script(1).returncode,0)
        self.assertEqual(self.calls(),[])

    def test_default_collection_passed_to_config_validation(self):
        self.assertEqual(self.run_script('config').returncode,0)
        call=next(a for a in self.calls() if a[:3]==['run','python','-'])
        self.assertEqual(call[-1],'NISAR_L2_GSLC_PROVISIONAL_V1')


class CollectionPersistenceTests(unittest.TestCase):
    """Execute the wrapper's Python block with a file-backed model double."""

    def exercise(self,mode,requested,initial='NISAR_L2_GSLC_BETA_V1',supported=True):
        import contextlib
        import io
        import types
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=root/'config.json'
            config.write_text(json.dumps({'short_name':initial,'extra':'preserved'}))
            original=config.read_bytes()

            class Search:
                model_fields={'short_name':object()} if supported else {}
                def __init__(self,name):
                    self.short_name=name
                    self.out_dir=root/'data'

            class Workflow:
                @classmethod
                def from_yaml(cls,path):
                    obj=cls()
                    obj.payload=json.loads(Path(path).read_text())
                    obj.search=Search(obj.payload['short_name'])
                    obj.work_dir=root/'work'
                    obj.overwrite=False
                    return obj
                def to_yaml(self,path):
                    self.payload['short_name']=self.search.short_name
                    Path(path).write_text(json.dumps(self.payload))

            core=types.ModuleType('sweets.core');core.Workflow=Workflow
            download=types.ModuleType('sweets.download');download.NisarGslcSearch=Search
            block=SCRIPT.read_text().split('<<\'PY\'\n')[-1].split('\nPY\n')[0]
            output=io.StringIO()
            with patch.dict(sys.modules,{'sweets.core':core,'sweets.download':download}), patch.object(
                sys,'argv',['-',str(config),str(root/'data'),str(root/'work'),mode,requested]
            ), contextlib.redirect_stdout(output):
                exec(compile(block,str(SCRIPT),'exec'),{})
            return json.loads(config.read_text()),output.getvalue(),config.read_bytes()==original

    def test_new_config_persists_provisional_and_preserves_other_fields(self):
        data,output,_=self.exercise('config','NISAR_L2_GSLC_PROVISIONAL_V1')
        self.assertEqual(data,{'short_name':'NISAR_L2_GSLC_PROVISIONAL_V1','extra':'preserved'})
        self.assertIn('CMR short_name: NISAR_L2_GSLC_PROVISIONAL_V1',output)

    def test_explicit_beta_is_supported(self):
        data,_,_=self.exercise('1','NISAR_L2_GSLC_BETA_V1')
        self.assertEqual(data['short_name'],'NISAR_L2_GSLC_BETA_V1')

    def test_resume_does_not_rewrite_beta_and_warns(self):
        data,output,unchanged=self.exercise('2','NISAR_L2_GSLC_PROVISIONAL_V1')
        self.assertTrue(unchanged)
        self.assertEqual(data['short_name'],'NISAR_L2_GSLC_BETA_V1')
        self.assertIn('WARNING: existing YAML uses',output)

    def test_missing_model_field_fails(self):
        with self.assertRaisesRegex(SystemExit,'does not support'):
            self.exercise('config','NISAR_L2_GSLC_PROVISIONAL_V1',supported=False)

if __name__=='__main__':unittest.main()
