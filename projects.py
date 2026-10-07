"""Bounded lexical retrieval and project summaries within the chosen root."""
import ast
from collections import Counter
import math
import re
import unicodedata


def terms(text):
    text = unicodedata.normalize("NFKC", text).lower()
    result = re.findall(r"[a-z0-9_]{2,}", text)
    for word in re.findall(r"[\u3040-\u30ff\u3400-\u9fff]+", text):
        result.extend(word[index:index + 2] for index in range(len(word) - 1))
    return Counter(result)


def overview(application, root):
    from app import safe_file, decode_text
    project = application.project(root)
    entries, suggested = [], []
    for item in project["files"]:
        path = item["path"]
        if path.lower().endswith(("readme.md", "pyproject.toml", "package.json", "requirements.txt", "cargo.toml")):
            suggested.append(path)
        if path.endswith(".py") and len(entries) < 150:
            try:
                from pathlib import Path
                text, _ = decode_text(safe_file(Path(project["root"]), path).read_bytes())
                parsed = ast.parse(text)
                definitions = [{"name": node.name, "line": node.lineno, "kind": "class" if isinstance(node, ast.ClassDef) else "function"}
                               for node in ast.walk(parsed) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))][:40]
                imports = sorted({node.names[0].name.split(".")[0] if isinstance(node, ast.Import) else node.module or "" for node in ast.walk(parsed) if isinstance(node, (ast.Import, ast.ImportFrom))})[:30]
                entries.append({"path": path, "definitions": definitions, "imports": imports})
            except (SyntaxError, ValueError, OSError):
                pass
    return {"root": project["root"], "files": len(project["files"]), "truncated": project["truncated"], "suggested": suggested[:12], "python": entries}


def search(application, root, query):
    from pathlib import Path
    from app import AppError, safe_file, decode_text, sha
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise AppError("検索する語句は500文字以内で指定してください。")
    project = application.project(root)
    chunks, total, truncated = [], 0, project["truncated"]
    for item in project["files"]:
        try:
            data = safe_file(Path(project["root"]), item["path"]).read_bytes()
            total += len(data)
            if total > 30 * 1024 * 1024:
                truncated = True; break
            text, _ = decode_text(data)
            lines = text.splitlines()
            for start in range(0, len(lines), 40):
                snippet = "\n".join(lines[start:start + 50])
                frequencies = terms(snippet + " " + item["path"])
                chunks.append({"path":item["path"],"start":start + 1,"end":min(start + 50, len(lines)),"text":snippet[:6000],
                               "sha256":sha(data),"frequencies":frequencies,"length":sum(frequencies.values())})
        except (AppError, OSError):
            continue
    query_terms = terms(query)
    if not query_terms: return {"results":[], "truncated":truncated, "scanned_bytes":total}
    average = sum(chunk["length"] for chunk in chunks) / max(1,len(chunks))
    document_frequency = {term:sum(term in chunk["frequencies"] for chunk in chunks) for term in query_terms}
    for chunk in chunks:
        score = 0
        for term in query_terms:
            frequency = chunk["frequencies"].get(term,0)
            if frequency:
                weight = math.log(1 + (len(chunks)-document_frequency[term]+0.5)/(document_frequency[term]+0.5))
                score += weight * frequency * 2.5/(frequency+1.5*(0.25+0.75*chunk["length"]/max(1,average)))
        chunk["score"] = round(score,4)
    results = []
    for chunk in sorted(chunks,key=lambda value:value["score"],reverse=True):
        if not chunk["score"]: break
        if any(item["path"] == chunk["path"] and item["end"] >= chunk["start"] and chunk["end"] >= item["start"] for item in results): continue
        results.append({key:value for key,value in chunk.items() if key not in {"frequencies","length"}})
        if len(results) == 8: break
    return {"results":results,"truncated":truncated,"scanned_bytes":total,"root":project["root"]}


def citations(content, sources):
    results = []
    for match in re.finditer(r"\[([^\]\n:]{1,200}):L(\d+)(?:-L?(\d+))?\]", content):
        path, start, end = match.group(1), int(match.group(2)), int(match.group(3) or match.group(2))
        source = next((source for source in sources if source["path"] == path and source.get("included_end") and source["start"] <= start <= end <= source["included_end"] - int(source.get("last_line_partial",False))), None)
        results.append({"path":path,"start":start,"end":end,"verified":bool(source)})
    return results[:50]
