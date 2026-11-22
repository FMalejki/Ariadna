# Implementation status

STATUS: PARTIALLY_IMPLEMENTED

Honest inventory. "Verified" means a command was actually run and its result observed. The only session note so far is [2026-11-21](sessions/2026-11-21-initial-build.md); it predates the Lead Search, Automation, redesign and Polish-UI work listed below (a newer session note is still to be written).

## One-paragraph summary

Ariadna is a persistent OSINT intelligence and knowledge-graph platform for crypto analysis (hackathon project for KAS / Ministry of Finance). An analyst starts from a name, a login, a URL, an address or a transaction hash. The system collects observations through replaceable providers, stores them append-only with raw artifacts, and derives relationship status only from evidence (there is no confidence score). A wallet mention on a page is never treated as ownership. LLM output is stored as an unverified claim. The backend (FastAPI + SQLAlchemy + Alembic) and the Polish-language web UI (Next.js) are built and covered by automated tests; end-to-end runs against real external services are mostly unverified (see below).

## Co można robić w aplikacji (po stronach)

Opis oparty na kodzie stron frontendu i endpointach API. Interfejs jest po polsku. Wartości statusów w API zostają po angielsku (`verified`, `proposed`), a tłumaczone są tylko etykiety.

**Role.** Obserwator tylko czyta. Analityk uruchamia wyszukiwania, pobieranie stron, śledzenie, automatyzację, zadania rozpoznania i importy, przegląda relacje, twierdzenia i kandydatów do połączenia oraz pobiera artefakty. Administrator dodatkowo zarządza dostawcami i politykami oraz czyta dziennik audytu. Logowanie: login i hasło, token JWT.

**Przegląd.** Sumy encji, źródeł, obserwacji i relacji. Nowe elementy z ostatnich 24 godzin. Lista „Wymaga uwagi" z linkami: zadania do zatwierdzenia, kandydaci do połączenia, twierdzenia modelu, relacje sprzeczne, źródła nieaktualne lub niedostępne, zadania martwe. Encje według typu, relacje według statusu dowodowego, ostatnie uruchomienia dostawców.

**Wyszukiwanie tropów** (przepływ ręczny, każdy krok zatwierdza analityk):
1. Podanie osoby lub tematu. System proponuje zapytania deterministycznie (bez AI). Zapytania można edytować, a sesję rozpoczyna się jawnie.
2. „Zatwierdź i szukaj": Tavily (oficjalne API) albo ddgs (nieoficjalny, w szarej strefie regulaminów). Przejście na zapas jest zawsze zgłaszane. Wyniki to tropy, nie fakty. Można wrócić do wcześniejszych wyszukiwań sesji.
3. „Pobierz stronę" z wyników albo z ręcznie wklejonego adresu. Respektowane są `robots.txt` i ochrona SSRF, jest limit rozmiaru, a X, Telegram, Instagram, Facebook, TikTok i LinkedIn nigdy nie są pobierane. Treść strony nie jest zapisywana. Z strony wyciągani są kandydaci na adresy Solana, EVM i Bitcoin z krótkim kontekstem (bez e-maili i telefonów). Przy kandydacie widać powtarzalność wzmianki słowami. „Śledź adres" wymaga uzasadnienia (min. 10 znaków, trafia do audytu) i tworzy tylko relację „wzmianka na stronie".
4. „Sprawdź w blockchainie": saldo, liczba transakcji, ostatnie transakcje, kontrahenci. Dostępne są „Odśwież", „Wczytaj więcej" (kolejna strona historii jako osobne audytowane uruchomienie) oraz wybór do 20 kontrahentów. „Dodaj zaznaczone jako powiązane portfele" tworzy `WALLET_TRANSACTED_WITH`, czyli fakt transakcyjny, a nie własność.
5. Lista relacji utworzonych w sesji i link do grafu.

Wskaźnik powtarzalności w tej stronie pojawia się tylko wtedy, gdy fraza z kroku 1 (pełne imię i nazwisko) występuje obok adresu na pobranej stronie. W ręcznym przepływie login nie jest używany, tylko w Automatyzacji. Poziomy (słowami): bardzo niska od 1 strony, niska od 3, umiarkowana od 5, wysoka od 8, bardzo wysoka od 13, niemal pewna od 20 różnych stron. Backend zwraca je po angielsku (`very low`, `low`, `moderate`, `high`, `very high`, `almost certain repeatability`).

