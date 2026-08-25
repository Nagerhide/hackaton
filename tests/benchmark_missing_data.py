"""Group-CV model2 po deterministycznym usunięciu 10% pomiarów.

Uruchomienie:
    .venv/bin/python -B tests/benchmark_missing_data.py --masks 5
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_DIR / "model"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

from model2 import (  # noqa: E402
    FREQ_COLS,
    AcousticDiagnosticModel,
    group_kfold_indices,
    interpolate_raw_spectra,
    optimize_imputed_spectra,
    prediction_frame,
    score_predictions,
)


def deterministic_mask(frame: pd.DataFrame, seed: int, fraction: float = 0.10):
    """Usuwa dokładny odsetek komórek według stabilnego BLAKE2b."""
    ranked = []
    keys = frame[["engine_id", "cylinder"]].itertuples(index=False)
    for row_index, row in enumerate(keys):
        for band_index, column in enumerate(FREQ_COLS):
            token = f"{seed}|{row.engine_id}|{row.cylinder}|{column}".encode()
            rank = hashlib.blake2b(token, digest_size=8).digest()
            ranked.append((rank, row_index, band_index))

    mask = np.zeros((len(frame), len(FREQ_COLS)), dtype=bool)
    for _, row_index, band_index in sorted(ranked)[: round(len(ranked) * fraction)]:
        mask[row_index, band_index] = True
    damaged = frame.copy()
    values = damaged[FREQ_COLS].to_numpy(dtype=float, copy=True)
    values[mask] = np.nan
    damaged[FREQ_COLS] = values
    return damaged, mask


def benchmark(mask_count: int = 5, first_seed: int = 2026) -> pd.DataFrame:
    validation = pd.read_csv(PROJECT_DIR / "tests" / "val.csv").reset_index(drop=True)
    folds = list(group_kfold_indices(validation, n_splits=5, seed=42))
    models = [
        AcousticDiagnosticModel().fit(validation.iloc[train].reset_index(drop=True))
        for train, _ in folds
    ]
    reports = []

    for seed in range(first_seed, first_seed + mask_count):
        damaged, global_mask = deterministic_mask(validation, seed)
        truth_parts, baseline_parts, optimized_parts = [], [], []
        true_missing, baseline_missing, optimized_missing = [], [], []
        applied = evaluations = observed_changes = 0

        for model, (_, validation_indices) in zip(models, folds):
            truth = validation.iloc[validation_indices].reset_index(drop=True)
            fold_input = damaged.iloc[validation_indices].reset_index(drop=True)
            missing_mask = global_mask[validation_indices]
            prepared = interpolate_raw_spectra(fold_input)
            baseline = prediction_frame(model, prepared)
            optimized_frame, audit = optimize_imputed_spectra(model, prepared)
            optimized = prediction_frame(model, optimized_frame)

            truth_parts.append(truth)
            baseline_parts.append(baseline)
            optimized_parts.append(optimized)
            true_values = truth[FREQ_COLS].to_numpy(dtype=float)
            baseline_values = prepared[FREQ_COLS].to_numpy(dtype=float)
            optimized_values = optimized_frame[FREQ_COLS].to_numpy(dtype=float)
            true_missing.extend(true_values[missing_mask])
            baseline_missing.extend(baseline_values[missing_mask])
            optimized_missing.extend(optimized_values[missing_mask])
            observed_changes += int(
                np.count_nonzero(optimized_values[~missing_mask] != true_values[~missing_mask])
            )
            applied += int(audit.confidence_optimization_applied.sum())
            evaluations += int(audit.optimization_candidate_evaluations.sum())

        truth = pd.concat(truth_parts, ignore_index=True)
        baseline = pd.concat(baseline_parts, ignore_index=True)
        optimized = pd.concat(optimized_parts, ignore_index=True)
        baseline_score = score_predictions(truth, baseline)
        optimized_score = score_predictions(truth, optimized)
        baseline_error = baseline.label.to_numpy() != truth.label.to_numpy()
        optimized_error = optimized.label.to_numpy() != truth.label.to_numpy()

        reports.append(
            {
                "seed": seed,
                "removed": int(global_mask.sum()),
                "baseline_raw": baseline_score["raw_score"],
                "optimized_raw": optimized_score["raw_score"],
                "baseline_macro_f1": baseline_score["macro_f1_label"],
                "optimized_macro_f1": optimized_score["macro_f1_label"],
                "baseline_severity": baseline_score["severity_accuracy"],
                "optimized_severity": optimized_score["severity_accuracy"],
                "baseline_confidence": float(baseline.vote_confidence.mean()),
                "optimized_confidence": float(optimized.vote_confidence.mean()),
                "baseline_mae": float(
                    np.mean(np.abs(np.asarray(baseline_missing) - true_missing))
                ),
                "optimized_mae": float(
                    np.mean(np.abs(np.asarray(optimized_missing) - true_missing))
                ),
                "verdict_changes": int(
                    (
                        (baseline.label != optimized.label)
                        | (baseline.severity != optimized.severity)
                    ).sum()
                ),
                "observed_value_changes": observed_changes,
                "high_confidence_errors_before": int(
                    (baseline_error & baseline.vote_confidence.ge(0.95)).sum()
                ),
                "high_confidence_errors_after": int(
                    (optimized_error & optimized.vote_confidence.ge(0.95)).sum()
                ),
                "optimized_cylinders": applied,
                "candidate_evaluations": evaluations,
            }
        )
    return pd.DataFrame(reports)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--masks", type=int, default=5)
    parser.add_argument("--first-seed", type=int, default=2026)
    args = parser.parse_args()
    report = benchmark(args.masks, args.first_seed)
    print(report.to_string(index=False))
    print("\nŚrednia:")
    print(report.mean(numeric_only=True).to_string())


if __name__ == "__main__":
    main()
