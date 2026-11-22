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
