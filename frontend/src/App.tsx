import { NavLink, Route, Routes } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { RunsPage, RunDetailPage } from "./pages/Runs";
import { CalibrationPage } from "./pages/Calibration";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/runs", label: "Runs" },
  { to: "/calibration", label: "Calibration" },
];

export function App() {
  return (
    <div className="app">
      <header className="app-header">
        <span className="brand">PromptGuard</span>
        <span className="brand-sub">
          LLM evaluation with statistical quality gates
        </span>
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                isActive ? "nav-link active" : "nav-link"
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/calibration" element={<CalibrationPage />} />
        </Routes>
      </main>
      <footer className="app-footer">
        Read-only calibration by design (ADR-0002); reviewer fields are
        filled in the workbook by humans.
      </footer>
    </div>
  );
}
