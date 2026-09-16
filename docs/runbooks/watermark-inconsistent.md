# Runbook: watermark inconsistente

Suspender novas coletas dessa unidade. Conferir a identidade completa: source, site, endpoint e tag. JSON inválido ou legado sem identidade falha explicitamente; não reconstruir estado por suposição.

CAS divergente é conflito: outro escritor pode ter avançado. Reler estado, inspecionar prepared/terminal e planejar nova run. Retrocesso é proibido; recuperação histórica deve usar `--backfill`, que não altera estado operacional.

`watermarks.json.lock` é um diretório de exclusão local fail-fast. Após crash, verificar que não existe escritor ativo antes de removê-lo manualmente. Não há expiração automática nem lock distribuído. Preservar estado e registrar intervenção.

CAS bem-sucedido seguido de crash pode deixar apenas prepared. Validar páginas e usar backfill para reconstruir evidência; não publicar sucesso sem investigar. Ver ADR-007.
