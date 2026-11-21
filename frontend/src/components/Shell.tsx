"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";

const NAV: [string, string][] = [
  ["/", "Overview"],
  ["/leads", "Lead Search"],
  ["/automation", "Automation"],
  ["/discovery", "Discovery"],
  ["/graph", "Knowledge Graph"],
  ["/entities", "Entities"],
  ["/sources", "Sources"],
  ["/investigations", "Investigations"],
  ["/providers", "Providers"],
  ["/evidence", "Evidence Center"],
  ["/kb", "Intelligence KB"],
  ["/settings", "Settings"],
];

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
      setErr(x instanceof Error ? x.message : "login failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>OSINT Memory</h1>
        <div className="muted">Sign in to the evidence-first intelligence workspace.</div>
        <input placeholder="username" value={u} onChange={(e) => setU(e.target.value)} autoFocus />
        <input placeholder="password" type="password" value={p} onChange={(e) => setP(e.target.value)} />
        {err && <div className="err">{err}</div>}
        <button disabled={busy || !u || !p}>Sign in</button>
      </form>
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
      <nav className="nav" aria-label="Primary">
        <div className="brand">
          OSINT Memory<small>evidence-first · persistent</small>
        </div>
        {NAV.map(([href, label]) => (
          <Link key={href} href={href} className={(href === "/" ? path === "/" : path.startsWith(href)) ? "active" : ""}>
            {label}
          </Link>
        ))}
        <div className="foot">
          {session.username} · {session.role}
          <br />
          <a href="#" onClick={(e) => { e.preventDefault(); signOut(); }}>Sign out</a>
        </div>
      </nav>
      <main className="main">{children}</main>
    </div>
  );
}
