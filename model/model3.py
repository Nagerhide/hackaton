"""Model3: wielowidokowa diagnostyka niepełnych widm akustycznych.

Model łączy predykcje po dwóch niezależnych rekonstrukcjach braków (residual
KNN i regresja Ridge) z modelem typu usterki liczonym wyłącznie na rzeczywiście
zmierzonych pasmach. Rekonstruktor KNN uczy się bez etykiet z ``train.csv``;
``test.csv`` celowo nie jest używany do uczenia ani pseudoetykietowania.

Uruchomienie:
    .venv/bin/python model/model3.py
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf
from sklearn.linear_model import Ridge
from sklearn.metrics.pairwise import nan_euclidean_distances
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

try:  # Uruchomienie jako moduł ``model.model3``.
    from .model2 import (
        DEFAULT_CV_SEEDS,
        FAULT_LABELS,
        FREQ_COLS,
        IMPUTED_MASK_ATTR,
        LABELS,
        SEVERITIES,
        AcousticDiagnosticModel,
        SpectralVerdictExplainer,
        attach_optimization_audit,
        export_explainer,
        group_kfold_indices,
        interpolate_raw_spectra,
        load_explainer,
        prediction_frame,
        score_predictions,
        validate_schema,
    )
except ImportError:  # Uruchomienie bezpośrednie: ``python model/model3.py``.
    from model2 import (
        DEFAULT_CV_SEEDS,
        FAULT_LABELS,
        FREQ_COLS,
        IMPUTED_MASK_ATTR,
        LABELS,
        SEVERITIES,
        AcousticDiagnosticModel,
        SpectralVerdictExplainer,
        attach_optimization_audit,
        export_explainer,
        group_kfold_indices,
        interpolate_raw_spectra,
        load_explainer,
        prediction_frame,
        score_predictions,
        validate_schema,
    )


MODEL_DIR = Path(__file__).resolve().parent
PROJECT_DIR = MODEL_DIR.parent
DATA_DIR = PROJECT_DIR / "tests"
MODEL3_ANOMALY_THRESHOLD = 0.48
MODEL3_KNN_NEIGHBORS = 5
MODEL3_RIDGE_ALPHA = 1.0
MODEL3_RIDGE_LABEL_WEIGHT = 0.50
MODEL3_RIDGE_SEVERITY_WEIGHT = 0.25
MODEL3_MASKED_TYPE_WEIGHT = 0.30


def _numeric_spectra(frame: pd.DataFrame) -> np.ndarray:
    values = frame[FREQ_COLS].apply(pd.to_numeric, errors="coerce")
    return values.replace([np.inf, -np.inf], np.nan).to_numpy(float)


def _engine_peer_profiles(
    frame: pd.DataFrame,
    spectra: np.ndarray,
    fallback: np.ndarray | None = None,
) -> np.ndarray:
    numeric = frame[["engine_id"]].copy()
    numeric[FREQ_COLS] = spectra
    peer = (
        numeric.groupby("engine_id", sort=False)[FREQ_COLS]
        .transform("median")
        .to_numpy(float)
    )
    if fallback is not None:
        missing = ~np.isfinite(peer)
        peer[missing] = np.broadcast_to(fallback, peer.shape)[missing]
    return peer


class ResidualKNNImputer:
    """KNN rekonstruujący resztę cylindra względem profilu jego silnika.

    Biblioteka odniesienia może zawierać naturalne braki. Przy uzupełnianiu
    pasma wybierani są wyłącznie sąsiedzi, którzy mają to pasmo zmierzone.
    Oryginalne, obserwowane wartości nigdy nie są modyfikowane.
    """

    def __init__(self, n_neighbors: int = MODEL3_KNN_NEIGHBORS):
        if n_neighbors < 1:
            raise ValueError("n_neighbors musi być dodatnie")
        self.n_neighbors = int(n_neighbors)

    def fit(self, frames: list[pd.DataFrame] | tuple[pd.DataFrame, ...]):
        if not frames:
            raise ValueError("Rekonstruktor wymaga co najmniej jednej ramki")
        residual_parts = []
        raw_parts = []
        for frame in frames:
            validate_schema(frame)
            spectra = _numeric_spectra(frame)
            peer = _engine_peer_profiles(frame, spectra)
            residual_parts.append(spectra - peer)
            raw_parts.append(spectra)

        residuals = np.vstack(residual_parts)
        usable = np.isfinite(residuals).any(axis=1)
        if not usable.any():
            raise ValueError("Brak użytecznych profili do nauczenia rekonstruktora")
        self.reference_residuals = residuals[usable]
        raw = np.vstack(raw_parts)
        self.raw_band_median = np.nanmedian(raw, axis=0)
        self.residual_band_median = np.nanmedian(self.reference_residuals, axis=0)
        if not np.isfinite(self.raw_band_median).all():
            raise ValueError("Co najmniej jedno pasmo nie ma żadnej wartości uczącej")
        self.residual_band_median = np.nan_to_num(
            self.residual_band_median, nan=0.0
        )
        return self

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        validate_schema(frame)
        result = frame.copy()
        spectra = _numeric_spectra(result)
        missing_mask = ~np.isfinite(spectra)
        if not missing_mask.any():
            result[FREQ_COLS] = spectra
            result.attrs[IMPUTED_MASK_ATTR] = missing_mask
            return result

        if np.any(np.isfinite(spectra).sum(axis=1) == 0):
            bad = np.flatnonzero(np.isfinite(spectra).sum(axis=1) == 0)[:10]
            raise ValueError(
                "Nie można wiarygodnie odtworzyć całkowicie pustego widma; "
                f"wiersze: {bad.tolist()}"
            )

        peer = _engine_peer_profiles(result, spectra, self.raw_band_median)
        query_residuals = spectra - peer
        distances = nan_euclidean_distances(
            query_residuals, self.reference_residuals
        )
        completed = spectra.copy()

        for row_index, band_index in np.argwhere(missing_mask):
            valid = np.isfinite(self.reference_residuals[:, band_index]) & np.isfinite(
                distances[row_index]
            )
            candidates = np.flatnonzero(valid)
            if not candidates.size:
                residual = float(self.residual_band_median[band_index])
            else:
                candidate_distances = distances[row_index, candidates]
                count = min(self.n_neighbors, len(candidates))
                nearest_positions = np.argpartition(
                    candidate_distances, count - 1
                )[:count]
                nearest = candidates[nearest_positions]
                nearest_distances = distances[row_index, nearest]
                exact = nearest_distances <= 1e-12
                if exact.any():
                    residual = float(
                        np.mean(self.reference_residuals[nearest[exact], band_index])
                    )
                else:
                    residual = float(
                        np.average(
                            self.reference_residuals[nearest, band_index],
                            weights=1.0 / nearest_distances,
                        )
                    )
            completed[row_index, band_index] = peer[row_index, band_index] + residual

        if not np.isfinite(completed).all():
            raise ValueError("Rekonstruktor nie uzupełnił wszystkich brakujących pasm")
        np.testing.assert_array_equal(
            completed[~missing_mask], spectra[~missing_mask]
        )
        result[FREQ_COLS] = completed
        result.attrs[IMPUTED_MASK_ATTR] = missing_mask
        return result

    def fit_transform(self, frames, frame: pd.DataFrame) -> pd.DataFrame:
        return self.fit(frames).transform(frame)


class BandRidgeImputer:
    """Regresyjna rekonstrukcja każdego pasma z pozostałych 20 pasm.

    Najpierw powstaje bezpieczny profil peer-based z ``model2``. Regresja
    zastępuje tylko komórki, które były brakujące w surowych danych; żaden
    prawdziwy pomiar nie jest modyfikowany.
    """

    def __init__(self, alpha: float = MODEL3_RIDGE_ALPHA):
        if alpha <= 0:
            raise ValueError("alpha musi być dodatnie")
        self.alpha = float(alpha)

    def fit(self, frame: pd.DataFrame):
        validate_schema(frame)
        spectra = _numeric_spectra(frame)
        if not np.isfinite(spectra).all():
            raise ValueError("BandRidgeImputer wymaga kompletnych danych uczących")
        self.models = []
        for band_index in range(len(FREQ_COLS)):
            predictors = np.arange(len(FREQ_COLS)) != band_index
            model = make_pipeline(
                StandardScaler(), Ridge(alpha=self.alpha)
            ).fit(spectra[:, predictors], spectra[:, band_index])
            self.models.append(model)
        return self

    def transform_from_peer(self, peer_prepared: pd.DataFrame) -> pd.DataFrame:
        result = peer_prepared.copy()
        initial = _numeric_spectra(peer_prepared)
        missing_mask = peer_prepared.attrs.get(IMPUTED_MASK_ATTR)
        if missing_mask is None:
            missing_mask = np.zeros_like(initial, dtype=bool)
        missing_mask = np.asarray(missing_mask, dtype=bool)
        if missing_mask.shape != initial.shape:
            raise ValueError("Maska imputacji ma nieprawidłowy rozmiar")

        completed = initial.copy()
        for band_index, model in enumerate(self.models):
            rows = np.flatnonzero(missing_mask[:, band_index])
            if not rows.size:
                continue
            predictors = np.arange(len(FREQ_COLS)) != band_index
            completed[rows, band_index] = model.predict(
                initial[rows][:, predictors]
            )
        if not np.isfinite(completed).all():
            raise ValueError("Regresja Ridge nie uzupełniła wszystkich braków")
        np.testing.assert_array_equal(
            completed[~missing_mask], initial[~missing_mask]
        )
        result[FREQ_COLS] = completed
        result.attrs[IMPUTED_MASK_ATTR] = missing_mask
        return result

    def transform(self, raw_frame: pd.DataFrame) -> pd.DataFrame:
        return self.transform_from_peer(interpolate_raw_spectra(raw_frame))


def _incomplete_engine_residuals(frame: pd.DataFrame) -> np.ndarray:
    spectra = _numeric_spectra(frame)
    peer = _engine_peer_profiles(frame, spectra)
    return spectra - peer


def _stable_softmax(scores: np.ndarray) -> np.ndarray:
    shifted = scores - np.max(scores, axis=1, keepdims=True)
    exponential = np.exp(np.clip(shifted, -700.0, 0.0))
    return exponential / exponential.sum(axis=1, keepdims=True)


class MissingAwareTypeModel:
    """Model typu usterki marginalizujący, a nie imputujący, braki.

    Dla każdej unikalnej maski wybierany jest podzbiór kowariancji i średnich
    klas. Dzięki temu syntetyczna wartość nigdy nie może zostać dowodem typu
    usterki.
    """

    anomaly_classes = [*FAULT_LABELS, "unknown"]

    def fit(self, frame: pd.DataFrame):
        validate_schema(frame, require_labels=True)
        residuals = _incomplete_engine_residuals(frame)
        labels = frame.label.to_numpy()
        anomaly_mask = labels != "ok"
        values = residuals[anomaly_mask]
        anomaly_labels = labels[anomaly_mask]
        if not np.isfinite(values).all():
            raise ValueError("MissingAwareTypeModel wymaga kompletnych danych uczących")
        absent = [
            label for label in self.anomaly_classes if not np.any(anomaly_labels == label)
        ]
        if absent:
            raise ValueError(f"Brak klas typu usterki w danych uczących: {absent}")
        self.class_means = np.vstack(
            [
                values[anomaly_labels == label].mean(axis=0)
                for label in self.anomaly_classes
            ]
        )
        self.covariance = LedoitWolf().fit(values).covariance_
        return self

    def predict_from_residuals(self, residuals: np.ndarray) -> np.ndarray:
        residuals = np.asarray(residuals, dtype=float)
        if residuals.ndim != 2 or residuals.shape[1] != len(FREQ_COLS):
            raise ValueError("Nieprawidłowy rozmiar widma resztowego")
        result = np.zeros((len(residuals), len(self.anomaly_classes)))
        observed_masks, inverse = np.unique(
            np.isfinite(residuals), axis=0, return_inverse=True
        )
        precision_cache = getattr(self, "_precision_cache", None)
        if precision_cache is None:
            precision_cache = self._precision_cache = {}
        for mask_index, observed_mask in enumerate(observed_masks):
            rows = np.flatnonzero(inverse == mask_index)
            observed = np.flatnonzero(observed_mask)
            if not observed.size:
                result[rows] = 1.0 / len(self.anomaly_classes)
                continue
            cache_key = np.packbits(observed_mask).tobytes()
            precision = precision_cache.get(cache_key)
            if precision is None:
                covariance = self.covariance[np.ix_(observed, observed)]
                try:
                    precision = np.linalg.inv(covariance)
                except np.linalg.LinAlgError:
                    precision = np.linalg.pinv(covariance, hermitian=True)
                if len(precision_cache) >= 128:
                    precision_cache.pop(next(iter(precision_cache)))
                precision_cache[cache_key] = precision
            delta = (
                residuals[rows, None, :][:, :, observed]
                - self.class_means[None, :, :][:, :, observed]
            )
            scores = -0.5 * np.einsum(
                "rci,ij,rcj->rc", delta, precision, delta, optimize=True
            )
            result[rows] = _stable_softmax(scores)
        return result

    def predict_probabilities(self, raw_frame: pd.DataFrame) -> np.ndarray:
        validate_schema(raw_frame)
        return self.predict_from_residuals(_incomplete_engine_residuals(raw_frame))


def combine_multiview_probabilities(
    ridge_label_probability: np.ndarray,
    ridge_severity_probability: np.ndarray,
    knn_label_probability: np.ndarray,
    knn_severity_probability: np.ndarray,
    masked_type_probability: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Stały soft-vote wybrany przed końcowymi testami odporności."""
    anomaly_classes = [*FAULT_LABELS, "unknown"]
    anomaly_indices = [LABELS.index(label) for label in anomaly_classes]
    anomaly_probability = knn_label_probability[:, anomaly_indices].sum(axis=1)
    conditional_type = (
        knn_label_probability[:, anomaly_indices]
        / np.maximum(anomaly_probability[:, None], 1e-12)
    )
    masked_view = knn_label_probability.copy()
    masked_view[:, anomaly_indices] = anomaly_probability[:, None] * (
        (1.0 - MODEL3_MASKED_TYPE_WEIGHT) * conditional_type
        + MODEL3_MASKED_TYPE_WEIGHT * masked_type_probability
    )
    label_probability = (
        MODEL3_RIDGE_LABEL_WEIGHT * ridge_label_probability
        + (1.0 - MODEL3_RIDGE_LABEL_WEIGHT) * masked_view
    )
    severity_probability = (
        MODEL3_RIDGE_SEVERITY_WEIGHT * ridge_severity_probability
        + (1.0 - MODEL3_RIDGE_SEVERITY_WEIGHT) * knn_severity_probability
    )
    return label_probability, severity_probability


