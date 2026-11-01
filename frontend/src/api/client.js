/**
 * Thin API client that wraps fetch with credentials and JSON handling.
 */

const BASE = "/api";

async function request(path, options = {}) {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    credentials: "include", // always send cookies
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  // Handle 204 No Content
  if (res.status === 204) return null;

  const data = await res.json().catch(() => null);

  if (!res.ok) {
    let msg = data?.detail;
    if (Array.isArray(msg)) msg = msg.map((e) => e.msg || e.message).join("; ");
    msg = msg || `Request failed (${res.status})`;
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }

  return data;
}

export const api = {
  get: (path) => request(path, { method: "GET" }),
  post: (path, body) =>
    request(path, { method: "POST", body: JSON.stringify(body) }),
  patch: (path, body) =>
    request(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: (path) => request(path, { method: "DELETE" }),

  /** Upload a file via multipart/form-data. */
  upload: async (path, file) => {
    const form = new FormData();
    form.append("file", file);
    const url = `${BASE}${path}`;
    const res = await fetch(url, {
      method: "POST",
      credentials: "include",
      body: form,
      // Do NOT set Content-Type — browser sets the boundary automatically
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const msg = data?.detail || `Upload failed (${res.status})`;
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return data;
  },
};