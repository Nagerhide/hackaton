"""HTTP API oraz serwer statyczny WebApp korzystający z model2."""

from __future__ import annotations

import io
import logging
import sys
import zipfile
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response


PROJECT_DIR = Path(__file__).resolve().parents[2]
WEBAPP_DIR = PROJECT_DIR / "WebApp"
MODEL_PATH = PROJECT_DIR / "model" / "acoustic_model2.pkl"
EXPLAINER_PATH = PROJECT_DIR / "model" / "verdict_explainer.pkl"

# Ten moduł może być uruchamiany zarówno z katalogu projektu, jak i bezpośrednio
# z WebApp/backend. W obu przypadkach importujemy jeden, publiczny punkt inferencji.
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from predict_api import predict as model2_predict  # noqa: E402


LOGGER = logging.getLogger("model2-webapp")
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_EXTRACTED_CSV_SIZE = 10 * 1024 * 1024
MAX_COMPRESSION_RATIO = 50

app = FastAPI(
    title="PIHER2 Engine Failure Predictor",
    description="WebApp i API inferencji model2.",
    version="2.0",
)

# Umożliwia także otwarcie index.html bezpośrednio z dysku. Przy zwykłym
# uruchomieniu WebApp korzysta z tego samego originu i CORS nie jest potrzebny.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000", "null"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def extract_csv_from_zip(data: bytes) -> bytes:
    """Zwraca pojedynczy, bezpiecznie zweryfikowany plik CSV z archiwum ZIP."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) != 1:
                raise HTTPException(
                    status_code=400,
                    detail="Archiwum ZIP musi zawierać dokładnie jeden plik CSV.",
                )

            member = members[0]
            member_path = Path(member.filename)
            if member_path.suffix.lower() != ".csv":
                raise HTTPException(status_code=400, detail="Archiwum musi zawierać plik CSV.")
            if member_path.is_absolute() or ".." in member_path.parts:
                raise HTTPException(status_code=400, detail="Niedozwolona ścieżka w archiwum ZIP.")
            if member.flag_bits & 0x1:
                raise HTTPException(status_code=400, detail="Zaszyfrowane archiwa nie są obsługiwane.")
            if member.file_size == 0:
                raise HTTPException(status_code=400, detail="Plik CSV w archiwum jest pusty.")
            if member.file_size > MAX_EXTRACTED_CSV_SIZE:
                raise HTTPException(status_code=413, detail="Rozpakowany CSV przekracza 10 MB.")
            if member.compress_size == 0 or member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                raise HTTPException(status_code=400, detail="Niedozwolony współczynnik kompresji ZIP.")
            return archive.read(member)
    except zipfile.BadZipFile as error:
        raise HTTPException(status_code=400, detail="Nieprawidłowe archiwum ZIP.") from error


async def read_uploaded_csv(file: UploadFile) -> bytes:
    """Waliduje upload CSV/ZIP i zwraca bajty CSV."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nie podano nazwy pliku.")

    filename = file.filename.lower()
    if not filename.endswith((".csv", ".zip")):
        raise HTTPException(
            status_code=400,
            detail="Akceptowane są CSV albo ZIP zawierający jeden CSV.",
        )

    # Czytanie o jeden bajt ponad limit pozwala odrzucić plik bez wczytywania
    # dowolnie dużego uploadu do pamięci.
    data = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Plik przekracza 10 MB.")
    if not data:
        raise HTTPException(status_code=400, detail="Plik jest pusty.")
    return extract_csv_from_zip(data) if filename.endswith(".zip") else data


def parse_csv(data: bytes) -> pd.DataFrame:
    try:
        frame = pd.read_csv(io.BytesIO(data))
    except (UnicodeDecodeError, pd.errors.ParserError) as error:
        raise HTTPException(status_code=400, detail=f"Nie można odczytać CSV: {error}") from error
    if frame.empty:
        raise HTTPException(status_code=400, detail="CSV nie zawiera żadnych rekordów.")
    return frame


async def run_prediction(file: UploadFile, include_bands: bool = False) -> dict:
    data = await read_uploaded_csv(file)
    frame = parse_csv(data)
    try:
        response = await run_in_threadpool(
            model2_predict,
            frame,
            include_bands,
            False,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:  # pragma: no cover - ochronna granica endpointu
        LOGGER.exception("Predykcja model2 nie powiodła się")
        raise HTTPException(status_code=500, detail="Predykcja model2 nie powiodła się.") from error
    response["input_rows"] = len(frame)
    return response


@app.get("/api/health")
def health_check() -> dict:
    artifacts_ready = MODEL_PATH.is_file() and EXPLAINER_PATH.is_file()
    return {
        "status": "ok" if artifacts_ready else "missing_model",
        "service": "model2-prediction-api",
        "model": MODEL_PATH.name,
        "explainer": EXPLAINER_PATH.name,
        "artifacts_ready": artifacts_ready,
    }


@app.post("/api/extract-csv", response_class=Response)
async def extract_csv(file: UploadFile = File(...)) -> Response:
    """Udostępnia CSV z uploadu, aby podgląd ZIP działał w przeglądarce."""
    data = await read_uploaded_csv(file)
    return Response(content=data, media_type="text/csv; charset=utf-8")


@app.post("/api/predict")
async def prediction(
    file: UploadFile = File(...),
    include_bands: bool = False,
) -> dict:
    """Uruchamia model2 i zwraca predykcje wraz z wyjaśnieniem werdyktu."""
    return await run_prediction(file, include_bands=include_bands)


# Aliasy zachowują kompatybilność z wcześniejszym adresem backendu.
@app.post("/extract-csv", include_in_schema=False, response_class=Response)
async def legacy_extract_csv(file: UploadFile = File(...)) -> Response:
    return await extract_csv(file)


@app.post("/predict", include_in_schema=False)
async def legacy_prediction(file: UploadFile = File(...)) -> dict:
    return await run_prediction(file)


@app.get("/style.css", include_in_schema=False)
@app.get("/static/style.css", include_in_schema=False)
def stylesheet() -> FileResponse:
    return FileResponse(WEBAPP_DIR / "style.css", media_type="text/css")


@app.get("/script.js", include_in_schema=False)
@app.get("/static/script.js", include_in_schema=False)
def javascript() -> FileResponse:
    return FileResponse(WEBAPP_DIR / "script.js", media_type="text/javascript")


@app.get("/images/logo.png", include_in_schema=False)
def logo() -> FileResponse:
    return FileResponse(PROJECT_DIR / "images" / "logo.png", media_type="image/png")


@app.get("/", include_in_schema=False)
def webapp() -> FileResponse:
    return FileResponse(WEBAPP_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
