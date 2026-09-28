import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, getToken, setToken, ApiError } from "../services/api";
import type { User } from "../types";

interface AuthState {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, name?: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** true when the user dismissed onboarding for this session */
  onboardingDone: boolean;
  completeOnboarding: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(!!getToken());
  const [onboardingDone, setOnboardingDone] = useState<boolean>(() => {
    try {
      return localStorage.getItem("dedan_onboarded") === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    api
      .me()
      .then((out) => {
        if (!cancelled) setUser(out.user);
      })
      .catch((err: unknown) => {
        // Token invalid/expired — clear silently, stay public.
        if (err instanceof ApiError && err.status === 401) setToken(null);
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const out = await api.login(email, password);
    setUser(out.user);
  }, []);

  const signUp = useCallback(
    async (email: string, password: string, name?: string) => {
      const out = await api.register(email, password, name);
      setUser(out.user);
    },
    [],
  );

  const signOut = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  const completeOnboarding = useCallback(() => {
    setOnboardingDone(true);
    try {
      localStorage.setItem("dedan_onboarded", "1");
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      signIn,
      signUp,
      signOut,
      onboardingDone,
      completeOnboarding,
    }),
    [user, loading, signIn, signUp, signOut, onboardingDone,
     completeOnboarding],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
