"""Model diagnostyczny z osobnym modelem wyjaśniającym werdykt.

Program zawiera samodzielny hierarchiczny ensemble i dodatkowo uczy
``SpectralVerdictExplainer``. Explainer nie zmienia predykcji klasyfikatora:
porównuje cylinder ze zdrową referencją i zaznacza najbardziej nietypowy,
ciągły fragment widma.

Uruchomienie:
    .venv/bin/python model/model2.py
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
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC


MODEL_DIR = Path(__file__).resolve().parent
PROJECT_DIR = MODEL_DIR.parent
DATA_DIR = PROJECT_DIR / "tests"
FREQ_COLS = [f"mV_{index}" for index in range(21)]
LABELS = ["ok", "zakoksowany", "lejacy", "pompa", "iglica", "unknown"]
FAULT_LABELS = ["zakoksowany", "lejacy", "pompa", "iglica"]
SEVERITIES = ["male", "srednie", "duze"]
DEFAULT_CV_SEEDS = [1, 7, 13, 21, 42, 84, 123, 256, 777, 2026]
IMPUTED_MASK_ATTR = "imputed_frequency_mask"
LOW_CONFIDENCE_THRESHOLD = 0.75
CONFIDENCE_OPTIMIZATION_MIN_GAIN = 1e-4
CONFIDENCE_CANDIDATE_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


def validate_schema(frame: pd.DataFrame, require_labels: bool = False) -> None:
    required = {"engine_id", "cylinder", "n_cylinders", *FREQ_COLS}
    if require_labels:
        required.update({"label", "severity"})
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Brak wymaganych kolumn: {missing}")
    group_sizes = frame.groupby("engine_id").size()
    declared_sizes = frame.groupby("engine_id").n_cylinders.first()
    incomplete = group_sizes[group_sizes != declared_sizes]
    if not incomplete.empty:
        raise ValueError(
            "Explainer i klasyfikator wymagają wszystkich cylindrów silnika; "
            f"niekompletne engine_id: {incomplete.index.tolist()[:10]}"
        )


def interpolate_raw_spectra(frame: pd.DataFrame) -> pd.DataFrame:
    """Uzupełnia braki profilem pozostałych cylindrów tego samego silnika.

    Liniowa interpolacja wzdłuż jednego widma potrafi tworzyć nieistniejące
    piki. Tutaj brakujące pasmo bierze poziom mediany innych cylindrów, a
    lokalne przesunięcie cylindra wyznaczają najbliższe rzeczywiste pomiary po
    lewej i prawej stronie. Pierwotna maska braków zostaje zachowana dla
    explainera, który nie może uznać wartości imputowanej za dowód usterki.
    """
    validate_schema(frame)
    result = frame.copy()
    missing = [column for column in FREQ_COLS if column not in result.columns]
    if missing:
        raise ValueError(f"Brak kolumn częstotliwości: {missing}")

    numeric = result[FREQ_COLS].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.replace([np.inf, -np.inf], np.nan)
    spectra = numeric.to_numpy(dtype=float, copy=True)
    imputed_mask = ~np.isfinite(spectra)

    for engine_id, positions in result.groupby("engine_id", sort=False).indices.items():
        positions = np.asarray(positions, dtype=int)
        engine_spectra = spectra[positions].copy()
        peer_profile = pd.DataFrame(engine_spectra).median(axis=0).to_numpy(dtype=float)
        bands_with_missing = np.any(~np.isfinite(engine_spectra), axis=0)
        missing_reference = np.flatnonzero(
            bands_with_missing & ~np.isfinite(peer_profile)
        )
        if missing_reference.size:
            columns = [FREQ_COLS[index] for index in missing_reference]
            raise ValueError(
                f"Silnik {engine_id} nie ma żadnego pomiaru dla pasm: {columns}"
            )

        for local_row, global_row in enumerate(positions):
            row = engine_spectra[local_row]
            observed = np.flatnonzero(np.isfinite(row) & np.isfinite(peer_profile))
            if not observed.size:
                cylinder = result.iloc[global_row].cylinder
                raise ValueError(
                    f"Silnik {engine_id}, cylinder {cylinder}: brak wszystkich pomiarów"
                )

            for band in np.flatnonzero(~np.isfinite(row)):
                left = observed[observed < band][-1:]
                right = observed[observed > band][:1]
                neighbours = np.concatenate([left, right])
                if not neighbours.size:
                    neighbours = observed
                local_offset = float(
                    np.median(row[neighbours] - peer_profile[neighbours])
                )
                spectra[global_row, band] = peer_profile[band] + local_offset

    if not np.isfinite(spectra).all():
        raise ValueError("Nie można wiarygodnie uzupełnić wszystkich braków widma")
    result[FREQ_COLS] = spectra
    result.attrs[IMPUTED_MASK_ATTR] = imputed_mask
    return result


def build_features(frame: pd.DataFrame) -> np.ndarray:
    """Tworzy widmo względne, jego pochodne, statystyki i typ silnika."""
    spectrum = frame[FREQ_COLS].to_numpy(dtype=float)
    engine_median = (
        frame.groupby("engine_id", sort=False)[FREQ_COLS]
        .transform("median")
        .to_numpy(dtype=float)
    )
    relative = spectrum - engine_median
    slopes = np.diff(relative, axis=1)
    summaries = np.column_stack(
        [
            relative.mean(axis=1),
            relative.std(axis=1),
            np.abs(relative).mean(axis=1),
            np.abs(relative).max(axis=1),
            relative.min(axis=1),
            relative.max(axis=1),
        ]
    )
    cylinder_count = np.column_stack(
        [(frame.n_cylinders.to_numpy() == value) for value in (8, 12, 16)]
    ).astype(float)
    return np.column_stack([relative, slopes, summaries, cylinder_count])


def _lda(shrinkage="auto"):
    return make_pipeline(
        StandardScaler(),
        LinearDiscriminantAnalysis(solver="lsqr", shrinkage=shrinkage),
    )


def decode_probabilities(label_probability, severity_probability):
    anomaly_classes = [*FAULT_LABELS, "unknown"]
    anomaly_indices = [LABELS.index(label) for label in anomaly_classes]
    anomaly_probability = label_probability[:, anomaly_indices].sum(axis=1)
    anomaly_type = np.asarray(anomaly_classes)[
        label_probability[:, anomaly_indices].argmax(axis=1)
    ]
    labels = np.where(anomaly_probability >= 0.5, anomaly_type, "ok")
    severities = np.asarray(SEVERITIES)[severity_probability.argmax(axis=1)]
    confidence = np.maximum(anomaly_probability, 1.0 - anomaly_probability)
    fault_mask = np.isin(labels, FAULT_LABELS)
    confidence[fault_mask] = np.minimum(
        confidence[fault_mask], severity_probability.max(axis=1)[fault_mask]
    )
    return labels, np.where(fault_mask, severities, "nie_dotyczy"), confidence


class AcousticDiagnosticModel:
    """Hierarchia SVC anomaly → LDA type → LDA severity."""

    def fit(self, frame: pd.DataFrame) -> "AcousticDiagnosticModel":
        validate_schema(frame, require_labels=True)
        self.input_scaler = StandardScaler().fit(frame[FREQ_COLS])
        transformed = self._transform_input(frame)
        features = build_features(transformed)
        labels = frame.label.to_numpy()

        anomaly_target = np.where(labels == "ok", "ok", "anomaly")
        self.anomaly_model = make_pipeline(
            StandardScaler(), SVC(C=4.0, class_weight="balanced")
        ).fit(features, anomaly_target)

        anomaly_mask = labels != "ok"
        self.type_model = _lda().fit(features[anomaly_mask], labels[anomaly_mask])

        fault_mask = np.isin(labels, FAULT_LABELS)
        severity_type = np.column_stack(
            [labels[fault_mask] == label for label in FAULT_LABELS]
        ).astype(float)
        self.severity_model = _lda(shrinkage=0.05).fit(
            np.column_stack([features[fault_mask], severity_type]),
            frame.severity.to_numpy()[fault_mask],
        )
        return self

    def _transform_input(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        result[FREQ_COLS] = self.input_scaler.transform(frame[FREQ_COLS])
        return result

    @staticmethod
    def _aligned_probability(model, features, classes):
        probability = model.predict_proba(features)
        aligned = np.zeros((len(features), len(classes)), dtype=float)
        for index, label in enumerate(classes):
            available = np.where(model.classes_ == label)[0]
            if available.size:
                aligned[:, index] = probability[:, available[0]]
        return aligned

    def predict_probabilities(self, frame: pd.DataFrame):
        features = build_features(self._transform_input(frame))
        decision = self.anomaly_model.decision_function(features)
        positive = 1.0 / (1.0 + np.exp(-np.clip(decision, -40, 40)))
        anomaly_probability = (
            positive if self.anomaly_model.classes_[1] == "anomaly" else 1.0 - positive
        )
        anomaly_classes = [*FAULT_LABELS, "unknown"]
        type_probability = self._aligned_probability(
            self.type_model, features, anomaly_classes
        )
        label_probability = np.zeros((len(frame), len(LABELS)), dtype=float)
        label_probability[:, LABELS.index("ok")] = 1.0 - anomaly_probability
        for index, label in enumerate(anomaly_classes):
            label_probability[:, LABELS.index(label)] = (
                anomaly_probability * type_probability[:, index]
            )

        predicted_type = np.asarray(anomaly_classes)[type_probability.argmax(axis=1)]
        severity_type = np.column_stack(
            [predicted_type == label for label in FAULT_LABELS]
        ).astype(float)
        severity_probability = self._aligned_probability(
            self.severity_model,
            np.column_stack([features, severity_type]),
            SEVERITIES,
        )
        return label_probability, severity_probability

    def predict(self, frame: pd.DataFrame):
        return decode_probabilities(*self.predict_probabilities(frame))


class ProbabilityEnsemble:
    def __init__(self, models):
        self.models = list(models)
        if not self.models:
            raise ValueError("Ensemble nie może być pusty")

    def predict_with_details(self, frame: pd.DataFrame):
        probabilities = [model.predict_probabilities(frame) for model in self.models]
        label_probability = np.mean([item[0] for item in probabilities], axis=0)
        severity_probability = np.mean([item[1] for item in probabilities], axis=0)
        labels, severities, probability_score = decode_probabilities(
            label_probability, severity_probability
        )

        individual = [decode_probabilities(*item) for item in probabilities]
        label_votes = np.vstack([item[0] for item in individual])
        severity_votes = np.vstack([item[1] for item in individual])
        label_vote_confidence = (label_votes == labels[None, :]).mean(axis=0)
        severity_vote_confidence = (severity_votes == severities[None, :]).mean(axis=0)
        joint_vote_confidence = (
            (label_votes == labels[None, :])
            & (severity_votes == severities[None, :])
        ).mean(axis=0)
        fault_mask = np.isin(labels, FAULT_LABELS)
        vote_confidence = np.where(
            fault_mask, joint_vote_confidence, label_vote_confidence
        )
        details = {
            "vote_confidence": vote_confidence,
            "label_vote_confidence": label_vote_confidence,
            "severity_vote_confidence": severity_vote_confidence,
            "probability_score": probability_score,
            "n_model_votes": np.full(len(frame), len(self.models), dtype=int),
        }
        return labels, severities, details

    def predict(self, frame: pd.DataFrame):
        labels, severities, details = self.predict_with_details(frame)
        return labels, severities, details["vote_confidence"]


def prediction_frame(model, frame: pd.DataFrame) -> pd.DataFrame:
    if hasattr(model, "predict_with_details"):
        labels, severities, details = model.predict_with_details(frame)
    else:
        labels, severities, confidence = model.predict(frame)
        details = {
            "vote_confidence": confidence,
            "label_vote_confidence": confidence,
            "severity_vote_confidence": confidence,
            "probability_score": confidence,
            "n_model_votes": np.ones(len(frame), dtype=int),
        }
    result = frame[["engine_id", "cylinder"]].reset_index(drop=True).copy()
    result.attrs = {}
    result["label"] = labels
    result["severity"] = severities
    result["confidence"] = details["vote_confidence"]
    for name, values in details.items():
        result[name] = values
    return result


def optimize_imputed_spectra(
    model,
    frame: pd.DataFrame,
    imputed_mask: np.ndarray | None = None,
    confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
    max_passes: int = 2,
    return_predictions: bool = False,
    baseline_predictions: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame] | tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Poprawia pewność, zmieniając wyłącznie brakujące punkty w bezpiecznym zakresie.

    Kandydaci są kwantylami rzeczywistych wartości pozostałych cylindrów tego
    samego silnika. Zmiana jest przyjmowana, gdy zwiększa confidence głosowania
    albo — przy remisie głosów — wynik probabilistyczny. Odrzucamy kandydatów,
    które zmieniają werdykt lub obniżają confidence jakiegokolwiek innego
    cylindra. Typ werdyktu optymalizowanego cylindra też
    pozostaje zamrożony, aby nie wybierać etykiety przez sztuczne pompowanie
    confidence.
    """
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("Próg confidence musi należeć do zakresu 0–1")
    if max_passes < 1:
        raise ValueError("max_passes musi być dodatnie")

    optimized = frame.copy()
    if imputed_mask is None:
        imputed_mask = frame.attrs.get(IMPUTED_MASK_ATTR)
    if imputed_mask is None:
        imputed_mask = np.zeros((len(frame), len(FREQ_COLS)), dtype=bool)
    imputed_mask = np.asarray(imputed_mask, dtype=bool)
    if imputed_mask.shape != (len(frame), len(FREQ_COLS)):
        raise ValueError("Maska imputacji ma nieprawidłowy rozmiar")

    if baseline_predictions is None:
        baseline_predictions = prediction_frame(model, optimized)
    else:
        baseline_predictions = baseline_predictions.reset_index(drop=True).copy()
        if len(baseline_predictions) != len(optimized):
            raise ValueError("Bazowa predykcja ma nieprawidłową liczbę wierszy")
    final_predictions = baseline_predictions.copy()
    before_label = baseline_predictions.label.to_numpy(dtype=object)
    before_severity = baseline_predictions.severity.to_numpy(dtype=object)
    before_confidence = baseline_predictions.vote_confidence.to_numpy(dtype=float)
    after_confidence = np.zeros(len(frame), dtype=float)
    adjusted_columns = [set() for _ in range(len(frame))]
    candidate_evaluations = np.zeros(len(frame), dtype=int)

    def is_better(candidate, current) -> bool:
        candidate_vote = float(candidate.vote_confidence)
        current_vote = float(current.vote_confidence)
        if candidate_vote > current_vote + 1e-12:
            return True
        return (
            abs(candidate_vote - current_vote) <= 1e-12
            and float(candidate.probability_score)
            > float(current.probability_score) + CONFIDENCE_OPTIMIZATION_MIN_GAIN
        )

    for _, positions in optimized.groupby("engine_id", sort=False).indices.items():
        positions = np.asarray(positions, dtype=int)
        engine_frame = optimized.iloc[positions].copy()
        engine_mask = imputed_mask[positions]
        current_predictions = baseline_predictions.iloc[positions].reset_index(drop=True)

        row_order = np.argsort(
            current_predictions.vote_confidence.to_numpy(dtype=float)
        )
        for local_row in row_order:
            missing_bands = np.flatnonzero(engine_mask[local_row])
            if not missing_bands.size:
                continue
            if (
                float(current_predictions.iloc[local_row].vote_confidence)
                >= confidence_threshold
            ):
                continue

            for _ in range(max_passes):
                pass_improved = False
                for band in missing_bands:
                    observed_peers = engine_frame.loc[
                        ~engine_mask[:, band], FREQ_COLS[band]
                    ].to_numpy(dtype=float)
                    if observed_peers.size < 2:
                        continue
                    current_point = float(
                        engine_frame.iloc[local_row][FREQ_COLS[band]]
                    )
                    peer_quantiles = np.quantile(
                        observed_peers, CONFIDENCE_CANDIDATE_QUANTILES
                    )
                    peer_iqr = float(
                        np.quantile(observed_peers, 0.75)
                        - np.quantile(observed_peers, 0.25)
                    )
                    max_shift = max(0.05, 0.35 * peer_iqr)
                    candidates = np.unique(
                        np.concatenate(
                            [
                                [current_point],
                                current_point
                                + np.clip(
                                    peer_quantiles - current_point,
                                    -max_shift,
                                    max_shift,
                                ),
                            ]
                        )
                    )
                    best_predictions = current_predictions
                    best_value = float(engine_frame.iloc[local_row][FREQ_COLS[band]])
                    best_row = current_predictions.iloc[local_row]

                    trial_values = [
                        float(candidate)
                        for candidate in candidates
                        if not np.isclose(
                            candidate, best_value, rtol=0.0, atol=1e-12
                        )
                    ]
                    trial_frames = []
                    for trial_index, candidate in enumerate(trial_values):
                        trial = engine_frame.copy()
                        trial.loc[trial.index[local_row], FREQ_COLS[band]] = candidate
                        trial["engine_id"] = (
                            f"__confidence_trial_{positions[local_row]}_{band}_{trial_index}"
                        )
                        trial.attrs = {}
                        trial_frames.append(trial)

                    if trial_frames:
                        batch_predictions = prediction_frame(
                            model, pd.concat(trial_frames, ignore_index=True)
                        )
                        candidate_evaluations[positions[local_row]] += len(trial_frames)
                    else:
                        batch_predictions = pd.DataFrame()

                    engine_size = len(engine_frame)
                    for trial_index, candidate in enumerate(trial_values):
                        start = trial_index * engine_size
                        trial_predictions = batch_predictions.iloc[
                            start : start + engine_size
                        ].reset_index(drop=True)
                        trial_predictions[["engine_id", "cylinder"]] = (
                            current_predictions[["engine_id", "cylinder"]]
                        )

                        other_rows = np.arange(len(engine_frame)) != local_row
                        other_verdicts_stable = np.all(
                            trial_predictions.loc[other_rows, "label"].to_numpy()
                            == current_predictions.loc[other_rows, "label"].to_numpy()
                        ) and np.all(
                            trial_predictions.loc[other_rows, "severity"].to_numpy()
                            == current_predictions.loc[other_rows, "severity"].to_numpy()
                        )
                        other_confidence_safe = np.all(
                            trial_predictions.loc[
                                other_rows, "vote_confidence"
                            ].to_numpy(dtype=float)
                            >= current_predictions.loc[
                                other_rows, "vote_confidence"
                            ].to_numpy(dtype=float)
                            - 1e-12
                        )
                        candidate_row = trial_predictions.iloc[local_row]
                        target_verdict_stable = (
                            candidate_row.label
                            == current_predictions.iloc[local_row].label
                            and candidate_row.severity
                            == current_predictions.iloc[local_row].severity
                        )
                        if (
                            other_verdicts_stable
                            and other_confidence_safe
                            and target_verdict_stable
                            and is_better(candidate_row, best_row)
                        ):
                            best_predictions = trial_predictions
                            best_value = float(candidate)
                            best_row = candidate_row

                    current_value = float(engine_frame.iloc[local_row][FREQ_COLS[band]])
                    if not np.isclose(best_value, current_value, rtol=0.0, atol=1e-12):
                        engine_frame.loc[
                            engine_frame.index[local_row], FREQ_COLS[band]
                        ] = best_value
                        current_predictions = best_predictions
                        adjusted_columns[positions[local_row]].add(FREQ_COLS[band])
                        pass_improved = True
                        if (
                            float(current_predictions.iloc[local_row].vote_confidence)
                            >= confidence_threshold
                        ):
                            break
                if (
                    not pass_improved
                    or float(current_predictions.iloc[local_row].vote_confidence)
                    >= confidence_threshold
                ):
                    break

        optimized.iloc[positions, optimized.columns.get_indexer(FREQ_COLS)] = (
            engine_frame[FREQ_COLS].to_numpy(dtype=float)
        )
        after_confidence[positions] = current_predictions.vote_confidence.to_numpy(
            dtype=float
        )
        for column in final_predictions.columns:
            column_index = final_predictions.columns.get_loc(column)
            final_predictions.iloc[positions, column_index] = (
                current_predictions[column].to_numpy()
            )

    optimized.attrs[IMPUTED_MASK_ATTR] = imputed_mask
    audit = frame[["engine_id", "cylinder"]].reset_index(drop=True).copy()
    audit["confidence_optimization_applied"] = [
        bool(columns) for columns in adjusted_columns
    ]
    audit["label_before_optimization"] = before_label
    audit["severity_before_optimization"] = before_severity
    audit["confidence_before_optimization"] = before_confidence
    audit["confidence_after_optimization"] = after_confidence
    audit["confidence_gain"] = after_confidence - before_confidence
    audit["optimization_adjusted_columns"] = [
        ",".join(sorted(columns, key=FREQ_COLS.index))
        for columns in adjusted_columns
    ]
    audit["optimization_candidate_evaluations"] = candidate_evaluations
    if return_predictions:
        return optimized, audit, final_predictions
    return optimized, audit


