"""Build a reproducible source-only ZIP; models and user data are never selected."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from version import VERSION

ROOT_FILES = '''app.py companion.py connections.py completion.py context.py desktop.py edits.py engines.py generation.py
import_worker.py imports.py maintenance.py model_catalog.py persistence.py productivity.py
project_tests.py projects.py setup_bonsai.py setup_ocr.py setup_voice.py version.py web_sources.py
Desktop.cmd Install.cmd Install-Desktop.ps1 Start-Bonsai.cmd Start.cmd Start.ps1
Uninstall-Desktop.ps1 README.md LICENSE CHANGELOG.md THIRD_PARTY_NOTICES.md
requirements-desktop.txt requirements-desktop.lock.txt .gitignore .gitattributes'''.split()
TREE_SUFFIXES = {
    'web': {'.js','.css','.html','.svg','.ico','.json','.txt'},
    'tests': {'.py','.cjs','.json'}, 'tools': {'.py'}, 'examples': {'.py','.md','.txt'},
    'docs': {'.md','.json','.jpg','.png'}, '.github': {'.yml','.yaml'},
    'vscode': {'.js','.json','.md','.txt',''},
}
LOCAL_PLANS = {'docs/PLAN.md', 'docs/IMPROVEMENTS.md', 'docs/IMPLEMENTATION-41.md'}

def source_paths(root):
    files = [root/name for name in ROOT_FILES if (root/name).is_file()]
    for folder, suffixes in TREE_SUFFIXES.items():
        for item in (root/folder).rglob('*'):
            relative = item.relative_to(root)
            if relative.as_posix() in LOCAL_PLANS:
                continue
            if item.is_file() and item.suffix in suffixes and not any(
                part in {'.data','.runtime','.git','node_modules','__pycache__','work','.venv'} for part in relative.parts
            ) and not item.is_symlink():
                files.append(item)
    for item in files:
        if item.is_symlink() or not item.resolve().is_relative_to(root.resolve()):
            raise ValueError('Source contains a linked path: '+str(item.relative_to(root)))
    return sorted(set(files))

def verify_vendor(root):
    manifest = json.loads((root/'web/vendor/manifest.json').read_text())
    for entry in manifest:
        if Path(entry['name']).name != entry['name']: raise ValueError('Unsafe vendor path')
        data=(root/'web/vendor'/entry['name']).read_bytes()
        if len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
            raise ValueError('Vendor hash mismatch: '+entry['name'])

def package(root, output):
    verify_vendor(root)
    selected=source_paths(root)
    files={str(item.relative_to(root)).replace('\\','/'):item.read_bytes() for item in selected}
    manifest={'version':VERSION,'files':[{'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()]}
    files['source-manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    pending=output.with_suffix('.zip.tmp')
    with zipfile.ZipFile(pending,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(files.items()):
            entry=zipfile.ZipInfo('LocalDesk/'+name,date_time=(2020,1,1,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(entry,data)
    pending.replace(output)
    return {'name':output.name,'files':len(files),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');parser.add_argument('--output',type=Path,default=BASE.parent/f'LocalDesk-{VERSION}.zip')
    args=parser.parse_args()
    if args.verify_only: verify_vendor(BASE);print('Vendored dependencies verified')
    else: print(json.dumps(package(BASE,args.output),indent=2))
