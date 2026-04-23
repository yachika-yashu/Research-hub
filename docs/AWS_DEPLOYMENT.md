# ☁️ AWS Deployment Guide: Hanuman Platform

Hanuman is designed to be cloud-agnostic. For AWS, we recommend a **Containerized Deployment** using **Amazon ECS** or **AWS App Runner**.

## 🚀 Option 1: AWS App Runner (Easiest)
Best for small to medium research teams. It handles scaling and SSL automatically.
1. **Push to ECR**: Push your Docker image to Amazon Elastic Container Registry.
2. **Create Service**: Connect App Runner to your ECR repository.
3. **Environment**: Add your `.env` variables in the App Runner console.
4. **Networking**: Ensure it can reach your Qdrant instance (if hosted separately).

## 🐳 Option 2: Amazon EC2 + Docker Compose (Recommended for Starters)
Most similar to your local environment.
1. **Provision EC2**: Launch a `t3.medium` or larger instance (Ubuntu).
2. **Install Docker**: Install Docker and Docker Compose.
3. **Clone & Config**: Clone your repo, add `.env`.
4. **Launch**: `docker-compose up -d`.
5. **Reverse Proxy**: Use Nginx or AWS ALB for SSL (HTTPS).

## 🧠 Database Considerations in AWS
*   **Qdrant**: In production, use an **EBS Volume** mounted to `./qdrant_storage` to ensure data persists if the container restarts.
*   **SQLite**: The `research.db` and `checkpoints.db` should also be stored on a persistent volume.
*   **S3 (Future Step)**: For high-availability, you should refactor `ASSETS_DIR` to use an S3 bucket instead of local storage.

## 📝 Logging in AWS
Hanuman is configured to log to `STDOUT`. This means:
*   **CloudWatch**: All logs are automatically captured by AWS CloudWatch Logs if using ECS or App Runner.
*   **Observability**: Use CloudWatch Insights to query for `[ERROR]` or `[QUERY]` tags to monitor system health.

## 📈 Baseline Before Production Cutover

Before moving traffic to AWS, capture a simple performance baseline:

1. Run `python perf/benchmark_api.py --mode health --runs 10`
2. Run `python perf/benchmark_api.py --mode query --token "<TOKEN>" --query "Summarize the latest uploaded paper." --runs 5`
3. If ingestion is a key workflow, run `python perf/benchmark_api.py --mode ingest --token "<TOKEN>" --ingest-file test.pdf --runs 2`
4. Save the results in `docs/PERF_BASELINE.md`

This gives you a before/after record once the app is deployed behind AWS networking, managed databases, and load balancers.
