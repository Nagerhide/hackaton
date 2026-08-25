"""Stabilny punkt wejścia do model2 dla FastAPI, Flask i innych serwerów."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
MODEL_DIR = PROJECT_DIR / "model"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

from model2 import clear_model_cache  # noqa: E402,F401
from model2 import predict as _model_predict  # noqa: E402


def predict(data, include_bands: bool = False, display: bool = False) -> dict:
    """Uruchamia diagnozę i zwraca odpowiedź gotową do serializacji JSON."""
    return _model_predict(
        data,
        classifier_path=MODEL_DIR / "acoustic_model2.pkl",
        explainer_path=MODEL_DIR / "verdict_explainer.pkl",
        include_bands=include_bands,
        display=display,
    )


__all__ = ["predict", "clear_model_cache"]
