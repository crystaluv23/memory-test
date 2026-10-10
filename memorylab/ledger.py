"""Controller-only immutable objects and append-only decisions in one transaction."""
import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from .contracts import (ContractError, canonical, identifier, namespace, positive_int,
                        reference, require, sha256)


class Ledger:
    COLUMNS={
        "objects":("kind","id","version","namespace","sha256","payload"),
        "events":("sequence","request_id","namespace","sha256","payload"),
        "receipts":("request_id","request_sha256","namespace","sha256","payload"),
    }

    def __init__(self, path, readonly=False):
        require(type(readonly) is bool,"invalid_ledger_readonly")
        if readonly:
            require(Path(path).is_file() and not Path(path).is_symlink(),"readonly_ledger_missing")
        self.path = Path(path).resolve()
        self.readonly=readonly;self.decode_cache=None
        if readonly:
            require(not any(Path(str(self.path)+suffix).exists() for suffix in ("-wal","-shm","-journal")),"readonly_ledger_unsealed")
            self.db=sqlite3.connect("file:"+quote(str(self.path),safe="/")+"?mode=ro&immutable=1",uri=True,isolation_level=None)
            try:
                self.db.execute("PRAGMA query_only=ON")
                require(self.db.execute("PRAGMA user_version").fetchone()[0]==1,"unsupported_ledger_schema")
                self._validate_schema()
            except BaseException:
                self.db.close();raise
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            version=self.db.execute("PRAGMA user_version").fetchone()[0]
            require(version in (0,1),"unsupported_ledger_schema")
            if version==1:self._validate_schema()
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            if version==0:
                self.db.executescript("""
          BEGIN IMMEDIATE;
          CREATE TABLE IF NOT EXISTS objects (
            kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
            namespace TEXT NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(kind,id,version,namespace));
          CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT UNIQUE NOT NULL, namespace TEXT NOT NULL,
            sha256 TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS receipts (
            request_id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
            namespace TEXT NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL);
          PRAGMA user_version=1;
        """)
                self._validate_schema()
                self.db.execute("COMMIT")
        except BaseException:
            self.db.close();raise
        self.decode_cache=None

    def _validate_schema(self):
        for table,columns in self.COLUMNS.items():
            definition=self.db.execute("SELECT type FROM sqlite_master WHERE name=?",(table,)).fetchone()
            require(definition==("table",),"invalid_ledger_schema")
            actual=self.db.execute("PRAGMA table_info("+table+")").fetchall()
            require(tuple(row[1] for row in actual)==columns,"invalid_ledger_schema")
            expected_types=tuple("INTEGER" if name in ("version","sequence") else "TEXT" for name in columns)
            require(tuple(row[2] for row in actual)==expected_types,"invalid_ledger_schema")
            primary={"objects":(1,2,3,4,0,0),"events":(1,0,0,0,0),"receipts":(1,0,0,0,0)}[table]
            require(tuple(row[5] for row in actual)==primary,"invalid_ledger_schema")

    @contextmanager
    def cached_reads(self,max_bytes):
        self._require_transaction();positive_int(max_bytes,"invalid_ledger_cache_limit")
        if self.decode_cache is not None and self.decode_cache["limit"]==max_bytes:
            yield self
            return
        previous=self.decode_cache;self.decode_cache={"limit":max_bytes,"bytes":0,"values":{}}
        try:yield self
        finally:self.decode_cache=previous

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        require(not self.readonly,"readonly_ledger_write_denied")
        require(not self.db.in_transaction, "nested_transaction")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _require_transaction(self):
        require(not self.readonly,"readonly_ledger_write_denied")
        require(self.db.in_transaction, "transaction_required")

    @contextmanager
    def mutation(self):
        """Join a controller's outer atomic unit; never commit it implicitly."""
        if self.db.in_transaction:
            with self.savepoint():
                yield self
        else:
            with self.transaction():
                yield self

    @contextmanager
    def savepoint(self):
        self._require_transaction()
        name = "controller_unit"
        self.db.execute("SAVEPOINT " + name)
        try:
            yield self
            self.db.execute("RELEASE SAVEPOINT " + name)
        except BaseException:
            self.db.execute("ROLLBACK TO SAVEPOINT " + name)
            self.db.execute("RELEASE SAVEPOINT " + name)
            raise

    def head(self):
        return self.db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]

    def bind_kernel(self, kernel):
        if self.readonly:
            value={'kind':'kernel','id':'kernel-v0','version':1,'contract':kernel}
            ref={k:value[k] for k in ('kind','id','version')};ref.update(namespace='control',sha256=sha256(value))
            require(self.get(ref)==value,'readonly_kernel_mismatch');return ref
        with self.transaction():
            return self.put({"kind": "kernel", "id": "kernel-v0", "version": 1, "contract": kernel}, "control")

    def _decode(self,payload, expected):
        cache=self.decode_cache;key=(expected,payload)
        if cache is not None and key in cache["values"]:return copy.deepcopy(cache["values"][key])
        try:
            value = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise ContractError("corrupt_ledger_json") from exc
        require(sha256(value) == expected, "corrupt_ledger_hash")
        size=len(payload.encode())
        if cache is not None and size<=cache["limit"]-cache["bytes"]:
            cache["values"][key]=copy.deepcopy(value);cache["bytes"]+=size
        return value

    def put(self, value, ns):
        self._require_transaction()
        namespace(ns)
        require(type(value) is dict and {"kind", "id", "version"} <= set(value), "invalid_object_identity")
        identifier(value["kind"])
        identifier(value["id"])
        positive_int(value["version"])
        if "namespace" in value:
            require(value["namespace"] == ns, "object_namespace_mismatch")
        ref = {"kind": value["kind"], "id": value["id"], "version": value["version"],
               "namespace": ns, "sha256": sha256(value)}
        key = (ref["kind"], ref["id"], ref["version"], ns)
        row = self.db.execute("SELECT sha256,payload FROM objects WHERE kind=? AND id=? AND version=? AND namespace=?", key).fetchone()
        if row:
            self._decode(row[1], row[0])
            require(row[0] == ref["sha256"], "immutable_object_conflict")
        else:
            self.db.execute("INSERT INTO objects VALUES(?,?,?,?,?,?)", key + (ref["sha256"], canonical(value).decode()))
        return ref

    def get(self, ref):
        reference(ref)
        key = (ref["kind"], ref["id"], ref["version"], ref["namespace"])
        row = self.db.execute("SELECT sha256,payload FROM objects WHERE kind=? AND id=? AND version=? AND namespace=?", key).fetchone()
        require(row is not None, "unresolved_reference")
        require(row[0] == ref["sha256"], "reference_hash_mismatch")
        value = self._decode(row[1], row[0])
        require((value["kind"], value["id"], value["version"]) == key[:3], "corrupt_object_identity")
        require("namespace" not in value or value["namespace"] == ref["namespace"], "corrupt_object_namespace")
        return value

    def objects(self, kind, ns):
        identifier(kind)
        namespace(ns)
        rows = self.db.execute("SELECT id,version,sha256 FROM objects WHERE kind=? AND namespace=? ORDER BY id,version", (kind, ns)).fetchall()
        return [(ref, self.get(ref)) for id_, version, hash_ in rows for ref in
                [{"kind": kind, "id": id_, "version": version, "sha256": hash_, "namespace": ns}]]

    def receipt(self, request_id, request=None):
        identifier(request_id)
        row = self.db.execute("SELECT request_sha256,sha256,payload FROM receipts WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        value = self._decode(row[2], row[1])
        require(value["request_id"] == request_id, "corrupt_receipt_identity")
        if request is not None:
            require(row[0] == sha256(request), "idempotency_conflict")
        return value

    def commit_receipt(self, request_id, request, result, ns):
        self._require_transaction()
        identifier(request_id)
        namespace(ns)
        require(self.receipt(request_id, request) is None, "duplicate_receipt")
        # Result data is copied; a caller cannot mutate the committed receipt.
        value = {"request_id": request_id, "request_sha256": sha256(request),
                 "namespace": ns, "result": copy.deepcopy(result)}
        self.db.execute("INSERT INTO receipts VALUES(?,?,?,?,?)",
                        (request_id, sha256(request), ns, sha256(value), canonical(value).decode()))
        self.append_event(request_id, ns, {"type": "decision", "receipt": value})
        return value

    def append_event(self, request_id, ns, data):
        self._require_transaction()
        identifier(request_id)
        namespace(ns)
        value = {"request_id": request_id, "namespace": ns, "data": copy.deepcopy(data)}
        row = self.db.execute("SELECT sequence,sha256,payload FROM events WHERE request_id=?", (request_id,)).fetchone()
        if row:
            self._decode(row[2], row[1])
            require(sha256(value) == row[1], "idempotency_conflict")
            return row[0]
        cursor = self.db.execute("INSERT INTO events(request_id,namespace,sha256,payload) VALUES(?,?,?,?)",
                                 (request_id, ns, sha256(value), canonical(value).decode()))
        return cursor.lastrowid

    def events(self, ns=None, cutoff=None):
        if ns is not None:
            namespace(ns)
        require(cutoff is None or (type(cutoff) is int and cutoff >= 0), "invalid_cutoff")
        rows = self.db.execute("SELECT sequence,namespace,sha256,payload FROM events ORDER BY sequence").fetchall()
        result = []
        for sequence, row_ns, hash_, payload in rows:
            if (ns is None or row_ns == ns) and (cutoff is None or sequence <= cutoff):
                value = self._decode(payload, hash_)
                require(value["namespace"] == row_ns, "corrupt_event_namespace")
                result.append({"sequence": sequence, **value})
        return result

    def audit(self):
        """Revalidate every content hash and receipt/event binding without rewriting it."""
        integrity = self.db.execute("PRAGMA integrity_check").fetchall()
        require(integrity == [("ok",)], "sqlite_integrity_failure")
        object_count = 0
        for kind, id_, version, ns, hash_ in self.db.execute("SELECT kind,id,version,namespace,sha256 FROM objects").fetchall():
            self.get({"kind": kind, "id": id_, "version": version, "namespace": ns, "sha256": hash_})
            object_count += 1
        events = self.events()
        by_id = {event["request_id"]: event for event in events}
        receipts = self.db.execute("SELECT request_id,request_sha256,namespace FROM receipts").fetchall()
        for request_id, request_hash, ns in receipts:
            value = self.receipt(request_id)
            require(value["request_sha256"] == request_hash and value["namespace"] == ns, "corrupt_receipt_binding")
            require(request_id in by_id and by_id[request_id]["namespace"] == ns and
                    by_id[request_id]["data"] == {"type": "decision", "receipt": value}, "missing_receipt_event")
        return {"objects": object_count, "events": len(events), "receipts": len(receipts), "integrity": "ok"}
