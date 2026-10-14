# Contract: silver_answer_metric_observations.v1

- **Finalidade/proprietário:** observações de score e aceite; Data Platform.
- **Grão/chave:** `site + answer_id + collected_at`, distinguindo ainda `observation_id` quando duas origens compartilham o instante.
- **Campos:** `schema_version`, `site`, `answer_id`, `question_id`, `collected_at`, `last_activity_at`, `score`, `is_accepted`, `record_hash`, `observation_id`, `quality`, `lineage`.
- **Nulos:** `last_activity_at`, `score` e `is_accepted` permanecem nulos quando a fonte os omite. Não são convertidos em zero ou falso.
- **Tempo:** `collected_at` é o `recovered_at` da página Bronze, em UTC com sufixo `Z`. Não é o relógio de processamento. `last_activity_at` é o timestamp de atividade informado pela fonte naquela observação. Mudá-lo não abre versão de conteúdo.
- **Identidade:** SHA-256 de site, run, lote, página, checksum do arquivo e índice do item. Replay da mesma origem não cria outra observação.
- **Integridade:** `record_hash` existe no catálogo de respostas. Score e aceite diferentes com o mesmo hash compartilham uma versão de conteúdo.
- **Limitações:** `is_accepted` é estado observado, não o instante em que a resposta foi aceita. Ausência numa coleta não prova exclusão.
