# Arquitetura implementada

CLI com configuração YAML -> cliente HTTP substituível -> páginas Bronze imutáveis -> prepared -> CAS do watermark -> manifesto terminal. A ingestão local é complementada pela transformação offline de perguntas Bronze → Silver, pela Gold descritiva derivada dessa Silver e pela trilha separada de respostas: lotes de IDs → Bronze imutável → Silver histórica. O Data Dump entra por `ingest-data-dump`, com contrato e caminho próprios. Survey, dashboard e NLP continuam futuros.

`RunContext` determina um único diretório por contexto de execução, baseado no início UTC da janela efetiva. Backoff válido é consumido pelo coletor; retries HTTP respeitam backoff mandatório sem reduzi-lo. Bronze usa hard link exclusivo e validação integral dos bytes. Watermark inclui fonte/site/endpoint/tag e usa compare-and-set protegido por lock local. Backfill preserva o watermark.

A janela entre CAS e publicação terminal não é atômica. Prepared permite investigar interrupções, sem promessa de recuperação automática. Veja ADR-007, contratos v2 e runbooks. O módulo Terraform declara apenas a versão mínima da ferramenta e não provisiona recursos.

## Transformação Silver

CLI transform-questions → BronzeReader → normalização pura/qualidade → catálogo de conteúdo + observações → reconstrução temporal → SilverStore + QuarantineStore → confirmação de checksums → TransformationManifestStore.

As quatro portas separam leitura, transformação e efeitos de persistência. O adaptador local lê somente páginas page=*.json; verifica schema e checksum Bronze antes dos itens. Erros de qualidade isolam itens; falhas de envelope/IO impedem commit. O lock por dataset serializa publicação; gerações são identificadas por conteúdo e o manifesto terminal seleciona a geração completa. Não há alteração de watermark ou de páginas Bronze.

O catálogo evita duplicar conteúdo por mudança de métrica; observações preservam toda a proveniência aceita. A materialização possui uma versão atual por pergunta/site. Snapshot tardio reconstrói intervalos por collected_at. Detalhes no [ADR-008](adr/ADR-008-silver-history-and-publication.md) e [contrato](data-contracts/silver_questions.md).

## Gold descritiva

CLI build-vitality lê o manifesto Silver committed, seleciona uma pergunta por site e question_id no corte `as_of` e publica dois JSONL imutáveis mais um manifesto. Métricas vêm da observação com `collected_at <= as_of`. Tags e fechamento vêm da versão de conteúdo vigente nesse corte. Publicação real exige `--as-of`; o dry-run pode apenas exibir o maior `collected_at`. Não altera Bronze, watermark ou Silver. Não estima tempo até resposta nem efeito causal. Detalhes no [ADR-009](adr/ADR-009-gold-descriptive-vitality.md) e no [contrato](data-contracts/gold_vitality.md).

## Respostas

`collect-answers` chama o mesmo cliente HTTP em `/questions/{ids}/answers`. O `;` separa IDs e não é o AND de tags. Cada snapshot fica em `answers/site=*/run=<run_id>/b=<hash>/page=NNNN.json`. O modo `incremental` busca só lotes fora do checkpoint e só então avança o cursor. O modo `refresh` busca de novo os lotes pedidos e não avança o cursor. Falha e truncamento também não avançam. O manifesto sai depois do checkpoint.

`transform-answers` publica catálogo, observações de score/aceite e versões de conteúdo. A história é ordenada por `collected_at`, independente da ordem de leitura. A Silver de perguntas só é lida, quando um arquivo é informado, para confrontar aceite. Divergência vira qualidade. Ver [ADR-010](adr/ADR-010-answer-collection-and-history.md).

## Data Dump

O contrato [bronze_stackexchange_data_dump_posts.v1](data-contracts/bronze_stackexchange_data_dump_posts.md) descreve um `Posts.xml` local e não comprimido. A geração é `ingestion_identity`, não um `run_id` aleatório. Shards de `completed` e `empty` só são consumo depois do manifesto terminal. `partial` fica legível e não consumível. O staging da tentativa não é dataset. `publish` não serve para esse JSONL: o publicador do dump usa hard link e staging. `source_kind=stackexchange_data_dump` não se mistura ao Bronze da API. O comando de ingestão é `ingest-data-dump`. Não há download nem modo completo.

`transform-data-dump` lê exatamente uma geração Bronze consumível e publica quatro JSONL em `--output-dir`, no grão de snapshot. A identidade é `silver_generation_id`. Ausência num snapshot posterior não é exclusão. Não há `valid_from`, `valid_to`, `is_current` nem `accepted_at`. Essa Silver não é lida pela Gold e não ocupa `silver/questions` nem `silver/answers`. A decisão está no [ADR-011](adr/ADR-011-data-dump-posts-snapshot.md) e no [ADR-012](adr/ADR-012-data-dump-silver-history.md).
