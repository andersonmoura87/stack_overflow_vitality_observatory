# Runbook futuro: reconciliação API e Data Dump

A Fase 5.3.0 não instala comando, job nem diretório populado. Este runbook diz como ler o desenho e o que uma execução futura não pode concluir. Os contratos estão em [resultados](../data-contracts/reconciled_stackexchange_posts.md) e [conflitos e execuções](../data-contracts/reconciliation_conflicts_and_runs.md). A decisão está no [ADR-013](../adr/ADR-013-api-data-dump-reconciliation.md).

## O que não fazer agora

Não apontar a Gold para `data/reconciled/stackexchange/`. Não reescrever `silver/questions`, `silver/answers` nem `silver/data_dump`. Não tratar `DD-018` como reconciliação das Silvers. Não comparar `content_hash` entre API e dump. Não criar `accepted_at`. Não promover `last_activity_at` ou `last_edit_at` a instante de edição, aceite ou `valid_from`. Não interpretar ausência como exclusão.

Não há rede, dump real nem corte `verified` neste desenho.

## Entradas que a execução futura poderá nomear

Uma execução lógica nomeia três gerações já publicadas e consumíveis:

- Silver de perguntas da API;
- Silver de respostas da API;
- Silver do Data Dump.

Também nomeia `api_as_of`, `dump_cutoff` e `dump_cutoff_status`. O manifesto guarda identificadores e checksums, nunca caminho absoluto. Geração ausente, `partial`, não consumível ou de outro esquema é RC-014 e não publica resultado.

`reconciliation_run_id` sai desses insumos e da política `reconciliation_policy.v1`. Repetir os mesmos insumos reencontra a mesma execução. Bytes diferentes no mesmo identificador são RC-016 e não substituem a publicação anterior.

## Como ler os gates

| Gate | Leitura segura |
| --- | --- |
| `readable` | os arquivos nomeados conferem com o manifesto; ainda não é análise |
| `reconciliation_complete` | cada campo exigido tem estado e cada `RC-*` aplicável está no log |
| `analytically_usable` | descreve coexistência ou cobertura; não afirma evolução, exclusão ou causa |
| `gold_eligible` | reservado a uma Gold futura; com corte `unknown` ou `operator_declared` fica falso |

A Gold atual ignora os quatro gates e continua no `as_of` da Silver de perguntas da API.

## Cortes

`unknown` produz RC-011 e confiança `unordered`. Não ordene conteúdo, score, visualizações, contagem de respostas ou aceite.

`operator_declared` produz RC-012 e confiança `provisional`. A comparação pode ser mostrada com essa sinalização. O rótulo do operador não verifica o corte e não autoriza métrica causal.

Qualquer outro status, inclusive `verified`, é recusado.

## Conflitos bloqueadores

RC-001, RC-002, RC-003 e RC-004 param a fusão da identidade, do tipo, do pai ou da criação. RC-015 não escolhe duplicata. Nesses casos não há `resolved_value`. RC-008 e RC-009 só usam `known_over_unknown` quando um lado é nulo e o outro tem valor conhecido. Zero, falso, string vazia e lista vazia não são nulos.

RC-010 registra cobertura unilateral. A frase operacional é “não observado nesta fonte”, não “removido”.

## Limites de interpretação

O snapshot do dump não reconstrói edições intermediárias nem o instante da primeira resposta. Divergência sem ordem temporal não é evolução. Efeito da IA generativa permanece hipótese. Nenhuma contagem deste runbook foi medida sobre dados reais.
