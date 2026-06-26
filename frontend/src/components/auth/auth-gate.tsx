"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Lock, LogIn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getCurrentAuth,
  login as loginRequest,
  logout as logoutRequest,
  type AuthUser,
} from "@/lib/api";

type AuthContextValue = {
  user: AuthUser | null;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthGate");
  return value;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshAuth = useCallback(async () => {
    try {
      const auth = await getCurrentAuth();
      setUser(auth.user);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  useEffect(() => {
    const handleExpired = () => setUser(null);
    window.addEventListener("miao-auth-expired", handleExpired);
    return () => window.removeEventListener("miao-auth-expired", handleExpired);
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const auth = await loginRequest({ username, password });
      setUser(auth.user);
      setPassword("");
    } catch {
      setError("账号或密码不正确");
    } finally {
      setSubmitting(false);
    }
  }

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo(() => ({ user, logout }), [user, logout]);

  if (loading) {
    return (
      <div className="auth-shell">
        <div className="auth-panel">
          <div className="skeleton h-9 w-9 rounded-[var(--radius-md)]" />
          <div className="skeleton h-5 w-36" />
          <div className="skeleton h-9 w-full" />
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="auth-shell">
        <form className="auth-panel" onSubmit={handleSubmit}>
          <div className="auth-mark">
            <Lock className="h-5 w-5" />
          </div>
          <div>
            <h1 className="auth-title">Miao AI</h1>
            <p className="auth-subtitle">控制台登录</p>
          </div>

          <div className="auth-fields">
            <div className="form-row">
              <Label htmlFor="username">账号</Label>
              <Input
                id="username"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                disabled={submitting}
                required
              />
            </div>
            <div className="form-row">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={submitting}
                required
              />
            </div>
          </div>

          {error && <p className="auth-error">{error}</p>}

          <Button type="submit" className="w-full" disabled={submitting}>
            <LogIn />
            {submitting ? "登录中" : "登录"}
          </Button>
        </form>
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
