"""Sync between the matcher and the live review page's database.

The page stores one document per bank line in the `lines` collection and the latest
run summary in `runs/latest`. People confirm, reject or mark lines on the page;
the daily task carries those decisions back here before matching.

  pull  <export dir>   apply page decisions (confirmed / rejected / not job-related)
                       from an ArtifactData export of the `lines` collection to
                       state/decisions.json
  push  [<export dir>] turn output/results.json into ArtifactData batch writes:
                       one JSON file per document in output/db_docs/ and a manifest
                       of batches (max 50 writes each) in output/db_writes.json

Usage:  python -m bjm.sync pull state/db_export
        python -m bjm.sync push state/db_export
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import state as st
from .common import OUTPUT, ROOT

DECIDED = {"confirmed", "rejected", "not_job"}


def read_export(export_dir: Path) -> dict[str, dict]:
    """{doc_id: {"data": {...}, "version": n}} from an ArtifactData `out_dir` export.

    The exported files hold document bodies only; versions (needed to pin updates) come
    from versions.json, {"lines": {"<doc id>": n}, "runs": {"latest": n}}, which the daily
    task writes from the listing's (id, version) pairs.
    """
    docs = {}
    if not export_dir.exists():
        return docs
    vfile = export_dir / "versions.json"
    versions = json.loads(vfile.read_text(encoding="utf-8")) if vfile.exists() else {}
    for f in export_dir.rglob("*.json"):
        if f.name == "versions.json":
            continue
        raw = json.loads(f.read_text(encoding="utf-8"))
        data = raw.get("data", raw) if isinstance(raw, dict) else {}
        doc_id = f.stem
        coll = "runs" if "runs" in f.parts else "lines"
        version = versions.get(coll, {}).get(doc_id) or (raw.get("version") if isinstance(raw, dict) else None)
        if "runs" in f.parts and doc_id == "latest":
            docs["__runs_latest__"] = {"data": data, "version": version}
        elif "lines" in f.parts or data.get("kind") == "line":
            docs[doc_id] = {"data": data, "version": version}
    return docs


def pull(export_dir: Path) -> dict:
    state = st.load()
    applied = {"confirmed": 0, "rejected": 0, "not_job": 0}
    for doc_id, doc in read_export(export_dir).items():
        d = doc["data"]
        status = d.get("status")
        if status not in DECIDED or d.get("synced"):
            continue
        cands = {c["key"]: c for c in d.get("candidates", [])}
        if status == "confirmed":
            c = cands.get(d.get("chosenKey"))
            if not c:
                continue
            items = [i["id"] for i in c["items"]]
            for b in c["bank_ids"]:
                state["matches"][b] = {"items": items, "bank_group": c["bank_ids"], "side": d.get("side", "customer"),
                                       "source": "confirmed"}
                state["rejected"].pop(b, None)
        elif status == "rejected":
            m = state["matches"].get(doc_id)
            if m and m.get("source") == "auto":
                del state["matches"][doc_id]
            keys = [d["chosenKey"]] if d.get("chosenKey") else list(cands)
            rej = state["rejected"].setdefault(doc_id, [])
            rej += [k for k in keys if k not in rej]
        elif status == "not_job":
            state["matches"].pop(doc_id, None)
            state["ignored"][doc_id] = "marked not job-related on the review page"
        applied[status] += 1
    st.save(state)
    return applied


def push(export_dir: Path | None) -> dict:
    res = json.loads((OUTPUT / "results.json").read_text(encoding="utf-8"))
    existing = read_export(export_dir) if export_dir else {}
    docs_dir = OUTPUT / "db_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for f in docs_dir.glob("*.json"):
        f.unlink()

    writes = []
    in_run = set()
    for line in res["lines"]:
        b = line["id"]
        in_run.add(b)
        status = {"auto": "auto", "ignored": "ignored"}.get(line["group"], "open")
        prev = existing.get(b, {}).get("data", {})
        doc = dict(line, kind="line", status=status, chosenKey=None, synced=False, runDate=res["as_of"],
                   note=prev.get("note") if prev.get("candidates") == line["candidates"] else None)
        path = docs_dir / f"{b}.json"
        path.write_text(json.dumps(doc, default=str), encoding="utf-8")
        w = {"op": "set", "collection": "lines", "doc_id": b, "file_path": path.relative_to(ROOT).as_posix()}
        if b in existing and existing[b]["version"]:
            w["if_version"] = existing[b]["version"]
        writes.append(w)

    # decided lines that left the open set: mark them synced so they show as settled
    for b, doc in existing.items():
        d = doc["data"]
        if b not in in_run and d.get("status") in DECIDED and not d.get("synced"):
            path = docs_dir / f"{b}.synced.json"
            path.write_text(json.dumps({"synced": True}), encoding="utf-8")
            writes.append({"op": "update", "collection": "lines", "doc_id": b, "file_path": path.relative_to(ROOT).as_posix(),
                           "if_version": doc["version"]})

    summary = {k: res[k] for k in ("as_of", "generated_at", "business", "stats", "model")}
    acc = OUTPUT / "accuracy.json"
    if acc.exists():
        summary["sample_accuracy"] = json.loads(acc.read_text(encoding="utf-8"))
    path = docs_dir / "_run_latest.json"
    path.write_text(json.dumps(summary, default=str), encoding="utf-8")
    run_write = {"op": "set", "collection": "runs", "doc_id": "latest", "file_path": path.relative_to(ROOT).as_posix()}
    vfile = export_dir / "versions.json" if export_dir else None
    run_version = (existing.get("__runs_latest__", {}).get("version")
                   or (json.loads(vfile.read_text()).get("runs", {}).get("latest") if vfile and vfile.exists() else None))
    if run_version:
        run_write["if_version"] = run_version
    writes.append(run_write)

    batches = [writes[i:i + 50] for i in range(0, len(writes), 50)]
    manifest = {"artifact_collections": ["lines", "runs"], "batches": batches}
    (OUTPUT / "db_writes.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return {"writes": len(writes), "batches": len(batches)}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("pull", "push"):
        print(__doc__)
        sys.exit(1)
    export = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if export and not export.is_absolute():
        export = ROOT / export
    if sys.argv[1] == "pull":
        print("Applied page decisions:", pull(export or ROOT / "state" / "db_export"))
    else:
        print("Prepared database writes:", push(export))


if __name__ == "__main__":
    main()
