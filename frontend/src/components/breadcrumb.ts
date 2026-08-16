import { createContext, useContext, useEffect } from 'react'

/** The mockup's header shows "Strata Learn / <repo name>" on a repo's detail
 *  page — a trail the layout can't derive itself, since the route only carries
 *  an id and the display name arrives with the page's own fetch. Kept in its
 *  own module (rather than exported from AppLayout.tsx) so a component file
 *  still exports only components, per oxlint's react/only-export-components. */
export const BreadcrumbContext = createContext<(label: string | null) => void>(() => {})

/** Publishes a trail label to AppLayout's header for as long as the calling
 *  page is mounted, and clears it on the way out. */
export function useBreadcrumb(label: string | null | undefined): void {
  const setLabel = useContext(BreadcrumbContext)
  useEffect(() => {
    setLabel(label ?? null)
    return () => setLabel(null)
  }, [label, setLabel])
}
