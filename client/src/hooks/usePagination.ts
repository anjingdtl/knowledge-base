import { useState, useCallback } from 'react'

interface PaginationState {
  page: number
  pageSize: number
  total: number
}

export function usePagination(defaultPageSize = 20) {
  const [state, setState] = useState<PaginationState>({
    page: 1,
    pageSize: defaultPageSize,
    total: 0,
  })

  const setPage = useCallback((page: number) => {
    setState(s => ({ ...s, page }))
  }, [])

  const setTotal = useCallback((total: number) => {
    setState(s => ({ ...s, total }))
  }, [])

  const nextPage = useCallback(() => {
    setState(s => {
      const maxPage = Math.max(1, Math.ceil(s.total / s.pageSize))
      return { ...s, page: Math.min(s.page + 1, maxPage) }
    })
  }, [])

  const prevPage = useCallback(() => {
    setState(s => ({ ...s, page: Math.max(1, s.page - 1) }))
  }, [])

  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize))
  const offset = (state.page - 1) * state.pageSize
  const hasNext = state.page < totalPages
  const hasPrev = state.page > 1

  return {
    ...state,
    totalPages,
    offset,
    hasNext,
    hasPrev,
    setPage,
    setTotal,
    nextPage,
    prevPage,
  }
}