def decode_probabilities3(
    label_probability: np.ndarray,
    severity_probability: np.ndarray,
    anomaly_threshold: float = MODEL3_ANOMALY_THRESHOLD,
):
    if not 0.0 < anomaly_threshold < 1.0:
        raise ValueError("anomaly_threshold musi należeć do zakresu (0, 1)")
    anomaly_classes = [*FAULT_LABELS, "unknown"]
    anomaly_indices = [LABELS.index(label) for label in anomaly_classes]
    anomaly_probability = label_probability[:, anomaly_indices].sum(axis=1)
    anomaly_type = np.asarray(anomaly_classes)[
        label_probability[:, anomaly_indices].argmax(axis=1)
    ]
    labels = np.where(
        anomaly_probability >= anomaly_threshold, anomaly_type, "ok"
    )
    severities = np.asarray(SEVERITIES)[severity_probability.argmax(axis=1)]
    confidence = np.maximum(anomaly_probability, 1.0 - anomaly_probability)
    fault_mask = np.isin(labels, FAULT_LABELS)
    confidence[fault_mask] = np.minimum(
        confidence[fault_mask], severity_probability.max(axis=1)[fault_mask]
    )
    return labels, np.where(fault_mask, severities, "nie_dotyczy"), confidence


class RobustProbabilityEnsemble:
    """Soft-vote wielu modeli nad trzema komplementarnymi widokami danych."""

    def __init__(
        self,
        models,
        ridge_imputers,
        masked_type_models,
        knn_imputer: ResidualKNNImputer,
        anomaly_threshold: float = MODEL3_ANOMALY_THRESHOLD,
    ):
        self.models = list(models)
        if not self.models:
            raise ValueError("Ensemble nie może być pusty")
        self.ridge_imputers = list(ridge_imputers)
        self.masked_type_models = list(masked_type_models)
        if not (
            len(self.models)
            == len(self.ridge_imputers)
            == len(self.masked_type_models)
        ):
            raise ValueError("Każdy model musi mieć Ridge i model masked-type")
        self.knn_imputer = knn_imputer
        self.anomaly_threshold = float(anomaly_threshold)

    def _component_probabilities(
        self, raw_frame: pd.DataFrame, knn_prepared: pd.DataFrame | None = None
    ):
        validate_schema(raw_frame)
        if knn_prepared is None:
            knn_prepared = self.knn_imputer.transform(raw_frame)
        try:
            peer_prepared = interpolate_raw_spectra(raw_frame)
        except ValueError as error:
            if "nie ma żadnego pomiaru dla pasm" not in str(error):
                raise
            peer_prepared = knn_prepared.copy()
            peer_prepared.attrs[IMPUTED_MASK_ATTR] = knn_prepared.attrs[
                IMPUTED_MASK_ATTR
            ]
        raw_residuals = _incomplete_engine_residuals(raw_frame)
        ridge_probabilities = []
        knn_probabilities = []
        type_probabilities = []
        for model, ridge_imputer, type_model in zip(
            self.models, self.ridge_imputers, self.masked_type_models
        ):
            ridge_prepared = ridge_imputer.transform_from_peer(peer_prepared)
            ridge_probabilities.append(model.predict_probabilities(ridge_prepared))
            knn_probabilities.append(model.predict_probabilities(knn_prepared))
            type_probabilities.append(
                type_model.predict_from_residuals(raw_residuals)
            )
        return ridge_probabilities, knn_probabilities, type_probabilities

    @staticmethod
    def _average_components(ridge_probabilities, knn_probabilities, type_probabilities):
        ridge_label = np.mean([item[0] for item in ridge_probabilities], axis=0)
        ridge_severity = np.mean([item[1] for item in ridge_probabilities], axis=0)
        knn_label = np.mean([item[0] for item in knn_probabilities], axis=0)
        knn_severity = np.mean([item[1] for item in knn_probabilities], axis=0)
        masked_type = np.mean(type_probabilities, axis=0)
        return combine_multiview_probabilities(
            ridge_label,
            ridge_severity,
            knn_label,
            knn_severity,
            masked_type,
        )

    def predict_probabilities(self, raw_frame: pd.DataFrame):
        return self._average_components(
            *self._component_probabilities(raw_frame)
        )

    def predict_with_details(
        self, raw_frame: pd.DataFrame, knn_prepared: pd.DataFrame | None = None
    ):
        ridge_probabilities, knn_probabilities, type_probabilities = (
            self._component_probabilities(raw_frame, knn_prepared=knn_prepared)
        )
        label_probability, severity_probability = self._average_components(
            ridge_probabilities, knn_probabilities, type_probabilities
        )
        labels, severities, probability_score = decode_probabilities3(
            label_probability, severity_probability, self.anomaly_threshold
        )

        individual = [
            decode_probabilities3(
                *combine_multiview_probabilities(
                    ridge_probability[0],
                    ridge_probability[1],
                    knn_probability[0],
                    knn_probability[1],
                    type_probability,
                ),
                self.anomaly_threshold,
            )
            for ridge_probability, knn_probability, type_probability in zip(
                ridge_probabilities, knn_probabilities, type_probabilities
            )
        ]
        label_votes = np.vstack([item[0] for item in individual])
        severity_votes = np.vstack([item[1] for item in individual])
        label_confidence = (label_votes == labels[None, :]).mean(axis=0)
        severity_confidence = (severity_votes == severities[None, :]).mean(axis=0)
        joint_confidence = (
            (label_votes == labels[None, :])
            & (severity_votes == severities[None, :])
        ).mean(axis=0)
        fault_mask = np.isin(labels, FAULT_LABELS)
        vote_confidence = np.where(
            fault_mask, joint_confidence, label_confidence
        )
        return labels, severities, {
            "vote_confidence": vote_confidence,
            "label_vote_confidence": label_confidence,
            "severity_vote_confidence": severity_confidence,
            "probability_score": probability_score,
            "n_model_votes": np.full(len(raw_frame), len(self.models), dtype=int),
        }

    def predict(self, raw_frame: pd.DataFrame):
        labels, severities, details = self.predict_with_details(raw_frame)
        return labels, severities, details["vote_confidence"]


