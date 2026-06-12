import { useState, useCallback } from 'react'
import { apiGet, apiPost, apiPut, apiDelete } from '../api'

interface UseApiState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

export function useApi<T>() {
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    loading: false,
    error: null,
  })

  const get = useCallback(async (path: string) => {
    setState(s => ({ ...s, loading: true, error: null }))
    try {
      const data = await apiGet<T>(path)
      setState({ data, loading: false, error: null })
      return data
    } catch (err) {
      const msg = err instanceof Error ? err.message : '请求失败'
      setState(s => ({ ...s, loading: false, error: msg }))
      throw err
    }
  }, [])

  const post = useCallback(async (path: string, body: unknown) => {
    setState(s => ({ ...s, loading: true, error: null }))
    try {
      const data = await apiPost<T>(path, body)
      setState({ data, loading: false, error: null })
      return data
    } catch (err) {
      const msg = err instanceof Error ? err.message : '请求失败'
      setState(s => ({ ...s, loading: false, error: msg }))
      throw err
    }
  }, [])

  const put = useCallback(async (path: string, body: unknown) => {
    setState(s => ({ ...s, loading: true, error: null }))
    try {
      const data = await apiPut<T>(path, body)
      setState({ data, loading: false, error: null })
      return data
    } catch (err) {
      const msg = err instanceof Error ? err.message : '请求失败'
      setState(s => ({ ...s, loading: false, error: msg }))
      throw err
    }
  }, [])

  const del = useCallback(async (path: string) => {
    setState(s => ({ ...s, loading: true, error: null }))
    try {
      const data = await apiDelete<T>(path)
      setState({ data, loading: false, error: null })
      return data
    } catch (err) {
      const msg = err instanceof Error ? err.message : '请求失败'
      setState(s => ({ ...s, loading: false, error: msg }))
      throw err
    }
  }, [])

  return { ...state, get, post, put, del }
}
