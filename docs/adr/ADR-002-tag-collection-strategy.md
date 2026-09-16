# ADR-002: Estratégia de coleta por tags

- **Status:** accepted

## Contexto
`tagged=python;machine-learning` é interpretado como AND pela API, não como OR. Usá-lo por padrão enviesaria ou esvaziaria a amostra.

## Decisão
Executar uma request independente por tag. Grupos só serão aceitos quando explicitamente nomeados e semanticamente intencionais. Proveniência registra tag/grupo, parâmetros, janela, página, endpoint, site, filtro e data.

## Alternativas
Coleta geral com classificação posterior; possível em fase posterior, mas menos econômica para o recorte inicial. Lista semicolonada; rejeitada.

## Consequências
Mais requests e necessidade de deduplicação na Silver, porém composição auditável.

## Riscos
Tags mudam significado; análises devem expor composição e versão de configuração.
