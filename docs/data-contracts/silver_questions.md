# Contract: silver_questions.v1

**Finalidade:** perguntas normalizadas, histórico de conteúdo e observações de métricas auditáveis. **Owner:** Analytics Engineering. **Fonte:** páginas locais Bronze v1/v2 verificadas. **Consumidores futuros:** métricas Gold e análise de conteúdo; nenhum consumidor analítico é implementado aqui.

## Identidade e temporalidade

A chave natural é `question_id`, qualificada por `site`. Cada linha materializada representa um intervalo de conteúdo observado. Chave de versão: `(site, question_id, valid_from, record_hash)`. `schema_version` é exatamente `silver_questions.v1`; transformação `1.0.0`. Datas são strings ISO 8601 UTC terminadas em `Z`; no modelo Python são datetime com timezone.

Intervalos são `[valid_from, valid_to)`, contíguos, sem sobreposição e com exatamente uma versão atual por entidade. `valid_to=null` equivale a `is_current=true`. Validade começa na observação Bronze (`recovered_at`), não na edição presumida na plataforma. Snapshots fora de ordem reconstroem deterministicamente todo o histórico. Em empate temporal vence o maior `(record_hash, observation_id)` lexicográfico, com warning se o conteúdo divergir; todas as observações permanecem disponíveis.

## Campos de questions.jsonl

Todos os campos devem estar presentes, inclusive os anuláveis. Campos extras e tipos incompatíveis são rejeitados. `null` nunca é convertido em zero ou string vazia. Booleanos não são inteiros.

| Campo | Tipo JSON | Null | Semântica |
|---|---|---|---|
| question_id | integer | não | ID positivo |
| site | string | não | namespace da origem |
| title_raw, title_clean | string | sim | título original e derivado com entidades decodificadas |
| body_html, body_text | string | sim | HTML original e narrativa sem código/script/style |
| code_blocks | array[string] | sim | código em ordem, com quebras preservadas |
| tags | array[string] | sim | valores não vazios, únicos e ordenados, sem alteração de caixa |
| creation_at | timestamp | não | creation_date da origem |
| last_activity_at, last_edit_at | timestamp | sim | datas da origem |
| score | integer | sim | valor assinado; pontuação negativa é legítima |
| view_count, answer_count | integer | sim | contadores não negativos |
| is_answered | boolean | sim | indicador da origem, sem inferência |
| accepted_answer_id | integer | sim | ID positivo quando presente |
| owner_user_id | integer | sim | ID mínimo do autor, sem perfil |
| content_license, link | string | sim | metadados da origem |
| closed_at | timestamp | sim | closed_date da origem |
| closed_reason | string | sim | motivo fornecido |
| collected_at | timestamp | não | instante da última observação representada no intervalo |
| source_run_id | string UUID | não | execução Bronze |
| source_page | integer | não | página positiva |
| source_checksum | string SHA-256 | não | checksum canônico do payload Bronze |
| source_file_checksum | string SHA-256 | não | checksum dos bytes do envelope Bronze |
| source_path | string | não | caminho relativo à raiz Bronze |
| source_item_index | integer | não | posição no array items, começando em zero |
| record_hash | string SHA-256 | não | identidade do conteúdo |
| schema_version | string | não | silver_questions.v1 |
| valid_from | timestamp | não | início do intervalo observado |
| valid_to | timestamp | sim | fim exclusivo |
| is_current | boolean | não | versão mais recente observada |

HTML ausente produz derivados null; HTML vazio produz texto vazio e lista de código vazia. O parser da biblioteca padrão tolera HTML incompleto; não é sanitizador para renderização no navegador.

## Hash, catálogo e observações

O SHA-256 usa JSON canônico UTF-8 com chaves ordenadas e separadores compactos. Inclui somente: `schema_version`, `site`, `question_id`, `title_raw`, `body_html`, `tags`, `creation_at`, `last_edit_at`, `owner_user_id`, `content_license`, `link`, `closed_at`, `closed_reason`. Derivados, métricas e proveniência não participam.

A geração contém três arquivos JSONL contratados em conjunto:

- `catalog.jsonl`: campos do hash mais `title_clean`, `body_text`, `code_blocks` e `record_hash`; uma linha por hash. Tipos e nulabilidade iguais aos acima.
- `observations.jsonl`: `observation_id`, `site`, `question_id`, `record_hash`, `score`, `view_count`, `answer_count`, `is_answered`, `accepted_answer_id`, `last_activity_at`, `collected_at` e todos os campos `source_*`. Uma linha por observação Bronze aceita. `observation_id` é SHA-256 canônico de site, source_run_id, source_page, source_file_checksum e source_item_index. Referência ao catálogo obrigatória; schema herdado do catálogo/manifesto.
- `questions.jsonl`: todos os campos da tabela; materialização dos intervalos, usando métricas e linhagem da última observação de cada intervalo. Para séries de métricas, consultar observations, não apenas questions.

