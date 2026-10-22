# Conflitos e execuções da reconciliação futura

A Fase 5.3.0 especifica estes contratos. Não há gravador, leitor nem comando. Códigos `RC-*` não substituem `SQ-*`, `SA-*`, `DD-*` ou `DS-*`.

## reconciliation_conflicts.v1

Grão: `reconciliation_run_id + site + post_type + post_id + field_name`.

| Campo | Semântica |
| --- | --- |
| `schema_version` | `reconciliation_conflicts.v1` |
| `reconciliation_run_id` | execução lógica |
| `site`, `post_type`, `post_id` | identidade canônica |
| `field_name` | campo comparado; `*` quando o conflito é do registro inteiro |
| `code` | um código `RC-*` |
| `severity` | `error`, `warning` ou `informational` |
| `state` | estado que o código produz |
| `effect` | o que acontece com o campo e com a linha |
| `resolution` | o que não é feito e qual base fica registrada |
| `api_presence`, `dump_presence` | `absent`, `null` ou `present` |
| `value_digest` | SHA-256 do valor normalizado quando o campo é texto longo; escalares pequenos podem repetir o valor |
| `temporal_confidence` | `not_applicable`, `unordered` ou `provisional` |
| `readable`, `reconciliation_complete`, `analytically_usable`, `gold_eligible` | impacto no registro depois deste código |

O log não grava corpo HTML, caminho absoluto nem escolhe um lado. O valor completo permanece na linha reconciliada. Dois códigos no mesmo grão são duas linhas, ordenadas por `code`.

## reconciliation_runs.v1

Grão: `reconciliation_run_id`.

O identificador é o SHA-256 do JSON canônico de:

- `policy_version` `reconciliation_policy.v1`;
- `normalizer_version` `reconciliation_normalize.v1`;
- versões `reconciled_questions.v1`, `reconciled_answers.v1`, `reconciliation_conflicts.v1` e `reconciliation_runs.v1`;
- geração Silver de perguntas da API, geração Silver de respostas da API e geração Silver do dump, cada uma pelo seu identificador e pelo checksum do manifesto;
- `api_as_of`, `dump_cutoff` e `dump_cutoff_status`.

Ficam de fora o identificador da tentativa, o relógio, o caminho, o commit, o diretório e os tempos técnicos.

O manifesto registra política, contratos, gerações de entrada, corte solicitado, checksums das saídas, contagens de cobertura por estado, contagens por código `RC-*` e os quatro gates da execução. `started_at`, `finished_at` e `published_at` são tempos técnicos desse manifesto. Não há caminho absoluto.

Uma geração de entrada `partial`, não consumível, ausente ou de esquema incompatível não produz execução consumível. `dump_cutoff_status=verified` também não, porque o código atual não o emite.

Enquanto o corte for `unknown` ou `operator_declared`, o gate `gold_eligible` da execução é falso.

## Taxonomia RC-*

| Código | Severidade | Efeito | Resolução | Gates |
| --- | --- | --- | --- | --- |
| RC-001 identidade | error | `conflict` no registro; as linhas não são fundidas | não completar `site`, `post_type` ou `post_id` com nulo nem com a outra fonte | `gold_eligible` falso; `analytically_usable` falso; os outros dois gates seguem a execução se o conflito ficou explícito |
| RC-002 tipo | error | `conflict`; `question` e `answer` não viram o mesmo post | não coagular `post_type` | igual a RC-001 |
| RC-003 relacionamento | error | `conflict` em `question_id` da resposta | não inventar o pai e não usar valor conhecido contra nulo | igual a RC-001 |
| RC-004 criação | error | `conflict` entre `creation_at` e `source_created_at` | não escolher um instante e não preencher nulo | igual a RC-001 |
| RC-005 conteúdo sem ordem temporal | warning | `temporally_unordered` em título, corpo, tags ou fechamento comparado | preservar os dois valores; não comparar hashes; sem vencedor | `gold_eligible` falso; `analytically_usable` falso para afirmação de evolução |
| RC-006 métrica sem ordem temporal | warning | `temporally_unordered` em score, visualizações ou contagem de respostas | preservar valor, fonte e tempo; não escolher o maior; não inserir o dump na série da API | `gold_eligible` falso |
| RC-007 aceite | warning | `temporally_unordered` quando os estados conhecidos diferem; `matched_compatible` quando um lado é nulo | não criar `accepted_at`; não eleger um booleano | divergência sem ordem deixa `gold_eligible` falso |
| RC-008 proprietário | warning | `temporally_unordered` entre inteiros diferentes; `matched_compatible` entre conhecido e nulo | `known_over_unknown` só no segundo caso | divergência sem ordem deixa `gold_eligible` falso; compatibilidade por nulo, sozinha, não |
| RC-009 licença | warning | a mesma distinção de RC-008 para `content_license` | igualdade é NFC, extremidades removidas e caixa preservada | igual a RC-008 |
| RC-010 ausência unilateral | informational | `source_only_api` ou `source_only_dump` | não é exclusão, lápide, reabertura ou remoção; o lado presente não vira `resolved_value` | `analytically_usable` verdadeiro só como cobertura; `gold_eligible` falso |
| RC-011 corte desconhecido | error | `temporal_confidence=unordered` | não ordenar conteúdo, métrica ou aceite | `gold_eligible` falso; `analytically_usable` falso para afirmação temporal |
| RC-012 corte declarado pelo operador | warning | `temporal_confidence=provisional` | comparação sinalizada; o corte não é verificado | `gold_eligible` falso; uso analítico só com a sinalização |
| RC-013 proveniência | error | campo de linhagem obrigatório sem referência utilizável | não preencher geração, observação ou tempo de observação | `gold_eligible` falso |
| RC-014 contrato | error | a execução para antes de uma geração consumível | não coagular esquema, status `verified` nem manifesto não consumível | os quatro gates ficam falsos |
| RC-015 duplicata conflitante | error | `conflict`; nenhuma cópia é eleita | a chave canônica com dois candidatos incompatíveis na mesma entrada não produz `resolved_value` | `gold_eligible` falso; `analytically_usable` falso |
| RC-016 replay divergente | error | a geração já publicada permanece | não substituir bytes; a tentativa nova não vira outra execução lógica | a tentativa divergente não é elegível; a execução anterior não muda |

Um warning não apaga um error. `reconciliation_complete` exige que o código aplicável esteja no log. `readable` exige manifesto terminal e checksums conferidos. RC-014 e RC-016 não deixam uma execução nova legível.

## Ordem e imutabilidade

Conflitos são ordenados por `site`, `post_type`, `post_id`, `field_name`, `code`. O manifesto da execução é o último artefato da publicação futura. Replay dos mesmos bytes não reescreve `published_at`. Bytes diferentes para o mesmo `reconciliation_run_id` são RC-016.
