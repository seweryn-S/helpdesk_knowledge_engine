# Wyszukiwanie i odtwarzanie wątków

## Wyszukiwanie semantyczne

```mermaid
sequenceDiagram
    actor C as Klient REST / LLM
    participant S as API / QueryService / ThreadQueryService
    participant E as API embeddingów
    participant Q as Qdrant

    C->>S: POST /api/v1/query lub /api/v1/query/threads
    break Nieprawidłowe żądanie, np. za duży limit
        S-->>C: HTTP 422
    end
    S->>E: POST /embeddings z tekstem zapytania
    break Błąd lub przekroczenie limitu czasu embeddingów
        E-->>S: Błąd wywołania
        S-->>C: HTTP 500
    end
    E-->>S: Wektor zapytania
    S->>Q: Wyszukaj wektor w kolekcji zgłoszeń, następnie aktualizacji
    Note over S,Q: hidden=false, semantic_empty=false oraz filtry żądania
    Q-->>S: Punkty i wyniki podobieństwa
    S->>S: Połącz i posortuj trafienia malejąco po wyniku
    alt /query
        S->>S: Wybierz do limit fragmentów i zbuduj linki
    else /query/threads
        S->>S: Wybierz do limit unikalnych ticket_id według najlepszych trafień
        opt Znaleziono ticket_id
            S->>Q: Pobierz fragmenty zgłoszeń i widocznych aktualizacji przez scroll
            Q-->>S: Payloady znalezionych zgłoszeń i aktualizacji
            S->>S: Odtwórz wątki i pomiń zgłoszenia bez fragmentów treści
        end
    end
    S-->>C: HTTP 200, fragmenty lub wątki
```

Wyszukiwanie wątków pobiera do `min(3 × limit, 100)` kandydatów z każdej kolekcji. Przy odtwarzaniu wybranych wątków widoczne aktualizacje mogą zawierać fragmenty z `semantic_empty=true`. Brak trafień daje pustą listę, a błędy zapytań do Qdrant dają HTTP 500. `debug=true` zachowuje pola techniczne przewidziane przez model odpowiedzi danego endpointu.

Źródła w repozytorium — [endpointy i formatowanie odpowiedzi](../app/api/v1/routes_query.py), [walidacja limitu i filtry wejściowe](../app/models/schemas.py), [wyszukiwanie fragmentów](../app/services/query.py), [wyszukiwanie wątków](../app/services/thread_query.py), [klient embeddingów](../app/clients/embeddings.py) i [repozytorium Qdrant](../app/clients/qdrant.py).

## Odczyt zgłoszenia po identyfikatorze

```mermaid
sequenceDiagram
    actor C as Klient REST
    participant S as API / ThreadService
    participant Q as Qdrant

    C->>S: GET /api/v1/tickets/{ticket_id}/details lub /thread
    S->>Q: Scroll kolekcji zgłoszeń po ticket_id
    Q-->>S: Fragmenty zgłoszenia
    break Brak zgłoszenia lub fragmentów treści
        S-->>C: HTTP 404
    end
    S->>S: Posortuj i połącz fragmenty zgłoszenia, zbuduj link
    alt /details
        S-->>C: Szczegóły zgłoszenia i metadane fragmentów
    else /thread
        S->>Q: Scroll kolekcji aktualizacji po ticket_id
        Q-->>S: Fragmenty wszystkich aktualizacji, także ukrytych
        S->>S: Zgrupuj po update_id i złóż wątek chronologicznie
        alt concise=true
            S-->>C: Spłaszczone metadane i thread_text
        else Odpowiedź pełna
            S-->>C: Zgłoszenie, lista aktualizacji i thread_text
        end
    end
```

Treść jest składana z `detail_chunk` w kolejności `chunk_start`, a następnie `chunk_no`. Kody odpowiedzi dla poprawnych ścieżek w diagramie to HTTP 200. Błędy połączenia z Qdrant dają HTTP 500.

Źródła w repozytorium — [endpointy odczytu i mapowanie błędów](../app/api/v1/routes_query.py), [rekonstrukcja tekstu](../app/services/thread.py) i [paginacja odczytu Qdrant](../app/clients/qdrant.py).
