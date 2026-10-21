# Contract: bronze_stackexchange_data_dump_posts.v1

- **Finalidade/proprietário:** extrato fiel das linhas selecionadas de um `Posts.xml` local e a proveniência dessa publicação; Data Platform.
- **Origem:** arquivo local não comprimido. Snapshot de um corte, não log de eventos e não resposta da API.
- **Estado:** implementado na Fase 5.1. Não há download, modo completo nem Silver.
- **Grão:** uma linha JSON por post emitido. Chave lógica do post: `site + post_id`. Chave da geração: `ingestion_identity`.
- **source_kind:** `stackexchange_data_dump` na linha e no manifesto. A API permanece `stackexchange_api`. Não há migração do Bronze antigo.

## Identidade

`source_identity` é o SHA-256 de `canonical({"site", "source_sha256", "source_file_size"})`. Não inclui caminho.

`selection_sha256` é o SHA-256 do array JSON dos IDs de pergunta únicos, ordenados antes do hash. A ordem informada não muda o valor. A serialização usa separadores compactos, `ensure_ascii` e sem NaN.

`ingestion_identity` é o SHA-256 de `canonical` deste objeto, com as chaves exatamente nesta grafia:

```text
source_identity
contract_version
parser_version
mode
selection_sha256
max_attribute_bytes
max_body_chars
max_shard_records
max_shard_bytes
dump_publication_id
dump_cutoff
dump_cutoff_status
```

`canonical` é JSON UTF-8, chaves ordenadas, separadores `(",", ":")`, `ensure_ascii` e sem NaN. `dump_cutoff` nulo permanece nulo. `max_attribute_bytes` e `max_body_chars` entram porque mudam quais linhas são aceitas. `max_shard_records` e `max_shard_bytes` entram porque mudam os bytes de cada shard.

Ficam fora da identidade: prazo de lock, frequência e nível de log, teto de RSS, reserva de disco, diretório de entrada, `run_id`, tamanho máximo da fonte, máximo de registros e máximo de bytes de saída. Esses três últimos só abortam a tentativa; não definem outra geração quando o conteúdo publicado é o mesmo.

`run_id` é a tentativa. Replay da mesma `ingestion_identity` com os mesmos shards não cria outra geração e não reescreve o manifesto. Bytes divergentes no mesmo destino são conflito.

## Particionamento

```text
data_dump/site=<site>/g=<ingestion_identity>/shard=NNNN.jsonl
data_dump/manifests/<ingestion_identity>.json
data_dump/site=<site>/.staging/<run_id>/
data_dump/manifests/runs/<run_id>.json
quarantine/data_dump/g=<ingestion_identity>/items.jsonl
```

Staging e manifesto de tentativa não são consumo. O manifesto terminal é o último arquivo da geração. Ele declara `readable` e `consumable`:

| Status | readable | consumable |
|---|---:|---:|
| `completed` | true | true |
| `empty` | true | true |
| `partial` | true | false |
| `failed` | false | false |

`completed` e `empty` autorizam o consumo automático dos shards nomeados. `partial` mantém os shards publicados para inspeção e recuperação, com `readable=true` e `consumable=false`. A Silver automática futura recusa `partial` por padrão. `failed` e `cancelled` não ocupam a chave da geração e não publicam manifesto consumível. `completed_with_warnings` e `truncated` não pertencem ao vocabulário da Fase 5.1.

`ingest-data-dump` usa estes códigos de saída. O código 2 permanece reservado ao argparse.

| Código | Situação |
|---|---|
| 0 | `completed`, `empty`, `dry_run` e replay de `completed` ou `empty` |
| 1 | `failed`, conflito, lock e fonte recusada |
| 3 | `partial` e replay de `partial` |

Não há caminho absoluto no manifesto nem no log. O nome persistido é o nome-base sanitizado. Caminho relativo é opcional, só sob a raiz de entrada declarada e sem `..`.

## Shard

UTF-8 sem BOM. Cada linha é um objeto JSON canônico terminado por `\n`. Os registros emitidos entram nos shards na ordem em que foram lidos no `Posts.xml`. O dump não é considerado ordenado por ID. Ordenar por identificador ou por tempo é trabalho da Silver histórica. No máximo 5.000 registros e 64 MiB por shard. O último shard pode ser menor e não é vazio. Seleção vazia não cria shard.

