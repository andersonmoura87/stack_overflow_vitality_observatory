# silver_data_dump_question_observations.v1

Observação das métricas de uma pergunta num snapshot. O grão é `site + question_id + ingestion_identity`. Há uma observação por pergunta em cada geração Bronze consumida. Outro snapshot tem outro `ingestion_identity` e portanto outra linha, mesmo quando o conteúdo não muda.

## Campos

| Campo | Tipo | Nulo |
| --- | --- | --- |
| schema_version | `silver_data_dump_question_observations.v1` | não |
| source_kind | `stackexchange_data_dump` | não |
| site | string | não |
| question_id | inteiro positivo | não |
| content_hash | hash do conteúdo observado nesse snapshot | não |
| score | inteiro | sim |
| view_count | inteiro não negativo | sim |
| answer_count | inteiro não negativo | sim |
| comment_count | inteiro não negativo | sim |
| accepted_answer_id | inteiro positivo; é um identificador no corte, não um instante | sim |
| last_activity_at | timestamp UTC com `Z` | sim |
| dump_publication_id | rótulo da publicação | não |
| dump_cutoff | timestamp UTC com `Z` | sim |
| dump_cutoff_status | `operator_declared` ou `unknown` | não |
| source_file_observed_at | timestamp UTC com `Z` | não |
| source_identity | SHA-256 hexadecimal | não |
| ingestion_identity | SHA-256 hexadecimal da geração Bronze | não |
| source_row_number | inteiro positivo | não |

`dump_cutoff`, `dump_cutoff_status` e `source_file_observed_at` vêm do manifesto Bronze, não da linha. `source_row_number` é a posição lida no XML, inclusive linhas ignoradas, e não ordena o dataset.

## Ordem

O JSONL é ordenado por `site`, `question_id`, `ingestion_identity`. A ordem não é temporal. Não há intervalo de vigência nem `accepted_at`.

Corte `unknown` não autoriza tratar a métrica como série histórica. `operator_declared` registra o corte declarado pelo operador e também não prova o instante da mudança. Este contrato não emite `verified`.

A qualidade está no [runbook](../runbooks/data-dump-silver.md). `DS-014` conserva a pergunta quando `accepted_answer_id` não encontra resposta no mesmo snapshot.
