"""CLI: `python -m chayuan_supervisor up|down|status|logs [--dry-run]`."""
from __future__ import annotations

import argparse
import json
import sys
import time

from chayuan_supervisor.manager import SupervisorManager


def main() -> None:
    ap = argparse.ArgumentParser(prog="chayuan-supervisor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp_up = sub.add_parser("up", help="start all processes")
    sp_up.add_argument("--dry-run", action="store_true")
    sp_up.add_argument("--only", nargs="*", help="only start these processes")
    sp_up.add_argument("--foreground", action="store_true", help="block until Ctrl-C")
    sub.add_parser("down", help="stop all processes")
    sp_status = sub.add_parser("status")
    sp_status.add_argument("--json", action="store_true")
    sp_logs = sub.add_parser("logs")
    sp_logs.add_argument("name")
    sp_logs.add_argument("--tail", type=int, default=100)
    sub.add_parser("plan", help="print plan + dependency graph")
    args = ap.parse_args()

    mgr = SupervisorManager()

    if args.cmd == "plan":
        plan = [{"name": p.name, "binary": p.binary, "args": p.args} for p in mgr.plan()]
        print(json.dumps({"plan": plan, "graph": mgr.graph(), "ports": mgr.ports()}, indent=2))
        return

    if args.cmd == "up":
        mgr.up(dry_run=args.dry_run, only=args.only)
        if args.foreground and not args.dry_run:
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                mgr.down()
        return

    if args.cmd == "down":
        mgr.down()
        return

    if args.cmd == "status":
        st = mgr.status()
        if args.json:
            print(json.dumps(st, indent=2))
        else:
            print(f"{'NAME':<16}{'STATE':<10}{'PID':<8}{'UPTIME':<10}{'ATTEMPTS':<10}")
            for s in st:
                print(f"{s['name']:<16}{s['state']:<10}{str(s.get('pid') or '-'):<8}"
                      f"{int(s['uptime_sec']):<10}{s['attempt']:<10}")
        return

    if args.cmd == "logs":
        for line in mgr.logs(args.name, tail=args.tail):
            print(line)
        return

    sys.exit(2)


if __name__ == "__main__":  # pragma: no cover
    main()