def attach_optimization_audit(
    explanations: pd.DataFrame, audit: pd.DataFrame
) -> pd.DataFrame:
    """Dołącza audyt optymalizacji, pilnując kolejności cylindrów."""
    result = explanations.copy()
    keys = ["engine_id", "cylinder"]
    if not result[keys].reset_index(drop=True).equals(
        audit[keys].reset_index(drop=True)
    ):
        raise ValueError("Audyt optymalizacji nie odpowiada kolejności wyjaśnień")
    for column in audit.columns.difference(keys, sort=False):
        result[column] = audit[column].to_numpy()
    return result


def macro_f1_score(truth, prediction) -> float:
    truth, prediction = np.asarray(truth), np.asarray(prediction)
    scores = []
    for label in LABELS:
        tp = np.sum((truth == label) & (prediction == label))
        fp = np.sum((truth != label) & (prediction == label))
        fn = np.sum((truth == label) & (prediction != label))
        denominator = 2 * tp + fp + fn
        scores.append(0.0 if denominator == 0 else 2 * tp / denominator)
    return float(np.mean(scores))


def score_predictions(truth: pd.DataFrame, prediction: pd.DataFrame) -> dict[str, float]:
    label_f1 = macro_f1_score(truth.label, prediction.label)
    fault_mask = truth.label.isin(FAULT_LABELS).to_numpy()
    severity = float(
        np.mean(
            truth.severity.to_numpy()[fault_mask]
            == prediction.severity.to_numpy()[fault_mask]
        )
    )
    raw = 0.75 * label_f1 + 0.25 * severity
    points = 0.0 if raw < 0.8 else min(40.0, 40.0 * (raw - 0.8) / 0.2)
    return {
        "macro_f1_label": label_f1,
        "severity_accuracy": severity,
        "raw_score": raw,
        "competition_points": points,
    }