Replay da mesma página não duplica observações nem reescreve geração idêntica. Nova observação de métricas cria somente uma observação e atualiza a materialização, sem criar versão completa adicional de conteúdo. Retorno A→B→A cria três intervalos e dois conteúdos no catálogo. Veja [ADR-008](../adr/ADR-008-silver-history-and-publication.md).

## Qualidade e quarentena

| Regra | Severidade | Validação |
|---|---|---|
| SQ-001 | error | item deve ser objeto |
| SQ-002 | error | question_id inteiro positivo |
| SQ-003 | error | linhagem e coleta UTC plausíveis |
| SQ-004 | error | creation_date obrigatória; epochs inteiros plausíveis |
| SQ-005 | error | atividade, edição e fechamento não anteriores à criação |
| SQ-006 | error | contadores/IDs relacionados com tipos e limites válidos |
| SQ-007 | error | owner objeto e user_id inteiro quando presente |
| SQ-008 | error | tags lista de strings não vazias quando presente |
| SQ-009 | error | campos textuais devem ser strings quando presentes |
| SQ-010 | error | corpo até max_body_chars, default 500000 caracteres |
| SQ-011 | informational | corpo ausente, sem inventar conteúdo |
| SQ-012 | error | is_answered booleano quando presente |
| SQ-013 | warning | inconsistência entre aceite, respostas e is_answered |
| SQ-014 | warning | conteúdos conflitantes no mesmo instante |

Datas válidas começam em 2008-01-01 UTC. Tolerância futura padrão: 86400 segundos, relativa ao relógio e à coleta. Configuração Python permite ajustar limites; CLI expõe max_body_chars. is_answered=false com respostas positivas é legítimo; score negativo também.

Error impede publicação **do item**, não das demais perguntas válidas. Warning publica com evento. Informational registra sem rejeitar. Envelope ilegível, checksum divergente ou falha de persistência falham a execução inteira.

`quarantined_records.jsonl` contém uma entrada por item rejeitado (primeiro erro); `quality_events.jsonl` contém todas as regras acionadas. Campos: transformation_run_id (UUID), source_run_id (string/null), source_page (integer/null), question_id (integer/null), rule_id, severity, reason, field, observed_value (strings), quarantined_at (timestamp UTC), source_path (string), source_item_index (integer). observed_value contém somente tipo/tamanho, nunca corpo. A página Bronze permite investigar o item original. Empates históricos podem voltar a gerar warnings no replay.

## Manifesto e publicação

Manifesto JSON contém transformation_run_id, dataset_id (SHA-256 da raiz Silver absoluta), commit_sequence (inteiro), source_run_ids (array), started_at, finished_at (UTC), status, schema_version, transformer_version, input_pages, input_records, accepted_records, quarantined_records, unchanged_records, new_versions, closed_versions, quality_errors, quality_warnings (inteiros), input_checksums, output_checksums, output_paths (mapas string→string), failure_stage e error_type (string/null).

accepted_records + quarantined_records = input_records em execução concluída. unchanged_records conta itens aceitos cujo conteúdo já era conhecido ou cuja observação já existia; não equivale a métricas inalteradas. new_versions/closed_versions são diferenças entre conjuntos de chaves de intervalos antes/depois, incluindo reconstrução tardia. quality_errors conta eventos, não itens; informational não incrementa erros ou warnings. Execuções failed podem conter contagens parciais.

Apenas completed/empty com todas as saídas confirmadas representam commit. empty significa zero itens de entrada e preserva estado anterior. failed é diagnóstico. started/outputs_persisted aparecem em logs; não são commits. A última sequência válida por dataset seleciona a geração; consumidores não devem escolher diretórios por data. Gerações e manifestos são imutáveis e publicação local exige hard links e lock exclusivo.

## Evolução, retenção e limites

JSONL evita dependência de engine Parquet nesta etapa. Migração para Parquet deve preservar tipos, observações, hashes, nulls e teste de contrato. Não há particionamento temporal por enquanto: cada geração é snapshot completo.

Mudança incompatível de campo, interpretação, normalização ou hash exige nova versão de contrato/transformador, migração explícita e nova raiz de dataset; não há migração automática. Leitor rejeita schema incompatível. A versão v1 aceita Bronze v1/v2 com linhagem completa.

Retenção local: indefinida até política explícita; sem expurgo automático de Bronze, gerações, observações ou quarentena. Isso não define autorização para publicação de dados. Limites: reconstrução em memória e escrita completa por geração, tamanho crescente, filesystem local, locks órfãos, nenhuma proteção WORM contra edição externa. Ausência de nova observação não indica remoção da pergunta. Observações não recuperam edições ocorridas entre coletas nem estabelecem causalidade.
