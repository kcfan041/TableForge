"""Background parameter sweeps for NFSP research, without console windows.

Every run is a separate process of `scripts/nfsp_probe.py` (heads-up cash) or
`scripts/tournament_probe.py` (tournaments). Runs start with no window and below-normal
priority, and on CPUs with several CCDs they alternate between CCDs so heat spreads evenly
(see `tableforge.capability.cpu_topology`).

Start a sweep (returns immediately):

    uv run python scripts/sweep.py start runs/p1b sweep.json --seeds 1,2,3 --size 1000000

`sweep.json` maps a name to extra probe arguments, for example:

    {"base": ["--reward-scale", "100"],
     "bigbuf": ["--reward-scale", "100", "--rl-capacity", "500000"]}

`{tag}` in an argument is replaced by the run tag (useful for `--save {tag}.zip`).
Use `--probe tournament` for tournaments; `--size` is then the number of tournaments.

Watch and summarize:

    uv run python scripts/sweep.py status runs/p1b
    uv run python scripts/sweep.py summary runs/p1b
    uv run python scripts/sweep.py stop runs/p1b
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tableforge.capability.cpu_topology import set_affinity, spread_masks  # noqa: E402

PROBES = {"cash": "nfsp_probe.py", "tournament": "tournament_probe.py"}
MAX_RUNS = 16
OPPONENTS = ("random", "call", "tight", "aggro")


def _python() -> str:
    venv = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def _children(pid: int) -> list[int]:
    if sys.platform != "win32":
        return []
    output = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}').ProcessId",
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout
    return [int(line) for line in output.split() if line.strip().isdigit()]


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        output = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        return str(pid) in output
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def start(args: argparse.Namespace) -> None:
    folder = Path(args.folder)
    folder.mkdir(parents=True, exist_ok=True)
    configs: dict[str, list[str]] = json.loads(Path(args.configs).read_text(encoding="utf-8"))
    seeds = [int(value) for value in args.seeds.split(",")]
    runs = [(name, extra, seed) for name, extra in configs.items() for seed in seeds]
    if len(runs) > args.max_runs:
        raise SystemExit(f"{len(runs)} runs requested; the limit is {args.max_runs}")
    probe = ROOT / "scripts" / PROBES[args.probe]
    common = (
        [
            "--hands",
            str(args.size),
            "--checkpoints",
            str(args.checkpoints),
            "--deals",
            str(args.evaluation),
            "--schema",
            "poker-hunlhe-vector-v2",
            "--device",
            "cpu",
        ]
        if args.probe == "cash"
        else [
            "--tournaments",
            str(args.size),
            "--checkpoints",
            str(args.checkpoints),
            "--seeds",
            str(args.evaluation),
        ]
    )
    masks = spread_masks()
    flags = 0
    if sys.platform == "win32":
        flags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        )
    manifest: dict[str, dict[str, object]] = {}
    for index, (name, extra, seed) in enumerate(runs):
        tag = f"{name}-s{seed}"
        command = [
            _python(),
            "-u",
            str(probe),
            *common,
            "--threads",
            "1",
            "--seed",
            str(seed),
            "--tag",
            tag,
            *[part.replace("{tag}", str(folder / tag)) for part in extra],
        ]
        with (
            (folder / f"{tag}.jsonl").open("w", encoding="utf-8") as out,
            (folder / f"{tag}.err").open("w", encoding="utf-8") as err,
        ):
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=out,
                stderr=err,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
                start_new_session=sys.platform != "win32",
            )
        manifest[tag] = {
            "pid": process.pid,
            "group": index % len(masks) if masks else None,
            "command": command,
        }
    # `.venv\Scripts\python.exe` starts the real interpreter as a child a moment later;
    # both get the affinity so the child does not inherit an unrestricted mask.
    if masks:
        pending = dict(manifest)
        deadline = time.monotonic() + 20
        while pending and time.monotonic() < deadline:
            for tag, info in list(pending.items()):
                pid = int(info["pid"])  # type: ignore[call-overload]
                found = _children(pid)
                if found or not _alive(pid) or sys.platform != "win32":
                    mask = masks[int(info["group"])]  # type: ignore[call-overload]
                    for target in (pid, *found):
                        set_affinity(target, mask)
                    info["pids"] = [pid, *found]
                    del pending[tag]
            time.sleep(0.5)
    (folder / "sweep.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    groups = defaultdict(int)
    for info in manifest.values():
        groups[str(info["group"])] += 1
    print(f"started {len(manifest)} runs in {folder}; per CPU group: {dict(groups)}")


def _rows(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("{")
    ]


def status(args: argparse.Namespace) -> None:
    folder = Path(args.folder)
    manifest = json.loads((folder / "sweep.json").read_text(encoding="utf-8"))
    for tag, info in manifest.items():
        rows = _rows(folder / f"{tag}.jsonl")
        running = any(_alive(pid) for pid in info.get("pids", [info["pid"]]))
        error = (folder / f"{tag}.err").read_text(encoding="utf-8").strip().splitlines()
        progress = rows[-1].get("hands", rows[-1].get("tournaments")) if rows else 0
        print(
            f"{tag:<32} {'running' if running else 'done   '} checkpoints {len(rows)}"
            f" progress {progress}" + (f"  error: {error[-1]}" if error else "")
        )


def _cell(benchmark: dict[str, dict[str, float]], name: str) -> str:
    item = benchmark[name]
    low = item.get("ci_low", item.get("low", 0.0))
    high = item.get("ci_high", item.get("high", 0.0))
    mark = "*" if low > 0 or high < 0 else " "
    value = item.get("bb_per_100", item.get("mean", 0.0))
    return f"{value:+6.0f}{mark}"


def _percent(item: dict[str, float] | None) -> str:
    if not item:
        return "      -       "
    mark = "*" if item["low"] > 0 else ("-" if item["high"] < 0 else " ")
    return f"{item['mean'] * 100:+6.1f}%{mark}"


def summary(args: argparse.Namespace) -> None:
    folder = Path(args.folder)
    by_config: dict[str, list[dict[str, object]]] = defaultdict(list)
    for path in sorted(folder.glob("*.jsonl")):
        rows = _rows(path)
        if not rows:
            continue
        print(path.stem)
        for row in rows:
            benchmark = row["benchmark"]
            assert isinstance(benchmark, dict)
            if "roi" in benchmark:
                greedy = row.get("greedy")
                print(
                    f"  {row['tournaments']:>7} tournaments  ROI {_percent(benchmark['roi'])}"
                    f"  bounty {benchmark['bounty_ev']['mean']:.3f}"
                    f"  greedy ROI {_percent(greedy['roi'] if isinstance(greedy, dict) else None)}"
                )
            else:
                cells = " ".join(_cell(benchmark, name) for name in OPPONENTS)
                print(f"  {int(row['hands']) // 1000:>6}k hands  {cells}")  # type: ignore[call-overload]
        by_config[path.stem.rsplit("-s", 1)[0]].append(rows[-1])
    print("\nlast checkpoint, mean over seeds (* = every seed significant):")
    for config, rows in by_config.items():
        benchmark = rows[0]["benchmark"]
        assert isinstance(benchmark, dict)
        if "roi" in benchmark:
            values = [row["benchmark"]["roi"] for row in rows]  # type: ignore[index]
            mean = sum(item["mean"] for item in values) / len(values)
            every = all(item["low"] > 0 for item in values)
            print(f"  {config:<28} ROI {mean * 100:+6.1f}%{'*' if every else ' '}  n={len(rows)}")
        else:
            parts = []
            for name in OPPONENTS:
                items = [row["benchmark"][name] for row in rows]  # type: ignore[index]
                mean = sum(item["bb_per_100"] for item in items) / len(items)
                every = all(item["ci_low"] > 0 for item in items)
                parts.append(f"{name} {mean:+5.0f}{'*' if every else ' '}")
            print(f"  {config:<28} " + "  ".join(parts) + f"  n={len(rows)}")


def stop(args: argparse.Namespace) -> None:
    folder = Path(args.folder)
    manifest = json.loads((folder / "sweep.json").read_text(encoding="utf-8"))
    for tag, info in manifest.items():
        for pid in reversed(info.get("pids", [info["pid"]])):
            if not _alive(pid):
                continue
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.kill(pid, 15)
        print(f"stopped {tag}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    start_parser = commands.add_parser("start")
    start_parser.add_argument("folder")
    start_parser.add_argument("configs", help="JSON file: name -> extra probe arguments")
    start_parser.add_argument("--seeds", default="1,2,3")
    start_parser.add_argument("--size", type=int, required=True, help="hands or tournaments")
    start_parser.add_argument("--probe", choices=sorted(PROBES), default="cash")
    start_parser.add_argument("--checkpoints", type=int, default=4)
    start_parser.add_argument(
        "--evaluation", type=int, default=1000, help="deals per bot, or benchmark seeds"
    )
    start_parser.add_argument("--max-runs", type=int, default=MAX_RUNS)
    start_parser.set_defaults(handler=start)
    for name, handler in (("status", status), ("summary", summary), ("stop", stop)):
        sub = commands.add_parser(name)
        sub.add_argument("folder")
        sub.set_defaults(handler=handler)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
