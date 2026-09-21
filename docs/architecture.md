# Arquitetura implementada

CLI com configuração YAML -> cliente HTTP substituível -> páginas Bronze imutáveis -> prepared -> CAS do watermark -> manifesto terminal. A ingestão local é complementada pela transformação offline de perguntas Bronze → Silver. Dump/Survey, Gold e dashboard/NLP são arquitetura futura.

`RunContext` determina um único diretório por contexto de execução, baseado no início UTC da janela efetiva. Backoff válido é consumido pelo coletor; retries HTTP respeitam backoff mandatório sem reduzi-lo. Bronze usa hard link exclusivo e validação integral dos bytes. Watermark inclui fonte/site/endpoint/tag e usa compare-and-set protegido por lock local. Backfill preserva o watermark.

A janela entre CAS e publicação terminal não é atômica. Prepared permite investigar interrupções, sem promessa de recuperação automática. Veja ADR-007, contratos v2 e runbooks. O módulo Terraform declara apenas a versão mínima da ferramenta e não provisiona recursos.

## Transformação Silver

CLI transform-questions → BronzeReader → normalização pura/qualidade → catálogo de conteúdo + observações → reconstrução temporal → SilverStore + QuarantineStore → confirmação de checksums → TransformationManifestStore.

As quatro portas separam leitura, transformação e efeitos de persistência. O adaptador local lê somente páginas page=*.json; verifica schema e checksum Bronze antes dos itens. Erros de qualidade isolam itens; falhas de envelope/IO impedem commit. O lock por dataset serializa publicação; gerações são identificadas por conteúdo e o manifesto terminal seleciona a geração completa. Não há alteração de watermark ou de páginas Bronze.

O catálogo evita duplicar conteúdo por mudança de métrica; observações preservam toda a proveniência aceita. A materialização possui uma versão atual por pergunta/site. Snapshot tardio reconstrói intervalos por collected_at. Detalhes no [ADR-008](adr/ADR-008-silver-history-and-publication.md) e [contrato](data-contracts/silver_questions.md).
