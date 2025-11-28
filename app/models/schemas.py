from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


# DTOs for Help Desk data
class TicketDTO(BaseModel):
    id: int  # Identyfikator zgłoszenia w Help Desk
    url_suffix: str  # Suffix dołączy do globalnego prefixu URL
    topic: str  # Temat zgłoszenia
    details: str  # Pełna treść zgłoszenia
    status: str  # Aktualny status
    category: Optional[str] = None  # Kategoria zgłoszenia
    time_created: datetime  # Czas utworzenia
    time_modified: datetime  # Czas ostatniej modyfikacji
    tags: List[str] = Field(default_factory=list)  # Lista tagów
    related_tickets: List[int] = Field(default_factory=list)  # Powiązane zgłoszenia


class UpdateDTO(BaseModel):
    id: int  # Identyfikator aktualizacji
    ticket_id: int  # Id zgłoszenia powiązanego
    url_suffix: str  # Suffix do pełnego linku aktualizacji
    type: str  # Typ aktualizacji (np. komentarz, zmiana statusu)
    new_ticket_status: Optional[str] = None  # Status zgłoszenia po aktualizacji
    details: str  # Treść aktualizacji
    user_role: str  # Rola użytkownika dodającego aktualizację
    time_created: datetime  # Czas utworzenia aktualizacji
    time_modified: datetime  # Czas ostatniej edycji
    hidden: bool = False  # Czy aktualizacja jest ukryta


# API request/response models
class SyncFilters(BaseModel):
    time_modified_after: Optional[datetime] = None  # Pobieraj wpisy zmodyfikowane po tej dacie
    time_modified_before: Optional[datetime] = None  # Ogranicz synchronizację do wpisów przed tą datą
    status: Optional[List[str]] = None  # Filtruj tickety po statusach
    category: Optional[List[str]] = None  # Filtruj po kategoriach
    tag: Optional[List[str]] = None  # Filtruj po tagach
    ticket_id: Optional[List[int]] = None  # Synchronizuj tylko wskazane ticket ID
    update_type: Optional[List[str]] = None  # Filtruj aktualizacje po typach
    new_ticket_status: Optional[List[str]] = None  # Filtruj aktualizacje po docelowym statusie


class SyncRequest(BaseModel):
    filters: Optional[SyncFilters] = None  # Zestaw filtrów dla synchronizacji
    dry_run: bool = False  # Nie zapisuj do Qdrant gdy True
    page_limit: int = 100  # Limit rekordów na stronę w Help Desk API


class SyncResult(BaseModel):
    tickets_fetched: int  # Liczba pobranych ticketów
    updates_fetched: int  # Liczba pobranych aktualizacji
    points_upserted: int  # Liczba zapisanych punktów w Qdrant
    points_marked_hidden: int  # Ile punktów oznaczono jako ukryte
    last_ticket_modified: Optional[datetime] = None  # Najpóźniejsza modyfikacja ticketów
    last_update_modified: Optional[datetime] = None  # Najpóźniejsza modyfikacja aktualizacji


class QueryFilters(BaseModel):
    time_from: Optional[datetime] = None  # Filtruj wyniki od tej daty modyfikacji
    time_to: Optional[datetime] = None  # Filtruj wyniki do tej daty modyfikacji
    status: Optional[List[str]] = None  # Dozwolone statusy zgłoszeń
    category: Optional[List[str]] = None  # Dozwolone kategorie
    tags: Optional[List[str]] = None  # Wymagane tagi
    user_roles: Optional[List[str]] = None  # Role autorów aktualizacji
    update_types: Optional[List[str]] = None  # Typy aktualizacji
    hide_hidden: bool = True  # Domyślnie pomijamy ukryte wpisy


class QueryRequest(BaseModel):
    query: str  # Treść zapytania tekstowego
    limit: int = Field(default=5, ge=1, le=50)  # Maksymalna liczba wyników
    filters: Optional[QueryFilters] = None  # Opcjonalne filtry wyników