Cada shard tem SHA-256, quantidade de registros e bytes. O primeiro e o último `post_id` do shard são os extremos da sequência emitida, não o menor e o maior identificador. A confirmação relê o arquivo antes da promoção. O consumidor não infere o conjunto de shards pelo diretório. A mesma fonte, a mesma configuração semântica e a mesma versão do parser produzem os mesmos shards e checksums.

## Linha

Sempre presentes: `schema_version`, `source`, `source_kind`, `site`, `ingestion_identity`, `post_id`, `post_type_id`, `source_row_number`, `source_file_name`, `source_sha256`, `source_file_size`, `dump_publication_id`, `parser_version`, `contract_version`.

Nulos quando o atributo falta: `parent_id`, `accepted_answer_id`, `source_created_at`, `score`, `body`, `title`, `tags_raw`, `owner_user_id`, `content_license`, `last_edit_at`, `last_activity_at`, `closed_at`, `view_count`, `answer_count`, `comment_count`.

Não entram na linha: `published_at`, `run_started_at`, `source_file_observed_at`, `dump_cutoff`, `transformation_run_id`, caminho, nome de exibição, corpo de comentário, HTML limpo e tags derivadas. `accepted_answer_id` não é data de aceite. `comment_count` não cria a entidade comentário. Inteiro rejeita booleano. Ausência não vira zero nem falso.

`source_row_number` começa em 1 e aumenta um a cada `<row>` lido, antes do filtro. A linha emitida conserva esse número. A ordem do shard é a ordem de emissão, que é a ordem de leitura dos registros aceitos.

## Manifesto da geração

`schema_version=bronze_stackexchange_data_dump_posts_manifest.v1`.

Campos de identidade e tempo: `source_identity`, `ingestion_identity`, `run_id` da tentativa que publicou, `site`, `dump_publication_id`, `dump_cutoff`, `dump_cutoff_status`, `source_file_name`, `source_sha256` antes e depois, tamanhos antes e depois, `source_file_observed_at`, `run_started_at`, `published_at`, `transformation_run_id=null`, `parser_version`, `contract_version`, `project_revision` nulo ou informado.

`dump_cutoff_status` é `operator_declared` ou `unknown` na Fase 5.1. `verified` não é emitido sem metadado local conferido. A Fase 5.1 não faz essa conferência externa.

Execução: `status`, `mode=restricted`, `selection_sha256`, contagens, hashes dos shards, limites efetivos, `api_comparison=skipped` e parâmetros sem segredo e sem caminho absoluto.

`published_at` é somente a publicação deste manifesto. Não é criação, corte, inspeção nem início.

## Qualidade

Post fora da seleção, inclusive resposta cujo `ParentId` não foi solicitado, incrementa `posts_ignored_filter` e não gera erro. Tipo diferente de 1 e 2 incrementa `posts_skipped_type` e não gera erro.

