# Data card inicial

**Fontes:** Stack Exchange API com coletor incremental implementado; Data Dump com contrato de Bronze definido e ingestão ainda não implementada; Developer Survey planejado. **Unidade:** pergunta, resposta ou execução, conforme contrato. Comentário não é entidade ingerida. **Uso:** análise longitudinal de comunidade e conteúdo técnico.

**Limitações:** API não representa necessariamente todo o histórico; Survey mede comportamento declarado; tags têm composição variável; sinais de IA e complexidade exigem validação humana; associação temporal não prova causalidade.

**Privacidade e governança:** preservar somente campos necessários, documentar origem e retenção e revisar termos/licença antes de publicar derivados.

**Dados Silver:** perguntas normalizadas de páginas Bronze locais, catálogo de conteúdo e observações de métricas com linhagem até arquivo e posição do item. Validação usa exclusivamente fixtures sintéticas; nenhum conjunto real é distribuído. Ausência legítima permanece null; quarentena é contabilizada e precisa ser considerada antes de análises.

**Dados Gold v1:** agregados descritivos mensais e por tag, no corte `as_of`, calculados só da Silver de perguntas. Os sinais `source_is_answered`, `has_answers` e `has_accepted_answer` permanecem distintos. A exposição até o corte acompanha a série. Os agregados não estimam tempo até resposta, sobrevivência ou efeito causal. Ver [contrato Gold](data-contracts/gold_vitality.md).

**Respostas:** Bronze imutável por lote de perguntas e Silver própria, com conteúdo versionado separado de score e aceite. `owner_user_id` nulo permanece nulo. Divergência de aceite com a pergunta é qualidade, não correção. A cobertura é a das perguntas solicitadas; não é o histórico completo do sítio. Ver [ADR-010](adr/ADR-010-answer-collection-and-history.md).

**Data Dump:** snapshot local de `Posts.xml`, com `source_kind=stackexchange_data_dump` e geração identificada por `ingestion_identity`. A ingestão offline grava só esse Bronze. `published_at` não é a data do post. Corte ausente ou só declarado não torna a fonte temporalmente determinada. A publicação não reconstrói aceite, edições intermediárias nem posts omitidos, e não substitui a Silver da API. Ver [ADR-011](adr/ADR-011-data-dump-posts-snapshot.md).

**Tempo e retenção:** intervalos refletem observação, não todas as alterações reais. Retenção local indefinida, sem expurgo automático. O campo de licença é preservado quando disponível; sua ausência não é preenchida por suposição. Somente owner_user_id é retido, sem perfil completo. Ver [contrato Silver](data-contracts/silver_questions.md).
