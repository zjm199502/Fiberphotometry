import json
import shutil

import numpy as np
import pandas as pd
import pytest

from fiberphoto.abet import AbetConfig, CUE_ALL
from fiberphoto.abet_batch import attach_abet, find_abet_sidecar, load_abet_manifest, write_abet_manifest_template
from fiberphoto.config import PipelineConfig
from fiberphoto.data import load_stage
from fiberphoto.pipeline import pick_marker, read_with_behavior, run_batch, run_file


def calcium(folder, xml):
    folder.mkdir()
    path = folder / "sample.csv"
    t = np.arange(0, 140, .1)
    ratio = .01 * np.cos(t)
    pd.DataFrame({"Frame": np.arange(len(t)) + 1, "time": t, "RawF_1": 25 * (1 + ratio),
                  "F0_1": 25, "R_1": ratio}).to_csv(path, index=False)
    shutil.copyfile(xml, path.with_suffix(".xml"))
    return path


def test_manifest_duplicate_stems_and_alignment_isolation(tmp_path, abet_xml, monkeypatch):
    a, b = [calcium(tmp_path / name, abet_xml) for name in ("day1", "day2")]
    manifest = tmp_path / "sync.csv"
    manifest.write_text("recording,mode,abet_1_s,calcium_1_s\nday1/sample.csv,offset,3,5\n", encoding="utf-8")
    cfg = PipelineConfig()
    cfg.deconv.enabled = False
    monkeypatch.setattr("fiberphoto.pipeline.export_figures", lambda *_: [])
    out = tmp_path / "results"
    first, second = run_batch([a, b], cfg, out, abet_manifest=manifest, retain_results=False)
    assert not (first.error or second.error)
    assert first.out_dir != second.out_dir and first.A is None
    assert first.summary["abet_mode"] == "offset" and second.summary["abet_mode"] == "behavior_only"
    assert len(pd.read_csv(first.out_dir / "abet/abet_trials.csv")) == 4
    alignment = json.loads((first.out_dir / "abet/abet_alignment.json").read_text())
    assert alignment["offset_s"] == 2
    assert not (second.out_dir / "abet/locked").exists()
    groups = pd.read_csv(out / "batch_abet_groups.csv")
    assert set(groups.sync_mode) == {"offset"} and set(groups.recording_path) == {str(a)}
    assert set(PipelineConfig.load(second.out_dir / "parameters.json").abet_by_recording) == {str(b)}
    assert set(pd.read_csv(out / "batch_summary.csv").recording_path) == {str(a), str(b)}


def test_repeat_export_removes_previous_synced_results(tmp_path, abet_xml):
    path = calcium(tmp_path / "day", abet_xml)
    cfg = PipelineConfig()
    cfg.deconv.enabled = False
    spec = AbetConfig(path=str(path.with_suffix(".xml")), mode="assumed_trigger")
    cfg.abet_by_recording[str(path)] = spec
    cfg.peth.marker = CUE_ALL
    out = tmp_path / "results"
    result = run_file(path, cfg, out, make_figures=False)
    assert not result.error, result.error
    session = result.out_dir
    prior = json.loads((session / "analysis_methods.json").read_text(encoding="utf-8"))
    assert prior["abet_marker_output_sha256"]
    reader, abet = prior["stages"][:2]
    assert reader["parameters"]["abet_marker_labels"] and not any("上升沿/下降沿" in s for s in reader["calculations"])
    assert abet["parameters"]["locked_analysis"]["sync_mode"] == "assumed_trigger"
    saved = load_stage(session / "sample.h5", "A")
    assert set(saved.abet_marker_names.values()) == set(saved.markers)
    assert not len(pick_marker(saved, "ABET: absent trial group")[1])
    assert not attach_abet(saved, None).markers
    cfg.abet_by_recording.clear()
    result = run_file(path, cfg, out, make_figures=False)
    assert not result.error, result.error
    assert not (session / "abet/locked").exists()
    assert list(session.glob("previous-abet-*/abet/locked/analysis.json"))
    assert not (session / "peth_metrics.csv").exists()
    assert not (session / "sample_dataA_markers.csv").exists()
    assert list(session.glob("previous-abet-markers-*/peth_metrics.csv"))
    assert (session / "abet/abet_trials.csv").is_file()
    assert not json.loads((session / "analysis_methods.json").read_text(encoding="utf-8")).get("abet_marker_output_sha256")


def test_manifest_template_roundtrip_and_rejected_rows(tmp_path, abet_xml):
    path = calcium(tmp_path / "day", abet_xml)
    sidecar = find_abet_sidecar(path)
    spec = AbetConfig(path=str(sidecar), mode="affine", anchors=[[1, 2], [110, 112]])
    manifest = tmp_path / "clock.csv"
    write_abet_manifest_template(manifest, [path], {str(path): spec})
    assert load_abet_manifest(manifest)[str(path)] == spec
    lines = manifest.read_text(encoding="utf-8-sig").splitlines()
    manifest.write_text("\n".join(lines + [lines[-1]]), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_abet_manifest(manifest)
    manifest.write_text("recording,mode,abet_1_s\nday/sample.csv,offset,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete anchor"):
        load_abet_manifest(manifest)
    sidecar.write_text("<Other/>")
    assert find_abet_sidecar(path) is None
    assert read_with_behavior(path, PipelineConfig()).abet is None
def test_transient_windows_lock_retries_output_rename(tmp_path, monkeypatch):
    from pathlib import Path
    from fiberphoto.abet_batch import _rename_output

    source, dest = tmp_path / "staged", tmp_path / "abet"
    source.mkdir()
    original = Path.rename
    calls = []

    def rename(path, target):
        calls.append((path, target))
        if len(calls) == 1:
            exc = PermissionError("directory in use")
            exc.winerror = 5
            raise exc
        return original(path, target)

    monkeypatch.setattr(Path, "rename", rename)
    _rename_output(source, dest)
    assert dest.is_dir() and len(calls) == 2
