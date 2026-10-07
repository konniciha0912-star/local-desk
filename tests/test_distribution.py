import importlib.util
from pathlib import Path
import tempfile
import subprocess
import sys
import zipfile
import unittest

path=Path(__file__).resolve().parents[1]/'tools/package_source.py'
spec=importlib.util.spec_from_file_location('distribution',path);distribution=importlib.util.module_from_spec(spec);spec.loader.exec_module(distribution)

class DistributionTests(unittest.TestCase):
    def test_private_data_and_runtime_are_never_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name in ['app.py','README.md','.data/history.sqlite3','.runtime/key.py','web/.data/secret.js','web/node_modules/leak.js','tests/__pycache__/leak.py','web/app.js','secret.py']:
                item=root/name;item.parent.mkdir(parents=True,exist_ok=True);item.write_text('fixture')
            selected={str(item.relative_to(root)).replace('\\','/') for item in distribution.source_paths(root)}
            self.assertEqual(selected,{'app.py','README.md','web/app.js'})
    def test_vendored_dependencies_match_their_pinned_hashes(self):
        distribution.verify_vendor(path.parents[1])
    def test_packaged_source_imports_without_the_working_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            archive=root/'source.zip'
            distribution.package(path.parents[1],archive)
            with zipfile.ZipFile(archive) as bundle: bundle.extractall(root/'extracted')
            result=subprocess.run([sys.executable,'-I','-c',
                'import sys; sys.path.insert(0, "."); import app, desktop, import_worker, web_sources; print("source imports OK")'],
                cwd=root/'extracted/LocalDesk',capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