class Model3Bundle:
    """Eksportowany komplet: rekonstruktor, skalery i ensemble klasyfikatorów."""

    def __init__(self, imputer: ResidualKNNImputer, classifier):
        self.imputer = imputer
        self.classifier = classifier
        if getattr(classifier, "knn_imputer", imputer) is not imputer:
            raise ValueError("Bundle i classifier muszą współdzielić rekonstruktor KNN")

    def prepare(self, raw_frame: pd.DataFrame) -> pd.DataFrame:
        return self.imputer.transform(raw_frame)

    def predict_probabilities(self, raw_frame: pd.DataFrame):
        return self.classifier.predict_probabilities(raw_frame)

    def predict_with_details(self, raw_frame: pd.DataFrame):
        return self.classifier.predict_with_details(raw_frame)

    def predict(self, raw_frame: pd.DataFrame):
        labels, severities, details = self.predict_with_details(raw_frame)
        return labels, severities, details["vote_confidence"]


def export_model(bundle: Model3Bundle, path: Path) -> None:
    if __name__ == "__main__":
        sys.modules["model3"] = sys.modules[__name__]
        for cls in (
            ResidualKNNImputer,
            BandRidgeImputer,
            MissingAwareTypeModel,
            RobustProbabilityEnsemble,
            Model3Bundle,
        ):
            cls.__module__ = "model3"
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "format_version": 3,
            "bundle": bundle,
            "model": bundle,
            "frequency_columns": FREQ_COLS,
            "labels": LABELS,
            "severities": SEVERITIES,
            "preprocessing": {
                "imputers": ["engine_residual_knn", "band_ridge"],
                "n_neighbors": bundle.imputer.n_neighbors,
                "ridge_alpha": MODEL3_RIDGE_ALPHA,
                "uses_unlabeled_train": True,
                "uses_test_data": False,
                "normalization": "StandardScaler stored in every base classifier",
            },
            "ensemble": {
                "ridge_label_weight": MODEL3_RIDGE_LABEL_WEIGHT,
                "ridge_severity_weight": MODEL3_RIDGE_SEVERITY_WEIGHT,
                "masked_type_weight": MODEL3_MASKED_TYPE_WEIGHT,
                "masked_type_uses_only_observed_bands": True,
            },
            "anomaly_threshold": bundle.classifier.anomaly_threshold,
        },
        path,
        compress=3,
    )


