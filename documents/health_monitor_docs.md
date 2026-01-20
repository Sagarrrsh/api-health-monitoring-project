# Health Monitor - Complete Deployment Guide

## 📋 Overview

This project monitors API endpoints and sends Slack alerts when services go up or down. It uses AWS infrastructure (ECS Fargate, RDS PostgreSQL, SQS) with Terraform for infrastructure-as-code.

**Architecture:**
- **API Service**: FastAPI REST API for managing monitors
- **Scheduler Service**: Polls database and queues health checks to SQS
- **Worker Service**: Processes SQS messages, performs health checks, updates database, sends Slack alerts
- **RDS PostgreSQL**: Stores monitor configurations and status
- **SQS**: Message queue for health check tasks
- **Application Load Balancer**: Routes traffic to API service

---

## 🛠️ Prerequisites

Before starting, ensure you have:

1. **AWS Account** with credentials configured
2. **AWS CLI** installed and configured (`aws configure`)
3. **Terraform** installed (v1.0+)
4. **Docker** installed and running
5. **Slack Workspace** with admin access

---

## 📁 Project Structure

```
health-monitor/
├── docker/
│   ├── Dockerfile.api
│   ├── Dockerfile.dbinit
│   ├── Dockerfile.scheduler
│   └── Dockerfile.worker
├── documents/
│   ├── DEPLOYMENT.md (this file)
│   └── ARCHITECTURE.md
├── src/
│   ├── api.py
│   ├── db_init.py
│   ├── scheduler.py
│   └── worker.py
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── vpc.tf
│   ├── security.tf
│   ├── rds.tf
│   ├── sqs.tf
│   ├── iam.tf
│   ├── ecr.tf
│   ├── ecs.tf
│   ├── autoscaling.tf
│   └── alb.tf
├── README.md
└── requirements.txt
```

---

## 🚀 Deployment Steps

### STEP 1: Create Slack Webhook

1. Go to your Slack workspace → **Apps** → **Incoming Webhooks**
2. Click **Enable** → **Add New Webhook to Workspace**
3. Select the channel where you want alerts
4. **Copy the webhook URL** (looks like: `https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXX`)
5. Save this URL - you'll need it later

---

### STEP 2: Clone Project and Navigate to Root

```bash
# Navigate to project root
cd /path/to/health-monitor
```

---

### STEP 3: Create terraform.tfvars

Create a file `terraform/terraform.tfvars` with your database password:

```bash
cd terraform
cat > terraform.tfvars << 'EOF'
db_password = "YourStrongPassword123!"
EOF
```

> ⚠️ **Security Note**: Use a strong password (16+ characters, mix of uppercase, lowercase, numbers, symbols). Never commit this file to version control.

---

### STEP 4: Deploy Infrastructure with Terraform

```bash
# Initialize Terraform (downloads providers)
terraform init

# Preview what will be created
terraform plan

# Deploy infrastructure (type 'yes' when prompted)
terraform apply
```

This will create:
- VPC with public/private subnets
- RDS PostgreSQL database
- SQS queue
- ECS cluster with task definitions
- ECR repositories for Docker images
- Application Load Balancer
- IAM roles and security groups

**⏱️ Deployment time**: ~10-15 minutes

---

### STEP 5: Save Terraform Outputs

After deployment completes, save the important outputs:

```bash
terraform output -json > ../outputs.json
```

**Key outputs you'll need:**
```bash
# View specific outputs
terraform output api_url
terraform output ecr_repositories
terraform output sqs_queue_url
terraform output ecs_cluster_name
terraform output public_subnet_ids
terraform output ecs_security_group_id
```

---

### STEP 6: Build and Push Docker Images

#### 6.1 Get Your AWS Account ID

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION="us-east-1"
echo "Account ID: $AWS_ACCOUNT_ID"
```

#### 6.2 Login to ECR

```bash
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
```

#### 6.3 Build and Push All Images

Navigate back to project root:

```bash
cd ..
```

**Build and push API:**
```bash
docker build -f docker/Dockerfile.api -t hm-api .
docker tag hm-api:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-api:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-api:latest
```

**Build and push Scheduler:**
```bash
docker build -f docker/Dockerfile.scheduler -t hm-scheduler .
docker tag hm-scheduler:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-scheduler:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-scheduler:latest
```

**Build and push Worker:**
```bash
docker build -f docker/Dockerfile.worker -t hm-worker .
docker tag hm-worker:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-worker:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-worker:latest
```

**Build and push DB Init:**
```bash
docker build -f docker/Dockerfile.dbinit -t hm-dbinit .
docker tag hm-dbinit:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-db-init:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-db-init:latest
```

---

### STEP 7: Deploy Services to ECS

Force ECS to pull the new images and restart services:

```bash
aws ecs update-service \
  --cluster health-monitor-cluster \
  --service health-monitor-api \
  --force-new-deployment

