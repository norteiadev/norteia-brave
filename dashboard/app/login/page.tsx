"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { setOperatorToken } from "@/lib/api-client";

/**
 * Login / token gate (DASH-06, UI-SPEC).
 *
 * Single-operator Bearer token this milestone (multi-user/RBAC deferred). The
 * operator pastes the token; we persist it (browser storage) and the api-client
 * presents it to the BFF on every request. On a 401 from the BFF, callers
 * redirect here with `?reason=expired` and we show the UI-SPEC 401 copy
 * ("Sessão expirada ou token inválido").
 */
function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const expired = params.get("reason") === "expired";
  const [token, setToken] = useState("");

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token.trim()) return;
    setOperatorToken(token.trim());
    // Land directly on the single-shell painel (phase H removed the `/` hub;
    // `/` now 307-redirects to `/painel` anyway — push there to skip the hop).
    router.push("/painel");
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 p-16">
      <div className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold">Entrar</h1>
        <p className="text-sm text-muted-foreground">
          Informe o token de operador para acessar o CMS Territorial.
        </p>
      </div>

      {expired && (
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 p-4"
        >
          <p className="text-sm font-semibold text-destructive">
            Sessão expirada ou token inválido
          </p>
          <p className="text-sm text-muted-foreground">
            Faça login novamente para continuar.
          </p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide">
            Token de operador
          </span>
          <input
            type="password"
            name="operatorToken"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="off"
            className="h-8 rounded-md border border-input bg-transparent px-2 font-mono text-sm"
            placeholder="Bearer token"
          />
        </label>
        <button
          type="submit"
          className="inline-flex h-8 items-center justify-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground"
        >
          Entrar
        </button>
      </form>
    </main>
  );
}

export default function LoginPage() {
  // useSearchParams requires a Suspense boundary in the App Router.
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
