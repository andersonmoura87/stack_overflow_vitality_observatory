# Contract: bronze_stackexchange_questions_snapshot.v2

- **Finalidade/proprietário:** página original da API e proveniência; Data Platform.
- **Origem/granularidade/chave:** Stack Exchange API; uma página por run; contexto de execução + `page`.
- **Campos não nulos:** `run_id` UUID; `source`, `endpoint`, `site`, `tag`, `collector_version`, `schema_version`, `checksum` strings; `window` objeto com `start` e `end` UTC; `page` inteiro positivo; `recovered_at` UTC; `payload` objeto JSON; `request_params` objeto sem segredo; `has_more` booleano.
- **Campos nulos permitidos:** `quota_max`, `quota_remaining`, `backoff_seconds` inteiros não negativos; `replayed_from_run_id` UUID.
- **Qualidade:** checksum SHA-256 do payload canônico, preservação de campos desconhecidos no payload, confirmação por parse, tamanho e SHA-256 dos bytes canônicos do envelope completo. Metadados esperados devem corresponder ao arquivo. Destino idêntico é replay sem reescrita; destino divergente gera conflito. Temporário não é objeto confirmado.
- **Particionamento:** `stackexchange/questions/site=<encoded>/tag=<encoded>/window_date=<window.start UTC>/run_id=<UUID>/page=NNNN.json`. Função única recebe `RunContext`; `recovered_at` não determina caminho.
- **Evolução:** v2 muda a convenção de caminhos e documenta o objeto `window` real. Dados legados são preservados; nenhuma migração automática. Campo de replay é aditivo.
- **Retenção/consumidores:** sem remoção automática nesta fase; orçamento/retenção definitiva pendentes. Consumidores futuros: Silver, auditoria e reprocessamento.
- **Limitações:** payload é JSON canônico, não bytes HTTP originais; recuperação de replay permanece a original. Imutabilidade é garantida pelo adaptador para escritores que o utilizam, não por proteção contra alteração externa. Requer filesystem local com hard links; ver ADR-007.
