"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { clearOperatorToken, getOperatorToken } from "@/lib/api-client";

/**
 * Dashboard home — authenticated nav shell.
 *
 * If no operator token is stored, send the operator to the login gate.
 * Otherwise present the six territorial-CMS surfaces.
 */
const SURFACES = [
  { href: "/dlq", title: "Fila DLQ", desc: "Revisão batch-by-state · §7.6 · aprovar/rejeitar/editar→re-score" },
  { href: "/monitor", title: "Monitor Brave", desc: "Volume por camada · taxas · throughput · alertas · auditoria" },
  { href: "/gate", title: "Gate WhatsApp", desc: "Fila aguardando_consulta_whatsapp · ramp · qualidade" },
  { href: "/cost", title: "Custo & LLM", desc: "Gasto por lane/modelo (llm_generations)" },
  { href: "/funnels", title: "Funis", desc: "Destinos & atrativos por UF/fonte" },
  { href: "/conversations", title: "Conversas", desc: "Transcrições WhatsApp (telefone minimizado)" },
  { href: "/destinos", title: "Destinos", desc: "CMS territorial · lista/detalhe/ações por etapa" },
  { href: "/atrativos", title: "Atrativos", desc: "CMS atrativo · FSM sub_state · detalhe/ações" },
  { href: "/processo", title: "Processo", desc: "Workers · falhas · fila humana · funil" },
];

export default function Home() {
  const router = useRouter();
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    const token = getOperatorToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    // One-shot mount auth gate: read the stored token, then flip to authed.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAuthed(true);
  }, [router]);

  if (authed === null) {
    return (
      <main className="mx-auto flex min-h-screen max-w-3xl items-center justify-center p-16">
        <p className="text-sm text-muted-foreground">Carregando…</p>
      </main>
    );
  }

  function logout() {
    clearOperatorToken();
    router.replace("/login");
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-8 p-16">
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-[28px] font-semibold leading-tight">
            Norteia Brave — CMS Territorial
          </h1>
          <p className="text-sm text-muted-foreground">
            Painel de operações do pipeline Brave (Nascente → Rio → Mar).
          </p>
        </div>
        <button
          type="button"
          onClick={logout}
          className="inline-flex h-8 shrink-0 items-center rounded-md border border-input px-3 text-sm font-semibold"
        >
          Sair
        </button>
      </header>

      <nav className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {SURFACES.map((s) => (
          <Link
            key={s.href}
            href={s.href}
            className="flex flex-col gap-1 rounded-md border border-input bg-card p-4 transition-colors hover:border-primary hover:bg-accent"
          >
            <span className="text-sm font-semibold">{s.title}</span>
            <span className="text-xs text-muted-foreground">{s.desc}</span>
          </Link>
        ))}
      </nav>
    </main>
  );
}
