# Descrições geradas — Sonnet 4.5 vs Gemini (POC 2026-08-19)

Saída literal da POC (`scripts/poc/gemini_copywriter_poc.py`), prompt de produção
(`COPYWRITER_SYSTEM` + `_build_context`), sem edição. Contexto do Places idêntico nas três
colunas; muda só o modelo e a fonte de fato.

Legenda: **negrito** = dado factual verificável afirmado pelo modelo.

---

## A · Convento da Penha — Vila Velha/ES (atrativo famoso)

### A1. claude-sonnet-4-5 + web_search · in=11.279 out=704 · 1 busca · **$0,0544**

> No alto de um penhasco de **154 metros**, cercado por parte preservada da **Mata
> Atlântica**, o Convento da Penha parece brotar da rocha viva, com vista panorâmica para
> Vila Velha, Vitória e o Oceano Atlântico. A subida exige fôlego, mas cada degrau aproxima
> o visitante de um dos **santuários marianos mais antigos do Brasil** e de uma paisagem que
> abraça a imensidão do mar.
>
> Fundado em **1558** pelo **frei espanhol Pedro Palácios**, que trouxe consigo o **Painel de
> Nossa Senhora das Alegrias**, o conjunto arquitetônico colonial respira simplicidade e
> devoção. Nas paredes da capela, obras paisagísticas de **Vitor Meireles, entregues em
> 1877**, e pinturas sacras de **Benedicto e Pedrina Calixto, dos anos 1920**, contam
> histórias de fé, lendas de proteção e milagres atribuídos à Padroeira do Espírito Santo.
> **Tombado pelo IPHAN em 1943**, o convento guarda ainda o interior em **cedro entalhado
> pelo português José Fernandes Pereira** e o **altar rococó de 1800**, restaurado e composto
> por **mais de 200 peças de 19 tipos de mármore**.
>
> Visite de manhã cedo, quando o silêncio da capela amplia a sensação de recolhimento e a luz
> rasante ilumina a baía com suavidade. Procure a janelinha dentro da capela, o ponto mais
> fotografado, com enquadramento perfeito da paisagem. Nas varandas externas, observe como a
> arquitetura se encaixa na pedra, janelas e muros desenhando molduras naturais para a
> vastidão do Atlântico. Leve água, calce sapatos confortáveis e reserve tempo para
> contemplar a mata ao redor, testemunha verde de mais de quatro séculos e meio de história.

**Fatos afirmados: 11.**

---

### A2. gemini-3.5-flash-lite, SEM busca, só Places · in=638 out=321 · **$0,00099**

> No alto de um penhasco rochoso que desafia o horizonte em Vila Velha, o Convento da Penha
> ergue-se como um dos **santuários mais antigos do Brasil**, fundado no **século XVI** pelo
> **Frei Pedro Palácios**. A caminhada pela ladeira arborizada, conhecida como **Ladeira dos
> Sete Voltas** ⚠️, revela aos poucos a imponência da construção histórica, enquanto a brisa
> do mar acompanha cada passo rumo ao topo. Ao cruzar os portões da igreja, o silêncio e a
> simplicidade arquitetônica convidam à contemplação, ecoando séculos de devoção e histórias
> que moldaram a identidade capixaba. Do alto do pátio principal, descortina-se uma vista
> panorâmica magnifica ⚠️ que abraça a baía e as cidades de Vila Velha e Vitória, em um
> encontro marcante entre a imensidão do oceano e o verde da Mata Atlântica. Para aproveitar
> a visita com serenidade, o início da manhã revela-se o momento ideal para subir sem pressa,
> desfrutar da calmaria do espaço sagrado e capturar a luz suave do amanhecer refletida nas
> águas e na fachada centenária. Vale a pena caminhar pelo **Campinho** ⚠️ com atenção aos
> detalhes da vegetação nativa e às antigas edificações, levando calçados confortáveis para a
> caminhada, uma garrafa de água para a subida e roupas leves, permitindo-se absorver por
> completo a atmosfera de paz e a grandiosidade da paisagem.

**Fatos afirmados: 3** (século XVI, Frei Pedro Palácios, "mais antigos do Brasil").
⚠️ **Problemas:** "Ladeira dos Sete Voltas" e "Campinho" são topônimos não fornecidos no
contexto — o modelo os produziu de memória, sem verificação (concordância errada em
"dos Sete Voltas" sugere reconstrução, não citação). "magnifica" sem acento.

---

### A3. gemini-3.5-flash-lite + fatos determinísticos · in=789 out=294 · **$0,00097**

