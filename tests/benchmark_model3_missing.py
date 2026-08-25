"""Pesymistyczny benchmark model3 przy dokładnie 10% brakujących pomiarów.

Każda maska ma najwyżej trzy braki w wierszu i najwyżej dwa kolejne pasma.
Klasyfikatory, rekonstruktor oraz skalery dla danego outer-foldu nie widzą
żadnego cylindra ocenianych silników.

Uruchomienie:
    .venv/bin/python -B tests/benchmark_model3_missing.py --masks 30
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import nan_euclidean_distances


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_DIR / "model"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

from model2 import (  # noqa: E402
    DEFAULT_CV_SEEDS,
    FREQ_COLS,
    LABELS,
    SEVERITIES,
    AcousticDiagnosticModel,
    group_kfold_indices,
    score_predictions,
)
from model3 import (  # noqa: E402
    BandRidgeImputer,
    MODEL3_ANOMALY_THRESHOLD,
    MODEL3_KNN_NEIGHBORS,
    MissingAwareTypeModel,
    _engine_peer_profiles,
    _numeric_spectra,
    combine_multiview_probabilities,
    decode_probabilities3,
)


def longest_run(row: np.ndarray) -> int:
    best = current = 0
    for value in row:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def deterministic_constrained_mask(
    frame: pd.DataFrame,
    seed: int,
    fraction: float = 0.10,
    max_per_row: int = 3,
    max_consecutive: int = 2,
) -> np.ndarray:
    """Stabilna maska niezależna od kolejności działania generatora RNG."""
    candidates = []
    for row_index, row in enumerate(
        frame[["engine_id", "cylinder"]].itertuples(index=False)
    ):
        for band_index, column in enumerate(FREQ_COLS):
            token = f"{seed}|{row.engine_id}|{row.cylinder}|{column}".encode()
            rank = hashlib.blake2b(token, digest_size=8).digest()
            candidates.append((rank, row_index, band_index))

    target = round(len(frame) * len(FREQ_COLS) * fraction)
    mask = np.zeros((len(frame), len(FREQ_COLS)), dtype=bool)
    counts = np.zeros(len(frame), dtype=int)
    selected = 0
    for _, row_index, band_index in sorted(candidates):
        if selected == target:
            break
        if counts[row_index] >= max_per_row:
            continue
        trial = mask[row_index].copy()
        trial[band_index] = True
        if longest_run(trial) > max_consecutive:
            continue
        mask[row_index, band_index] = True
        counts[row_index] += 1
        selected += 1

    if selected != target:
        raise RuntimeError(f"Wybrano {selected} braków zamiast {target}")
    assert mask.sum(axis=1).max() <= max_per_row
    assert max(longest_run(row) for row in mask) <= max_consecutive
    return mask


def residual_matrix(frame: pd.DataFrame):
    spectra = _numeric_spectra(frame)
    peer = _engine_peer_profiles(frame, spectra)
    if not np.isfinite(peer).all():
        raise ValueError("Co najmniej jeden silnik nie ma całego pasma")
    return spectra - peer, peer, spectra


def fill_from_pools(
    query_residuals: np.ndarray,
    query_peer: np.ndarray,
    query_spectra: np.ndarray,
    query_missing: np.ndarray,
    distance_parts: list[np.ndarray],
    target_parts: list[np.ndarray],
    n_neighbors: int = MODEL3_KNN_NEIGHBORS,
) -> np.ndarray:
    distances = np.concatenate(distance_parts, axis=1)
    targets = np.vstack(target_parts)
    completed = query_spectra.copy()
    for row_index, band_index in np.argwhere(query_missing):
        valid = np.isfinite(targets[:, band_index]) & np.isfinite(
            distances[row_index]
        )
        candidates = np.flatnonzero(valid)
        if not candidates.size:
            residual = 0.0
        else:
            candidate_distances = distances[row_index, candidates]
            count = min(n_neighbors, len(candidates))
            nearest = candidates[
                np.argpartition(candidate_distances, count - 1)[:count]
            ]
            nearest_distances = distances[row_index, nearest]
            exact = nearest_distances <= 1e-12
            if exact.any():
                residual = float(np.mean(targets[nearest[exact], band_index]))
            else:
                residual = float(
                    np.average(
                        targets[nearest, band_index],
                        weights=1.0 / nearest_distances,
                    )
                )
        completed[row_index, band_index] = query_peer[row_index, band_index] + residual
    np.testing.assert_array_equal(
        completed[~query_missing], query_spectra[~query_missing]
    )
    return completed


def benchmark(
    mask_count: int = 30,
    first_seed: int = 6000,
    cv_repeats: int = 10,
):
    validation = pd.read_csv(PROJECT_DIR / "tests" / "val.csv").reset_index(
        drop=True
    )
    unlabeled = pd.read_csv(PROJECT_DIR / "tests" / "train.csv").reset_index(
        drop=True
    )
    validation_residuals, _, validation_spectra = residual_matrix(validation)
    unlabeled_residuals, _, _ = residual_matrix(unlabeled)
    selected_cv_seeds = DEFAULT_CV_SEEDS[:cv_repeats]

    fold_models = []
    clean_ridge_label_sum = np.zeros((len(validation), len(LABELS)))
    clean_ridge_severity_sum = np.zeros((len(validation), len(SEVERITIES)))
    clean_knn_label_sum = np.zeros((len(validation), len(LABELS)))
    clean_knn_severity_sum = np.zeros((len(validation), len(SEVERITIES)))
    clean_type_sum = np.zeros((len(validation), 5))
    clean_counts = np.zeros(len(validation), dtype=int)
    for cv_seed in selected_cv_seeds:
        for train_indices, validation_indices in group_kfold_indices(
            validation, 5, cv_seed
        ):
            train_engines = set(validation.iloc[train_indices].engine_id)
            valid_engines = set(validation.iloc[validation_indices].engine_id)
            assert train_engines.isdisjoint(valid_engines)
            train = validation.iloc[train_indices].reset_index(drop=True)
            query = validation.iloc[validation_indices].reset_index(drop=True)
            model = AcousticDiagnosticModel().fit(train)
            ridge_imputer = BandRidgeImputer().fit(train)
            type_model = MissingAwareTypeModel().fit(train)
            ridge_label, ridge_severity = model.predict_probabilities(
                ridge_imputer.transform(query)
            )
            knn_label, knn_severity = model.predict_probabilities(query)
            type_probability = type_model.predict_probabilities(query)
            clean_ridge_label_sum[validation_indices] += ridge_label
            clean_ridge_severity_sum[validation_indices] += ridge_severity
            clean_knn_label_sum[validation_indices] += knn_label
            clean_knn_severity_sum[validation_indices] += knn_severity
            clean_type_sum[validation_indices] += type_probability
            clean_counts[validation_indices] += 1
            fold_models.append(
                (
                    model,
                    ridge_imputer,
                    type_model,
                    train_indices,
                    validation_indices,
                )
            )

    clean_label_probability, clean_severity_probability = (
        combine_multiview_probabilities(
            clean_ridge_label_sum / clean_counts[:, None],
            clean_ridge_severity_sum / clean_counts[:, None],
            clean_knn_label_sum / clean_counts[:, None],
            clean_knn_severity_sum / clean_counts[:, None],
            clean_type_sum / clean_counts[:, None],
        )
    )
    clean_labels, clean_severities, _ = decode_probabilities3(
        clean_label_probability,
        clean_severity_probability,
    )
    clean_score = score_predictions(
        validation,
        pd.DataFrame({"label": clean_labels, "severity": clean_severities}),
    )
    print("Czyste OOF:")
    print(pd.Series(clean_score).to_string())

    reports = []
    for offset in range(mask_count):
        seed = first_seed + offset
        mask = deterministic_constrained_mask(validation, seed)
        damaged = validation.copy()
        damaged_values = validation_spectra.copy()
        damaged_values[mask] = np.nan
        damaged[FREQ_COLS] = damaged_values
        query_residuals, query_peer, query_spectra = residual_matrix(damaged)
        distance_unlabeled = nan_euclidean_distances(
            query_residuals, unlabeled_residuals
        )
        distance_validation = nan_euclidean_distances(
            query_residuals, validation_residuals
        )

        ridge_label_sum = np.zeros((len(validation), len(LABELS)))
        ridge_severity_sum = np.zeros((len(validation), len(SEVERITIES)))
        knn_label_sum = np.zeros((len(validation), len(LABELS)))
        knn_severity_sum = np.zeros((len(validation), len(SEVERITIES)))
        type_sum = np.zeros((len(validation), 5))
        counts = np.zeros(len(validation), dtype=int)
        reconstructed_errors = []
        for (
            model,
            ridge_imputer,
            type_model,
            train_indices,
            validation_indices,
        ) in fold_models:
            local = validation_indices
            completed = fill_from_pools(
                query_residuals[local],
                query_peer[local],
                query_spectra[local],
                mask[local],
                [
                    distance_unlabeled[local],
                    distance_validation[np.ix_(local, train_indices)],
                ],
                [unlabeled_residuals, validation_residuals[train_indices]],
            )
            prepared = damaged.iloc[local].reset_index(drop=True)
            prepared[FREQ_COLS] = completed
            knn_label, knn_severity = model.predict_probabilities(
                prepared
            )
            raw = damaged.iloc[local].reset_index(drop=True)
            ridge_label, ridge_severity = model.predict_probabilities(
                ridge_imputer.transform(raw)
            )
            type_probability = type_model.predict_probabilities(raw)
            ridge_label_sum[local] += ridge_label
            ridge_severity_sum[local] += ridge_severity
            knn_label_sum[local] += knn_label
            knn_severity_sum[local] += knn_severity
            type_sum[local] += type_probability
            counts[local] += 1
            reconstructed_errors.extend(
                np.abs(
                    completed[mask[local]]
                    - validation_spectra[local][mask[local]]
                )
            )

        assert np.all(counts == cv_repeats)
        label_probability, severity_probability = combine_multiview_probabilities(
            ridge_label_sum / counts[:, None],
            ridge_severity_sum / counts[:, None],
            knn_label_sum / counts[:, None],
            knn_severity_sum / counts[:, None],
            type_sum / counts[:, None],
        )
        labels, severities, _ = decode_probabilities3(
            label_probability,
            severity_probability,
            MODEL3_ANOMALY_THRESHOLD,
        )
        score = score_predictions(
            validation,
            pd.DataFrame({"label": labels, "severity": severities}),
        )
        reports.append(
            {
                "seed": seed,
                "removed": int(mask.sum()),
                "max_per_row": int(mask.sum(axis=1).max()),
                "max_consecutive": max(longest_run(row) for row in mask),
                "reconstruction_mae": float(np.mean(reconstructed_errors)),
                **score,
            }
        )
        print(
            f"maska {offset + 1}/{mask_count}, seed={seed}: "
            f"raw={score['raw_score']:.6f}, "
            f"punkty={score['competition_points']:.3f}",
            flush=True,
        )

    report = pd.DataFrame(reports)
    return clean_score, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--masks", type=int, default=30)
    parser.add_argument("--first-seed", type=int, default=6000)
    parser.add_argument("--cv-repeats", type=int, default=10)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_DIR / "model" / "model3_missing_benchmark.csv",
    )
    args = parser.parse_args()
    if not 1 <= args.cv_repeats <= len(DEFAULT_CV_SEEDS):
        raise ValueError(
            f"cv-repeats musi należeć do zakresu 1–{len(DEFAULT_CV_SEEDS)}"
        )
    _, report = benchmark(args.masks, args.first_seed, args.cv_repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(args.output, index=False)
    print("\nPodsumowanie:")
    print(
        report[
            [
                "raw_score",
                "macro_f1_label",
                "severity_accuracy",
                "competition_points",
                "reconstruction_mae",
            ]
        ]
        .agg(["mean", "std", "min", "max"])
        .to_string()
    )
    print(f"\nZapisano: {args.output.resolve()}")


if __name__ == "__main__":
    main()
