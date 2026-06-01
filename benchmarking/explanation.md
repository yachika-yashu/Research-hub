| Metric                       | Meaning                                                                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `faithfulness: 0.82`         | 82% of the AI answers should stay grounded in the provided context/documents instead of hallucinating or inventing information. |
| `context_recall: 0.78`       | The retrieval system should successfully fetch about 78% of the relevant information needed to answer user queries.             |
| `p99_latency_ms: 5000`       | 99% of user requests should complete within 5 seconds. Only 1% are allowed to be slower.                                        |
| `cost_per_query_usd: 0.008`  | Each AI query should cost no more than $0.008 on average. This controls infrastructure and API expenses.                        |
| `worker_p99_ms: 30000`       | 99% of background worker jobs (processing pipelines, evaluations, indexing, etc.) should finish within 30 seconds.              |
| `pii_leak_rate: 0.0`         | The system should never leak personally identifiable information (PII) like emails, phone numbers, SSNs, or private user data.  |
| `injection_block_rate: 0.95` | The security system should successfully block 95% of prompt injection or jailbreak attempts.                                    |