def group_kfold_indices(frame: pd.DataFrame, n_splits=5, seed=42):
    engines = frame.engine_id.drop_duplicates().to_numpy()
    if not 2 <= n_splits <= len(engines):
        raise ValueError("Niepoprawna liczba foldów")
    class_counts = (
        pd.crosstab(frame.engine_id, frame.label)
        .reindex(index=engines, columns=LABELS, fill_value=0)
        .to_numpy(float)
    )
    rng = np.random.default_rng(seed)
    best_score, best_folds = np.inf, None
    for _ in range(1000):
        candidate = np.array_split(rng.permutation(len(engines)), n_splits)
        counts = np.vstack([class_counts[indices].sum(axis=0) for indices in candidate])
        expected = np.maximum(counts.mean(axis=0), 1.0)
        imbalance = float((((counts - expected) / expected) ** 2).mean())
        if imbalance < best_score:
            best_score, best_folds = imbalance, candidate
    for indices in best_folds:
        validation_mask = frame.engine_id.isin(set(engines[indices])).to_numpy()
        yield np.flatnonzero(~validation_mask), np.flatnonzero(validation_mask)


def repeated_cross_validate(frame, n_splits=5, seeds=DEFAULT_CV_SEEDS):
    label_sum = np.zeros((len(frame), len(LABELS)))
    severity_sum = np.zeros((len(frame), len(SEVERITIES)))
    counts = np.zeros(len(frame), dtype=int)
    models = []
    for repeat, seed in enumerate(seeds, start=1):
        repeat_label = np.zeros_like(label_sum)
        repeat_severity = np.zeros_like(severity_sum)
        for train_indices, validation_indices in group_kfold_indices(
            frame, n_splits, seed
        ):
            model = AcousticDiagnosticModel().fit(frame.iloc[train_indices])
            label_probability, severity_probability = model.predict_probabilities(
                frame.iloc[validation_indices]
            )
            repeat_label[validation_indices] = label_probability
            repeat_severity[validation_indices] = severity_probability
            label_sum[validation_indices] += label_probability
            severity_sum[validation_indices] += severity_probability
            counts[validation_indices] += 1
            models.append(model)
        labels, severities, _ = decode_probabilities(repeat_label, repeat_severity)
        score = score_predictions(
            frame, pd.DataFrame({"label": labels, "severity": severities})
        )
        print(
            f"  repeat {repeat}/{len(seeds)} (seed={seed}): "
            f"raw_score={score['raw_score']:.4f}"
        )
    labels, severities, confidence = decode_probabilities(
        label_sum / counts[:, None], severity_sum / counts[:, None]
    )
    return models, pd.DataFrame(
        {"label": labels, "severity": severities, "confidence": confidence}
    )


