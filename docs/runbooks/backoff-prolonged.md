# Runbook: backoff prolongado

O backoff mandatório da API é aguardado integralmente, mesmo acima do máximo de retry local. HTTP limita apenas exponential backoff + jitter e usa o maior entre esse atraso local e o exigido pela API. Payload válido é aguardado pelo coletor antes da escrita, inclusive na última página, para evitar que falha de persistência elimine a obrigação.

A CLI conecta `time.sleep`; testes injetam sleeper falso. Uma interrupção do processo durante espera exige aguardar o restante antes de reiniciar manualmente. Não há coordenação de backoff entre processos nem cancelamento automático por limite operacional.

Investigar quota, escopo e frequência. Não reduzir o backoff da API para acelerar a execução. Dry-run nunca espera.
