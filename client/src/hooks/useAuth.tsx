import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'
import { getToken, setToken, clearToken, isAuthenticated } from '../api'

interface AuthContextType {
  authenticated: boolean
  login: (token: string) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextType>({
  authenticated: false,
  login: () => {},
  logout: () => {},
})

export function useAuth() {
  return useContext(AuthContext)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState(isAuthenticated())

  const login = useCallback((token: string) => {
    setToken(token)
    setAuthenticated(true)
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setAuthenticated(false)
  }, [])

  return (
    <AuthContext.Provider value={{ authenticated, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
