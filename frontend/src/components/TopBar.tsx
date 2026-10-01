export function TopBar({
  hostname,
  username,
  online,
  menuOpen,
  onMenu,
}: {
  hostname: string
  username: string
  online: boolean
  menuOpen: boolean
  onMenu: () => void
}) {
  return (
    <header className="topbar">
      <button
        type="button"
        className="menu-button"
        aria-label="Open navigation"
        aria-expanded={menuOpen}
        onClick={onMenu}
      >
        <span />
        <span />
        <span />
      </button>
      <div className="topbar-title">
        <span className="eyebrow">Host</span>
        <strong>{hostname}</strong>
      </div>
      <div className="topbar-meta">
        <span className={online ? "status-pill online" : "status-pill offline"}>
          <span className="status-dot" aria-hidden="true" />
          {online ? "Live" : "Offline"}
        </span>
        <span className="user-chip">{username}</span>
      </div>
    </header>
  )
}
