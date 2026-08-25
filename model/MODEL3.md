# Model3 — odporność na brakujące pasma

`model3.py` jest niezależnym wariantem eksperymentalnym zgodnym z publicznym
API `model2.predict()`. Powstał po odrzuceniu metod, które podnosiły confidence,
ale nie poprawiały trafności.

## Architektura

1. `ResidualKNNImputer(k=5)` rekonstruuje resztę widma względem mediany
   cylindrów tego samego silnika. Bibliotekę tworzą `train.csv` i oznakowana
   część ucząca `val.csv`.
2. `BandRidgeImputer` niezależnie przewiduje każde brakujące pasmo z pozostałych
   20 pasm. Obie imputacje zmieniają wyłącznie pierwotne braki.
3. `MissingAwareTypeModel` nie imputuje danych: przy ocenie typu usterki wybiera
   podmacierz kowariancji odpowiadającą tylko rzeczywiście zmierzonym pasmom.
4. Stały soft-vote łączy prawdopodobieństwa etykiet KNN/masked-type i Ridge po
   `0.50`; dla powagi usterki wagi wynoszą odpowiednio `0.75` i `0.25`.
5. Predykcję stabilizuje ensemble 10 × Group 5-Fold oraz model końcowy. Każdy
   bazowy model i regresor przechowuje własny `StandardScaler` w pliku PKL.
6. Zamrożony próg anomalii wynosi `0.48`. Confidence pochodzi ze zgodności
   głosów modeli, nie jest deklarowany jako skalibrowane prawdopodobieństwo.

`test.csv` nie jest używany do uczenia rekonstruktora ani pseudoetykietowania.
Eksperyment transdukcyjny z dołączeniem testu obniżył najgorszy wynik, dlatego
został odrzucony. Testowano też interpolację liniową, same KNN/Ridge,
class-conditional imputation, losową augmentację brakami i maksymalizowanie
confidence. Każdy z tych wariantów osobno miał gorsze minimum.

## Trening i eksport

```bash
cd /home/szymon/hackathon/hackaton
.venv/bin/python model/model3.py
```

Program zapisuje:

- `model/acoustic_model3.pkl` — rekonstruktor, wszystkie skalery i ensemble;
- `model/verdict_explainer3.pkl` — osobny model wyjaśniający;
- `model/model3_predictions.csv` — cztery kolumny zgłoszeniowe;
- `model/model3_explanations.csv` — confidence i wyjaśnienia;
- `model/model3_band_explanations.csv` — informacje o pasmach.
- `model/model3_missing_benchmark.csv` — wynik odtworzony finalnym kodem;
- `model/model3_hash_benchmark.csv` i `model/model3_rng_benchmark.csv` —
  zachowane wyniki szerokich testów, w tym porównanie odrzuconych wariantów.

## Walidacja brakujących danych

```bash
.venv/bin/python -B tests/benchmark_model3_missing.py --masks 30
```

Benchmark:

- usuwa dokładnie 10% z 9996 komórek;
- dopuszcza najwyżej 3 braki w wierszu i 2 kolejne pasma;
- dzieli dane po całych `engine_id`;
- dla każdego rekonstruktora udostępnia tylko `train.csv` bez etykiet i
  oznakowaną część outer-train;
- nie udostępnia modelowi prawdziwych wartości usuniętych komórek.

Konfigurację zamrożono po serii rozwojowej `7000–7029`. Następnie wykonano dwa
testy bez dalszego dostrajania:

| Test | Maski | Średni raw | Najgorszy raw | Najmniej punktów |
|---|---:|---:|---:|---:|
| hash holdout `7030–7149` | 120 | 0.995421 | 0.976277 | 35.255 |
| niezależny RNG `8000–8099` | 100 | 0.996116 | 0.978070 | 35.614 |

Zachowany raport zawiera kolumnę `split`, która oddziela 30 masek rozwojowych
od 120 holdout. Wszystkie 220 masek końcowych przekroczyły 35 punktów. Dla
porównania pojedyncze widoki Ridge i masked-Gaussian spadały w tej samej serii
hash poniżej 35.

Jest to wynik lokalnego, powtarzanego Group-CV, a nie gwarancja wyniku na
ukrytym zbiorze konkursowym. Minimum oznacza najgorszą z przetestowanych masek,
nie matematyczną gwarancję dla dowolnego układu braków. Podział po całych
silnikach i osobny holdout ograniczają ryzyko overfittingu do cylindrów.

## API

```python
from model3 import predict

response = predict(records, include_bands=False, display=True)
```

Format odpowiedzi jest zgodny z `model2`: zawiera `results`, `model_votes`,
confidence głosowania, opis werdyktu i opcjonalne rekordy pasm.
