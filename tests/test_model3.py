"""Testy regresyjne rekonstruktora i publicznego API model3."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_DIR / "model"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

from model2 import FREQ_COLS, IMPUTED_MASK_ATTR, LABELS, SEVERITIES  # noqa: E402
from model3 import (  # noqa: E402
    BandRidgeImputer,
    MODEL3_ANOMALY_THRESHOLD,
    MissingAwareTypeModel,
    ResidualKNNImputer,
    decode_probabilities3,
    load_exported_model,
    predict,
)


class Model3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = pd.read_csv(PROJECT_DIR / "tests" / "val.csv")
        cls.unlabeled = pd.read_csv(PROJECT_DIR / "tests" / "train.csv")
        cls.test = pd.read_csv(PROJECT_DIR / "tests" / "test.csv")
        cls.imputer = ResidualKNNImputer().fit(
            [cls.unlabeled, cls.validation]
        )

    def test_imputer_changes_only_missing_cells(self):
        engine = self.test[self.test.engine_id.eq("test_0049")].reset_index(
            drop=True
        )
        before = engine[FREQ_COLS].to_numpy(float)
        missing = ~np.isfinite(before)
        prepared = self.imputer.transform(engine)
        after = prepared[FREQ_COLS].to_numpy(float)

        self.assertTrue(np.isfinite(after).all())
        np.testing.assert_array_equal(after[~missing], before[~missing])
        np.testing.assert_array_equal(
            prepared.attrs[IMPUTED_MASK_ATTR], missing
        )

    def test_completely_empty_spectrum_is_rejected(self):
        engine = self.test[self.test.engine_id.eq("test_0000")].reset_index(
            drop=True
        )
        engine.loc[0, FREQ_COLS] = np.nan
        with self.assertRaisesRegex(ValueError, "całkowicie pustego widma"):
            self.imputer.transform(engine)

    def test_ridge_imputer_changes_only_missing_cells(self):
        train = self.validation[self.validation.engine_id.ne("val_0000")]
        engine = self.validation[
            self.validation.engine_id.eq("val_0000")
        ].reset_index(drop=True)
        before = engine[FREQ_COLS].to_numpy(float, copy=True)
        engine.loc[0, ["mV_3", "mV_4"]] = np.nan
        prepared = BandRidgeImputer().fit(train).transform(engine)
        after = prepared[FREQ_COLS].to_numpy(float)
        missing = ~np.isfinite(engine[FREQ_COLS].to_numpy(float))

        self.assertTrue(np.isfinite(after).all())
        np.testing.assert_array_equal(after[~missing], before[~missing])

    def test_masked_type_model_returns_finite_probabilities(self):
        train = self.validation[
            self.validation.engine_id.ne("val_0000")
        ].reset_index(drop=True)
        engine = self.validation[
            self.validation.engine_id.eq("val_0000")
        ].reset_index(drop=True)
        engine.loc[0, ["mV_3", "mV_4"]] = np.nan
        probability = MissingAwareTypeModel().fit(train).predict_probabilities(
            engine
        )

        self.assertEqual(probability.shape, (len(engine), 5))
        self.assertTrue(np.isfinite(probability).all())
        np.testing.assert_allclose(probability.sum(axis=1), 1.0)

    def test_model3_threshold_is_frozen_at_point_48(self):
        label_probability = np.zeros((1, len(LABELS)))
        label_probability[0, LABELS.index("ok")] = 0.51
        label_probability[0, LABELS.index("iglica")] = 0.49
        severity_probability = np.zeros((1, len(SEVERITIES)))
        severity_probability[0, SEVERITIES.index("male")] = 1.0
        labels, severities, _ = decode_probabilities3(
            label_probability, severity_probability
        )

        self.assertEqual(MODEL3_ANOMALY_THRESHOLD, 0.48)
        self.assertEqual(labels[0], "iglica")
        self.assertEqual(severities[0], "male")

    def test_exported_bundle_contains_imputer_and_scalers(self):
        path = MODEL_DIR / "acoustic_model3.pkl"
        if not path.exists():
            self.skipTest("Najpierw uruchom model/model3.py")
        bundle = load_exported_model(path)

        self.assertEqual(bundle.imputer.n_neighbors, 5)
        self.assertGreater(len(bundle.imputer.reference_residuals), 2000)
        self.assertGreater(len(bundle.classifier.models), 1)
        self.assertEqual(
            len(bundle.classifier.models), len(bundle.classifier.ridge_imputers)
        )
        self.assertEqual(
            len(bundle.classifier.models),
            len(bundle.classifier.masked_type_models),
        )
        self.assertTrue(
            all(hasattr(model, "input_scaler") for model in bundle.classifier.models)
        )

    def test_server_api_is_compatible_with_model2_contract(self):
        classifier_path = MODEL_DIR / "acoustic_model3.pkl"
        explainer_path = MODEL_DIR / "verdict_explainer3.pkl"
        if not classifier_path.exists() or not explainer_path.exists():
            self.skipTest("Najpierw uruchom model/model3.py")
        engine = self.test[self.test.engine_id.eq("test_0049")]
        response = predict(
            engine,
            classifier_path=classifier_path,
            explainer_path=explainer_path,
        )

        self.assertEqual(len(response["results"]), len(engine))
        self.assertGreaterEqual(response["model_votes"], 50)
        self.assertTrue(
            {"engine_id", "cylinder", "label", "severity", "vote_confidence"}
            <= set(response["results"][0])
        )


if __name__ == "__main__":
    unittest.main()
