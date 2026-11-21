"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";

const NET_LABEL: Record<string, string> = { evm: "EVM", solana: "Solana", bitcoin: "Bitcoin" };
const STORE = "osint.leads.investigation";

function Step({ n, title, children, hint }: { n: number; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h2>
        <span className="badge b-info" style={{ marginRight: 8 }}>step {n}</span>
        {title}
      </h2>
      {hint && <div className="muted" style={{ marginBottom: 8 }}>{hint}</div>}
      {children}
    </div>
  );
}

/** Honest one-line result of a provider run (never hides unavailable / failed). */
function RunLine({ r }: { r: any }) {
  if (!r) return null;
  return (
    <div className="row" style={{ margin: "6px 0" }}>
      <Badge value={r.status} />
      <span className="muted">{r.provider} · run {short(r.tool_run_id, 8)}</span>
      {r.error && <span className={r.unavailable ? "muted" : "err"}>{r.error}</span>}
    </div>
  );
}

export default function Leads() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const canAct = can("analyst");

  const [subject, setSubject] = useState("");
  const [extra, setExtra] = useState("");
  const [inv, setInv] = useState<string | null>(null);
  useEffect(() => { setInv(window.localStorage.getItem(STORE)); }, []);
  const setInvStored = (id: string | null) => {
    setInv(id);
    if (id) window.localStorage.setItem(STORE, id); else window.localStorage.removeItem(STORE);
  };

  const cfg = useQuery({ queryKey: ["lead-config"], queryFn: () => api<any>("/leads/config") });
  const state = useQuery({ queryKey: ["lead-state", inv], queryFn: () => api<any>(`/leads/sessions/${inv}`), enabled: !!inv, retry: false });
  useEffect(() => { if (state.error && inv) setInvStored(null); /* stale id after a DB reset */ }, [state.error]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => qc.invalidateQueries({ queryKey: ["lead-state", inv] });

  // ---- step 1: proposed queries
  const [queries, setQueries] = useState<{ query: string; purpose: string }[]>([]);
  const propose = useMutation({
    mutationFn: () => api<any>("/leads/queries/propose", { body: { subject, extra_terms: extra.split(",").map((s) => s.trim()).filter(Boolean) } }),
    onSuccess: (r) => setQueries(r.queries),
  });
  const begin = useMutation({
    mutationFn: () => api<any>("/leads/sessions", { body: { subject } }),
    onSuccess: (r) => { setInvStored(r.investigation_id); setSearches({}); setPage(null); setOnchain({}); },
  });

  // ---- step 2: search (analyst approves each query; backend explicit)
  const [backend, setBackend] = useState("auto");
  const [searches, setSearches] = useState<Record<string, any>>({});
  const [activeQuery, setActiveQuery] = useState<string | null>(null);
  const search = useMutation({
    mutationFn: (q: string) => api<any>("/leads/search", { body: { investigation_id: inv, query: q, backend } }),
    onSuccess: (r) => { setSearches((s) => ({ ...s, [r.query]: r })); setActiveQuery(r.query); refresh(); },
  });

  // ---- step 3: fetch one selected page
  const [manualUrl, setManualUrl] = useState("");
  const [page, setPage] = useState<any>(null);
  const fetchPage = useMutation({
    mutationFn: (url: string) => api<any>("/leads/fetch", { body: { investigation_id: inv, url } }),
    onSuccess: (r) => { setPage(r); refresh(); },
  });

  // ---- step 4: track decision (rationale mandatory)
  const [rationale, setRationale] = useState<Record<string, string>>({});
  const [tracked, setTracked] = useState<Record<string, any>>({});
  const track = useMutation({
    mutationFn: (c: any) =>
      api<any>("/leads/track", {
        body: { investigation_id: inv, fetch_run_id: page.tool_run_id, network: c.network, address: c.address, rationale: rationale[`${c.network}:${c.address}`] ?? "" },
      }),
    onSuccess: (r, c) => { setTracked((t) => ({ ...t, [`${c.network}:${c.address}`]: r })); refresh(); },
  });

  // ---- step 5: on-chain lookup
  const [onchain, setOnchain] = useState<Record<string, any>>({});
  const lookup = useMutation({
    mutationFn: (v: { entity_id: string; refresh: boolean }) => api<any>("/leads/onchain", { body: { investigation_id: inv, ...v } }),
    onSuccess: (r) => { setOnchain((o) => ({ ...o, [r.entity_id]: r })); refresh(); },
  });

  // ---- step 6: expand selected counterparties
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState<Record<string, any>>({});
  const expand = useMutation({
    mutationFn: (v: { entity_id: string; lookup_run_id: string; list: any[] }) =>
      api<any>("/leads/expand", { body: { investigation_id: inv, entity_id: v.entity_id, lookup_run_id: v.lookup_run_id, counterparties: v.list } }),
    onSuccess: (r, v) => { setExpanded((e) => ({ ...e, [v.entity_id]: r })); setPicked({}); refresh(); },
  });

  const searchRuns = (state.data?.runs ?? []).filter((r: any) => r.provider === "web_search");
  const results = activeQuery ? searches[activeQuery] : null;
  const loadPastRun = async (runId: string) => {
    const d = await api<any>(`/leads/runs/${runId}`);
    setSearches((s) => ({ ...s, [d.query]: { ...d, query: d.query } }));
    setActiveQuery(d.query);
  };
  const wallets: any[] = state.data?.wallets ?? [];

  return (
    <Page
      title="Lead search → wallet → on-chain"
      sub="Find where a public wallet address is mentioned next to a person or topic, then check it on-chain. Every step is your decision. A mention is not ownership."
    >
      <div className="warnbox">
        Search results are <b>leads</b>, not evidence. A page that shows an address next to a name proves only that the page says so. Nothing here links a
        person to a wallet; counterparties are transaction facts, not identity. Contact data (e-mail, phone) is dropped; only address, short context and URL are kept.
      </div>
      {cfg.data && (
        <div className="notice">
          Search: Tavily {cfg.data.tavily_configured ? "configured (official API)" : "NOT configured (set OSINT_TAVILY_API_KEY)"} · ddgs is{" "}
          <b>unofficial / terms-of-service gray</b> keyless scraping, used only as an explicit choice or a recorded fallback. On-chain: Bitcoin{" "}
          {cfg.data.btc_esplora_configured ? "configured" : "not configured"} · EVM history explorers: {cfg.data.evm_explorer_networks.join(", ") || "none"}.
        </div>
      )}
      {!canAct && <div className="muted">Viewer role can read results but cannot run steps.</div>}

      <Step n={1} title="Person or topic → proposed queries" hint="Suggestions are deterministic (no AI) and nothing is sent until you approve a query in step 2. Edit freely.">
        <div className="row">
          <input className="grow" placeholder='Person or topic, e.g. Jan Kowalski' value={subject} onChange={(e) => setSubject(e.target.value)} />
          <input placeholder="extra terms, comma-separated" value={extra} onChange={(e) => setExtra(e.target.value)} />
          <button disabled={!subject.trim() || propose.isPending} onClick={() => propose.mutate()}>Propose queries</button>
          <button className="ghost" disabled={!canAct || !subject.trim() || begin.isPending} onClick={() => begin.mutate()}>
            {inv ? "Start a new session" : "Start session"}
          </button>
        </div>
        <ErrorLine error={propose.error || begin.error} />
        {inv && state.data && (
          <div className="notice">
            Session <code>{short(inv, 8)}</code>: {state.data.investigation.name} · <Link href={`/investigations?id=${inv}`}>investigation</Link>
          </div>
        )}
        {!inv && queries.length > 0 && <div className="warnbox">Start a session to run searches (it groups your steps and audit trail).</div>}
        {queries.length > 0 && (
          <table>
            <thead><tr><th>Query (editable)</th><th>Why</th><th /></tr></thead>
            <tbody>
              {queries.map((q, i) => (
                <tr key={q.purpose + i}>
                  <td>
                    <input className="mono" style={{ width: "100%" }} value={q.query}
                      onChange={(e) => setQueries((all) => all.map((x, j) => (j === i ? { ...x, query: e.target.value } : x)))} />
                  </td>
                  <td className="muted">{q.purpose}</td>
                  <td><button className="sm" disabled={!canAct || !inv || search.isPending} onClick={() => search.mutate(q.query)}>Approve & search</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <label>search backend{" "}
            <select value={backend} onChange={(e) => setBackend(e.target.value)}>
              <option value="auto">auto (Tavily, recorded fallback to ddgs)</option>
              <option value="tavily">Tavily only</option>
              <option value="ddgs">ddgs only (unofficial)</option>
            </select>
          </label>
        </div>
        <ErrorLine error={search.error} />
      </Step>

      <Step n={2} title="Search results" hint="Pick pages worth reading. Only pages you choose are fetched.">
        {searchRuns.length > 0 && (
          <div className="row" style={{ marginBottom: 6 }}>
            <span className="muted">Earlier searches:</span>
            {searchRuns.slice(0, 8).map((r: any) => (
              <button key={r.tool_run_id} className="ghost sm" onClick={() => loadPastRun(r.tool_run_id)} title={when(r.started_at)}>{short(r.query ?? r.target, 40)}</button>
            ))}
          </div>
        )}
        {!results ? <Empty>No search yet. Approve a query in step 1.</Empty> : (
          <>
            <RunLine r={results} />
            <div className="row">
              <span className="muted">backend used:</span> <Badge value={results.backend_used} />
              {results.unofficial_backend && <span className="warnbox" style={{ margin: 0 }}>unofficial ddgs scraping (ToS-gray, may break)</span>}
              {results.fallback && <span className="warnbox" style={{ margin: 0 }}>fallback from {results.fallback.from}: {results.fallback.reason}</span>}
            </div>
            {results.results?.length === 0 && <Empty>The search returned no results.</Empty>}
            <table>
              <tbody>
                {results.results?.map((r: any) => (
                  <tr key={r.url}>
                    <td style={{ width: 28 }}>{r.rank}</td>
                    <td>
                      <b>{r.title || r.domain}</b>
                      <div className="mono muted">{r.url}</div>
                      <div>{r.snippet}</div>
                    </td>
                    <td><button className="sm" disabled={!canAct || fetchPage.isPending} onClick={() => fetchPage.mutate(r.url)}>Fetch page</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <input className="grow mono" placeholder="…or paste a page URL you already have (e.g. an ad or a post)" value={manualUrl} onChange={(e) => setManualUrl(e.target.value)} />
          <button className="ghost" disabled={!canAct || !inv || !manualUrl.trim() || fetchPage.isPending} onClick={() => fetchPage.mutate(manualUrl.trim())}>Fetch page</button>
        </div>
        <ErrorLine error={fetchPage.error} />
      </Step>

      <Step n={3} title="Fetched page → address candidates" hint="Deterministic extraction of Solana, EVM and Bitcoin addresses with short context. robots.txt is respected; X, Telegram, Instagram, Facebook, TikTok and LinkedIn are never fetched.">
        {!page ? <Empty>No page fetched yet.</Empty> : (
          <>
            <RunLine r={page} />
            {page.page && (
              <>
                <div>
                  <b>{page.page.title ?? "(no title)"}</b> <span className="mono muted">{page.page.final_url}</span>
                </div>
                <div className="muted">
                  {page.page.bytes_read} bytes{page.page.truncated ? " (truncated at the size limit; later content was not read)" : ""} · page body is not stored
                </div>
                {page.page.redirects?.length > 0 && <div className="muted">redirects: {page.page.redirects.join(" → ")}</div>}
                {page.page.address_candidates.length === 0 && <Empty>No blockchain address found on this page.</Empty>}
                {page.page.address_candidates.map((c: any) => {
                  const key = `${c.network}:${c.address}`;
                  const t = tracked[key];
                  const r = rationale[key] ?? "";
                  return (
                    <div key={key} className="ev">
                      <div className="row">
                        <Badge value={NET_LABEL[c.network] ?? c.network} />
                        <code>{c.address}</code>
                        <span className="muted">{c.occurrences}× on page</span>
                      </div>
                      {c.contexts.map((ctx: string, i: number) => <div key={i} className="muted" style={{ fontStyle: "italic" }}>“{ctx}”</div>)}
                      <div className="mono muted">{page.page.final_url}</div>
                      {t ? (
                        <div className="notice">
                          Tracked as wallet. Relationship <code>{t.relationship_type}</code> <Badge value={t.relationship_status} />. {t.note}
                        </div>
                      ) : (
                        <div className="row" style={{ marginTop: 6 }}>
                          <input className="grow" placeholder="Why track this address? (required, min 10 characters; recorded in the audit log)" value={r}
                            onChange={(e) => setRationale((x) => ({ ...x, [key]: e.target.value }))} />
                          <button className="sm" disabled={!canAct || r.trim().length < 10 || track.isPending} onClick={() => track.mutate(c)}>Track address</button>
                        </div>
                      )}
                    </div>
                  );
                })}
                <ErrorLine error={track.error} />
              </>
            )}
          </>
        )}
      </Step>

      <Step n={4} title="Tracked wallets → on-chain lookup" hint="Balance, recent transactions and counterparties from your configured node/explorer. Unavailable sources are reported, never replaced by scraping.">
        {wallets.length === 0 ? <Empty>No tracked wallets yet. Track an address in step 3.</Empty> : wallets.map((w) => {
          const o = onchain[w.entity_id];
          return (
            <div key={w.entity_id} className="ev">
              <div className="row">
                <Badge value={NET_LABEL[w.network] ?? w.network} /> <code>{w.address}</code>
                <Link href={`/graph?root=${w.entity_id}`}>graph</Link>
                <span className="grow" />
                <button className="sm" disabled={!canAct || lookup.isPending} onClick={() => lookup.mutate({ entity_id: w.entity_id, refresh: false })}>Look up on-chain</button>
                {o && <button className="ghost sm" disabled={!canAct || lookup.isPending} onClick={() => lookup.mutate({ entity_id: w.entity_id, refresh: true })}>Refresh</button>}
              </div>
              {o && (
                <>
                  <RunLine r={o} />
                  {o.reused_prior_run && <div className="muted">Reused a recent stored lookup (retrieved {when(o.retrieved_at)}); use Refresh to query the node again.</div>}
                  {o.view?.networks.map((n: any) => (
                    <div key={n.network} style={{ marginTop: 6 }}>
                      <div className="row">
                        <b>{n.network}</b>
                        {n.found === false && <span className="muted">address not seen on this network</span>}
                        {n.balance && <span>balance: <b>{n.balance}</b></span>}
                        {n.tx_count != null && <span className="muted">tx count/nonce {n.tx_count}</span>}
                        {n.has_code && <Badge value="contract" />}
                        {n.explorer_flags?.is_scam && <Badge value="explorer flags scam" />}
                      </div>
                      {n.error && <div className="err">{n.error}</div>}
                      {n.history_error && <div className="muted">history: {n.history_error}</div>}
                      {n.recent_transactions?.length > 0 && (
                        <table>
                          <thead><tr><th>Recent tx</th><th>Time</th><th>Direction / amount</th></tr></thead>
                          <tbody>
                            {n.recent_transactions.map((t: any) => (
                              <tr key={t.hash}>
                                <td className="mono">{short(t.hash, 18)}</td>
                                <td>{when(t.time)}</td>
                                <td>{t.direction ?? (t.failed ? "failed" : "")} {t.net_btc ? `${t.net_btc} BTC` : ""} {t.value_wei ? `${t.value_wei} wei` : ""}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                      {n.history_truncated && <div className="muted">History is truncated to the most recent transactions.</div>}
                      {n.counterparties?.length > 0 && (
                        <>
                          <h3>Counterparties (step 5: you choose)</h3>
                          <div className="notice">Other addresses seen in the same transactions. Exchanges and contracts appear for many unrelated parties; this is not ownership.</div>
                          <table>
                            <tbody>
                              {n.counterparties.map((c: any) => {
                                const k = `${w.entity_id}|${n.network}|${c.address}|${c.direction}`;
                                return (
                                  <tr key={k}>
                                    <td style={{ width: 28 }}><input type="checkbox" checked={!!picked[k]} onChange={(e) => setPicked((p) => ({ ...p, [k]: e.target.checked }))} /></td>
                                    <td className="mono">{c.address}</td>
                                    <td>{c.direction.replace(/_/g, " ")} · {c.tx_count} tx {c.is_contract ? <Badge value="contract" /> : null}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                          <div className="row">
                            <button className="sm" disabled={!canAct || expand.isPending || !Object.keys(picked).some((k) => k.startsWith(`${w.entity_id}|`) && picked[k])}
                              onClick={() => {
                                const list = Object.keys(picked).filter((k) => picked[k] && k.startsWith(`${w.entity_id}|`)).map((k) => {
                                  const [, network, address, direction] = k.split("|");
                                  return { network, address, direction };
                                });
                                expand.mutate({ entity_id: w.entity_id, lookup_run_id: o.lookup_run_id, list });
                              }}>Add selected as related wallets</button>
                            <span className="muted">creates WALLET_TRANSACTED_WITH (a transaction fact), not an ownership link</span>
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                  {expanded[w.entity_id] && (
                    <div className="notice">
                      Added {expanded[w.entity_id].added.length} wallet(s){expanded[w.entity_id].skipped.length ? `, skipped ${expanded[w.entity_id].skipped.length}` : ""}.{" "}
                      {expanded[w.entity_id].note}
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
        <ErrorLine error={lookup.error || expand.error} />
      </Step>

      {state.data && state.data.relationships.length > 0 && (
        <Step n={5} title="Relationships created in this session" hint="Click through to the Knowledge Graph for evidence and provenance.">
          <table>
            <thead><tr><th>Type</th><th>Status</th></tr></thead>
            <tbody>
              {state.data.relationships.map((r: any) => (
                <tr key={r.id}><td><code>{r.type}</code></td><td><Badge value={r.status} /></td></tr>
              ))}
            </tbody>
          </table>
        </Step>
      )}
    </Page>
  );
}