def load_exported_model(path: Path | str) -> Model3Bundle:
    artifact = joblib.load(Path(path))
    if artifact.get("format_version") != 3:
        raise ValueError("Nieobsługiwana wersja model3")
    return artifact.get("bundle", artifact["model"])


def _no_confidence_optimization_audit(predictions: pd.DataFrame) -> pd.DataFrame:
    audit = predictions[
        ["engine_id", "cylinder", "label", "severity", "vote_confidence"]
    ].copy()
    audit["confidence_optimization_applied"] = False
    audit["label_before_optimization"] = audit.label
    audit["severity_before_optimization"] = audit.severity
    audit["confidence_before_optimization"] = audit.vote_confidence
    audit["confidence_after_optimization"] = audit.vote_confidence
    audit["confidence_gain"] = 0.0
    audit["optimization_adjusted_columns"] = ""
    audit["optimization_candidate_evaluations"] = 0
    audit["imputation_strategy"] = "soft_vote_knn5_ridge_observed_only"
    return audit.drop(columns=["label", "severity", "vote_confidence"])


@lru_cache(maxsize=8)
def _load_server_artifacts(classifier_path: str, explainer_path: str):
    return (
        load_exported_model(Path(classifier_path)),
        load_explainer(Path(explainer_path)),
    )


