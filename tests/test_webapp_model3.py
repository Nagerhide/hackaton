"""Test regresyjny wyboru model2/model3 w backendzie WebApp."""

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


class WebAppModelSelectionTests(unittest.TestCase):
    def test_health_reports_three_models_and_model2_as_default(self):
        health = main.health_check()

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["service"], "piher2-prediction-api")
        self.assertEqual(health["version"], "4.0")
        self.assertEqual(health["default_model"], "model2")
        self.assertTrue(health["models"]["model1"]["ready"])
        self.assertTrue(health["models"]["model2"]["ready"])
        self.assertTrue(health["models"]["model3"]["ready"])

    def test_uploaded_csv_uses_model2_by_default_and_allows_model1_and_model3(self):
        frame = pd.read_csv(main.PROJECT_DIR / "tests" / "test.csv")
        engine = frame[frame.engine_id.eq("test_0049")]
        content = engine.to_csv(index=False).encode()

        with patch.object(main, "run_in_threadpool", run_immediately):
            default_response = asyncio.run(
                main.run_prediction(InMemoryUpload("engine.csv", content))
            )
            model1_response = asyncio.run(
                main.run_prediction(
                    InMemoryUpload("engine.csv", content), model_name="model1"
                )
            )
            model3_response = asyncio.run(
                main.run_prediction(
                    InMemoryUpload("engine.csv", content), model_name="model3"
                )
            )

        self.assertEqual(default_response["selected_model"], "model2")
        self.assertEqual(model1_response["selected_model"], "model1")
        self.assertEqual(model3_response["selected_model"], "model3")
        self.assertEqual(len(model1_response["results"]), len(engine))
        self.assertEqual(default_response["input_rows"], len(engine))
        self.assertEqual(len(model3_response["results"]), len(engine))
        self.assertEqual(model3_response["model_votes"], 51)
        self.assertTrue(default_response["reference_profiles"])


if __name__ == "__main__":
    unittest.main()
