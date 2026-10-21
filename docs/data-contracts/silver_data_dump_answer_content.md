# silver_data_dump_answer_content.v1

Dataset de conteúdo observado de uma resposta. O grão é `site + answer_id + content_hash`. Resposta não tem título, tags nem `closed_at`.

## Campos

| Campo | Tipo | Nulo |
| --- | --- | --- |
| schema_version | `silver_data_dump_answer_content.v1` | não |
| source_kind | `stackexchange_data_dump` | não |
| site | string | não |
| answer_id | inteiro positivo | não |
| question_id | inteiro positivo, o `parent_id` observado | não |
| content_hash | SHA-256 hexadecimal | não |
| body_html | string, corpo observado | sim |
| body_text | string derivada | sim |
| code_blocks | lista de strings | sim quando `body_html` é nulo |
| owner_user_id | inteiro positivo | sim |
| content_license | string | sim |
| source_created_at | timestamp UTC com `Z` | sim |
| last_edit_at | timestamp UTC com `Z` | sim |

A limpeza do corpo é a mesma `content_parser.v1` das perguntas. `content_hash` é o SHA-256 do JSON canônico de `site`, `answer_id`, `question_id`, `body_html`, `body_text`, `code_blocks`, `owner_user_id`, `content_license`, `source_created_at` e `last_edit_at`.

## Ordem

O JSONL é ordenado por `site`, `answer_id`, `content_hash`. Não há tags, `accepted_at` nem caminho absoluto.

Uma resposta cujo `parent_id` está ausente na linha é rejeitada por `DS-008` e não entra neste dataset. A resposta cujo pai não está entre as perguntas emitidas permanece, e a observação marca o aceite como nulo. Ver o [runbook](../runbooks/data-dump-silver.md).
