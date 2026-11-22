import json
import shutil
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from fiberphoto.abet import AbetConfig, CUE_ALL, DURATION_RESAMPLE
from fiberphoto.abet_batch import attach_abet, find_abet_sidecar, load_abet_manifest, write_abet_manifest_template
from fiberphoto.config import PipelineConfig
from fiberphoto.behavior import BehaviorConfig
from fiberphoto.data import load_stage, save_stage
from fiberphoto.pipeline import attach_behavior, pick_marker, read_with_behavior, run_batch, run_file
from fiberphoto.preprocess import dedrift


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
    manifest.write_text("recording,enabled,control_gap_s\nday1/sample.csv,1,5\n", encoding="utf-8")
    cfg = PipelineConfig()
    cfg.deconv.enabled = False
    monkeypatch.setattr("fiberphoto.pipeline.export_figures", lambda *_: [])
    out = tmp_path / "results"
    first, second = run_batch([a, b], cfg, out, abet_manifest=manifest, retain_results=False)
    assert not (first.error or second.error)
    assert first.out_dir != second.out_dir and first.A is None
    assert first.summary["abet_mode"] == second.summary["abet_mode"] == DURATION_RESAMPLE
    assert len(pd.read_csv(first.out_dir / "abet/abet_trials.csv")) == 4
    alignment = json.loads((first.out_dir / "abet/abet_alignment.json").read_text())
    assert alignment["control_gap_s"] == 5
    assert json.loads((second.out_dir / "abet/abet_alignment.json").read_text())["control_gap_s"] == 3
    assert alignment["source_samples"] == 1400 and alignment["aligned_samples"] == 1279
    assert (second.out_dir / "abet/locked").exists()
    groups = pd.read_csv(out / "batch_abet_groups.csv")
    assert set(groups.sync_mode) == {DURATION_RESAMPLE} and set(groups.recording_path) == {str(a), str(b)}
    assert set(PipelineConfig.load(second.out_dir / "parameters.json").abet_by_recording) == {str(b)}
    assert set(pd.read_csv(out / "batch_summary.csv").recording_path) == {str(a), str(b)}


def test_repeat_export_removes_previous_synced_results(tmp_path, abet_xml):
    path = calcium(tmp_path / "day", abet_xml)
    cfg = PipelineConfig()
    cfg.deconv.enabled = False
    spec = AbetConfig(path=str(path.with_suffix(".xml")))
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
    assert abet["parameters"]["locked_analysis"]["sync_mode"] == DURATION_RESAMPLE
    saved = load_stage(session / "sample.h5", "A")
    assert len(saved.abet_native.time) == 1400 and len(saved.time) == 1279
    assert set(saved.abet_marker_names.values()) == set(saved.markers)
    assert not len(pick_marker(saved, "ABET: absent trial group")[1])
    assert not attach_abet(saved, None).markers
    cfg.abet_by_recording[str(path)] = AbetConfig(mode="disabled")
    result = run_file(path, cfg, out, make_figures=False)
    assert not result.error, result.error
    assert not (session / "abet/locked").exists()
    assert list(session.glob("previous-abet-*/abet/locked/analysis.json"))
    assert not (session / "peth_metrics.csv").exists()
    assert not (session / "sample_dataA_markers.csv").exists()
    assert list(session.glob("previous-abet-markers-*/peth_metrics.csv"))
    assert not (session / "abet/abet_trials.csv").is_file()
    assert not json.loads((session / "analysis_methods.json").read_text(encoding="utf-8")).get("abet_marker_output_sha256")


def test_manifest_template_roundtrip_and_rejected_rows(tmp_path, abet_xml):
    path = calcium(tmp_path / "day", abet_xml)
    sidecar = find_abet_sidecar(path)
    spec = AbetConfig(path=str(sidecar), control_gap_s=2)
    manifest = tmp_path / "clock.csv"
    write_abet_manifest_template(manifest, [path], {str(path): spec})
    assert load_abet_manifest(manifest)[str(path)] == spec
    lines = manifest.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "recording,xml,enabled,control_gap_s"
    manifest.write_text("\n".join(lines + [lines[-1]]), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_abet_manifest(manifest)
    manifest.write_text("recording,mode,abet_1_s\nday/sample.csv,offset,1\n", encoding="utf-8")
    migrated = load_abet_manifest(manifest)[str(path)]
    assert migrated.mode == DURATION_RESAMPLE and not hasattr(migrated, "anchors")
    manifest.write_text("recording,enabled\nday/sample.csv,maybe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="enabled"):
        load_abet_manifest(manifest)
    sidecar.write_text("<Other/>")
    assert find_abet_sidecar(path) is None
    assert read_with_behavior(path, PipelineConfig()).abet is None


def test_native_clock_restored_after_resampling_and_editing(tmp_path, abet_xml):
    path = calcium(tmp_path / "day", abet_xml)
    xml = str(path.with_suffix(".xml"))
    cfg = PipelineConfig.from_dict({"abet_by_recording": {str(path): {"path": xml, "mode": "affine",
                                    "anchors": [[0, 350], [5, 525]], "latency_s": 13}},
                                    "manual_ttl_by_recording": {str(path): [10, 30]}})
    raw = read_with_behavior(path, cfg)
    assert raw.abet_native.source == str(path) and raw.time[[0,-1]].tolist() == [.2, 128.]
    assert np.allclose(raw.markers["Manual TTL"], raw.from_acquisition_time([10,30]))
    csv = tmp_path / "velocity.csv"
    csv.write_text("time,x\n0,1\n40,2\n70,3\n", encoding="utf-8")
    behavior = BehaviorConfig(str(csv), "x", "time")
    edit = attach_behavior(raw.with_manual_ttl([11,32]), behavior)
    cfg.behavior_by_recording[str(path)] = behavior
    cfg.manual_ttl_by_recording[str(path)] = [11,32]
    reread = read_with_behavior(path,cfg)
    assert np.allclose(edit.behavior, reread.behavior, equal_nan=True)
    assert np.array_equal(edit.markers["Manual TTL"], reread.markers["Manual TTL"])
    cfg.intervals_by_recording[str(path)] = {"signal_start_s": 24, "signal_end_s": 88}
    effective = cfg.for_recording(path,raw=edit).dedrift
    assert [effective.signal_start_s, effective.signal_end_s] == pytest.approx(raw.from_acquisition_time([24,88]))
    save_stage(tmp_path / "archive.h5", "A", edit)
    backed = load_stage(tmp_path / "archive.h5", "A")
    assert np.allclose(backed.from_acquisition_time([11,32]), edit.markers["Manual TTL"])
    cfg.dedrift = replace(cfg.dedrift, prefer_plexon_dff=False, method="polynomial",
                          auto_trim=False, downsample_hz=3., lowpass_hz=None)
    for a in (edit, backed):
        b = dedrift(a, cfg.dedrift)
        assert b.time[[0, -1]].tolist() == [.2, 128.]
        assert len(b.time) == round((128.-.2)*3) + 1
        assert np.allclose(np.diff(b.time), 1./b.fs)
    restored = attach_abet(backed, None)
    assert restored.time[-1] == pytest.approx(139.9)
    assert np.array_equal(restored.markers["Manual TTL"],[11,32])
    fresh = attach_abet(backed, AbetConfig(path=xml))
    assert np.array_equal(fresh.exported_dff, reread.exported_dff)
    assert np.allclose(fresh.behavior, edit.behavior, equal_nan=True)


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
