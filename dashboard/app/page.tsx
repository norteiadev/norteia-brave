import Link from "next/link";

/**
 * Dashboard home (foundation slice). The full nav shell + six surfaces
 * (DLQ / monitor / gate / cost / funnels / conversations) land in later slices.
 * For now this is the authenticated landing that proves the app boots and points
 * the operator at the login gate.
 */
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-6 p-16">
      <div className="flex flex-col gap-2">
        <h1 className="text-[28px] font-semibold leading-tight">
          Norteia Brave — CMS Territorial
        </h1>
        <p className="text-sm text-muted-foreground">
          Painel de operações do pipeline Brave (Nascente → Rio → Mar). Faça
          login para acessar a fila DLQ, o monitor e o gate de WhatsApp.
        </p>
      </div>
      <Link
        href="/login"
        className="inline-flex h-8 w-fit items-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground"
      >
        Entrar
      </Link>
    </main>
  );
}
