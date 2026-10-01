import { lazy, Suspense, useEffect, useState } from "react"
import { ApiError, getSnapshot } from "../api/client"
import type { SystemSnapshot } from "../api/types"
import { useAuth } from "../auth/AuthContext"
import { DashboardPage } from "../pages/DashboardPage"
import { SettingsPage } from "../pages/SettingsPage"
import { Sidebar } from "./Sidebar"
import { TopBar } from "./TopBar"

const TerminalPage = lazy(() =>
  import("../pages/TerminalPage").then((module) => ({ default: module.TerminalPage })),
)

export type View = "dashboard" | "terminal" | "settings"

function viewFromPath(path: string): View {
  if (path.startsWith("/terminal")) {
    return "terminal"
  }
  if (path.startsWith("/settings")) {
    return "settings"
  }
  return "dashboard"
}

function pathFor(view: View): string {
  if (view === "terminal") {
    return "/terminal"
  }
  if (view === "settings") {
    return "/settings"
  }
  return "/"
}

export function Layout() {
  const { user, logout, expire } = useAuth()
  const [view, setView] = useState<View>(() => viewFromPath(window.location.pathname))
  const [menuOpen, setMenuOpen] = useState(false)
  const [snapshot, setSnapshot] = useState<SystemSnapshot | null>(null)
  const [online, setOnline] = useState(false)

  useEffect(() => {
    const titles: Record<View, string> = {
      dashboard: "Dashboard",
      terminal: "Terminal",
      settings: "Settings",
    }
    document.title = `${titles[view]} · PiDeck`
  }, [view])

  useEffect(() => {
    const onPop = () => setView(viewFromPath(window.location.pathname))
    window.addEventListener("popstate", onPop)
    return () => window.removeEventListener("popstate", onPop)
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await getSnapshot()
        if (!cancelled) {
          setSnapshot(next)
          setOnline(true)
        }
      } catch (error) {
        if (!cancelled) {
          setOnline(false)
          if (error instanceof ApiError && error.status === 401) {
            expire()
          }
        }
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 2000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [expire])

  const navigate = (next: View) => {
    if (next !== view) {
      window.history.pushState({}, "", pathFor(next))
      setView(next)
    }
    setMenuOpen(false)
  }

  return (
    <div className="shell">
      <Sidebar
        view={view}
        open={menuOpen}
        onNavigate={navigate}
        onLogout={() => {
          void logout()
        }}
      />
      {menuOpen ? <button type="button" className="backdrop" aria-label="Close navigation" onClick={() => setMenuOpen(false)} /> : null}
      <div className="main">
        <TopBar
          hostname={snapshot?.network.hostname ?? "PiDeck"}
          username={user?.username ?? ""}
          online={online}
          menuOpen={menuOpen}
          onMenu={() => setMenuOpen((open) => !open)}
        />
        <div className={view === "terminal" ? "content content-terminal" : "content"}>
          {view === "dashboard" ? <DashboardPage snapshot={snapshot} online={online} /> : null}
          {view === "terminal" ? (
            <Suspense fallback={<div className="terminal-toolbar">Loading terminal</div>}>
              <TerminalPage />
            </Suspense>
          ) : null}
          {view === "settings" && user ? <SettingsPage user={user} /> : null}
        </div>
      </div>
    </div>
  )
}