def clear_model_cache() -> None:
    _load_server_artifacts.cache_clear()


def predict_and_explain(
    classifier_path: Path,
    explainer_path: Path,
    raw_frame: pd.DataFrame,
    include_bands: bool = True,
    include_band_scores: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    bundle, explainer = _load_server_artifacts(
        str(Path(classifier_path).resolve()), str(Path(explainer_path).resolve())
    )
    prepared = bundle.prepare(raw_frame)
    labels, severities, details = bundle.classifier.predict_with_details(
        raw_frame, knn_prepared=prepared
    )
    prediction_with_confidence = raw_frame[
        ["engine_id", "cylinder"]
    ].reset_index(drop=True).copy()
    prediction_with_confidence["label"] = labels
    prediction_with_confidence["severity"] = severities
    prediction_with_confidence["confidence"] = details["vote_confidence"]
    for name, values in details.items():
        prediction_with_confidence[name] = values
    explanations, bands = explainer.explain(
        prepared,
        prediction_with_confidence,
        include_bands=include_bands,
        include_band_scores=include_band_scores,
    )
    explanations = attach_optimization_audit(
        explanations,
        _no_confidence_optimization_audit(prediction_with_confidence),
    )
    predictions = prediction_with_confidence[
        ["engine_id", "cylinder", "label", "severity"]
    ].copy()
    return predictions, explanations, bands


def predict_raw_data(classifier_path: Path, raw_frame: pd.DataFrame) -> pd.DataFrame:
    bundle = load_exported_model(classifier_path)
    return prediction_frame(bundle, raw_frame)[
        ["engine_id", "cylinder", "label", "severity"]
    ].copy()


def display_explanations(
    explanations: pd.DataFrame,
    only_anomalies: bool = False,
    limit: int | None = None,
) -> None:
    rows = explanations[explanations.label.ne("ok")] if only_anomalies else explanations
    if limit is not None:
        rows = rows.head(limit)
    for row in rows.itertuples(index=False):
        print(
            f"[{row.engine_id} / cylinder {row.cylinder}] "
            f"{str(row.label).upper()} ({row.severity}) | "
            f"vote confidence={100.0 * row.vote_confidence:.1f}% | "
            f"{row.explanation}"
        )


def _frame_from_server_input(data) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, (list, tuple)):
        return pd.DataFrame(list(data))
    if isinstance(data, dict):
        if "records" in data:
            return pd.DataFrame(data["records"])
        if "data" in data:
            return pd.DataFrame(data["data"])
        if all(np.isscalar(value) or value is None for value in data.values()):
            return pd.DataFrame([data])
        return pd.DataFrame(data)
    raise TypeError("data musi być DataFrame, listą rekordów albo słownikiem JSON")


