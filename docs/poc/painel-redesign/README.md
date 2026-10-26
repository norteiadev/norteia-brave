# Painel Brave — POC do redesenho

Protótipo navegável (HTML único, dados simulados) do novo Painel e o relatório de cobertura de API que o acompanha. Referência visual e de comportamento para portar o Painel para `dashboard/` (Next.js).

- `index.html` — a POC. Telas: Visão geral, Registros (Revisão / Mar / Falha em lista agrupada, substitui o kanban), Duplicados, Varreduras, Custo & LLM, Configuração. Simula o pipeline ao vivo com o motor ligado.
- `relatorio-api.html` — auditoria de 118 componentes da POC contra a API do coletor: o que já existe, endpoints a criar ou estender, complexidade e plano em fases.
- `norteia-logo.svg`, `norteia-marca.svg` — recortes do logo (`norteia-frontend/src/assets/logo-norteia-vertical.svg`), usados como máscara CSS.

Para abrir, sirva a pasta por HTTP (as máscaras SVG não carregam via `file://`):

```
python3 -m http.server -d docs/poc/painel-redesign 8000
```

Os números da POC são inventados com seed fixa. O relatório reflete o código em 26/10/2026.
