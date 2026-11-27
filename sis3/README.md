# orders — SIS3 (Cloud Application Development)

Event-driven order service: ECS Fargate API behind an ALB publishes domain
events to SNS; an idempotent Lambda consumer reads them off an SQS queue
(with a dead-letter queue). Everything here is defined in CloudFormation —
nothing in this stack was ever created by clicking in the console.

## Architecture

```
                         ┌─────────────────────────┐
  client ── HTTP:80 ──▶  │   ALB  (public subnets)  │
                         └───────────┬─────────────┘
                                     │ :8080
                         ┌───────────▼─────────────┐
                         │  ECS Fargate: orders-api │
                         │  (TaskRole: sns:Publish) │
                         └───────────┬─────────────┘
                                     │ SNS Publish
                         ┌───────────▼─────────────┐
                         │   SNS: order-events      │
                         └───────────┬─────────────┘
                                     │ fan-out (raw delivery)
                         ┌───────────▼─────────────┐        ┌───────────────┐
                         │   SQS: orders-queue      │─ DLQ ─▶│ orders-dlq    │
                         └───────────┬─────────────┘        └───────────────┘
                                     │ event source mapping
                         ┌───────────▼─────────────┐        ┌───────────────┐
                         │ Lambda: orders-consumer  │───────▶│ DynamoDB:     │
                         │ (idempotent, dedup key)  │        │ idempotency   │
                         └──────────────────────────┘        └───────────────┘
```

## Deploy (one command per stack, two stacks total)

```bash
# 1. Foundation: queue, DLQ, topic, idempotency table, ECR repo, consumer Lambda.
#    No image required yet - this always deploys cleanly from a clean checkout.
aws cloudformation deploy \
  --template-file infra/01-foundation.yaml \
  --stack-name orders-foundation-dev \
  --parameter-overrides Env=dev \
  --capabilities CAPABILITY_NAMED_IAM

# 2. Build and push the container image to the repo the foundation stack just created.
REPO=$(aws cloudformation describe-stacks --stack-name orders-foundation-dev \
  --query "Stacks[0].Outputs[?OutputKey=='EcrRepoName'].OutputValue" --output text)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
TAG=$(git rev-parse --short HEAD)

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com"

docker build -t "$REPO:$TAG" ./app
docker tag "$REPO:$TAG" "$ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/$REPO:$TAG"
docker push "$ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/$REPO:$TAG"

# 3. App stack: VPC, ALB, ECS Fargate service, running the image just pushed.
aws cloudformation deploy \
  --template-file infra/02-app.yaml \
  --stack-name orders-app-dev \
  --parameter-overrides Env=dev ImageTag=$TAG \
  --capabilities CAPABILITY_NAMED_IAM
```

## Reproducibility test (what the defence will ask for)

Deploy a second, independent copy under a new name, from a clean checkout:

```bash
aws cloudformation deploy --template-file infra/01-foundation.yaml \
  --stack-name orders-foundation-staging --parameter-overrides Env=staging \
  --capabilities CAPABILITY_NAMED_IAM
# build/push image again with the staging repo name, then:
aws cloudformation deploy --template-file infra/02-app.yaml \
  --stack-name orders-app-staging --parameter-overrides Env=staging ImageTag=$TAG \
  --capabilities CAPABILITY_NAMED_IAM
```

If anything here needed a console click, it would not count as reproducible.

## Teardown

```bash
aws cloudformation delete-stack --stack-name orders-app-dev
aws cloudformation wait stack-delete-complete --stack-name orders-app-dev
aws cloudformation delete-stack --stack-name orders-foundation-dev
```

`OrdersRepo` and `IdempotencyTable` have `DeletionPolicy: Retain` — they will
survive stack deletion on purpose. Delete them by hand if you really want them
gone (`aws ecr delete-repository --force`, `aws dynamodb delete-table`).

## Use of generative AI

Declare per course policy: infrastructure template and guidance drafted with
AI assistance (Claude), reviewed, deployed and tested by the author.

## Practice 8 - Observability

- Custom metric (EMF): namespace `OrdersApp`, metrics `OrdersProcessed` and `DuplicatesSkipped`, dimension `Env`.
- Alarm `orders-dlq-not-empty-dev` on DLQ depth (>= 1), notifies SNS topic `orders-alarms-dev`, email subscription Confirmed.
- Tracing: active X-Ray tracing on SNS and Lambda (trace map: Client -> Lambda Context -> Lambda Function).
- Budget: monthly, alerts at 50% and 100%.

Saved Logs Insights query `orders-consumer-p95-latency-dev`:

```
filter @type = "REPORT"
| stats avg(@duration) as avg_ms, pct(@duration, 95) as p95_ms, max(@duration) as max_ms, count(*) as invocations by bin(5m) as t
| sort t desc
```

Dashboard JSON (sis3/dashboard-orders-dev.json):

```json
{
    "widgets": [
        {
            "type": "alarm",
            "x": 0,
            "y": 0,
            "width": 24,
            "height": 3,
            "properties": {
                "title": "Alarm status",
                "alarms": [
                    "arn:aws:cloudwatch:us-east-1:409289617759:alarm:orders-dlq-not-empty-dev"
                ]
            }
        },
        {
            "type": "metric",
            "x": 0,
            "y": 3,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "Traffic: orders processed and consumer invocations",
                "region": "us-east-1",
                "stat": "Sum",
                "period": 60,
                "view": "timeSeries",
                "metrics": [
                    [
                        "OrdersApp",
                        "OrdersProcessed",
                        "Env",
                        "dev"
                    ],
                    [
                        "AWS/Lambda",
                        "Invocations",
                        "FunctionName",
                        "orders-consumer-dev"
                    ]
                ]
            }
        },
        {
            "type": "metric",
            "x": 12,
            "y": 3,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "Errors: Lambda errors, duplicates skipped, DLQ depth",
                "region": "us-east-1",
                "stat": "Sum",
                "period": 60,
                "view": "timeSeries",
                "metrics": [
                    [
                        "AWS/Lambda",
                        "Errors",
                        "FunctionName",
                        "orders-consumer-dev"
                    ],
                    [
                        "OrdersApp",
                        "DuplicatesSkipped",
                        "Env",
                        "dev"
                    ],
                    [
                        "AWS/SQS",
                        "ApproximateNumberOfMessagesVisible",
                        "QueueName",
                        "orders-dlq-dev"
                    ]
                ]
            }
        },
        {
            "type": "metric",
            "x": 0,
            "y": 9,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "Latency: consumer duration p95 (ms)",
                "region": "us-east-1",
                "stat": "p95",
                "period": 60,
                "view": "timeSeries",
                "metrics": [
                    [
                        "AWS/Lambda",
                        "Duration",
                        "FunctionName",
                        "orders-consumer-dev"
                    ]
                ]
            }
        },
        {
            "type": "metric",
            "x": 12,
            "y": 9,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "Saturation: queue backlog and age of oldest message",
                "region": "us-east-1",
                "stat": "Maximum",
                "period": 60,
                "view": "timeSeries",
                "metrics": [
                    [
                        "AWS/SQS",
                        "ApproximateNumberOfMessagesVisible",
                        "QueueName",
                        "orders-queue-dev"
                    ],
                    [
                        "AWS/SQS",
                        "ApproximateAgeOfOldestMessage",
                        "QueueName",
                        "orders-queue-dev"
                    ]
                ]
            }
        }
    ]
}
```
