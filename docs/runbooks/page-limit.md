# Runbook: has_more no limite

A run deve falhar explicitamente com `truncated=true`. Aumentar limite apenas após verificar quota/custo, ou dividir janela/tag. Não avançar watermark até uma execução completa.
