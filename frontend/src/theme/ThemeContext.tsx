import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from "react"
import type { ThemeSettings } from "../api/types"
import { DEFAULT_THEME, applyTheme, isTheme } from "./themes"

const STORAGE_KEY = "pideck-theme"

type ThemeContextValue = {
  theme: ThemeSettings
  applySaved: (theme: ThemeSettings) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readStored(): ThemeSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return DEFAULT_THEME
    }
    const parsed: unknown = JSON.parse(raw)
    return isTheme(parsed) ? parsed : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<ThemeSettings>(readStored)

  useLayoutEffect(() => {
    applyTheme(theme)
  }, [theme])

  const applySaved = useCallback((next: ThemeSettings) => {
    const safe = isTheme(next) ? next : DEFAULT_THEME
    setTheme(safe)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(safe))
  }, [])

  const value = useMemo(() => ({ theme, applySaved }), [theme, applySaved])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext)
  if (!value) {
    throw new Error("useTheme must be used inside ThemeProvider")
  }
  return value
}
