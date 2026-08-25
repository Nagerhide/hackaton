# PIHER2 — diagnostyka akustyczna silnika

WebApp jest podłączony do `model/model2.py` przez stabilną funkcję
`predict_api.predict`. Ten sam proces FastAPI serwuje stronę oraz endpointy,
więc nie trzeba uruchamiać osobnego serwera statycznego.

## Uruchomienie

```bash
cd /home/szymon/hackathon/hackaton
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python WebApp/backend/main.py
```

Następnie otwórz <http://127.0.0.1:8000>. Dokumentacja API jest dostępna pod
<http://127.0.0.1:8000/docs>, a kontrola gotowości pod
<http://127.0.0.1:8000/api/health>.

Backend automatycznie korzysta z artefaktów:

- `model/acoustic_model2.pkl` — ensemble klasyfikatorów;
- `model/verdict_explainer.pkl` — osobny model wyjaśniający werdykt.

## Dane wejściowe

Interfejs przyjmuje plik CSV albo ZIP zawierający dokładnie jeden CSV, do 10 MB.
Wymagane kolumny to:

```text
engine_id, cylinder, n_cylinders, mV_0, ..., mV_20
```

W jednym żądaniu trzeba przekazać wszystkie cylindry każdego silnika. Kolumny
`label` oraz `severity` mogą występować w pliku walidacyjnym, ale nie są
wymagane do predykcji.

Po analizie WebApp pokazuje etykietę, nasilenie, confidence z głosowania
ensemble, wyjaśnienie oraz podejrzany fragment widma. Pełny wynik można pobrać
jako `wynik_model2.csv`.

## API

`POST /api/predict` przyjmuje `multipart/form-data` z polem `file`. Odpowiedź ma
postać JSON:

```json
{
  "results": [
    {
      "engine_id": "test_0002",
      "cylinder": 8,
      "label": "unknown",
      "severity": "nie_dotyczy",
      "vote_confidence": 0.9411764706,
      "label_vote_confidence": 0.9411764706,
      "severity_vote_confidence": 1.0,
      "n_model_votes": 51,
      "suspicious_frequency_range": "1–4 kHz",
      "suspicious_columns": "mV_1,mV_2,mV_3,mV_4",
      "explanation": "Podejrzenie unknown: najbardziej nietypowe jest pasmo 1–4 kHz..."
    }
  ],
  "model_votes": 51,
  "input_rows": 12
}
```

Opcjonalne `?include_bands=true` dodaje szczegółową tabelę wyników dla każdego
pasma. Historyczne adresy `/predict` oraz `/extract-csv` pozostają aliasami.
