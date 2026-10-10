"""A minimal reference adapter and metered disclosure, not a mechanism comparison."""
import copy
import heapq
import json
import re
import socket
import threading
import time

from .common import digest, encode, save


class ReferenceMemory:
    def __init__(self, snapshot=None):
        self.records = copy.deepcopy((snapshot or {}).get("records", []))
        self.sources = copy.deepcopy((snapshot or {}).get("sources", {}))

    def snapshot(self):
        return {"adapter": "reference-v1", "records": self.records, "sources": self.sources}

    def active(self):
        latest = {}
        retired = set()
        for record in self.records:
            latest[record["id"]] = record
            retired.update(record["supersedes"])
        return [r for key, r in latest.items() if key not in retired]

    def register_source(self, source_id, text):
        if source_id in self.sources and self.sources[source_id]["sha256"] != digest(text):
            raise ValueError("immutable source id cannot be overwritten")
        self.sources[source_id] = {"text": text, "sha256": digest(text)}
        return {"id": source_id, "sha256": digest(text)}

    def source_handles(self):
        return [{"id": key, "sha256": source["sha256"]} for key, source in self.sources.items()]

    def select_briefs(self, byte_budget, k=10):
        selected, omitted = [], []
        for record in heapq.nlargest(k, self.active(), key=lambda r: (r["sequence"], r["id"])):
            brief = {key: record[key] for key in ("id", "version", "brief", "category", "level", "conditions", "limits")}
            # Account for the entire JSON array, including envelopes and separators.
            if len(encode(selected + [brief]).encode()) <= byte_budget:
                selected.append(brief)
            else:
                omitted.append(record["id"])
        return selected, omitted

    def commit(self, proposal, episode_id):
        """Atomically validate provenance/versions/acyclic support; semantic truth needs review."""
        notes = proposal.get("notes") if isinstance(proposal, dict) else None
        if not isinstance(notes, list) or len(notes) > 3:
            return {"accepted": 0, "errors": ["notes must be a list of at most 3"]}
        previous = {r["id"]: r for r in self.active()}
        staged, identifiers = [], set()
        try:
            for index, raw in enumerate(notes):
                if not isinstance(raw, dict):
                    raise ValueError("note must be an object")
                note = copy.deepcopy(raw)
                identifier = note.get("id") or episode_id + ":n" + str(index+1)
                if identifier in identifiers:
                    raise ValueError("duplicate note id")
                identifiers.add(identifier)
                old = previous.get(identifier)
                expected = note.get("expected_version")
                if type(expected) is not int or expected != (old["version"] if old else 0):
                    raise ValueError("stale or invalid expected_version")
                for key, limit in (("brief", 700), ("content", 6000), ("conditions", 1400), ("limits", 1400)):
                    if not isinstance(note.get(key), str) or not note[key].strip() or len(note[key].encode()) > limit:
                        raise ValueError("missing or oversized " + key)
                category = note.get("category")
                if not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*", category):
                    raise ValueError("invalid category path")
                level = note.get("level")
                if type(level) is not int or not 0 <= level <= 4:
                    raise ValueError("invalid abstraction level")
                refs = note.get("source_refs")
                if not isinstance(refs, list) or not refs:
                    raise ValueError("missing actual source")
                for ref in refs:
                    if not isinstance(ref, dict) or ref.get("id") not in self.sources:
                        raise ValueError("unresolved source")
                    if self.sources[ref["id"]]["sha256"] != ref.get("sha256"):
                        raise ValueError("source hash mismatch")
                for key in ("support_ids", "links", "supersedes"):
                    ids = note.get(key)
                    if not isinstance(ids, list) or any(not isinstance(x, str) or x not in previous or x == identifier for x in ids):
                        raise ValueError("unresolved/self " + key)
                    if len(ids) != len(set(ids)):
                        raise ValueError("duplicate " + key)
                if level == 0 and note["support_ids"]:
                    raise ValueError("atomic note cannot abstract other notes")
                if level > 0:
                    if len(note["support_ids"]) < 2:
                        raise ValueError("abstraction needs two prior supports")
                    for sid in note["support_ids"]:
                        support = previous[sid]
                        if support["level"] >= level or not (support["category"] == category or support["category"].startswith(category+"/")):
                            raise ValueError("abstraction level/category conflicts")
                note["support_versions"] = {sid: previous[sid]["version"] for sid in note["support_ids"]}
                note.update(id=identifier, version=(old["version"]+1 if old else 1),
                            episode_id=episode_id, sequence=len(self.records)+index)
                staged.append(note)
            # Supports bind to immutable historical versions, so later edits don't rewrite their basis.
            self.records.extend(staged)
            return {"accepted": len(staged), "errors": [], "semantic_validation": "requires_independent_review"}
        except (ValueError, TypeError, KeyError) as exc:
            return {"accepted": 0, "errors": [str(exc)]}


