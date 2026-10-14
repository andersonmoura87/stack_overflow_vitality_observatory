# ADR-009: Gold v1 descritiva a partir da Silver de perguntas

- **Status:** accepted

## Contexto e alternativas

1. Calcular vitalidade na transformação Silver mistura contrato de preservação com indicadores e impede reconstruir o corte.
2. Uma Gold descritiva, lida da geração Silver committed, publica agregados versionados sem nova coleta e sem alterar a Silver.
3. Um único indicador “respondida”, ou um modelo de tempo até resposta, responderia a uma pergunta que os campos atuais não sustentam.

## Decisão

Adotar a alternativa 2. A Gold v1 lê catálogo e observações da Silver de perguntas. O grão mensal é `site + creation_month + as_of`; o grão de tag acrescenta `tag`. A pergunta conta uma vez no mês e uma vez por tag.

O corte explícito é obrigatório para publicar. Métricas vêm da última observação com `collected_at <= as_of`, com o desempate da Silver no mesmo instante. Tags, fechamento e `creation_at` vêm da versão de conteúdo cuja validade observacional contém `as_of`, não das métricas gravadas no fim do intervalo. `creation_month` vem de `creation_at` em UTC. `--dry-run` pode apenas exibir o maior `collected_at`, marcado como derivado, e não o publica. `source_is_answered`, `has_answers` e `has_accepted_answer` permanecem separados. Nulo não vira zero nem falso, exceto a ausência de `accepted_answer_id`, que significa que nenhum identificador foi observado. Divergências conhecidas são contadas.

A saída é JSONL canônico e manifesto imutável, no mesmo estilo da Silver, sem ponto flutuante binário. Percentis usam nearest rank inteiro. Concentração usa milionésimos truncados por termo. A exposição em segundos acompanha a série porque o corte censura perguntas recentes.

## Consequências

Tempo até resposta, sobrevivência, respostas, comentários, NLP e inferência causal ficam fora desta versão. O manifesto carrega a cobertura Silver, inclusive quarentena. Duas publicações do mesmo corte na mesma raiz são byte a byte iguais. Um corte novo não reescreve a geração anterior. Não há ponteiro mutável: o manifesto impresso pelo comando é o commit daquele corte.
