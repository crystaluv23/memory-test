"""Time-bounded observable evidence and fixed canonical extraction proposals."""
import copy

from .contracts import (ContractError, bounded_text, canonical, enum, exact_object,
                        identifier, namespace, positive_int, read_kernel, reference,
                        references, require, sha256, text_hash, timestamp, validate_kernel)

SOURCE_KEYS = {"schema_version", "kind", "id", "version", "namespace", "event_type",
               "episode", "sequence", "timestamp", "visibility", "text", "sha256"}
CARD_KEYS = {"schema_version", "kind", "id", "version", "namespace", "type_hint", "brief", "detail",
             "evidence_refs", "timestamp", "outcome", "status", "supersedes", "extractor"}
RAW_CARD_KEYS = {"id", "expected_version", "type_hint", "brief", "detail", "evidence_refs", "outcome", "status", "supersedes"}


class EvidenceStore:
    def __init__(self, ledger, kernel=None):
        self.ledger = ledger
        self.kernel = validate_kernel(kernel if kernel is not None else read_kernel())
        self.ledger.bind_kernel(self.kernel)

    def _access(self, role, ns, cutoff):
        namespace(ns)
        require(ns != "control", "controller_namespace_denied")
        require(type(cutoff) is int and cutoff >= 0, "invalid_cutoff")
        require(cutoff <= self.ledger.head(), "future_cutoff")
        require(role in self.kernel["role_source_namespaces"], "invalid_source_role")
        permitted = self.kernel["role_source_namespaces"][role]
        require(ns in permitted or ("task/*" in permitted and ns.startswith("task/")), "source_access_denied")

    def validate_source(self, source):
        exact_object(source, SOURCE_KEYS, "invalid_source_fields")
        require(type(source["schema_version"]) is int and source["schema_version"] == 1, "invalid_source_schema")
        require(source["kind"] == "source", "invalid_source_kind")
        identifier(source["id"])
        identifier(source["episode"])
        require(type(source["version"]) is int and source["version"] == 1, "invalid_source_version")
        namespace(source["namespace"])
        positive_int(source["sequence"], "invalid_source_sequence")
        timestamp(source["timestamp"])
        enum(source["event_type"], self.kernel["source_event_types"], "unregistered_event_type")
        enum(source["visibility"], ["observable", "controller_only"], "invalid_visibility")
        require(source["namespace"] != "control" or source["visibility"] == "controller_only", "control_source_visible")
        bounded_text(source["text"], self.kernel["limits"]["source_text_bytes"], "invalid_source_text")
        require(source["sha256"] == text_hash(source["text"]), "source_text_hash_mismatch")
        return source

    def register(self, request_id, ns, event_type, episode, at, text, visibility="observable"):
        """Trusted controller registration; workers cannot register or label sources."""
        identifier(request_id)
        namespace(ns)
        request = {"namespace": ns, "event_type": event_type, "episode": episode,
                   "timestamp": at, "text": text, "visibility": visibility}
        canonical(request)
        with self.ledger.mutation():
            prior = self.ledger.receipt(request_id, request)
            if prior is not None:
                return prior["result"]["source"]
            # Validate before registering an event; the trial sequence is assigned
            # within this same transaction, never accepted from a worker.
            source = {"schema_version": 1, "kind": "source", "id": "source-" + sha256({"request_id": request_id}),
                      "version": 1, "namespace": ns, "sequence": 1, **{k: request[k] for k in
                      ("event_type", "episode", "timestamp", "text", "visibility")}, "sha256": text_hash(text)}
            self.validate_source(source)
            sequence = self.ledger.append_event("input-" + sha256({"request_id": request_id}), ns,
                {"type": "source_input", "source_request_id": request_id, "request": request})
            source["sequence"] = sequence
            ref = self.ledger.put(source, ns)
            self.ledger.commit_receipt(request_id, request, {"status": "registered", "source": ref}, ns)
            return ref

    def read(self, ref, role, ns, cutoff):
        self._access(role, ns, cutoff)
        reference(ref, {"source"})
        require(ref["namespace"] == ns, "cross_namespace_source")
        source = self.validate_source(self.ledger.get(ref))
        require(source["visibility"] == "observable", "hidden_source_denied")
        require(source["sequence"] <= cutoff, "future_source_denied")
        # Source sequence must bind to the actual registered input, not arbitrary
        # source-shaped data inserted through the generic controller object API.
        events = self.ledger.events(ns, cutoff)
        matches = [e for e in events if e["sequence"] == source["sequence"]]
        expected = {k: source[k] for k in ("namespace", "event_type", "episode", "timestamp", "text", "visibility")}
        require(len(matches) == 1, "source_event_binding_mismatch")
        data = matches[0]["data"]
        request_id = data.get("source_request_id")
        require(type(request_id) is str and data == {"type": "source_input", "source_request_id": request_id, "request": expected}, "source_event_binding_mismatch")
        receipt = self.ledger.receipt(request_id, expected)
        require(receipt is not None and receipt["namespace"] == ns and
                receipt["result"] == {"status": "registered", "source": ref}, "source_event_binding_mismatch")
        return source

    def visible(self, role, ns, cutoff):
        self._access(role, ns, cutoff)
        visible = []
        for ref, source in self.ledger.objects("source", ns):
            self.validate_source(source)
            if source["visibility"] == "observable" and source["sequence"] <= cutoff:
                self.read(ref, role, ns, cutoff)
                visible.append((ref, source))
        return sorted(visible, key=lambda item: (item[1]["sequence"], item[0]["id"]))


