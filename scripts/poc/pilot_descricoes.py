#!/usr/bin/env python3
"""Piloto §21: escrever ``descricao_editorial`` FORA do Brave, pelo subagente da assinatura.

A lane de atrativos escreve a descrição chamando a API da Anthropic de dentro do pipeline
(``TourismCopywriter``, $0,0749/atrativo — §15.1). Este script tira a escrita de dentro da
aplicação: exporta os atrativos sem descrição para um JSON, o subagente ``norteia-copywriter``
do Claude Code preenche o campo rodando sobre a assinatura Max já paga, e o JSON validado
volta para a base pelo caminho auditado.

A pergunta que o piloto responde é uma só: **quantos atrativos a cota da assinatura aguenta.**
A régua é o ``subagent_tokens`` que a sessão reporta por invocação, não este script.

Três subcomandos:

  export  seleciona atrativos sem descrição e escreve o JSON de trabalho
  merge   funde os JSONs que o subagente escreveu de volta no JSON de trabalho
  import  aplica as descrições na base pelo caminho auditado (edit + reprocess)

Uso:
  set -a; . ./.env; set +a
  .venv/bin/python -m scripts.poc.pilot_descricoes export --limit 30
  # ... rodar o subagente, que escreve em docs/poc/pilot-subagente/saidas/*.json ...
  .venv/bin/python -m scripts.poc.pilot_descricoes merge
  # ... validação visual do usuário ...
  .venv/bin/python -m scripts.poc.pilot_descricoes import --commit

  .venv/bin/python -m scripts.poc.pilot_descricoes --self-check   # offline, sem rede/banco

Fidelidade do contexto: o export chama ``copy_batch.build_request``, o mesmo construtor que
a lane de lote de produção usa, então o texto de grounding entregue ao subagente é
byte-idêntico ao que a produção mandaria. Isso carrega junto a lacuna que aquele módulo já
documenta: ``types``, ``editorial_summary`` e ``reviews`` do Places são transitórios e não
sobrevivem em ``normalized`` — só ``address``. Fora da lane, como no lote, a busca web carrega
mais peso. É uma propriedade da medição, não um defeito do script.

Nunca faz UPDATE direto em ``rio_records.normalized`` (§20.7): o import passa por
``PATCH /api/v1/atrativos/{rio_id}/edit`` (emite ``cms_edited`` no audit) seguido de
``PATCH /api/v1/dlq/{rio_id}/reprocess`` (recomputa o score) — ``edit_atrativo`` não
re-pontua sozinho, então as duas chamadas são obrigatórias em par.

O edit vive atrás de ``require_editing_unlocked`` (brave/api/deps.py:215): enquanto o motor
está LIGADO ele devolve 423 Locked, para que um steward não edite por baixo de uma sweep em
voo. Pausar ou desligar o motor antes do ``import --commit``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]

_OUT_DEFAULT = _REPO_ROOT / "docs/poc/pilot-subagente/atrativos.json"
_SAIDAS_DEFAULT = _REPO_ROOT / "docs/poc/pilot-subagente/saidas"

# Mesmo degrau que os dois aplicadores de produção usam quando a descrição entra:
# places_enrichment.py:470 e copy_batch.py:424 (_COMPLETUDE_WITH_DESCRIPTION).
_COMPLETUDE_WITH_DESCRIPTION = 90.0

# Status do contrato do subagente (.claude/agents/norteia-copywriter.md).
_STATUS_OK = "ok"
_STATUS_SEM_FONTE = "sem_fonte"


# --------------------------------------------------------------------------- export


def cmd_export(args: argparse.Namespace) -> int:
    """Seleciona atrativos sem descrição e escreve o JSON de trabalho."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from brave.lanes.atrativos.copy_batch import build_request, candidates_select

    engine = create_engine(os.environ["BRAVE_DB_URL"])
    session = sessionmaker(bind=engine)()

    # candidates_select carrega o predicado de elegibilidade da produção (attraction,
    # routing != descarte, sem descrição, orçamento de tentativas). Reusado em vez de
    # reescrito para que a amostra do piloto seja a mesma que a lane escolheria.
    # O FOR UPDATE SKIP LOCKED que ele traz exige transação — o rollback no fim solta.
    rios = list(session.execute(candidates_select(args.limit)).scalars())

    entradas = [_entrada(rio, build_request) for rio in rios]
    session.rollback()

    print(f"elegíveis selecionados: {len(entradas)} (limite {args.limit})")
    if not entradas:
        print("nada a exportar — a base não tem atrativo sem descrição")
        return 1
    for e in entradas:
        print(f"  {e['uf']} {e['nome']} — {e['municipio']} [{e['canonical_key']}]")

    if args.dry_run:
        print("\n--dry-run: nada escrito")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(entradas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nescrito: {out}")
    return 0


def _entrada(rio: Any, build_request: Any) -> dict[str, Any]:
    """Uma entrada do JSON de trabalho, com o contexto de grounding da produção."""
    normalized = rio.normalized or {}
    # build_request monta os params exatos que a lane de lote mandaria para a Anthropic;
    # o content da única mensagem é o _build_context(nome, municipio, uf, places_context).
    contexto = build_request(rio)["params"]["messages"][0]["content"]
    return {
        "rio_id": str(rio.id),
        "canonical_key": rio.canonical_key,
        "nome": normalized.get("name") or "",
        "municipio": normalized.get("municipio") or "",
        "uf": rio.uf or normalized.get("uf") or "",
        "contexto": contexto,
        # score é Decimal no Postgres e Decimal não serializa em JSON
        "score": float(rio.score) if rio.score is not None else None,
        "routing": rio.routing,
        "completude_value": normalized.get("completude_value"),
        # slots que o subagente preenche (contrato em .claude/agents/norteia-copywriter.md)
        "descricao_editorial": None,
        "status": None,
        "fontes": [],
        "queries": [],
    }


# ---------------------------------------------------------------------------- merge


def merge_entradas(
    base: list[dict[str, Any]], saidas: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Funde as saídas do subagente na lista base, casando por ``rio_id``.

    Idempotente: rodar duas vezes com as mesmas saídas dá o mesmo resultado. Saídas com
    ``rio_id`` desconhecido são reportadas em vez de silenciosamente ignoradas — um
    ``rio_id`` que não casa quase sempre significa que o subagente inventou a chave.
    """
    por_id = {e["rio_id"]: e for e in base}
    orfas: list[str] = []
    for s in saidas:
        rio_id = s.get("rio_id")
        alvo = por_id.get(rio_id) if rio_id else None
        if alvo is None:
            orfas.append(str(rio_id or s.get("nome") or "?"))
            continue
        alvo["descricao_editorial"] = s.get("descricao_editorial")
        alvo["status"] = s.get("status")
        alvo["fontes"] = s.get("fontes") or []
        alvo["queries"] = s.get("queries") or []
    return base, orfas


def cmd_merge(args: argparse.Namespace) -> int:
    alvo = Path(args.into)
    base = json.loads(alvo.read_text(encoding="utf-8"))

    saidas: list[dict[str, Any]] = []
    for p in sorted(Path(args.dir).glob("*.json")):
        carga = json.loads(p.read_text(encoding="utf-8"))
        # o subagente escreve um objeto por atrativo, ou uma lista quando roda em lote
        saidas.extend(carga if isinstance(carga, list) else [carga])

    base, orfas = merge_entradas(base, saidas)
    alvo.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    preenchidas = sum(1 for e in base if e.get("descricao_editorial"))
    ok = sum(1 for e in base if e.get("status") == _STATUS_OK)
    sem_fonte = sum(1 for e in base if e.get("status") == _STATUS_SEM_FONTE)
    print(f"saídas lidas: {len(saidas)}")
    print(f"preenchidas: {preenchidas}/{len(base)}  (ok={ok}, sem_fonte={sem_fonte})")
    if orfas:
        print(f"ATENÇÃO: {len(orfas)} saída(s) com rio_id que não casa: {', '.join(orfas)}")
    print(f"escrito: {alvo}")
    return 0


# --------------------------------------------------------------------------- import


def importaveis(base: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Separa o que entra na base do que é pulado, com o motivo de cada pulo.

    Só ``status == "ok"`` com prosa não vazia entra. ``sem_fonte`` é pulado por decisão:
    a §19 mediu três modelos descrevendo com confiança seis atrativos inexistentes e
    fabricando URLs de fonte, então prosa sem fonte confirmada é defeito, não conteúdo.
    """
    entram: list[dict[str, Any]] = []
    pulados: list[str] = []
    for e in base:
        nome = e.get("nome") or e.get("rio_id") or "?"
        prosa = (e.get("descricao_editorial") or "").strip()
        status = e.get("status")
        if not prosa:
            pulados.append(f"{nome}: sem descrição")
        elif status != _STATUS_OK:
            pulados.append(f"{nome}: status={status!r}")
        elif not e.get("fontes"):
            # o contrato manda o subagente registrar as fontes que sustentam os fatos;
            # status ok sem fonte é exatamente o padrão que a §19 marcou como suspeito
            pulados.append(f"{nome}: status ok mas fontes vazias")
        else:
            entram.append(e)
    return entram, pulados


def cmd_import(args: argparse.Namespace) -> int:
    import httpx

    base = json.loads(Path(getattr(args, "from")).read_text(encoding="utf-8"))
    entram, pulados = importaveis(base)

    print(f"entram: {len(entram)}   pulados: {len(pulados)}")
    for p in pulados:
        print(f"  pulado — {p}")

    if not args.commit:
        print("\n--dry-run (default): nada gravado. Use --commit para aplicar.")
        return 0

    token = os.environ.get("BRAVE_DASHBOARD_BEARER_TOKEN")
    if not token:
        print("ERRO: falta BRAVE_DASHBOARD_BEARER_TOKEN no ambiente (set -a; . ./.env; set +a)")
        return 1
    headers = {"Authorization": f"Bearer {token}"}

    falhas = 0
    with httpx.Client(base_url=args.api, headers=headers, timeout=30.0) as client:
        for e in entram:
            rio_id = e["rio_id"]
            body = {
                "fields": {
                    "descricao_editorial": e["descricao_editorial"],
                    "completude_value": _COMPLETUDE_WITH_DESCRIPTION,
                }
            }
            r1 = client.patch(f"/api/v1/atrativos/{rio_id}/edit", json=body)
            # edit_atrativo grava e audita mas NÃO re-pontua; sem o reprocess o degrau 90
            # fica em normalized sem nunca mexer em score/routing.
            r2 = client.patch(f"/api/v1/dlq/{rio_id}/reprocess") if r1.is_success else None
            ok = r1.is_success and r2 is not None and r2.is_success
            falhas += 0 if ok else 1
            marca = "ok " if ok else "FALHA"
            detalhe = f"edit={r1.status_code}" + (f" reprocess={r2.status_code}" if r2 else "")
            print(f"  {marca} {e['nome']} ({detalhe})")

    print(f"\naplicados: {len(entram) - falhas}/{len(entram)}   falhas: {falhas}")
    return 1 if falhas else 0



# -------------------------------------------------------------------------- auditar


# Classes de resultado. A distinção que importa é entre "o caminho não existe" (o padrão de
# fabricação que a §19 mediu) e "não consegui alcançar" (rede do auditor), porque só a primeira
# é defeito da descrição. Um 403 de anti-bot prova que a página existe, então conta como viva.
_VIVA = "viva"
_BLOQUEADA = "bloqueada"        # servidor recusa o auditor, mas a URL existe
_INEXISTENTE = "inexistente"    # 404/410 com a raiz do domínio respondendo: suspeita de fabricação
_SEM_DOMINIO = "sem_dominio"    # nem a raiz responde: domínio morto ou inventado
_INALCANCAVEL = "inalcancavel"  # erro de conexão: inconclusivo, não acusa a descrição

_BLOQUEIO_STATUS = {401, 402, 403, 405, 406, 409, 418, 429, 500, 502, 503, 520, 530}
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def classificar(status: Any, status_raiz: Any) -> str:
    """Traduz (status do caminho, status da raiz do domínio) em uma das classes acima."""
    if isinstance(status, int):
        if status < 400:
            return _VIVA
        if status in _BLOQUEIO_STATUS:
            return _BLOQUEADA
        if status in (404, 410):
            # raiz viva + caminho 404 = caminho não existe. Raiz morta = não dá para culpar
            # a descrição, o domínio inteiro sumiu.
            return _INEXISTENTE if isinstance(status_raiz, int) and status_raiz < 400 else _SEM_DOMINIO
        return _BLOQUEADA
    return _INALCANCAVEL


async def _verificar(urls: list[str], concorrencia: int = 12) -> dict[str, tuple[Any, Any]]:
    import asyncio

    import httpx

    lim = asyncio.Semaphore(concorrencia)
    raizes: dict[str, Any] = {}

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=25.0, headers={"User-Agent": _UA}
    ) as client:

        async def status(u: str) -> Any:
            try:
                r = await client.head(u)
                # muitos servidores respondem 403/404/405 a HEAD e 200 a GET
                if r.status_code >= 400:
                    r = await client.get(u)
                return r.status_code
            except Exception as exc:  # noqa: BLE001 — a classe do erro é o dado
                return type(exc).__name__

        async def uma(u: str) -> tuple[str, Any, Any]:
            async with lim:
                s = await status(u)
            raiz = None
            if isinstance(s, int) and s in (404, 410):
                root = "/".join(u.split("/")[:3])
                if root not in raizes:
                    async with lim:
                        raizes[root] = await status(root)
                raiz = raizes[root]
            return u, s, raiz

        import asyncio as _a

        res = await _a.gather(*(uma(u) for u in urls))
    return {u: (s, r) for u, s, r in res}


def cmd_auditar(args: argparse.Namespace) -> int:
    """Verifica se cada URL citada em `fontes` realmente existe.

    A §19 mediu que o modelo fabrica URLs de fonte quando não consegue buscar, uma delas um
    es.gov.br com caminho inventado que devolve 404. Como `fontes` só tem valor se for
    auditável, esta checagem é o que transforma a promessa em evidência.
    """
    import asyncio

    registros: list[dict[str, Any]] = []
    for caminho in args.arquivos:
        registros.extend(json.loads(Path(caminho).read_text(encoding="utf-8")))

    pares = [(e.get("nome") or "?", u) for e in registros for u in (e.get("fontes") or []) if u]
    urls = sorted({u for _, u in pares})
    print(f"registros: {len(registros)} | URLs citadas: {len(pares)} | únicas: {len(urls)}")

    resultado = asyncio.run(_verificar(urls, args.concorrencia))

    linhas = []
    for nome, u in pares:
        s, raiz = resultado[u]
        linhas.append(
            {"atrativo": nome, "url": u, "status": s, "status_raiz": raiz,
             "classe": classificar(s, raiz)}
        )

    contagem: dict[str, int] = {}
    for l in linhas:
        contagem[l["classe"]] = contagem.get(l["classe"], 0) + 1
    print()
    for c in (_VIVA, _BLOQUEADA, _INALCANCAVEL, _SEM_DOMINIO, _INEXISTENTE):
        n = contagem.get(c, 0)
        print(f"  {c:14} {n:4}  ({n/len(pares):5.1%})")

    suspeitas = [l for l in linhas if l["classe"] == _INEXISTENTE]
    if suspeitas:
        print(f"\ncaminho inexistente com domínio vivo ({len(suspeitas)}) "
              f"— o padrão de fabricação da §19:")
        for l in suspeitas:
            print(f"  {l['atrativo'][:34]:36} {l['url']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(linhas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nescrito: {out}")
    return 0

# ------------------------------------------------------------------------ self-check


def _self_check() -> int:
    """Valida merge e triagem de import offline — sem rede, sem banco."""
    base = [
        {"rio_id": "a", "nome": "Mirante da Lagoa", "descricao_editorial": None,
         "status": None, "fontes": [], "queries": []},
        {"rio_id": "b", "nome": "Vista Linda", "descricao_editorial": None,
         "status": None, "fontes": [], "queries": []},
        {"rio_id": "c", "nome": "Nunca Escrito", "descricao_editorial": None,
         "status": None, "fontes": [], "queries": []},
    ]
    saidas = [
        {"rio_id": "a", "descricao_editorial": "prosa boa", "status": "ok",
         "fontes": ["https://x"], "queries": ["q1", "q2"]},
        {"rio_id": "b", "descricao_editorial": "prosa suspeita", "status": "sem_fonte",
         "fontes": [], "queries": ["q1", "q2"]},
        {"rio_id": "zzz", "descricao_editorial": "chave inventada", "status": "ok",
         "fontes": ["https://y"], "queries": []},
    ]

    fundido, orfas = merge_entradas([dict(e) for e in base], saidas)
    assert orfas == ["zzz"], f"órfã não reportada: {orfas}"
    assert fundido[0]["descricao_editorial"] == "prosa boa"
    assert fundido[0]["fontes"] == ["https://x"]
    assert fundido[1]["status"] == "sem_fonte"
    assert fundido[2]["descricao_editorial"] is None, "entrada sem saída foi tocada"

    # idempotência: fundir de novo não muda nada
    refundido, _ = merge_entradas([dict(e) for e in fundido], saidas)
    assert refundido == fundido, "merge não é idempotente"

    entram, pulados = importaveis(fundido)
    assert [e["rio_id"] for e in entram] == ["a"], f"triagem errada: {entram}"
    assert len(pulados) == 2, pulados
    assert any("sem_fonte" in p for p in pulados), pulados

    # status ok mas sem fonte é o padrão que a §19 marcou como suspeito: não entra
    ok_sem_fonte = [{"rio_id": "d", "nome": "X", "descricao_editorial": "prosa",
                     "status": "ok", "fontes": []}]
    entram2, pulados2 = importaveis(ok_sem_fonte)
    assert entram2 == [], "descrição sem fonte passou pela triagem"
    assert "fontes vazias" in pulados2[0], pulados2

    # classificação da auditoria de fontes
    assert classificar(200, None) == _VIVA
    assert classificar(301, None) == _VIVA
    assert classificar(403, None) == _BLOQUEADA, "403 de anti-bot prova que a página existe"
    assert classificar(429, None) == _BLOQUEADA
    assert classificar(404, 200) == _INEXISTENTE, "raiz viva + caminho 404 = suspeita de fabricação"
    assert classificar(404, "ConnectError") == _SEM_DOMINIO, "domínio morto não acusa a descrição"
    assert classificar("ConnectError", None) == _INALCANCAVEL, "erro de rede é inconclusivo"

    print("self-check ok: merge casa por rio_id, reporta órfã, é idempotente; "
          "triagem barra sem_fonte, sem prosa e ok-sem-fonte; classificação separa fabricação de falha de rede")
    return 0


# ------------------------------------------------------------------------------ cli


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Piloto: descricao_editorial escrita fora do Brave pelo subagente."
    )
    ap.add_argument("--self-check", action="store_true",
                    help="valida merge/triagem offline e sai")
    sub = ap.add_subparsers(dest="cmd")

    p_exp = sub.add_parser("export", help="seleciona atrativos sem descrição → JSON")
    p_exp.add_argument("--limit", type=int, default=30)
    p_exp.add_argument("--out", default=str(_OUT_DEFAULT))
    p_exp.add_argument("--dry-run", action="store_true", help="só lista, não escreve")
    p_exp.set_defaults(func=cmd_export)

    p_mrg = sub.add_parser("merge", help="funde as saídas do subagente no JSON de trabalho")
    p_mrg.add_argument("--dir", default=str(_SAIDAS_DEFAULT))
    p_mrg.add_argument("--into", default=str(_OUT_DEFAULT))
    p_mrg.set_defaults(func=cmd_merge)

    p_imp = sub.add_parser("import", help="aplica as descrições na base (caminho auditado)")
    p_imp.add_argument("--from", default=str(_OUT_DEFAULT), dest="from")
    p_imp.add_argument("--api", default=os.environ.get("BRAVE_API_URL", "http://localhost:8000"))
    p_imp.add_argument("--commit", action="store_true",
                       help="grava de verdade; sem isso é dry-run")
    p_imp.set_defaults(func=cmd_import)

    p_aud = sub.add_parser("auditar", help="verifica se as URLs citadas em fontes existem")
    p_aud.add_argument("arquivos", nargs="+", help="JSONs de trabalho já preenchidos")
    p_aud.add_argument("--out", default=str(_REPO_ROOT / "docs/poc/auditoria-fontes.json"))
    p_aud.add_argument("--concorrencia", type=int, default=12)
    p_aud.set_defaults(func=cmd_auditar)

    args = ap.parse_args()
    if args.self_check:
        return _self_check()
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
