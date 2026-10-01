import type { View } from "./Layout"

const ITEMS: { id: View; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "terminal", label: "Terminal" },
  { id: "settings", label: "Settings" },
]

export function Sidebar({
  view,
  open,
  onNavigate,
  onLogout,
}: {
  view: View
  open: boolean
  onNavigate: (view: View) => void
  onLogout: () => void
}) {
  return (
    <aside className={open ? "sidebar open" : "sidebar"}>
      <div className="brand">
        <div className="mark" aria-hidden="true" />
        <div>
          <div className="brand-name">PiDeck</div>
          <div className="brand-sub">Control center</div>
        </div>
      </div>
      <nav className="nav" aria-label="Primary">
        {ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={view === item.id ? "nav-button active" : "nav-button"}
            aria-current={view === item.id ? "page" : undefined}
            onClick={() => onNavigate(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="sidebar-footer">
        <button type="button" className="nav-button" onClick={onLogout}>
          Logout
        </button>
      </div>
    </aside>
  )
}