> No alto de um penhasco a cerca de **154 metros de altitude**, o Convento da Penha coroa a
> paisagem de Vila Velha e convida o viajante a uma pausa contemplativa com vista para a
> baía. Fundado em **1558** pelo **frei espanhol Pedro Palácios**, este é um dos **santuários
> marianos mais antigos do Brasil**, carregando séculos de devoção e significado cultural
> para quem visita o Espírito Santo. **Tombado pelo IPHAN em 1943**, o conjunto arquitetônico
> guarda preciosidades artísticas em seu interior, como o **altar rococó datado de 1800**, os
> trabalhos em **cedro entalhado por José Fernandes Pereira** e as **obras de Vitor Meireles
> entregues em 1877**. Para aproveitar melhor a jornada, a recomendação é iniciar a subida
> logo nas primeiras horas da manhã, quando o movimento é menor e o silêncio da igreja
> convida à introspecção. Vale a pena caminhar sem pressa pelo trajeto íngreme para observar
> os detalhes da arquitetura histórica e a vegetação ao redor. Leve água para o caminho, uma
> máquina fotográfica ou o celular a postos para registrar os mirantes ao longo do percurso e
> repare na harmonia entre a imponência da rocha e a delicadeza do trabalho sacro que resiste
> ao tempo.

**Fatos afirmados: 7 — os 6 fornecidos, todos corretos, mais nenhum inventado.**
Nenhum topônimo alucinado. Comparável ao Sonnet em densidade factual.

---

### A4. gemini-3.6-flash, SEM busca · in=638 out=1.946 · **$0,00778**

> Elevado a **mais de cem metros** sobre o mar, o olhar alcança o encontro entre a **Baía de
> Vitória**, a imensidão do Atlântico e o contorno das cidades capixabas. O Convento da Penha
> ergue-se no topo de um imponente penhasco rochoso em Vila Velha, cercado pelo verde
> preservado da **Mata Atlântica** e banhado pela brisa constante do litoral. Fundado no
> **século XVI** pelo **frei franciscano Pedro Palácios**, o santuário é um dos marcos
> religiosos e arquitetônicos mais antigos do Brasil. Suas paredes de pedra, o altar-mor e os
> detalhes barrocos guardam séculos de devoção e memórias que se entrelaçam com a própria
> história do Espírito Santo, acolhendo visitantes de todos os perfis com a mesma serenidade.
> Para vivenciar o local em sua plenitude, vale começar a visita no início da manhã, momento
> em que a temperatura é amena para a subida e o silêncio no interior do templo proporciona
> instantes de profunda contemplação. Durante a travessia rumo ao cume, mantenha o olhar
> atento à vegetação, pois é frequente avistar aves nativas e **pequenos saguis** ⚠️ entre os
> galhos. Nos mirantes próximos à capela, aproveite para registrar a vista panorâmica que
> enquadra a **Terceira Ponte** e o recorte da costa. Leve uma garrafa de água, use calçados
> confortáveis para caminhar sem pressa e dedique alguns minutos para observar o contraste
> impressionante entre a edificação secular e o horizonte urbano ao redor.

⚠️ **Problemas:** "pequenos saguis" é fauna afirmada sem nenhuma fonte no contexto — o tipo
exato de invenção que o prompt proíbe. Gastou **1.946 tokens de saída** (6x o flash-lite)
para entregar menos fato.

---

## B · Cachoeira da Fumaça — Alegre/ES (atrativo obscuro) — **o caso que decide**

### B1. claude-sonnet-4-5 + web_search · in=13.799 out=712 · 1 busca · **$0,0621**

> No coração da **serra do Caparaó capixaba**, a Cachoeira da Fumaça despenca de **144 metros
> de altura**, a **maior cachoeira com água perene do Espírito Santo**. O nome vem do
> fenômeno que nasce do encontro da força e do volume: a água bate nas rochas e ergue nuvens
> de gotículas finas, como véus de fumaça que refrescam o vale inteiro. Esse "fumo" de
> respingos tem efeito refrescante, transformando o espaço em um microclima úmido e
> acolhedor.
>
> O parque que a protege nasceu em **1984**, quando a área foi desapropriada e transformada
> em reserva, guardando as nascentes e os remanescentes de mata atlântica do **rio Braço
> Norte Direito, afluente do Itapemirim**. Em **2002**, integrou o **Corredor Ecológico da
> Mata Atlântica Central**, reforçando seu papel na conservação de uma biodiversidade que
> inclui **lontras, maitacas e gatos-do-mato-pequenos**.
>
> Para viver a experiência completa, visite o mirante na estrada de acesso antes de entrar:
> de lá você contempla e fotografa a queda inteira, emoldurada pela mata fechada. Dentro do
> parque, explore o poço formado na base da cachoeira, onde a água gelada convida ao banho
> mais restaurador. Vá nas primeiras horas da manhã, quando a luz baixa ilumina as gotículas
> em suspensão e forma raios dourados cortando a névoa. Leve protetor solar biodegradável,
> roupa de banho e uma toalha extra, porque a vontade de mergulhar é inevitável. Observe os
> detalhes: bromélias penduradas nas pedras úmidas, o barulho da água que abafa qualquer
> outro som, a sensação de frescor na pele antes mesmo de tocar o rio.

**Fatos afirmados: 8.**

---

### B2. gemini-3.5-flash-lite, SEM busca, só Places · in=581 out=366 · **$0,00109**