aws ecs update-service \
  --cluster health-monitor-cluster \
  --service health-monitor-scheduler \
  --force-new-deployment

aws ecs update-service \
  --cluster health-monitor-cluster \
  --service health-monitor-worker \
  --force-new-deployment
```

**⏱️ Wait time**: 2-3 minutes for services to stabilize

---

### STEP 8: Initialize Database

Run the DB initialization task **once** to create the database table:

```bash
# Get subnet and security group IDs from terraform output
SUBNET_1=$(terraform -chdir=terraform output -json public_subnet_ids | jq -r '.[0]')
SUBNET_2=$(terraform -chdir=terraform output -json public_subnet_ids | jq -r '.[1]')
SECURITY_GROUP=$(terraform -chdir=terraform output -raw ecs_security_group_id)

# Run the db-init task
aws ecs run-task \
  --cluster health-monitor-cluster \
  --task-definition health-monitor-db-init \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_1,$SUBNET_2],securityGroups=[$SECURITY_GROUP],assignPublicIp=ENABLED}"
```

**Verify DB initialization:**
```bash
# Check CloudWatch logs (wait 30 seconds after running task)
aws logs tail /ecs/health-monitor-db-init --follow
```

Expected output:
```
[DB INIT] monitors table ensured
```

---

### STEP 9: Test the API

Get your API URL:
```bash
API_URL=$(terraform -chdir=terraform output -raw api_url)
echo "API URL: $API_URL"
```

**Test health endpoint:**
```bash
curl $API_URL/health
```

Expected response:
```json
{"status":"ok"}
```

---

### STEP 10: Create Your First Monitor

Replace `YOUR_SLACK_WEBHOOK_URL` with the webhook from Step 1:

```bash
curl -X POST $API_URL/monitors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Google Homepage",
    "url": "https://google.com/",
    "check_interval": 30,
    "timeout": 5,
    "expected_status_code": 200,
    "webhook_url": "YOUR_SLACK_WEBHOOK_URL",
    "enabled": true
  }'
```

**List all monitors:**
```bash
curl $API_URL/monitors | jq
```

---

### STEP 11: Verify System is Working

#### Check SQS Queue Activity

```bash
SQS_URL=$(terraform -chdir=terraform output -raw sqs_queue_url)

aws sqs get-queue-attributes \
  --queue-url "$SQS_URL" \
  --attribute-names ApproximateNumberOfMessagesVisible ApproximateNumberOfMessagesNotVisible
```

You should see messages being processed (numbers will fluctuate).

#### Check Worker Logs

```bash
aws logs tail /ecs/health-monitor-worker --follow
```

Look for:
```
[STATE CHANGE] monitor=1 UNKNOWN -> UP
[WEBHOOK] sent status=200
```

#### Check Slack

You should receive a notification like:
```
🔔 API Status Changed
Name: Google Homepage
URL: https://google.com/
Old: UNKNOWN -> New: UP
Time: 2026-01-20T10:30:00.000Z
```

---

### STEP 12: Test Failure Detection (Optional)

Create a monitor that will fail (expecting wrong status code):

```bash
curl -X POST $API_URL/monitors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Fail Test",
    "url": "https://google.com/",
    "check_interval": 30,
    "timeout": 5,
    "expected_status_code": 500,
    "webhook_url": "YOUR_SLACK_WEBHOOK_URL",
    "enabled": true
  }'
```

After 2 consecutive failures (~1 minute), you'll receive a DOWN alert in Slack.

---

## 📊 Monitoring and Logs

### View Logs

**API logs:**
```bash
aws logs tail /ecs/health-monitor-api --follow
```

**Scheduler logs:**
```bash
aws logs tail /ecs/health-monitor-scheduler --follow
```

**Worker logs:**
```bash
aws logs tail /ecs/health-monitor-worker --follow
```

### Check Service Status

```bash
aws ecs describe-services \
  --cluster health-monitor-cluster \
  --services health-monitor-api health-monitor-scheduler health-monitor-worker
```

### View Database Monitors

```bash
curl $API_URL/monitors | jq
```

---

## 🔧 Configuration

### Environment Variables

The system uses these environment variables (set automatically by Terraform):

- `DATABASE_URL`: PostgreSQL connection string
- `SQS_QUEUE_URL`: AWS SQS queue URL
- `AWS_REGION`: AWS region (default: us-east-1)
- `FAIL_THRESHOLD`: Consecutive failures before DOWN alert (default: 2)
- `SUCCESS_THRESHOLD`: Consecutive successes before UP alert (default: 2)

### Autoscaling

Worker service automatically scales based on SQS queue depth:
- **Target**: 10 messages per worker
- **Min workers**: 1
- **Max workers**: 5
- **Scale out**: When queue depth > 10 messages
- **Scale in**: When queue depth < 10 messages

---

## 🔄 Updating the Application

### Update Code and Redeploy

```bash
# 1. Make changes to src/*.py files

