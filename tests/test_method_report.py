import json
from pathlib import Path

import numpy as np
import pandas as pd

from fiberphoto import PipelineConfig, run_file
from fiberphoto.method_report import write_method_report
from fiberphoto.sample_data import make_sample_dataset


def report_for(path, cfg, tmp_path):
    result = run_file(path, cfg, tmp_path / "out", make_figures=False)
    assert not result.error, result.error
    write_method_report(result, cfg)
    raw = (result.out_dir / "analysis_methods.json").read_text(encoding="utf-8")
    def invalid_number(token):
        raise AssertionError(token)
    report = json.loads(raw, parse_constant=invalid_number)
    return result, report, {s["id"]: s for s in report["stages"]}


def test_direct_ratio_and_file_scoped_report(tmp_path):
    path = tmp_path / "demo.csv"
    pd.DataFrame(dict(time=np.arange(8), RawF_1=np.arange(8)+25, R_1=np.arange(8)*.01)).to_csv(path, index=False)
    cfg = PipelineConfig()
    cfg.deconv.enabled = False
    cfg.intervals_by_recording[str(path.resolve())] = {"signal_start_s": 1, "signal_end_s": 4}
    cfg.intervals_by_recording[str(tmp_path / "other.csv")] = {"signal_start_s": 2, "signal_end_s": 6}
    result, report, stages = report_for(path, cfg, tmp_path)
    assert report["configured_parameters"]["dedrift"]["signal_start_s"] == 1
    assert "other.csv" not in str(report)
    assert set(stages) == {"read", "preprocess"}
    assert stages["preprocess"]["parameters"]["actual_method"] == "Plexon R_1"
    assert stages["preprocess"]["parameters"]["output_range_s"] == [1, 4]
    assert "R_1(t)" in (result.out_dir / "analysis_methods.md").read_text(encoding="utf-8")
    assert "SciPy · butter" not in stages["preprocess"]["references"]


def test_report_records_fits_and_downstream_methods(tmp_path):
    path, = make_sample_dataset(tmp_path / "data", n_animals=1, duration_s=180)
    cfg = PipelineConfig()
    result, _, stages = report_for(path, cfg, tmp_path)
    assert stages["preprocess"]["parameters"]["formula_info"]["slope"] == result.B.formula_info["slope"]
    assert stages["deconvolution"]["parameters"]["fitted_g"] == list(result.C.g)
    assert stages["events"]["parameters"]["actual_threshold"] == result.events.threshold
    assert stages["peth"]["parameters"]["accepted_trials"] == len(result.peth.onsets)
    assert stages["statistics"]["parameters"]["comparisons"]["auc_z"]["n"] == len(result.peth.onsets)
    assert "Friedrich, Zhou & Paninski (2017) · OASIS" in stages["deconvolution"]["references"]
