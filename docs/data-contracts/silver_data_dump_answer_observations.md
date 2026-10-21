# silver_data_dump_answer_observations.v1

Observação de uma resposta num snapshot. O grão é `site + answer_id + ingestion_identity`.

## Campos

| Campo | Tipo | Nulo |
| --- | --- | --- |
| schema_version | `silver_data_dump_answer_observations.v1` | não |
| source_kind | `stackexchange_data_dump` | não |
| site | string | não |
| answer_id | inteiro positivo | não |
| question_id | inteiro positivo | não |
| content_hash | hash do conteúdo observado nesse snapshot | não |
| score | inteiro | sim |
| comment_count | inteiro não negativo | sim |
| last_activity_at | timestamp UTC com `Z` | sim |
| is_accepted_in_snapshot | booleano | sim |
| dump_publication_id | rótulo | não |
| dump_cutoff | timestamp UTC com `Z` | sim |
| dump_cutoff_status | `operator_declared` ou `unknown` | não |
| source_file_observed_at | timestamp UTC com `Z` | não |
| source_identity | SHA-256 hexadecimal | não |
| ingestion_identity | SHA-256 hexadecimal | não |
| source_row_number | inteiro positivo, só linhagem | não |

## Aceite no snapshot

`is_accepted_in_snapshot` é `true` quando `answer_id` é igual ao `accepted_answer_id` da pergunta emitida no mesmo snapshot. É `false` quando essa pergunta está disponível e aponta para outra resposta ou para nenhuma. É `null` quando a pergunta não foi emitida ou o identificador de aceite não é comparável.

O campo não é um instante. O contrato não tem `accepted_at`.

## Ordem

O JSONL é ordenado por `site`, `answer_id`, `ingestion_identity`. `source_row_number` não define essa ordem. Corte e proveniência vêm do manifesto Bronze.

`DS-013` conserva a linha e deixa o aceite nulo. A tabela completa está no [runbook](../runbooks/data-dump-silver.md).
