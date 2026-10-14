# Contract: silver_answer_quality.v1

- **Finalidade:** distinguir rejeição, warning, falha de página e publicação com cobertura explícita.
- **Rejeição (quarentena, o item não entra na Silver):** SA-001 `answer_id` ausente ou inválido; SA-002 `question_id` ausente ou inválido; SA-003 resposta ligada a pergunta fora do lote; SA-004 timestamp inválido, sem UTC ou anterior à criação; SA-005 `creation_at` posterior a `collected_at`; SA-006 score ou aceite com tipo inválido; SA-007 corpo ausente, vazio ou acima do limite; SA-009 duplicata conflitante da mesma identidade de observação; SA-010 quando o user id está presente e é inválido.
- **Warning:** SA-010 owner ausente, com `owner_user_id` nulo; SA-011 divergência entre `accepted_answer_id` da pergunta e `is_accepted` da resposta; SA-012 mais de uma resposta aceita para a mesma pergunta; SA-013 pergunta aponta aceite ainda não coletado; SA-016 conteúdos diferentes no mesmo `collected_at`.
- **Falha:** schema diferente de `bronze_stackexchange_answers_snapshot.v1`, checksum divergente, página ilegível ou caminho fora da raiz. Nada é publicado como sucesso.
- **Publicação parcial:** itens válidos são publicados e o manifesto traz `status=partial`, contagens e `coverage_complete=false` quando há rejeição ou reconciliação aberta. A Silver de perguntas não é alterada.
- **Nulos e JSON:** ausência legítima permanece null. A serialização canônica recusa NaN e Infinity.
