import { useEffect } from "react"
import { useAuth } from "./auth/AuthContext"
import { Layout } from "./components/Layout"
import { LoginPage } from "./pages/LoginPage"
import { useTheme } from "./theme/ThemeContext"

export function App() {
  const { user, loading } = useAuth()
  const { applySaved } = useTheme()

  useEffect(() => {
    if (user) {
      applySaved(user.theme)
      return
    }
    document.title = "PiDeck"
  }, [user, applySaved])

  if (loading) {
    return (
      <div className="app-loading">
        <div className="mark" aria-hidden="true" />
        <p>Loading PiDeck</p>
      </div>
    )
  }
  if (!user) {
    return <LoginPage />
  }
  return <Layout />
}
