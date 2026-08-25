from pathlib import Path
import io
import os
import tempfile
import zipfile

import joblib
import pandas as pd

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from DEBUG import DEBUG
from model import interpolate_raw_spectra, prediction_frame


# ============================================================
# APP
# ============================================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODEL
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "acoustic_model.pkl"

print("Loading model...")

artifact = joblib.load(MODEL_PATH)

if artifact.get("format_version") != 2:
    raise ValueError("Nieobsługiwana wersja modelu")

model = artifact["model"]

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_EXTRACTED_CSV_SIZE = 10 * 1024 * 1024
MAX_COMPRESSION_RATIO = 50

print("Model loaded successfully!")


def report_debug_accuracy(source: pd.DataFrame, predictions: pd.DataFrame) -> None:
    """Print evaluation metrics only for labelled debug uploads."""
    required_columns = {"label", "severity"}
    if not DEBUG or not required_columns.issubset(source.columns):
        return

    truth = source.loc[:, ["label", "severity"]].reset_index(drop=True)
    predicted = predictions.loc[:, ["label", "severity"]].reset_index(drop=True)
    valid_rows = truth.notna().all(axis=1)

    if not valid_rows.any():
        print("DEBUG accuracy skipped: label and severity contain no complete ground-truth rows.")
        return

    truth = truth.loc[valid_rows]
    predicted = predicted.loc[valid_rows]
    label_accuracy = (truth["label"] == predicted["label"]).mean()
    severity_accuracy = (truth["severity"] == predicted["severity"]).mean()
    combined_accuracy = (
        (truth["label"] == predicted["label"])
        & (truth["severity"] == predicted["severity"])
    ).mean()

    print(
        "DEBUG model accuracy "
        f"({len(truth)} labelled rows): "
        f"label={label_accuracy:.2%}, "
        f"severity={severity_accuracy:.2%}, "
        f"combined={combined_accuracy:.2%}"
    )

def prediction_frame(model, frame: pd.DataFrame) -> pd.DataFrame:
    print("MODEL:", type(model))

    labels, severities, confidence = model.predict(frame)

    print("LABELS:", labels[:10])
    print("SEVERITIES:", severities[:10])
    print("CONFIDENCE:", confidence[:10])

    result = frame[["engine_id", "cylinder"]].reset_index(drop=True).copy()

    result["label"] = labels
    result["severity"] = severities
    result["confidence"] = confidence

    return result
# ============================================================
# PREDICT
# ============================================================
def predict(input_path: str) -> str:
    # Wczytanie CSV
    frame = pd.read_csv(input_path)

    print("Input columns:", frame.columns.tolist())
    print("Input shape:", frame.shape)

    # Preprocessing
    frame = interpolate_raw_spectra(frame)

    # Predykcja
    result = prediction_frame(model, frame)
    report_debug_accuracy(frame, result)

    print("Prediction result:")
    print(result.head())
    print("Result columns:", result.columns.tolist())

    # Zamiana DataFrame -> CSV
    return result.to_csv(index=False)


def extract_csv_from_zip(data: bytes) -> bytes:
    """Returns the single, safely validated CSV file contained in a ZIP archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) != 1:
                raise HTTPException(
                    status_code=400,
                    detail="Archiwum ZIP musi zawierać dokładnie jeden plik CSV",
                )

            member = members[0]
            member_path = Path(member.filename)
            if member_path.suffix.lower() != ".csv":
                raise HTTPException(status_code=400, detail="Archiwum ZIP musi zawierać plik CSV")
            if member_path.is_absolute() or ".." in member_path.parts:
                raise HTTPException(status_code=400, detail="Niedozwolona ścieżka w archiwum ZIP")
            if member.flag_bits & 0x1:
                raise HTTPException(status_code=400, detail="Zaszyfrowane archiwa ZIP nie są obsługiwane")
            if member.file_size == 0:
                raise HTTPException(status_code=400, detail="Plik CSV w archiwum jest pusty")
            if member.file_size > MAX_EXTRACTED_CSV_SIZE:
                raise HTTPException(status_code=400, detail="Rozpakowany plik CSV musi być mniejszy niż 10 MB")
            if member.compress_size == 0 or member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                raise HTTPException(status_code=400, detail="Archiwum ma niedozwolony współczynnik kompresji")
            return archive.read(member)
    except zipfile.BadZipFile as error:
        raise HTTPException(status_code=400, detail="Nieprawidłowe archiwum ZIP") from error


async def read_uploaded_csv(file: UploadFile) -> bytes:
    """Validate an uploaded CSV/ZIP and return its CSV bytes."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nie podano nazwy pliku")

    filename = file.filename.lower()
    if not filename.endswith((".csv", ".zip")):
        raise HTTPException(
            status_code=400,
            detail="Akceptowane są tylko pliki CSV lub ZIP zawierające jeden plik CSV",
        )

    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Plik musi być mniejszy niż 10 MB")
    if not data:
        raise HTTPException(status_code=400, detail="Plik jest pusty")
    return extract_csv_from_zip(data) if filename.endswith(".zip") else data


# ============================================================
# API
# ============================================================

@app.post("/extract-csv")
async def extract_csv(file: UploadFile = File(...)):
    """Expose the CSV within an upload so ZIP files support the full UI."""
    data = await read_uploaded_csv(file)
    return Response(content=data, media_type="text/csv")

@app.post("/predict")
async def prediction(file: UploadFile = File(...)):

    print("Received file:", file.filename)

    # --------------------------------------------------------
    # CHECK EXTENSION
    # --------------------------------------------------------

    data = await read_uploaded_csv(file)

    # --------------------------------------------------------
    # TEMPORARY FILE
    # --------------------------------------------------------

    input_path = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".csv",
            delete=False
        ) as temp_file:

            temp_file.write(data)
            input_path = temp_file.name

        # ----------------------------------------------------
        # PREDICTION
        # ----------------------------------------------------

        csv_data = predict(input_path)

        # ----------------------------------------------------
        # RETURN CSV
        # ----------------------------------------------------

        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="wynik.csv"'
            }
        )

    except HTTPException:
        raise
    except Exception as e:

        print("PREDICTION ERROR:")
        print(repr(e))

        raise HTTPException(
            status_code=500,
            detail=f"Predykcja się nie udała: {str(e)}"
        )

    finally:

        # ----------------------------------------------------
        # REMOVE TEMP FILE
        # ----------------------------------------------------

        if input_path is not None and os.path.exists(input_path):
            os.remove(input_path)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
def health_check():

    return {
        "status": "ok",
        "service": "prediction-api"
    }
