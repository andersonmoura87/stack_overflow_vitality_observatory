# Data card inicial

**Fontes:** Stack Exchange API com coletor incremental implementado; Data Dump com Bronze offline e Silver de snapshots; Developer Survey planejado. **Unidade:** pergunta, resposta ou execução, conforme contrato. Comentário não é entidade ingerida. **Uso:** análise longitudinal de comunidade e conteúdo técnico.

**Limitações:** API não representa necessariamente todo o histórico; Survey mede comportamento declarado; tags têm composição variável; sinais de IA e complexidade exigem validação humana; associação temporal não prova causalidade.

**Privacidade e governança:** preservar somente campos necessários, documentar origem e retenção e revisar termos/licença antes de publicar derivados.

**Dados Silver:** perguntas normalizadas de páginas Bronze locais, catálogo de conteúdo e observações de métricas com linhagem até arquivo e posição do item. Validação usa exclusivamente fixtures sintéticas; nenhum conjunto real é distribuído. Ausência legítima permanece null; quarentena é contabilizada e precisa ser considerada antes de análises.

**Dados Gold v1:** agregados descritivos mensais e por tag, no corte `as_of`, calculados só da Silver de perguntas. Os sinais `source_is_answered`, `has_answers` e `has_accepted_answer` permanecem distintos. A exposição até o corte acompanha a série. Os agregados não estimam tempo até resposta, sobrevivência ou efeito causal. Ver [contrato Gold](data-contracts/gold_vitality.md).

**Respostas:** Bronze imutável por lote de perguntas e Silver própria, com conteúdo versionado separado de score e aceite. `owner_user_id` nulo permanece nulo. Divergência de aceite com a pergunta é qualidade, não correção. A cobertura é a das perguntas solicitadas; não é o histórico completo do sítio. Ver [ADR-010](adr/ADR-010-answer-collection-and-history.md).

**Data Dump:** snapshot local de `Posts.xml`, com `source_kind=stackexchange_data_dump`. O Bronze é `ingestion_identity`. A Silver própria é `silver_generation_id` e guarda o estado observado naquele corte, sem intervalo de vigência. `published_at` não é a data do post. Corte ausente ou só declarado não torna a fonte temporalmente determinada e não autoriza métrica causal. A publicação não reconstrói o instante do aceite, edições intermediárias nem posts omitidos, e não substitui a Silver da API. A reconciliação com a API está desenhada, sem implementação e sem consumo pela Gold. Ausência numa fonte não é exclusão. Ver [ADR-012](adr/ADR-012-data-dump-silver-history.md) e [ADR-013](adr/ADR-013-api-data-dump-reconciliation.md).

**Tempo e retenção:** intervalos refletem observação, não todas as alterações reais. Retenção local indefinida, sem expurgo automático. O campo de licença é preservado quando disponível; sua ausência não é preenchida por suposição. Somente owner_user_id é retido, sem perfil completo. Ver [contrato Silver](data-contracts/silver_questions.md).