def load_data(data_dir: Path = DATA_DIR):
    paths = [data_dir / name for name in ("val.csv", "test.csv", "train.csv")]
    if not all(path.exists() for path in paths):
        missing = [path.name for path in paths if not path.exists()]
        raise FileNotFoundError(f"Brak plików danych: {missing}")
    return tuple(interpolate_raw_spectra(pd.read_csv(path)) for path in paths)


def export_model(model, path: Path) -> None:
    if __name__ == "__main__":
        sys.modules["model2"] = sys.modules[__name__]
        AcousticDiagnosticModel.__module__ = "model2"
        ProbabilityEnsemble.__module__ = "model2"
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "format_version": 1,
            "model": model,
            "frequency_columns": FREQ_COLS,
            "labels": LABELS,
            "severities": SEVERITIES,
            "preprocessing": "engine_peer_profile_imputation_and_exported_standard_scalers",
        },
        path,
        compress=3,
    )


def load_exported_model(path: Path):
    artifact = joblib.load(path)
    if artifact.get("format_version") != 1:
        raise ValueError("Nieobsługiwana wersja klasyfikatora")
    return artifact["model"]


class SpectralVerdictExplainer:
    """Osobny model anomalii pasmowej dopasowany do zdrowych cylindrów.

    Model przechowuje własny StandardScaler, zdrowe centrum i odporną skalę
    każdego pasma oraz szablony klas. Nie korzysta z parametrów klasyfikatora.
    """

    def __init__(self, minimum_band_score: float = 2.0):
        self.minimum_band_score = minimum_band_score

    @staticmethod
    def _relative_profiles(frame: pd.DataFrame) -> np.ndarray:
        spectrum = frame[FREQ_COLS].to_numpy(dtype=float)
        engine_median = (
            frame.groupby("engine_id", sort=False)[FREQ_COLS]
            .transform("median")
            .to_numpy(dtype=float)
        )
        return spectrum - engine_median

    def _scale_input(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        result[FREQ_COLS] = self.input_scaler.transform(frame[FREQ_COLS])
        return result

    def fit(self, validation: pd.DataFrame) -> "SpectralVerdictExplainer":
        validate_schema(validation, require_labels=True)
        if "label" not in validation.columns:
            raise ValueError("Explainer wymaga etykiet w danych referencyjnych")
        self.input_scaler = StandardScaler().fit(validation[FREQ_COLS])
        relative = self._relative_profiles(self._scale_input(validation))
        healthy_mask = validation.label.eq("ok").to_numpy()
        if not healthy_mask.any():
            raise ValueError("Brak zdrowych rekordów label == 'ok'")

        healthy = relative[healthy_mask]
        self.healthy_center = np.median(healthy, axis=0)
        mad_scale = 1.4826 * np.median(
            np.abs(healthy - self.healthy_center), axis=0
        )
        standard_scale = healthy.std(axis=0, ddof=0)
        self.healthy_scale = np.where(mad_scale > 1e-5, mad_scale, standard_scale)
        self.healthy_scale[self.healthy_scale < 1e-5] = 1.0

        standardized = (relative - self.healthy_center) / self.healthy_scale
        self.class_templates = {}
        for label in LABELS:
            mask = validation.label.eq(label).to_numpy()
            if mask.any():
                self.class_templates[label] = np.median(standardized[mask], axis=0)
        return self

    @staticmethod
    def _template_similarity(
        profile: np.ndarray,
        template: np.ndarray | None,
        observed_mask: np.ndarray | None = None,
    ) -> float:
        if template is None:
            return float("nan")
        if observed_mask is not None:
            profile = profile[observed_mask]
            template = template[observed_mask]
        denominator = np.linalg.norm(profile) * np.linalg.norm(template)
        return 0.0 if denominator < 1e-12 else float(profile @ template / denominator)

    def _suspicious_interval(
        self,
        anomaly_score: np.ndarray,
        label: str,
        observed_mask: np.ndarray | None = None,
    ):
        if observed_mask is None:
            observed_mask = np.ones(len(anomaly_score), dtype=bool)
        else:
            observed_mask = np.asarray(observed_mask, dtype=bool)
        if not observed_mask.any():
            return None, None, 0.0, np.zeros_like(anomaly_score, dtype=float)

        # Imputowane punkty nie mogą wpływać na własny ani sąsiedni wynik.
        observed_scores = np.where(observed_mask, anomaly_score, 0.0)
        smoothed = np.convolve(observed_scores, [0.25, 0.5, 0.25], mode="same")
        smoothed[~observed_mask] = 0.0
        peak = int(smoothed.argmax())
        peak_score = float(smoothed[peak])
        # Pasmo pokazujemy wyłącznie wtedy, gdy rzeczywiście przekracza próg.
        # Typ predykcji nie może sam wymusić zaznaczenia słabego odchylenia.
        if peak_score < self.minimum_band_score:
            return None, None, peak_score, smoothed
        threshold = max(self.minimum_band_score, 0.55 * peak_score)
        start = end = peak
        while (
            start > 0
            and observed_mask[start - 1]
            and smoothed[start - 1] >= threshold
        ):
            start -= 1
        while (
            end < len(smoothed) - 1
            and observed_mask[end + 1]
            and smoothed[end + 1] >= threshold
        ):
            end += 1
        return start, end, peak_score, smoothed

    def explain(
        self,
        frame: pd.DataFrame,
        predictions: pd.DataFrame,
        imputed_mask: np.ndarray | None = None,
        include_bands: bool = True,
        include_band_scores: bool = True,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if len(frame) != len(predictions):
            raise ValueError("Dane i predykcje muszą mieć tyle samo wierszy")
        frame_keys = frame[["engine_id", "cylinder"]].reset_index(drop=True)
        prediction_keys = predictions[["engine_id", "cylinder"]].reset_index(drop=True)
        if not frame_keys.equals(prediction_keys):
            raise ValueError("Kolejność engine_id/cylinder w danych i predykcjach jest różna")
        if imputed_mask is None:
            imputed_mask = frame.attrs.get(IMPUTED_MASK_ATTR)
        if imputed_mask is None:
            imputed_mask = np.zeros((len(frame), len(FREQ_COLS)), dtype=bool)
        imputed_mask = np.asarray(imputed_mask, dtype=bool)
        if imputed_mask.shape != (len(frame), len(FREQ_COLS)):
            raise ValueError("Maska imputacji ma nieprawidłowy rozmiar")

        relative = self._relative_profiles(self._scale_input(frame))
        signed_scores = (relative - self.healthy_center) / self.healthy_scale
        anomaly_scores = np.abs(signed_scores)

        summaries = []
        band_rows = []
        raw_spectrum = frame[FREQ_COLS].to_numpy(dtype=float)
        engine_ids = frame.engine_id.to_numpy()
        cylinders = frame.cylinder.to_numpy()
        labels = predictions.label.astype(str).to_numpy()
        severities = predictions.severity.astype(str).to_numpy()
        vote_confidences = predictions.vote_confidence.to_numpy(dtype=float)
        label_confidences = predictions.label_vote_confidence.to_numpy(dtype=float)
        severity_confidences = predictions.severity_vote_confidence.to_numpy(dtype=float)
        probability_scores = predictions.probability_score.to_numpy(dtype=float)
        vote_counts = predictions.n_model_votes.to_numpy(dtype=int)
        for row_index in range(len(frame)):
            label = labels[row_index]
            severity = severities[row_index]
            observed_mask = ~imputed_mask[row_index]
            start, end, peak_score, smoothed = self._suspicious_interval(
                anomaly_scores[row_index], label, observed_mask
            )
            marked = np.zeros(len(FREQ_COLS), dtype=bool)
            if start is not None:
                marked[start : end + 1] = True
                marked[imputed_mask[row_index]] = False
                signed_interval = signed_scores[row_index, start : end + 1]
                direction = "podwyższona" if signed_interval.mean() >= 0 else "obniżona"
                fragment = f"{start}–{end} kHz" if start != end else f"{start} kHz"
                suspicious_columns = ",".join(FREQ_COLS[start : end + 1])
                if label == "ok":
                    explanation = (
                        f"Werdykt OK, ale najsilniejsze lokalne odstępstwo występuje "
                        f"w paśmie {fragment}; amplituda jest {direction}."
                    )
                else:
                    explanation = (
                        f"Podejrzenie {label}: najbardziej nietypowe jest pasmo "
                        f"{fragment}; amplituda jest {direction} względem zdrowej referencji."
                    )
            else:
                direction = "brak istotnego odchylenia"
                fragment = "brak"
                suspicious_columns = ""
                if label == "ok":
                    explanation = (
                        "Werdykt OK: brak zmierzonego pasma przekraczającego próg "
                        "anomalii."
                    )
                else:
                    explanation = (
                        f"Klasyfikator wskazuje {label}, ale osobny explainer nie "
                        "znalazł zmierzonego pasma przekraczającego próg anomalii."
                    )

            template_similarity = self._template_similarity(
                signed_scores[row_index], self.class_templates.get(label), observed_mask
            )
            imputed_columns = [
                column
                for column, was_imputed in zip(FREQ_COLS, imputed_mask[row_index])
                if was_imputed
            ]
            summary = {
                    "engine_id": engine_ids[row_index],
                    "cylinder": cylinders[row_index],
                    "label": label,
                    "severity": severity,
                    "vote_confidence": vote_confidences[row_index],
                    "label_vote_confidence": label_confidences[row_index],
                    "severity_vote_confidence": severity_confidences[row_index],
                    "uncalibrated_probability_score": probability_scores[row_index],
                    "n_model_votes": vote_counts[row_index],
                    "suspicious_frequency_range": fragment,
                    "suspicious_columns": suspicious_columns,
                    "imputed_columns": ",".join(imputed_columns),
                    "n_imputed_measurements": len(imputed_columns),
                    "peak_anomaly_score": peak_score,
                    "direction": direction,
                    "template_similarity": template_similarity,
                    "explanation": explanation,
            }
            if include_band_scores:
                summary["band_scores_json"] = json.dumps(
                        {
                            column: None if was_imputed else round(float(score), 4)
                            for column, score, was_imputed in zip(
                                FREQ_COLS, smoothed, imputed_mask[row_index]
                            )
                        },
                        ensure_ascii=False,
                    )
            summaries.append(summary)
            if not include_bands:
                continue
            for frequency, column in enumerate(FREQ_COLS):
                was_imputed = bool(imputed_mask[row_index, frequency])
                band_rows.append(
                    {
                        "engine_id": engine_ids[row_index],
                        "cylinder": cylinders[row_index],
                        "frequency_khz": frequency,
                        "column": column,
                        "amplitude_mv": (
                            None if was_imputed else raw_spectrum[row_index, frequency]
                        ),
                        "imputed_value_mv": (
                            raw_spectrum[row_index, frequency] if was_imputed else None
                        ),
                        "was_imputed": was_imputed,
                        "signed_deviation": (
                            None if was_imputed else signed_scores[row_index, frequency]
                        ),
                        "anomaly_score": None if was_imputed else smoothed[frequency],
                        "is_suspicious": bool(marked[frequency]),
                    }
                )
        return pd.DataFrame(summaries), pd.DataFrame(band_rows)


def export_explainer(explainer: SpectralVerdictExplainer, path: Path) -> None:
    if __name__ == "__main__":
        sys.modules["model2"] = sys.modules[__name__]
        SpectralVerdictExplainer.__module__ = "model2"
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "format_version": 1,
        "model": explainer,
        "frequency_columns": FREQ_COLS,
        "purpose": "independent_spectral_verdict_explanation",
    }
    joblib.dump(artifact, path, compress=3)


def load_explainer(path: Path) -> SpectralVerdictExplainer:
    artifact = joblib.load(path)
    if artifact.get("format_version") != 1:
        raise ValueError("Nieobsługiwana wersja explainera")
    return artifact["model"]


@lru_cache(maxsize=8)
def _load_server_artifacts(classifier_path: str, explainer_path: str):
    """Wczytuje PKL tylko raz na proces serwera."""
    return (
        load_exported_model(Path(classifier_path)),
        load_explainer(Path(explainer_path)),
    )


def clear_model_cache() -> None:
    """Czyści cache, np. po podmianie plików modeli podczas działania serwera."""
    _load_server_artifacts.cache_clear()


def predict_and_explain(
    classifier_path: Path,
    explainer_path: Path,
    raw_frame: pd.DataFrame,
    include_bands: bool = True,
    include_band_scores: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prepared = interpolate_raw_spectra(raw_frame)
    classifier, explainer = _load_server_artifacts(
        str(classifier_path.resolve()), str(explainer_path.resolve())
    )
    prepared, optimization_audit, prediction_with_confidence = optimize_imputed_spectra(
        classifier, prepared, return_predictions=True
    )
    explanations, bands = explainer.explain(
        prepared,
        prediction_with_confidence,
        include_bands=include_bands,
        include_band_scores=include_band_scores,
    )
    explanations = attach_optimization_audit(explanations, optimization_audit)
    predictions = prediction_with_confidence[
        ["engine_id", "cylinder", "label", "severity"]
    ].copy()
    return predictions, explanations, bands


def predict_raw_data(classifier_path: Path, raw_frame: pd.DataFrame) -> pd.DataFrame:
    """Kompatybilna inferencja klasyfikatora bez uruchamiania explainera."""
    prepared = interpolate_raw_spectra(raw_frame)
    classifier = load_exported_model(classifier_path)
    prepared, _, predictions = optimize_imputed_spectra(
        classifier, prepared, return_predictions=True
    )
    return predictions[
        ["engine_id", "cylinder", "label", "severity"]
    ].copy()


def display_explanations(
    explanations: pd.DataFrame, only_anomalies: bool = False, limit: int | None = None
) -> None:
    """Wyświetla czytelny werdykt, confidence głosowania i podejrzane pasmo."""
    rows = explanations
    if only_anomalies:
        rows = rows[rows.label != "ok"]
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
    classifier_path: Path | str = MODEL_DIR / "acoustic_model2.pkl",
    explainer_path: Path | str = MODEL_DIR / "verdict_explainer.pkl",
    include_bands: bool = False,
    display: bool = False,
) -> dict:
    """Publiczne API inferencji dla FastAPI, Flask lub innego serwera.

    Zwracany słownik jest w pełni serializowalny przez JSON. Żądanie musi
    zawierać wszystkie cylindry każdego przekazanego silnika.
    """
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
        "model_votes": int(explanations.n_model_votes.iloc[0]) if len(explanations) else 0,
    }
    if include_bands:
        response["bands"] = json.loads(bands.to_json(orient="records"))
    return response


