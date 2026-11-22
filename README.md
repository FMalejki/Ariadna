# Ariadna: trwała pamięć wywiadowcza i graf wiedzy dla analizy kryptowalut

Ariadna to **trwała, oparta na dowodach platforma OSINT** do analizy aktywności w ekosystemie kryptowalut. Powstała jako projekt hackathonowy dla administracji skarbowej (KAS) i Ministerstwa Finansów. Nie jest kolejnym narzędziem OSINT ani klonem Maltego. Jest warstwą pamięci nad istniejącymi narzędziami: zapamiętuje, co znaleziono, skąd to pochodzi i jak mocno to potwierdzają dowody. Druga sprawa korzysta z wiedzy zebranej w pierwszej.

**Status: działający prototyp.** Co jest sprawdzone uruchomieniem, a co nie, opisuje [status implementacji](docs/LLM_WIKI/IMPLEMENTATION_STATUS.md). Dokumentację projektu zaczyna się od [`docs/LLM_WIKI/START_HERE.md`](docs/LLM_WIKI/START_HERE.md). Asystenci AI muszą też stosować [`AGENTS.md`](AGENTS.md). Interfejs użytkownika jest w języku polskim.

---

## 1. Do czego służy

Analityk zaczyna od tego, co ma: imienia i nazwiska, loginu, adresu strony, adresu portfela, hasha transakcji, domeny albo uchwytu. System:

1. proponuje zapytania do wyszukiwarki i uruchamia je po zatwierdzeniu przez analityka;
2. pobiera tylko te strony, które analityk wskaże, i wyciąga z nich adresy Solana, EVM i Bitcoin razem z krótkim kontekstem;
3. sprawdza wybrane adresy w blockchainie (saldo, ostatnie transakcje, kontrahenci);
4. zapisuje każdą obserwację razem ze źródłem, czasem i surowym artefaktem;
5. buduje graf relacji, w którym status każdej relacji wynika wyłącznie z dowodów;
6. pozwala wrócić do wiedzy po miesiącach i nie dubluje tego, co już znane.

### Zasady, których system nie łamie

| Zasada | Co to oznacza w praktyce |
|---|---|
| Dowody ponad domysły | Obserwacje są dopisywane i nigdy nie nadpisywane (wersjonowanie). Każda relacja ma łańcuch pochodzenia: relacja, dowód, obserwacja, źródło, uruchomienie narzędzia, artefakt. |
| Wzmianka to nie własność | Adres znaleziony na stronie obok nazwiska jest zapisany jako „wzmianka na stronie". System **nigdy** nie tworzy relacji własności ani kontroli portfela. |
| Brak wskaźnika pewności | Nie ma procentów ani punktów pewności. Status relacji wynika z dowodów (sekcja 5). „Powtarzalność wzmianki" jest podawana słowami i niczego nie zmienia w statusie. |
| Konserwatywne łączenie encji | Podobne nazwy użytkowników nigdy nie są łączone automatycznie. Połączenia są miękkie i odwracalne. |
| Model językowy nie jest źródłem faktów | Wynik modelu to niezweryfikowane twierdzenie. Akceptacja zapisuje przegląd analityka, ale nie tworzy faktu ani relacji. Domyślnie warstwa LLM jest wyłączona. |
| Treści zewnętrzne to dane, nie polecenia | Tekst ze stron i wyszukiwarek jest niezaufany. Nie jest wykonywany ani przekazywany modelowi. |
| Bez prywatnych danych | Brak doxxingu i deanonimizacji. E-maile i numery telefonów są usuwane z kontekstu. Telegram nie jest scrapowany. |

---

## 2. Architektura

```
Przeglądarka (Next.js, React, Cytoscape)
        |  REST /api/v1, JWT, role: obserwator / analityk / administrator
FastAPI (backend) ---- SQLAlchemy + Alembic ---- PostgreSQL (SQLite w testach)
        |                                  \--- artefakty na dysku (lokalny magazyn)
        +-- rejestr dostawców danych (wymienialne adaptery)
        +-- silnik rozpoznania (polityki, zatwierdzanie, ponowne użycie, ponowienia)
        +-- warstwa dowodów i statusów relacji
Worker (osobny proces) ---- wykonuje zadania i automatyzację w paczkach czasowych
```

