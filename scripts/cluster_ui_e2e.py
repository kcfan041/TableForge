"""End-to-end check of cluster management inside `tableforge ui` (U2).

Two `tableforge ui` processes with separate homes on this machine:

1. The creator creates a cluster and sees the management pages as admin.
2. The creator makes invites (worker + operator, viewer-only, confirmed admin).
3. A second person joins with the worker + operator invite; their UI shows the same
   cluster documents through gRPC, can create and start an experiment, but cannot see
   the admin pages. Their worker runs the jobs.
4. A viewer can read but not change anything. An admin invite needs confirmation and is
   recorded when used.

Prints one line per step; a watchdog aborts after --timeout seconds.

    uv run python scripts/cluster_ui_e2e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import local_ui_e2e  # noqa: E402
from create_cluster_e2e import cleanup_started, close_ui, start_ui  # noqa: E402
from local_ui_e2e import expect, free_port, ok, step  # noqa: E402


def main() -> int:
    timeout = float(sys.argv[sys.argv.index("--timeout") + 1]) if "--timeout" in sys.argv else 300
    local_ui_e2e.watchdog(timeout)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tableforge-cluster-ui-e2e-") as directory:
        root = Path(directory)
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        environment.pop("TABLEFORGE_HOME", None)
        creator_home = root / "creator"
        uis: list[subprocess.Popen[str]] = []
        try:
            step("creator creates a cluster")
            creator_ui, creator = start_ui(creator_home, free_port(), environment)
            uis.append(creator_ui)
            code, created = creator.call("POST", "/api/cluster", {"network": "local"}, timeout=120)
            expect(code == 200, f"{code} {created}")
            code, session = creator.call("GET", "/api/cluster/session")
            expect(code == 200 and session["role"] == "admin", f"{code} {session}")
            ok(f"admin of {session['cluster_id']}")

            step("creator sees the management documents")
            code, overview = creator.call("GET", "/api/cluster/query/overview")
            expect(code == 200 and overview["cluster_id"] == session["cluster_id"], f"{overview}")
            _, diagnostics = creator.call("GET", "/api/cluster/query/diagnostics")
            expect(diagnostics["advertised_address"] == "127.0.0.1:7443", f"{diagnostics}")
            code, _ = creator.call("GET", "/api/cluster/query/invites")
            expect(code == 200, "admin cannot list invites")
            ok(f"address {diagnostics['advertised_address']}")

            step("creator makes invites")
            code, member_invite = creator.call(
                "POST",
                "/api/cluster/command/create_invite",
                {"node": True, "role": "operator", "ttl_hours": 24, "label": "member"},
            )
            expect(code == 200, f"{code} {member_invite}")
            code, viewer_invite = creator.call(
                "POST",
                "/api/cluster/command/create_invite",
                {"node": False, "role": "viewer", "ttl_hours": 24, "label": "watcher"},
            )
            expect(code == 200, f"{code} {viewer_invite}")
            code, refused = creator.call(
                "POST",
                "/api/cluster/command/create_invite",
                {"node": False, "role": "admin", "ttl_hours": 1, "label": "deputy"},
            )
            expect(code == 400 and "confirm" in refused["detail"], f"{code} {refused}")
            code, admin_invite = creator.call(
                "POST",
                "/api/cluster/command/create_invite",
                {
                    "node": False,
                    "role": "admin",
                    "ttl_hours": 1,
                    "label": "deputy",
                    "confirm_admin": True,
                },
            )
            expect(code == 200, f"{code} {admin_invite}")
            ok("worker+operator, viewer, confirmed admin (unconfirmed refused)")

            step("member joins with the worker + operator invite")
            member_ui, member = start_ui(root / "member", free_port(), environment)
            uis.append(member_ui)
            code, joined = member.call("POST", "/api/join", {"invite": member_invite["invite"]})
            expect(code == 200, f"{code} {joined}")
            status = joined["status"]
            expect(status["joined"] and status["operator"]["role"] == "operator", f"{status}")
            code, session = member.call("GET", "/api/cluster/session")
            expect(code == 200 and session["role"] == "operator", f"{code} {session}")
            ok(f"member role {session['role']}")

            step("member runs a 6-max experiment on their own worker")
            code, worker = member.call("POST", "/api/workers")
            expect(code == 200, f"{code} {worker}")
            code, experiment = member.call(
                "POST", "/api/cluster/command/create_experiment", {"preset": "6max-rule-smoke"}
            )
            expect(code == 200, f"{code} {experiment}")
            experiment_id = experiment["experiment_id"]
            code, running = member.call(
                "POST", "/api/cluster/command/start_experiment", {"experiment_id": experiment_id}
            )
            expect(code == 200 and running["status"] == "running", f"{code} {running}")
            deadline = time.monotonic() + 120
            detail: dict = {}
            while time.monotonic() < deadline:
                _, detail = member.call(
                    "GET", f"/api/cluster/query/experiment?argument={experiment_id}"
                )
                if detail["status"] in ("completed", "failed"):
                    break
                time.sleep(0.5)
            expect(detail["status"] == "completed", f"experiment {detail.get('status')}")
            expect(sum(detail["results"]["winnings"]) == 0, "winnings do not sum to zero")
            _, log = member.call("GET", f"/api/workers/{worker['worker']}/log")
            expect(log["worker"]["jobs"] == 4, f"member worker ran {log['worker']['jobs']} jobs")
            ok(f"4 jobs on the member's worker, winnings {detail['results']['winnings']}")

            step("the creator sees the member's work")
            _, overview = creator.call("GET", "/api/cluster/query/overview")
            expect(overview["nodes"]["total"] == 1, f"nodes {overview['nodes']}")
            _, experiments = creator.call("GET", "/api/cluster/query/experiments")
            expect(experiments[0]["experiment_id"] == experiment_id, "experiment missing")
            _, events = creator.call("GET", "/api/cluster/query/events_after?argument=0")
            types = {event["type"] for event in events["events"]}
            expect("EXPERIMENT_COMPLETED" in types, f"events {types}")
            ok("member's node, experiment and events visible")

            step("operator cannot use admin functions")
            code, _ = member.call("GET", "/api/cluster/query/invites")
            expect(code == 403, f"operator listed invites: {code}")
            code, _ = member.call(
                "POST", "/api/cluster/command/create_invite", {"node": True, "ttl_hours": 1}
            )
            expect(code == 403, f"operator created an invite: {code}")
            ok()

            step("viewer can read but not change anything")
            viewer_ui, viewer = start_ui(root / "viewer", free_port(), environment)
            uis.append(viewer_ui)
            code, joined = viewer.call("POST", "/api/join", {"invite": viewer_invite["invite"]})
            expect(code == 200, f"{code} {joined}")
            expect(not joined["status"]["joined"], "viewer-only invite enrolled a worker")
            code, session = viewer.call("GET", "/api/cluster/session")
            expect(session["role"] == "viewer", f"{session}")
            code, _ = viewer.call("GET", "/api/cluster/query/experiments")
            expect(code == 200, "viewer cannot read")
            code, denied = viewer.call(
                "POST", "/api/cluster/command/stop_experiment", {"experiment_id": experiment_id}
            )
            expect(code == 403, f"viewer changed something: {code} {denied}")
            ok()

            step("admin invite is recorded when used")
            deputy_ui, deputy = start_ui(root / "deputy", free_port(), environment)
            uis.append(deputy_ui)
            code, joined = deputy.call("POST", "/api/join", {"invite": admin_invite["invite"]})
            expect(code == 200, f"{code} {joined}")
            code, session = deputy.call("GET", "/api/cluster/session")
            expect(session["role"] == "admin", f"{session}")
            _, events = creator.call("GET", "/api/cluster/query/events?argument=50")
            recorded = [event for event in events if event["type"] == "ADMIN_INVITE_REDEEMED"]
            expect(len(recorded) == 1, f"admin redemption events: {recorded}")
            expect(recorded[0]["payload"]["label"] == "deputy", f"{recorded}")
            ok("ADMIN_INVITE_REDEEMED recorded")

            step("admin revokes the operator; the member loses management access")
            _, operators = creator.call("GET", "/api/cluster/query/operators")
            member_operator = next(item for item in operators if item["label"] == "member")
            code, _ = creator.call(
                "POST",
                "/api/cluster/command/revoke_operator",
                {"operator_id": member_operator["operator_id"]},
            )
            expect(code == 200, f"revoke returned {code}")
            code, _ = member.call("GET", "/api/cluster/query/overview")
            expect(code == 401, f"revoked operator still reads: {code}")
            ok()

            step("stop the cluster and close every UI")
            creator.call("POST", "/api/cluster/service/stop", timeout=60)
            for ui in reversed(uis):
                close_ui(ui)
            uis.clear()
            ok()
        finally:
            cleanup_started()
    print(f"all cluster UI checks passed in {time.monotonic() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(local_ui_e2e.run(main))
