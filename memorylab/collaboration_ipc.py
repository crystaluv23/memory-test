"""Per-role local IO; client receipts are never provider/context attestations."""
import argparse
import hashlib
import json
import secrets
import socket
import sys
import time
from pathlib import Path

from .common import exclusive_run, save
from .contracts import canonical, exact_object, require, sha256
from .workspace import read_workspace_text


CLIENT = r'''import fcntl, hashlib, json, os, socket, sys
from pathlib import Path
CAPABILITY = __CAPABILITY__
ROOT = Path(__file__).resolve().parent
LIMIT = __LIMIT__
def frame(connection):
    data = bytearray()
    while not data.endswith(b"\n"):
        part = connection.recv(min(65536, 3000000 - len(data)))
        if not part or len(data) + len(part) >= 3000000:
            raise RuntimeError("Incomplete/oversized reply; do not retry")
        data.extend(part)
    return json.loads(data)
raw = sys.stdin.buffer.read(LIMIT + 1)
if len(raw) > LIMIT: raise SystemExit("Role request too large")
raw = raw.decode("utf-8")
if not raw.strip(): raise SystemExit("Nonempty JSON stdin required; no request sent")
with open(ROOT / "client.lock", "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    state_path = ROOT / "client-state.json"
    state = json.loads(state_path.read_text())
    if state["status"] != "ready": raise SystemExit("Prior send uncertain; do not retry")
    sequence = state["sequence"]
    state_path.write_text(json.dumps({"status":"pending", "sequence":sequence,
                                     "raw_sha256":hashlib.sha256(raw.encode()).hexdigest()}))
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(30); connection.connect(str(ROOT / "role.sock"))
        request = {"capability": CAPABILITY, "sequence": sequence, "raw": raw}
        connection.sendall((json.dumps(request) + "\n").encode())
        reply = frame(connection)
        if "error" in reply: raise RuntimeError(reply["error"])
        wire = reply["wire"]
        digest = hashlib.sha256(wire.encode()).hexdigest()
        if digest != reply["wire_sha256"]: raise RuntimeError("Reply digest mismatch")
        sys.stdout.write(wire); sys.stdout.flush()
        ack = {"capability": CAPABILITY, "sequence": sequence,
               "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(), "wire_sha256": digest}
        connection.sendall((json.dumps(ack) + "\n").encode())
        accepted = frame(connection)
        if accepted != {"status":"client_ack_saved"}: raise RuntimeError("Ack uncertain; do not retry")
        state_path.write_text(json.dumps({"status":"ready", "sequence":sequence + 1}))
'''


def prepare_client(root, slot, executable, request_limit):
    directory = Path(root) / slot
    require(not directory.exists(), "uncommitted_client_needs_attention")
    directory.mkdir(mode=0o700)
    token = secrets.token_hex(24)
    source = CLIENT.replace("__CAPABILITY__", repr(token)).replace("__LIMIT__", str(request_limit))
    (directory / "client.py").write_text(source)
    (directory / "client.py").chmod(0o500)
    save(directory / "client-state.json", {"status": "ready", "sequence": 1})
    public = {"command": [executable, str(directory / "client.py")], "input": "one raw JSON request on stdin",
              "output": "exact public controller JSON on stdout", "tool_output_token_cap": 40000}
    private = {"directory": str(directory), "capability_sha256": sha256(token), "client_sha256": sha256(source)}
    return public, private


def receive_frame(connection, limit):
    raw = bytearray()
    while not raw.endswith(b"\n"):
        part = connection.recv(min(65536, limit - len(raw)))
        require(bool(part) and len(raw) + len(part) < limit, "ipc_incomplete_or_oversized_frame")
        raw.extend(part)
    return json.loads(raw), bytes(raw)


def serve(run, slot):
    from .collaboration import Loop
    with Loop(run) as loop:
        actor = loop._actor(slot)
        require(loop.config["transport"] == "controller_collaboration_tools_with_local_role_ipc", "ipc_route_not_selected")
        private = actor["internal"]["service_client"]
        directory = Path(private["directory"])
        require(directory == loop._client_root() / slot and len(str(directory / "role.sock").encode()) < 104,
                "invalid_ipc_client_directory")
        require(sha256(read_workspace_text(directory, "client.py")) == private["client_sha256"], "ipc_client_drift")
        deadline = time.monotonic() + loop.config["actor_wall_seconds"]
    address = directory / "role.sock"
    require(not address.exists(), "ipc_endpoint_already_exists")
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(address)); address.chmod(0o600); server.listen(1); server.settimeout(1)
    print(json.dumps({"status": "role_service_ready", "slot": slot}), flush=True)
    try:
        while time.monotonic() < deadline:
            try: connection, _ = server.accept()
            except socket.timeout: continue
            with connection:
                connection.settimeout(30)
                request, raw_frame = receive_frame(connection, 400000)
                exact_object(request, {"capability", "sequence", "raw"})
                require(type(request["capability"]) is str and sha256(request["capability"]) == private["capability_sha256"],
                        "ipc_capability_denied")
                # A new actor may reach the endpoint before spawn's actual
                # handle is bound. Wait for that same binding; never invent it.
                binding = None
                while binding is None and time.monotonic() < deadline:
                    with Loop(run, readonly=True) as loop: binding = loop.ledger.receipt("bind-" + slot)
                    if binding is None: time.sleep(.05)
                require(binding is not None, "ipc_actor_not_bound")
                with exclusive_run(run), Loop(run) as loop:
                    actor = loop._actor(slot)
                    require(sha256(read_workspace_text(directory, "client.py")) == private["client_sha256"], "ipc_client_drift")
                    agent = loop.ledger.get(binding["result"]["binding"])["agent_id"]
                    sequence = request["sequence"]
                    response = loop.receive(slot, sequence, agent, request["raw"])
                    wire = loop._public_reply(response["response"])
                    reservation = loop.client_delivery_intent(slot, sequence)
                    require(reservation["dispatch_status"] == "call_client_send_once", "ipc_send_unknown_do_not_repeat")
                outbound = {"wire": wire, "wire_sha256": hashlib.sha256(wire.encode()).hexdigest()}
                connection.sendall(canonical(outbound) + b"\n")
                ack, ack_frame = receive_frame(connection, 2000)
                exact_object(ack, {"capability", "sequence", "raw_sha256", "wire_sha256"})
                require(ack["capability"] == request["capability"] and ack["sequence"] == sequence and
                        ack["raw_sha256"] == hashlib.sha256(request["raw"].encode()).hexdigest() and
                        ack["wire_sha256"] == outbound["wire_sha256"], "ipc_ack_mismatch")
                with exclusive_run(run), Loop(run) as loop:
                    require(sha256(read_workspace_text(directory, "client.py")) == private["client_sha256"], "ipc_client_drift")
                    loop.acknowledge_client(slot, sequence, agent, {k: ack[k] for k in ack if k != "capability"},
                                            hashlib.sha256(raw_frame).hexdigest(), hashlib.sha256(ack_frame).hexdigest())
                    terminal = loop.ledger.receipt("done-" + slot) is not None
                connection.sendall(canonical({"status": "client_ack_saved"}) + b"\n")
                if terminal:
                    print(json.dumps({"status": "role_service_terminal", "slot": slot}), flush=True)
                    return
        print(json.dumps({"status": "role_service_deadline_requires_actor_interrupt", "slot": slot}), flush=True)
    finally:
        server.close()
        address.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Local role service; never invokes an API or a model")
    parser.add_argument("--run", required=True); parser.add_argument("--slot", required=True)
    args = parser.parse_args(); serve(args.run, args.slot)


if __name__ == "__main__": main()
