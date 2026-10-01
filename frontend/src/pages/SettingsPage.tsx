import { useState } from "react"
import { ApiError, saveTheme } from "../api/client"
import type { ThemePreset, ThemeSettings, UserProfile } from "../api/types"
import { useAuth } from "../auth/AuthContext"
import { COLOR_FIELDS, PRESETS, themeStyle } from "../theme/themes"
import { useTheme } from "../theme/ThemeContext"

const PRESET_ORDER: Exclude<ThemePreset, "custom">[] = ["blue", "hacker", "purple", "red"]

export function SettingsPage({ user }: { user: UserProfile }) {
  const { applySaved } = useTheme()
  const { setUser } = useAuth()
  const [draft, setDraft] = useState<ThemeSettings>(user.theme)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const choosePreset = (preset: Exclude<ThemePreset, "custom">) => {
    setDraft({ ...PRESETS[preset] })
    setMessage(null)
    setError(null)
  }

  const changeColor = (key: (typeof COLOR_FIELDS)[number][0], value: string) => {
    setDraft((current) => ({ ...current, preset: "custom", [key]: value }))
    setMessage(null)
  }

  const persist = async (theme: ThemeSettings) => {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const saved = await saveTheme(theme)
      applySaved(saved.theme)
      setDraft(saved.theme)
      setUser({ ...user, theme: saved.theme })
      setMessage("Theme saved")
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the theme")
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Settings</h1>
          <p>Theme colors are stored for {user.username} and also kept in this browser.</p>
        </div>
      </header>
      <div className="settings-grid">
        <div className="card settings-card">
          <div className="card-label">Preset</div>
          <div className="preset-row">
            {PRESET_ORDER.map((preset) => (
              <button
                key={preset}
                type="button"
                className={draft.preset === preset ? "preset-button active" : "preset-button"}
                onClick={() => choosePreset(preset)}
              >
                {labelFor(preset)}
              </button>
            ))}
            <button type="button" className={draft.preset === "custom" ? "preset-button active" : "preset-button"} disabled>
              Custom
            </button>
          </div>
          <div className="color-grid">
            {COLOR_FIELDS.map(([key, label]) => (
              <label key={key} className="color-field">
                <span>{label}</span>
                <input
                  type="color"
                  value={draft[key]}
                  aria-label={label}
                  onChange={(event) => changeColor(key, event.target.value)}
                />
                <code>{draft[key]}</code>
              </label>
            ))}
          </div>
          <div className="button-row">
            <button type="button" className="button" disabled={busy} onClick={() => void persist(draft)}>
              {busy ? "Saving" : "Save theme"}
            </button>
            <button
              type="button"
              className="button button-ghost"
              disabled={busy}
              onClick={() => void persist({ ...PRESETS.blue })}
            >
              Reset to default
            </button>
          </div>
          {message ? <p className="success-text">{message}</p> : null}
          {error ? (
            <p className="error-text" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="card">
          <div className="card-label">Live preview</div>
          <div className="preview-frame" style={themeStyle(draft)}>
            <article className="preview-card">
              <div className="card-label">Sample</div>
              <div className="metric">42%</div>
              <div className="bar" aria-hidden="true">
                <span style={{ width: "42%" }} />
              </div>
              <p className="preview-copy">Panels, text, and accent follow the draft colors before you save.</p>
              <button type="button" className="button button-small">
                Action
              </button>
            </article>
          </div>
        </div>
      </div>
    </section>
  )
}

function labelFor(preset: Exclude<ThemePreset, "custom">): string {
  if (preset === "hacker") {
    return "Hacker Green"
  }
  return preset.charAt(0).toUpperCase() + preset.slice(1)
}
