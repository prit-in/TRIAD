import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { SignIn, useAuth } from "./auth";
import { Dashboard } from "./pages/Dashboard";
import { Runs, RunDetailPage } from "./pages/Runs";
import { Models } from "./pages/Models";
import { Predictions } from "./pages/Predictions";
import { Approvals } from "./pages/Approvals";

const PAGES = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/runs", label: "Runs", end: false },
  { to: "/models", label: "Models", end: false },
  { to: "/predictions", label: "Predictions", end: false },
  { to: "/approvals", label: "Approvals", end: false },
];

export function App() {
  const { user, loading, signOut } = useAuth();

  if (loading) return <div className="loading">Checking your session…</div>;
  if (!user) return <SignIn />;

  return (
    <div className="shell">
      <header>
        <span className="brand">TRIAD</span>
        <nav>
          {PAGES.map((page) => (
            <NavLink key={page.to} to={page.to} end={page.end}>
              {page.label}
            </NavLink>
          ))}
        </nav>
        <span className="who">
          {user.display_name} <span className="role">{user.role.replace("_", " ")}</span>
          <button className="link" onClick={signOut}>
            Sign out
          </button>
        </span>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/models" element={<Models />} />
          <Route path="/predictions" element={<Predictions />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
