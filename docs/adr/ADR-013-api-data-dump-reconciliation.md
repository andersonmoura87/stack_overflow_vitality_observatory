# ADR-013: Reconciliação entre a Silver da API e a Silver do Data Dump

- **Status:** accepted
- **Fase:** 5.3.0 fecha o desenho. O reconciliador não existe. Nenhum comando lê ou grava `data/reconciled/stackexchange/`. A Gold v1 não consome essa camada.

## Contexto

A Silver de perguntas da API (`silver_questions.v1`) materializa intervalos de observação. A Gold escolhe a métrica com `collected_at <= as_of` e o conteúdo pelo intervalo que contém esse corte. A Silver de respostas da API separa versão de conteúdo e observação de aceite. A Silver do Data Dump guarda um snapshot por `ingestion_identity`, sem `valid_from`, `valid_to` ou `is_current`.

Os hashes de conteúdo não são intercambiáveis. O hash da API inclui `schema_version`, `link` e `closed_reason` e deixa o texto derivado fora. O hash do dump inclui `body_text`, `code_blocks` e `tags_raw`. `DD-018` compara chaves no formato do Bronze e a ingestão não o executa. Não há desacordo material entre o código e o contrato de cada família. ADR-011 impede uma fonte vencedora global e não define precedência por campo e por tempo. Esta decisão fecha essa precedência para uma implementação futura.

`transformed_at` não existe no repositório. `collected_at` da API é o `recovered_at` da página. `started_at` e `finished_at` são tempos do manifesto de transformação. No dump, `source_file_observed_at` é a inspeção do arquivo e `published_at` é a escrita do manifesto.

## Decisão

A camada futura vive em `data/reconciled/stackexchange/`. Ela referencia as duas Silvers e não as reescreve. Não altera Bronze, watermarks, checkpoints nem a Gold.

### Identidade

A identidade canônica do post é `site + post_type + post_id`. `post_type` é `question` ou `answer`. A pergunta também é `site + question_id`. A resposta também é `site + answer_id`. O relacionamento da resposta é `site + answer_id + question_id`.

Não entram nessa identidade `source_identity`, `ingestion_identity`, proprietário, conteúdo, hash nem ordem física. `content_hash` da API e `content_hash` do dump nunca são comparados entre fontes.

Tipo, relacionamento ou criação divergentes são conflitos bloqueadores. Campos obrigatórios de identidade não usam a regra de valor conhecido contra nulo.

### Nulos e estados

Ausente, nulo, zero, falso, string vazia e lista vazia são distintos. Nulo não substitui valor conhecido. Valor conhecido contra nulo, fora dos campos bloqueadores, é `matched_compatible`, não `matched_equal`. Os dois valores permanecem. Ausência unilateral não é exclusão.

Os estados de campo e de registro são `matched_equal`, `matched_compatible`, `source_only_api`, `source_only_dump`, `conflict`, `temporally_unordered` e `not_comparable`.

### Tempo

`creation_at` e `source_created_at` são o tempo de evento informado pela fonte. `collected_at` e `source_file_observed_at` são tempos de observação. `published_at`, `started_at` e `finished_at` são tempos técnicos. `valid_from` e `valid_to` da API são intervalos observados, não instantes reais de edição. `last_activity_at` não é edição nem aceite. `last_edit_at` não vira `valid_from`. Não se cria `accepted_at`.

`dump_cutoff_status=unknown` não ordena estados. `operator_declared` permite apenas comparação provisória sinalizada. O código atual não produz corte verificado, e esta fase não aceita `verified`. Chegada posterior de um artefato não significa estado de negócio posterior.

### Precedência

Não existe fonte vencedora global.

| Situação | Resultado |
| --- | --- |
| Identidade, tipo, parent ou criação divergentes | `conflict` |
| Conteúdo | comparar campos normalizados, nunca hashes |
| Valor conhecido contra nulo, fora dos bloqueadores | `resolved_value` do conhecido, com `resolution_basis=known_over_unknown` |
| Conteúdo ou métrica divergentes sem ordem confiável | `temporally_unordered` |
| Métrica | preservar valor, fonte e tempo; não escolher o maior; não inserir métrica do dump na série da API |
| Campo exclusivo de uma fonte | `not_comparable` |
| Registro só em uma fonte | `source_only_api` ou `source_only_dump` |

Não se infere exclusão, reabertura ou remoção.

### Contratos e gates

Os resultados são `reconciled_questions.v1` e `reconciled_answers.v1`. Conflitos são `reconciliation_conflicts.v1`. A execução é `reconciliation_runs.v1`. A taxonomia nova é `RC-*` e não reutiliza `SQ-*`, `SA-*`, `DD-*` nem `DS-*`.

Os gates `readable`, `reconciliation_complete`, `analytically_usable` e `gold_eligible` são independentes. Com os cortes que o repositório emite hoje, `unknown` e `operator_declared`, o gate `gold_eligible` da execução fica falso. A Gold atual continua lendo somente a Silver de perguntas da API.

`reconciliation_run_id` é o SHA-256 do JSON canônico da política, do normalizador, das versões de contrato, das gerações de entrada, de `api_as_of`, de `dump_cutoff` e de `dump_cutoff_status`. Não entram relógio, caminho, commit, diretório nem identificador da tentativa.

## Consequências

A Fase 5.3.1 pode implementar o leitor e o publicador sem escolher uma fonte global e sem tratar o snapshot como log. Enquanto não houver corte verificado, a camada não autoriza tempo até resposta, instante de aceite, série histórica única nem conclusão sobre efeito de IA.

## Limites científicos

O snapshot não é log de eventos. Ausência não é exclusão. Não há instante de primeira resposta nem de aceite. Remoção não é inferida. Divergência não prova evolução quando a ordem temporal não é confiável. Efeito da IA permanece hipótese, não conclusão causal.

## Alternativas recusadas

Escrever o resultado nas Silvers atuais mudaria a história que a Gold já lê. Comparar hashes cruzados classificaria diferenças de contrato como diferença de conteúdo. Escolher o maior score, a chegada mais recente ou o corte declarado como se fosse verificado inventaria uma ordem que as fontes não dão.
