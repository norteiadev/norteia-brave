# Verificação pós-escrita da descrição de atrativos (rubrica CF v1.2)

Código: `brave/domains/places/verifier.py` (`AtrativoVerifier`), chamado por
`TourismCopywriter.write_cascade` **depois** do gate de groundedness (`grounding.py`), que
continua valendo. Só no modo cascade; o modo web_search e a lane de Message Batches não passam
pelo verificador.

## Por que existe

O gate de groundedness só enxerga tokens concretos (anos, medidas, nomes próprios compostos).
O A/B de 20 textos novos (`result2.json`, cascade Parallel + Gemini 2.5 Flash) mostrou o que
passa por ele: reputação fabricada a partir de 1 avaliação ("considerado por muitos"), plural
fabricado ("artistas locais"), fato da atração vizinha atribuído ao atrativo, dado operacional
na prosa (rodovia, horário). O verificador pede a um modelo que **rotule** cada afirmação contra
o mesmo contexto que o redator recebeu; a nota e a decisão são calculadas **em código**.

## Validação da rubrica

Rubrica acordada entre Claude e Antigravity (dois juízes independentes) em 5 rodadas:

- 20 textos novos avaliados de forma independente: decisão igual em **13/20**;
- após conciliação (clarificações (a)-(g) abaixo): **20/20**;
- |ΔCF| médio entre os juízes: **0,035** (meta ≤ 0,07).

## Rótulos

Cada afirmação verificável do texto recebe um rótulo:

| Rótulo | Significado |
|---|---|
| **S** | Sustentada pelo contexto. |
| **L** | Frouxa: fontes divergem e o texto escolheu um lado num fato duro; anúncio antigo dito no presente como anúncio. |
| **U** | Sem lastro: não está no contexto, ou amplia o que a fonte diz. |
| **C** | Contradita pelo contexto, ou dado de entidade vizinha/homônima. |
| **OP** | Operacional (proibido na prosa): distância de acesso, rodovia/rota, dias/horários, preço/gratuidade de ingresso, contato. |

Regras de extração:

- Enumerações da mesma categoria formam **uma** afirmação; 1 item inventado → a afirmação inteira vira U.
- Fatos embutidos em convites contam ("observe a fachada barroca azul" → barroco, azul).
- Adjetivo descritivo presente na fonte = S. Puffery absoluto claramente retórico fica fora de N; dito como recorde, entra como U. Recorde/título com lastro = S.
- Fontes divergentes: valor consensual/aproximado = S; escolher um lado em fato duro = L (U se obsoleto/contradito).
- "Coração da cidade" = S se o endereço é central, U se não. Poético de bioma = fora de N.
- Anúncio antigo no presente = L; afirmado como já existente = U. Dado de entidade vizinha/homônima = C.
- Características físicas (queda, extensão de trilha, altitude, área) e período do dia/estação **não** são OP.

### Clarificações v1.1

- **(a)** Opinião de visitante: S se modalizada/atribuída sem plural nem consenso ("há quem destaque X"; "visitantes costumam notar X" só com ≥ 2 avaliações). U se 1 único review (ou 1 único crítico da web) vira plural ("para muitos", "a maioria") ou reputação/atributo institucional ("é reconhecido por", "é famoso por", "é preparado para PCD").
- **(b)** Puffery interpretativo abstrato ("redefiniu a experiência", "convida a desacelerar", "transmite paz") fica fora de N, salvo se embutir alegação histórica/causal verificável sem fonte (U: "foi pioneiro", "inspirou outros parques"); paráfrase de citação da fonte = S.
- **(c)** Dica que embute suporte físico ("à beira do lago", "vistas da cachoeira X"): o suporte conta em N (S se existe no contexto, U se não). O fenômeno temporal/climático (pôr do sol, luz da manhã, levar água) fica fora de N, salvo se contradiz o funcionamento (pôr do sol num local que fecha antes → U).
- **(d)** Localização × rota: rua, bairro, praça ou "estrada que liga A a B" = localização (S). Distância em km, tempo de deslocamento, código de rodovia (BR/GO-xxx) ou instruções de como chegar = OP.

### Clarificações v1.2

- **(e)** Gratuidade só é OP quando se refere a ingresso/visita ("entrada gratuita"). Comodidade grátis com fonte ("Wi-Fi gratuito") = S.
- **(f)** Gatilhos sempre U quando o lastro é 1 único review/fonte: "para muitos", "muitos visitantes", "a maioria", "considerado por muitos", "é reconhecido por", "é famoso por", "visitantes apreciam/elogiam". Exceção: fonte publicada da web que traz a frase de consenso literalmente = S.
- **(g)** Plural fabricado a partir de 1 agente (1 artista → "artistas locais"; 1 review → "visitantes") = U.

## Nota e decisão (em código)

```
CF = clamp((S + 0,75·L − 0,5·C) / N, 0, 1)    N = S + L + U + C  (OP fora de N; N = 0 → CF = 1)
```

- **REPROVA**: CF < 0,65, ou C ≥ 2, ou erro estrutural (descreve outro lugar/homônimo).
- **APROVA**: U = 0, C = 0, OP = 0 e CF ≥ 0,90.
- **CORRIGE**: qualquer outro caso.

A regra do produto é "nunca inventar" e remover uma frase é barato, por isso U não é tolerado na
aprovação: leva à correção.

## CORRIGE: remoção determinística

Sem reescrita por LLM:

1. Remove toda frase que contém um `trecho` rotulado U/C/OP (divisão de frases resistente a
   abreviações como "Sr.", "km." e a decimais; parágrafos preservados).
2. Trecho não encontrado (nem literal, nem normalizado por acento/aspas/espaços) → REPROVA:
   nunca se mantém texto não verificado.
3. Sem nada removível (CF baixo só por L), restante vazio ou < 60% do tamanho original → REPROVA.
4. Senão, o texto corrigido é verificado **uma** vez de novo: APROVA → publica o corrigido;
   qualquer outra decisão → REPROVA.

## Onde o veredito vai parar

| Resultado | `descricao_editorial` | `descricao_gate` | Roteamento |
|---|---|---|---|
| APROVA | texto original | `None` | score normal |
| CORRIGE (re-verificado) | texto corrigido | `None` | score normal |
| REPROVA | não escreve | `nao_verificada` (rascunho em `descricao_rascunho`) | DLQ `descricao_nao_fundamentada` |
| Falha do verificador | não escreve | `verificacao_falhou` | fora do DLQ; próxima passada tenta de novo (conta em `descricao_attempts`) |

`descricao_verificacao` guarda `decisao`, `cf`, contagens S/L/U/C/OP, `estrutural`, os trechos
sinalizados (prosa do próprio redator) e `cf_corrigido` quando houve re-verificação. Nunca guarda
a `evidencia` do modelo (pode citar uma avaliação; LGPD). A chave é board-only: excluída do
canônico do Mar (`_NON_CANONICAL_KEYS`).

`CostGuardError` / `ProviderBalanceError` propagam, como no resto do cascade. O gasto é registrado
pelo cliente LLM na lane `atrativo_verifier`.

## Configuração

- `atrativo_description_verifier_enabled` (padrão `True`; chave de overlay no Painel).
- `ATRATIVO_VERIFIER_MODEL` (padrão `gemini-2.5-flash`; mesmas regras de rota do cascade).
