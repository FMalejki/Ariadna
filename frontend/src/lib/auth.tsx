"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { loadSession, login as apiLogin, saveSession, ROLE_RANK, type Role, type Session } from "./api";

interface AuthCtx {
  session: Session | null;
  ready: boolean;
  signIn: (u: string, p: string) => Promise<void>;
  signOut: () => void;
  can: (min: Role) => boolean;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setSession(loadSession());
    setReady(true);
    const onChange = () => setSession(loadSession());
    window.addEventListener("osint-auth-changed", onChange);
    return () => window.removeEventListener("osint-auth-changed", onChange);
  }, []);

  const signIn = useCallback(async (u: string, p: string) => {
    const s = await apiLogin(u, p);
    saveSession(s);
    setSession(s);
  }, []);
  const signOut = useCallback(() => {
    saveSession(null);
    setSession(null);
  }, []);
  const can = useCallback((min: Role) => !!session && ROLE_RANK[session.role] >= ROLE_RANK[min], [session]);

  return <Ctx.Provider value={{ session, ready, signIn, signOut, can }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
