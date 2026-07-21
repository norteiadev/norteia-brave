# Embratur / Visit Brasil — caminho para licenciamento do acervo

> ## ⛔ Status: BLOQUEADO até termos o Termo em mãos e lido
>
> Verificado em 2026-07-21. Dois bloqueios independentes:
>
> **1. O Termo não é público.** Busca exaustiva e negativa (WP REST API do embratur.com.br,
> 9 snapshots do Wayback, CDX do Internet Archive sobre 4 domínios com filtro PDF/DOC, perfil
> do Flickr, 12 variações de query). A página de serviço do gov.br **cita o Termo 4 vezes e
> nunca o linka**. Nenhum terceiro jamais publicou o conteúdo. Só chega depois de aprovado —
> ou seja, não dá para avaliar antes de se comprometer.
>
> **2. A Embratur não pode ceder direito perpétuo.** Os documentos de contratação **dela
> própria** mostram que ela adquire licenças temporárias. Briefing do Banco de Imagens Brasil
> 2025, verbatim:
>
> > *"Todo material (fotos, vídeos e minidocumentários) deverá ter cessão de direitos Royalty
> > Free, **com o período de utilização de até 05 anos após a data final de entrega do
> > acervo**"*
>
> E na Especificação Técnica da campanha EUA 2026: *"Período de veiculação/utilização:
> **12 meses**"*.
>
> *Nemo dat quod non habet* — não se cede o que não se tem. **Isso independe do que o Termo
> diga; é teto estrutural.**
>
> **Implicações:**
> - O acervo vai de 2009 a 2026, várias gerações de contratação. **Parcela relevante das
>   29.471 provavelmente já está fora da janela de utilização.**
> - Sinal favorável: uso comercial **está** no escopo (*"fins publicitários e editoriais"*), e
>   a cláusula *"o direito de uso deverá ser total e irrestrito e **ampliado ao trade do
>   turismo brasileiro**"* é o gancho que permite redistribuir para terceiros como a Norteia.
>   Mas herda o teto de 5 anos.
> - **Direito de imagem das pessoas retratadas é ônus separado e não renunciado** — obtê-lo é
>   responsabilidade do fornecedor, foto a foto.
> - Isso explica o link de 7 dias: **quem concede direito perpétuo não expira o link.** O TTL
>   é sintoma de concessão estreita e vinculada à finalidade declarada.
>
> **Consequência para o desenho:** cópia única para o S3 sem re-sync é incompatível com licença
> de 5 anos num registro canônico do Mar — resultaria em imagem publicada expirando em data que
> ninguém rastreia, sem gatilho de remoção. O modelo "baixa e esquece" só fecha se a resposta
> for "prazo indeterminado", o que os contratos da Embratur sugerem que **não** será.
>
> **Enquanto isso: a lane vai pelo MTur Destinos** — domínio público, prazo indeterminado,
> uso comercial irrestrito, termos publicados. Única fonte pública de turismo brasileira que
> passa no teste de retenção + exibição permanente. Ver `docs/poc/atrativo-imagens.md`.

Pesquisa verificada em 2026-07-21. Alvo: `flickr.com/photos/visitbrasil` — **29.471 fotos**,
todas `All Rights Reserved` (license id 0, confirmado). Objetivo: autorização para baixar,
re-hospedar em S3 próprio e exibir em plataforma comercial.

## 1. Natureza jurídica — muda tudo

**Lei nº 14.002/2020, art. 3º** (Planalto, verbatim):

> "Fica o Poder Executivo federal autorizado a instituir a Agência Brasileira de Promoção
> Internacional do Turismo (Embratur), **serviço social autônomo, na forma de pessoa
> jurídica de direito privado, sem fins lucrativos**..."

A mesma lei **extinguiu** o antigo Instituto Brasileiro de Turismo (autarquia) e **revogou a
Lei 8.181/1991**. Alterada depois pela **Lei nº 14.901/2024**; segue entidade de direito
privado — nenhuma reconversão encontrada até 2026.

