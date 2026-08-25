# Model2 — diagnoza i wyjaśnienie werdyktu

`model2.py` jest samodzielnym programem kompatybilnym z plikami `val.csv`,
`train.csv` i `test.csv` opisanymi w projekcie. Wczytuje dane surowe,
interpoluje braki i dopasowuje `StandardScaler` wyłącznie na danych uczących.

## Uruchomienie

```bash
cd /home/szymon/hackathon/hackaton
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python model/model2.py
```

Domyślne katalogi są wyznaczane względem skryptu:

```text
projekt: /home/szymon/hackathon/hackaton
dane:    /home/szymon/hackathon/hackaton/tests
wyniki:  /home/szymon/hackathon/hackaton/model
```

## Pliki wynikowe

- `acoustic_model2.pkl` — klasyfikator ensemble;
- `verdict_explainer.pkl` — niezależny model wyjaśniający;
- `model2_predictions.csv` — format zgodny z `sample_submit.csv`;
- `model2_explanations.csv` — werdykt i podejrzany zakres dla cylindra;
- `model2_band_explanations.csv` — po jednym wierszu na pasmo, z kolumną
  `is_suspicious` wskazującą fragment wymagający uwagi.

Explainer porównuje widmo cylindra ze zdrową referencją wyznaczoną wyłącznie
z rekordów `label == "ok"`. Wskazanie pasma jest informacją diagnostyczną,
a nie dowodem związku przyczynowego.

## Inferencja po eksporcie

```python
from pathlib import Path
import pandas as pd
from model2 import predict_and_explain

raw = pd.read_csv("nowe_pomiary.csv")
predictions, explanations, bands = predict_and_explain(
    Path("acoustic_model2.pkl"),
    Path("verdict_explainer.pkl"),
    raw,
)
```

## Podłączenie do serwera

Publiczna funkcja `predict` przyjmuje `DataFrame`, listę rekordów albo słownik
JSON i zwraca słownik gotowy do serializacji:

```python
from model2 import predict

response = predict(
    request_json,
    include_bands=False,
    display=False,
)
```

Przykład FastAPI:

```python
from fastapi import FastAPI
from predict_api import predict

app = FastAPI()

@app.post("/predict")
def diagnose(payload: dict):
    return predict(payload, include_bands=False)
```

Każdy wynik zawiera `vote_confidence`, `label_vote_confidence`,
`severity_vote_confidence`, liczbę głosujących modeli, opis oraz podejrzane
pasmo. `vote_confidence` jest odsetkiem modeli ensemble zgodnych z finalnym
werdyktem. Szczegóły wszystkich pasm można dołączyć przez
`include_bands=True`.

Wyświetlanie wyjaśnień podczas uruchamiania CLI:

```bash
.venv/bin/python model/model2.py --show-explanations --show-limit 20
```

Do inferencji należy przekazywać razem wszystkie cylindry danego silnika,
ponieważ zarówno klasyfikator, jak i explainer korzystają z mediany silnika.
