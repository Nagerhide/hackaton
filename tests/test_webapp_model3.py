"""Test regresyjny podłączenia model3 do backendu WebApp."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import pandas as pd

from WebApp.backend import main


async def run_immediately(function, *args, **kwargs):
    """W teście omija ograniczenia wątków sandboxa, zachowując argumenty API."""
    return function(*args, **kwargs)


class InMemoryUpload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content = content

    async def read(self, size: int = -1) -> bytes:
        return self.content if size < 0 else self.content[:size]


class WebAppModel3Tests(unittest.TestCase):
    def test_health_reports_model3_artifacts(self):
        health = main.health_check()

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["service"], "model3-prediction-api")
        self.assertEqual(health["version"], "3.0")
        self.assertEqual(health["model"], "acoustic_model3.pkl")
        self.assertEqual(health["explainer"], "verdict_explainer3.pkl")

    def test_uploaded_csv_uses_model3_contract(self):
        frame = pd.read_csv(main.PROJECT_DIR / "tests" / "test.csv")
        engine = frame[frame.engine_id.eq("test_0049")]
        upload = InMemoryUpload(
            "engine.csv", engine.to_csv(index=False).encode()
        )

        with patch.object(main, "run_in_threadpool", run_immediately):
            response = asyncio.run(main.run_prediction(upload))

        self.assertEqual(response["input_rows"], len(engine))
        self.assertEqual(len(response["results"]), len(engine))
        self.assertEqual(response["model_votes"], 51)
        self.assertTrue(response["reference_profiles"])


if __name__ == "__main__":
    unittest.main()
