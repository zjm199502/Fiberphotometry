"""Command-line entry point.

    fiberphoto run  <file-or-folder> ... -o results [-c params.json] [--lang en]
    fiberphoto inspect <file.tdms>
    fiberphoto sample <folder>
    fiberphoto config <params.json>        # write default parameters to edit
"""
from __future__ import annotations

import argparse
import sys

from .config import PipelineConfig


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fiberphoto", description="Fiber photometry batch analysis")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="process TDMS files or folders")
    r.add_argument("inputs", nargs="+")
    r.add_argument("-o", "--out", default="results")
    r.add_argument("-c", "--config", help="parameters JSON")
    r.add_argument("--lang", choices=["zh", "en"])

    i = sub.add_parser("inspect", help="list channels and guessed roles")
    i.add_argument("file")

    s = sub.add_parser("sample", help="write the synthetic training dataset")
    s.add_argument("folder", nargs="?", default="sample_data")

    c = sub.add_parser("config", help="write default parameters")
    c.add_argument("path", nargs="?", default="params.json")

    a = ap.parse_args(argv)

    if a.cmd == "run":
        from .pipeline import run_batch
        cfg = PipelineConfig.load(a.config) if a.config else PipelineConfig()
        if a.lang:
            cfg.plot.language = a.lang
        results = run_batch(a.inputs, cfg, a.out, log=print)
        return 1 if any(r.error for r in results) else 0
    if a.cmd == "inspect":
        from .io_tdms import inspect_tdms
        print(f"{'group':<14}{'channel':<20}{'samples':>10}{'fs (Hz)':>10}  guess")
        for ch in inspect_tdms(a.file):
            fs = f"{ch.fs:.1f}" if ch.fs else "-"
            print(f"{ch.group:<14}{ch.name:<20}{ch.length:>10}{fs:>10}  {ch.guess}")
        return 0
    if a.cmd == "sample":
        from .sample_data import make_sample_dataset
        for p in make_sample_dataset(a.folder):
            print(p)
        return 0
    if a.cmd == "config":
        PipelineConfig().save(a.path)
        print(a.path)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