- **Dostawcy danych** to adaptery, a nie rdzeń systemu: `web_search`, `web_fetch`, `sherlock`, `evm_rpc`, `solana_rpc`, `bitcoin_esplora`, `manual_import`. Zaślepki `maigret`, `spiderfoot`, `maltego` są wymienione wprost i nie można ich włączyć.
- **Kolejka zadań** opiera się na bazie danych (worker odpytuje bazę). Redis jest w `docker-compose.yml`, ale nic go nie używa.
- **Artefakty** (surowe odpowiedzi dostawców) są na lokalnym dysku i są pobierane, nigdy wyświetlane w przeglądarce.

---

## 3. Parametry wyszukiwania

Poniżej wszystkie parametry, którymi system steruje wyszukiwaniem tropów. Wartości domyślne pochodzą z kodu (`backend/app/config.py`, `backend/app/providers/websearch.py`, `backend/app/services/leads.py`).

### 3.1. Podpowiedzi zapytań (krok 1, bez AI)

Z podanej osoby lub tematu (maks. 120 znaków) system buduje deterministycznie do 10 zapytań, plus do 10 fraz dodanych przez analityka. Nic nie jest wysyłane, dopóki analityk nie zatwierdzi zapytania.

| Zapytanie (`"Imię Nazwisko"` w cudzysłowie) | Cel |
|---|---|
| `"..."` | dokładna fraza, podstawowe wzmianki |
| `"..." wallet` | wzmianki o portfelu obok nazwy |
| `"..." solana` / `ethereum` / `bitcoin` | wzmianki dotyczące konkretnej sieci |
| `"..." ("0x" OR "bc1" OR "wallet address" OR "adres portfela")` | ciągi przypominające adresy |
| `"..." (donate OR donation OR "send to" OR "wpłata" OR "wplata")` | wezwania do wpłat, które zwykle podają adres |
| `"..." site:olx.pl` | ogłoszenia, których treść może zawierać adres do płatności |
| `"..." site:github.com` | pliki readme z adresami darowizn |
| `"..." site:medium.com` | artykuły i wpisy |
| `"..." <fraza analityka>` | do 10 własnych fraz (każda do 80 znaków) |

Zapytania można dowolnie edytować przed zatwierdzeniem. Duplikaty są usuwane.

### 3.2. Wykonanie wyszukiwania (krok 2)

| Parametr | Wartość | Opis |
|---|---|---|
| `query` | 1 do 300 znaków | Interfejs API przyjmuje do 400, ale dostawca odrzuca zapytania dłuższe niż 300. |
| `backend` | `auto` (domyślnie), `tavily`, `ddgs` | `auto` próbuje najpierw Tavily i przechodzi na ddgs, gdy Tavily nie jest skonfigurowane lub zawiedzie. Każde przejście jest jawnie zapisane (`fallback` z powodem), nigdy po cichu. |
| `max_results` | 1 do 20, domyślnie 10 (`OSINT_SEARCH_MAX_RESULTS`) | Liczba wyników. |
| Tavily `search_depth` | `basic` | Tryb podstawowy. |
| Tavily `exact_match` | włączane, gdy zapytanie zawiera cudzysłów | Wymusza dokładne dopasowanie frazy. |
| Tavily `include_answer` | wyłączone | Nie pobieramy odpowiedzi generowanej przez Tavily. |
| Limit czasu | `OSINT_PROVIDER_HTTP_TIMEOUT_SECONDS`, domyślnie 20 s | Dotyczy Tavily i ddgs. |

- **Tavily** to oficjalne, płatne API z kluczem (`OSINT_TAVILY_API_KEY`).
- **ddgs** to **nieoficjalny** scraper wyszukiwarki DuckDuckGo bez klucza. Może przestać działać w każdej chwili i **może naruszać regulamin wyszukiwarki**. Interfejs zawsze oznacza jego użycie.
- Wyniki są normalizowane: odrzucane są adresy inne niż http(s), niepubliczne i zduplikowane. Tytuł jest przycinany do 300 znaków, fragment do 600, a z obu usuwane są e-maile i telefony.
- Wyniki mają klasę dowodu `tool_generated_discovery`, czyli są **tropami, a nie faktami**. Nic nie trafia do grafu, dopóki analityk nie podejmie decyzji.

