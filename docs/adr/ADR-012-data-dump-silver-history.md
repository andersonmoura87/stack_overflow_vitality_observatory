# ADR-012: Silver histórica só do Data Dump

- **Status:** accepted
- **Fase:** 5.2 transforma uma geração Bronze consumível em quatro datasets de snapshot. Não reconcilia com a API e não alimenta a Gold.

## Contexto

O Bronze `bronze_stackexchange_data_dump_posts.v1` guarda o estado publicado num `Posts.xml`. Cada geração consumível é um corte, não um log. A Silver de perguntas da API e a Silver de respostas da API usam outros caminhos, outros esquemas e a Gold lê a primeira. Escrever nelas misturaria observação operacional e corte publicado.

O dump não informa o instante em que o conteúdo, a métrica ou o aceite mudaram. Ausência num snapshot posterior não prova exclusão.

## Decisão

A Silver do dump é um dataset próprio, publicado por `transform-data-dump` sob o diretório informado em `--output-dir`. O layout lógico de referência é `data/silver/data_dump/`. Cada execução transforma exatamente uma geração Bronze.

Uma geração `completed` é transformada. Uma geração `empty` publica quatro JSONL vazios consumíveis. Uma geração `partial`, um manifesto ausente, não terminal ou com `consumable=false`, é recusada antes de lock, staging, JSONL ou manifesto Silver. `failed` não tem geração transformável.

A história é uma sequência de observações discretas. Não há `valid_from`, `valid_to`, `is_current`, lápide nem intervalo SCD.

### Datasets

O grão e os campos estão nos quatro contratos. Os nomes acompanham o prefixo `silver_data_dump_`.

- Conteúdo de pergunta: `site + question_id + content_hash`.
- Observação de pergunta: `site + question_id + ingestion_identity`.
- Conteúdo de resposta: `site + answer_id + content_hash`.
- Observação de resposta: `site + answer_id + ingestion_identity`.

`body_html` e `tags_raw` são os valores observados. `body_text`, `code_blocks` e `tags` são derivados. Resposta não tem tags. `tags` sai de tokens `<nome>`, sem duplicata, em ordem lexicográfica. `tags_raw` nulo vira lista vazia.

`content_hash` é o SHA-256 do JSON canônico dos campos de conteúdo, sem `schema_version`, `source_kind` nem o próprio hash. Métrica, aceite, corte e identidades ficam na observação. Por isso uma mudança só de métrica conserva o hash e uma mudança de corpo o altera.

`is_accepted_in_snapshot` é verdadeiro só quando `answer_id` é o `accepted_answer_id` da pergunta no mesmo snapshot. É falso quando a pergunta está disponível e aponta para outra resposta ou para nenhuma. É nulo quando a pergunta não está disponível ou a comparação não é validável. Não existe `accepted_at`.

`source_row_number` é linhagem da leitura do XML. A ordem dos JSONL é a do grão, não a ordem do arquivo nem uma ordem temporal.

### Identidade

`silver_generation_id` é o SHA-256 do JSON canônico de `ingestion_identity`, das quatro versões de contrato, `transformer_version`, `body_rules`, `tag_rules` e `max_body_chars`. Este último muda quais linhas sobrevivem, então entra na identidade.

Não entram `transformation_run_id`, relógio, caminho, commit, diretório de saída, log, timeout nem limite que só aborta. O mesmo Bronze e as mesmas regras produzem os mesmos bytes de JSONL.

### Publicação e qualidade

O publicador usa lock local de falha imediata, staging privado, JSONL canônico, checksum, releitura e manifesto terminal por último. Replay de bytes iguais não reescreve. Bytes divergentes não são substituídos. Falha antes do manifesto não cria geração consumível. Manifesto, linhas, log e stdout não gravam caminho absoluto.

`empty` sai com status `empty`, `readable=true`, `consumable=true` e saída 0. `completed` também sai com 0. Rejeição recuperável de linha publica `partial`, com `readable=true`, `consumable=false` e saída 3. Falha, conflito e lock ocupado saem com 1. Argumento inválido continua saindo com 2.

A qualidade usa só o prefixo `DS-*`. A tabela de severidade, efeito na linha, efeito na execução e impacto em `readable`/`consumable` está no [runbook](../runbooks/data-dump-silver.md).

## Consequências

A Gold, os watermarks, os checkpoints, o Bronze e as Silvers da API permanecem intactos. Reconciliação entre API e dump continua fora desta fase. Corte `unknown` ou apenas `operator_declared` não autoriza métrica causal nem `dump_cutoff_status=verified`.

## Alternativas recusadas

Reusar `silver/questions` ou `silver/answers` alteraria datasets que a Gold e os contratos da API já consomem. Tratar o snapshot como SCD inventaria vigência que a fonte não declara. Escolher uma linha vencedora entre duplicatas divergentes esconderia contradição no mesmo corte.
