# Benchmark brakujących danych i optymalizacji confidence

## Metoda

- dane: `tests/val.csv` — 476 cylindrów i 9996 pomiarów `mV`;
- walidacja: Group 5-Fold, bez wspólnych `engine_id` w treningu i walidacji;
- pięć masek BLAKE2b, seed `2026`–`2030`;
- każda maska usuwa dokładnie 1000 wartości, czyli 10% informacji;
- model nie otrzymuje etykiet ani prawdziwych wartości usuniętych punktów;
- porównanie jest sparowane: ten sam fold i ta sama maska przed/po optymalizacji.

Uruchomienie:

```bash
.venv/bin/python -B tests/benchmark_missing_data.py --masks 5
```

## Wynik średni

| Metryka | Imputacja bazowa | Po optymalizacji |
|---|---:|---:|
| raw score | 0.953302 | 0.953302 |
| macro F1 | 0.963467 | 0.963467 |
| severity accuracy | 0.922807 | 0.922807 |
| średnie confidence | 0.784889 | 0.786814 |
| MAE uzupełnionych punktów | 1.252146 mV | 1.260177 mV |
| błędy z confidence ≥ 0.95 | 0 | 0 |

Łącznie sprawdzono 5000 sztucznie usuniętych wartości. Liczba zmian werdyktu
wyniosła `0`, podobnie jak liczba zmian rzeczywistych, nieusuniętych pomiarów.
Optymalizator uruchamiał się średnio dla 66.2 cylindrów na maskę.

## Zabezpieczenia

Optymalizacja może zmienić wyłącznie komórkę oznaczoną pierwotnie jako brak.
Zakres ruchu jest ograniczony przez IQR rzeczywistych wartości innych cylindrów
tego silnika. Kandydat jest odrzucany, jeśli zmieni etykietę lub severity
jakiegokolwiek cylindra albo zmniejszy confidence innego cylindra.

Agresywny wariant, który wybierał pełne kwantyle wyłącznie według maksymalnego
confidence, został odrzucony: podnosił pewność, ale obniżał średni raw score z
`0.953302` do `0.932660`. To potwierdza, że samo maksymalizowanie pewności bez
ograniczeń prowadzi do samopotwierdzających, mniej wiarygodnych predykcji.

`vote_confidence` oznacza zgodność modeli ensemble, a nie skalibrowane
prawdopodobieństwo poprawności.
