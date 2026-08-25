"""Warstwa zgodności starszego modelu z publicznym API WebApp."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from WebApp.backend import model as legacy_model


# Artefakt był eksportowany, gdy klasy znajdowały się w module `model`.
sys.modules.setdefault("model", legacy_model)


def predict(
    frame: pd.DataFrame,
    classifier_path: Path | str,
    explainer_path: Path | str | None = None,
    include_bands: bool = False,
    display: bool = False,
) -> dict:
    del explainer_path, include_bands, display
    prepared = legacy_model.interpolate_raw_spectra(frame)
    classifier = legacy_model.load_exported_model(Path(classifier_path))
    labels, severities, confidence = classifier.predict(prepared)
    vote_count = max(1, len(getattr(classifier, "models", [])))
    results = []
    for index, source in prepared.reset_index(drop=True).iterrows():
        label = str(labels[index])
        severity = str(severities[index])
        certainty = float(np.clip(confidence[index], 0.0, 1.0))
        explanation = (
            "Nie wykryto usterki cylindra."
            if label == "ok"
            else f"Wykryto usterkę: {label}; powaga: {severity}."
        )
        results.append(
            {
                "engine_id": source["engine_id"],
                "cylinder": int(source["cylinder"]),
                "label": label,
                "severity": severity,
                "confidence": certainty,
                "vote_confidence": certainty,
                "label_vote_confidence": certainty,
                "severity_vote_confidence": certainty,
                "n_model_votes": vote_count,
                "suspicious_frequency_range": "",
                "suspicious_columns": "",
                "explanation": explanation,
            }
        )
    return {"results": results, "model_votes": vote_count}
