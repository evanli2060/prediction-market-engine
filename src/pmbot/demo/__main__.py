from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import build, render


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pmbot.demo", description="Build the HTML replay page.")
    ap.add_argument("--out", default="docs/index.html", help="where to write the page")
    ap.add_argument("--seed", type=int, default=10)
    ap.add_argument("--days", type=float, default=2.0)
    ap.add_argument("--latency", type=float, default=0.8)
    ap.add_argument("--warmup-days", type=float, default=1.0)
    ap.add_argument("--seeds", type=int, default=0, help="also run seeds 1..N, gated and ungated")
    ap.add_argument("--data", help="also write the raw data as JSON here")
    ap.add_argument("--fragment", action="store_true", help="write head+body content without a document skeleton")
    args = ap.parse_args(argv)
    if args.days < 0.5:
        ap.error("--days must be at least 0.5 so the run includes core hours")

    data = build(
        seed=args.seed,
        days=args.days,
        latency=args.latency,
        warmup_days=args.warmup_days,
        seeds=args.seeds,
        log=lambda s: print(s, file=sys.stderr),
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(render(data, standalone=not args.fragment), encoding="utf-8")
    if args.data:
        Path(args.data).write_text(json.dumps(data), encoding="utf-8")
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
