"""Validated edit batches with rollback, crash recovery and syntax checks."""
from __future__ import annotations
import ast
import difflib
import json
from pathlib import Path
import time
import uuid


def diff_hunks(edit):
    old, new = edit["old"].splitlines(keepends=True), edit["new"].splitlines(keepends=True)
    groups = list(difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_grouped_opcodes(3))
    result = []
    for identifier, group in enumerate(groups):
        lines = []
        for kind, start, end, new_start, new_end in group:
            if kind in {"equal", "delete", "replace"}: lines.extend((" " if kind == "equal" else "-") + line.rstrip("\r\n") for line in old[start:end])
            if kind in {"insert", "replace"}: lines.extend("+" + line.rstrip("\r\n") for line in new[new_start:new_end])
        result.append({"id": identifier, "old_start": group[0][1] + 1, "new_start": group[0][3] + 1, "lines": lines})
    return result


def partial_edit(edit, selected):
    from app import AppError
    if edit.get("create"):
        if selected not in (None, [0]): raise AppError("新規ファイルは全文を確認して作成してください。")
        return edit
    old, new = edit["old"].splitlines(keepends=True), edit["new"].splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    groups = list(matcher.get_grouped_opcodes(3))
    if selected is None: return edit
    if not isinstance(selected, list) or not selected or any(type(value) is not int or not 0 <= value < len(groups) for value in selected) or len(set(selected)) != len(selected):
        raise AppError("反映する差分の箇所を選んでください。")
    operations = [operation for index in selected for operation in groups[index] if operation[0] != "equal"]
    result = old[:]
    for _, start, end, new_start, new_end in sorted(operations, key=lambda value:value[1], reverse=True):
        result[start:end] = new[new_start:new_end]
    return {**edit, "new": "".join(result)}


def syntax_check(path, text):
    if path.suffix.lower() == ".py":
        ast.parse(text, filename=path.name)
        return "Python構文を確認しました。"
    if path.suffix.lower() == ".json":
        json.loads(text)
        return "JSONの形式を確認しました。"
    return "この種類の構文検証はありません。差分を確認してください。"


