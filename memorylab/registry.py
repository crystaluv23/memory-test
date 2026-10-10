"""Immutable root registration and single-field proposals; controller API only."""
import copy

from .contracts import (ContractError, canonical, exact_object, identifier, mutation_candidate,
                        read_kernel, reference, require, sha256, validate_spec, validate_kernel)


class Registry:
    def __init__(self, ledger, kernel=None):
        self.ledger = ledger
        self.kernel = validate_kernel(kernel if kernel is not None else read_kernel())
        self.fingerprint = sha256(self.kernel)
        self.ledger.bind_kernel(self.kernel)

    def register_root(self, kind, id_, config=None):
        identifier(id_)
        require(kind in ("memory", "scenario"), "invalid_spec_kind")
        spec = {"schema_version": 1, "kind": kind, "id": id_, "version": 1,
                "parent": None, "config": copy.deepcopy(config if config is not None else self.kernel[kind + "_initial"])}
        validate_spec(spec, self.kernel)
        with self.ledger.mutation():
            return self.ledger.put(spec, "control")

    def resolve(self, ref):
        reference(ref, {"memory", "scenario"})
        require(ref["namespace"] == "control", "invalid_spec_namespace")
        return validate_spec(self.ledger.get(ref), self.kernel)

    def propose(self, request_id, proposal, role, cutoff, evidence_reader):
        """An evidence_reader checks role AND namespace AND exact sequence cutoff.

        The request binds even rejected invalid JSON objects. Non-JSON requests are
        refused before storage: there is no canonical immutable record to serialize.
        """
        identifier(request_id)
        canonical(proposal)
        require(role in ("evaluator", "proposer"), "invalid_proposer_role")
        require(type(cutoff) is int and cutoff >= 0, "invalid_cutoff")
        ns = "research/" + role
        request = {"proposal": proposal, "role": role, "cutoff": cutoff, "kernel": self.fingerprint}
        with self.ledger.mutation():
            require(cutoff <= self.ledger.head(), "future_cutoff")
            prior = self.ledger.receipt(request_id, request)
            if prior is not None:
                return prior
            try:
                require(type(proposal) is dict, "expected_object")
                require(proposal.get("kind") == ("memory" if role == "proposer" else "scenario"), "role_kind_mismatch")
                parent_ref = proposal.get("parent")
                parent = self.resolve(parent_ref)
                child = mutation_candidate(proposal, parent, parent_ref, self.kernel)
                for ref in proposal["evidence_refs"]:
                    evidence_reader(ref, role, ns, cutoff)
                if child is None:
                    result = {"status": "noop", "code": "no_behavior_change", "candidate": None}
                else:
                    child_ref = self.ledger.put(child, "control")
                    result = {"status": "accepted", "code": "legal_single_field_mutation", "candidate": child_ref}
            except ContractError as exc:
                result = {"status": "rejected", "code": exc.code, "detail": exc.detail, "candidate": None}
            return self.ledger.commit_receipt(request_id, request, result, ns)