class CanonicalCards:
    def __init__(self, evidence):
        self.evidence = evidence
        self.ledger = evidence.ledger
        self.kernel = copy.deepcopy(evidence.kernel)
        self.extractor = {"id": self.kernel["extractor"]["id"], "sha256": sha256(self.kernel["extractor"])}

    def validate(self, card):
        exact_object(card, CARD_KEYS, "invalid_card_fields")
        require(type(card["schema_version"]) is int and card["schema_version"] == 1, "invalid_card_schema")
        require(card["kind"] == "card", "invalid_card_kind")
        identifier(card["id"])
        positive_int(card["version"])
        namespace(card["namespace"])
        require(card["namespace"] != "control", "invalid_card_namespace")
        timestamp(card["timestamp"])
        enum(card["type_hint"], self.kernel["taxonomy"], "unregistered_type_hint")
        enum(card["outcome"], ["success", "failure", "unknown"], "invalid_card_outcome")
        enum(card["status"], ["active", "withdrawn"], "invalid_card_status")
        limits = self.kernel["limits"]
        bounded_text(card["brief"], limits["card_brief_bytes"], "invalid_card_brief")
        bounded_text(card["detail"], limits["card_detail_bytes"], "invalid_card_detail")
        references(card["evidence_refs"], {"source"}, limits["card_evidence_refs"], 1)
        references(card["supersedes"], {"card"}, limits["card_supersedes_refs"])
        exact_object(card["extractor"], {"id", "sha256"}, "invalid_card_extractor")
        require(card["extractor"] == self.extractor, "extractor_identity_mismatch")
        for ref in card["evidence_refs"] + card["supersedes"]:
            require(ref["namespace"] == card["namespace"], "cross_namespace_card")
        require(not any(ref["id"] == card["id"] and ref["version"] == card["version"] for ref in card["supersedes"]), "self_supersession")
        return card

    def _committed(self, ns, cutoff):
        self.evidence._access("extractor", ns, cutoff)
        refs = []
        for event in self.ledger.events(ns, cutoff):
            data = event["data"]
            if data.get("type") == "decision":
                result = data["receipt"]["result"]
                if result.get("status") == "extracted":
                    refs.extend(result["cards"])
        # Access is based on extraction commit time, not only the times of sources.
        return [(ref, self.validate(self.ledger.get(ref))) for ref in refs]

    def active(self, ns, cutoff):
        namespace(ns)
        latest = {}
        retired = set()
        for ref, card in self._committed(ns, cutoff):
            prior = latest.get(card["id"])
            require(prior is None or card["version"] == prior[1]["version"] + 1, "invalid_card_version_chain")
            require(prior is not None or card["version"] == 1, "missing_card_version")
            latest[card["id"]] = (ref, card)
            retired.update(canonical(old) for old in card["supersedes"])
        return [item for item in latest.values() if canonical(item[0]) not in retired and item[1]["status"] == "active"]

    def read(self, ref, role, ns, cutoff):
        self.evidence._access(role, ns, cutoff)
        reference(ref, {"card"})
        require(ref["namespace"] == ns, "cross_namespace_card")
        committed = {canonical(r) for r, _ in self._committed(ns, cutoff)}
        require(canonical(ref) in committed, "future_or_uncommitted_card")
        card = self.validate(self.ledger.get(ref))
        for source_ref in card["evidence_refs"]:
            self.evidence.read(source_ref, role, ns, cutoff)
        return card

    def proposal_evidence(self, ref, role, ns, cutoff):
        reference(ref, {"source", "card", "event"})
        require(ref["kind"] != "event", "unregistered_public_event")
        if ref["kind"] == "source":
            return self.evidence.read(ref, role, ns, cutoff)
        return self.read(ref, role, ns, cutoff)

    def extract(self, request_id, proposal, ns, cutoff, at):
        identifier(request_id)
        self.evidence._access("extractor", ns, cutoff)
        timestamp(at)
        canonical(proposal)
        request = {"proposal": proposal, "namespace": ns, "cutoff": cutoff, "timestamp": at, "extractor": self.extractor}
        with self.ledger.mutation():
            prior = self.ledger.receipt(request_id, request)
            if prior is not None:
                return prior
            try:
                exact_object(proposal, {"cards"}, "invalid_extraction_fields")
                raw_cards = proposal["cards"]
                require(type(raw_cards) is list and len(raw_cards) <= self.kernel["limits"]["extract_cards_per_batch"], "invalid_extraction_count")
                # View before this batch: supersedes cannot target another staged card.
                active = {canonical(ref): (ref, card) for ref, card in self.active(ns, cutoff)}
                all_prior = self._committed(ns, cutoff)
                latest = {}
                for ref, card in all_prior:
                    latest[card["id"]] = (ref, card)
                current_seq = self.ledger.events(ns)
                # Delayed proposals must not append an old-context branch to the
                # same canonical namespace after a newer extraction has committed.
                require(not any(e["sequence"] > cutoff and e["data"].get("type") == "decision" and
                        e["data"]["receipt"]["result"].get("status") == "extracted" for e in current_seq), "stale_extraction_cutoff")
                staged, batch_ids, retired = [], set(), set()
                for index, raw in enumerate(raw_cards):
                    exact_object(raw, RAW_CARD_KEYS, "invalid_extract_card_fields")
                    require(type(raw["expected_version"]) is int and raw["expected_version"] >= 0, "invalid_expected_version")
                    require(type(raw["id"]) is str, "invalid_card_id")
                    id_ = raw["id"] or "card-" + sha256({"namespace": ns, "request_id": request_id, "index": index})
                    identifier(id_)
                    require(id_ not in batch_ids, "duplicate_card_id")
                    batch_ids.add(id_)
                    old = latest.get(id_)
                    require(raw["expected_version"] == (old[1]["version"] if old else 0), "stale_card_version")
                    if old:
                        require(canonical(old[0]) in active, "inactive_card_revision")
                    card = {"schema_version": 1, "kind": "card", "id": id_, "version": raw["expected_version"] + 1,
                            "namespace": ns, "timestamp": at, "extractor": self.extractor,
                            **{k: copy.deepcopy(raw[k]) for k in ("type_hint", "brief", "detail", "evidence_refs", "outcome", "status", "supersedes")}}
                    self.validate(card)
                    for ref in card["evidence_refs"]:
                        source = self.evidence.read(ref, "extractor", ns, cutoff)
                        require(timestamp(source["timestamp"]) <= timestamp(at), "future_source_timestamp")
                    for ref in card["supersedes"]:
                        self.read(ref, "extractor", ns, cutoff)
                        key = canonical(ref)
                        require(key in active, "inactive_supersedes")
                        require(key not in retired, "duplicate_batch_supersession")
                        require(ref["id"] != id_, "revision_cannot_self_supersede")
                        retired.add(key)
                    staged.append(card)
                require(not any(canonical(latest[id_][0]) in retired for id_ in batch_ids if id_ in latest), "revision_supersession_conflict")
                for card in staged:
                    prior = latest.get(card["id"])
                    if prior:
                        require(timestamp(prior[1]["timestamp"]) <= timestamp(at), "card_timestamp_regression")
                with self.ledger.savepoint():
                    refs = [self.ledger.put(card, ns) for card in staged]
                result = {"status": "extracted", "cards": refs, "code": "canonical_batch_committed",
                          "semantic_validation": "requires_independent_review"}
            except ContractError as exc:
                result = {"status": "rejected", "cards": [], "code": exc.code, "detail": exc.detail}
            return self.ledger.commit_receipt(request_id, request, result, ns)

    def corpus(self, ns, cutoff):
        """Frozen rebuild input includes immutable history, not previous projections."""
        self.evidence._access("extractor", ns, cutoff)
        cards = self._committed(ns, cutoff)
        for ref, _ in cards:
            self.read(ref, "extractor", ns, cutoff)
        value = {"schema_version": 1, "namespace": ns, "cutoff": cutoff, "extractor": self.extractor,
                 "cards": [{"ref": ref, "card": card} for ref, card in cards]}
        return {**value, "sha256": sha256(value)}