**Automatyzacja.** To samo w tle, etapami A–E, każdy włączany osobno: A Sherlock (login do adresów profili, tylko zgodność ciągu znaków), B wyszukiwanie, pobranie stron i kandydaci na adresy, C śledzenie adresów z imieniem i nazwiskiem lub loginem w kontekście ±120 znaków (wymaga B), D sprawdzenie portfeli w blockchainie (wymaga C), E rozwinięcie o jeden krok do 20 kontrahentów (wymaga D). Twarde limity: zapytania (domyślnie 6, maks. 20), strony (15, maks. 50), adresy (30, maks. 100), czas (600 s, maks. 1800 s). Strona pokazuje dziennik każdego kroku, powód zatrzymania, tabelę kandydatów i listę uruchomień. Uruchomienie można przerwać. Worker prowadzi je dalej po odświeżeniu strony i restarcie.

**Rozpoznanie.** Nowa sprawa z imienia i nazwiska, loginu lub elementu początkowego (URL, @uchwyt, adres portfela, hash transakcji, domena), wystarczy jedno z nich. Kolejka zadań z filtrem statusu. Zatwierdzanie lub odrzucanie zadań wrażliwych. Przycisk „Wykonaj zadania do uruchomienia" wykonuje do 25 zadań. Martwe zadania można ponowić.

**Graf wiedzy.** Wyszukiwanie encji, filtr statusów relacji, rozwijanie sąsiadów dwuklikiem (portfel bez zapisanych danych on-chain dostaje zapytanie, jeden krok, bez duplikatów). Kliknięcie krawędzi otwiera dowody. Panel węzła ma linki do strony źródłowej i do zewnętrznych eksploratorów (Blockscout, Blockstream, Solana Explorer), które służą do własnej weryfikacji i nie są dowodem. Najkrótsza ścieżka między dwoma węzłami, czyszczenie widoku, legenda statusów. Widok jest ograniczony rozmiarem i ostrzega, gdy jest skrócony.

**Encje.** Lista z wyszukiwaniem i filtrem typu (12 typów). Profil ma zakładki: przegląd, relacje, obserwacje, źródła, twierdzenia, oś czasu. Pokazuje zdarzenia zweryfikowane w blockchainie, aliasy, sprzeczności i oczekujące dopasowania. Podobne nazwy nigdy nie są łączone automatycznie.

**Źródła.** Lista z wyszukiwaniem i filtrem statusu oraz osobna lista źródeł nieaktualnych.

**Sprawy.** Lista spraw i szczegóły: ponowne wykorzystanie wiedzy (co było znane, co nowe), notatki, eksport JSON, import rekordów dostarczonych przez analityka (wymaga podstawy uprawnienia, rekordy są zapisywane jako niezweryfikowane).

**Centrum dowodów.** Każde uruchomienie dostawcy z filtrem dostawcy i statusu. Surowy artefakt jest pobierany przez analityka, nigdy wyświetlany.

**Baza wiedzy** (5 zakładek). Obserwacje z wyszukiwaniem. Relacje (akceptacja lub odrzucenie przeglądu). Twierdzenia modelu językowego (akceptacja zapisuje tylko przegląd, nie weryfikuje faktu i nie tworzy relacji). Kandydaci do połączenia encji. Zapisane połączenia z możliwością cofnięcia.

**Dostawcy danych.** Lista z opisem, kategorią i klasą dowodu. Analityk sprawdza stan, administrator włącza i wyłącza dostawców. Widoczne są limity zapytań. Zaślepki (`maigret`, `spiderfoot`, `maltego`) nie dają się włączyć.

**Ustawienia.** Polityki zbierania (kategoria, domena lub dostawca: zezwól, wymagaj zatwierdzenia, zabroń), dodaje i usuwa je administrator. Wiki projektu: lista stron, synchronizacja z dysku, kontrola spójności. Dziennik audytu (administrator). Konfiguracja uruchomieniowa bez sekretów.

**Tylko w API, bez przycisku w interfejsie** (frontend ich nie wywołuje): usuwanie encji i źródeł oraz poprawianie źródeł, wyciąganie twierdzeń z obserwacji i streszczenie encji przez model językowy, zapisane widoki sprawy, lista zmian od wskazanej daty.

