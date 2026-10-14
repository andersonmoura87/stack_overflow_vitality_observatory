# Contract: silver_answer_content_versions.v1

- **Finalidade/proprietário:** versões textuais de respostas; Data Platform.
- **Grão/chave:** `site + answer_id + valid_from`. Relacionamento com a pergunta: `site + question_id`. A resposta não é aninhada na Silver de perguntas.
- **Campos:** `schema_version`, `entity_type=answer`, `site`, `answer_id`, `question_id`, `creation_at`, `last_edit_at`, `owner_user_id`, `body_html`, `body_text`, `code_blocks`, `content_license`, `record_hash`, `valid_from`, `valid_to`, `is_current`, `quality`, `lineage`.
- **Hash de conteúdo:** schema, entity_type, site, answer_id, question_id, creation_at, last_edit_at, owner_user_id, body_html, content_license. Não entram score, is_accepted, last_activity_at, collected_at, body_text, code_blocks nem linhagem.
- **Nulos:** `last_edit_at`, `owner_user_id`, `content_license`, `valid_to`. Score, aceite e `last_activity_at` não aparecem nesta versão.
- **Tempo:** datas em UTC terminando em `Z`. `valid_from` é `collected_at` da observação que abre o intervalo, não o instante real da edição. `valid_to` nulo se e só se `is_current`.
- **Catálogo:** `silver_answer_catalog.v1`, separado do catálogo de perguntas, com `entity_type=answer` e o mesmo `record_hash`.
- **Limitações:** `last_activity_at` não faz parte da versão de conteúdo. A série fica só nas observações de métricas. Não é o instante da edição nem o instante do aceite.
