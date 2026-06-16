import { timingSafeEqual } from "node:crypto";

/**
 * BFF browser-token validation (D-02, threat T-04-05 / T-04-08).
 *
 * Server-only module: it reads `DASHBOARD_OPERATOR_TOKEN` from the process env
 * and is imported exclusively by the Route Handler. It mirrors the FastAPI
 * `require_steward`/`require_bearer` discipline: constant-time compare,
 * fail-closed (an unset operator token rejects every caller), and it surfaces a
 * 401 BEFORE any forward to FastAPI.
 *
 * NEVER import this into a Client Component — it would not work (Node crypto) and
 * would risk leaking the operator-token comparison into the bundle.
 */

/** Extract the bearer credential from an `Authorization: Bearer <token>` header. */
export function extractBearer(authorization: string | null): string | null {
  if (!authorization) return null;
  const prefix = "Bearer ";
  if (!authorization.startsWith(prefix)) return null;
  const token = authorization.slice(prefix.length).trim();
  return token.length > 0 ? token : null;
}

/** Constant-time string compare; false when either side is empty (fail-closed). */
function constantTimeEqual(a: string, b: string): boolean {
  if (!a || !b) return false;
  const bufA = Buffer.from(a, "utf8");
  const bufB = Buffer.from(b, "utf8");
  // timingSafeEqual requires equal-length buffers; comparing lengths first would
  // leak length but length mismatch already means "not equal". Pad to the longer
  // length so the comparison itself stays constant-time over equal-length input.
  if (bufA.length !== bufB.length) {
    // Still do a fixed-work compare to avoid an early-return timing signal.
    const max = Math.max(bufA.length, bufB.length);
    const padA = Buffer.alloc(max);
    const padB = Buffer.alloc(max);
    bufA.copy(padA);
    bufB.copy(padB);
    timingSafeEqual(padA, padB);
    return false;
  }
  return timingSafeEqual(bufA, bufB);
}

/**
 * Validate the browser-presented operator token against DASHBOARD_OPERATOR_TOKEN.
 *
 * Fail-closed: returns false if the env token is unset/empty or the header is
 * missing/invalid. The Route Handler returns 401 on a false result before any
 * fetch to FastAPI (T-04-08: no unauthenticated mutation forwarding).
 */
export function isAuthorizedBrowserToken(authorization: string | null): boolean {
  const expected = process.env.DASHBOARD_OPERATOR_TOKEN ?? "";
  const provided = extractBearer(authorization);
  if (!expected || !provided) return false;
  return constantTimeEqual(provided, expected);
}
