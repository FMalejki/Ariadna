"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";

const NAV: { title: string | null; items: [string, string][] }[] = [
  { title: null, items: [["/", "Overview"]] },
  { title: "Collect", items: [["/leads", "Lead Search"], ["/automation", "Automation"], ["/discovery", "Discovery"]] },
  { title: "Explore", items: [["/graph", "Knowledge Graph"], ["/entities", "Entities"], ["/sources", "Sources"], ["/investigations", "Investigations"]] },
  { title: "Review", items: [["/evidence", "Evidence Center"], ["/kb", "Intelligence KB"]] },
  { title: "System", items: [["/providers", "Providers"], ["/settings", "Settings"]] },
];

/** Sign-in illustration: one thread through mentions. Solid = checked link, dashed = unverified lead. */
function Thread() {
  return (
    <svg viewBox="0 0 560 220" role="img" aria-label="A thread linking a mention, an address and a transaction. The last link is dashed because it is only a lead.">
      <path d="M20 160 C 90 160, 90 60, 170 60 S 260 170, 330 130" fill="none" stroke="#2b3a9e" strokeWidth="3" />
      <path d="M330 130 C 390 100, 420 40, 540 52" fill="none" stroke="#2b3a9e" strokeWidth="3" strokeDasharray="7 6" />
      <g fill="#fff" stroke="#2b3a9e" strokeWidth="3">
        <circle cx="20" cy="160" r="9" />
        <circle cx="170" cy="60" r="9" />
        <circle cx="330" cy="130" r="9" />
      </g>
      <circle cx="540" cy="52" r="9" fill="#e8ecee" stroke="#5b6773" strokeWidth="3" strokeDasharray="3 3" />
      <g fontSize="13" fill="#17212b" fontFamily="IBM Plex Sans, system-ui, sans-serif">
        <text x="20" y="190" textAnchor="start">mention</text>
        <text x="170" y="38" textAnchor="middle">address</text>
        <text x="330" y="160" textAnchor="middle">transaction</text>
        <text x="540" y="82" textAnchor="end">lead, not verified</text>
      </g>
    </svg>
  );
}

function Login() {
  const { signIn } = useAuth();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await signIn(u, p);
    } catch (x) {
      setErr(x instanceof Error ? x.message : "Sign-in failed. Check the username and password.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="signin">
      <section className="signin-art" aria-label="About Ariadna">
        <div>
          <h1>Ariadna</h1>
          <p>Follow a mention to an address and a transaction, and see the evidence behind every link.</p>
        </div>
        <Thread />
      </section>
      <section className="signin-form">
        <form onSubmit={submit}>
          <h2>Sign in</h2>
          <label>
            Username
            <input name="username" autoComplete="username" spellCheck={false} autoCapitalize="none" value={u} onChange={(e) => setU(e.target.value)} autoFocus />
          </label>
          <label>
            Password
            <input name="password" type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} />
          </label>
          {err && <div className="err" role="alert">{err}</div>}
          <button disabled={busy || !u || !p}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </section>
    </div>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const { session, ready, signOut } = useAuth();
  const path = usePathname();
  if (!ready) return null;
  if (!session) return <Login />;
  return (
    <div className="shell">
      <a className="skip" href="#content">Skip to content</a>
      <nav className="nav" aria-label="Primary">
        <Link href="/" className="brand">
          <b>Ariadna</b>
          <small>Evidence-first OSINT workspace</small>
        </Link>
        {NAV.map((g) => (
          <div key={g.title ?? "top"} className="nav-group">
            {g.title && <h4>{g.title}</h4>}
            <ul>
              {g.items.map(([href, label]) => {
                const on = href === "/" ? path === "/" : path.startsWith(href);
                return (
                  <li key={href}>
                    <Link href={href} className={on ? "active" : ""} aria-current={on ? "page" : undefined}>{label}</Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
        <div className="foot">
          Signed in as <b translate="no">{session.username}</b> ({session.role})
          <br />
          <button type="button" className="ghost sm" onClick={signOut}>Sign out</button>
        </div>
      </nav>
      <main className="main" id="content">{children}</main>
    </div>
  );
}
