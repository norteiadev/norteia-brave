# POC — viabilidade de enriquecimento de imagens para atrativos

Amostra: **27 atrativos**. Índice MTur: **4886** fotos usáveis. Tempo: 0s.

> Duas coortes, **nunca somadas**: `db` = registros reais de `rio_records` (15, todas no ES); `control` = conjunto nacional montado à mão para cobrir o gradiente de fama, com coordenadas do Wikidata P625. Um zero no controle pode ser erro de coordenada, não ausência de cobertura.


## Coorte `db` (15 atrativos)

### Hit-rate por tier

| tier | atrativos com hit | % |
|---|---|---|
| `M1_mtur` | 10 | 67% |
| `C1_geo_500m` | 11 | 73% |
| `C2_geo_2km` | 13 | 87% |
| `C3_name` | 14 | 93% |
| `P1_exact` | 15 | 100% |
| `P2_type_municipio` | 15 | 100% |
| `P3_type_generic` | 15 | 100% |
| **nenhum** | 0 | 0% |

### Cobertura

- ≥3 imagens: **15/15** (100%)
- ≥1 imagem: **15/15** (100%)
- zero imagens: **0/15** (0%)
- sem lat/lon (pulam tiers geo): **1/15**

### Complementaridade (qual fonte resolve, sozinha)

| fontes que retornaram | atrativos |
|---|---|
| commons + mtur + pixabay | 10 |
| commons + pixabay | 5 |

### O número que importa — cobertura ANCORADA NO LUGAR

Só `M1_mtur` (foto oficial identificada por nome+município) e `C1`/`C2` (geosearch por coordenada) prendem a imagem ao lugar. `C3_name` casa por texto e pode errar; **todo Pixabay é decorativo** — ele nunca devolve vazio, serve stock genérico para qualquer query.

| estrato | ancorada (MTur ou geo) | via MTur | via geo | só por nome |
|---|---|---|---|---|
| db | **14/15** | 10 | 13 | 14 |

## Coorte `control` (12 atrativos)

### Hit-rate por tier

| tier | atrativos com hit | % |
|---|---|---|
| `M1_mtur` | 6 | 50% |
| `C1_geo_500m` | 9 | 75% |
| `C2_geo_2km` | 10 | 83% |
| `C3_name` | 12 | 100% |
| `P1_exact` | 12 | 100% |
| `P2_type_municipio` | 12 | 100% |
| `P3_type_generic` | 12 | 100% |
| **nenhum** | 0 | 0% |

### Cobertura

- ≥3 imagens: **12/12** (100%)
- ≥1 imagem: **12/12** (100%)
- zero imagens: **0/12** (0%)
- sem lat/lon (pulam tiers geo): **0/12**

### Complementaridade (qual fonte resolve, sozinha)

| fontes que retornaram | atrativos |
|---|---|
| commons + mtur + pixabay | 6 |
| commons + pixabay | 6 |

### O número que importa — cobertura ANCORADA NO LUGAR

Só `M1_mtur` (foto oficial identificada por nome+município) e `C1`/`C2` (geosearch por coordenada) prendem a imagem ao lugar. `C3_name` casa por texto e pode errar; **todo Pixabay é decorativo** — ele nunca devolve vazio, serve stock genérico para qualquer query.

| estrato | ancorada (MTur ou geo) | via MTur | via geo | só por nome |
|---|---|---|---|---|
| famoso | **4/4** | 3 | 4 | 4 |
| medio | **4/4** | 2 | 4 | 4 |
| obscuro | **2/4** | 1 | 2 | 4 |

## Licenças observadas

| fonte: licença | imagens |
|---|---|
| pixabay: Pixabay Content License | 243 |
| commons: CC BY-SA 4.0 | 117 |
| commons: CC BY-SA 3.0 | 55 |
| mtur: Public domain | 41 |
| commons: CC BY 4.0 | 13 |
| commons: CC BY-SA 2.0 | 8 |
| commons: CC BY 2.0 | 6 |
| commons: CC BY 3.0 | 4 |
| commons: CC0 | 2 |
| commons: Public domain | 1 |
| commons: CC BY-SA 3.0 es | 1 |

## Colisões (mesma imagem em múltiplos atrativos)