class Passage(BaseModel):
    text: str  # Tekst znalezionego fragmentu
    score: float  # Wynik podobieństwa kosinusowego
    source: str  # Źródło fragmentu (ticket/update)
    ticket_id: Optional[int] = None  # Identyfikator zgłoszenia
    update_id: Optional[int] = None  # Identyfikator aktualizacji
    status: Optional[str] = None  # Status zgłoszenia w momencie rekordu
    category: Optional[str] = None  # Kategoria zgłoszenia
    tags: List[str] = Field(default_factory=list)  # Lista tagów
    user_role: Optional[str] = None  # Rola autora aktualizacji
    update_type: Optional[str] = None  # Typ aktualizacji
    time_created: Optional[datetime] = None  # Data utworzenia wpisu
    time_modified: Optional[datetime] = None  # Data modyfikacji wpisu
    url_suffix: Optional[str] = None  # Suffix linku w Help Desk
    url: Optional[HttpUrl] = None  # Pełny link złożony w API
    chunk_no: Optional[int] = None  # Numer chunku (0-based)
    chunk_total: Optional[int] = None  # Łączna liczba chunków wpisu
    chunk_start: Optional[int] = None  # Offset początkowy w `details`
    chunk_end: Optional[int] = None  # Offset końcowy w `details`
    sentence_start: Optional[int] = None  # Indeks pierwszego zdania
    sentence_end: Optional[int] = None  # Indeks ostatniego zdania
    details_hash: Optional[str] = None  # Hash całego tekstu
    chunk_text: Optional[str] = None  # Tekst chunku (alias `detail_chunk`)


class QueryResponse(BaseModel):
    query: str  # Zapytanie wejściowe
    results: List[Passage]  # Lista najlepszych wyników


class ThreadSearchResult(BaseModel):
    ticket_id: int  # Ticket identifier
    topic: Optional[str] = None  # Ticket topic
    status: Optional[str] = None  # Ticket status
    tags: List[str] = Field(default_factory=list)  # Tags inherited from ticket
    url_suffix: Optional[str] = None  # Ticket URL suffix
    url: Optional[HttpUrl] = None  # Full URL (prefix + suffix)
    time_created: Optional[datetime] = None  # Ticket creation time
    time_modified: Optional[datetime] = None  # Latest modification time across the thread
    score: float  # Best similarity score for the ticket
    thread_text: str  # Full thread text with user_role prefixes


class ThreadQueryResponse(BaseModel):
    query: str  # Zapytanie wejściowe
    results: List[ThreadSearchResult]  # Lista wątków posortowanych po trafności


class ChunkMetadata(BaseModel):
    chunk_no: int  # Numer fragmentu
    chunk_total: int  # Liczba fragmentów
    chunk_start: int  # Offset początkowy
    chunk_end: int  # Offset końcowy
    sentence_start: int  # Indeks pierwszego zdania
    sentence_end: int  # Indeks ostatniego zdania
    text: str  # Tekst fragmentu
    details_hash: Optional[str] = None  # Hash całego tekstu


class TicketDetailsResponse(BaseModel):
    ticket_id: int  # Identyfikator zgłoszenia
    topic: Optional[str] = None  # Temat zgłoszenia
    details: str  # Pełna treść odtworzona z chunków
    chunks: List[ChunkMetadata]  # Metadane chunków


class ThreadEntry(BaseModel):
    source: Literal["ticket", "update"]  # Typ wpisu w wątku
    ticket_id: int  # ID zgłoszenia
    update_id: Optional[int] = None  # ID aktualizacji (dla update)
    user_role: Optional[str] = None  # Rola autora
    status: Optional[str] = None  # Status po aktualizacji
    time_created: Optional[datetime] = None  # Czas utworzenia wpisu
    chunk_count: int  # Liczba chunków, z których złożono tekst
    text: str  # Treść wpisu


class TicketThreadResponse(BaseModel):
    ticket: TicketDetailsResponse  # Szczegóły zgłoszenia
    updates: List[ThreadEntry]  # Lista aktualizacji
    thread_text: str  # Cały wątek jako jeden tekst
