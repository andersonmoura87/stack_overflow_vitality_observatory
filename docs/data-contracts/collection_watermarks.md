# Contract: collection_watermarks.v2

- **Finalidade/proprietário:** último fim de janela operacional confirmado; Data Platform.
- **Origem/granularidade:** coleta API / uma unidade identificada por fonte, site, endpoint e tag.
- **Chave:** JSON determinístico da tupla `(source, site, endpoint, tag_or_group)`; nenhuma concatenação ambígua.
- **Campos:** `source`, `site`, `endpoint`, `tag_or_group` strings não nulas; `value`, `updated_at` timestamps UTC não nulos.
- **Qualidade:** CAS compara registro anterior completo; novo valor não pode retroceder. Lock local protege leitura/validação/substituição atômica. JSON ou schema corrompido falha explicitamente e não é sobrescrito. Backfill não altera estado. Outros sites/tags/endpoints/fontes não colidem.
- **Particionamento:** mapa único em `watermarks.json`, com lock irmão `watermarks.json.lock`. É estado operacional compartilhado, não um snapshot dentro do diretório de uma run.
- **Evolução:** schema v2 exige site/endpoint; legado é rejeitado para evitar atribuição incorreta. Migração exige evidência da identidade e intervenção explícita.
- **Retenção/consumidores:** estado corrente; prepared/manifestos preservam os limites antes/depois. Coletor e recuperação operacional consomem o estado.
- **Limitações:** lock local fail-fast, sem coordenação distribuída. Crash pode deixar lock órfão. Após CAS, falha no terminal não reverte watermark. Não se permite rebaixamento direto como recuperação; use backfill e revisão operacional. Ver ADR-007.
