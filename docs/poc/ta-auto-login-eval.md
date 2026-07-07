# POC / Avaliação — Login automático do TripAdvisor por user+senha

**Data:** 2026-07-06 · **Escopo:** avaliar + POC (sem alterar código do pipeline) ·
**Pergunta:** dá pra criar a sessão TA (`brave:ta:session`) só com `TA_EMAIL`/`TA_PASSWORD`
no `.env`, sem o operador copiar cURL do DevTools?

## Veredito

**Login 100% automático (só user+senha, sem intervenção) NÃO é viável de forma não-adversarial.**
O único caminho legítimo é uma **captura atendida, uma única vez**, num browser real (headful) onde
um humano resolve o desafio anti-bot; depois disso é **reúso de cookie**, que o norteia-brave já
implementa. A automação **mecaniza a captura**, mas **não elimina o passo humano** do bot-check.

## Por quê (evidência empírica do POC)

| Abordagem | Resultado | Evidência |
|---|---|---|
| **httpx puro** (replicar o cURL) | ❌ **403** DataDome | GET a `tripadvisor.com.br` → HTTP 403, `server: DataDome`, body com `captcha=true`, Set-Cookie só com seed `datadome` não-resolvido |
| **Chromium headless** (headless-shell) | ❌ **403 + iframe captcha** | 1 navegação → status 403, body vazio, jar só `[TAUnique, datadome(seed len=128)]`, **sem** `TASID/TASSK/__vt`, **sem** form de login, iframe DataDome presente |
| **Chromium headful via Playwright** (contexto de automação) | ❌ **restrição temporária** | tentativa 2026-07-06: janela não-interativa (bg job, sem display), DataDome bloqueou (só `datadome`-seed + `TAUnique`, sem `TASID/TASSK`) e o TA aplicou "acesso temporariamente restrito" à conta/IP. **Fingerprint de automação é detectado mesmo headful.** |
| **Chrome REAL do usuário** (perfil já clareado) | ✅ único confiável | manual cURL (atual) ou `/browse` no browser real — IP/fingerprint legítimos, DataDome já resolvido |

> **Lição empírica (2026-07-06):** headful lançado por Playwright/automação **também é
> flaggado** pelo DataDome → não basta "ter um humano por perto"; o browser precisa ser o
> **Chrome real do usuário** (perfil/fingerprint/IP legítimos). Automatizar a aquisição a
> partir de um contexto controlado por código **não é viável** sem evasão adversarial. Após
> um bloqueio, **não re-tentar** — deixar esfriar (a restrição é temporária e escala com retry).

**Fatos-chave:**
- O cURL enviado (`POST /EmailSpellCheckJson`, `action=emailSuggestion`) é **red herring** — é o
  AJAX de sugestão/validação de e-mail do formulário, **não** o POST de autenticação.
- O login real é uma **mutation GraphQL persistida** em `POST /data/graphql/ids` (mesmo endpoint que
  o pipeline já usa p/ dados), com email+senha+token em `variables`, **gated por DataDome + reCAPTCHA**.
- O cookie `datadome` é um token **assinado pelo servidor** emitido só após passar um desafio JS
  (proof-of-work + fingerprint canvas/WebGL/UA/TLS). httpx não executa JS → nunca minta o cookie.
  `__vt`, `TASID`, `TASSK` também nascem do handshake JS.
- Cookies que diferenciam sessão **autenticada** vs anônima: `TAUD` (member id), `TASSK`, `SRT`/`TART`,
  `PAC`, `TASession` reatado. `TAUnique`/`TADCID`/ads/consent **persistem iguais** (device/tracking).

## Contrato de integração (o que a captura deve produzir)

Shape do `brave:ta:session` (Redis), idêntico ao que o parser de cURL de
`brave/api/routers/tripadvisor_session.py` gera hoje:
```json
{ "cookies": { "<name>": "<value>", ... }, "session_id": "<TASID>" }
```
Depois de injetado, o `ta_keepalive` (beat, `pipeline.py`, ~600s, TTL 1800s) **mantém vivo**
rotacionando cookies em cima do tráfego GraphQL read-only — **não re-autentica**. Ou seja: a
aquisição é o único passo novo; reúso + keepalive já existem.

## Recomendação

Automatizar **só a captura**, no **Chrome real do usuário** (não em browser lançado por automação —
esse é flaggado, ver tabela), e reusar o resto:
1. **Captura no browser real (recomendado):** o operador continua no seu Chrome (DataDome já
   clareado, fingerprint/IP legítimos) e ou (a) copia o cURL do DevTools → injeta (fluxo atual), ou
   (b) usamos `/browse` conectado ao Chrome real dele pra colher o jar autenticado direto. O harness
   `tmp/ta_auto_login_poc.py` (Playwright headful) **não** é confiável — foi bloqueado (ver tabela).
2. **Reúso:** o pipeline (`client.py`) + `ta_keepalive` mantêm a sessão viva (já implementado).
3. **Re-login = evento raro e MANUAL:** só quando `SessionExpiredError` persistir além do que o
   keepalive recupera. **Nunca** re-login automático em loop.

### Segurança da conta (não-negociável)
- **Nunca** logar/re-tentar login em loop — é o maior gatilho de lock. **1 login atendido**, depois reúso.
- UA + IP de egress **estáveis** entre captura e reúso (o `datadome` é ligado a eles).
- **Sem** serviço anti-captcha / evasão de DataDome (adversarial, fora de escopo). Se o desafio
  escalar p/ captcha v2, **o humano resolve** na hora.
- Escopo estrito à conta própria do usuário (first-party, risco ToS já documentado no repo).

## O que falta pra fechar end-to-end
- Os **seletores do form de login** são best-guess (o form nunca renderizou sob headless). A 1ª
  execução atendida confirma os seletores + que `TASID/TASSK/__vt/TAUD` aparecem pós-login.
- Captura opcional (1× no DevTools) do `preRegisteredQueryId` da mutation de auth — não necessário
  se o browser dirige o form via DOM.

## Artefatos (em `tmp/`, fora do repo)
- `ta_auto_login_poc.py` — harness atendido headful (lê `TA_EMAIL`/`TA_PASSWORD`, mascara valores, para antes de submeter se sem cred).
- `step1_httpx_probe.py` — prova httpx bloqueado (403).
- `step3_headless_observe.py` — prova headless desafiado (403 + iframe).

**Nenhum arquivo do pipeline (`brave/**`, `dashboard/**`) foi alterado.** Cookies/creds nunca logados.