# 2. Rebuild and push images (example for API)
cd /path/to/health-monitor
docker build -f docker/Dockerfile.api -t hm-api .
docker tag hm-api:latest $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-api:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/health-monitor-api:latest

# 3. Force ECS to deploy new image
aws ecs update-service \
  --cluster health-monitor-cluster \
  --service health-monitor-api \
  --force-new-deployment
```

### Update Infrastructure

```bash
# 1. Make changes to terraform/*.tf files

# 2. Preview changes
cd terraform
terraform plan

# 3. Apply changes
terraform apply
```

---

## 🛑 Cleanup (Destroy All Resources)

**⚠️ WARNING**: This will delete all resources and data!

```bash
cd terraform
terraform destroy
```

Type `yes` when prompted.

---

## 🐛 Troubleshooting

### API not responding
- Check ECS service is running: `aws ecs describe-services --cluster health-monitor-cluster --services health-monitor-api`
- Check logs: `aws logs tail /ecs/health-monitor-api --follow`
- Verify security groups allow ALB → ECS traffic

### No Slack notifications
- Verify webhook URL is correct
- Check worker logs for webhook errors: `aws logs tail /ecs/health-monitor-worker --follow`
- Test webhook manually: `curl -X POST YOUR_WEBHOOK_URL -d '{"text":"Test message"}'`

### Database connection errors
- Verify RDS is running: `aws rds describe-db-instances --db-instance-identifier health-monitor-db`
- Check security groups allow ECS → RDS traffic
- Verify DATABASE_URL is correct in task definitions

### Workers not processing messages
- Check SQS queue has messages: `aws sqs get-queue-attributes --queue-url YOUR_QUEUE_URL`
- Verify worker service is running: `aws ecs describe-services --cluster health-monitor-cluster --services health-monitor-worker`
- Check IAM permissions for SQS access

### Docker build fails
- Ensure you're in the project root directory
- Check that `requirements.txt` exists
- Verify all source files are in `src/` directory
- Check Dockerfile paths reference correct source location

---

## 📚 API Reference

### Create Monitor
```bash
POST /monitors
Content-Type: application/json

{
  "name": "Service Name",
  "url": "https://example.com",
  "check_interval": 60,          # seconds
  "timeout": 5,                  # seconds
  "expected_status_code": 200,
  "webhook_url": "https://hooks.slack.com/...",
  "enabled": true
}
```

### List Monitors
```bash
GET /monitors
```

### Get Monitor
```bash
GET /monitors/{monitor_id}
```

### Update Monitor
```bash
PUT /monitors/{monitor_id}
Content-Type: application/json

{
  "name": "Updated Name",
  "url": "https://example.com",
  "check_interval": 120,
  "timeout": 10,
  "expected_status_code": 200,
  "webhook_url": "https://hooks.slack.com/...",
  "enabled": false
}
```

### Delete Monitor
```bash
DELETE /monitors/{monitor_id}
```

---

## 💰 Cost Estimation

**Approximate monthly costs** (us-east-1):
- RDS db.t3.micro: ~$15
- ECS Fargate tasks (4 tasks, 0.25 vCPU, 0.5 GB): ~$30
- Application Load Balancer: ~$20
- SQS (1M requests): ~$0.50
- Data transfer: ~$5

**Total**: ~$70/month

> 💡 **Tip**: Enable AWS Cost Explorer and set up billing alerts

---

## 🔒 Security Best Practices

1. **Never commit** `terraform.tfvars` or `outputs.json` to version control
2. **Rotate** database passwords regularly
3. **Use** AWS Secrets Manager for production deployments
4. **Enable** RDS encryption (already enabled in this config)
5. **Restrict** security group rules to minimum required access
6. **Review** IAM permissions regularly
7. **Enable** CloudTrail for audit logging

---

## 📝 Notes

- Database backups are retained for 7 days
- CloudWatch logs are retained for 7 days
- The system uses consecutive failure/success thresholds to prevent alert fatigue from transient failures
- SQS messages are retained for 24 hours if not processed
- All Python source files are in `src/` directory
- All Dockerfiles are in `docker/` directory
- Documentation files are in `documents/` directory

---

## 🤝 Support

For issues or questions:
1. Check CloudWatch logs for errors
2. Verify all services are running in ECS
3. Review this documentation's troubleshooting section
4. Check AWS Service Health Dashboard for regional issues

---

**Last Updated**: January 2026  
**Version**: 1.0