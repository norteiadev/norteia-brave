import { redirect } from "next/navigation";

/**
 * Root route — the dark 9-surface hub was removed in phase H (route
 * consolidation). Every operator surface now lives inside the single-shell
 * `/painel` (Painel Brave). `/` is a thin server redirect so bookmarks and the
 * post-login push land directly on the painel.
 *
 * `redirect()` throws NEXT_REDIRECT and issues a 307 — no client JS, no flash.
 * The operator-token gate that used to live here now lives in `/painel`
 * (app/painel/page.tsx), so an unauthenticated visitor still lands on /login.
 */
export default function Home() {
  redirect("/painel");
}