### 3.3. Pobieranie stron (krok 3)

Pobierany jest wyłącznie adres, który analityk wskazał: z wyników albo wklejony ręcznie. System nie przeszukuje stron i nie podąża za linkami.

| Parametr | Wartość domyślna | Zmienna |
|---|---|---|
| Maks. rozmiar odpowiedzi | 2 000 000 B | `OSINT_FETCH_MAX_BYTES` |
| Limit czasu żądania | 15 s (łączny limit na wszystkie przekierowania: dwukrotność, min. 5 s) | `OSINT_FETCH_TIMEOUT_SECONDS` |
| Maks. liczba przekierowań | 3 (każde sprawdzane osobno) | `OSINT_FETCH_MAX_REDIRECTS` |
| Maks. tekst do analizy | 400 000 znaków | stała w kodzie |
| Dozwolone typy treści | `text/html`, `application/xhtml+xml`, `text/plain` | stała w kodzie |
| User-Agent | `OSINTMemoryBot/0.1 (analyst-initiated fetch; honors robots.txt)` | `OSINT_FETCH_USER_AGENT` |
| Lista domen bez scrapowania | x.com, twitter.com, t.me, telegram.me, telegram.org, instagram.com, facebook.com, tiktok.com, linkedin.com | `OSINT_FETCH_BLOCKED_DOMAINS` |

Zabezpieczenia:

- **Ochrona SSRF:** nazwa hosta jest rozwiązywana przed każdym żądaniem i odrzucana, jeśli którykolwiek adres nie jest publiczny. Żądanie idzie na rozwiązany adres IP, więc odpowiedź DNS nie może się zmienić między sprawdzeniem a połączeniem.
- **`robots.txt`** (semantyka RFC 9309): zakaz oznacza brak pobrania, kod 4xx oznacza zgodę, a niedostępność lub 5xx jest traktowana jak zakaz.
- **Treść strony nie jest zapisywana.** Zostają tylko: adres, tytuł, skrót treści oraz dla każdego znalezionego adresu blockchain krótki kontekst bez danych kontaktowych.

### 3.4. Wyciąganie adresów i kontekst

| Parametr | Wartość |
|---|---|
| Rozpoznawane sieci | Solana, EVM (Ethereum i sieci zgodne), Bitcoin (adresy `1…`, `3…` z sumą kontrolną, SegWit `bc1…` z weryfikacją bech32/bech32m) |
| Promień kontekstu | 120 znaków z każdej strony adresu |
| Maks. kontekstów na adres | 2 |
| Maks. kandydatów na stronę | 50 |
| Usuwanie danych | e-maile i ciągi przypominające numer telefonu (co najmniej 9 znaków złożonych z cyfr i separatorów) zastępowane znacznikami |

### 3.5. Śledzenie adresu i zapytanie do blockchaina (kroki 3 i 4)

- **Śledzenie** wymaga uzasadnienia (min. 10 znaków w interfejsie), które trafia do dziennika audytu. Powstaje portfel i relacja `PAGE_REFERENCES_ENTITY` (wzmianka na stronie).
- **Zapytanie on-chain:** saldo, liczba transakcji lub nonce, ostatnie transakcje (`OSINT_ONCHAIN_HISTORY_LIMIT`, domyślnie 10) i kontrahenci, czyli inne adresy widoczne w tych samych transakcjach. Ostatnie zapytanie jest ponownie wykorzystywane, a „Odśwież" wymusza nowe.
- **„Wczytaj więcej"** pobiera kolejną stronę historii jako osobne, audytowane uruchomienie (kursor w kluczu wejściowym, więc nie zwróci pierwszej strony).
- **Rozwinięcie kontrahentów:** analityk wybiera do **20** kontrahentów (najczęstsi pierwsi). Powstaje `WALLET_TRANSACTED_WITH`, czyli fakt transakcyjny, który **nie oznacza wspólnej własności**. Operacja jest idempotentna. Kontrakty nie są zaznaczane przez „Zaznacz wszystkie". Giełdy i kontrakty występują przy wielu niezwiązanych stronach, co interfejs zaznacza.

### 3.6. Poziom powtarzalności wzmianki

Liczy, na ilu **różnych stronach** (różne URL, po normalizacji) w tej sesji pełne imię i nazwisko albo login stoi obok tego samego adresu. Pokazywany jest wyłącznie słowami.

