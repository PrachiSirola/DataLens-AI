import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { LogOut, BarChart3, Shield } from "lucide-react";

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Top nav */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-14">
          <Link to="/dashboard" className="flex items-center gap-2 font-semibold text-slate-900 hover:text-emerald-600 transition-colors">
            <img src="/logo.png" alt="DataLensAI" className="h-7 w-7" />
            <span>DataLensAI</span>
          </Link>

          <div className="flex items-center gap-4">
            {user?.role === "admin" && (
              <Link
                to="/admin/invitations"
                className="flex items-center gap-1.5 text-sm text-slate-600 hover:text-emerald-600 transition-colors"
              >
                <Shield className="h-4 w-4" />
                Admin
              </Link>
            )}
            <div className="flex items-center gap-2 text-sm text-slate-600">
              <div className="h-7 w-7 rounded-full bg-emerald-100 text-emerald-700 flex items-center justify-center text-xs font-semibold">
                {user?.name?.[0]?.toUpperCase() || "?"}
              </div>
              <span className="hidden sm:inline">{user?.name}</span>
            </div>
            <button
              onClick={handleLogout}
              className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-red-600 transition-colors cursor-pointer"
            >
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline">Logout</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {children}
      </main>
    </div>
  );
}