**Consequência:** as fotos **não são domínio público**. A Lei 9.610/98 se aplica normalmente
e a Embratur as detém como parte privada. O argumento "é dinheiro público, logo é livre"
**não existe** aqui.

**O gancho útil é o art. 14, II** — a Embratur está expressamente autorizada a:

> "celebrar contratos com [...] **empresas e instituições ou entidades privadas** nacionais,
> internacionais ou estrangeiras, **com ou sem fins lucrativos**, para a realização de seus
> objetivos, inclusive para distribuir ou divulgar a 'Marca Brasil' **por meio de licenças,
> cessão de direitos de uso**, joint-venture ou outros instrumentos legais"

É a base legal do pedido. Citar.

## 2. LAI serve — mas não para o que a maioria acha

A Embratur **está sujeita à LAI** e mantém SIC próprio (Decreto 7.724/2012 art. 7º §3º VIII;
IN-TCU 84/2020; art. 20 da Lei 14.002/2020). Canal formal único: **Fala.BR**
(`falabr.cgu.gov.br`), prazo **20 dias** prorrogáveis por 10.

**Distinção que importa:** a LAI obriga a divulgar *informação*, **não** a conceder licença de
direito autoral. O art. 22 preserva expressamente a propriedade intelectual, e para serviço
social autônomo a LAI alcança só a parcela ligada a recurso público. Um pedido via LAI
dizendo "autorizem o uso das fotos" será respondido — corretamente — com "não é matéria de
acesso à informação".

**Usar a LAI para o que ela resolve.** Pedir via Fala.BR:
1. Existe política formal de reuso/licenciamento de imagens? Fornecer.
2. Qual unidade detém os direitos do acervo Visit Brasil e quem assina licenças?
3. Cópia do "Termo de utilização de fotos e vídeos" vigente.
4. As fotos são de titularidade própria ou licenciadas de fotógrafos terceiros?

Roda **em paralelo** ao e-mail direto, não no lugar dele. Gera protocolo, data e prazo.

## 3. Contatos verificados

| Canal | Contato |
|---|---|
| **Imprensa** (melhor primeira porta) | **imprensa@embratur.com.br** · +55 61 2023-8545 |
| **Marca Brasil / brand assets** | **marcabrasil@embratur.com.br** |
| Ouvidoria | ouvidoria@embratur.com.br |
| SIC | sic@embratur.com.br (formal = Fala.BR) |
| Presidência | presidencia@embratur.com.br |
| Sede | SCN Q2 Bloco G, Brasília–DF, 70.712-907 · (61) 2023-8900 · 9h–18h |
| Unidade dona do acervo | Coord.-Geral de Publicidade e Propaganda · (61) 2023-8598 / 8624 / 8604 / 8605 |

> ⚠️ **NÃO usar `promotional@embratur.gov.br`.** É o endereço listado na página de serviço do
> gov.br, e o domínio `embratur.gov.br` **não tem registro MX nem A** — está desativado. O
> e-mail volta. O domínio vivo é `embratur.com.br`.

Não existe termo de uso de imagens publicado em lugar nenhum do `embratur.com.br`. O item
"Banco de imagens" do menu só aponta para o Flickr.

## 4. Já existe um processo formal — e onde ele não serve

**`gov.br/pt-br/servicos/obter-autorizacao-de-uso-de-imagens-e-videos-da-embratur`**

- **Quem pode:** agências de turismo nacionais/internacionais, operadoras, imprensa
- **Passo 1:** selecionar as fotos no Flickr da Embratur
- **Passo 2:** e-mail solicitando, **explicando a finalidade**; a Embratur avalia
- **Passo 3:** aprovado, a Embratur envia o **"Termo de utilização de fotos e vídeos"** para
  preencher, assinar e devolver
- **Passo 4:** recebe link de download **válido por 7 dias**
- **Custo:** gratuito. **Prazo:** 1 a 5 dias úteis

