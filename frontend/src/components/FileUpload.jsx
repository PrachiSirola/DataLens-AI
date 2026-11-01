import { useState, useRef, useCallback } from "react";
import { Upload, X, FileText, FileSpreadsheet, File, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { api } from "../api/client";

const FILE_ICONS = {
  csv: FileSpreadsheet,
  xlsx: FileSpreadsheet,
  xls: FileSpreadsheet,
  pdf: FileText,
  docx: File,
};

export default function FileUpload({ onUploadComplete }) {
  const [dragActive, setDragActive] = useState(false);
  const [uploads, setUploads] = useState([]); // { id, file, status, error, dataset }
  const inputRef = useRef(null);

  const handleFiles = useCallback(
    (files) => {
      const newUploads = Array.from(files).map((file) => ({
        id: crypto.randomUUID(),
        file,
        status: "uploading",
        error: null,
        dataset: null,
      }));
      setUploads((prev) => [...prev, ...newUploads]);

      newUploads.forEach(async (upload) => {
        try {
          const dataset = await api.upload("/datasets/upload", upload.file);
          setUploads((prev) =>
            prev.map((u) =>
              u.id === upload.id
                ? { ...u, status: "done", dataset }
                : u
            )
          );
          onUploadComplete?.(dataset);
        } catch (err) {
          setUploads((prev) =>
            prev.map((u) =>
              u.id === upload.id
                ? { ...u, status: "error", error: err.message }
                : u
            )
          );
        }
      });
    },
    [onUploadComplete]
  );

  const handleDrop = (e) => {
    e.preventDefault();
    setDragActive(false);
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setDragActive(true);
  };

  const removeUpload = (id) => {
    setUploads((prev) => prev.filter((u) => u.id !== id));
  };

  const ext = (name) => name.split(".").pop().toLowerCase();

  return (
    <div className="space-y-3">
      {/* Drop zone */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={() => setDragActive(false)}
        onClick={() => inputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors ${
          dragActive
            ? "border-emerald-400 bg-emerald-50"
            : "border-slate-200 hover:border-emerald-300 hover:bg-slate-50"
        }`}
      >
        <Upload className="h-8 w-8 mx-auto text-slate-400 mb-2" />
        <p className="text-sm font-medium text-slate-700">
          Drop files here or click to browse
        </p>
        <p className="text-xs text-slate-400 mt-1">
          CSV, Excel (.xlsx, .xls) — up to 50 MB each
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".csv,.xlsx,.xls"
          className="hidden"
          onChange={(e) => {
            if (e.target.files.length) handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {/* Upload list */}
      {uploads.length > 0 && (
        <div className="space-y-2">
          {uploads.map((u) => {
            const Icon = FILE_ICONS[ext(u.file.name)] || File;
            return (
              <div
                key={u.id}
                className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm"
              >
                <Icon className="h-4 w-4 text-slate-400 shrink-0" />
                <span className="truncate flex-1 text-slate-700">
                  {u.file.name}
                </span>
                <span className="text-xs text-slate-400">
                  {(u.file.size / 1024).toFixed(0)} KB
                </span>
                {u.status === "uploading" && (
                  <Loader2 className="h-4 w-4 animate-spin text-emerald-500" />
                )}
                {u.status === "done" && (
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                )}
                {u.status === "error" && (
                  <span className="flex items-center gap-1 text-xs text-red-500">
                    <AlertCircle className="h-4 w-4" />
                    {u.error}
                  </span>
                )}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeUpload(u.id);
                  }}
                  className="text-slate-300 hover:text-slate-500 cursor-pointer"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
