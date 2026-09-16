# Contract: ingestion_runs.v2

- **Finalidade/proprietário:** auditoria da transação lógica local; SRE/DataOps.
- **Origem/granularidade/chave:** coletor / uma tentativa / UUID novo por tentativa.
- **Artefatos:** `prepared.json` imutável em estado intermediário `pages_persisted`; `manifest.json` imutável em estado terminal `completed`, `empty`, `truncated` ou `failed`. Planejamento/coleta são estágios da execução, não manifestos terminais.
- **Campos não nulos:** `run_id`, `source`, `endpoint`, `site`, `tag`; `window_start`, `window_end`, `started_at`, `finished_at` UTC; `status`; `pages_requested`, `pages_persisted`, `items_received` inteiros não negativos; `truncated`, `backfill` booleanos; `collector_version`; `errors` array de nomes de tipos.
- **Campos nullable:** `quota_remaining` inteiro, `watermark_before`, `watermark_after` UTC, `failure_stage` string. `pages_requested` conta páginas lógicas, incluindo replay; tentativas HTTP aparecem nos logs.
- **Qualidade:** intervalos ordenados; páginas persistidas <= solicitadas; sucesso operacional exige watermark_after igual ao fim da janela e ausência de erro/truncamento. `prepared` não declara avanço. Backfill permite sucesso sem CAS, identificado explicitamente. Erros contêm estágio/tipo e nunca mensagem ou URL de exceção com segredo.
- **Particionamento:** mesmo `RunContext` e diretório das páginas. O retorno `IngestionRun` da CLI é resumo, não o schema do manifesto.
- **Evolução:** v2 formaliza preparo separado e acrescenta modo/estágio; mudanças incompatíveis exigem versão.
- **Retenção/consumidores:** preservados sem limpeza automática; auditoria, monitoramento e recuperação.
- **Limitações:** crash após CAS pode deixar apenas prepared; falha na publicação terminal pode exigir recuperação manual. Falha antes do CAS preserva watermark. Não existe transação atômica entre os arquivos. Ver ADR-007.
