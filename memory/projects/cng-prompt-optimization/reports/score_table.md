# Optimization Score Table

| run_id | n | mean/30 | median | stdev | mean latency s | prompt | sampler |
|---|---:|---:|---:|---:|---:|---|---|
| iter_003_v3_sample15 | 15 | 27.0 | 28 | 5.2409 | 34.919 | prompt_v3_ndfix.txt | {"temperature": 0.2, "top_p": 0.9, "max_tokens": 6000} |
| iter_002_v2_sample15 | 15 | 26.8 | 29 | 7.3412 | 34.141 | prompt_v2_baseline.txt | {"temperature": 0.2, "top_p": 0.9, "max_tokens": 6000} |
| iter_004_v3b_sample15 | 15 | 26.6 | 28 | 4.3019 | 32.458 | prompt_v3b_guardrails.txt | {"temperature": 0.15, "top_p": 0.85, "max_tokens": 5000} |

Note: iter_001 was excluded due parser bug before section-heading normalization fix.
