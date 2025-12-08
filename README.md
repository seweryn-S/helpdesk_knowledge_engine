# hd_ke – narzędzie RAG dla Help Desk

## Cel
`hd_ke` to zewnętrzne narzędzie RAG, które indeksuje zgłoszenia i aktualizacje Help Desk do Qdrant i udostępnia REST API dla LLM w celu budowania kontekstu (passages) z cytowaniami.

## Architektura
- **ETL/sync**: cykliczne pobieranie ticketów i updates z Help Desk API, inkrementalnie po `time_modified_after`, zapis checkpointów w SQLite (`/var/lib/hd_ke/state.db`).
- **Chunking**: nowe, zdań-aware dzielenie na fragmenty (domyślnie 512 znaków). Każdy chunk zawiera offsety (`chunk_start`, `chunk_end`), indeksy zdań i hash całego `details`, dzięki czemu można odtworzyć całość korzystając wyłącznie z payloadu Qdrant.
- **Embeddings**: klient OpenAI‑compatible (endpoint konfigurowalny); wymiar wektora wykrywany przez wywołanie `/embeddings` przy starcie lub brany z config.
- **Indeks**: dwie kolekcje Qdrant – `hd_ticket_chunks` (zgłoszenia z pełnymi metadanymi payloadu) oraz `hd_update_chunks` (aktualizacje z odchudzonym payloadem i tylko referencją do ticketu). Obie kolekcje przechowują wektor `embedding`, a payload zawiera tylko `url_suffix` (pełny URL składany jest w API).
- **API**: FastAPI (`/api/v1/query`, `/api/v1/sync`, `/api/v1/info`, `/api/v1/tickets/{id}/details`, `/api/v1/tickets/{id}/thread`, `/health`).
- **OpenAPI**: spec generowany dynamicznie – dostępne kategorie ticketów są cyklicznie pobierane z Help Desk i publikowane jako `enum` w polu `category` (z fallbackiem na domyślną listę, gdy słownik jest chwilowo niedostępny).

## Konfiguracja (`/etc/default/hd_ke` → env)
Przykład:
```
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_TICKET_COLLECTION=hd_ticket_chunks
QDRANT_UPDATE_COLLECTION=hd_update_chunks
HELPDESK_API_BASE_URL=https://edu.p.lodz.pl/webservice/restful/server.php
HELPDESK_API_KEY=CHANGEME
HELPDESK_TICKET_URL_PREFIX=https://edu.p.lodz.pl/blocks/helpdesk/view.php?
EMBEDDING_API_BASE_URL=http://172.22.1.5:8000/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL_NAME=your-model
EMBEDDING_CONTEXT_LENGTH=512
# Opcjonalnie, jeśli znamy: EMBEDDING_DIM=768
EMBEDDING_PROMPT_PREFIX=
EMBEDDING_DOCUMENT_PREFIX=
SYNC_CHECKPOINT_PATH=/var/lib/hd_ke/state.db
HD_KE_DATA_DIR=/srv/hd_ke
LOG_LEVEL=INFO  # ustaw na DEBUG, aby logować szczegóły zapytań do Help Desk API
THREAD_FILTER_PATTERNS=["^\\s*\\.\\s*$","^\\s*\\r?\\n\\s*$","^\\s*\\.\\s*\\r?\\n\\s*$","^\\s*\\r?\\n\\s*\\r?\\n\\s*$"]
HELPDESK_CATEGORIES_REFRESH_HOURS=24
HELPDESK_DEFAULT_CATEGORIES=["wikamp","network","software","mail","ztn","skryba","ekstazjusz","ezd_puw","parking","mzp","kir","teamwww","teta","idcards","violation","usos"]
```

`THREAD_FILTER_PATTERNS` przyjmuje listę wyrażeń regularnych (JSON, przecinki lub nowe linie), które powodują całkowite pomijanie zgłoszeń i aktualizacji już na etapie synchronizacji. W domyślnym fallbacku (gdy zmienna nie jest ustawiona) używamy zestawu `\.`, `^\r?\n$`, `^\.\r?\n$` oraz `^\r?\n\r?\n$`. Przykładowa konfiguracja w `deploy/hd_ke.default` prezentuje bardziej tolerancyjne wzorce (`^\s*\.\s*$`, `^\s*\r?\n\s*$`, `^\s*\.\s*\r?\n\s*$`, `^\s*\r?\n\s*\r?\n\s*$`), dzięki czemu filtry działają również na wpisach zawierających dodatkowe spacje przed/po kropce lub pustych liniach. W praktyce warto dopasować wzorce do realnych danych (np. `^\s*$`), aby nie usuwać nadmiarowo treści.