**Dwie zasady, które warto powiedzieć głośno.** W produkcie nie ma liczbowej „pewności": status relacji wynika wyłącznie z dowodów, a powtarzalność wzmianki jest podawana słowami i niczego nie zmienia w statusie. Wzmianka o adresie na stronie nigdy nie oznacza własności.


## Verified by running

| Area | Evidence |
|---|---|
| Backend logic and API | `cd backend && pytest -q` on SQLite: **317 tests pass** (last run after the Polish backend texts). No test touches the network. Includes the wiki lint test and the repeatability, automation, leads and web-provider tests. |
| Migrations | Two Alembic revisions (`0001_initial_schema`, `0002_automation_runs`). Upgrade/downgrade and model/migration drift are covered by `tests/test_migrations.py` on SQLite. |
| Frontend build | `npx tsc --noEmit` and `npm run build` succeed (12 routes: Overview, Leads, Automation, Discovery, Graph, Entities, Sources, Investigations, Evidence, KB, Providers, Settings). |
| Docker images | An earlier `docker compose build` of backend, worker and frontend finished (reported from the user's terminal). Images were rebuilt by the user after the later changes; not re-observed here. |
| MVP demo via API (earlier) | Live `uvicorn` on SQLite with curl and synthetic data: investigation A import, overlapping investigation B import gave `knowledge_reuse` = 1 reused, 1 new observation, 0 new relationships; `/wiki/lint` ok; CORS preflight 200. |

## Implemented and tested (backend)

URL/identifier normalization · idempotent, versioned ingestion · conservative entity resolution with reversible merges · evidence-derived relationship status incl. contradictions and historical edges · discovery with policy, approval, reuse, depth cap, retry, dead-letter · bounded graph, path and search endpoints · entity profile and timeline · governance (deletion, correction, export) · JWT auth and roles (viewer / analyst / admin) · audit log · wiki mirror and lint · LLM claim layer (scripted fake client) · manual import · REST API with OpenAPI at `/docs`.

### Lead Search workflow (tested with mocked HTTP and fixtures)

- `POST /leads/queries/propose`: deterministic query suggestions (no AI); nothing runs until the analyst approves a query.
- `POST /leads/search`: web search via **Tavily** (official keyed API) with **ddgs** as an **unofficial, ToS-gray** fallback; a fallback is always reported, never silent. Results are `tool_generated_discovery` leads only.
- `POST /leads/fetch`: fetches only pages the analyst picked. Respects `robots.txt`, SSRF protection, size limit, no-scrape domain list (X, Telegram, Instagram, Facebook, TikTok, LinkedIn). Extracts Solana / EVM / Bitcoin address candidates with short context; contact data is redacted.
- `POST /leads/track`: records an address as a wallet with a `PAGE_REFERENCES_ENTITY` relation. This is a page mention, not ownership; the analyst must give a reason (audited).
- `POST /leads/onchain`, `/leads/onchain/more`: on-chain facts through `evm_rpc`, `solana_rpc`, `bitcoin_esplora`; "load more" fetches the next history page as a separate provider run with a cursor.
- `POST /leads/expand`, `/leads/graph-expand`: one-step counterparty expansion, at most 20 counterparties, idempotent, creates `WALLET_TRANSACTED_WITH` (an on-chain fact, not shared ownership). Reuses stored lookups; no new node request if data exists.
- **Repeatability level** of a mention: shown in words only (low / medium / high), derived from the number of distinct URLs, never as a percentage, does not change relationship status, always labelled "not ownership; different URLs may copy the same content".

### Automation (stages A to E, tested; background worker)

- Runs are stored in the database and advanced by the worker in time-boxed chunks, so they survive restarts. Endpoints: `GET /automation/config`, `POST/GET /automation/runs`, `GET /automation/runs/{id}`, `POST /automation/runs/{id}/abort`.
- Stages, each switched on independently: **A** Sherlock (login to profile URLs, username-string matches only), **B** web search then fetch then address candidates, **C** track only addresses that have the full name or login within ±120 characters on the same page, **D** on-chain lookup of the tracked wallets, **E** one expansion step.
- Hard limits always apply (queries, pages, addresses, seconds), every step is logged, runs can be aborted, and the result states why it stopped.
- Choosing stage A is recorded as the analyst's explicit approval.

### Discovery input

A full name and a login (stored as a username without a platform) are accepted as discovery inputs besides URL / handle / address / hash.

## Frontend (built, type-checked, not seen in a browser)

- **Polish UI** (`<html lang="pl">`): all pages, navigation, sign-in, drawers, placeholders and aria-labels. API values stay in English (`verified`, `proposed`, `pending_approval`); only labels are translated through `frontend/src/lib/i18n.ts`. Dates use `Intl` with pl-PL.
- **Ariadna design**: light theme, single brand colour (indigo), self-hosted IBM Plex fonts, grouped navigation with a "thread" line. Status colours are reserved for evidence status and are always paired with a glyph and border style, so "proposed" never looks "verified".
- Pages: Overview, Lead Search, Automation, Discovery, Knowledge graph, Entities, Sources, Investigations, Evidence center, Knowledge base, Providers, Settings.
- All warnings are kept in Polish: mention is not ownership, ddgs is unofficial, Sherlock results are username-string matches, repeatability is not a confidence score.
- Texts that still arrive from the backend in English: automation log details, stop reasons and outcomes, error messages (`HTTPException`, `LeadError`), JSON keys in the settings panel. Already-stored runs keep the English texts they were saved with.

## Implemented, not verified end to end

| Area | Gap |
|---|---|
| Frontend runtime | Never opened in a browser by the assistant. Neither the redesign nor the Polish translation has been visually checked. Graph interactions (expand, edge drawer, path, link panel) are untested. |
| Containers running | `docker compose up`, Alembic upgrade on PostgreSQL inside the container, worker start and frontend-to-backend connectivity were not verified by the assistant. |
| PostgreSQL | Migrations rendered as PostgreSQL SQL offline; never executed on a PostgreSQL server. Tests run on SQLite. JSONB behavior is untested. |
| EVM, Solana, Bitcoin | Tested against mocked JSON-RPC / Esplora / Blockscout responses only. No real-node smoke test has been run. |
| Web search and fetch | Tavily and ddgs are tested with mocked responses. Live behavior (rate limits, ddgs breakage, real pages) is not covered by tests. |
| Sherlock | Wrapper and parser tested with fixtures. The `sherlock` binary was not installed, so it was never run. Known issue: a profile URL such as `website:discord.com/` is not normalised cleanly. |
| Automation under load | Time boxes and limits are tested with fakes. Behavior against public nodes returning HTTP 429 is not observed. |
| LLM layer | Tested only with a scripted fake client. Never run against a real model. |

## Placeholders (no integration code)

`maigret`, `spiderfoot`, `maltego`. Listed in the UI as placeholders; cannot be enabled (HTTP 409). (`web_search` and `web_fetch` are no longer placeholders; they are implemented providers.)

## Provisioned but unused

Redis is in `docker-compose.yml`. No code uses it. MinIO was removed from Compose (its image was not pullable and nothing uses it). The worker polls the database (ADR-0006) and artifacts go to the local filesystem (ADR-0007).

## Not built

S3/MinIO artifact backend · Redis queue · scheduled re-collection · full-text search · official-API collectors for X or web crawling · login throttling and cookie sessions · user management UI · pagination and virtualization for very large result sets · relationship-review audit entries · stablecoin payout analysis (facts only) · translation of backend log and error texts.

## Documentation debt

The wiki has not yet been brought up to date with the Lead Search and Automation work. Still to do: `PROVIDER_CATALOG`, `DISCOVERY_ENGINE`, `DATA_FLOWS`, `SECURITY_AND_PRIVACY`, `RELATIONSHIP_TYPES` (`WALLET_TRANSACTED_WITH`, `PAGE_REFERENCES_ENTITY`), `EVIDENCE_MODEL` (repeatability is not a confidence score), `ROADMAP`, `runbooks`; ADR-0010 and later (web search and lead workflow, automation, Polish UI and redesign); a new session note; `OSINT_*` variable names (no values) in `.env.example`.

## Known minor issues

- `app/api/knowledge.py` re-exports a few unused names through `__all__`.
- Alembic emits a `path_separator` deprecation warning in tests.
- The graph "shortest path" UI needs two clicks (set start, then select end node).
- The Automation page does not show how many URLs are left before a run starts.
- Open question: the same network may appear as both `evm` and `ethereum`, which can create duplicate entities.
