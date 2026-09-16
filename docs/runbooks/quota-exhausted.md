# Runbook: quota esgotada

1. Marcar a run como falha recuperável e preservar páginas já gravadas.
2. Não atualizar watermark.
3. Aguardar janela de quota/backoff indicada; reduzir frequência ou escopo.
4. Reexecutar a mesma janela e comparar `run_id`, quota e duplicação.
