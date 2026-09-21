# Data card inicial

**Fontes:** Stack Exchange API com coletor incremental implementado; Data Dump e Developer Survey planejados. **Unidade:** pergunta, resposta, comentário ou execução, conforme contrato. **Uso:** análise longitudinal de comunidade e conteúdo técnico.

**Limitações:** API não representa necessariamente todo o histórico; Survey mede comportamento declarado; tags têm composição variável; sinais de IA e complexidade exigem validação humana; associação temporal não prova causalidade.

**Privacidade e governança:** preservar somente campos necessários, documentar origem e retenção e revisar termos/licença antes de publicar derivados.

**Dados Silver:** perguntas normalizadas de páginas Bronze locais, catálogo de conteúdo e observações de métricas com linhagem até arquivo e posição do item. Validação usa exclusivamente fixtures sintéticas; nenhum conjunto real é distribuído. Ausência legítima permanece null; quarentena é contabilizada e precisa ser considerada antes de análises.

**Tempo e retenção:** intervalos refletem observação, não todas as alterações reais. Retenção local indefinida, sem expurgo automático. O campo de licença é preservado quando disponível; sua ausência não é preenchida por suposição. Somente owner_user_id é retido, sem perfil completo. Ver [contrato Silver](data-contracts/silver_questions.md).
