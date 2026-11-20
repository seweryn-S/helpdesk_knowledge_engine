# Kopie zapasowe i migracja wektorów

## Eksport i import kopii zapasowej

```mermaid
sequenceDiagram
    actor O as Operator
    participant B as API / BackupService
    participant Q as Qdrant
    participant D as Plik SQLite synchronizacji

    alt Eksport kopii
        O->>B: GET /api/v1/admin/backup/export
        loop Kolekcja zgłoszeń, następnie kolekcja aktualizacji
            B->>Q: Utwórz i pobierz snapshot
            Q-->>B: Plik i metadane snapshota
        end
        B->>D: Skopiuj bazę, jeśli istnieje
        B->>B: Zbuduj archiwum z Qdrant, SQLite i metadata.json
        Note over B,Q: Próba usunięcia utworzonych snapshotów także po błędzie
        B-->>O: HTTP 200 z archiwum lub HTTP 500 przy błędzie
    else Import kopii
        O->>B: POST /api/v1/admin/backup/import z plikiem
        B->>B: Rozpakuj archiwum i sprawdź metadane kolekcji
        loop Kolekcja zgłoszeń, następnie kolekcja aktualizacji
            B->>B: Sprawdź ścieżkę i plik snapshota
            B->>Q: Wgraj i odtwórz snapshot, czekaj na wynik
            Q-->>B: Wynik odtwarzania kolekcji
        end
        B->>D: Sprawdź i podmień plik SQLite z archiwum
        B->>B: Usuń katalog roboczy również po błędzie
        B-->>O: HTTP 200, HTTP 400 przy ValueError lub HTTP 500
    end
```

Gdy plik SQLite jeszcze nie istnieje, eksport tworzy w archiwum pusty plik. Po pomyślnym wysłaniu archiwum API usuwa katalog roboczy eksportu w zadaniu w tle. Import przerywa się przy pierwszym błędzie. Jeśli odtwarzanie kolejnej kolekcji lub podmiana SQLite się nie powiedzie, wcześniej odtworzona kolekcja pozostaje w Qdrant.

Źródła w repozytorium — [endpointy i odpowiedzi HTTP](../app/api/v1/routes_backup.py), [obsługa archiwum, snapshotów i SQLite](../app/services/backup.py).

## Migracja na nowy model embeddingów

```mermaid
flowchart TD
    Start(["Uruchomienie scripts/reembed.py"]) --> Probe["Odczytaj wymiar z próbnego wywołania /embeddings"]
    Probe -->|Błąd lub limit czasu| Stop(["Zakończ z kodem 1"])
    Probe -->|Wymiar odczytany| Select["Wybierz kolekcję i policz punkty źródłowe"]
    Select -->|Kolekcja pusta| More
    Select -->|Są punkty| Offset["Przy --resume odczytaj zapisany offset z SQLite"]
    Offset --> Mode{"Tryb zapisu?"}
    Mode -->|Tak| Target["Sprawdź lub utwórz kolekcję docelową"]
    Mode -->|dry-run| Read["Czytaj strony payloadów Qdrant"]
    Target --> Read
    Read --> Process["Odtwórz teksty, obsłuż pełne partie i wektory zerowe"]
    Process -->|Kolejna strona| Read
    Process -->|Brak kolejnej strony| Flush["Obsłuż końcową niepełną partię, jeśli istnieje"]
    Flush --> More{"Pozostała kolejna kolekcja?"}
    More -->|Kolejna kolekcja| Select
    More -->|Koniec| Summary(["Podsumuj postęp i błędy"])
    Process -.->|Ctrl+C podczas przetwarzania| Interrupt["Zakończ przetwarzanie tej kolekcji i zachowaj zapisany checkpoint"]
    Interrupt --> More
```

- W trybie `--dry-run` skrypt odczytuje payloady i liczy rekordy. W trybie zapisu robi upsert partii z nowymi embeddingami, a punkty z wektorem zerowym zapisuje po przetworzeniu każdej strony.
- Klient embeddingów ma limit czasu 120 s. Wykonuje maksymalnie trzy próby embeddingu partii przy HTTP 429 lub błędzie transportu, w tym przy przekroczeniu limitu czasu. Upsert partii z embeddingami również ma maksymalnie trzy próby. Po błędzie takiej partii skrypt zlicza błędne punkty i kontynuuje. Błędy w podsumowaniu dają kod wyjścia `1`.
- Checkpoint jest zapisywany po udanym upsercie pełnej partii z embeddingami. `--resume` używa ostatniego zapisanego identyfikatora punktu jako offsetu. Zapis checkpointu po późniejszej udanej partii może przesunąć offset poza wcześniejszą partię zakończoną błędem.
- Błąd odczytu strony z Qdrant, zapisu punktów z wektorem zerowym lub zapisu checkpointu przerywa skrypt.

Po migracji przełączenie serwisu na kolekcje docelowe wymaga zmiany `QDRANT_TICKET_COLLECTION` i `QDRANT_UPDATE_COLLECTION` oraz restartu usługi.

Źródła w repozytorium — [skrypt migracji i wznowienie](../scripts/reembed.py), [ustawienia aplikacji](../app/core/config.py) i [opis przełączenia kolekcji](../README.md#migracja-modelu-embeddingowego-re-embed).
