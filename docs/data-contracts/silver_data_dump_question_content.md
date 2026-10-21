# silver_data_dump_question_content.v1

Dataset de conteúdo observado numa geração Data Dump. O grão é `site + question_id + content_hash`. Um snapshot produz uma versão por pergunta. Outra geração pode repetir o mesmo hash quando o conteúdo observado não muda.

## Campos

| Campo | Tipo | Nulo |
| --- | --- | --- |
| schema_version | string constante `silver_data_dump_question_content.v1` | não |
| source_kind | string constante `stackexchange_data_dump` | não |
| site | string | não |
| question_id | inteiro positivo | não |
| content_hash | SHA-256 hexadecimal | não |
| title | string | sim |
| body_html | string, corpo observado | sim |
| body_text | string derivada | sim |
| code_blocks | lista de strings, na ordem do parser | sim quando `body_html` é nulo |
| tags | lista de strings | não; vazia se `tags_raw` for nulo |
| tags_raw | string observada, formato `<python><pandas>` | sim |
| owner_user_id | inteiro positivo | sim |
| content_license | string | sim |
| source_created_at | timestamp UTC com `Z` | sim |
| last_edit_at | timestamp UTC com `Z` | sim |
| closed_at | timestamp UTC com `Z` | sim |

`body_html` e `tags_raw` não são reescritos. `body_text` e `code_blocks` vêm de `content_parser.v1`: texto com espaços colapsados, blocos `code` na ordem do documento, `script` e `style` omitidos. `tags` contém os tokens internos de `<nome>`, sem duplicata, em ordem lexicográfica e com a caixa preservada.

`content_hash` é o SHA-256 do JSON canônico, chaves ordenadas, destes campos e de nenhum outro: `site`, `question_id`, `title`, `body_html`, `body_text`, `code_blocks`, `tags`, `tags_raw`, `owner_user_id`, `content_license`, `source_created_at`, `last_edit_at`, `closed_at`.

## Ordem e limites

O JSONL é ordenado por `site`, `question_id`, `content_hash`. Cada linha é um objeto canônico terminado em `\n`. Não há `valid_from`, `valid_to`, `is_current`, `accepted_at` nem caminho absoluto.

A qualidade `DS-*`, o manifesto e os efeitos em `readable` e `consumable` estão no [runbook](../runbooks/data-dump-silver.md).