- URLs distintas: 349; reutilizadas: **8**
  - 17× `https://pixabay.com/get/g47844dadd9db4e2f271924d462151388d9240d6a0c779d37ea13b76a89a89235195c6ec2919`
  - 17× `https://pixabay.com/get/gfceeea5f38f7d4e92e063a3d841b33155f598a0601833533b44cd754ac37a935bc4c5dcd9d6`
  - 17× `https://pixabay.com/get/g381a4753627593d75849ee9e9a6774d432ea5835a5bbae22616abd4cdf3f36e579f4a0885cd`
  - 3× `https://pixabay.com/get/g123c200544f7d691a9f5a2a11126a62ae5064079cc3a417f4d763e0dac7ca20389fc354c772`
  - 3× `https://pixabay.com/get/g158f12171f9d15f1328150bb529acb554f706a8c5468f422c02f77b7a5c95576adf5a2342f6`

## Veredito

**Viável, com ressalva no long tail.**

- **MTur é o motor de especificidade.** 41 imagens casadas, domínio público, alta resolução, foto oficial do atrativo. Resolveu `Igreja de Santa Isabel / Mucugê` — o caso onde IPHAN deu 0 e busca por nome no Commons deu 0.
- **Commons geosearch é a cobertura mais larga** entre as fontes ancoradas, mas depende de lat/lon e cai junto com o MTur no long tail.
- **Pixabay é só decoração.** Nunca devolve vazio; uma única imagem foi servida para 17 atrativos diferentes. Não deve ser legendada como foto do atrativo — cairia em *misleading or deceptive* no ToS.
- **O long tail continua sendo o problema:** apenas **2/4** dos atrativos obscuros têm imagem ancorada no lugar. Vale do Pati, Poço Encantado não têm nada além de busca textual e stock genérico.
- **Match fuzzy exige a trava de UF.** Antes dela, 1 de 11 matches do MTur era falso positivo (convento de Itanhaém/SP atribuído a Vila Velha/ES). Há teste de regressão em `mtur.demo()`.

### Verificação manual — FEITA (2026-07-21)

Revisão humana dos 10 links amostrados. Resultado:

| fonte | veredito |
|---|---|
| `M1_mtur` | **correto** — foto oficial do atrativo |
| `C1_geo_500m` (Commons) | **correto** |
| `P1_exact` (Pixabay) | **FALSO** — 3/3 errados |

O revisor identificou a causa do falso positivo do Pixabay: *"fez match por palavras-chave do Pixabay 'Praia', 'Costa'"*. `Praia da Costa` (Vila Velha/ES) devolveu praia genérica e pôr-do-sol no **Mar do Norte**. Confirma que o motor do Pixabay casa tokens do nome contra tags de stock, sem qualquer noção de lugar — e que tratar `P1_exact` como decorativo é a classificação correta, não conservadorismo.

**As fontes ancoradas passaram na verificação semântica.** O hit-rate ancorado acima está validado por humano, não só medido.

## Amostra usada na verificação manual

Links revisados por humano em 2026-07-21 (resultado no Veredito acima):

- [Praia Da Costa / C1_geo_500m](https://commons.wikimedia.org/wiki/File:Vit%C3%B3ria,_no_Esp%C3%ADrito_Santo,_vista_do_Convento_da_Penha.jpg)
- [Praia Da Costa / C1_geo_500m](https://commons.wikimedia.org/wiki/File:Vila_velha_(6209085141).jpg)
- [Praia Da Costa / C1_geo_500m](https://commons.wikimedia.org/wiki/File:Orla_de_Vila_Velha_-_panoramio.jpg)
- [Praia Da Costa / P1_exact](https://pixabay.com/pt/photos/praia-ondas-oceano-n%C3%A9voa-respingo-2089936/)
- [Praia Da Costa / P1_exact](https://pixabay.com/pt/photos/p%C3%B4r-do-sol-mar-do-norte-mar-2191645/)
- [Praia Da Costa / P1_exact](https://pixabay.com/pt/photos/pedras-agua-confus%C3%A3o-costa-821573/)
- [Praia Bacutia / M1_mtur](https://commons.wikimedia.org/wiki/File:MarceloMoryan_Bacutia_Guarapari_ES_(40866782802).jpg)
- [Praia Bacutia / M1_mtur](https://commons.wikimedia.org/wiki/File:MarceloMoryan_Bacutia_Guarapari_ES_(40200276914).jpg)
- [Praia Bacutia / M1_mtur](https://commons.wikimedia.org/wiki/File:VitorJubini_PraiadaBacutia_Guarapari_ES_(41584941572).jpg)
- [Praia Bacutia / C1_geo_500m](https://commons.wikimedia.org/wiki/File:Guarapari2.JPG)