def apply_batch(application, message_id, indices, hunks=None):
    from app import AppError, MAX_FILE, decode_text, safe_file, safe_create, sha
    if not isinstance(indices, list) or not indices or len(indices) > 12 or any(type(index) is not int or index < 0 for index in indices) or len(set(indices)) != len(indices):
        raise AppError("反映する変更を選択してください。")
    with application.files_lock:
        if (application.store.directory / "batch-journal.json").exists():
            raise AppError("中断された変更の復旧を完了してから反映してください。", 409)
        message = application.store.get_message(message_id)
        edits = message["metadata"].get("edits", [])
        if any(index >= len(edits) for index in indices):
            raise AppError("修正案が不正です。")
        groups = {}
        for index in indices:
            edit = partial_edit(edits[index], (hunks or {}).get(str(index)))
            path = (safe_create if edit.get("create") else safe_file)(Path(edit["root"]), edit["path"])
            groups.setdefault(path, []).append((index, edit))
        batch = uuid.uuid4().hex
        prepared = []
        for path, group in groups.items():
            creating = bool(group[0][1].get("create"))
            if creating and len(group) != 1:
                raise AppError("同じ新規ファイルに複数の提案があります。一つを選択してください。")
            before = b"" if creating else path.read_bytes()
            original, encoding = ("", "utf-8") if creating else decode_text(before)
            spans = []
            for index, edit in group:
                if sha(before) != edit["sha256"] or not creating and original.count(edit["old"]) != 1:
                    raise AppError("生成後にファイルが変わったか、置換箇所が一意ではありません。再生成してください。", 409)
                start = original.index(edit["old"])
                spans.append((start, start + len(edit["old"]), edit["new"]))
            spans.sort()
            if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
                raise AppError("変更箇所が重なっています。一方を選択するか、修正案を作り直してください。", 409)
            changed = original
            for start, end, value in reversed(spans):
                changed = changed[:start] + value + changed[end:]
            try:
                check = syntax_check(path, changed)
                after = changed.encode(encoding)
            except (SyntaxError, ValueError, UnicodeError) as exc:
                raise AppError("変更後の構文または文字コードの検証に失敗しました: " + str(exc)) from exc
            if len(after) > MAX_FILE:
                raise AppError("変更後のファイルが大きすぎます。")
            identifier = uuid.uuid4().hex
            backup = application.store.directory / "backups" / (identifier + ".bak")
            backup.parent.mkdir(exist_ok=True)
            backup.write_bytes(before)
            temporary = path.with_name(path.name + ".localdesk-" + batch + ".tmp")
            prepared.append({"id": identifier, "path": str(path), "backup": str(backup), "before_hash": sha(before), "after_hash": sha(after),
                             "temporary": str(temporary), "after": after, "before": before, "indices": [index for index, _ in group], "check": check, "created_file": creating})
        journal = application.store.directory / "batch-journal.json"
        journal.write_text(json.dumps({"batch": batch, "operation": "apply", "files": [{key:value for key,value in item.items() if key not in {"after", "before"}} for item in prepared]}), encoding="utf-8")
        replaced = []
        try:
            for item in prepared:
                Path(item["path"]).parent.mkdir(parents=True, exist_ok=True)
                Path(item["temporary"]).write_bytes(item["after"])
            with application.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                # Validate once more immediately before replacing any target.
                if any(Path(item["path"]).exists() if item["created_file"] else sha(Path(item["path"]).read_bytes()) != item["before_hash"] for item in prepared):
                    raise AppError("反映直前にファイルが変わりました。再生成してください。", 409)
                for item in prepared:
                    Path(item["temporary"]).replace(item["path"])
                    replaced.append(item)
                    db.execute("INSERT INTO changes(id,path,backup,before_hash,after_hash,undone,created,batch,created_file) VALUES(?,?,?,?,?,0,?,?,?)",
                               (item["id"], item["path"], item["backup"], item["before_hash"], item["after_hash"], time.time(), batch, int(item["created_file"])))
                    for index in item["indices"]:
                        edits[index]["change_id"] = item["id"]
                        edits[index]["batch_id"] = batch
                        edits[index]["undone"] = False
                        if hunks and str(index) in hunks:
                            edits[index]["applied_hunks"] = hunks[str(index)]
                db.execute("UPDATE messages SET metadata=? WHERE id=?", (json.dumps(message["metadata"], ensure_ascii=False), message_id))
        except Exception:
            for item in reversed(replaced):
                path = Path(item["path"])
                if sha(path.read_bytes()) == item["after_hash"]:
                    if item["created_file"]:
                        path.unlink()
                    else:
                        rollback = path.with_name(path.name + ".rollback-tmp")
                        rollback.write_bytes(item["before"]); rollback.replace(path)
            raise
        finally:
            for item in prepared:
                Path(item["temporary"]).unlink(missing_ok=True)
            # If an external edit interrupted rollback, keep the journal for recovery.
            with application.store.connect() as db:
                committed = db.execute("SELECT COUNT(*) FROM changes WHERE batch=?", (batch,)).fetchone()[0] == len(prepared)
            if committed or all(not Path(item["path"]).exists() if item["created_file"] else sha(Path(item["path"]).read_bytes()) == item["before_hash"] for item in prepared):
                journal.unlink(missing_ok=True)
        return {"batch_id": batch, "change_id": prepared[0]["id"], "changes": [{"id":item["id"], "path":item["path"], "check":item["check"]} for item in prepared],
                "message": f"{len(prepared)}ファイルの変更を検証し、バックアップを保存して反映しました。"}


