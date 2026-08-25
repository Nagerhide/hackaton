"""Model diagnostyczny: Group K-Fold ensemble z klasyfikatorem shrinkage LDA.

Uruchomienie:
    .venv/bin/python model.py

Model używa różnicy widma cylindra względem mediany cylindrów jego silnika.
Osobne klasyfikatory przewidują rodzaj usterki oraz jej nasilenie.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC


BASE_DIR = Path(__file__).resolve().parent
FREQ_COLS = [f"mV_{index}" for index in range(21)]
LABELS = ["ok", "zakoksowany", "lejacy", "pompa", "iglica", "unknown"]
FAULT_LABELS = ["zakoksowany", "lejacy", "pompa", "iglica"]
SEVERITIES = ["male", "srednie", "duze"]


def build_features(frame: pd.DataFrame) -> np.ndarray:
    """Buduje cechy odporne na różnice poziomu sygnału między silnikami.

    Cechy obejmują:
    - odchylenie każdego pasma od mediany cylindrów danego silnika,
    - różnice pomiędzy sąsiednimi pasmami odchylenia,
    - sześć statystyk opisujących wielkość anomalii,
    - one-hot liczby cylindrów (8, 12 lub 16).
    """
    missing = [column for column in FREQ_COLS if column not in frame.columns]
    if missing:
        raise ValueError(f"Brak kolumn częstotliwości: {missing}")
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
        [(frame["n_cylinders"].to_numpy() == value) for value in (8, 12, 16)]
    ).astype(float)
    return np.column_stack([relative, slopes, summaries, cylinder_count])


def interpolate_raw_spectra(frame: pd.DataFrame) -> pd.DataFrame:
    """Interpoluje surowe widma; skalowanie wykonuje później model."""
    prepared = frame.copy()
    missing = [column for column in FREQ_COLS if column not in prepared.columns]
    if missing:
        raise ValueError(f"Brak kolumn częstotliwości: {missing}")
    prepared[FREQ_COLS] = prepared[FREQ_COLS].interpolate(
        method="linear", axis=1, limit_direction="both"
    )
    if prepared[FREQ_COLS].isna().any().any():
        raise ValueError("Nie można uzupełnić widma bez żadnego poprawnego pomiaru")
    return prepared


def _make_classifier(shrinkage="auto") -> object:
    """Odporne skalowanie i LDA z automatycznym shrinkage kowariancji."""
    return make_pipeline(
        StandardScaler(),
        LinearDiscriminantAnalysis(solver="lsqr", shrinkage=shrinkage),
    )


def synthetic_feature_rows(
    frame: pd.DataFrame,
    features: np.ndarray,
    max_pairs_per_group: int = 200,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    """Interpoluje pary tej samej klasy i liczby cylindrów w przestrzeni cech."""
    rng = np.random.default_rng(seed)
    label_x, label_y, severity_x, severity_y, audit = [], [], [], [], []
    indexed = frame.reset_index(drop=True)
    for (n_cylinders, label), group in indexed.groupby(
        ["n_cylinders", "label"], sort=True
    ):
        indices = group.index.to_numpy()
        pairs = np.asarray(
            [(indices[a], indices[b]) for a in range(len(indices)) for b in range(a + 1, len(indices))],
            dtype=int,
        ).reshape(-1, 2)
        if len(pairs) > max_pairs_per_group:
            pairs = pairs[rng.choice(len(pairs), max_pairs_per_group, replace=False)]
        for first, second in pairs:
            mixed = (features[first] + features[second]) / 2.0
            label_x.append(mixed)
            label_y.append(label)
            first_severity = indexed.iloc[first].severity
            second_severity = indexed.iloc[second].severity
            severity = first_severity if first_severity == second_severity else None
            if severity in SEVERITIES:
                severity_x.append(mixed)
                severity_y.append(severity)
            audit.append(
                {
                    "source_engine_1": indexed.iloc[first].engine_id,
                    "source_cylinder_1": indexed.iloc[first].cylinder,
                    "source_engine_2": indexed.iloc[second].engine_id,
                    "source_cylinder_2": indexed.iloc[second].cylinder,
                    "n_cylinders": n_cylinders,
                    "label": label,
                    "severity": severity or "mixed_not_used",
                }
            )
    width = features.shape[1]
    return (
        np.asarray(label_x, dtype=float).reshape(-1, width),
        np.asarray(label_y),
        np.asarray(severity_x, dtype=float).reshape(-1, width),
        np.asarray(severity_y),
        pd.DataFrame(audit),
    )


class AcousticLDAModel:
    """Hierarchiczny model anomalii, typu usterki i jej nasilenia."""

    def __init__(self, synthetic_augmentation=False, max_pairs_per_group=200, seed=42):
        self.synthetic_augmentation = synthetic_augmentation
        self.max_pairs_per_group = max_pairs_per_group
        self.seed = seed

    def _transform_input(self, frame: pd.DataFrame) -> pd.DataFrame:
        transformed = frame.copy()
        transformed[FREQ_COLS] = self.input_scaler.transform(frame[FREQ_COLS])
        return transformed

    def fit(self, frame: pd.DataFrame) -> "AcousticLDAModel":
        # Odpowiednik scaler.fit_transform(x_train). Obiekt scaler zostaje w
        # modelu i dla walidacji/testu wykonywane jest wyłącznie transform.
        self.input_scaler = StandardScaler().fit(frame[FREQ_COLS])
        transformed_frame = self._transform_input(frame)
        features = build_features(transformed_frame)
        label_features = features
        label_targets = frame.label.to_numpy()
        fault_mask = frame.label.isin(FAULT_LABELS).to_numpy()
        severity_features = features[fault_mask]
        severity_targets = frame.severity.to_numpy()[fault_mask]
        severity_labels = frame.label.to_numpy()[fault_mask]
        self.synthetic_data = pd.DataFrame()

        if self.synthetic_augmentation:
            synthetic = synthetic_feature_rows(
                frame, features, self.max_pairs_per_group, self.seed
            )
            synthetic_label_x, synthetic_label_y, synthetic_severity_x, synthetic_severity_y, audit = synthetic
            label_features = np.vstack([label_features, synthetic_label_x])
            label_targets = np.concatenate([label_targets, synthetic_label_y])
            severity_features = np.vstack([severity_features, synthetic_severity_x])
            severity_targets = np.concatenate([severity_targets, synthetic_severity_y])
            # Syntetyczne severity powstało z par tej samej klasy label.
            synthetic_severity_labels = synthetic_label_y[:0]
            if len(synthetic_severity_y):
                # Odtworzenie klas bezpiecznych par z audytu w tej samej kolejności.
                synthetic_severity_labels = audit.loc[
                    audit.severity.isin(SEVERITIES), "label"
                ].to_numpy()
            severity_labels = np.concatenate([severity_labels, synthetic_severity_labels])
            self.synthetic_data = audit

        anomaly_targets = np.where(label_targets == "ok", "ok", "anomaly")
        anomaly_mask = label_targets != "ok"
        self.anomaly_model = make_pipeline(
            StandardScaler(), SVC(C=1.0, class_weight="balanced")
        ).fit(label_features, anomaly_targets)
        self.type_model = _make_classifier().fit(
            label_features[anomaly_mask], label_targets[anomaly_mask]
        )
        # Mały, stały shrinkage znacznie lepiej rozdziela duze od srednie niż
        # automatyczny shrinkage dobrany do bardzo małego zbioru usterek.
        severity_label_features = np.column_stack(
            [severity_labels == label for label in FAULT_LABELS]
        ).astype(float)
        self.severity_model = _make_classifier(shrinkage=0.01).fit(
            np.column_stack([severity_features, severity_label_features]), severity_targets
        )
        return self

    @staticmethod
    def _aligned_probabilities(model, features, classes):
        probabilities = model.predict_proba(features)
        model_classes = model.classes_
        return np.column_stack(
            [probabilities[:, np.where(model_classes == value)[0][0]] for value in classes]
        )

    def predict_probabilities(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        features = build_features(self._transform_input(frame))
        decision = self.anomaly_model.decision_function(features)
        positive_probability = 1.0 / (1.0 + np.exp(-np.clip(decision, -40, 40)))
        positive_class = self.anomaly_model.classes_[1]
        anomaly_probability = (
            positive_probability if positive_class == "anomaly" else 1.0 - positive_probability
        )
        type_probabilities = self._aligned_probabilities(
            self.type_model, features, [*FAULT_LABELS, "unknown"]
        )
        label_probabilities = np.zeros((len(frame), len(LABELS)), dtype=float)
        label_probabilities[:, LABELS.index("ok")] = 1.0 - anomaly_probability
        for index, label in enumerate([*FAULT_LABELS, "unknown"]):
            label_probabilities[:, LABELS.index(label)] = (
                anomaly_probability * type_probabilities[:, index]
            )
        predicted_types = np.asarray([*FAULT_LABELS, "unknown"])[
            type_probabilities.argmax(axis=1)
        ]
        severity_label_features = np.column_stack(
            [predicted_types == label for label in FAULT_LABELS]
        ).astype(float)
        return (
            label_probabilities,
            self._aligned_probabilities(
                self.severity_model,
                np.column_stack([features, severity_label_features]),
                SEVERITIES,
            ),
        )

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        label_probabilities, severity_probabilities = self.predict_probabilities(frame)
        anomaly_classes = [*FAULT_LABELS, "unknown"]
        anomaly_indices = [LABELS.index(label) for label in anomaly_classes]
        anomaly_probability = label_probabilities[:, anomaly_indices].sum(axis=1)
        anomaly_types = np.asarray(anomaly_classes)[
            label_probabilities[:, anomaly_indices].argmax(axis=1)
        ]
        labels = np.where(anomaly_probability >= 0.5, anomaly_types, "ok")
        severities = np.asarray(SEVERITIES)[severity_probabilities.argmax(axis=1)]
        confidence = label_probabilities.max(axis=1)
        fault_mask = np.isin(labels, FAULT_LABELS)
        confidence[fault_mask] = np.minimum(
            confidence[fault_mask], severity_probabilities.max(axis=1)[fault_mask]
        )
        severities = np.where(fault_mask, severities, "nie_dotyczy")
        return labels, severities, confidence


class ProbabilityEnsemble:
    """Uśrednia prawdopodobieństwa modeli z foldów i modelu finalnego."""

    def __init__(self, models):
        self.models = list(models)
        if not self.models:
            raise ValueError("Ensemble wymaga co najmniej jednego modelu")

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        probabilities = [model.predict_probabilities(frame) for model in self.models]
        label_probabilities = np.mean([item[0] for item in probabilities], axis=0)
        severity_probabilities = np.mean([item[1] for item in probabilities], axis=0)
        anomaly_classes = [*FAULT_LABELS, "unknown"]
        anomaly_indices = [LABELS.index(label) for label in anomaly_classes]
        anomaly_probability = label_probabilities[:, anomaly_indices].sum(axis=1)
        anomaly_types = np.asarray(anomaly_classes)[
            label_probabilities[:, anomaly_indices].argmax(axis=1)
        ]
        labels = np.where(anomaly_probability >= 0.5, anomaly_types, "ok")
        severities = np.asarray(SEVERITIES)[severity_probabilities.argmax(axis=1)]
        confidence = label_probabilities.max(axis=1)
        fault_mask = np.isin(labels, FAULT_LABELS)
        confidence[fault_mask] = np.minimum(
            confidence[fault_mask], severity_probabilities.max(axis=1)[fault_mask]
        )
        severities = np.where(fault_mask, severities, "nie_dotyczy")
        return labels, severities, confidence


def prediction_frame(model, frame: pd.DataFrame) -> pd.DataFrame:
    labels, severities, confidence = model.predict(frame)
    result = frame[["engine_id", "cylinder"]].reset_index(drop=True).copy()
    result["label"] = labels
    result["severity"] = severities
    result["confidence"] = confidence
    return result


def macro_f1_score(y_true, y_pred, classes=LABELS) -> float:
    truth, prediction = np.asarray(y_true), np.asarray(y_pred)
    scores = []
    for label in classes:
        tp = np.sum((truth == label) & (prediction == label))
        fp = np.sum((truth != label) & (prediction == label))
        fn = np.sum((truth == label) & (prediction != label))
        denominator = 2 * tp + fp + fn
        scores.append(0.0 if denominator == 0 else 2 * tp / denominator)
    return float(np.mean(scores))


def severity_accuracy(y_label, y_true, y_pred) -> float:
    labels = np.asarray(y_label)
    truth, prediction = np.asarray(y_true), np.asarray(y_pred)
    mask = np.isin(labels, FAULT_LABELS)
    return float(np.mean(truth[mask] == prediction[mask])) if mask.any() else 0.0


def competition_points(score: float) -> float:
    if score < 0.80:
        return 0.0
    return 40.0 if score >= 1.0 else 40.0 * (score - 0.80) / 0.20


def score_predictions(truth: pd.DataFrame, prediction: pd.DataFrame) -> dict[str, float]:
    f1 = macro_f1_score(truth.label, prediction.label)
    severity = severity_accuracy(truth.label, truth.severity, prediction.severity)
    score = 0.75 * f1 + 0.25 * severity
    return {
        "macro_f1_label": f1,
        "severity_accuracy": severity,
        "raw_score": score,
        "competition_points": competition_points(score),
    }


def group_kfold_indices(frame: pd.DataFrame, n_splits=5, seed=42):
    """Stratyfikowany podział całych silników na rozłączne foldy."""
    engines = frame.engine_id.drop_duplicates().to_numpy()
    if not 2 <= n_splits <= len(engines):
        raise ValueError("Niepoprawna liczba foldów")
    counts = (
        pd.crosstab(frame.engine_id, frame.label)
        .reindex(index=engines, columns=LABELS, fill_value=0)
        .to_numpy(float)
    )
    rng = np.random.default_rng(seed)
    best_score, best_folds = np.inf, None
    for _ in range(1000):
        candidate = np.array_split(rng.permutation(len(engines)), n_splits)
        fold_counts = np.vstack([counts[indices].sum(axis=0) for indices in candidate])
        expected = np.maximum(fold_counts.mean(axis=0), 1.0)
        imbalance = float((((fold_counts - expected) / expected) ** 2).mean())
        if imbalance < best_score:
            best_score, best_folds = imbalance, candidate
    for indices in best_folds:
        validation_mask = frame.engine_id.isin(set(engines[indices])).to_numpy()
        yield np.flatnonzero(~validation_mask), np.flatnonzero(validation_mask)


def cross_validate(frame, n_splits=5, synthetic_augmentation=False, max_pairs=200):
    oof = pd.DataFrame(index=np.arange(len(frame)), columns=["label", "severity", "confidence"])
    models = []
    for fold, (train_indices, validation_indices) in enumerate(
        group_kfold_indices(frame, n_splits), start=1
    ):
        model = AcousticLDAModel(synthetic_augmentation, max_pairs).fit(frame.iloc[train_indices])
        prediction = prediction_frame(model, frame.iloc[validation_indices])
        oof.iloc[validation_indices] = prediction[["label", "severity", "confidence"]].to_numpy()
        models.append(model)
        score = score_predictions(frame.iloc[validation_indices].reset_index(drop=True), prediction)
        print(f"  fold {fold}: raw_score={score['raw_score']:.4f}")
    return models, oof


def add_pseudo_labels(model, labelled, unlabelled, threshold=0.95):
    prediction = prediction_frame(model, unlabelled)
    selected = prediction.confidence.to_numpy(dtype=float) >= threshold
    pseudo = unlabelled.loc[selected].copy()
    pseudo["label"] = prediction.loc[selected, "label"].to_numpy()
    pseudo["severity"] = prediction.loc[selected, "severity"].to_numpy()
    pseudo["pseudo_confidence"] = prediction.loc[selected, "confidence"].to_numpy()
    augmented = pd.concat([labelled, pseudo.drop(columns="pseudo_confidence")], ignore_index=True)
    return augmented, pseudo


def load_data():
    paths = [BASE_DIR / name for name in ("val.csv", "test.csv", "train.csv")]
    if not all(path.exists() for path in paths):
        raise FileNotFoundError("Brak plików val.csv, test.csv lub train.csv")
    return tuple(interpolate_raw_spectra(pd.read_csv(path)) for path in paths)


def export_model(model, path: Path) -> None:
    """Zapisuje model wraz z informacjami potrzebnymi podczas inferencji."""
    # Przy uruchomieniu `python model.py` klasy miałyby nazwę modułu __main__,
    # której późniejszy proces nie potrafi zaimportować. Utrwalamy nazwę model.
    if __name__ == "__main__":
        sys.modules["model"] = sys.modules[__name__]
        AcousticLDAModel.__module__ = "model"
        ProbabilityEnsemble.__module__ = "model"
    artifact = {
        "format_version": 2,
        "model": model,
        "frequency_columns": FREQ_COLS,
        "labels": LABELS,
        "fault_labels": FAULT_LABELS,
        "severities": SEVERITIES,
        "preprocessing": "row_interpolation_then_exported_standard_scalers",
    }
    joblib.dump(artifact, path, compress=3)


def load_exported_model(path: Path):
    """Wczytuje zaufany artefakt utworzony przez export_model."""
    artifact = joblib.load(path)
    if artifact.get("format_version") != 2:
        raise ValueError("Nieobsługiwana wersja artefaktu modelu")
    if artifact.get("frequency_columns") != FREQ_COLS:
        raise ValueError("Model używa innego zestawu kolumn częstotliwości")
    return artifact["model"]


def predict_raw_data(path: Path, raw_frame: pd.DataFrame) -> pd.DataFrame:
    """Wczytuje artefakt i przewiduje bez ręcznego uruchamiania preprocessora."""
    artifact = joblib.load(path)
    if artifact.get("format_version") != 2:
        raise ValueError("Nieobsługiwana wersja artefaktu modelu")
    prepared = interpolate_raw_spectra(raw_frame)
    return prediction_frame(artifact["model"], prepared).drop(columns="confidence")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--synthetic-augmentation", action="store_true")
    parser.add_argument("--max-pairs-per-group", type=int, default=200)
    parser.add_argument("--self-training", action="store_true")
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    parser.add_argument(
        "--model-output", type=Path, default=BASE_DIR / "acoustic_model.pkl",
        help="ścieżka eksportu wytrenowanego ensemble",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    validation, test, unlabelled = load_data()
    validation = validation.reset_index(drop=True)

    print(f"Walidacja Group {args.folds}-Fold:")
    fold_models, oof = cross_validate(
        validation, args.folds, args.synthetic_augmentation, args.max_pairs_per_group
    )
    print("Łączny wynik out-of-fold:")
    for name, value in score_predictions(validation, oof).items():
        print(f"  {name}: {value:.4f}")

    final_training = validation
    if args.self_training:
        pseudo_source_model = AcousticLDAModel().fit(validation)
        final_training, pseudo = add_pseudo_labels(
            pseudo_source_model, validation, unlabelled, args.confidence_threshold
        )
        pseudo.to_csv(BASE_DIR / "pseudo_training_labels.csv", index=False)
        print(f"Dodano {len(pseudo)} pseudoetykiet")

    final_model = AcousticLDAModel(
        args.synthetic_augmentation, args.max_pairs_per_group
    ).fit(final_training)
    if args.synthetic_augmentation:
        final_model.synthetic_data.to_csv(BASE_DIR / "synthetic_training_data.csv", index=False)
        print(f"Dodano {len(final_model.synthetic_data)} syntetycznych cech")

    ensemble = ProbabilityEnsemble([*fold_models, final_model])
    export_model(ensemble, args.model_output)
    print(f"Wyeksportowano model do {args.model_output}")
    output = prediction_frame(ensemble, test).drop(columns="confidence")
    output.to_csv(BASE_DIR / "model_predictions.csv", index=False)
    print(f"Zapisano {len(output)} wierszy do model_predictions.csv")


if __name__ == "__main__":
    main()
