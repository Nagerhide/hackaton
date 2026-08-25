"""HTTP API WebApp: modele 2/3, konta pracowników i zadania serwisowe."""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import zipfile
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field


PROJECT_DIR = Path(__file__).resolve().parents[2]
WEBAPP_DIR = PROJECT_DIR / "WebApp"
MODEL_DIR = PROJECT_DIR / "model"
REFERENCE_SPECTRA_PATH = WEBAPP_DIR / "reference_spectra.json"
DATABASE_PATH = Path(
    os.environ.get("PIHER2_DATABASE_PATH", WEBAPP_DIR / "backend" / "piher2.sqlite3")
)

# Ten moduł może być uruchamiany zarówno z katalogu projektu, jak i bezpośrednio
# z WebApp/backend. W obu przypadkach importujemy jeden, publiczny punkt inferencji.
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from model2 import predict as model2_predict  # noqa: E402
from model3 import predict as model3_predict  # noqa: E402
from WebApp.backend.store import AppStore, StoreError  # noqa: E402


DEFAULT_MODEL = "model2"
MODEL_CONFIGS = {
    "model2": {
        "predict": model2_predict,
        "classifier": MODEL_DIR / "acoustic_model2.pkl",
        "explainer": MODEL_DIR / "verdict_explainer.pkl",
    },
    "model3": {
        "predict": model3_predict,
        "classifier": MODEL_DIR / "acoustic_model3.pkl",
        "explainer": MODEL_DIR / "verdict_explainer3.pkl",
    },
}
MODEL_PATH = MODEL_CONFIGS[DEFAULT_MODEL]["classifier"]
EXPLAINER_PATH = MODEL_CONFIGS[DEFAULT_MODEL]["explainer"]
STORE = AppStore(DATABASE_PATH)


LOGGER = logging.getLogger("piher2-webapp")
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_EXTRACTED_CSV_SIZE = 10 * 1024 * 1024
MAX_COMPRESSION_RATIO = 50

app = FastAPI(
    title="PIHER2 Engine Failure Predictor",
    description="Diagnostyka model2/model3 oraz współdzielona lista serwisowa.",
    version="4.0",
)

# Umożliwia także otwarcie index.html bezpośrednio z dysku. Przy zwykłym
# uruchomieniu WebApp korzysta z tego samego originu i CORS nie jest potrzebny.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000", "null"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


class AccountRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(default="", max_length=80)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=256)


class TodoCreateRequest(BaseModel):
    owner_id: int | None = None
    engine_id: str = Field(min_length=1, max_length=100)
    cylinder: int = Field(ge=1, le=1000)
    n_cylinders: int | None = Field(default=None, ge=1, le=1000)
    fault_label: str = Field(min_length=1, max_length=100)
    severity: str = "nie_dotyczy"
    note: str = Field(default="", max_length=1000)
    status: str = "todo"
    spectrum: list[float | None] = Field(default_factory=list, max_length=21)


class TodoUpdateRequest(BaseModel):
    owner_id: int | None = None
    fault_label: str | None = Field(default=None, min_length=1, max_length=100)
    severity: str | None = None
    note: str | None = Field(default=None, max_length=1000)
    status: str | None = None