def recover(application):
    from app import sha
    journal = application.store.directory / "batch-journal.json"
    if not journal.exists(): return
    try:
        value = json.loads(journal.read_text(encoding="utf-8"))
        with application.store.connect() as db:
            if value.get("operation") == "undo":
                count = sum(bool(db.execute("SELECT undone FROM changes WHERE id=?", (item["id"],)).fetchone()[0]) for item in value["files"])
            else:
                count = db.execute("SELECT COUNT(*) FROM changes WHERE batch=?", (value["batch"],)).fetchone()[0]
        if count == len(value["files"]):
            journal.unlink(); return
        for item in value["files"]:
            path, backup = Path(item["path"]), Path(item["backup"])
            if not backup.resolve().is_relative_to((application.store.directory / "backups").resolve()) or backup.is_symlink():
                raise ValueError("Invalid recovery backup")
            before = backup.read_bytes()
            if sha(before) != item["before_hash"]:
                raise ValueError("Recovery hash mismatch")
            if not path.exists() and item.get("created_file"):
                continue
            if not path.exists() and item.get("target_deleted"):
                path.write_bytes(before)
                continue
            current = path.read_bytes()
            if sha(current) == item["after_hash"]:
                if path.is_symlink(): raise ValueError("Recovery target is a link")
                if item.get("created_file"):
                    path.unlink()
                else:
                    pending = path.with_name(path.name + ".recover-tmp")
                    pending.write_bytes(before); pending.replace(path)
            elif sha(current) != item["before_hash"]:
                raise ValueError("Target changed after interrupted edit")
            Path(item["temporary"]).unlink(missing_ok=True)
        journal.unlink()
        application.notices.append("中断された変更を、バックアップから変更前に戻しました。")
    except (OSError, ValueError, KeyError, TypeError):
        application.notices.append("中断された変更の復旧を完了できませんでした。batch-journal.jsonとバックアップを確認してください。新しい反映は停止しています。")


def undo_batch(application, identifier):
    from app import AppError, sha
    with application.files_lock, application.store.connect() as db:
        journal = application.store.directory / "batch-journal.json"
        if journal.exists(): raise AppError("中断された変更の復旧を完了してから取り消してください。", 409)
        row = db.execute("SELECT * FROM changes WHERE id=?", (identifier,)).fetchone()
        if not row or row["undone"]: raise AppError("取り消せる変更が見つかりません。", 404)
        rows = db.execute("SELECT * FROM changes WHERE batch=? AND undone=0", (row["batch"],)).fetchall() if row["batch"] else [row]
        prepared = []
        for item in rows:
            path, backup = Path(item["path"]), Path(item["backup"])
            if not path.is_file() or path.is_symlink() or path.resolve() != path or sha(path.read_bytes()) != item["after_hash"]:
                raise AppError("反映後にファイルが変わりました。全ファイルを変更せず、取り消しを中止しました。", 409)
            before = backup.read_bytes()
            if sha(before) != item["before_hash"]: raise AppError("バックアップの検証に失敗しました。", 409)
            prepared.append((dict(item), before, path.read_bytes()))
        recovery_files = []
        for item, before, after in prepared:
            copy = application.store.directory / "backups" / (uuid.uuid4().hex + ".bak")
            copy.write_bytes(after)
            recovery_files.append({"id":item["id"], "path":item["path"], "backup":str(copy), "before_hash":sha(after), "after_hash":sha(before),
                                   "temporary":item["path"] + ".undo-tmp", "target_deleted": bool(item["created_file"])})
        journal.write_text(json.dumps({"operation":"undo", "batch": row["batch"], "files":recovery_files}), encoding="utf-8")
        replaced = []
        committed = False
        try:
            for item, before, after in prepared:
                path = Path(item["path"]); pending = path.with_name(path.name + ".undo-tmp")
                if item["created_file"]:
                    path.unlink()
                else:
                    pending.write_bytes(before); pending.replace(path)
                replaced.append((item, after))
                db.execute("UPDATE changes SET undone=1 WHERE id=?", (item["id"],))
            db.commit()
            committed = True
        except Exception:
            for item, after in reversed(replaced):
                if item["created_file"] and not Path(item["path"]).exists() or Path(item["path"]).is_file() and sha(Path(item["path"]).read_bytes()) == item["before_hash"]:
                    Path(item["path"]).write_bytes(after)
            raise
        finally:
            # Keep the journal if any target still needs crash recovery.
            if committed or all(Path(item["path"]).is_file() and sha(Path(item["path"]).read_bytes()) == item["before_hash"] for item in recovery_files):
                journal.unlink(missing_ok=True)
        return {"message": f"{len(prepared)}ファイルを変更前に戻しました。", "batch_id": row["batch"]}