def predict(
    data,
    classifier_path: Path | str = MODEL_DIR / "acoustic_model3.pkl",
    explainer_path: Path | str = MODEL_DIR / "verdict_explainer3.pkl",
    include_bands: bool = False,
    display: bool = False,
) -> dict:
    """Zewnętrzne API zgodne z model2 i backendem serwera."""
    frame = _frame_from_server_input(data)
    _, explanations, bands = predict_and_explain(
        Path(classifier_path),
        Path(explainer_path),
        frame,
        include_bands=include_bands,
        include_band_scores=include_bands,
    )
    if display:
        display_explanations(explanations)
    response = {
        "results": json.loads(explanations.to_json(orient="records")),
        "model_votes": int(explanations.n_model_votes.iloc[0])
        if len(explanations)
        else 0,
    }
    if include_bands:
        response["bands"] = json.loads(bands.to_json(orient="records"))
    return response


def predict_stages(
    data,
    classifier_path: Path | str = MODEL_DIR / "acoustic_model3.pkl",
    explainer_path: Path | str = MODEL_DIR / "verdict_explainer3.pkl",
    include_bands: bool = False,
):
    """Zwraca predykcję przed obliczeniem pełnego wyjaśnienia."""
    frame = _frame_from_server_input(data)
    bundle, explainer = _load_server_artifacts(
        str(Path(classifier_path).resolve()), str(Path(explainer_path).resolve())
    )
    prepared = bundle.prepare(frame)
    labels, severities, details = bundle.classifier.predict_with_details(
        frame, knn_prepared=prepared
    )
    predictions = frame[["engine_id", "cylinder"]].reset_index(drop=True).copy()
    predictions["label"] = labels
    predictions["severity"] = severities
    predictions["confidence"] = details["vote_confidence"]
    for name, values in details.items():
        predictions[name] = values
    quick_columns = [
        "engine_id",
        "cylinder",
        "label",
        "severity",
        "confidence",
        "vote_confidence",
        "label_vote_confidence",
        "severity_vote_confidence",
        "n_model_votes",
    ]
    yield {
        "stage": "predictions",
        "results": json.loads(predictions[quick_columns].to_json(orient="records")),
        "model_votes": int(predictions.n_model_votes.iloc[0]) if len(predictions) else 0,
    }
    explanations, bands = explainer.explain(
        prepared,
        predictions,
        include_bands=include_bands,
        include_band_scores=include_bands,
    )
    explanations = attach_optimization_audit(
        explanations, _no_confidence_optimization_audit(predictions)
    )
    response = {
        "stage": "complete",
        "results": json.loads(explanations.to_json(orient="records")),
        "model_votes": int(explanations.n_model_votes.iloc[0])
        if len(explanations)
        else 0,
    }
    if include_bands:
        response["bands"] = json.loads(bands.to_json(orient="records"))
    yield response


