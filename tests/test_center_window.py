import numpy as np

from fiberphoto.config import PipelineConfig
from fiberphoto.data import DffData, RawData, load_stage, save_stage
from fiberphoto.preprocess import dedrift, format_dff_formula


def test_retired_median_setting_is_ignored_on_rerun():
    cfg = PipelineConfig.from_dict({"dedrift": {"center_window_s": 20.0}})
    assert "center_window_s" not in cfg.to_dict()["dedrift"]
    t = np.arange(8.)
    ratio = .1 + t / 100.
    a = RawData(t, t + 20., None, 1., exported_dff=ratio, channel_names={"exported_dff": "R_1"})
    np.testing.assert_array_equal(dedrift(a, cfg.dedrift).dff, ratio)


def test_historical_correction_is_preserved_when_reading_a_saved_result(tmp_path):
    t = np.arange(8.)
    window_baseline = .1 + t / 100.
    saved = DffData(t, np.zeros_like(t), 1., t + 20., None, "Plexon R_1", "ratio",
                    dff_center_baseline=window_baseline, formula_info={"center_window_s": 5.0})
    restored = load_stage(save_stage(tmp_path / "old.h5", "B", saved), "B")
    a = RawData(t, t + 20., None, 1.)
    np.testing.assert_array_equal(restored.dff_center_baseline, window_baseline)
    assert "median(X(u)" in format_dff_formula(a, restored, "en")
