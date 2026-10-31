import { NavLink, Route, Routes } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { RunsPage, RunDetailPage } from "./pages/Runs";
import { CalibrationPage } from "./pages/Calibration";
import { SettingsPage } from "./pages/Settings";

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: "monitoring", end: true },
  { to: "/runs", label: "Evaluation runs", icon: "terminal" },
  { to: "/calibration", label: "Calibration", icon: "tune" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

export function App() {
  return (
    <div className="min-h-screen bg-surface font-body-md text-on-surface antialiased">
      <aside className="fixed left-0 top-0 bottom-0 w-60 bg-surface-container-lowest z-50 flex flex-col justify-between select-none border-r border-surface-container">
        <div className="flex flex-col">
          <div className="h-12 px-space-xl flex items-center gap-space-sm">
            <span className="w-7 h-7 rounded bg-surface-container-high flex items-center justify-center font-code-sm text-code-sm font-semibold text-primary">
              PG
            </span>
            <span className="font-code-md text-code-md font-semibold tracking-tight text-on-surface uppercase">
              PromptGuard
            </span>
          </div>
          <div className="px-space-md py-space-xs">
            <span className="px-space-sm font-label-sm text-label-sm text-outline uppercase tracking-wider">
              Workspace
            </span>
          </div>
          <nav className="flex flex-col gap-0.5 px-space-md">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `flex items-center gap-space-sm px-space-sm py-1.5 rounded font-code-md text-code-md transition-colors ${
                    isActive
                      ? "bg-surface-container text-primary font-medium"
                      : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                  }`
                }
              >
                <span className="material-symbols-outlined text-[16px]">
                  {item.icon}
                </span>
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="p-space-md font-code-sm text-code-sm text-outline border-t border-surface-container">
          <p className="text-on-surface-variant">
            LLM evaluation with statistical quality gates.
          </p>
          <p className="mt-space-xs">
            Calibration is read-only by design (ADR-0002).
          </p>
        </div>
      </aside>
      <div className="pl-60">
        <header className="fixed top-0 left-60 right-0 h-12 bg-surface-container-lowest z-40 flex items-center justify-between px-space-xl border-b border-surface-container">
          <div className="flex items-center gap-space-md font-code-sm text-code-sm text-on-surface-variant">
            <span className="font-semibold tracking-tight text-on-surface uppercase">
              evals
            </span>
            <span className="text-outline">/</span>
            <span className="text-on-surface font-medium">PromptGuard</span>
          </div>
          <div className="flex items-center gap-space-xs">
            <a
              className="px-space-sm py-1 rounded bg-surface-container text-on-surface-variant font-code-sm text-code-sm hover:bg-surface-container-high hover:text-on-surface transition-colors"
              href="https://github.com/Priyanshi669/Promptguard"
              target="_blank"
              rel="noreferrer"
            >
              <span className="material-symbols-outlined text-[14px] align-middle mr-1">
                menu_book
              </span>
              Docs
            </a>
          </div>
        </header>
        <main className="w-full pt-12 bg-surface min-h-screen">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/runs/:runId" element={<RunDetailPage />} />
            <Route path="/calibration" element={<CalibrationPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