def train_repeated_ensemble(
    validation: pd.DataFrame,
    unlabeled_train: pd.DataFrame,
    n_splits: int = 5,
    seeds=DEFAULT_CV_SEEDS,
):
    ridge_label_sum = np.zeros((len(validation), len(LABELS)))
    ridge_severity_sum = np.zeros((len(validation), len(SEVERITIES)))
    knn_label_sum = np.zeros((len(validation), len(LABELS)))
    knn_severity_sum = np.zeros((len(validation), len(SEVERITIES)))
    masked_type_sum = np.zeros((len(validation), len(FAULT_LABELS) + 1))
    counts = np.zeros(len(validation), dtype=int)
    models = []
    ridge_imputers = []
    masked_type_models = []
    for repeat, seed in enumerate(seeds, start=1):
        for train_indices, validation_indices in group_kfold_indices(
            validation, n_splits, seed
        ):
            train = validation.iloc[train_indices].reset_index(drop=True)
            query = validation.iloc[validation_indices].reset_index(drop=True)
            model = AcousticDiagnosticModel().fit(train)
            ridge_imputer = BandRidgeImputer().fit(train)
            masked_type_model = MissingAwareTypeModel().fit(train)
            fold_knn_imputer = ResidualKNNImputer().fit(
                [unlabeled_train, train]
            )

            ridge_label, ridge_severity = model.predict_probabilities(
                ridge_imputer.transform(query)
            )
            knn_label, knn_severity = model.predict_probabilities(
                fold_knn_imputer.transform(query)
            )
            masked_type = masked_type_model.predict_probabilities(query)
            ridge_label_sum[validation_indices] += ridge_label
            ridge_severity_sum[validation_indices] += ridge_severity
            knn_label_sum[validation_indices] += knn_label
            knn_severity_sum[validation_indices] += knn_severity
            masked_type_sum[validation_indices] += masked_type
            counts[validation_indices] += 1
            models.append(model)
            ridge_imputers.append(ridge_imputer)
            masked_type_models.append(masked_type_model)
        label_probability, severity_probability = combine_multiview_probabilities(
            ridge_label_sum / np.maximum(counts[:, None], 1),
            ridge_severity_sum / np.maximum(counts[:, None], 1),
            knn_label_sum / np.maximum(counts[:, None], 1),
            knn_severity_sum / np.maximum(counts[:, None], 1),
            masked_type_sum / np.maximum(counts[:, None], 1),
        )
        labels, severities, _ = decode_probabilities3(
            label_probability, severity_probability
        )
        score = score_predictions(
            validation, pd.DataFrame({"label": labels, "severity": severities})
        )
        print(
            f"  repeat {repeat}/{len(seeds)} (seed={seed}): "
            f"cumulative raw_score={score['raw_score']:.4f}"
        )
    label_probability, severity_probability = combine_multiview_probabilities(
        ridge_label_sum / counts[:, None],
        ridge_severity_sum / counts[:, None],
        knn_label_sum / counts[:, None],
        knn_severity_sum / counts[:, None],
        masked_type_sum / counts[:, None],
    )
    labels, severities, confidence = decode_probabilities3(
        label_probability, severity_probability
    )
    return (
        models,
        ridge_imputers,
        masked_type_models,
        pd.DataFrame(
            {"label": labels, "severity": severities, "confidence": confidence}
        ),
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument(
        "--model-output", type=Path, default=MODEL_DIR / "acoustic_model3.pkl"
    )
    parser.add_argument(
        "--explainer-output",
        type=Path,
        default=MODEL_DIR / "verdict_explainer3.pkl",
    )
    parser.add_argument(
        "--predictions-output",
        type=Path,
        default=MODEL_DIR / "model3_predictions.csv",
    )
    parser.add_argument(
        "--explanations-output",
        type=Path,
        default=MODEL_DIR / "model3_explanations.csv",
    )
    parser.add_argument(
        "--bands-output",
        type=Path,
        default=MODEL_DIR / "model3_band_explanations.csv",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.repeats <= len(DEFAULT_CV_SEEDS):
        raise ValueError(
            f"repeats musi należeć do zakresu 1–{len(DEFAULT_CV_SEEDS)}"
        )
    validation = pd.read_csv(args.data_dir / "val.csv").reset_index(drop=True)
    unlabeled_train = pd.read_csv(args.data_dir / "train.csv").reset_index(drop=True)
    test = pd.read_csv(args.data_dir / "test.csv").reset_index(drop=True)
    validate_schema(validation, require_labels=True)
    validate_schema(unlabeled_train)
    validate_schema(test)

    selected_seeds = DEFAULT_CV_SEEDS[: args.repeats]
    print(
        f"Model3: KNN{MODEL3_KNN_NEIGHBORS} + Ridge + observed-only type + "
        f"Group {args.folds}-Fold × {len(selected_seeds)}"
    )
    fold_models, fold_ridge_imputers, fold_type_models, clean_oof = (
        train_repeated_ensemble(
            validation, unlabeled_train, args.folds, selected_seeds
        )
    )
    print("Czysty wynik OOF:")
    for name, value in score_predictions(validation, clean_oof).items():
        print(f"  {name}: {value:.4f}")

    final_model = AcousticDiagnosticModel().fit(validation)
    final_ridge_imputer = BandRidgeImputer().fit(validation)
    final_type_model = MissingAwareTypeModel().fit(validation)
    imputer = ResidualKNNImputer().fit([unlabeled_train, validation])
    classifier = RobustProbabilityEnsemble(
        [*fold_models, final_model],
        [*fold_ridge_imputers, final_ridge_imputer],
        [*fold_type_models, final_type_model],
        imputer,
    )
    bundle = Model3Bundle(imputer, classifier)
    export_model(bundle, args.model_output)

    prepared_test = bundle.prepare(test)
    prediction_with_confidence = prediction_frame(bundle, test)
    args.predictions_output.parent.mkdir(parents=True, exist_ok=True)
    prediction_with_confidence[
        ["engine_id", "cylinder", "label", "severity"]
    ].to_csv(args.predictions_output, index=False)

    explainer = SpectralVerdictExplainer().fit(validation)
    export_explainer(explainer, args.explainer_output)
    explanations, bands = explainer.explain(prepared_test, prediction_with_confidence)
    explanations = attach_optimization_audit(
        explanations,
        _no_confidence_optimization_audit(prediction_with_confidence),
    )
    args.explanations_output.parent.mkdir(parents=True, exist_ok=True)
    args.bands_output.parent.mkdir(parents=True, exist_ok=True)
    explanations.to_csv(args.explanations_output, index=False)
    bands.to_csv(args.bands_output, index=False)

    print(f"Zapisano model: {args.model_output.resolve()}")
    print(f"Zapisano explainer: {args.explainer_output.resolve()}")
    print(f"Zapisano predykcje: {args.predictions_output.resolve()}")


if __name__ == "__main__":
    main()
