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
    """Interpoluje braki wzdłuż pasm bez dopasowywania normalizacji."""
    validate_schema(frame)
    result = frame.copy()
    missing = [column for column in FREQ_COLS if column not in result.columns]
    if missing:
        raise ValueError(f"Brak kolumn częstotliwości: {missing}")
    result[FREQ_COLS] = result[FREQ_COLS].interpolate(
        method="linear", axis=1, limit_direction="both"
    )
    if result[FREQ_COLS].isna().any().any():
        raise ValueError("Widmo bez żadnego pomiaru nie może zostać interpolowane")
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

    def predict(self, frame: pd.DataFrame):
        probabilities = [model.predict_probabilities(frame) for model in self.models]
        label_probability = np.mean([item[0] for item in probabilities], axis=0)
        severity_probability = np.mean([item[1] for item in probabilities], axis=0)
        return decode_probabilities(label_probability, severity_probability)


def prediction_frame(model, frame: pd.DataFrame) -> pd.DataFrame:
    labels, severities, confidence = model.predict(frame)
    result = frame[["engine_id", "cylinder"]].reset_index(drop=True).copy()
    result["label"] = labels
    result["severity"] = severities
    result["confidence"] = confidence
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
            "preprocessing": "interpolation_and_exported_standard_scalers",
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
    def _template_similarity(profile: np.ndarray, template: np.ndarray | None) -> float:
        if template is None:
            return float("nan")
        denominator = np.linalg.norm(profile) * np.linalg.norm(template)
        return 0.0 if denominator < 1e-12 else float(profile @ template / denominator)

    def _suspicious_interval(self, anomaly_score: np.ndarray, label: str):
        smoothed = np.convolve(anomaly_score, [0.25, 0.5, 0.25], mode="same")
        peak = int(smoothed.argmax())
        peak_score = float(smoothed[peak])
        # Dla predykcji OK pokazujemy fragment tylko przy wyraźnym odstępstwie.
        if label == "ok" and peak_score < self.minimum_band_score:
            return None, None, peak_score, smoothed
        threshold = max(self.minimum_band_score, 0.55 * peak_score)
        start = end = peak
        while start > 0 and smoothed[start - 1] >= threshold:
            start -= 1
        while end < len(smoothed) - 1 and smoothed[end + 1] >= threshold:
            end += 1
        return start, end, peak_score, smoothed

    def explain(
        self, frame: pd.DataFrame, predictions: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if len(frame) != len(predictions):
            raise ValueError("Dane i predykcje muszą mieć tyle samo wierszy")
        frame_keys = frame[["engine_id", "cylinder"]].reset_index(drop=True)
        prediction_keys = predictions[["engine_id", "cylinder"]].reset_index(drop=True)
        if not frame_keys.equals(prediction_keys):
            raise ValueError("Kolejność engine_id/cylinder w danych i predykcjach jest różna")
        relative = self._relative_profiles(self._scale_input(frame))
        signed_scores = (relative - self.healthy_center) / self.healthy_scale
        anomaly_scores = np.abs(signed_scores)

        summaries = []
        band_rows = []
        raw_spectrum = frame[FREQ_COLS].to_numpy(dtype=float)
        for row_index in range(len(frame)):
            label = str(predictions.iloc[row_index].label)
            severity = str(predictions.iloc[row_index].severity)
            start, end, peak_score, smoothed = self._suspicious_interval(
                anomaly_scores[row_index], label
            )
            marked = np.zeros(len(FREQ_COLS), dtype=bool)
            if start is not None:
                marked[start : end + 1] = True
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
                explanation = "Werdykt OK: brak pasma przekraczającego próg anomalii."

            template_similarity = self._template_similarity(
                signed_scores[row_index], self.class_templates.get(label)
            )
            summaries.append(
                {
                    "engine_id": frame.iloc[row_index].engine_id,
                    "cylinder": frame.iloc[row_index].cylinder,
                    "label": label,
                    "severity": severity,
                    "uncalibrated_model_score": float(predictions.iloc[row_index].confidence),
                    "suspicious_frequency_range": fragment,
                    "suspicious_columns": suspicious_columns,
                    "peak_anomaly_score": peak_score,
                    "direction": direction,
                    "template_similarity": template_similarity,
                    "explanation": explanation,
                    "band_scores_json": json.dumps(
                        {column: round(float(score), 4) for column, score in zip(FREQ_COLS, smoothed)},
                        ensure_ascii=False,
                    ),
                }
            )
            for frequency, column in enumerate(FREQ_COLS):
                band_rows.append(
                    {
                        "engine_id": frame.iloc[row_index].engine_id,
                        "cylinder": frame.iloc[row_index].cylinder,
                        "frequency_khz": frequency,
                        "column": column,
                        "amplitude_mv": raw_spectrum[row_index, frequency],
                        "signed_deviation": signed_scores[row_index, frequency],
                        "anomaly_score": smoothed[frequency],
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


def predict_and_explain(
    classifier_path: Path, explainer_path: Path, raw_frame: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prepared = interpolate_raw_spectra(raw_frame)
    classifier = load_exported_model(classifier_path)
    explainer = load_explainer(explainer_path)
    prediction_with_confidence = prediction_frame(classifier, prepared)
    explanations, bands = explainer.explain(prepared, prediction_with_confidence)
    predictions = prediction_with_confidence.drop(columns="confidence")
    return predictions, explanations, bands


def predict_raw_data(classifier_path: Path, raw_frame: pd.DataFrame) -> pd.DataFrame:
    """Kompatybilna inferencja klasyfikatora bez uruchamiania explainera."""
    prepared = interpolate_raw_spectra(raw_frame)
    return prediction_frame(
        load_exported_model(classifier_path), prepared
    ).drop(columns="confidence")


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

    prediction_with_confidence = prediction_frame(classifier, test)
    args.predictions_output.parent.mkdir(parents=True, exist_ok=True)
    prediction_with_confidence.drop(columns="confidence").to_csv(
        args.predictions_output, index=False
    )

    explainer = SpectralVerdictExplainer().fit(validation)
    export_explainer(explainer, args.explainer_output)
    explanations, bands = explainer.explain(test, prediction_with_confidence)
    args.explanations_output.parent.mkdir(parents=True, exist_ok=True)
    args.bands_output.parent.mkdir(parents=True, exist_ok=True)
    explanations.to_csv(args.explanations_output, index=False)
    bands.to_csv(args.bands_output, index=False)

    print(f"Wyeksportowano klasyfikator: {args.model_output}")
    print(f"Wyeksportowano explainer: {args.explainer_output}")
    print(f"Zapisano predykcje: {args.predictions_output}")
    print(f"Zapisano wyjaśnienia: {args.explanations_output}")
    print(f"Zapisano oznaczenia pasm: {args.bands_output}")


if __name__ == "__main__":
    main()
