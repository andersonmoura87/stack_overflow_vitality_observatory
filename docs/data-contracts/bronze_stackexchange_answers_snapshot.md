# Contract: bronze_stackexchange_answers_snapshot.v1

- **Finalidade/proprietário:** página original de respostas e proveniência; Data Platform.
- **Origem/granularidade/chave:** `GET /questions/{ids}/answers`; uma página por lote e número de página. O `;` do path é vetor de IDs, não filtro AND de tags.
- **Campos não nulos:** `run_id`, `source`, `endpoint`, `site`, `strategy=question_id_batches`, `batch_id`, `batch_index`, `question_ids`, `question_ids_origin`, `page`, `requested_at`, `recovered_at`, `payload`, `request_params`, `has_more`, `http_status=200`, `collector_version`, `user_agent`, `item_count`, `truncated`, `checksum`, `schema_version`.
- **Campos nulos permitidos:** `quota_max`, `quota_remaining`, `backoff_seconds`.
- **Qualidade:** checksum SHA-256 do payload canônico. IDs completos ficam no objeto, não no diretório. `request_params` não contém chave de API. Confirmação relê o objeto. Destino idêntico é replay; destino divergente é conflito.
- **Particionamento:** `answers/site=<site>/run=<run_id>/b=<16 hex do batch_id>/page=NNNN.json`. A identidade do snapshot é site, execução, lote e página. Dois runs do mesmo lote convivem. O manifesto fica em `answers/manifests/<run_id>.json` e é publicado depois do checkpoint, só quando a execução termina sem falha e sem truncamento. Não há `coverage.json`: o único cursor é o checkpoint.
- **Evolução:** v1 inicial. Sem migração automática.
- **Retenção/consumidores:** sem expurgo automático. Consumidor: Silver de respostas e auditoria.
- **Limitações:** o payload é JSON canônico, não os bytes HTTP crus. Execução truncada ou com lote falho não é sucesso e não entra no checkpoint. Um snapshot antigo permanece legível quando uma coleta posterior grava outro run. Filesystem local com hard links. A janela entre o checkpoint e o manifesto não é uma transação distribuída.