| Código | Condição | Severidade | Disposição | Status | Contador | Log permitido | Recuperação |
|---|---|---|---|---|---|---|---|
| DD-001 | XML malformado | erro | aborta | `failed` | `xml_failures` | posição e tipo do erro | corrigir a fonte e tentar de novo |
| DD-002 | elemento que não é `posts` nem `row` | erro | aborta | `failed` | `unexpected_elements` | nome do elemento | corrigir a fonte |
| DD-003 | `Id` ausente ou inválido; identificador opcional presente e inválido (`ParentId`, `AcceptedAnswerId`, `OwnerUserId`) | erro | quarentena | `partial` | `rejected_records` | índice e código | a linha não entra |
| DD-004 | `PostTypeId` presente e inválido | erro | quarentena | `partial` | `rejected_records` | índice e código | a linha não entra |
| DD-005 | tipo fora de 1 e 2 | informativo | ignora | inalterado | `posts_skipped_type` | tipo numérico | nenhuma |
| DD-006 | resposta sem `ParentId` | erro | quarentena | `partial` | `rejected_records` | índice | a linha não entra |
| DD-007 | post fora da seleção | informativo | ignora | inalterado | `posts_ignored_filter` | contagem | nenhuma; não é falha |
| DD-008 | `ParentId` solicitado e a pergunta não está no arquivo | warning | grava a resposta | `partial` | `missing_parents` | `parent_id` | não inventar a pergunta |
| DD-009 | data fora do formato | erro | quarentena | `partial` | `rejected_records` | nome do campo | a linha não entra |
| DD-010 | criação anterior a 2008-01-01, posterior ao corte conhecido ou posterior à inspeção | erro | quarentena | `partial` | `rejected_records` | nome do campo | a linha não entra |
| DD-011 | corpo ausente ou vazio em pergunta ou resposta | erro | quarentena | `partial` | `rejected_records` | índice | a linha não entra |
| DD-012 | corpo acima do limite | erro | quarentena | `partial` | `rejected_records` | tamanho, não o texto | a linha não entra |
| DD-013 | `Score`, `ViewCount`, `AnswerCount` ou `CommentCount` presente com tipo inválido | erro | quarentena | `partial` | `rejected_records` | nome do campo | nulo permanece nulo |
| DD-014 | proprietário ausente | warning | grava nulo | `completed` se não houver erro | `owner_absent` | código | não preencher o dono |
| DD-015 | licença ausente | warning | grava nulo | `completed` se não houver erro | `license_absent` | código | não preencher a licença |
| DD-016 | duplicata idêntica | informativo | não grava de novo | inalterado | `duplicates_identical` | `post_id` | nenhuma |
| DD-017 | duplicata divergente, ou o mesmo ID como pergunta e resposta | erro | aborta | `failed` | `duplicates_divergent` | `post_id` e código | não escolher uma cópia |
| DD-018 | conflito com a identidade observada na API | erro | só na reconciliação | não emitido na ingestão | `api_conflicts` | campos em conflito, sem corpo | as duas fontes permanecem |
| DD-019 | fonte alterada durante a execução | erro | aborta | `failed` | `source_unstable` | apenas a divergência | repetir com arquivo estável |
| DD-020 | limite de registros | erro | aborta | `failed` | `limit_records` | contagens | aumentar o limite de propósito |
| DD-021 | limite de bytes da fonte, do buffer, do atributo ou da saída | erro | aborta | `failed` | `limit_bytes` | contagens | reduzir a seleção ou ampliar o limite |
| DD-022 | espaço livre insuficiente ou indeterminado | erro | aborta | `failed` | `limit_disk` | bytes livres se conhecidos | liberar espaço |
| DD-023 | DTD ou entidade proibida | erro | aborta | `failed` | `xml_forbidden` | código | não processar o arquivo |

`completed` não tem erro nem DD-008. Warning de dono ou licença pode coexistir com `completed` e mantém `consumable=true`. `partial` tem quarentena ou DD-008, com a fonte ainda íntegra, `readable=true` e `consumable=false`. `empty` não emite post, não tem erro, publica manifesto terminal sem shards e é sucesso. `failed` não promove a geração. Limite excedido permanece `failed`, sem amostra publicada.

DD-018 não é produzido pela varredura do Bronze da API na Fase 5.1. O manifesto traz `api_comparison=skipped`. A regra fica definida para a reconciliação.

## Limites padrão

| Limite | Valor | Classe |
|---|---|---|
| tamanho da fonte | 8 GiB | pré-check; arquivo maior exige override explícito |
| registros emitidos | 100.000 | hard limit; aborta a tentativa e não entra na identidade |
| bytes de um atributo | 1 MiB | hard limit |
| caracteres do corpo | 500.000 | hard limit; o mesmo teto já recusado na resposta da API |
| bytes de saída | 2 GiB | hard limit |
| registros por shard | 5.000 | hard limit de empacotamento |
| bytes por shard | 64 MiB | hard limit de empacotamento |
| lock | sem prazo | a primitiva atual falha na hora; não há timeout |
| progresso | 100.000 linhas lidas | apenas observável |
| espaço livre | o maior entre 1 GiB e o dobro da saída prevista | pré-check; desconhecido falha fechado; não entra na identidade |
| memória esperada | linha corrente, shard aberto até 64 MiB e mapa dos IDs emitidos | consequência do streaming |
| RSS | opcional, só como proteção | não entra na identidade e não cria geração |

O streaming não materializa a seleção para reordená-la. Um dump completo do Stack Overflow tende a passar de 8 GiB. O padrão não o engole em silêncio. Esses números não vêm do benchmark da Silver.

## Imutabilidade

A geração é escrita uma vez. Replay não muda `published_at`. Falha de tentativa não substitui geração. Não há checkpoint de offset. Watermark de perguntas e checkpoint de respostas não são alterados.

## Limitações

O shard não é o XML original. O checksum da fonte cobre o arquivo inteiro. Hard link é requisito. Edição externa posterior não é impedida. `operator_declared` não é verificação contra a Stack Exchange. `unknown` não autoriza métrica histórica. A Fase 5.1 não emite `verified`.
