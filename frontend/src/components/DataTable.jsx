import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";

const PAGE_SIZE = 15;

export default function DataTable({ columns, rows, title }) {
  const [page, setPage] = useState(0);
  if (!rows || rows.length === 0) return null;

  const cols = columns || Object.keys(rows[0]);
  const totalPages = Math.ceil(rows.length / PAGE_SIZE);
  const pageRows = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
      {title && (
        <div className="px-4 py-2 border-b border-slate-100 bg-slate-50">
          <h3 className="text-sm font-medium text-slate-700">{title}</h3>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200">
              {cols.map((col) => (
                <th
                  key={col}
                  className="px-3 py-2 text-left text-xs font-semibold text-slate-600 whitespace-nowrap"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, i) => (
              <tr
                key={i}
                className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors"
              >
                {cols.map((col) => (
                  <td
                    key={col}
                    className="px-3 py-2 text-slate-700 whitespace-nowrap max-w-[200px] truncate"
                    title={String(row[col] ?? "")}
                  >
                    {row[col] ?? "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-slate-100 text-xs text-slate-500">
          <span>
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, rows.length)} of {rows.length} rows
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="p-1 rounded hover:bg-slate-100 disabled:opacity-30 cursor-pointer disabled:cursor-default"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="p-1 rounded hover:bg-slate-100 disabled:opacity-30 cursor-pointer disabled:cursor-default"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