| Liczba różnych stron | Poziom |
|---|---|
| 0 | brak (żadna strona nie umieszcza nazwy obok adresu) |
| 1 do 2 | bardzo niska |
| 3 do 4 | niska |
| 5 do 7 | umiarkowana |
| 8 do 12 | wysoka |
| 13 do 19 | bardzo wysoka |
| 20 i więcej | niemal pewna powtarzalność |

Dopasowanie jest ostrożne: bez rozróżniania wielkości liter i polskich znaków diakrytycznych; pasuje tylko **pełne** imię i nazwisko (dwa słowa także w odwrotnej kolejności), nigdy samo imię ani samo nazwisko. Login musi mieć co najmniej 3 znaki i występować jako cały token. Ten wskaźnik **nie jest dowodem własności** (różne adresy URL mogą kopiować tę samą treść), niczego nie zapisuje w bazie i nie zmienia statusu relacji.

W ręcznym Wyszukiwaniu tropów porównywana jest fraza z kroku 1. Login uwzględnia tylko Automatyzacja.

---

## 4. Automatyzacja (etapy A–E)

Ten sam przepływ, uruchamiany w tle przez workera, w paczkach po ok. 20 s. Przeżywa odświeżenie strony i restart. Każdy etap włącza się osobno.

| Etap | Co robi | Wymaga |
|---|---|---|
| A | Sherlock: login do adresów profili (tylko zgodność ciągu znaków nazwy użytkownika, a nie tożsamość). Login: 3 do 32 znaków (litery, cyfry, `_`, `.`, `-`), limit czasu 120 s. | loginu |
| B | Wyszukiwanie w sieci, pobranie stron, kandydaci na adresy. | |
| C | Śledzenie adresów, przy których w kontekście (±120 znaków, ta sama strona) stoi pełne imię i nazwisko lub login. Pozostałe adresy zostają kandydatami. | B |
| D | Zapytanie on-chain o portfele śledzone w tym uruchomieniu. | C |
| E | Rozwinięcie o jeden krok od tych portfeli (do 20 kontrahentów każdy). | D |

