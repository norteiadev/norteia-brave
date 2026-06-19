"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import type { MutationResult } from "@/lib/destinos-api";

/**
 * Steward edit of canonical `normalized` fields (D-03/D-04, T-08-05).
 *
 * Renders one input per editable top-level scalar field, submits only the
 * changed ones via `editFn`, and invalidates `invalidateKey` on settle so the
 * detail/list refetch. This is the first-party consumer of the PATCH
 * `/api/v1/{destinos,atrativos}/{id}/edit` endpoints — without it the route is
 * reachable but orphaned (no UI). The backend strips phone_e164; the UI also
 * never surfaces it as an editable field (it is not a scalar in the safe
 * normalized payload and is excluded here by the denylist).
 */
const NON_EDITABLE = new Set(["phone_e164"]);

function isEditable(key: string, value: unknown): boolean {
  if (NON_EDITABLE.has(key)) return false;
  // §7.6 score inputs end with `_value` — they are derived, never hand-edited.
  if (key.endsWith("_value")) return false;
  return typeof value === "string" || typeof value === "number";
}

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    return err.message;
  }
  return "Falha ao salvar os campos.";
}

export function EditFieldsDialog({
  normalized,
  editFn,
  invalidateKey,
  disabled = false,
}: {
  normalized: Record<string, unknown>;
  editFn: (fields: Record<string, unknown>) => Promise<MutationResult>;
  invalidateKey: readonly unknown[];
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const editable = Object.entries(normalized).filter(([k, v]) =>
    isEditable(k, v),
  );

  const [open, setOpen] = useState(false);
  // Working copy of the editable fields as strings (inputs are text).
  const [draft, setDraft] = useState<Record<string, string>>({});

  function seed() {
    const initial: Record<string, string> = {};
    for (const [k, v] of editable) initial[k] = String(v);
    setDraft(initial);
  }

  const mutation = useMutation({
    mutationFn: (fields: Record<string, unknown>) => editFn(fields),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => {
      toast.success("Campos atualizados");
      setOpen(false);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: invalidateKey });
    },
  });

  function handleSave() {
    // Submit only changed fields; coerce back to number when the original was.
    const changed: Record<string, unknown> = {};
    for (const [k, original] of editable) {
      const next = draft[k];
      if (next === undefined || next === String(original)) continue;
      changed[k] = typeof original === "number" ? Number(next) : next;
    }
    if (Object.keys(changed).length === 0) {
      setOpen(false);
      return;
    }
    mutation.mutate(changed);
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (next) seed();
        setOpen(next);
      }}
    >
      <AlertDialogTrigger asChild>
        <Button size="sm" variant="outline" disabled={disabled}>
          Editar campos
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Editar campos canônicos</AlertDialogTitle>
          <AlertDialogDescription>
            Corrige os campos do registro antes de re-pontuar. Salvar não
            re-pontua — use Reprocessar/Validar em seguida.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="grid gap-3 py-2">
          {editable.length === 0 && (
            <p className="text-[12px] text-muted-foreground">
              Nenhum campo editável neste registro.
            </p>
          )}
          {editable.map(([key]) => (
            <div key={key} className="grid gap-1">
              <Label htmlFor={`edit-${key}`} className="text-[12px]">
                {key}
              </Label>
              <Input
                id={`edit-${key}`}
                value={draft[key] ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, [key]: e.target.value }))
                }
              />
            </div>
          ))}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <Button
            size="sm"
            disabled={mutation.isPending || editable.length === 0}
            onClick={handleSave}
          >
            Salvar
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