> O som da água despencando ganha força conforme a trilha avança pela vegetação da zona rural
> de Alegre, revelando aos poucos a imponência da Cachoeira da Fumaça. Cercada pelo verde
> característico da paisagem capixaba, a queda d'água forma uma névoa fina que dá nome ao
> lugar e convida a uma pausa contemplativa logo no primeiro olhar. Para quem busca uma
> conexão genuína com a natureza, o espaço traduz a essência do interior do estado, onde o
> tempo parece desacelerar entre o canto dos pássaros e o balanço das árvores ao vento. O
> poço que se forma logo abaixo da queda é um convite irresistível para renovar as energias,
> embora a temperatura da água peça um mergulho corajoso e revigorante. Para aproveitar
> melhor a visita, o ideal é chegar nas primeiras horas da manhã, quando a luz do sol
> atravessa a copa das árvores e cria reflexos dourados na água, revelando detalhes únicos da
> paisagem. Os melhores ângulos para registro fotográfico surgem das rochas ao redor do poço
> principal, que permitem capturar a grandeza da queda em contraste com a delicadeza da
> névoa. Na mochila, vale a pena incluir roupas confortáveis para a caminhada, calçados
> firmes que garantam estabilidade no terreno e disposição para observar os detalhes da fauna
> e da flora locais, desde pequenas flores silvestres até borboletas que povoam o caminho.
> Observar o movimento das águas e sentir a brisa fresca que sopra da cachoeira é uma
> experiência que acolhe diferentes perfis de viajantes, lembrando que o Brasil real guarda
> belezas simples e profundas bem longe dos roteiros tradicionais.

**Fatos afirmados: ZERO.** Nada de altura, parque, rio, fauna, ano. Bem escrito, sem
alucinar — e **factualmente oco**. Serviria para qualquer cachoeira do Brasil. Este é o
resultado esperado para a maioria dos atrativos numa varredura nacional.

---

### B3. gemini-3.5-flash-lite + fatos determinísticos · in=735 out=341 · **$0,00107**

> O som da água que despenca de **cento e quarenta e quatro metros de altura** ⚠️ anuncia a
> grandiosidade da Cachoeira da Fumaça, no interior de Alegre. Ao avistar a **maior queda de
> água perene do Espírito Santo**, a sensação é de pequeno diante da força da natureza, uma
> névoa fina suspensa no ar que dá nome ao lugar e refresca quem se aproxima. O **rio Braço
> Norte Direito, afluente do rio Itapemirim**, molda este cenário onde a água ganha um tom
> cristalino e surpreende pela temperatura revigorante, convidando a um mergulho profundo no
> poço calmo que se forma logo abaixo. Protetora de uma biodiversidade rica, a região abriga
> refúgios seguros para **lontras, maitacas e gatos do mato pequenos**, fazendo parte do
> **Parque Estadual Cachoeira da Fumaça, criado em mil novecentos e oitenta e quatro** ⚠️, e
> integrando o **Corredor Ecológico da Mata Atlântica Central**. Para aproveitar melhor a
> visita, chegue nas primeiras horas da manhã quando a luz do sol atravessa a neblina da
> queda e cria arco-íris efêmeros perfeitos para fotografias contemplativas. Vale a pena
> caminhar com calma pelas trilhas vestindo calçados confortáveis para caminhada, carregar
> uma garrafa de água para se manter hidratado e levar uma troca de roupa extra, pois o vapor
> da cachoeira acompanha cada passo. Observe com atenção as copas das árvores em busca de
> aves coloridas e preste ouvido atento aos sons da floresta que abraça toda a paisagem.

**Fatos afirmados: 6 — os 6 fornecidos, todos corretos, nenhum inventado.** Densidade
factual equivalente à do Sonnet, a **1/58 do custo**.
⚠️ **Único defeito:** números **por extenso** ("cento e quarenta e quatro metros", "mil
novecentos e oitenta e quatro"). Corrigível com uma linha no `COPYWRITER_SYSTEM`
("escreva números em algarismos").

---

## Leitura

| | fatos corretos | fatos inventados | custo/atrativo |
|---|---|---|---|
| Sonnet + busca (famoso) | 11 | 0 | $0,0544 |
| flash-lite só Places (famoso) | 3 | 2 topônimos ⚠️ | $0,00099 |
| flash-lite + fatos (famoso) | 7 | **0** | $0,00097 |
| 3.6-flash só Places (famoso) | 4 | 1 (fauna) ⚠️ | $0,00778 |
| **Sonnet + busca (obscuro)** | **8** | 0 | $0,0621 |
| **flash-lite só Places (obscuro)** | **0** | 0 | $0,00109 |
| **flash-lite + fatos (obscuro)** | **6** | **0** | $0,00107 |

Três conclusões que os textos sustentam sozinhos:

1. **O gargalo nunca foi o modelo — é a fonte de fato.** O mesmo flash-lite escreve prosa
   oca ou prosa densa dependendo apenas do que chega no prompt.
2. **Sem fonte de fato, o modelo pequeno é pior que oco: ele preenche.** Os topônimos do A2
   e os saguis do A4 são exatamente o modo de falha que a lane existe para evitar.
3. **Com fatos servidos, o flash-lite não inventa** — 13 fatos fornecidos ao todo, 13 usados
   corretamente, zero acrescentados. A fidelidade é a parte que já funciona.