def store_result(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except StoreError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Zaloguj się, aby wykonać tę operację.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Nieprawidłowy token sesji.")
    return token


def current_user(token: str = Depends(bearer_token)) -> dict:
    return store_result(STORE.user_for_token, token)


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


@lru_cache(maxsize=1)
def load_reference_spectra() -> dict:
    """Wczytuje gotowe średnie; endpoint predykcji nie przelicza pliku valid."""
    if not REFERENCE_SPECTRA_PATH.is_file():
        LOGGER.warning("Nie znaleziono %s", REFERENCE_SPECTRA_PATH)
        return {"source": None, "by_label": {}, "profiles": []}

    try:
        with REFERENCE_SPECTRA_PATH.open(encoding="utf-8") as reference_file:
            payload = json.load(reference_file)
    except (OSError, json.JSONDecodeError):
        LOGGER.exception("Nie można wczytać zapisanych widm referencyjnych")
        return {"source": None, "by_label": {}, "profiles": []}

    if not isinstance(payload.get("by_label"), dict) or not isinstance(
        payload.get("profiles"), list
    ):
        LOGGER.error("Nieprawidłowy format %s", REFERENCE_SPECTRA_PATH)
        return {"source": payload.get("source"), "by_label": {}, "profiles": []}
    return payload


async def run_prediction(
    file: UploadFile,
    include_bands: bool = False,
    model_name: str = DEFAULT_MODEL,
) -> dict:
    data = await read_uploaded_csv(file)
    frame = parse_csv(data)
    model_name = str(model_name).lower()
    model_config = MODEL_CONFIGS.get(model_name)
    if model_config is None:
        raise HTTPException(
            status_code=422,
            detail=f"Nieznany model. Dostępne: {', '.join(MODEL_CONFIGS)}.",
        )
    if not model_config["classifier"].is_file() or not model_config["explainer"].is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Artefakty {model_name} nie są dostępne.",
        )
    try:
        response = await run_in_threadpool(
            model_config["predict"],
            frame,
            classifier_path=model_config["classifier"],
            explainer_path=model_config["explainer"],
            include_bands=include_bands,
            display=False,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:  # pragma: no cover - ochronna granica endpointu
        LOGGER.exception("Predykcja %s nie powiodła się", model_name)
        raise HTTPException(
            status_code=500, detail=f"Predykcja {model_name} nie powiodła się."
        ) from error
    reference_data = load_reference_spectra()
    response["selected_model"] = model_name
    response["input_rows"] = len(frame)
    response["reference_spectra"] = reference_data["by_label"]
    response["reference_profiles"] = reference_data["profiles"]
    response["reference_spectra_source"] = reference_data.get("source")
    return response


@app.get("/api/health")
def health_check() -> dict:
    models = {
        name: {
            "ready": config["classifier"].is_file() and config["explainer"].is_file(),
            "model": config["classifier"].name,
            "explainer": config["explainer"].name,
        }
        for name, config in MODEL_CONFIGS.items()
    }
    artifacts_ready = models[DEFAULT_MODEL]["ready"]
    reference_data = load_reference_spectra()
    return {
        "status": "ok" if artifacts_ready else "missing_model",
        "service": "piher2-prediction-api",
        "version": app.version,
        "default_model": DEFAULT_MODEL,
        "models": models,
        "model": MODEL_PATH.name,
        "explainer": EXPLAINER_PATH.name,
        "artifacts_ready": artifacts_ready,
        "reference_spectra_ready": bool(reference_data["profiles"]),
        "reference_spectra_source": reference_data.get("source"),
    }


@app.post("/api/auth/register", status_code=201)
def register_account(request: AccountRequest) -> dict:
    """Tworzy samodzielne konto przełożonego i od razu rozpoczyna sesję."""
    return store_result(
        STORE.register, request.username, request.password, request.display_name
    )


@app.post("/api/auth/login")
def login(request: LoginRequest) -> dict:
    return store_result(STORE.login, request.username, request.password)


@app.post("/api/auth/logout", status_code=204)
def logout(token: str = Depends(bearer_token)) -> Response:
    STORE.logout(token)
    return Response(status_code=204)


@app.get("/api/auth/me")
def session_user(user: dict = Depends(current_user)) -> dict:
    return {"user": user}


@app.get("/api/employees")
def employees(user: dict = Depends(current_user)) -> dict:
    return {"employees": store_result(STORE.list_employees, user)}


@app.post("/api/employees", status_code=201)
def add_employee(
    request: AccountRequest, user: dict = Depends(current_user)
) -> dict:
    employee = store_result(
        STORE.create_employee,
        user,
        request.username,
        request.password,
        request.display_name,
    )
    return {"employee": employee}


@app.get("/api/todos")
def todos(
    owner_id: int | None = None,
    status: str | None = None,
    user: dict = Depends(current_user),
) -> dict:
    return {
        "todos": store_result(
            STORE.list_todos, user, owner_id=owner_id, status=status
        )
    }


@app.post("/api/todos", status_code=201)
def add_todo(
    request: TodoCreateRequest, user: dict = Depends(current_user)
) -> dict:
    return {
        "todo": store_result(
            STORE.create_todo, user, request.model_dump()
        )
    }


@app.patch("/api/todos/{todo_id}")
def edit_todo(
    todo_id: int,
    request: TodoUpdateRequest,
    user: dict = Depends(current_user),
) -> dict:
    return {
        "todo": store_result(
            STORE.update_todo,
            user,
            todo_id,
            request.model_dump(exclude_unset=True),
        )
    }


@app.delete("/api/todos/{todo_id}", status_code=204)
def remove_todo(todo_id: int, user: dict = Depends(current_user)) -> Response:
    store_result(STORE.delete_todo, user, todo_id)
    return Response(status_code=204)


@app.post("/api/extract-csv", response_class=Response)
async def extract_csv(file: UploadFile = File(...)) -> Response:
    """Udostępnia CSV z uploadu, aby podgląd ZIP działał w przeglądarce."""
    data = await read_uploaded_csv(file)
    return Response(content=data, media_type="text/csv; charset=utf-8")


@app.post("/api/predict")
async def prediction(
    file: UploadFile = File(...),
    include_bands: bool = False,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Uruchamia wybrany model i zwraca predykcje wraz z wyjaśnieniem."""
    return await run_prediction(file, include_bands=include_bands, model_name=model)


# Aliasy zachowują kompatybilność z wcześniejszym adresem backendu.
@app.post("/extract-csv", include_in_schema=False, response_class=Response)
async def legacy_extract_csv(file: UploadFile = File(...)) -> Response:
    return await extract_csv(file)


@app.post("/predict", include_in_schema=False)
async def legacy_prediction(
    file: UploadFile = File(...), model: str = DEFAULT_MODEL
) -> dict:
    return await run_prediction(file, model_name=model)


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