**Twarde limity** (zawsze obowiązują; przekroczenie kończy uruchomienie jako „zatrzymane limitem" z zapisanym powodem):

| Limit | Domyślnie | Maksimum |
|---|---|---|
| Liczba zapytań | 6 | 20 |
| Liczba pobranych stron | 15 | 50 |
| Liczba śledzonych adresów | 30 | 100 |
| Czas (sekundy) | 600 | 1800 |

Dodatkowo:

- Wybór etapu A przez analityka jest jawnym zatwierdzeniem Sherlocka i trafia do audytu.
- Każdy krok jest zapisywany w dzienniku (do 600 wpisów), także odrzucenia przez politykę pobierania. Błędy (np. HTTP 429) nie są pomijane po cichu.
- Uruchomienie można przerwać. Etap nigdy nie tworzy relacji własności i nie wychodzi poza jeden krok od kontrahentów.
- Brak źródła nie jest po cichu zastępowany innym.

---

## 5. Model dowodów: statusy relacji

Nie ma wskaźnika pewności. Status wynika z dowodów (pierwsza pasująca reguła wygrywa):

1. analityk odrzucił: `rejected`;
2. jest dowód sprzeczny: `contradicted` (oba rodzaje dowodów pozostają widoczne);
3. wszystkie dowody zastąpiła nowsza obserwacja tego samego źródła: `historical`;
4. co najmniej jedna deterministyczna weryfikacja w blockchainie: `verified`;
5. mocne dowody z co najmniej dwóch różnych źródeł: `corroborated`;
6. co najmniej jeden mocny dowód: `supported`;
7. w pozostałych przypadkach (odkrycie narzędzia, model językowy, nieweryfikowane): `proposed`.

Siła kategorii dowodu to uporządkowanie, a nie prawdopodobieństwo: weryfikacja blockchain i potwierdzenie analityka (6), dane oficjalne platformy (5), niezależne potwierdzenie (4), bezpośrednie oświadczenie źródła (3), odkrycie narzędzia (2), twierdzenie modelu (1). Szczegóły: [`EVIDENCE_MODEL.md`](docs/LLM_WIKI/EVIDENCE_MODEL.md).

---

## 6. Interfejs i role

Strony: Przegląd, Wyszukiwanie tropów, Automatyzacja, Rozpoznanie, Graf wiedzy, Encje, Źródła, Sprawy, Centrum dowodów, Baza wiedzy, Dostawcy danych, Ustawienia.

| Rola | Uprawnienia |
|---|---|
| Obserwator | czyta wszystko oprócz dziennika audytu i surowych artefaktów |
| Analityk | uruchamia wyszukiwania, pobieranie, śledzenie, automatyzację i zadania rozpoznania, przegląda relacje, twierdzenia i kandydatów do połączenia, importuje rekordy, pobiera artefakty |
| Administrator | dodatkowo zarządza dostawcami i politykami oraz czyta dziennik audytu |

Statusy dowodowe zawsze mają kolor, symbol i styl obramowania, więc „proponowana" nigdy nie wygląda jak „zweryfikowana". Linki do zewnętrznych eksploratorów w grafie służą do własnej weryfikacji i nie są dowodem.

---

## 7. Konfiguracja

Wszystkie zmienne mają prefiks `OSINT_`. Skopiuj `.env.example` do `.env` i **nigdy nie zapisuj sekretów w repozytorium**.

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `OSINT_DATABASE_URL` | `sqlite:///./osint.db` | adres bazy (w Compose PostgreSQL) |
| `OSINT_ARTIFACT_DIR` | `./artifacts` | katalog surowych artefaktów |
| `OSINT_JWT_SECRET` | brak (losowy przy starcie) | sekret tokenów; ustaw stały |
| `OSINT_ADMIN_USERNAME`, `OSINT_ADMIN_PASSWORD` | `admin`, brak | konto administratora powstaje tylko, gdy ustawiono hasło |
| `OSINT_CORS_ORIGINS` | `["http://localhost:3000"]` | dozwolone źródła przeglądarki |
| `OSINT_MAX_DISCOVERY_DEPTH` | 2 | maks. głębokość rozpoznania |
| `OSINT_REUSE_WINDOW_HOURS` | 168 | jak długo wynik jest ponownie wykorzystywany |
| `OSINT_STALE_AFTER_DAYS` | 30 | po ilu dniach źródło jest nieaktualne |
| `OSINT_AUTO_APPROVE_CATEGORIES` | `["blockchain_verification"]` | kategorie bez ręcznego zatwierdzania |
| `OSINT_TASK_MAX_ATTEMPTS`, `OSINT_TASK_RETRY_BACKOFF_SECONDS` | 3, 60 | ponowienia zadań |
| `OSINT_DEFAULT_RATE_LIMIT_PER_MINUTE`, `OSINT_DEFAULT_QUOTA_PER_DAY` | 20, 500 | limity dostawców |
| `OSINT_GRAPH_MAX_NODES`, `OSINT_GRAPH_MAX_PATH_DEPTH` | 200, 6 | ograniczenia widoku grafu |
| `OSINT_TAVILY_API_KEY`, `OSINT_TAVILY_BASE_URL` | brak, `https://api.tavily.com` | wyszukiwanie Tavily |
| `OSINT_SEARCH_DEFAULT_BACKEND`, `OSINT_SEARCH_MAX_RESULTS` | `auto`, 10 | domyślny silnik i liczba wyników |
| `OSINT_EVM_RPC_URLS` | `{}` | słownik sieć: adres RPC, np. `{"ethereum":"https://..."}` |
| `OSINT_EVM_EXPLORER_APIS` | `{}` | słownik sieć: API zgodne z Blockscout |
| `OSINT_SOLANA_RPC_URL` | brak | węzeł RPC Solany |
| `OSINT_BTC_ESPLORA_URL` | brak | API zgodne z Esplora, np. `https://mempool.space/api` |
| `OSINT_ONCHAIN_HISTORY_LIMIT` | 10 | liczba ostatnich transakcji na zapytanie |
| `OSINT_PROVIDER_HTTP_TIMEOUT_SECONDS` | 20 | limit czasu dostawców HTTP |
| `OSINT_SHERLOCK_BINARY`, `OSINT_SHERLOCK_TIMEOUT_SECONDS` | `sherlock`, 120 | Sherlock |
| `OSINT_LLM_PROVIDER` | `none` | `none` lub `openai_compatible`; `OSINT_LLM_BASE_URL`, `OSINT_LLM_MODEL`, `OSINT_LLM_API_KEY` |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | adres API widoczny w przeglądarce (ustalany przy budowie) |

Bez konfiguracji dostawca zgłasza „niedostępny", a system niczego nie zmyśla ani nie podstawia danych z innego źródła.

---

## 8. Uruchomienie

### Lokalnie (bez Dockera)

```bash
# backend (SQLite, bez usług zewnętrznych)
cd backend
python3.12 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
export OSINT_ADMIN_PASSWORD=change-me OSINT_JWT_SECRET=dev-secret-dev-secret-dev-secret-123456
uvicorn app.main:app --reload          # http://localhost:8000/docs
python -m app.worker                   # wymagany dla automatyzacji i zadań w tle (drugi terminal)

# frontend
cd frontend && npm install && npm run dev   # http://localhost:3000, zaloguj się jako admin
```

### Docker Compose

```bash
cp .env.example .env     # ustaw OSINT_ADMIN_PASSWORD i OSINT_JWT_SECRET
docker compose up -d --build backend worker frontend
```

Usługi: `postgres`, `backend` (uruchamia `alembic upgrade head`), `worker`, `frontend`. Redis startuje, ale nic go nie używa. Compose uruchamiał autor w Rancher Desktop; nie jest to objęte testami automatycznymi.

### Testy

```bash
cd backend && pytest -q                 # offline, SQLite, bez dostępu do sieci
cd frontend && npx tsc --noEmit && npm run build
```

Ostatnio: 317 testów backendu przechodzi, a `tsc` i `npm run build` kończą się powodzeniem. Testy używają atrap HTTP i danych testowych. Nie ma testów na prawdziwych węzłach blockchain, Tavily, ddgs ani Sherlocku.

---

## 9. Przykładowy scenariusz

1. Zaloguj się i otwórz **Wyszukiwanie tropów**. Wpisz osobę lub temat, kliknij „Zaproponuj zapytania" i rozpocznij sesję.
2. Zatwierdź wybrane zapytanie. Z wyników pobierz stronę, która wygląda na wartą przeczytania.
3. Przy kandydacie na adres sprawdź kontekst i poziom powtarzalności wzmianki. Jeśli warto, wpisz uzasadnienie i kliknij „Śledź adres".
4. Sprawdź portfel w blockchainie, wczytaj więcej historii i wybierz kontrahentów do dodania.
5. Otwórz **Graf wiedzy**: rozwiń węzły, kliknij krawędź, aby zobaczyć łańcuch dowodów, i znajdź ścieżkę między dwoma węzłami.
6. Alternatywnie użyj **Automatyzacji** dla tych samych kroków w tle z twardymi limitami.
7. Rozpocznij drugą sprawę z częściowo wspólnymi źródłami. **Sprawy** pokażą, które obserwacje i uruchomienia zostały ponownie wykorzystane, a które są nowe.

---

## 10. Ograniczenia (uczciwie)

- Interfejs nie ma testów automatycznych w przeglądarce. Sprawdzono tylko typy i budowanie (`tsc`, `npm run build`).
- Migracje nie były wykonane na prawdziwym PostgreSQL w testach (testy działają na SQLite).
- Dostawcy blockchain, wyszukiwania i Sherlock są przetestowani tylko na atrapach i danych testowych.
- Szczegóły dziennika automatyzacji i komunikaty błędów z backendu są nadal po angielsku.
- Nie zbudowano: magazynu S3/MinIO, kolejki Redis, cyklicznego ponownego zbierania, wyszukiwania pełnotekstowego, ograniczania prób logowania, interfejsu zarządzania użytkownikami ani oficjalnych kolektorów dla X.

## 11. Struktura repozytorium

`backend/` aplikacja FastAPI, migracje, testy · `frontend/` interfejs Next.js · `docs/LLM_WIKI/` wiki projektu i decyzje architektoniczne (ADR) · `docker-compose.yml` · `.env.example` · `PROMPT.md` pierwotna specyfikacja

Wiki projektu (`docs/LLM_WIKI`) jest pamięcią inżynierską i jest oddzielona od bazy wiedzy wywiadowczej, która żyje w bazie danych.
