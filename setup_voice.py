"""Install one pinned multilingual Whisper model; inference remains offline."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
from setup_bonsai import download, digest, fetch_json

REPO="Systran/faster-whisper-base"
REVISION="ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
ROOT=Path(__file__).resolve().parent / ".runtime/voice/base"
FILES={"config.json","model.bin","tokenizer.json","vocabulary.txt"}

def verify(root=ROOT):
    manifest=json.loads((root/"manifest.json").read_text())
    if manifest.get("revision")!=REVISION or {item["name"] for item in manifest["files"]}!=FILES: raise ValueError("音声モデルの導入情報が不正です。")
    return all((root/item["name"]).is_file() and digest(root/item["name"])==item["sha256"] for item in manifest["files"])

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--verify",action="store_true");args=parser.parse_args()
    if args.verify:
        good=verify();print(json.dumps({"verified":good,"revision":REVISION}));return 0 if good else 1
    ROOT.mkdir(parents=True,exist_ok=True)
    tree=fetch_json(f"https://huggingface.co/api/models/{REPO}/tree/{REVISION}?recursive=true")
    manifest=[]
    for name in sorted(FILES):
        item=next(entry for entry in tree if entry["path"]==name);target=ROOT/name
        url=f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        if item.get("lfs"):
            download(url,target,item["size"],item["lfs"]["oid"])
        else:
            with urllib.request.urlopen(url,timeout=60) as response: raw=response.read(5*1024*1024)
            if hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()!=item["oid"]: raise ValueError("音声モデル設定のハッシュが一致しません。")
            temporary=target.with_suffix(target.suffix+".part");temporary.write_bytes(raw);temporary.replace(target)
        manifest.append({"name":name,"sha256":digest(target),"size":target.stat().st_size})
    (ROOT/"manifest.json").write_text(json.dumps({"repository":REPO,"revision":REVISION,"license":"MIT","files":manifest},indent=2),encoding="utf-8")
    print("Local multilingual voice model installed and verified.");return 0

if __name__=="__main__": raise SystemExit(main())
