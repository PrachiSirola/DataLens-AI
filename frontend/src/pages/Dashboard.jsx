import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../contexts/AuthContext";
import Layout from "../components/Layout";
import FileUpload from "../components/FileUpload";
import DatasetList from "../components/DatasetList";
import ChatInterface from "../components/ChatInterface";
import DataTable from "../components/DataTable";
import { Loader2, X, ChevronDown, ChevronUp } from "lucide-react";

export default function Dashboard() {
  const { user } = useAuth();
  const [datasets, setDatasets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Fetch datasets on mount
  const fetchDatasets = useCallback(async () => {
    try {
      const res = await api.get("/datasets/");
      setDatasets(res.datasets);
    } catch {
      // silent on initial load
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDatasets();
  }, [fetchDatasets]);

  // Called after each successful upload
  const handleUploadComplete = useCallback(() => {
    fetchDatasets();
  }, [fetchDatasets]);

  const handleDelete = useCallback(async (id) => {
    setDeletingId(id);
    try {
      await api.delete(`/datasets/${id}`);
      setDatasets((prev) => prev.filter((d) => d.id !== id));
      if (preview?.dataset_id === id) setPreview(null);
    } catch {
      // handled silently
    } finally {
      setDeletingId(null);
    }
  }, [preview]);

  const handlePreview = useCallback(async (ds) => {
    setPreviewLoading(true);
    try {
      const res = await api.get(`/datasets/${ds.id}/preview?limit=30`);
      setPreview(res);
    } catch {
      // handled silently
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  if (loading) {
    return (
      <Layout>
        <div className="flex justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-emerald-600" />
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="flex gap-6 items-start">
        {/* Sidebar — upload + datasets */}
        <div className={`shrink-0 transition-all duration-200 ${sidebarOpen ? "w-72" : "w-0 overflow-hidden"}`}>
          <div className="space-y-5">
            {/* Welcome */}
            <div>
              <h1 className="text-lg font-bold text-slate-900">
                Hi, {user?.name} 👋
              </h1>
              <p className="text-xs text-slate-500 mt-0.5">
                Upload files and ask questions
              </p>
            </div>

            {/* Upload zone */}
            <FileUpload onUploadComplete={handleUploadComplete} />

            {/* Dataset list */}
            <DatasetList
              datasets={datasets}
              onDelete={handleDelete}
              onPreview={handlePreview}
              deletingId={deletingId}
            />
          </div>
        </div>

        {/* Toggle sidebar on mobile */}
        <button
          onClick={() => setSidebarOpen((p) => !p)}
          className="lg:hidden shrink-0 mt-2 p-1 rounded-lg border border-slate-200 text-slate-400 hover:text-slate-600 cursor-pointer"
        >
          {sidebarOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>

        {/* Main area — chat + preview */}
        <div className="flex-1 min-w-0 space-y-4">
          {/* Preview overlay */}
          {preview && (
            <div className="relative">
              <button
                onClick={() => setPreview(null)}
                className="absolute top-2 right-2 z-10 p-1 rounded-full bg-white border border-slate-200 text-slate-400 hover:text-slate-700 cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
              {previewLoading ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
                </div>
              ) : (
                <DataTable
                  columns={preview.columns}
                  rows={preview.rows}
                  title={`${preview.table_name} — preview (${preview.preview_rows} of ${preview.total_rows?.toLocaleString()} rows)`}
                />
              )}
            </div>
          )}

          {/* Chat interface */}
          <ChatInterface datasets={datasets} />
        </div>
      </div>
    </Layout>
  );
}
