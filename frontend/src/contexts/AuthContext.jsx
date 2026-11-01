import { createContext, useContext, useState, useEffect, useCallback } from "react";
import { api } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true); // initial auth check

  // Check existing session on mount
  useEffect(() => {
    api
      .get("/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const register = useCallback(async ({ email, password, name, invitation_code }) => {
    const data = await api.post("/auth/register", {
      email,
      password,
      name,
      invitation_code,
    });
    setUser(data.user);
    return data;
  }, []);

  const login = useCallback(async ({ email, password }) => {
    const data = await api.post("/auth/login", { email, password });
    setUser(data.user);
    return data;
  }, []);

  const logout = useCallback(async () => {
    await api.post("/auth/logout");
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, register, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}