**A página está desatualizada** — cita o extinto Instituto Brasileiro de Turismo, a revogada
Lei 8.181/1991 e o e-mail morto. Mas o que importa é que **o instrumento existe**: não estamos
pedindo para inventarem um termo, e sim para aplicarem um que já têm.

**Cuidado para não confundir entrega com licença.** O link de 7 dias é **mecanismo de
entrega**; quem autoriza o uso é o **Termo assinado**. Não presumir que o TTL do link implique
TTL da permissão — são coisas diferentes, e o erro leva a desenhos ruins:

- Se o Termo concede uso permanente com crédito → baixa uma vez e pronto. O link expirar é
  irrelevante e **não há gap para negociar**.
- Se o Termo concede uso por prazo limitado → **re-baixar não re-licencia**. Um job de
  "refresh a cada 7 dias" produziria bytes novos sob permissão vencida: teatro de compliance,
  não conformidade.

Some-se que o processo é **manual** (passo 2 = e-mail avaliado caso a caso, 1–5 dias úteis).
Não há API. Refresh periódico significaria um chamado humano por semana, para sempre, num
catálogo que cresce — e re-baixar os mesmos arquivos indefinidamente do servidor deles é o
tipo de automação que azeda a relação institucional.

**Portanto: não presumir o gap — perguntar.** A carta pede explicitamente o que o Termo permite
quanto a (i) reter os arquivos após o download, (ii) servir a partir de infraestrutura própria,
(iii) prazo de vigência. Se a resposta for "uso permanente com crédito", o problema nunca
existiu.

Enquadramento: a Norteia é plataforma de turismo — mais próxima de *operadora/agência* que de
imprensa. Liderar por aí.

## 5. Precedentes a citar

- **MTur `mturdestinos`: 5.936 fotos em Public Domain Mark 1.0** (license id 10, confirmado).
  `visitbrasil` e `embratur` são id 0. **O mesmo aparelho federal de turismo já liberou acervo
  comparável ao domínio público.** É o argumento mais forte.
- **Agência Brasil / EBC** — uso livre inclusive comercial, **crédito obrigatório**, com
  ressalva para fotos de agências parceiras. Modelo federal funcionando de "reuso livre +
  crédito".
- **Programa "Parceiros" da Embratur** (`/institucional/parceiros/`) — trilha formal de
  parceria institucional existe; a página não publica critérios. Perguntar.

## 6. Risco a precificar desde o primeiro contato

