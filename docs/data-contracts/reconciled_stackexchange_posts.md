# Resultados reconciliados futuros

A Fase 5.3.0 define estes contratos e não os publica. O reconciliador não existe. A Gold não lê `data/reconciled/stackexchange/`.

Dois datasets compartilham as regras abaixo.

- `reconciled_questions.v1`, grão `reconciliation_run_id + site + question_id`.
- `reconciled_answers.v1`, grão `reconciliation_run_id + site + answer_id`.

Cada linha preserva a identidade canônica, os valores normalizados dos dois lados, a referência às observações, o resultado por campo, `resolved_value` quando a regra o permite, `resolution_basis`, a confiança temporal, os conflitos, a linhagem e a elegibilidade analítica.

## Identidade

`post_type` é `question` ou `answer`. `post_id` é `question_id` na pergunta e `answer_id` na resposta. O relacionamento da resposta é `question_id` no mesmo `site`.

A identidade canônica não inclui `source_identity`, `ingestion_identity`, proprietário, conteúdo, hash nem `source_row_number`. Os hashes de conteúdo das duas Silvers são linhagem opaca. Não são comparados e não decidem estado.

## Presença de valor

Ausente, nulo, zero, falso, string vazia e lista vazia não se substituem. Todo valor de campo usa presença explícita:

| presence | value |
| --- | --- |
| `absent` | a chave não leva valor; o registro ou o campo não foi observado nessa fonte |
| `null` | a fonte observou o campo e ele é nulo |
| `present` | a fonte observou o valor, inclusive `0`, `false`, `""` e `[]` |

## Normalização `reconciliation_normalize.v1`

A normalização serve à comparação. Os valores de origem continuam nos dois lados.

| Campo | Comparação |
| --- | --- |
| instantes | instantes UTC, não a forma textual |
| inteiros | valor numérico; booleano não é inteiro |
| `title` | NFC, entidades decodificadas e espaços colapsados, a mesma regra de `clean_title`; a API contribui `title_raw` e o dump contribui `title` |
| `body_html` | NFC e corte das extremidades; o HTML interno não é reescrito; `body_text` e `code_blocks` não decidem igualdade |
| `tags` | conjunto com a caixa preservada, depois da derivação de cada fonte; `[]` não é nulo |
| `content_license` | NFC, extremidades removidas, caixa preservada |
| `owner_user_id` | inteiro positivo ou nulo |
| aceite da resposta | `is_accepted` da API contra `is_accepted_in_snapshot` do dump, sem criar `accepted_at` |

## Campos comparados

Pergunta: `post_type`, `post_id`, `question_id`, criação (`creation_at` contra `source_created_at`), `title`, `body_html`, `tags`, `owner_user_id`, `content_license`, `score`, `view_count`, `answer_count`, `accepted_answer_id`, `closed_at`, `last_activity_at`, `last_edit_at`.

Resposta: `post_type`, `post_id`, `answer_id`, `question_id`, criação, `body_html`, `owner_user_id`, `content_license`, `score`, `last_activity_at`, `last_edit_at` e o estado de aceite.

`closed_reason`, `link`, `is_answered`, `title_clean`, `comment_count`, `tags_raw`, `body_text` e `code_blocks` são `not_comparable` quando aparecem em um só lado. `comment_count` não cria entidade de comentário. `answer_count` não é recalculado a partir das respostas lidas.

## Resultado por campo

| Campo do resultado | Semântica |
| --- | --- |
| `field_name` | nome canônico do campo comparado |
| `state` | um dos sete estados |
| `api` e `dump` | presença e valor normalizado |
| `resolved_value` | presente só quando a base abaixo o autoriza |
| `resolution_basis` | `exact_match`, `known_over_unknown` ou `not_resolved` |
| `temporal_confidence` | `not_applicable`, `unordered` ou `provisional` |
| `codes` | zero ou mais códigos `RC-*` |

`exact_match` acompanha `matched_equal`. `known_over_unknown` acompanha `matched_compatible` e copia o lado `present` para `resolved_value`. Identidade, tipo, parent e criação não usam essa base: divergência ou nulo contra conhecido nesses campos é `conflict`.

`not_resolved` cobre `conflict`, `temporally_unordered`, `not_comparable` e ausência unilateral. Ausência de registro não promove o lado presente a `resolved_value`.

Métricas guardam valor, fonte e tempo de observação. Valores diferentes não escolhem o maior e não entram na série da API. Sem ordem confiável, o estado é `temporally_unordered`.

## Confiança temporal

| Confiança | Quando |
| --- | --- |
| `not_applicable` | igualdade que não depende de ordenar as fontes, ou campo que não é temporal |
| `unordered` | `dump_cutoff_status=unknown`, ou diferença que exigiria ordem |
| `provisional` | `dump_cutoff_status=operator_declared` |

Não existe confiança `verified`. Um status fora de `unknown` e `operator_declared` é contrato incompatível. Chegada posterior de arquivo, manifesto ou transformação não muda essa confiança.

`valid_from` e `valid_to` explicam qual versão da API foi referenciada. Não são o instante da edição. `last_edit_at` não abre intervalo. `last_activity_at` não data edição nem aceite.

## Referências e linhagem

Cada lado informa, sem caminho absoluto:

- esquema e identificador da geração Silver;
- chave da observação (`observation_id` na API ou `ingestion_identity` mais `source_row_number` no dump);
- `collected_at` ou `source_file_observed_at`;
- hash de conteúdo local, marcado como não comparável entre fontes.

A seleção da API, para o `api_as_of` pedido, usa a última observação com `collected_at <= api_as_of` e a versão de conteúdo cujo intervalo observado contém esse corte. Isso copia a regra de leitura da Gold e não escreve na Gold. O dump contribui a única observação da geração Silver nomeada.

## Elegibilidade

A linha repete os quatro gates. Eles não se implicam.

| Gate | Verdadeiro quando |
| --- | --- |
| `readable` | a linha pertence a uma execução cujo manifesto terminal confirma os bytes |
| `reconciliation_complete` | todo campo exigido tem estado explícito e todo conflito exigido está no log |
| `analytically_usable` | a linha sustenta coexistência descritiva, com `RC-010` quando a cobertura é unilateral, sem afirmar ordem, exclusão ou causa |
| `gold_eligible` | nenhum bloqueio de identidade, tipo, parent, criação, proveniência, contrato, campo obrigatório ou tempo |

Com corte `unknown` ou `operator_declared`, o tempo é insuficiente e `gold_eligible` fica falso. A Gold atual não consulta o gate.

## Layout lógico

```text
data/reconciled/stackexchange/runs/<reconciliation_run_id>/questions.jsonl
data/reconciled/stackexchange/runs/<reconciliation_run_id>/answers.jsonl
data/reconciled/stackexchange/manifests/<reconciliation_run_id>.json
```

JSONL canônico, uma linha por registro do grão, sem caminho absoluto. A publicação futura é imutável: replay idêntico não reescreve; bytes divergentes não são substituídos.
