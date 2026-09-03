# PayFlow Load-Test Results

## Test environment

The test was executed locally using Docker Compose with:

- FastAPI API
- PostgreSQL
- Redis
- Celery worker
- Celery Beat
- Provider simulator
- Prometheus
- Grafana

Locust generated a mixed workload containing payment creation, payment retrieval, and payment-event retrieval.

## 10-user baseline

| Metric           |                Result |
| ---------------- | --------------------: |
| Concurrent users |                    10 |
| Total requests   |                   932 |
| Failures         |                     0 |
| Failure rate     |                    0% |
| Throughput       | 15.60 requests/second |
| Average latency  |                 13 ms |
| Median latency   |                 10 ms |
| p95 latency      |                 27 ms |
| p99 latency      |                 97 ms |
| Maximum latency  |                280 ms |

## 25-user test

| Metric                        |                Result |
| ----------------------------- | --------------------: |
| Concurrent users              |                    25 |
| Total requests                |                 2,355 |
| Failures                      |                     0 |
| Failure rate                  |                    0% |
| Throughput                    | 39.82 requests/second |
| Average latency               |               8.87 ms |
| Median latency                |                  7 ms |
| p95 latency                   |                 17 ms |
| p99 latency                   |                 52 ms |
| Maximum latency               |             243.93 ms |
| Celery queue after completion |                     0 |
| Application readiness         |                 Ready |

## Command

```bash
python -m locust \
  -f locustfile.py \
  --headless \
  --users 25 \
  --spawn-rate 5 \
  --run-time 60s \
  --host http://localhost:8000 \
  --csv loadtest/results/users25
```