class DisclosureServer:
    """Memory stays outside worker FS; even direct socket calls retain caps and logging."""
    def __init__(self, workspace, memory, byte_budget, log_path, enabled=True):
        self.path = str(workspace / ".memory.sock")
        self.memory = memory
        self.budget = byte_budget
        self.log_path = log_path
        self.enabled = enabled
        self.events = []
        self.stop = threading.Event()
        self.sock = None
        self.thread = None

    def __enter__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(self.path)
        self.sock.listen(4)
        self.sock.settimeout(0.1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        return self

    def _serve(self):
        while not self.stop.is_set() and len(self.events) < 128:
            try:
                connection, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(2)
                started = time.monotonic()
                request = {}
                try:
                    data = b""
                    while b"\n" not in data and len(data) < 8192:
                        chunk = connection.recv(8192-len(data))
                        if not chunk:
                            break
                        data += chunk
                    if len(data) >= 8192:
                        raise ValueError("request too large")
                    request = json.loads(data.split(b"\n")[0])
                    payload = self._dispatch(request)
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    payload = {"error": str(exc)}
                wire = (encode(payload)+"\n").encode()
                success = "error" not in payload
                if success and len(wire) > self.budget:
                    payload = {"error": "disclosure budget exhausted; no partial unit returned"}
                    wire = (encode(payload)+"\n").encode()
                    success = False
                if len(wire) > self.budget:
                    wire = b""
                    success = False
                self.budget -= len(wire)
                self.events.append({"request": request, "response": payload, "bytes": len(wire),
                                    "budget_remaining": self.budget, "success": success,
                                    "elapsed_ms": (time.monotonic()-started)*1000})
                save(self.log_path, self.events)
                try:
                    connection.sendall(wire)
                except OSError:
                    pass
        self.sock.close()

    def _dispatch(self, request):
        if not self.enabled:
            return {"error": "cross-task memory disabled in this trial"}
        op = request["op"]
        active = self.memory.active()
        if op == "read":
            version = request.get("version")
            pool = self.memory.records if type(version) is int else active
            row = next((r for r in pool if r["id"] == request.get("id") and (version is None or r["version"] == version)), None)
            return {"record": row} if row else {"error": "unknown active memory id"}
        if op == "source":
            source = self.memory.sources.get(request.get("id"))
            if not source:
                return {"error": "unknown source id"}
            offset = request.get("offset", 0)
            if type(offset) is not int or offset < 0:
                return {"error": "invalid source offset"}
            text = source["text"]
            chunk = text[offset:offset+2000]
            return {"id": request["id"], "sha256": source["sha256"], "offset": offset,
                    "text": chunk, "next_offset": offset+len(chunk) if offset+len(chunk) < len(text) else None}
        if op in ("list", "search"):
            offset = request.get("offset", 0)
            if type(offset) is not int or offset < 0:
                return {"error": "invalid catalogue offset"}
            query = str(request.get("query", "")).lower().split()
            matches = [r for r in active if all(term in (r["brief"]+" "+r["category"]).lower() for term in query)]
            rows = [{key: r[key] for key in ("id", "version", "brief", "category", "level")} for r in matches[offset:offset+8]]
            return {"items": rows, "next_offset": offset+8 if offset+8 < len(matches) else None}
        return {"error": "unknown operation"}

    def __exit__(self, *_):
        self.stop.set()
        self.sock.close()
        self.thread.join(timeout=3)
        from pathlib import Path
        Path(self.path).unlink(missing_ok=True)
        save(self.log_path, self.events)


CLIENT = '''import argparse, json, socket
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('op', choices=['list', 'search', 'read', 'source'])
p.add_argument('--id'); p.add_argument('--version', type=int); p.add_argument('--query', default=''); p.add_argument('--offset', type=int, default=0)
a = p.parse_args()
with socket.socket(socket.AF_UNIX) as s:
    s.connect(str(Path(__file__).with_name('.memory.sock')))
    s.sendall((json.dumps(vars(a))+'\\n').encode())
    response = b''
    while True:
        chunk = s.recv(65536)
        if not chunk: break
        response += chunk
    print(response.decode(), end='')
'''