`EMBEDDING_PROMPT_PREFIX` jest dodawany przed tekstem zapytań (np. `query: <twoje zapytanie>`), a `EMBEDDING_DOCUMENT_PREFIX` przed chunkami dokumentów podczas synchronizacji (np. `passage: <fragment>`). Domyślnie oba są puste – ustaw odpowiednie wartości tylko wtedy, gdy model embeddingowy wymaga konkretnych prefixów.

`HELPDESK_CATEGORIES_REFRESH_HOURS` steruje jak często (w godzinach) serwis odświeża słownik kategorii Help Desk wykorzystywany do publikowania listy `enum` w OpenAPI (minimum 1 minuta niezależnie od konfiguracji). `HELPDESK_DEFAULT_CATEGORIES` to lista wartości używana jako bezpieczny fallback, gdy odczyt słownika z Help Desk się nie powiedzie.

### Filtr niestotnych treści
- Filtr działa jednakowo dla zgłoszeń i aktualizacji – rekord pasujący do któregokolwiek regexu nie jest chunkowany, embedowany ani upsertowany do Qdrant.
- Wzorce są kompilowane przy starcie serwisu; w przypadku błędnego regexu aplikacja przerwie start z czytelnym komunikatem.
- W logach (`INFO`) pojawia się komunikat ze wskazaniem ID ticketu/aktualizacji pominiętej przez filtr.

## Endpointy API
- `GET /api/v1/health` – healthcheck.
- `GET /api/v1/info` – wersja, konfiguracja bazowa, model embeddingowy.
- `POST /api/v1/sync` – uruchamia synchronizację (opcjonalne filtry, `dry_run`).
- `POST /api/v1/query` – wyszukiwanie semantyczne z filtrami i cytowaniami; domyślnie ukrywa pola `chunk_no`, `chunk_total`, `chunk_start`, `chunk_end`, `sentence_start`, `sentence_end`, `details_hash` oraz `url_suffix`, a dopiero dodanie `?debug=true` do adresu powoduje zwrócenie pełnego payloadu chunków.
- `POST /api/v1/query/threads` – wyszukiwanie semantyczne i zwrócenie pełnych wątków (ticket + aktualizacje) z prefiksem `user_role` w treści oraz metadanymi agregowanymi z ticketu; podobnie jak `/query`, domyślnie ukrywa pola chunkowe i `url_suffix`, a `?debug=true` przywraca pełne payloady.
- `GET /api/v1/tickets/{ticket_id}/details` – rekonstrukcja pełnego `details` ticketu na podstawie chunków z Qdrant.
- `GET /api/v1/tickets/{ticket_id}/thread` – pełen wątek: zgłoszenie + chronologiczne aktualizacje (również składane tylko na podstawie Qdrant); `?concise=true` zwraca scalone metadane wątku i jeden `thread_text` zamiast listy chunków.

## Rekonstrukcja treści tylko z Qdrant
- Każdy chunk przechowuje jeden fragment w polu `detail_chunk` oraz metadane pozycyjne; nie duplikujemy tekstu w polach `text`/`full_text`.
- `details_hash` umożliwia walidację, że wszystkie fragmenty należą do tej samej wersji zgłoszenia/aktualizacji.
- API `/tickets/{ticket_id}/details` oraz `/tickets/{ticket_id}/thread` używa samego Qdrant (`scroll` + filtry po `ticket_id` i `source`) by budować całe zgłoszenia/wątki bez dodatkowych baz.
- Pełne linki do systemu Help Desk powstają dopiero w API: trzymamy tylko `url_suffix`, a prefix (`HELPDESK_TICKET_URL_PREFIX`) dokładamy dopiero przy serwowaniu wyników.

### Metadane chunków
- `chunk_no` – numer fragmentu w obrębie jednego wpisu (liczone od 0).
- `chunk_total` – całkowita liczba fragmentów dla danego wpisu.
- `chunk_start` / `chunk_end` – pozycje znakowe (offsety) w oryginalnym polu `details`.
- `sentence_start` / `sentence_end` – indeksy pierwszego i ostatniego zdania w chunku.
- `logical_id` – oryginalny deterministyczny identyfikator chunku zapisany w payloadzie (po wygenerowaniu UUID trafia do pola `logical_id`); pomaga diagnozować i mapować punkty przy ponownych synchronizacjach.

