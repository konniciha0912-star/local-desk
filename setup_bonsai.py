"""Install the official Bonsai runtime locally; no Python packages required."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import zipfile
import uuid

RELEASE = "prism-b10743-adfffbe"
REPO = "prism-ml/Ternary-Bonsai-2-27B-gguf"
MODEL = "Ternary-Bonsai-2-27B-PQ2_0.gguf"
REVISION = "b072e1d3b35a0a630cece372c2127528e0994386"
ROOT = Path(__file__).resolve().parent / ".runtime" / "bonsai"

def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Local-Desk-setup"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(16 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()

def download(url, target, size, expected):
    if not expected:
        raise RuntimeError(f"No upstream SHA-256 available for {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == size and digest(target) == expected:
        print(f"Already verified: {target.name}", flush=True)
        return target
    partial = target.with_suffix(target.suffix + ".part")
    if partial.is_file() and partial.stat().st_size >= size:
        if partial.stat().st_size == size and digest(partial) == expected:
            partial.replace(target)
            print(f"Verified completed download: {target.name}", flush=True)
            return target
        partial.rename(partial.with_name(partial.name + f".failed-{uuid.uuid4().hex}"))
    for attempt in range(4):
        start = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(url, headers={"Range": f"bytes={start}-"} if start else {})
        print(f"Downloading {target.name}: {start:,}/{size:,}", flush=True)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                append = start > 0 and response.status == 206
                total = start if append else 0
                last = time.monotonic()
                with partial.open("ab" if append else "wb") as stream:
                    while chunk := response.read(8 * 1024 * 1024):
                        stream.write(chunk)
                        total += len(chunk)
                        if time.monotonic() - last >= 15:
                            print(f"{target.name}: {total / size:.1%} ({total / 1e9:.2f} GB)", flush=True)
                            last = time.monotonic()
            if partial.stat().st_size != size:
                raise RuntimeError("Incomplete download")
            actual = digest(partial)
            if actual != expected:
                # Keep the failed file for diagnosis; never treat it as installed.
                partial.rename(partial.with_name(partial.name + f".failed-{uuid.uuid4().hex}"))
                raise RuntimeError(f"SHA-256 mismatch: {target.name}")
            partial.replace(target)
            print(f"Verified SHA-256: {target.name} {actual}", flush=True)
            return target
        except Exception as exc:
            print(f"Attempt {attempt + 1}: {exc}", flush=True)
            if attempt == 3:
                raise
    raise RuntimeError("Download failed")

def verify_runtime(root=ROOT):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for entry in manifest.get("files", []):
        directory = "models" if entry["name"].endswith(".gguf") else "downloads"
        path = root / directory / entry["name"]
        results.append({"name": entry["name"], "ok": path.is_file() and path.stat().st_size == entry["size"] and digest(path) == entry["sha256"]})
    for entry in manifest.get("binaries", []):
        path = root / "bin" / entry["name"]
        results.append({"name": entry["name"], "ok": path.is_file() and path.stat().st_size == entry["size"] and digest(path) == entry["sha256"]})
    return {"ok": bool(results) and bool(manifest.get("binaries")) and all(item["ok"] for item in results),
            "release": manifest.get("release"), "revision": manifest.get("revision"), "files": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        report = verify_runtime()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["ok"]:
            raise RuntimeError("導入ファイルの検証に失敗しました。再インストールしてください。")
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    release = fetch_json(f"https://api.github.com/repos/PrismML-Eng/llama.cpp/releases/tags/{RELEASE}")
    names = [f"llama-{RELEASE}-bin-win-cuda-13.3-x64.zip", "cudart-llama-bin-win-cuda-13.3-x64.zip"]
    assets = [next(item for item in release["assets"] if item["name"] == name) for name in names]
    entries = fetch_json(f"https://huggingface.co/api/models/{REPO}/tree/{REVISION}")
    model = next(item for item in entries if item["path"] == MODEL)
    jobs = [(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{MODEL}?download=true", ROOT / "models" / MODEL, model["size"], model["lfs"]["oid"])]
    jobs += [(item["browser_download_url"], ROOT / "downloads" / item["name"], item["size"], (item.get("digest") or "").removeprefix("sha256:")) for item in assets]
    with ThreadPoolExecutor(max_workers=3) as pool:
        files = list(pool.map(lambda job: download(*job), jobs))
    binary = ROOT / "bin"
    binary.mkdir(exist_ok=True)
    for archive in files[1:]:
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if member.is_dir():
                    continue
                # Vendor archives are flattened into a dedicated runtime directory.
                name = Path(member.filename).name
                if name:
                    (binary / name).write_bytes(package.read(member))
    manifest = {"release": RELEASE, "revision": REVISION, "repo": REPO, "model": MODEL,
                "files": [{"name": path.name, "size": path.stat().st_size, "sha256": digest(path)} for path in files],
                "binaries": [{"name": path.name, "size": path.stat().st_size, "sha256": digest(path)} for path in sorted(binary.iterdir()) if path.is_file()]}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not (binary / "llama-server.exe").is_file():
        raise RuntimeError("llama-server.exe missing from official archive")
    print(f"Bonsai is installed: {ROOT}", flush=True)

if __name__ == "__main__":
    main()
