import { Trash2, FileSpreadsheet, FileText, File, Eye, Loader2, AlertCircle } from "lucide-react";

const FILE_ICONS = {
  csv: FileSpreadsheet,
  pdf: FileText,
  docx: File,
};

export default function DatasetList({ datasets, onDelete, onPreview, deletingId }) {
  if (!datasets || datasets.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Your Datasets ({datasets.length})
      </h3>
      {datasets.map((ds) => {
        const Icon = FILE_ICONS[ds.file_type] || File;
        const isReady = ds.status === "ready";
        const isError = ds.status === "error";

        return (
          <div
            key={ds.id}
            className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-3 py-2 text-sm group"
          >
            <Icon className={`h-4 w-4 shrink-0 ${isError ? "text-red-400" : "text-slate-400"}`} />
            <div className="flex-1 min-w-0">
              <p className="text-slate-700 truncate font-medium text-xs">
                {ds.original_filename}
              </p>
              <p className="text-xs text-slate-400">
                {isReady && `${ds.row_count?.toLocaleString()} rows · ${ds.column_count} cols`}
                {isError && (
                  <span className="text-red-500 flex items-center gap-1">
                    <AlertCircle className="h-3 w-3" />
                    {ds.error_message || "Processing failed"}
                  </span>
                )}
                {ds.status === "processing" && (
                  <span className="text-emerald-500 flex items-center gap-1">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Processing...
                  </span>
                )}
              </p>
            </div>

            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              {isReady && (
                <button
                  onClick={() => onPreview?.(ds)}
                  className="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-emerald-600 cursor-pointer"
                  title="Preview data"
                >
                  <Eye className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                onClick={() => onDelete?.(ds.id)}
                disabled={deletingId === ds.id}
                className="p-1 rounded hover:bg-red-50 text-slate-400 hover:text-red-500 cursor-pointer disabled:opacity-50"
                title="Delete dataset"
              >
                {deletingId === ds.id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