## Uruchomienie lokalne
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -e .`
3. `uvicorn app.main:app --host 0.0.0.0 --port 8000`

### Devcontainer (VSCodium / VS Code)
- Folder `.devcontainer/` zawiera Dockerfile oparty na obrazie runtime (Python 3.11 slim + `build-essential`) oraz `devcontainer.json`.
- Po wybraniu w VSCodium komendy `Dev Containers: Reopen in Container` workspace montuje się w `/workspace`, a `postCreateCommand` automatycznie wykonuje `pip install -e .[dev]`, dzięki czemu `python3 -m pytest` działa od razu.
- Do kontenera podpinany jest plik `deploy/hd_ke.default` jako źródło zmiennych środowiskowych; ścieżka używa `${localWorkspaceFolder}` (host), więc działa poprawnie po mountcie. W razie potrzeby możesz ją zmienić w `devcontainer.json`.
- Folder projektu w kontenerze to `/workspaces/<nazwa repo>`; `.devcontainer/devcontainer.json` używa `${localWorkspaceFolderBasename}` aby dopasować nazwę.
- Dodany jest bind-mount `~/.codex` -> `/root/.codex`, wyliczany jako `${localWorkspaceFolder}/../.codex` (katalog domowy sąsiadujący z repo), aby rozszerzenie Codex miało dostęp do swojego katalogu konfiguracyjnego.

## Docker
Budowa:
```
docker build -t hd_ke:latest .
```
Uruchomienie (zakłada katalog danych na hoście):
```
docker run --rm -p 8000:8000 \
  --env-file /etc/default/hd_ke \
  -v /srv/hd_ke:/var/lib/hd_ke \
  hd_ke:latest
```

## Plik default i systemd
- Przykład pliku `/etc/default/hd_ke` w `deploy/hd_ke.default`.
- Jednostka systemd w `deploy/hd_ke.service` (używa zmiennej `HD_KE_DATA_DIR` do binda `/var/lib/hd_ke`).

## systemd (kontener)
Jednostka `hd_ke.service` (przykład):
```
[Unit]
Description=hd_ke RAG service
After=network.target docker.service
Requires=docker.service

[Service]
EnvironmentFile=/etc/default/hd_ke
Restart=always
ExecStartPre=/usr/bin/docker pull hd_ke:latest
ExecStart=/usr/bin/docker run --rm \
  --name hd_ke \
  --env-file /etc/default/hd_ke \
  -p 8000:8000 \
  -v ${HD_KE_DATA_DIR}:/var/lib/hd_ke \
  hd_ke:latest
ExecStop=/usr/bin/docker stop hd_ke

[Install]
WantedBy=multi-user.target
```

## Synchronizacja nocna
- Skrypt `scripts/nightly_sync.py` wywołuje `POST /api/v1/sync`, dzięki czemu w całości korzysta z istniejących filtrów (`--time-modified-after`, `--status`, `--category`, `--tag`, `--ticket-id`, `--update-type`, `--new-ticket-status`, `--dry-run`, `--page-limit`).
- Logi trafiają na STDOUT oraz domyślnie do `/var/log/hd_ke/nightly_sync.log` (rotacja 10 MB × 5). Ścieżkę można zmienić parametrem `--log-file` lub zmienną `HD_KE_SYNC_LOG_FILE`. Poziom logowania kontroluje `--log-level` / `HD_KE_SYNC_LOG_LEVEL`.
- Dedykowane środowisko wirtualne dla skryptu tworzysz poleceniem `scripts/setup_nightly_sync_env.sh` (powstaje `scripts/.venv_nightly` z zależnościami z `scripts/requirements.txt`); zmienne `PYTHON_BIN` / `VENV_PATH` pozwalają wskazać inny interpreter lub docelową ścieżkę.
- Domyślna wartość `--api-base-url` pochodzi ze zmiennej `HD_KE_API_BASE_URL` (fallback to `http://127.0.0.1:8000`), ale zawsze możesz jawnie wskazać adres w CLI.
- Skrypt kończy się kodem ≠0 w razie błędów HTTP/JSON, dzięki czemu cron/systemd wykryją niepowodzenie. Komunikaty zawierają parametry filtra oraz rezultat (`tickets_fetched`, `updates_fetched`, itp.).

Przykład lokalnego uruchomienia:
```
python scripts/nightly_sync.py \
  --api-base-url http://127.0.0.1:8000 \
  --log-file /var/log/hd_ke/nightly_sync.log \
  --status Open --status Pending \
  --tag "LLM-ready"
```

