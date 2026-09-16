# ADR-003: Watermark e idempotência

- **Status:** accepted

## Contexto
Janela móvel sem estado confirmado perde rastreabilidade ou repete dados.

## Decisão
Ler watermark, aplicar sobreposição de 24h configurável, coletar todas as páginas, validar Bronze e só então gravar watermark. Silver deduplica por chave natural e versão. Falhas, respostas incompletas e `has_more` no limite não avançam watermark.

## Alternativas
Atualizar por página; rejeitada porque falha intermediária cria lacuna. Atualizar no início; rejeitada por perda.

## Consequências
Retries são seguros, mas podem regravar Bronze apenas com política explícita de objeto existente. Lock/conditional write será necessário para concorrência AWS.

## Riscos
Atraso da fonte exige sobreposição/backfill e monitoramento de freshness.

## Refinamento Fase 1.1

ADR-007 substitui a chave incompleta e detalha publicação/CAS: fonte + site + endpoint + tag, backfill sem avanço, prepared antes do CAS e manifesto terminal depois. Bronze divergente jamais é substituída; lock/CAS local já existem, sem promessa distribuída.
