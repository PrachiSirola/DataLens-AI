import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import Layout from "../components/Layout";
import {
  Plus,
  Loader2,
  Copy,
  Check,
  Ban,
  KeyRound,
  Clock,
  Users,
} from "lucide-react";

export default function AdminInvitations() {
  const [codes, setCodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    code: "",
    max_uses: 1,
    expires_in_hours: "",
    note: "",
  });

  const fetchCodes = useCallback(async () => {
    try {
      const data = await api.get("/admin/invitations?include_inactive=true");
      setCodes(data.codes);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCodes();
  }, [fetchCodes]);

  const handleCreate = async (e) => {
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      await api.post("/admin/invitations", {
        code: form.code || null,
        max_uses: Number(form.max_uses) || 1,
        expires_in_hours: form.expires_in_hours
          ? Number(form.expires_in_hours)
          : null,
        note: form.note,
      });
      setForm({ code: "", max_uses: 1, expires_in_hours: "", note: "" });
      setShowForm(false);
      await fetchCodes();
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (id) => {
    if (!confirm("Revoke this invitation code?")) return;
    try {
      await api.patch(`/admin/invitations/${id}/revoke`);
      await fetchCodes();
    } catch (err) {
      setError(err.message);
    }
  };

  const copyCode = (code, id) => {
    navigator.clipboard.writeText(code);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const set = (field) => (e) => setForm((p) => ({ ...p, [field]: e.target.value }));

  return (
    <Layout>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Invitation Codes</h1>
          <p className="text-slate-500 text-sm mt-1">
            Manage who can register for the platform
          </p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 transition cursor-pointer"
        >
          <Plus className="h-4 w-4" />
          New Code
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3 mb-4">
          {error}
        </div>
      )}

      {/* Create form */}
      {showForm && (
        <form
          onSubmit={handleCreate}
          className="bg-white rounded-xl border border-slate-200 p-6 mb-6 grid grid-cols-1 sm:grid-cols-2 gap-4"
        >
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Code (leave empty to auto-generate)
            </label>
            <input
              type="text"
              value={form.code}
              onChange={set("code")}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-emerald-500 transition placeholder:text-slate-400"
              placeholder="AUTO-GENERATED"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Max uses
            </label>
            <input
              type="number"
              min={1}
              max={10000}
              value={form.max_uses}
              onChange={set("max_uses")}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 transition"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Expires in (hours, optional)
            </label>
            <input
              type="number"
              min={1}
              max={8760}
              value={form.expires_in_hours}
              onChange={set("expires_in_hours")}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 transition placeholder:text-slate-400"
              placeholder="Never expires"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Note
            </label>
            <input
              type="text"
              value={form.note}
              onChange={set("note")}
              maxLength={200}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 transition placeholder:text-slate-400"
              placeholder="For new hires, team beta, etc."
            />
          </div>
          <div className="sm:col-span-2 flex justify-end gap-3">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50 transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={creating}
              className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-60 transition cursor-pointer"
            >
              {creating && <Loader2 className="h-4 w-4 animate-spin" />}
              Create
            </button>
          </div>
        </form>
      )}

      {/* Codes table */}
      {loading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-emerald-600" />
        </div>
      ) : codes.length === 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-slate-500">
          No invitation codes yet. Create one above.
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-slate-600">Code</th>
                  <th className="text-left px-4 py-3 font-medium text-slate-600">Usage</th>
                  <th className="text-left px-4 py-3 font-medium text-slate-600">Expires</th>
                  <th className="text-left px-4 py-3 font-medium text-slate-600">Note</th>
                  <th className="text-left px-4 py-3 font-medium text-slate-600">Status</th>
                  <th className="text-right px-4 py-3 font-medium text-slate-600">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {codes.map((c) => {
                  const expired =
                    c.expires_at && new Date(c.expires_at) < new Date();
                  const exhausted = c.current_uses >= c.max_uses;
                  const active = c.is_active && !expired && !exhausted;

                  return (
                    <tr key={c.id} className={!active ? "opacity-60" : ""}>
                      <td className="px-4 py-3 font-mono text-xs">
                        <span className="flex items-center gap-2">
                          <KeyRound className="h-3.5 w-3.5 text-emerald-500" />
                          {c.code}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="flex items-center gap-1 text-xs">
                          <Users className="h-3.5 w-3.5 text-slate-400" />
                          {c.current_uses} / {c.max_uses}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs">
                        {c.expires_at ? (
                          <span className="flex items-center gap-1">
                            <Clock className="h-3.5 w-3.5 text-slate-400" />
                            {new Date(c.expires_at).toLocaleDateString()}
                          </span>
                        ) : (
                          <span className="text-slate-400">Never</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-500 max-w-[200px] truncate">
                        {c.note || "—"}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                            active
                              ? "bg-emerald-50 text-emerald-700"
                              : expired
                              ? "bg-amber-50 text-amber-700"
                              : exhausted
                              ? "bg-slate-100 text-slate-600"
                              : "bg-red-50 text-red-700"
                          }`}
                        >
                          {active
                            ? "Active"
                            : expired
                            ? "Expired"
                            : exhausted
                            ? "Used up"
                            : "Revoked"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => copyCode(c.code, c.id)}
                            className="p-1.5 rounded-md hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition cursor-pointer"
                            title="Copy code"
                          >
                            {copiedId === c.id ? (
                              <Check className="h-4 w-4 text-emerald-500" />
                            ) : (
                              <Copy className="h-4 w-4" />
                            )}
                          </button>
                          {active && (
                            <button
                              onClick={() => handleRevoke(c.id)}
                              className="p-1.5 rounded-md hover:bg-red-50 text-slate-400 hover:text-red-600 transition cursor-pointer"
                              title="Revoke"
                            >
                              <Ban className="h-4 w-4" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Layout>
  );
}
