# Synchronizacja z Help Desk

## Przebieg `POST /api/v1/sync`

```mermaid
sequenceDiagram
    actor Caller as Klient REST lub nightly_sync.py
    participant S as API / SyncService
    participant DB as SQLite
    participant H as Help Desk API
    participant E as API embeddingów
    participant Q as Qdrant

    Caller->>S: POST /api/v1/sync
    S->>DB: Utwórz tabelę checkpoints, jeżeli brakuje
    opt Brak time_modified_after w żądaniu
        S->>DB: Odczytaj checkpoint dla tickets i updates
        DB-->>S: Dwa niezależne znaczniki
    end
    loop Kolejne strony zgłoszeń
        S->>H: Pobierz zgłoszenia z filtrami i zakresem czasu
        H-->>S: Rekordy i next_page
        S->>S: Walidacja, wybór ID, filtrowanie tekstu i podział na fragmenty
        opt Zapis i są fragmenty do indeksowania
            opt Fragmenty o treści semantycznej
                S->>E: Oblicz embeddingi dokumentów
                E-->>S: Wektory
            end
            S->>Q: Upsert punktów zgłoszeń
        end
        break Błąd strony, embeddingów lub upsertu
            S-->>Caller: HTTP 500 przed zapisem checkpointów
        end
    end
    opt Filtry status, category lub tag
        loop Strony zgłoszeń nadrzędnych bez ograniczenia czasowego
            S->>H: Pobierz zgłoszenia pasujące do filtrów
            H-->>S: Zgłoszenia i next_page
            break Błąd pobierania lub walidacji
                S-->>Caller: HTTP 500 przed zapisem checkpointów
            end
        end
        S->>S: Ustal dozwolone ticket_id dla aktualizacji
    end
    loop Kolejne strony aktualizacji dla partii ticket_id
        S->>H: Pobierz aktualizacje z zakresu czasu i z filtrami aktualizacji
        H-->>S: Rekordy i next_page
        S->>S: Walidacja, filtrowanie tekstu i podział na fragmenty
        opt Zapis i są fragmenty do indeksowania
            opt Fragmenty o treści semantycznej
                S->>E: Oblicz embeddingi dokumentów
                E-->>S: Wektory
            end
            S->>Q: Upsert punktów aktualizacji
        end
        break Błąd strony, embeddingów lub upsertu
            S-->>Caller: HTTP 500 przed zapisem checkpointów
        end
    end
    opt Obie fazy zakończone i dry_run wyłączony
        S->>DB: Zapisz dostępne checkpointy, każdy w osobnej transakcji
    end
    S-->>Caller: SyncResult z licznikami i znacznikami czasu
```

- `time_modified_after` podany w żądaniu zastępuje oba odczyty checkpointów. Bez filtrów zgłoszenia nadrzędnego aktualizacje mogą być ograniczone do `ticket_id` z żądania. Jawnie ustalone ID są dzielone na partie po najwyżej 200, a pusta lista dopasowań pomija pobieranie aktualizacji.
- Rekordy dopasowane do `THREAD_FILTER_PATTERNS` są pomijane. Fragmenty treści uznanej za semantycznie pustą trafiają do Qdrant z wektorem zerowym. Przy `dry_run` aplikacja nadal inicjalizuje tabelę i liczy przetworzone rekordy, a wywołania embeddingów, upsert i zapisy checkpointów są pomijane.
- Przekroczenie limitu czasu Help Desk lub embeddingów (30 s) kończy żądanie błędem. Dla `ResponseHandlingException` podczas upsertu repozytorium wykonuje do trzech prób z przerwami 1 s i 2 s. Błąd podczas przetwarzania stron kończy synchronizację przed zapisem checkpointów, a wcześniejsze upserty mogą pozostać w Qdrant. Kolejne uruchomienie może przetworzyć je ponownie, używając tych samych identyfikatorów punktów.
- `nightly_sync.py` domyślnie ustawia limit czasu klienta HTTP na 120 s i kończy z kodem `1` po błędzie HTTP, przekroczeniu limitu albo nieprawidłowej odpowiedzi. Limit po stronie klienta dotyczy oczekiwania na odpowiedź. Zapis punktów i checkpointów odbywa się w aplikacji.

Źródła w repozytorium — [endpoint](../app/api/v1/routes_sync.py), [algorytm synchronizacji i SQLite](../app/services/sync.py), [walidacja danych](../app/models/schemas.py), [klient Help Desk](../app/clients/helpdesk.py), [klient embeddingów](../app/clients/embeddings.py), [ponowienia upsertu](../app/clients/qdrant.py) i [klient nocny](../scripts/nightly_sync.py).
