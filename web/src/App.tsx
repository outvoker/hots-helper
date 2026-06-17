import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import DashboardPage from "./features/dashboard/DashboardPage";
import HeroRankingsPage from "./features/heroes/HeroRankingsPage";
import HeroDetailPage from "./features/heroes/HeroDetailPage";
import TalentsPage from "./features/heroes/TalentsPage";
import PlayerRankingsPage from "./features/players/PlayerRankingsPage";
import PlayerDetailPage from "./features/players/PlayerDetailPage";
import BpAdvisorPage from "./features/bp/BpAdvisorPage";
import MatchListPage from "./features/matches/MatchListPage";
import MatchDetailPage from "./features/matches/MatchDetailPage";
import WeeklyReportPage from "./features/weekly/WeeklyReportPage";

const NAV = [
  { to: "/", label: "总览", end: true },
  { to: "/matches", label: "比赛记录" },
  { to: "/heroes", label: "英雄强度" },
  { to: "/talents", label: "天赋推荐" },
  { to: "/players", label: "玩家战力" },
  { to: "/bp", label: "BP 助手" },
  { to: "/weekly", label: "周报" },
];

export default function App() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

  // Close the mobile drawer on navigation so a tap takes you straight to the page.
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  // Lock body scroll and allow Escape-to-close while the drawer is open.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", onKey);
    };
  }, [drawerOpen]);

  return (
    <div className={`app${drawerOpen ? " app--drawer-open" : ""}`}>
      <header className="app-header">
        <button
          className="app-header__menu"
          aria-label="打开导航菜单"
          aria-expanded={drawerOpen}
          aria-controls="primary-nav"
          onClick={() => setDrawerOpen(true)}
        >
          <span className="app-header__bars" aria-hidden="true" />
        </button>
        <div className="brand">
          <span className="mark">⚔</span> HotS Helper
        </div>
      </header>

      <button
        className="scrim"
        aria-label="关闭导航菜单"
        tabIndex={drawerOpen ? 0 : -1}
        onClick={() => setDrawerOpen(false)}
      />

      <aside className="sidebar" id="primary-nav">
        <div className="brand">
          <span className="mark">⚔</span> HotS Helper
        </div>
        <nav className="nav" aria-label="主导航">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <p className="muted" style={{ fontSize: "0.72rem", marginTop: "auto" }}>
          战队对局数据 · 仅 Storm League 计入强度分析
        </p>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/matches" element={<MatchListPage />} />
          <Route path="/matches/:replayId" element={<MatchDetailPage />} />
          <Route path="/heroes" element={<HeroRankingsPage />} />
          <Route path="/heroes/:hero" element={<HeroDetailPage />} />
          <Route path="/talents" element={<TalentsPage />} />
          <Route path="/players" element={<PlayerRankingsPage />} />
          <Route path="/players/:handle" element={<PlayerDetailPage />} />
          <Route path="/bp" element={<BpAdvisorPage />} />
          <Route path="/weekly" element={<WeeklyReportPage />} />
        </Routes>
      </main>
    </div>
  );
}
