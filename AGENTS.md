## Diagramy projektowe

Gdy zmiana dotyczy architektury, komunikacji między komponentami, cyklu życia obiektu lub relacji w bazie danych, dodaj lub zaktualizuj diagram w `docs/`.

- Zapisuj diagramy bezpośrednio w plikach `.md` jako bloki kodu otwierane przez ` ```mermaid ` i zamykane przez ` ``` `. Dzięki temu są widoczne we wbudowanym podglądzie Markdown w VS Code.
- Dobierz typ diagramu do zmiany. Użyj diagramu architektury dla usług i ich zależności, diagramu sekwencji dla kolejności wywołań, diagramu stanów dla cyklu życia obiektu lub ERD dla relacji w bazie danych.
- Pokaż ścieżki błędu, przekroczenia limitu czasu, ponowienia i anulowania, jeśli mają znaczenie dla opisywanego zachowania.
- Przed implementacją oznacz diagram jako „Projekt”. Po implementacji zweryfikuj go względem kodu, konfiguracji lub migracji i podaj pod diagramem odnośniki do odpowiednich plików.
- Aktualizuj istniejący diagram w tej samej zmianie co kod, jeśli zmienia się przedstawione w nim zachowanie lub struktura.