import { useState, useRef, useEffect } from "react";
import { Send, Loader2, Bot, User, Code2, Table2, BarChart3, Download, Trash2, BookOpen, FileText } from "lucide-react";
import { api } from "../api/client";
import QueryChart from "./QueryChart";
import DataTable from "./DataTable";

export default function ChatInterface({ datasets }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  const hasDatasets = datasets && datasets.some((d) => d.status === "ready");

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setLoading(true);

    try {
      const result = await api.post("/query/", { question });
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.answer,
          sql: result.sql_generated,
          data: result.data,
          chart_config: result.chart_config,
          row_count: result.row_count,
          query_type: result.query_type,
          sources: result.sources,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${err.message}`, isError: true },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const exportCSV = (data) => {
    if (!data || data.length === 0) return;
    const cols = Object.keys(data[0]);
    const csv = [
      cols.join(","),
      ...data.map((row) =>
        cols.map((c) => {
          const v = row[c] ?? "";
          return typeof v === "string" && v.includes(",") ? `"${v}"` : v;
        }).join(",")
      ),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "query-results.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl flex flex-col" style={{ height: "calc(100vh - 220px)", minHeight: 400 }}>
      {/* Chat messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center py-12">
            <Bot className="h-12 w-12 text-slate-200 mb-3" />
            <h3 className="text-base font-semibold text-slate-600">
              {hasDatasets
                ? "Ask a question about your data"
                : "Upload a file to get started"}
            </h3>
            <p className="text-sm text-slate-400 mt-1 max-w-sm">
              {hasDatasets
                ? 'Try "How many rows are there?" or "Show the top 5 records"'
                : "Upload a CSV or Excel file using the panel on the left, then ask questions here."}
            </p>
            {hasDatasets && (
              <div className="flex flex-wrap justify-center gap-2 mt-4">
                {[
                  "How many rows are there?",
                  "Show all columns",
                  "What are the column names?",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => {
                      setInput(q);
                      inputRef.current?.focus();
                    }}
                    className="px-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-full text-slate-600 hover:bg-emerald-50 hover:border-emerald-200 hover:text-emerald-700 transition-colors cursor-pointer"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : ""}`}>
            {msg.role === "assistant" && (
              <div className="h-7 w-7 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center shrink-0 mt-0.5">
                <Bot className="h-4 w-4" />
              </div>
            )}
            <div className={`max-w-[85%] space-y-3 ${msg.role === "user" ? "order-first" : ""}`}>
              {/* Query type badge */}
              {msg.query_type && msg.query_type !== "sql" && (
                <div className="flex items-center gap-1.5">
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                    msg.query_type === "knowledge"
                      ? "bg-purple-50 text-purple-600 border border-purple-200"
                      : "bg-blue-50 text-blue-600 border border-blue-200"
                  }`}>
                    <BookOpen className="h-3 w-3" />
                    {msg.query_type === "knowledge" ? "Document Q&A" : "Hybrid"}
                  </span>
                </div>
              )}

              {/* Text bubble */}
              <div
                className={`rounded-xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
                  msg.role === "user"
                    ? "bg-emerald-600 text-white ml-auto"
                    : msg.isError
                    ? "bg-red-50 text-red-700 border border-red-200"
                    : "bg-slate-50 text-slate-700"
                }`}
              >
                {msg.content}
              </div>

              {/* Source references for knowledge answers */}
              {msg.sources && msg.sources.length > 0 && (
                <details className="group" open>
                  <summary className="flex items-center gap-1.5 text-xs text-purple-500 cursor-pointer hover:text-purple-700 select-none font-medium">
                    <FileText className="h-3.5 w-3.5" />
                    Sources ({msg.sources.length} page{msg.sources.length !== 1 ? "s" : ""})
                  </summary>
                  <div className="mt-2 space-y-1.5">
                    {msg.sources.map((src, j) => (
                      <div
                        key={j}
                        className="bg-purple-50 border border-purple-100 rounded-lg px-3 py-2 text-xs"
                      >
                        <span className="font-semibold text-purple-700">
                          Page {src.page}
                        </span>
                        <p className="text-purple-600 mt-0.5 line-clamp-2">
                          {src.excerpt}
                        </p>
                      </div>
                    ))}
                  </div>
                </details>
              )}

              {/* SQL collapsible */}
              {msg.sql && (
                <details className="group">
                  <summary className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer hover:text-slate-600 select-none">
                    <Code2 className="h-3.5 w-3.5" />
                    View SQL query
                  </summary>
                  <pre className="mt-1 bg-slate-900 text-slate-200 text-xs p-3 rounded-lg overflow-x-auto">
                    {msg.sql}
                  </pre>
                </details>
              )}

              {/* Chart */}
              {msg.chart_config && msg.data && (
                <QueryChart data={msg.data} config={msg.chart_config} />
              )}

              {/* Data table */}
              {msg.data && msg.data.length > 0 && (
                <div className="space-y-2">
                  <DataTable
                    rows={msg.data}
                    title={`Results (${msg.row_count ?? msg.data.length} rows)`}
                  />
                  <button
                    onClick={() => exportCSV(msg.data)}
                    className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-emerald-600 transition-colors cursor-pointer"
                  >
                    <Download className="h-3.5 w-3.5" />
                    Export as CSV
                  </button>
                </div>
              )}
            </div>

            {msg.role === "user" && (
              <div className="h-7 w-7 rounded-full bg-slate-200 text-slate-600 flex items-center justify-center shrink-0 mt-0.5">
                <User className="h-4 w-4" />
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex gap-3">
            <div className="h-7 w-7 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center shrink-0">
              <Bot className="h-4 w-4" />
            </div>
            <div className="bg-slate-50 rounded-xl px-4 py-3 text-sm text-slate-500 flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Analyzing your data...
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-slate-200 p-3 flex gap-2"
      >
        {messages.length > 0 && (
          <button
            type="button"
            onClick={() => setMessages([])}
            title="Clear chat"
            className="text-slate-400 hover:text-red-500 transition-colors rounded-lg px-2 py-2 cursor-pointer"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            hasDatasets
              ? "Ask about your data..."
              : "Upload a CSV or Excel file first to start querying"
          }
          disabled={!hasDatasets || loading}
          className="flex-1 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-transparent disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!input.trim() || loading || !hasDatasets}
          className="bg-emerald-600 text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-default transition-colors flex items-center gap-1.5 cursor-pointer"
        >
          <Send className="h-4 w-4" />
        </button>
      </form>
      {/* AI disclaimer */}
      <p className="text-center text-[11px] text-slate-400 px-3 pb-2">
        DataLensAI is built by humans and can make mistakes — just like humans do.
      </p>

    </div>
  );
}
