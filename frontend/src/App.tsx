import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { SignIn, useAuth } from "./auth";
import { Dashboard } from "./pages/Dashboard";
import { Datasets } from "./pages/Datasets";
import { Runs, RunDetailPage } from "./pages/Runs";
import { Models } from "./pages/Models";
import { Predictions } from "./pages/Predictions";
import { Approvals } from "./pages/Approvals";
import { Experiments } from "./pages/Experiments";
import { Monitoring } from "./pages/Monitoring";
import { Audit } from "./pages/Audit";
import { Reports } from "./pages/Reports";
import { Settings } from "./pages/Settings";
import { Notifications } from "./notifications";

const PAGES: { to: string; label: string; end: boolean; adminOnly?: boolean }[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/datasets", label: "Datasets", end: false },
  { to: "/runs", label: "Runs", end: false },
  { to: "/models", label: "Models", end: false },
  { to: "/predictions", label: "Predictions", end: false },
  { to: "/reports", label: "Reports", end: false },
  { to: "/approvals", label: "Approvals", end: false },
  { to: "/monitoring", label: "Monitoring", end: false },
  { to: "/experiments", label: "Experiments", end: false },
  { to: "/audit", label: "Audit", end: false, adminOnly: true },
  { to: "/settings", label: "Settings", end: false, adminOnly: true },
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
          {/* The audit trail is admin-only in the API. Offering the link to everyone else
              means every non-admin click lands on a 403 they cannot act on. */}
          {PAGES.filter((page) => !page.adminOnly || user.role === "admin").map((page) => (
            <NavLink key={page.to} to={page.to} end={page.end}>
              {page.label}
            </NavLink>
          ))}
        </nav>
        <Notifications user={user} />
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
          <Route path="/datasets" element={<Datasets />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/models" element={<Models />} />
          <Route path="/predictions" element={<Predictions />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/monitoring" element={<Monitoring />} />
          <Route path="/experiments" element={<Experiments />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