Przykładowy zestaw systemd service + timer odpalany codziennie o 02:30:
```
# /etc/systemd/system/hd_ke-nightly-sync.service
[Unit]
Description=hd_ke nightly sync
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/srv/hd_ke
ExecStart=/usr/bin/python3 scripts/nightly_sync.py --api-base-url http://127.0.0.1:8000
User=hd_ke
Group=hd_ke

# /etc/systemd/system/hd_ke-nightly-sync.timer
[Unit]
Description=Run hd_ke nightly sync at 02:30

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
```
Po utworzeniu jednostek wykonaj `systemctl daemon-reload && systemctl enable --now hd_ke-nightly-sync.timer`.

## Changelog
- 0.11.0 – `/tickets/{ticket_id}/thread` obsługuje `concise=true`, scalając metadane wątku i zwracając pojedynczy `thread_text` (jak `/query/threads`), co skraca odpowiedź dla LLM.
- 0.10.1 – naprawa `scripts/nightly_sync.py`: usunięty import nieistniejącego `SyncFilters`, filtry przekazywane jako płaskie pola `SyncRequest` zgodnie z kontraktem API.
- 0.10.0 – płaskie pola filtrów w `/query` i `/query/threads` (bez zagnieżdżonego `filters`), ukryty parametr `?debug=true`, dynamiczne OpenAPI z aktualnym wykazem kategorii (w tym fallback i cykliczny refresh sterowany `HELPDESK_CATEGORIES_REFRESH_HOURS` / `HELPDESK_DEFAULT_CATEGORIES`), uproszczony kontrakt dla LLM.
- 0.9.0 – moduł filtrujący semantycznie puste treści aktualizacji/ticketów (`TextContentFilter`), oznaczanie takich chunków flagą `semantic_empty` i użycie wektorów zerowych zamiast embeddingów; wyniki zapytań wektorowych domyślnie je ukrywają (`hide_semantic_empty` w filtrach), ale nadal są widoczne przy odtwarzaniu pełnych wątków.
- 0.8.2 – domyślny `EMBEDDING_DOCUMENT_PREFIX` to teraz pusty string, aby nie modyfikować treści chunków, jeśli prefiks nie jest wymagany.
- 0.8.1 – dodana zmienna `EMBEDDING_DOCUMENT_PREFIX` i wsparcie dla osobnego prefiksu chunków dokumentów podczas embeddingu.
- 0.8.0 – endpointy `/query` i `/query/threads` domyślnie ukrywają pola chunkowe (`chunk_*`, `sentence_*`, `details_hash`, `url_suffix`), a nowy parametr `debug` pozwala na zwrócenie pełnego payloadu w odpowiedzi.
- 0.7.1 – rozbudowane rekomendowane `THREAD_FILTER_PATTERNS` (konfiguracja/env) o wersje tolerujące spacje dla filtrów `.` / `\r\n`, aby lepiej usuwać puste wpisy przed chunkowaniem.
- 0.7.0 – `scripts/setup_nightly_sync_env.sh` + `scripts/requirements.txt` do szybkiego zbudowania odseparowanego virtualenv dla `nightly_sync.py`.
- 0.6.0 – skrypt `scripts/nightly_sync.py` do nocnej synchronizacji z logowaniem, dokumentacja konfiguracji timerów/systemd.
- 0.5.0 – nowy endpoint `/query/threads`: zwraca pełne wątki (ticket + aktualizacje) sklejone z chunków z prefiksami `user_role` w treści i metadanymi z ticketu (status, tagi, URL, time_created, time_modified z ostatniego wpisu).
- 0.4.6 – poprawka parsowania `THREAD_FILTER_PATTERNS`: źródło env nie wymusza JSON, walidator obsługuje puste, CSV i JSON.
- 0.4.5 – devcontainer: mount `~/.codex` liczony względem folderu repo (`${localWorkspaceFolder}/../.codex`), aby był zawsze widoczny w kontenerze.
- 0.4.4 – devcontainer: poprawka mountu `~/.codex` (użycie `${localEnv:HOME}` do wskazania katalogu domowego na hoście).
- 0.4.3 – devcontainer: workspace w `/workspaces/<repo>`, mount `~/.codex` do `/root/.codex` dla rozszerzenia Codex.
- 0.4.2 – poprawka devcontainera: `--env-file` wskazuje na plik na hoście (`${localWorkspaceFolder}/deploy/hd_ke.default`), co rozwiązuje błąd „no such file” przy starcie kontenera.
- 0.4.1 – dodany devcontainer dla VSCodium/VS Code (instalacja `[dev]` i gotowe środowisko do uruchamiania `pytest`).
- 0.4.0 – konfigurowalne filtry regex dla `details` (pomijanie całych zgłoszeń/aktualizacji już w trakcie sync), domyślny filtr `\.` w `/etc/default/hd_ke`, logowanie pomijanych rekordów oraz walidacja wzorców przy starcie.
- 0.3.1 – komentarze do pól (PL) w kodzie, dokumentacja metadanych chunków, usunięcie przechowywania pełnego URL w payloadzie (budowanie URL następuje w API z `url_suffix`). **Wymagany re-sync**, jeśli potrzebujesz pełnych URL w wynikach zapytań.
- 0.3.0 – rozdzielenie indeksu na dwie kolekcje (`hd_ticket_chunks`, `hd_update_chunks`), uproszczone payloady (tylko `detail_chunk` zamiast `text`/`full_text`/`details_full`) oraz nowe zmienne `QDRANT_TICKET_COLLECTION` i `QDRANT_UPDATE_COLLECTION`. **Wymagany pełny re-sync lub wyczyszczenie obu kolekcji** ze względu na nowe ID/payloady.
- 0.2.0 – chunking po pełnych zdaniach + metadane (offsety, hash) w payloadzie, nowe endpointy `/tickets/{id}/details` oraz `/tickets/{id}/thread`, możliwość odtworzenia całego zgłoszenia i wątku wyłącznie z Qdrant. **Wymagany pełny re-sync lub wyczyszczenie kolekcji** ze względu na zmianę struktury chunków/ID.
- 0.1.14 – naprawa błędu w `QdrantRepository.search`: zwracana jest lista punktów (`response.points`) zamiast całego obiektu odpowiedzi (`QueryResponse`), co eliminuje błąd `tuple` przy iteracji w `QueryService`.
- 0.1.13 – dostosowanie integracji z Qdrant do dokumentacji `qdrant-client 1.16.1`: użycie `qdrant_client.models` oraz high-level `client.query_points(...)` zamiast `search`.
- 0.1.12 – przejście na high-level API `QdrantClient.search(...)` i podbicie wymagania do `qdrant-client>=1.16.1,<2.0.0`.
- 0.1.11 – wyszukiwanie w Qdrant oparte bezpośrednio na `openapi_client.points_api.search_points(...)` z `SearchRequest`, niezależne od tego, czy `QdrantClient` udostępnia metodę `search`.
- 0.1.10 – uproszczone wyszukiwanie w Qdrant: `QdrantRepository.search` używa bezpośrednio `QdrantClient.search(...)` zgodnie z dokumentacją `qdrant-client>=1.8.0`.
- 0.1.9 – poprawione wyszukiwanie w Qdrant: użycie `QdrantClient.search` jeśli dostępny, w przeciwnym razie bezpośrednie wywołanie `remote.search` na `_client`, z komunikatem o konieczności aktualizacji w razie braku obu metod.
- 0.1.8 – obsługa obu wariantów klienta Qdrant (`search` oraz `search_points`) w `QdrantRepository.search`, aby zapytania działały niezależnie od wersji biblioteki.
- 0.1.7 – zmiana formatu ID punktów w Qdrant na deterministyczne UUID (zamiast stringów `ticket-...` / `update-...`), oryginalne ID przeniesione do payload jako `logical_id`.
- 0.1.6 – naprawa błędu `NameError: qmodels is not defined` w `SyncService` (brakujący import modeli Qdrant).
- 0.1.5 – nagłówek `Accept: application/json` dodany do wszystkich wywołań Help Desk w `HelpDeskClient`, aby wymusić odpowiedź JSON zamiast XML.
- 0.1.4 – poprawione logowanie nagłówków (w tym `Content-Type`) dla wywołań Help Desk w `HelpDeskClient`.
- 0.1.3 – nagłówek `Content-Type: application/json` dodany również do wywołań GET słowników Help Desk w `HelpDeskClient`.
- 0.1.2 – nagłówek `Content-Type: application/json` dodany do wywołań POST Help Desk API w `HelpDeskClient`.
- 0.1.1 – logowanie w trybie DEBUG pełnych zapytań/odpowiedzi do Help Desk API w kliencie `HelpDeskClient`.
- 0.1.0 – struktura, konfiguracja, sync/query API, unit systemd, Dockerfile.