**A Embratur provavelmente não detém direitos integrais sobre todas as 29.471.** Os créditos
apontam terceiros (no acervo da Embratur, "Renato Vaz/Embratur"; no do MTur, "Governo do
Distrito Federal/Bento Viana"). Só se sublicencia o que se detém — espere concessão parcial.

Some-se que a Embratur foi publicamente envolvida em disputa de direito autoral em 2019 (uso
de tipografia em logo de campanha), então o jurídico deles tende a ser cauteloso.

**Por isso a pergunta (c) da carta vai no primeiro contato, não depois:** se a Embratur só
detém limpo alguns milhares das 29 mil, a negociação muda de forma inteira — e é melhor saber
antes de assinar um Termo.

## 7. Sequência recomendada

1. **E-mail para `imprensa@embratur.com.br`, com cópia para `marcabrasil@embratur.com.br`**
   (minuta abaixo). Caminho humano e rápido, mais provável de produzir o Termo.
2. **No mesmo dia, protocolar no Fala.BR** pedindo política, unidade detentora e cópia do
   Termo. Cria rastro documental e relógio de 20 dias que o e-mail não tem.
3. **Sem resposta em ~10 dias úteis:** escalar para Ouvidoria, depois Presidência.
4. **Plano que não depende de ninguém:** o acervo `mturdestinos` (Public Domain Mark) já está
   utilizável hoje e já foi indexado pela POC. Começar por ele tira a dependência do caminho
   crítico.

---

## Minuta do pedido

> Revisar antes de enviar. Preencher `[...]`. Conferir se a descrição da Norteia bate com o
> posicionamento institucional que vocês querem apresentar.

**Assunto:** Solicitação de autorização de uso de acervo fotográfico — plataforma Norteia

Prezados,

Dirijo-me à Coordenação-Geral de Publicidade e Propaganda e à Assessoria de Imprensa da
Embratur para solicitar autorização de uso do acervo fotográfico institucional "Visit Brasil".

**Quem somos.** A Norteia é uma plataforma brasileira de turismo que organiza e disponibiliza
informação qualificada sobre destinos e atrativos de todos os estados do país. Operamos um
processo de curadoria que só publica registros validados, com verificação de confiabilidade —
nosso interesse é qualidade e precisão na representação dos destinos brasileiros.

**O que solicitamos.** Autorização para utilizar as fotografias do acervo Visit Brasil
(flickr.com/photos/visitbrasil), com as seguintes condições de uso, que declaramos de forma
transparente:

1. Download e **armazenamento em infraestrutura própria** (Amazon S3), com exibição contínua a
   partir dos nossos servidores. Registramos que o download é pontual — não haveria acesso
   automatizado ou recorrente aos sistemas da Embratur.
2. Exibição em **plataforma de natureza comercial**, em contexto editorial e informativo sobre
   os destinos retratados.
3. **Atribuição integral**: crédito ao fotógrafo e à Embratur em cada imagem, no padrão que a
   Agência determinar, com link para visitbrasil.com.

**Contrapartidas.** Oferecemos crédito permanente e visível, link institucional para os canais
oficiais da Embratur, e a promoção qualificada de destinos brasileiros a um público em intenção
de viagem — objetivo convergente com o previsto no art. 4º da Lei nº 14.002/2020.

**Precedente.** Registramos que o Ministério do Turismo mantém o acervo "MTur Destinos"
(flickr.com/photos/mturdestinos), com 5.936 imagens sob **Public Domain Mark 1.0**, permitindo
reuso irrestrito. Solicitamos que seja avaliada a possibilidade de tratamento análogo, ou de
licença específica nos termos do **art. 14, II, da Lei nº 14.002/2020**, que autoriza
expressamente a Embratur a celebrar contratos com entidades privadas com fins lucrativos,
inclusive mediante licenças e cessão de direitos de uso.

**Pedidos objetivos:**

a) É possível o **envio do Termo de utilização de fotos e vídeos vigente para análise prévia**,
   antes da formalização do pedido?
b) A concessão é por **prazo indeterminado** ou determinado? Se determinado, qual o prazo, e
   ele corre **por foto, a partir da data de aquisição daquele acervo**?
c) O Termo autoriza **reter os arquivos após a expiração do link de 7 dias** e **exibi-los a
   partir de servidores próprios**, ou cada exibição deve consumir origem da Embratur?
d) Autoriza **uso comercial em plataforma com fins lucrativos**, ou restringe-se a divulgação
   turística e imprensa?
e) A **finalidade declarada é vinculante e restrita** — mudança de escopo do produto exige novo
   Termo?
f) Qual a **string exata de atribuição** exigida, e o fotógrafo individual deve ser nomeado?
g) **Sublicenciamento/transferência** a terceiros (usuários, parceiros, consumidores de API) é
   permitido?
h) Para as fotos solicitadas: estão **dentro da janela de utilização** vigente, e há
   **autorizações de direito de imagem** arquivadas para as pessoas retratadas?
i) Qual parcela do acervo tem direitos **integralmente detidos pela Embratur** e apta a
   sublicenciamento?
j) Há programa de parceria institucional aplicável a plataformas digitais de turismo?

> As perguntas (b) e (h) são as decisivas e derivam dos contratos de captação da própria
> Embratur, que fixam utilização em 5 anos. Se a resposta a (b) for prazo determinado, o acervo
> não serve para registro canônico sem um mecanismo de expiração — decisão de produto, não de
> engenharia.

Permanecemos à disposição para reunião ou para formalização por instrumento próprio.

Atenciosamente,
[Nome] — [cargo], Norteia
[e-mail] · [telefone] · [CNPJ]
