# Architektura hd_ke

## Komponenty i zależności

```mermaid
flowchart LR
    Client["Klient REST / LLM"] --> API
    Nightly["scripts/nightly_sync.py"] -->|POST /api/v1/sync| API
    Operator["Operator"] -->|Eksport i import przez REST| API
    Operator --> Reembed["scripts/reembed.py"]

    subgraph App["hd_ke · FastAPI"]
        API["Routery /api/v1"]
        Init["Cykl życia aplikacji i OpenAPI"]
        Sync["SyncService"]
        Search["QueryService i ThreadQueryService"]
        Read["ThreadService"]
        Backup["BackupService"]
        HC["HelpDeskClient"]
        EC["EmbeddingClient"]
        QR["QdrantRepository"]

        API --> Sync & Search & Read & Backup
        Init --> HC & EC & QR
        Sync --> HC & EC & QR
        Search --> EC & QR
        Read --> QR
        Backup --> QR
    end

    HC --> HD["Help Desk API"]
    EC --> Emb["API embeddingów zgodne z OpenAI"]
    QR --> QD[("Qdrant · kolekcje zgłoszeń i aktualizacji")]
    Sync --> State[("SQLite · checkpointy synchronizacji")]
    Backup --> State
    Backup --> TGZ["Archiwum .tgz"]
    Reembed --> Emb & QD
    Reembed --> Progress[("SQLite · checkpointy migracji")]
```

Źródła w repozytorium — [montowanie routerów i tworzenie klientów](../app/main.py), [zależności FastAPI](../app/api/deps.py), [serwisy](../app/services/), [skrypt synchronizacji](../scripts/nightly_sync.py), [skrypt migracji](../scripts/reembed.py), [kontener](../Dockerfile) i [jednostka systemd](../deploy/hd_ke.service).

## Cykl życia aplikacji

```mermaid
stateDiagram-v2
    state "Odczyt konfiguracji i tworzenie klientów" as Configure
    state "Ustalenie wymiaru wektora i sprawdzenie kolekcji" as Prepare
    state "Pierwsze odświeżenie kategorii i budowa OpenAPI" as Refresh
    state "Obsługa API bez cyklicznego odświeżania" as Static
    state "Obsługa API z cyklicznym odświeżaniem" as Running
    state "Zamykanie klientów HTTP" as Shutdown
    state "Start przerwany" as Failed

    [*] --> Configure
    Configure --> Prepare
    Configure --> Failed: błąd konfiguracji lub inicjalizacji
    Prepare --> Failed: błąd sondy embeddingów lub Qdrant
    Prepare --> Refresh: kolekcje gotowe
    Refresh --> Static: interwał mniejszy lub równy zero
    Refresh --> Running: dodatni interwał
    Running --> Running: próba odświeżenia i przerwa co najmniej 60 s
    Running --> Shutdown: zatrzymanie i anulowanie zadania odświeżania
    Static --> Shutdown: zatrzymanie
    Failed --> [*]
    Shutdown --> [*]
```

Przy starcie aplikacja używa wymiaru z `EMBEDDING_DIM` albo odczytuje go z próbnego wywołania `/embeddings`. Qdrant tworzy brakujące kolekcje i sprawdza wymiar istniejących. Gdy Help Desk zwróci błąd, przekroczy limit czasu lub zwróci pustą listę kategorii, aplikacja zachowuje dotychczasowy słownik. Przed pierwszą udaną próbą korzysta z kategorii domyślnych. Limit czasu klienta Help Desk i klienta embeddingów wynosi 30 s na wywołanie.

Źródła w repozytorium — [cykl życia i pamięć podręczna OpenAPI](../app/main.py), [konfiguracja](../app/core/config.py), [klient Help Desk](../app/clients/helpdesk.py), [klient embeddingów](../app/clients/embeddings.py) i [inicjalizacja kolekcji](../app/clients/qdrant.py).
