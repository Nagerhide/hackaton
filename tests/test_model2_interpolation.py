"""Testy regresyjne imputacji brakujących pasm w model2."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd
import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_DIR / "model"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

from model2 import (  # noqa: E402
    FREQ_COLS,
    IMPUTED_MASK_ATTR,
    interpolate_raw_spectra,
    load_exported_model,
    optimize_imputed_spectra,
    prediction_frame,
    predict_and_explain,
)


class Model2InterpolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test = pd.read_csv(PROJECT_DIR / "tests" / "test.csv")
        cls.test = test
        cls.engine = test[test.engine_id.eq("test_0049")].copy()

    def test_peer_profile_replaces_missing_values(self):
        prepared = interpolate_raw_spectra(self.engine)
        row_position = int(prepared.cylinder.to_numpy().tolist().index(14))
        target = prepared.iloc[row_position]
        imputed = [
            column
            for column, missing in zip(
                FREQ_COLS, prepared.attrs[IMPUTED_MASK_ATTR][row_position]
            )
            if missing
        ]

        self.assertEqual(imputed, ["mV_8", "mV_17"])
        self.assertAlmostEqual(float(target.mV_8), 63.18225, places=5)
        self.assertAlmostEqual(float(target.mV_17), 12.712, places=5)
        self.assertNotAlmostEqual(float(target.mV_17), 23.8375, places=4)

    def test_test_0049_cylinder_14_is_healthy_and_imputation_is_not_evidence(self):
        _, explanations, bands = predict_and_explain(
            MODEL_DIR / "acoustic_model2.pkl",
            MODEL_DIR / "verdict_explainer.pkl",
            self.engine,
        )
        summary = explanations[explanations.cylinder.eq(14)].iloc[0]
        target_bands = bands[bands.cylinder.eq(14)]
        imputed_bands = target_bands[target_bands.was_imputed]

        self.assertEqual(summary.label, "ok")
        self.assertEqual(summary.suspicious_frequency_range, "brak")
        self.assertEqual(summary.imputed_columns, "mV_8,mV_17")
        self.assertEqual(set(imputed_bands.column), {"mV_8", "mV_17"})
        self.assertFalse(imputed_bands.is_suspicious.any())
        self.assertTrue(imputed_bands.amplitude_mv.isna().all())
        self.assertTrue(imputed_bands.anomaly_score.isna().all())

    def test_confidence_optimization_changes_only_missing_values(self):
        prepared = interpolate_raw_spectra(self.test)
        missing_mask = prepared.attrs[IMPUTED_MASK_ATTR]
        classifier = load_exported_model(MODEL_DIR / "acoustic_model2.pkl")
        before = prediction_frame(classifier, prepared)
        optimized, audit = optimize_imputed_spectra(classifier, prepared)
        after = prediction_frame(classifier, optimized)
        before_values = prepared[FREQ_COLS].to_numpy(dtype=float)
        after_values = optimized[FREQ_COLS].to_numpy(dtype=float)

        np.testing.assert_array_equal(
            after_values[~missing_mask], before_values[~missing_mask]
        )
        self.assertTrue(np.isfinite(after_values[missing_mask]).all())
        np.testing.assert_array_equal(after.label, before.label)
        np.testing.assert_array_equal(after.severity, before.severity)
        self.assertTrue(
            np.all(
                after.vote_confidence.to_numpy()
                >= before.vote_confidence.to_numpy() - 1e-12
            )
        )
        self.assertGreater(int(audit.confidence_optimization_applied.sum()), 0)


if __name__ == "__main__":
    unittest.main()
