import type { CSSProperties } from "react"
import type { ThemePreset, ThemeSettings } from "../api/types"

export const PRESETS: Record<Exclude<ThemePreset, "custom">, ThemeSettings> = {
  blue: {
    preset: "blue",
    primary: "#3b82f6",
    secondary: "#1d4ed8",
    accent: "#22d3ee",
    background: "#0b1220",
    panel: "#121a2b",
    text: "#e7eefc",
  },
  hacker: {
    preset: "hacker",
    primary: "#16a34a",
    secondary: "#14532d",
    accent: "#4ade80",
    background: "#070a07",
    panel: "#101610",
    text: "#d7f5df",
  },
  purple: {
    preset: "purple",
    primary: "#8b5cf6",
    secondary: "#6d28d9",
    accent: "#c4b5fd",
    background: "#120f18",
    panel: "#1c1726",
    text: "#f3e8ff",
  },
  red: {
    preset: "red",
    primary: "#ef4444",
    secondary: "#b91c1c",
    accent: "#fb7185",
    background: "#100c0c",
    panel: "#1a1212",
    text: "#ffe4e6",
  },
}

export const DEFAULT_THEME: ThemeSettings = PRESETS.blue

const HEX = /^#[0-9a-fA-F]{6}$/
const PRESET_NAMES: ThemePreset[] = ["blue", "hacker", "purple", "red", "custom"]

export const COLOR_FIELDS = [
  ["primary", "Primary"],
  ["secondary", "Secondary"],
  ["accent", "Accent"],
  ["background", "Background"],
  ["panel", "Panel"],
  ["text", "Text"],
] as const

export function isTheme(value: unknown): value is ThemeSettings {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const theme = value as Partial<ThemeSettings>
  if (!theme.preset || !PRESET_NAMES.includes(theme.preset)) {
    return false
  }
  return COLOR_FIELDS.every(([key]) => typeof theme[key] === "string" && HEX.test(theme[key]))
}

export function applyTheme(theme: ThemeSettings): void {
  const root = document.documentElement
  root.style.setProperty("--primary", theme.primary)
  root.style.setProperty("--secondary", theme.secondary)
  root.style.setProperty("--accent", theme.accent)
  root.style.setProperty("--background", theme.background)
  root.style.setProperty("--panel", theme.panel)
  root.style.setProperty("--text", theme.text)
  root.dataset.theme = theme.preset
}

export function themeStyle(theme: ThemeSettings): CSSProperties {
  return {
    "--primary": theme.primary,
    "--secondary": theme.secondary,
    "--accent": theme.accent,
    "--background": theme.background,
    "--panel": theme.panel,
    "--text": theme.text,
  } as CSSProperties
}
