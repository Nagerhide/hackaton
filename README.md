# Engin / prototyp diagnostyki silnika

Wstępny, interaktywny prototyp aplikacji dla inżynierów i mechaników Aesteel. Dashboard pokazuje stan silnika, widmo akustyczne wybranego cylindra, werdykt modelu, nasilenie usterki i mapę wszystkich cylindrów.

## Uruchomienie

Najprościej otworzyć `index.html` w przeglądarce. Dla lokalnego serwera HTTP można użyć:

```bash
python3 -m http.server 8000
```

Następnie wejdź na `http://localhost:8000`.

## Zakres prototypu

- przykładowy silnik 16-cylindrowy i przełącznik między silnikami,
- interaktywna mapa cylindrów z wyborem cylindra problemowego,
- wykres widma akustycznego z zaznaczonym anomalnym pasmem,
- diagnoza „pompa wtryskowa”, poziom nasilenia i wyjaśnienie werdyktu,
- demonstracyjne akcje eksportu raportu, porównania i zlecenia serwisowego.

Dane są obecnie makietą UI. Kolejnym krokiem będzie podpięcie `train.csv`, `val.csv` i `test.csv` oraz modelu predykcyjnego.