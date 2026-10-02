import { useEffect, useState } from "react"
import { ApiError, getBatterySettings, saveBatterySettings, saveTheme } from "../api/client"
import type { BatterySettings, ThemePreset, ThemeSettings, UserProfile } from "../api/types"
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
      <BatteryCalibration />
    </section>
  )
}

function BatteryCalibration() {
  const [runtime, setRuntime] = useState("")
  const [charge, setCharge] = useState("")
  const [saved, setSaved] = useState<BatterySettings | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    getBatterySettings()
      .then((settings) => {
        if (!active) {
          return
        }
        setSaved(settings)
        setRuntime(settings.estimated_full_runtime_minutes?.toString() ?? "")
        setCharge(settings.full_charge_time_minutes?.toString() ?? "")
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof ApiError ? err.message : "Could not load battery settings")
        }
      })
    return () => {
      active = false
    }
  }, [])

  const persist = async () => {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const next = await saveBatterySettings({
        estimated_full_runtime_minutes: minutesOrNull(runtime),
        full_charge_time_minutes: minutesOrNull(charge),
      })
      setSaved(next)
      setRuntime(next.estimated_full_runtime_minutes?.toString() ?? "")
      setCharge(next.full_charge_time_minutes?.toString() ?? "")
      setMessage("Battery estimate saved")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the battery estimate")
    } finally {
      setBusy(false)
    }
  }

  const history = saved?.calibration_samples_minutes ?? []
  return (
    <div className="card settings-battery">
      <div className="card-label">PiSugar S Plus estimate</div>
      <p className="help-copy">
        The S Plus cannot report a real percentage. Estimated charge is time on battery divided by a full runtime you
        measure. Switch on the PiSugar auto-start function, and leave I2C off. GPIO3 is the I2C clock, and auto-start
        cannot share it. A short time on external power does not count as a full charge.
      </p>
      <div className="battery-form">
        <label className="field">
          Full runtime (minutes)
          <input
            inputMode="numeric"
            value={runtime}
            aria-label="Full runtime in minutes"
            placeholder="300"
            onChange={(event) => setRuntime(event.target.value)}
          />
        </label>
        <label className="field">
          Full charge time (minutes)
          <input
            inputMode="numeric"
            value={charge}
            aria-label="Full charge time in minutes"
            placeholder="Leave empty until you measure it"
            onChange={(event) => setCharge(event.target.value)}
          />
        </label>
      </div>
      <p className="help-copy">
        PiSugar does not publish a charge time for the S Plus 5000 mAh pack. After external power stays connected for
        the full charge time, the estimate resets to 100%. Before that, the card says the charge estimate is uncertain.
        A later full discharge, from that known full charge until the Pi loses power, updates the runtime. The value
        used is the median of the last five runs, and one new run can move it by at most 25%.
      </p>
      {saved ? (
        <dl className="kv">
          <Row
            label="Runtime used"
            value={saved.estimated_full_runtime_minutes == null ? "Not set" : `${saved.estimated_full_runtime_minutes} min`}
          />
          <Row
            label="Calibration median"
            value={saved.calibration_median_minutes == null ? "No full discharges yet" : `${saved.calibration_median_minutes} min`}
          />
          <Row label="Calibrated sessions" value={String(saved.calibration_sample_count)} />
          <Row label="Recent runtimes" value={history.length ? history.map((minutes) => `${minutes} min`).join(", ") : "None"} />
        </dl>
      ) : null}
      <div className="button-row">
        <button type="button" className="button" disabled={busy} onClick={() => void persist()}>
          {busy ? "Saving" : "Save battery estimate"}
        </button>
      </div>
      {message ? <p className="success-text">{message}</p> : null}
      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
}

function minutesOrNull(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }
  if (!/^\d+(\.\d+)?$/.test(trimmed)) {
    throw new Error("Enter minutes, or leave the field empty")
  }
  return Number(trimmed)
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="kv-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function labelFor(preset: Exclude<ThemePreset, "custom">): string {
  if (preset === "hacker") {
    return "Hacker Green"
  }
  return preset.charAt(0).toUpperCase() + preset.slice(1)
}
