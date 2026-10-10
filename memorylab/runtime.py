"""Registered deterministic memory components and metered progressive disclosure."""
import copy
import json
import re
import time
import unicodedata
from pathlib import Path

from .common import save
from .contracts import (ContractError, bounded_text, canonical, exact_object, identifier,
                        namespace, reference, require, sha256, timestamp, validate_spec)


def terms(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    latin = re.findall(r"[a-z0-9_]+", text)
    han = re.findall(r"[\u3400-\u9fff]+", text)
    return set(latin + [run[i:i+2] for run in han for i in range(max(1, len(run)-1))])


class OfflineByteTokenizer:
    id = "utf8-byte-offline-v0"
    live_qualified = False

    def count(self, wire):
        return len(wire)


def read_runtime(path=None):
    from .kernel_cli import read_json
    return validate_runtime(read_json(path or Path(__file__).resolve().parent.parent / "configs/runtime_v0.json"))


def validate_runtime(config):
    exact_object(config, {"schema_version", "status", "hypothesis", "brief_tokens", "detail_tokens", "brief_bytes", "detail_bytes",
        "max_requests", "credit_ema_alpha", "index_min_support", "index_max_support", "index_max_depth", "reflection_max_annotations",
        "reflection_prompt", "reflection_model", "tokenizer", "route_terms", "research_ref_policy"}, "invalid_runtime_config")
    require(config["schema_version"] == "runtime-contract-v0", "unsupported_runtime_schema")
    require(config["research_ref_policy"] == "metered_exact_refs_in_research_namespace_only_v1", "unsupported_research_ref_policy")
    for key in ("brief_tokens", "detail_tokens", "brief_bytes", "detail_bytes", "max_requests", "index_max_support", "reflection_max_annotations"):
        require(type(config[key]) is int and config[key] > 0, "invalid_runtime_limit")
    require(type(config["credit_ema_alpha"]) is float and 0 < config["credit_ema_alpha"] <= 1, "invalid_ema_alpha")
    require(type(config["index_min_support"]) is int and config["index_min_support"] >= 2 and
            config["index_max_support"] >= config["index_min_support"], "invalid_index_support_limit")
    require(type(config["index_max_depth"]) is int and config["index_max_depth"] == 1, "recursive_index_denied")
    exact_object(config["tokenizer"], {"id", "live_qualified"}, "invalid_tokenizer_config")
    identifier(config["tokenizer"]["id"])
    require(type(config["tokenizer"]["live_qualified"]) is bool, "invalid_tokenizer_qualification")
    require(config["tokenizer"]["id"] != OfflineByteTokenizer.id or not config["tokenizer"]["live_qualified"], "offline_tokenizer_not_live")
    bounded_text(config["reflection_prompt"], 16000, "invalid_reflection_prompt")
    require(config["reflection_model"] is None or type(config["reflection_model"]) is str, "invalid_reflection_model")
    require(type(config["route_terms"]) is dict, "invalid_route_terms")
    for route, words in config["route_terms"].items():
        require(type(route) is str and type(words) is list and bool(words), "invalid_route_terms")
        for word in words:
            bounded_text(word, 128, "invalid_route_term")
    return copy.deepcopy(config)


class UsageStore:
    """Controller-only events; outcome association is explicitly not causal credit."""
    def __init__(self, cards):
        self.cards = cards
        self.ledger = cards.ledger

    def record(self, request_id, ns, episode, event_type, memory_ref=None, outcome=None, evidence_ref=None):
        identifier(request_id)
        identifier(episode)
        namespace(ns)
        require(ns != "control", "invalid_usage_namespace")
        require(event_type in ("auto_load", "open", "claimed_use", "behavior_link", "independent_outcome"), "invalid_usage_type")
        if memory_ref is not None:
            reference(memory_ref, {"card", "index"})
            require(memory_ref["namespace"] == ns, "cross_namespace_usage")
            self.ledger.get(memory_ref)
        require(event_type == "independent_outcome" or memory_ref is not None, "missing_usage_memory")
        require(event_type == "independent_outcome" or outcome is None, "unexpected_usage_outcome")
        if event_type == "independent_outcome":
            require(outcome in ("success", "failure", "unknown"), "invalid_usage_outcome")
            require(memory_ref is None, "unexpected_outcome_memory_ref")
        if event_type in ("behavior_link", "independent_outcome"):
            require(evidence_ref is not None, "missing_usage_evidence")
            source = self.cards.evidence.read(evidence_ref, "extractor", ns, self.ledger.head())
            require(source["event_type"] == ("visible_outcome" if event_type == "independent_outcome" else "tool_result"), "invalid_usage_evidence_type")
            require(source["episode"] == episode, "usage_episode_mismatch")
        else:
            require(evidence_ref is None, "unexpected_usage_evidence")
        request = {"namespace": ns, "episode": episode, "event_type": event_type, "memory_ref": memory_ref,
                   "outcome": outcome, "evidence_ref": evidence_ref}
        with self.ledger.mutation():
            prior = self.ledger.receipt(request_id, request)
            if prior:
                return prior
            if event_type == "behavior_link":
                require(any(e["data"].get("type") == "usage" and e["data"]["usage"]["episode"] == episode and
                    e["data"]["usage"]["event_type"] == "open" and e["data"]["usage"]["memory_ref"] == memory_ref
                    for e in self.ledger.events(ns)), "behavior_without_open")
            require(event_type != "independent_outcome" or not any(e["data"].get("type") == "usage" and
                e["data"]["usage"]["episode"] == episode and e["data"]["usage"]["event_type"] == "independent_outcome"
                for e in self.ledger.events(ns)), "duplicate_episode_outcome")
            sequence = self.ledger.append_event("usage-"+sha256({"request_id": request_id}), ns, {"type": "usage", "usage": request})
            return self.ledger.commit_receipt(request_id, request, {"status": "usage_recorded", "sequence": sequence}, ns)

    def visible(self, ns, cutoff):
        self.cards.evidence._access("extractor", ns, cutoff)
        result = []
        for event in self.ledger.events(ns, cutoff):
            if event["data"].get("type") == "usage":
                value = event["data"]["usage"]
                result.append({"sequence": event["sequence"], **copy.deepcopy(value)})
        return result


def credit_scores(events, alpha):
    scores, opened, linked, outcomes = {}, {}, {}, set()
    for event in events:
        episode = event["episode"]
        ref = event["memory_ref"]
        key = sha256(ref) if ref else None
        kind = event["event_type"]
        if kind == "open":
            opened.setdefault(episode, set()).add(key)
        elif kind == "behavior_link":
            linked.setdefault(episode, set()).add(key)
        elif kind == "independent_outcome":
            require(episode not in outcomes, "duplicate_episode_outcome")
            outcomes.add(episode)
            if event["outcome"] == "unknown":
                continue
            value = 1.0 if event["outcome"] == "success" else -1.0
            for key in opened.get(episode, set()) & linked.get(episode, set()):
                scores[key] = (1-alpha)*scores.get(key, 0.0) + alpha*value
    return scores


class RuntimeBuilder:
    def __init__(self, cards, registry, config=None):
        self.cards, self.registry = cards, registry
        self.config = validate_runtime(config if config is not None else read_runtime())
        require(set(self.config["route_terms"]) <= set(cards.kernel["taxonomy"]), "unregistered_route")
        self.usage = UsageStore(cards)

    def build(self, ns, cutoff, spec_ref, derived_refs=None):
        started = time.monotonic()
        reference(spec_ref, {"memory"})
        spec = self.registry.resolve(spec_ref)
        corpus = self.cards.corpus(ns, cutoff)
        latest, retired = {}, set()
        for entry in corpus["cards"]:
            ref, card = entry["ref"], entry["card"]
            latest[card["id"]] = entry
            retired.update(canonical(r) for r in card["supersedes"])
        valid = {canonical(r) for r, _ in self.cards.active(ns, cutoff)}
        leaves = [e for e in latest.values() if e["card"]["status"] == "active" and
                  (spec["config"]["invalidation.type"] == "none" or canonical(e["ref"]) not in retired)]
        items = []
        for entry in leaves:
            ref, card = entry["ref"], entry["card"]
            items.append({"id": card["id"], "version": card["version"], "type_hint": card["type_hint"], "brief": card["brief"],
                "detail": card["detail"], "level": 0, "support_refs": [], "ref": ref, "timestamp": card["timestamp"]})
        indices = []
        if derived_refs is None and spec["config"]["consolidation.type"] == "single_index":
            groups = {}
            for entry in leaves:
                if canonical(entry["ref"]) not in valid:
                    continue
                group = "root" if spec["config"]["organizer.type"] == "flat" else entry["card"]["type_hint"].split("/")[0]
                groups.setdefault(group, []).append(entry)
            for group, entries in sorted(groups.items()):
                entries.sort(key=lambda e: (-timestamp(e["card"]["timestamp"]).timestamp(), e["ref"]["id"]))
                entries = entries[:self.config["index_max_support"]]
                if len(entries) < self.config["index_min_support"]:
                    continue
                refs = [e["ref"] for e in entries]
                hints = [e["card"]["type_hint"].split("/") for e in entries]
                common = []
                for parts in zip(*hints):
                    if len(set(parts)) != 1:
                        break
                    common.append(parts[0])
                hint = "/".join(common) or "root"
                index = {"kind": "index", "id": "index-"+sha256({"supports": refs, "type_hint": hint}), "version": 1,
                         "namespace": ns, "type_hint": hint, "brief": "Navigation for " + hint + ": " + " | ".join(e["card"]["brief"] for e in entries),
                         "detail": "Navigation only; open each supported card for its applicability and exceptions.\n" +
                             "\n".join(e["ref"]["id"] + ": " + e["card"]["brief"] for e in entries),
                         "level": 1, "support_refs": refs, "timestamp": max((e["card"]["timestamp"] for e in entries), key=timestamp)}
                indices.append(index)
        # Fixed, provenance-preserving reflection adds navigation annotations only.
        annotations = []
        trigger = spec["config"]["reflection.trigger"]
        for item in items if derived_refs is None else []:
            card = next(e["card"] for e in leaves if e["ref"] == item["ref"])
            active_trigger = trigger == "failure" and card["outcome"] == "failure"
            if trigger == "feedback":
                active_trigger = any(self.cards.evidence.read(r, "reflector", ns, cutoff)["event_type"] == "explicit_feedback" for r in card["evidence_refs"])
            if active_trigger and len(annotations) < self.config["reflection_max_annotations"]:
                note = "This card is linked to " + ("an observed failure" if trigger == "failure" else "explicit feedback") + "; check its original applicability and exceptions."
                annotations.append({"support": item["ref"], "text": note, "generator": "bounded-navigation-v0"})
                item["detail"] += "\n\nNavigation annotation: " + note
        if derived_refs is not None:
            from .derived import DerivedStage
            require(type(derived_refs) is dict and set(derived_refs)<= {"consolidation","reflector"},"invalid_derived_refs")
            stage=DerivedStage(self.cards,self.registry,self.config)
            for role in ("consolidation","reflector"):
                require(not stage.prepare(role,ns,cutoff,spec_ref)["payload"]["scheduled"] or role in derived_refs,"missing_scheduled_derived_batch")
            for role,ref in derived_refs.items():
                batch=stage.resolve(ref,role,ns,cutoff,spec_ref)
                for generated in batch["items"]:
                    if role=="consolidation":
                        index={"kind":"index","id":"index-"+sha256({"item":generated,"batch":ref}),
                            "version":1,"namespace":ns,**generated,"level":1,"generator_batch":ref,
                            "timestamp":max((self.cards.ledger.get(r)["timestamp"] for r in generated["support_refs"]),key=timestamp)}
                        indices.append(index)
                    else:
                        note={**generated,"generator":"bounded-generated-reflection-v0","generator_batch":ref};annotations.append(note)
                        item=next(i for i in items if i["ref"]==generated["support"])
                        item["detail"]+="\n\nDerived reflection: "+generated["text"]
        events = self.usage.visible(ns, cutoff)
        scores = credit_scores(events, self.config["credit_ema_alpha"]) if spec["config"]["credit.type"] == "outcome_ema" else {}
        with self.cards.ledger.mutation():
            for index in indices:
                ref = self.cards.ledger.put(index, ns)
                items.append({**index, "ref": ref})
            identity={"corpus":corpus["sha256"],"spec":spec_ref,"runtime":self.config,"usage":events}
            if derived_refs is not None:identity["derived_refs"]=derived_refs
            value = {"kind": "projection", "id": "projection-"+sha256(identity),
                "version": 1, "namespace": ns, "corpus_sha256": corpus["sha256"], "spec": spec_ref, "runtime": self.config,
                "spec_config": spec["config"], "items": items, "credit_scores": scores, "annotations": annotations, "usage_sha256": sha256(events)}
            if derived_refs is not None:value["derived_refs"]=derived_refs
            projection_ref = self.cards.ledger.put(value, ns)
        return {"projection_ref": projection_ref, "projection": value, "maintenance": {"model_calls": 0,
            "elapsed_seconds": time.monotonic()-started, "source": "canonical_rebuild", "semantic_validation": "navigation_only" if derived_refs is None else "unverified_derived_semantics"}}


class RuntimeSession:
    """Only auto briefs and open(id); a caller cannot pick a corpus or bypass caps."""
    def __init__(self, ledger, projection_ref, task_text, tokenizer=None, persist=None, enabled=True):
        reference(projection_ref, {"projection"})
        projection = ledger.get(projection_ref)
        self.projection = copy.deepcopy(projection)
        self.config = validate_runtime(projection["runtime"])
        self.research_refs = projection["namespace"] in ("research/evaluator", "research/proposer")
        self.tokenizer = tokenizer or OfflineByteTokenizer()
        require(self.tokenizer.id == self.config["tokenizer"]["id"] and
                self.tokenizer.live_qualified == self.config["tokenizer"]["live_qualified"], "tokenizer_identity_mismatch")
        bounded_text(task_text, 2_000_000, "invalid_task_text")
        self.task_text = task_text
        self.persist = persist or (lambda events: None)
        self.enabled = enabled
        require(type(enabled) is bool, "invalid_runtime_enabled")
        self.items = {item["id"]: item for item in projection["items"]}
        require(len(self.items) == len(projection["items"]), "duplicate_projection_id")
        self.events, self.disclosed = [], set()
        self.remaining = {phase: {"tokens": self.config[phase+"_tokens"], "bytes": self.config[phase+"_bytes"]} for phase in ("brief", "detail")}
        self.loaded = False

    def _cost(self, payload):
        wire = canonical(payload)+b"\n"
        count = self.tokenizer.count(wire)
        require(type(count) is int and count >= 0, "invalid_token_count")
        return wire, count

    def _fits(self, phase, payload):
        wire, tokens = self._cost(payload)
        return len(wire) <= self.remaining[phase]["bytes"] and tokens <= self.remaining[phase]["tokens"]

    def _emit(self, phase, operation, request, payload, ids=(), omissions=()):
        if not self._fits(phase, payload):
            payload = {"error": "disclosure_budget_exhausted"}
        wire, tokens = self._cost(payload)
        if not self._fits(phase, payload):
            wire, tokens = b"", 0
        success = bool(wire) and "error" not in payload
        updated = copy.deepcopy(self.remaining)
        updated[phase]["bytes"] -= len(wire)
        updated[phase]["tokens"] -= tokens
        event = {"sequence": len(self.events)+1, "operation": operation, "request": request, "response": payload if wire else None,
                 "wire_sha256": __import__("hashlib").sha256(wire).hexdigest(), "bytes": len(wire), "tokens": tokens,
                 "tokenizer": self.config["tokenizer"], "remaining": updated, "success": success,
                 "memory_refs": [self.items[id_]["ref"] for id_ in ids] if success else [], "omissions": list(omissions)}
        # Persist before disclosure. Failures cannot release unmetered content.
        self.persist(copy.deepcopy(self.events+[event]))
        self.events.append(event)
        self.remaining = updated
        if success:
            self.disclosed.update(ids)
        return wire

    def _rank(self):
        query = terms(self.task_text)
        routes = [r for r, words in self.config["route_terms"].items() if any(terms(w) & query for w in words)]
        behavior = self.projection["spec_config"]
        local, global_ = [], []
        for id_, item in self.items.items():
            lexical = len(query & terms(item["brief"]+" "+item["detail"]))
            hint = item["type_hint"]
            shared = []
            for route in routes:
                count = 0
                for a, b in zip(hint.split("/"), route.split("/")):
                    if a != b:
                        break
                    count += 1
                shared.append(count)
            tree = max(shared, default=0)
            method = behavior["retrieval.type"]
            relevance = lexical if method == "lexical" else tree if method == "tree" else 2*tree+lexical
            credit = self.projection["credit_scores"].get(sha256(item["ref"]), 0.0)
            time_ = timestamp(item["timestamp"]).timestamp()
            if relevance > 0:
                local.append(((-relevance, -credit, -time_, id_), id_))
            # Stable zero-score tie uses time; no causal usefulness inferred.
            global_.append(((-credit, -time_, id_), id_))
        return ([i for _, i in sorted(global_)][:behavior["loader.global_briefs"]],
                [i for _, i in sorted(local)][:behavior["loader.local_briefs"]])

    def briefs(self):
        require(not self.loaded, "brief_already_disclosed")
        require(len(self.events) < self.config["max_requests"], "runtime_request_limit")
        selected, omitted = [], []
        if self.enabled:
            global_, local = self._rank()
            for id_ in dict.fromkeys(global_+local):
                item = self.items[id_]
                brief = {k: item[k] for k in ("id", "version", "type_hint", "brief", "level")}
                if self.research_refs:
                    brief["evidence_ref"] = copy.deepcopy(item["ref"])
                if self._fits("brief", {"items": selected+[brief]}):
                    selected.append(brief)
                else:
                    omitted.append({"id": id_, "reason": "whole_brief_exceeds_remaining_budget"})
        wire = self._emit("brief", "auto_load", {}, {"items": selected}, [i["id"] for i in selected], omitted)
        self.loaded = True
        return wire

    def open(self, request):
        require(self.loaded, "brief_not_disclosed")
        require(len(self.events) < self.config["max_requests"], "runtime_request_limit")
        try:
            exact_object(request, {"op", "id"}, "invalid_memory_request")
            require(request["op"] == "open_memory", "unsupported_memory_operation")
            identifier(request["id"])
            require(self.enabled and request["id"] in self.disclosed, "undisclosed_memory_denied")
            item = self.items[request["id"]]
            supports = [ref["id"] for ref in item["support_refs"]]
            require(all(id_ in self.items for id_ in supports), "dangling_index")
            payload = {"memory": {**{k: item[k] for k in ("id", "version", "type_hint", "detail", "level")}, "support_ids": supports}}
            if self.research_refs:
                payload["memory"]["evidence_ref"] = copy.deepcopy(item["ref"])
            wire = self._emit("detail", "open", request, payload, [item["id"]])
            if wire and "memory" in json.loads(wire):
                self.disclosed.update(supports)
            return wire
        except ContractError as exc:
            return self._emit("detail", "open", request, {"error": exc.code})


def file_log(path):
    return lambda events: save(Path(path), events)


class RuntimeSocket:
    """Single serialized, controller-owned service. No source/list/search API."""
    def __init__(self, workspace, session):
        import threading
        self.path = Path(workspace) / ".memory.sock"
        self.session = session
        self.stop = threading.Event()
        self.error = None

    def __enter__(self):
        import socket
        import threading
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.path))
        self.sock.listen(4)
        self.sock.settimeout(0.1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        return self

    def _serve(self):
        import socket
        from .kernel_cli import parse_json
        while not self.stop.is_set() and len(self.session.events) < self.session.config["max_requests"]:
            try:
                connection, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(2)
                try:
                    raw = b""
                    while b"\n" not in raw and len(raw) < 8192:
                        chunk = connection.recv(8192-len(raw))
                        if not chunk:
                            break
                        raw += chunk
                    require(len(raw) < 8192 and b"\n" in raw, "invalid_memory_wire")
                    request = parse_json(raw.split(b"\n", 1)[0].decode("utf-8"))
                    wire = self.session.open(request)
                except (ContractError, ValueError, UnicodeError, OSError) as exc:
                    code = exc.code if isinstance(exc, ContractError) else "invalid_memory_wire"
                    try:
                        wire = self.session._emit("detail", "open", {"invalid_wire": True}, {"error": code})
                    except BaseException as log_error:
                        self.error = log_error
                        self.stop.set()
                        break
                except BaseException as exc:
                    self.error = exc
                    self.stop.set()
                    break
                try:
                    connection.sendall(wire)
                except OSError:
                    pass

    def __exit__(self, *_):
        self.stop.set()
        self.sock.close()
        self.thread.join(timeout=3)
        self.path.unlink(missing_ok=True)
        require(not self.thread.is_alive(), "runtime_socket_shutdown_failure")
        if self.error is not None:
            raise self.error


CLIENT = '''import argparse, json, socket
from pathlib import Path
p = argparse.ArgumentParser(description="Expand one already disclosed memory")
p.add_argument("id")
a = p.parse_args()
with socket.socket(socket.AF_UNIX) as s:
    s.connect(str(Path(__file__).with_name(".memory.sock")))
    s.sendall((json.dumps({"op": "open_memory", "id": a.id})+"\\n").encode())
    response = b""
    while True:
        chunk = s.recv(65536)
        if not chunk: break
        response += chunk
    print(response.decode(), end="")
'''
