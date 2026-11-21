// Polish display labels. The API values (verified, proposed, ...) are never changed; only what the analyst reads is translated.
// The original value stays available as a tooltip so the Polish label can always be traced back to the stored status.

const LABELS: Record<string, string> = {
  // evidence status of relationships (the product's core vocabulary)
  verified: "zweryfikowane",
  corroborated: "potwierdzone niezależnie",
  supported: "poparte dowodem",
  proposed: "wstępne (niezweryfikowane)",
  contradicted: "sprzeczne",
  historical: "historyczne",
  rejected: "odrzucone",
  redacted: "zredagowane",
  // review and verification
  pending: "oczekuje",
  pending_approval: "czeka na zatwierdzenie",
  unverified: "niezweryfikowane",
  unreviewed: "nieprzejrzane",
  accepted: "zaakceptowane",
  approved: "zatwierdzone",
  confirmed: "potwierdzone",
  // runs and tasks
  completed: "zakończone",
  succeeded: "powiodło się",
  success: "powiodło się",
  failed: "niepowodzenie",
  timed_out: "przekroczono czas",
  dead: "wyczerpane próby",
  error: "błąd",
  unavailable: "niedostępne",
  skipped: "pominięte",
  skipped_policy: "pominięte przez politykę",
  stopped_by_limit: "zatrzymane limitem",
  aborted: "przerwane",
  reused: "użyto ponownie",
  queued: "w kolejce",
  running: "w toku",
  // providers and sources
  healthy: "sprawny",
  ok: "w porządku",
  degraded: "ograniczona sprawność",
  placeholder: "zaślepka",
  implemented: "zaimplementowany",
  partial: "częściowo",
  active: "aktywny",
  available: "dostępne",
  restricted: "ograniczone",
  stale: "nieaktualne",
  // entity resolution
  resolved: "rozpoznana",
  ambiguous: "niejednoznaczna",
  unresolved: "nierozpoznana",
  // entity types
  social_account: "konto w serwisie społecznościowym",
  public_profile: "profil publiczny",
  channel: "kanał",
  post: "wpis",
  website: "strona internetowa",
  domain: "domena",
  wallet: "portfel",
  token: "token",
  contract: "kontrakt",
  transaction: "transakcja",
  organization: "organizacja",
  document: "dokument",
  source: "źródło",
  // networks and misc
  evm: "EVM",
  solana: "Solana",
  bitcoin: "Bitcoin",
  "explorer flags scam": "eksplorator oznacza jako oszustwo",
  // policies
  allow: "zezwól",
  require_approval: "wymagaj zatwierdzenia",
  deny: "zabroń",
  // roles
  viewer: "obserwator",
  analyst: "analityk",
  admin: "administrator",
};

/** Polish label for a stored value. Unknown values stay readable (underscores replaced by spaces). */
export function label(value: string | null | undefined): string {
  if (!value) return "";
  return LABELS[value.toLowerCase()] ?? LABELS[value] ?? value.replace(/_/g, " ");
}

/** Polish plural: pl(1, "strona", "strony", "stron"), pl(2, ...), pl(5, ...). */
export function pl(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n);
  if (a === 1) return one;
  const last = a % 10;
  const last2 = a % 100;
  if (last >= 2 && last <= 4 && !(last2 >= 12 && last2 <= 14)) return few;
  return many;
}

/** Date and time in the Polish locale, e.g. "21.11.2026, 12:30". */
export function when(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`);
  if (Number.isNaN(d.getTime())) return s;
  return new Intl.DateTimeFormat("pl-PL", { dateStyle: "short", timeStyle: "short" }).format(d);
}
