# PIHER2 — diagnostyka akustyczna silnika

WebApp pozwala wybrać model 2 albo model 3 przed uruchomieniem analizy.
Domyślnie używa stabilnego `model2`, a `model3` pozostaje wariantem odpornym na
braki danych. Ten sam proces FastAPI serwuje stronę oraz endpointy, więc nie
trzeba uruchamiać osobnego serwera statycznego. Strona jest w miarę odporna na jakieś wygibaski. 

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

Backend automatycznie wykrywa artefakty obu modeli:

- `model/acoustic_model2.pkl` i `model/verdict_explainer.pkl` — domyślny model 2;
- `model/acoustic_model3.pkl` i `model/verdict_explainer3.pkl` — model 3 z
  rekonstruktorami brakujących odczytów.

## Konta i lista serwisowa

Diagnostyka oraz wybór modelu działają bez logowania. Rejestracja tworzy konto
przełożonego, który może następnie zakładać konta pracowników i przekazać im
login z hasłem startowym. Lista zespołu pokazuje imię i nazwisko, login oraz
liczbę zakończonych zadań. Przełożony może ustawić pracownikowi nowe hasło;
operacja unieważnia wszystkie wcześniejsze sesje tego pracownika. Przełożony
widzi swoje zadania oraz zadania wyłącznie swoich pracowników; pracownik widzi
i zmienia tylko własną listę.

W szczegółach cylindra przycisk `+` tworzy zadanie zawierające miniaturę widma,
numer silnika, numer cylindra, rozpoznaną usterkę i powagę. W osobnej zakładce
To-do można poprawić opis usterki, dopisać sposób naprawy, przypisać pracownika
oraz ustawić stan `Do zrobienia`, `W trakcie` albo `Skończone`. Zakończone
zadania pozostają w historii i można je filtrować.
Aktywny filtr jest wskazany kolorem, znacznikiem i tekstowym podsumowaniem.
Kliknięcie karty zadania przechodzi do odpowiadającego silnika i cylindra w
aktualnie wczytanym pliku diagnostycznym.

Konta, sesje i zadania są trwale zapisywane w lokalnej bazie SQLite
`WebApp/backend/piher2.sqlite3`, tworzonej przy pierwszym uruchomieniu. Inną
ścieżkę można wskazać zmienną `PIHER2_DATABASE_PATH`. Hasła są przechowywane
jako PBKDF2-SHA256 z indywidualną solą, a nie w postaci jawnej.

## Eksperymentalny model3

Odporna na braki architektura znajduje się w `model/model3.py`. Łączy
rekonstrukcje KNN i Ridge z klasyfikatorem pomijającym brakujące pasma oraz
wielokrotnym Group-CV ensemble. Zachowuje API zgodne z `model2`. W niezależnych
testach 220 masek (10% braków, maks. 3 w wierszu i 2 kolejne) najgorszy wynik
wyniósł `35.255` punktu. Można go wybrać w WebApp lub przez parametr endpointu
`/api/predict?model=model3`; bez parametru używany jest `model2`.

```bash
.venv/bin/python model/model3.py
.venv/bin/python -B tests/benchmark_model3_missing.py --masks 30
```

Szczegóły architektury i wyniki: `model/MODEL3.md`.

## Dane wejściowe

Interfejs przyjmuje plik CSV albo ZIP zawierający dokładnie jeden CSV, do 10 MB.
Wymagane kolumny to:

```text
engine_id, cylinder, n_cylinders, mV_0, ..., mV_20
```

W jednym żądaniu trzeba przekazać wszystkie cylindry każdego silnika. Kolumny
`label` oraz `severity` mogą występować w pliku walidacyjnym, ale nie są
wymagane do predykcji.

Brakujące pasma nie są interpolowane prostą pomiędzy sąsiednimi częstotliwościami.
Model uzupełnia je medianowym profilem pozostałych cylindrów tego samego silnika,
skorygowanym o lokalne przesunięcie danego cylindra. Zachowuje przy tym maskę
braków: wartość uzupełniona może posłużyć klasyfikatorowi do zachowania pełnego
wektora wejściowego, ale explainer nigdy nie zaznaczy jej jako przyczyny werdyktu.
Jeżeli dla danego pasma nie istnieje żaden rzeczywisty pomiar w całym silniku,
żądanie jest odrzucane zamiast tworzenia niewiarygodnej wartości.

Jeżeli cylinder ma uzupełnione pasma i confidence poniżej `0.75`, model wykonuje
ograniczone dostrojenie tych punktów. Sprawdza wartości bliskie imputacji,
wyznaczone z kwantyli pozostałych cylindrów, ale nie może zmienić zmierzonego
punktu, etykiety ani nasilenia żadnego cylindra. Przyjmuje tylko ruch zwiększający
confidence bez obniżenia confidence pozostałych cylindrów. Pola
`confidence_before_optimization`, `confidence_after_optimization` oraz
`optimization_adjusted_columns` umożliwiają audyt każdej takiej zmiany.

Odporność można odtworzyć poleceniem:

```bash
.venv/bin/python -B tests/benchmark_missing_data.py --masks 5
```

Benchmark używa Group 5-Fold po `engine_id` i w każdej próbie usuwa dokładnie
10% wartości `mV`, bez udostępniania modelowi prawdziwych usuniętych wartości.

Po analizie WebApp pokazuje etykietę, nasilenie, confidence z głosowania
ensemble, wyjaśnienie oraz podejrzany fragment widma. Przycisk eksportu zapisuje
`wynik_model2.csv` zawierający wyłącznie kolumny `engine_id`, `cylinder`, `label`
i `severity` (w tej kolejności).

## Biblioteka silników

Po przesłaniu danych aplikacja najpierw pokazuje pełną listę silników. Można ją
sortować według zdrowia, najpoważniejszej usterki, ryzyka albo niepewności.
Niepewność silnika jest liczona z najmniejszego confidence jego cylindra
(`1 - min(vote_confidence)`), dzięki czemu pojedynczy niepewny werdykt nie jest
ukrywany przez średnią. Kliknięcie silnika rozwija jego parametry i cylindry.

Wykres cylindra porównuje pomiar ze średnim zdrowym widmem. W wysuwanej tabeli
„typ awarii × poważność” można dodatkowo włączyć jeden profil usterki; ponowne
kliknięcie wyłącza nakładkę. Wiersze `ok` i `unknown` nie mają poziomu
poważności. Referencje zostały policzone z `tests/val.csv` po liniowej
interpolacji braków w każdym wierszu i są zapisane na stałe w
`WebApp/reference_spectra.json`. Backend jedynie wczytuje gotowy plik podczas
startu procesu — nie przelicza zbioru walidacyjnego przy predykcji.

## API

`POST /api/predict?model=model2` przyjmuje `multipart/form-data` z polem `file`.
Parametr `model` może mieć wartość `model2` (domyślna) albo `model3`. Odpowiedź
ma postać JSON:

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
  "selected_model": "model2",
  "model_votes": 51,
  "input_rows": 12
}
```

Opcjonalne `?include_bands=true` dodaje szczegółową tabelę wyników dla każdego
pasma. Historyczne adresy `/predict` oraz `/extract-csv` pozostają aliasami.

Endpointy kont i zadań używają nagłówka `Authorization: Bearer <token>`:

- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`;
- `GET /api/auth/me`;
- `GET/POST /api/employees` oraz
  `PATCH /api/employees/{id}/password` dla przełożonego;
- `GET/POST /api/todos` oraz `PATCH/DELETE /api/todos/{id}`.
