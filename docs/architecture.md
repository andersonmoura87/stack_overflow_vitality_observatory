# Arquitetura implementada

CLI com configuração YAML -> cliente HTTP substituível -> páginas Bronze imutáveis -> prepared -> CAS do watermark -> manifesto terminal. A ingestão local é complementada pela transformação offline de perguntas Bronze → Silver e pela Gold descritiva derivada dessa Silver. Dump/Survey, dashboard e NLP são arquitetura futura.

`RunContext` determina um único diretório por contexto de execução, baseado no início UTC da janela efetiva. Backoff válido é consumido pelo coletor; retries HTTP respeitam backoff mandatório sem reduzi-lo. Bronze usa hard link exclusivo e validação integral dos bytes. Watermark inclui fonte/site/endpoint/tag e usa compare-and-set protegido por lock local. Backfill preserva o watermark.

A janela entre CAS e publicação terminal não é atômica. Prepared permite investigar interrupções, sem promessa de recuperação automática. Veja ADR-007, contratos v2 e runbooks. O módulo Terraform declara apenas a versão mínima da ferramenta e não provisiona recursos.

## Transformação Silver

CLI transform-questions → BronzeReader → normalização pura/qualidade → catálogo de conteúdo + observações → reconstrução temporal → SilverStore + QuarantineStore → confirmação de checksums → TransformationManifestStore.

As quatro portas separam leitura, transformação e efeitos de persistência. O adaptador local lê somente páginas page=*.json; verifica schema e checksum Bronze antes dos itens. Erros de qualidade isolam itens; falhas de envelope/IO impedem commit. O lock por dataset serializa publicação; gerações são identificadas por conteúdo e o manifesto terminal seleciona a geração completa. Não há alteração de watermark ou de páginas Bronze.

O catálogo evita duplicar conteúdo por mudança de métrica; observações preservam toda a proveniência aceita. A materialização possui uma versão atual por pergunta/site. Snapshot tardio reconstrói intervalos por collected_at. Detalhes no [ADR-008](adr/ADR-008-silver-history-and-publication.md) e [contrato](data-contracts/silver_questions.md).

## Gold descritiva

CLI build-vitality lê o manifesto Silver committed, seleciona uma pergunta por site e question_id no corte `as_of` e publica dois JSONL imutáveis mais um manifesto. Métricas vêm da observação com `collected_at <= as_of`. Tags e fechamento vêm da versão de conteúdo vigente nesse corte. Publicação real exige `--as-of`; o dry-run pode apenas exibir o maior `collected_at`. Não altera Bronze, watermark ou Silver. Não estima tempo até resposta nem efeito causal. Detalhes no [ADR-009](adr/ADR-009-gold-descriptive-vitality.md) e no [contrato](data-contracts/gold_vitality.md).
