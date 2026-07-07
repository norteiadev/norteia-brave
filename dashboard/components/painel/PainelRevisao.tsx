"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  type DlqListItem,
  descarteDlqRecord,
  dlqKeys,
  fetchDlqList,
  validateDlqRecord,
} from "@/lib/dlq-api";
import {
  type GateQueueItem,
  approveGate,
  fetchGateQueue,
  gateKeys,
  maskedPhoneFrom,
  rejectGate,
} from "@/lib/gate-api";

/**
 * PainelRevisao — the "DLQ / Revisão" painel view (phase H).
 *
 * Folds the old dark /dlq + /gate routes into one painel-light review surface:
 *   - the DLQ queue (GET /api/v1/dlq) with per-record Validar / Descartar
 *   - the WhatsApp gate queue of atrativos aguardando consulta
 *     (GET /api/v1/atrativos/gate) with Aprovar / Rejeitar
 *
 * LGPD: gate rows surface ONLY the pre-masked phone via `maskedPhoneFrom` — the
 * raw E.164 never crosses the boundary. Mutations reuse the existing dlq-api /
 * gate-api clients (no new backend). Self-loading via TanStack Query.
 */

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada. Faça login novamente.";
    if (err.status === 409) return "Ação não disponível neste estágio.";
    return err.message;
  }
  return "Falha na ação de revisão.";
}

function readName(normalized: Record<string, unknown>): string {
  const n = normalized?.["name"];
  return typeof n === "string" && n.length > 0 ? n : "—";
}

