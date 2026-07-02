# Vamos fazer um refactory no Brave

Objetivo principal da plataforma Brave: publicar dados territoriais turísticos de todo o Brasil (27 UFs) de forma confiável e segura.

## O que é o Brave?

O Brave é a **Pipeline Brave (Collector)** da Norteia: um serviço 24/7 que **descobre, limpa, deduplica, normaliza, pontua e publica** dados territoriais turísticos de todo o Brasil (27 UFs) através de fontes de dados externas.

## O que é a Pipeline Brave?

A Pipeline Brave é um serviço que descobre, limpa, deduplica, normaliza, pontua e publica dados territoriais turísticos de todo o Brasil (27 UFs), a partir de fontes de dados externas.

### Descoberta (discover)
O Brave descobre destinos e atrativos turísticos de todo o Brasil (27 UFs) através de fontes como:

- mTur (Ministério do Turismo)
- TripAdvisor
- Entrada Manual (CRUD)

A descoberta é realizada através de um processo de scraping de websites e APIs públicas.
A descoberta pode ser realizada por fonte específica ou por todas as fontes disponíveis.
A descoberta pode ser realizada por UF ou por todo o Brasil.
A descoberta pode ser ligada/desligada via Painel de Controle.
A descoberta associa um Municipio, Estado e a um Destino Turistico e um Destino Turistico é associado a um ou mais Atrativos Turísticos.
A descoberta envia um atrativo para DLQ quando não consegue associar ao Municipio, Estado e Destino Turistico.
A descoberta coleta TODAS as informações necessárias pela API da Norteia (projeto: norteia-api), com base na disponibilidade de informações na fonte de dados externa e associa ao Atrativos Turísticos e Destinos Turísticos.
A descoberta adiciona os atrativos e destinos turisticos no status nascente da state machine NASCENTE -> RIO -> MAR

### Promoção para status rio (promote)

A promoção para status rio é realizada através de um processo de analise dos dados e confiabilidade das informações.
A promoção coleta automaticamente os atrativos turisticos do status nascente em batches de 100 para processamento.
A promoção utiliza um sistema de busca por availiações do atrativo turistico com base da fonte de dados externa:
- mTur (Ministério do Turismo)
  - Pesquisa pelo atrativo turistico no Google atraves da API Google Places
    - Coleta todas as informações possíveis sobre o atrativo turistico como: horario de funcionamento, preço, formas de contato (email, telefone, site, instagram), etc e adiciona ao atrativo turistico.
    - Complementa as informações faltantes fazendo uma busca pelo atrativo em sites conhecidos sobre turismo no Brasil utilizando LLMs (configuravel no painel)
    - Pesquisa pelas ultimas avaliações do atrativo turistico e com base nas datas classifica se o atrativo esta aberto e funcionando.
      - IMPORTANTE: Caso não haja availiações ou as ultimas avaliações tenham mais de 3 meses enviar o atrativo para DLQ para revisão manual.
- TripAdvisor
  - Pesquisa pelo atrativo turistico no TripAdvisor
    - Coleta todas as informações possíveis sobre o atrativo turistico como: horario de funcionamento, preço, formas de contato (email, telefone, site, instagram), etc e adiciona ao atrativo turistico.
      - IMPORTANTE: Os dados do atrativo requeridos pela API da Norteia que não estivem disponiveis no TripAdvisor serão complementados com uma busca pelo atrativo em sites conhecidos sobre turismo no Brasil utilizando LLMs (configuravel no painel)
    - Pesquisa pelas ultimas avaliações do atrativo turistico e com base nas datas classifica se o atrativo esta aberto e funcionando.
      - IMPORTANTE: Caso não haja avaliações ou as ultimas avaliações tenham mais de 3 meses enviar o atrativo para DLQ para revisão manual.
- Entrada Manual (CRUD)
  - Operação CRUD completa para atrativo e destino turistico.

### Promoção para status mar (promote)
- Atrativos com score confiável (score >= 80) são promovidos para status mar.
- Atrativos com score não confiável (score < 80) são enviados para DLQ para revisão manual.
- A promoção coleta automaticamente os atrativos turisticos do status rio em batches de 100 para processamento.

### Motor
- O motor controla o funcionamento total do sistema Brave descoberta, promoção para rio e promoção para mar
- O motor pode ser ligado/pausado/desligado via Painel.
  - Ligado: O motor está funcionando e executando as operações de descoberta, promoção para rio e promoção para mar.
  - Pausado: O motor pausa toda a operação de descoberta, promoção para rio e promoção para mar e habilita o Kanban para edição de cards de atrativo/destino.
  - Desligado: O motor está desligado e não executando as operações de descoberta, promoção para rio e promoção para mar.

### Operação
- O Painel da Brave controla a operação COMPLETA do sistema Brave, desde a descoberta até a promoção para status mar.
- O Painel é a unica tela de controle do sistema Brave.
- O Painel deve persistir todas as configurações e estados de funcionamento (motor ligado, motor desligado, fontes de dados externas, etc)
- O Painel deve ter uma tela de controle para cada status da state machine (NASCENTE, RIO, MAR) com opção de adição de novos statuses
- O Painel deve conter logs de todas as operações realizadas pelo sistema Brave com opção de filtragem por operação, status, data e hora.
- A operação nascente->rio->mar DLQ, erros devem funcionar no estilo Kanban
- Cada atrativo e destino se torna um card no Kanban e pode ser editado (todos os campos devem ser editaveis) em todos os statuses (NASCENTE, RIO, MAR).
- O painel deve ter feedback visual durante o processo de descoberta, promoção para rio e promoção para mar.
- O Painel deve bloquear o Kanban para edição de cards de atrativo/destino enquanto estiver ligado o motor.
- Cada coluna do Kanban deve carregar os cards em tempo real e ter lazy loading no scroll infinito.

### Premissas técnicas
 - Implementar uma state machine para o processo de descoberta, promoção para rio e promoção para mar (NASCENTE -> RIO -> MAR) com controle de falhas e retries.
 - O sistema deve comportar a adição de novas fontes externas sem impactar o código existente.
 - Cada fonte externa implementa contratos e aplica abstração de código.
 - O sistema Brave deve seguir o principio SOLID, Clean Code, DRY, KISS, YAGNI.
 - O sistema Brave deve utilizar um framework Python.
 - O sistema deve utilizar Next.js para frontend.
 - O sistema deve utilizar Tailwind CSS para estilos.
 - O sistema deve utilizar Shadcn UI para componentes.
 - O sistema deve utilizar PostgreSQL como banco de dados.
 - O sistema deve utilizar Celery para queue.
 - O sistema deve utilizar Docker para containerização.
 - TODAS as funcionalidades do sistema devem ser configuraveis através do Painel.
 - Cada fonte externa tem seu próprio Domain

### Arquitetura
- O sistema Brave deve ser um serviço monolito.
- O sistema Brave deve ter camada de persistencia.
- O sistema Brave deve ter camada de servicos.
- O sistema Brave deve ter camada de controllers.
- O sistema Brave deve ter camada de repositories.
- O sistema Brave deve ter camada de models.
- O sistema Brave deve ter camada de dtos.
- O sistema Brave deve ter camada de exceptions.
- O sistema Brave deve ter camada de tests.