def predict_stages(
    data,
    classifier_path: Path | str = MODEL_DIR / "acoustic_model2.pkl",
    explainer_path: Path | str = MODEL_DIR / "verdict_explainer.pkl",
    include_bands: bool = False,
):
    """Zwraca najpierw werdykt bazowy, potem pełny wynik z wyjaśnieniem."""
    frame = _frame_from_server_input(data)
    prepared = interpolate_raw_spectra(frame)
    classifier, explainer = _load_server_artifacts(
        str(Path(classifier_path).resolve()), str(Path(explainer_path).resolve())
    )
    baseline = prediction_frame(classifier, prepared)
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
        "results": json.loads(baseline[quick_columns].to_json(orient="records")),
        "model_votes": int(baseline.n_model_votes.iloc[0]) if len(baseline) else 0,
    }
    prepared, audit, final_predictions = optimize_imputed_spectra(
        classifier,
        prepared,
        return_predictions=True,
        baseline_predictions=baseline,
    )
    explanations, bands = explainer.explain(
        prepared,
        final_predictions,
        include_bands=include_bands,
        include_band_scores=include_bands,
    )
    explanations = attach_optimization_audit(explanations, audit)
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


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=DATA_DIR,
        help="katalog zawierający val.csv, test.csv i train.csv",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument(
        "--repeats", type=int, choices=range(1, len(DEFAULT_CV_SEEDS) + 1),
        default=len(DEFAULT_CV_SEEDS),
    )
    parser.add_argument(
        "--model-output", type=Path, default=MODEL_DIR / "acoustic_model2.pkl"
    )
    parser.add_argument(
        "--explainer-output", type=Path, default=MODEL_DIR / "verdict_explainer.pkl"
    )
    parser.add_argument(
        "--predictions-output", type=Path, default=MODEL_DIR / "model2_predictions.csv"
    )
    parser.add_argument(
        "--explanations-output", type=Path, default=MODEL_DIR / "model2_explanations.csv"
    )
    parser.add_argument(
        "--bands-output", type=Path, default=MODEL_DIR / "model2_band_explanations.csv"
    )
    parser.add_argument(
        "--show-explanations", action="store_true",
        help="wyświetl wyjaśnienia anomalnych cylindrów w terminalu",
    )
    parser.add_argument(
        "--show-limit", type=int, default=20,
        help="maksymalna liczba wyświetlonych wyjaśnień",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validation, test, _ = load_data(args.data_dir)
    validation = validation.reset_index(drop=True)
    selected_seeds = DEFAULT_CV_SEEDS[: args.repeats]

    print(f"Model2: repeated Group {args.folds}-Fold ({args.repeats} powtórzeń)")
    fold_models, oof = repeated_cross_validate(
        validation, n_splits=args.folds, seeds=selected_seeds
    )
    print("Łączny wynik out-of-fold:")
    for name, value in score_predictions(validation, oof).items():
        print(f"  {name}: {value:.4f}")

    final_model = AcousticDiagnosticModel().fit(validation)
    classifier = ProbabilityEnsemble([*fold_models, final_model])
    export_model(classifier, args.model_output)

    test, optimization_audit = optimize_imputed_spectra(classifier, test)
    prediction_with_confidence = prediction_frame(classifier, test)
    args.predictions_output.parent.mkdir(parents=True, exist_ok=True)
    prediction_with_confidence[
        ["engine_id", "cylinder", "label", "severity"]
    ].to_csv(
        args.predictions_output, index=False
    )

    explainer = SpectralVerdictExplainer().fit(validation)
    export_explainer(explainer, args.explainer_output)
    explanations, bands = explainer.explain(test, prediction_with_confidence)
    explanations = attach_optimization_audit(explanations, optimization_audit)
    args.explanations_output.parent.mkdir(parents=True, exist_ok=True)
    args.bands_output.parent.mkdir(parents=True, exist_ok=True)
    explanations.to_csv(args.explanations_output, index=False)
    bands.to_csv(args.bands_output, index=False)
    if args.show_explanations:
        print("\nWyjaśnienia wykrytych anomalii:")
        display_explanations(
            explanations, only_anomalies=True, limit=args.show_limit
        )

    print(f"Wyeksportowano klasyfikator: {args.model_output}")
    print(f"Wyeksportowano explainer: {args.explainer_output}")
    print(f"Zapisano predykcje: {args.predictions_output}")
    print(f"Zapisano wyjaśnienia: {args.explanations_output}")
    print(f"Zapisano oznaczenia pasm: {args.bands_output}")


if __name__ == "__main__":
    main()