export function PainelRevisao() {
  const qc = useQueryClient();

  const dlq = useQuery({
    queryKey: dlqKeys.list(),
    queryFn: () => fetchDlqList(undefined, undefined, 50),
  });
  const gate = useQuery({
    queryKey: gateKeys.list(),
    queryFn: () => fetchGateQueue(undefined, 50),
  });

  const dlqValidate = useMutation({
    mutationFn: (item: DlqListItem) => validateDlqRecord(item.id),
    onError: (e) => toast.error(explainError(e)),
    onSuccess: () => toast.success("Registro validado e publicado no Mar"),
    onSettled: () => void qc.invalidateQueries({ queryKey: dlqKeys.all }),
  });
  const dlqDescarte = useMutation({
    mutationFn: (item: DlqListItem) => descarteDlqRecord(item.id),
    onError: (e) => toast.error(explainError(e)),
    onSuccess: () => toast.success("Registro descartado"),
    onSettled: () => void qc.invalidateQueries({ queryKey: dlqKeys.all }),
  });
  const gateApprove = useMutation({
    mutationFn: (item: GateQueueItem) => approveGate(item.rio_id),
    onError: (e) => toast.error(explainError(e)),
    onSuccess: () => toast.success("Atrativo aprovado — outreach enfileirado"),
    onSettled: () => void qc.invalidateQueries({ queryKey: gateKeys.all }),
  });
  const gateReject = useMutation({
    mutationFn: (item: GateQueueItem) => rejectGate(item.rio_id),
    onError: (e) => toast.error(explainError(e)),
    onSuccess: () => toast.success("Atrativo rejeitado"),
    onSettled: () => void qc.invalidateQueries({ queryKey: gateKeys.all }),
  });

  const dlqRows = dlq.data ?? [];
  const gateRows = gate.data ?? [];
  const dlqBusy = dlqValidate.isPending || dlqDescarte.isPending;
  const gateBusy = gateApprove.isPending || gateReject.isPending;

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-7 pt-5">
      {/* DLQ queue */}
      <Section title="Fila DLQ" subtitle="Registros abaixo do limiar §7.6" count={dlqRows.length}>
        {dlqRows.length === 0 ? (
          <Empty testId="revisao-dlq-empty" text="Nenhum registro na DLQ." />
        ) : (
          <Table
            head={
              <>
                <Th>Entidade</Th>
                <Th>UF</Th>
                <Th>Motivo</Th>
                <Th right>Score</Th>
                <Th />
              </>
            }
          >
            {dlqRows.map((item) => (
              <tr
                key={item.id}
                data-testid="revisao-dlq-row"
                className="border-t border-[var(--painel-border-inner)]"
              >
                <Td>{item.entity_type}</Td>
                <Td>
                  <span className="font-mono font-semibold">{item.uf ?? "—"}</span>
                </Td>
                <Td>
                  <span className="text-[var(--painel-muted)]">
                    {item.dlq_reason ?? "—"}
                  </span>
                </Td>
                <Td right>
                  <span className="font-mono">
                    {item.score == null ? "—" : item.score.toFixed(1)}
                  </span>
                </Td>
                <Td right>
                  <div className="flex justify-end gap-2">
                    <ActionButton
                      testId="revisao-dlq-validar"
                      onClick={() => dlqValidate.mutate(item)}
                      disabled={dlqBusy}
                      tone="approve"
                    >
                      Validar
                    </ActionButton>
                    <ActionButton
                      testId="revisao-dlq-descartar"
                      onClick={() => dlqDescarte.mutate(item)}
                      disabled={dlqBusy}
                      tone="reject"
                    >
                      Descartar
                    </ActionButton>
                  </div>
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Section>

      {/* WhatsApp gate queue */}
      <Section
        title="Gate WhatsApp"
        subtitle="Atrativos aguardando consulta · telefone minimizado (LGPD)"
        count={gateRows.length}
      >
        {gateRows.length === 0 ? (
          <Empty testId="revisao-gate-empty" text="Nenhum atrativo aguardando consulta." />
        ) : (
          <Table
            head={
              <>
                <Th>Atrativo</Th>
                <Th>UF</Th>
                <Th>Telefone (min.)</Th>
                <Th right>Score</Th>
                <Th />
              </>
            }
          >
            {gateRows.map((item) => (
              <tr
                key={item.rio_id}
                data-testid="revisao-gate-row"
                className="border-t border-[var(--painel-border-inner)]"
              >
                <Td>{readName(item.normalized)}</Td>
                <Td>
                  <span className="font-mono font-semibold">{item.uf ?? "—"}</span>
                </Td>
                <Td>
                  <span className="font-mono text-[var(--painel-muted)]">
                    {maskedPhoneFrom(item.normalized) ?? "—"}
                  </span>
                </Td>
                <Td right>
                  <span className="font-mono">
                    {item.score == null ? "—" : item.score.toFixed(1)}
                  </span>
                </Td>
                <Td right>
                  <div className="flex justify-end gap-2">
                    <ActionButton
                      testId="revisao-gate-aprovar"
                      onClick={() => gateApprove.mutate(item)}
                      disabled={gateBusy}
                      tone="approve"
                    >
                      Aprovar
                    </ActionButton>
                    <ActionButton
                      testId="revisao-gate-rejeitar"
                      onClick={() => gateReject.mutate(item)}
                      disabled={gateBusy}
                      tone="reject"
                    >
                      Rejeitar
                    </ActionButton>
                  </div>
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Section>
    </div>
  );
}

function Section({
  title,
  subtitle,
  count,
  children,
}: {
  title: string;
  subtitle: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6 max-w-[1000px]">
      <div className="mb-[10px] flex items-baseline gap-2.5">
        <h2 className="text-[14px] font-semibold text-[var(--painel-text)]">
          {title}
        </h2>
        <span className="rounded-[5px] bg-[var(--painel-chip)] px-[7px] py-[1px] font-mono text-[11px] font-semibold text-[var(--painel-navy)]">
          {count}
        </span>
        <span className="text-[11.5px] text-[var(--painel-muted-2)]">
          {subtitle}
        </span>
      </div>
      {children}
    </section>
  );
}

function Table({
  head,
  children,
}: {
  head: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-[13px] border border-[var(--painel-border-outer)] bg-[var(--card)]">
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr className="text-left text-[10.5px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
            {head}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Empty({ testId, text }: { testId: string; text: string }) {
  return (
    <div
      data-testid={testId}
      className="rounded-[13px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-5 py-[44px] text-center text-[13px] text-[var(--painel-muted-2)]"
    >
      {text}
    </div>
  );
}

function ActionButton({
  testId,
  onClick,
  disabled,
  tone,
  children,
}: {
  testId: string;
  onClick: () => void;
  disabled?: boolean;
  tone: "approve" | "reject";
  children: React.ReactNode;
}) {
  const toneStyle =
    tone === "approve"
      ? { color: "oklch(0.5 0.13 150)" }
      : { color: "oklch(0.5 0.18 27)" };
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      style={toneStyle}
      className="h-[28px] cursor-pointer rounded-[7px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[10px] text-[11.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function Th({ children, right }: { children?: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={`px-[14px] py-[10px] font-semibold ${right ? "text-right" : "text-left"}`}
    >
      {children}
    </th>
  );
}

function Td({ children, right }: { children?: React.ReactNode; right?: boolean }) {
  return (
    <td className={`px-[14px] py-[11px] ${right ? "text-right" : "text-left"}`}>
      {children}
    </td>
  );
}
