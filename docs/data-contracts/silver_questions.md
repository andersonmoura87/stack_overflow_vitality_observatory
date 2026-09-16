# Contract: silver_questions

- **Purpose:** perguntas normalizadas e deduplicadas.
- **Owner:** Analytics Engineering.
- **Source/granularity:** Bronze / uma versão observada por `question_id`.
- **Key:** `question_id + observed_version`.
- **Fields:** `question_id` int non-null; `title` string; `body_html` string; `body_text` string; `code_blocks` array; `tags` array; `creation_date` timestamp UTC; `last_activity_date` timestamp UTC; `accepted_answer_id` int nullable; `closed_date` timestamp UTC nullable; `observed_version` string; `source_run_id` UUID.
- **Quality:** ID positivo, datas UTC e ordenadas, tags não vazias quando presentes, HTML/texto separados.
- **Partition:** `year/month/tag` com partição de ingestão disponível.
- **Evolution:** schema versionado, mudanças breaking com migração.
- **Retention/consumers:** conforme política analítica; Gold e NLP.
- **Limitations:** deduplicação não elimina mudanças legítimas do conteúdo.
