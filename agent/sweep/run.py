"""
Control sweep -- entry point.

    python agent/sweep/run.py enumerate --app SevenZip
    python agent/sweep/run.py codegen   --app SevenZip
    python agent/sweep/run.py live      --app SevenZip --max-controls 2 --yes
    python agent/sweep/run.py all                       # enumerate+codegen, every app

Tier 0 (enumerate) needs the app. Tier 1 (codegen) needs only the server.
Tier 2 (live) needs the server, an ELEVATED agent.py, and --yes, and it
physically clicks things.

See the module docstrings in enumerate_controls.py / tier1_codegen.py /
tier2_live.py for what each tier can and cannot detect.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_manifest, server_up  # noqa: E402
import enumerate_controls  # noqa: E402
import tier1_codegen  # noqa: E402
import tier2_live  # noqa: E402


def require_server():
    up, body = server_up()
    if not up:
        raise SystemExit(
            "sweep: server is not answering on http://localhost:3002.\n"
            "  start it yourself:  cd server; node server.js\n"
            "  (this harness never self-starts it -- same rule /regen follows)")
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("enumerate", help="walk the app's UIA tree into a cache")
    p.add_argument("--app", required=True)
    p.add_argument("--timeout", type=int, default=25)

    p = sub.add_parser("codegen", help="tier 1 -- audit the selector for every control")
    p.add_argument("--app", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--include-unsafe", action="store_true")

    p = sub.add_parser("live", help="tier 2 -- really click, record, replay, verify")
    p.add_argument("--app", required=True)
    p.add_argument("--max-controls", type=int, default=3)
    p.add_argument("--name", default=None)
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("all", help="enumerate + codegen for every enabled app")
    p.add_argument("--limit", type=int, default=None)

    args = ap.parse_args()

    if args.cmd == "enumerate":
        enumerate_controls.run(args.app, args.timeout)
    elif args.cmd == "codegen":
        require_server()
        tier1_codegen.run(args.app, args.limit, args.include_unsafe)
    elif args.cmd == "live":
        require_server()
        tier2_live.run(args.app, args.max_controls, args.yes, args.name)
    elif args.cmd == "all":
        require_server()
        summary = []
        for entry in load_manifest():
            if not entry.get("enabled", True):
                continue
            app = entry["app"]
            print("\n" + "=" * 70)
            print("  %s" % app)
            print("=" * 70)
            try:
                enumerate_controls.run(app)
                rep = tier1_codegen.run(app, args.limit)
                summary.append((app, rep["tested"], rep["flagged"],
                                len([r for r in rep["staticRisks"]
                                     if not r["detectableByCodegen"]])))
            except SystemExit as e:
                print("  skipped: %s" % e)
                summary.append((app, 0, 0, 0))
        print("\n" + "=" * 70)
        print("  %-14s %8s %8s %8s" % ("app", "tested", "flagged", "blindRisk"))
        for row in summary:
            print("  %-14s %8d %8d %8d" % row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
