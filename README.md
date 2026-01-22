[health_monitor_doc .md](https://github.com/user-attachments/files/24784948/health_monitor_doc.md)
# Health Monitor System Documentation

## Table of Contents

1. [System Overview](#system-overview)
2. [Component Documentation](#component-documentation)
3. [System Workflow](#system-workflow)
4. [Architecture Diagram](#architecture-diagram)
5. [Technology Choices](#technology-choices)
6. [Reliability & Failure Handling](#reliability--failure-handling)
7. [Performance & Capacity](#performance--capacity)
8. [Scaling Strategy](#scaling-strategy)
9. [Security Posture](#security-posture)

---

## System Overview

A distributed API monitoring platform that continuously checks if your endpoints are alive and alerts you via Slack when they go down or recover. Built on AWS using a queue-based architecture that decouples job scheduling from execution, enabling independent scaling and fault tolerance.

**The Problem:** Teams discover their APIs are down when customers complain, not when issues occur.

**The Solution:** Automated monitoring that checks URLs every 30-3600 seconds, tracks consecutive failures to avoid false alarms, and sends Slack notifications only when status actually changes (UP ↔ DOWN).

---

## Component Documentation

### API Service

FastAPI-based REST interface for monitor management. Users create monitors by specifying target URL, check interval, timeout, expected HTTP status code, and Slack webhook URL. Validates all inputs, stores configurations in PostgreSQL, and exposes health check endpoint for load balancer integration.

### Database Initializer

Standalone Python script that creates the monitors table schema in PostgreSQL. Runs once as a manual ECS Fargate task before deployment, establishing database structure with fields for monitor configuration, operational state, and timestamps.

### Scheduler Service

Queries PostgreSQL every 30 seconds to find enabled monitors and pushes check requests to SQS queue. Creates JSON messages containing monitor ID, target URL, timeout, expected status code, and webhook URL. Distributes work without performing actual health checks.

### Worker Service

Polls SQS queue for health check messages and executes HTTP validations. Retrieves up to 5 messages per poll, performs HTTP GET requests, compares responses against expected status codes, and updates database. Implements threshold-based alerting: 2 consecutive failures trigger DOWN status, 2 consecutive successes trigger UP status. Sends Slack notifications only on status changes.

---

## System Workflow

**How a monitor works from creation to alert:**

<img width="791" height="561" alt="Untitled Diagram drawio (9)" src="https://github.com/user-attachments/assets/3cd4e7be-28d4-4c68-bc57-0adc61757e77" />


1. **User creates monitor** → API validates input → Saves to PostgreSQL database

2. **Scheduler runs every 30 seconds** → Reads all enabled monitors from database → Creates message for each monitor → Sends messages to SQS queue

3. **Workers continuously poll SQS** → Get up to 5 messages at a time → For each message: make HTTP request to target URL → Check if response matches expected status code

4. **Worker updates database:**
   - If check succeeds: increment success counter, reset failure counter
   - If check fails: increment failure counter, reset success counter
   
5. **Status changes happen with thresholds:**
   - 2 consecutive failures → Status changes to DOWN → Worker sends "Service is down" alert to Slack
   - 2 consecutive successes → Status changes to UP → Worker sends "Service recovered" alert to Slack

6. **Auto-scaling responds to load:**
   - Queue has more than 10 messages → ECS adds more workers (up to 5 total)
   - Queue drains below threshold → ECS removes workers (down to 1 minimum)

**Key insight:** The threshold logic (requiring 2 consecutive results) prevents false alarms from temporary network issues while only adding ~1 minute delay to real alerts.

---

## Architecture Diagram


![ChatGPT Image Jan 19, 2026, 08_28_33 PM](https://github.com/user-attachments/assets/35d3f645-20c0-45b6-a76f-32f0e8b48259)

**High-Level Flow:**
```
Users → ALB (Port 80) → API Service (Port 8000) ↔ PostgreSQL

Scheduler → PostgreSQL (reads monitors) → SQS Queue

Workers (1-5 tasks) ↔ SQS Queue ↔ PostgreSQL
Workers → Target URLs (HTTP checks) → Slack Webhooks (alerts)
```

**Network Layout:**
- VPC: 10.0.0.0/16 across 2 availability zones
- Public Subnets: ALB + ECS tasks (API, Scheduler, Workers)
- Private Subnets: RDS PostgreSQL (no internet access)
- Security Groups: ALB → ECS:8000, ECS → RDS:5432

**Auto-Scaling:**
- Workers scale 1→5 based on queue depth (target: 10 messages)

---

## Technology Choices

### Why PostgreSQL RDS?

**Managed service benefits:** Automated backups, patching, and Multi-AZ failover mean zero time spent on database maintenance. Self-hosted PostgreSQL would need manual backups and complex HA setup.

**Cost and simplicity:** PostgreSQL costs $15/month at this scale versus $25/month for DynamoDB. We can use simple SQL queries instead of learning DynamoDB patterns, and ACID transactions ensure data consistency.

### Why ECS Fargate with Docker?

**Portability and consistency:** Docker ensures code runs identically in dev and production. We can test the entire stack locally with docker-compose before deploying to AWS.

**Operational simplicity:** Fargate manages servers for us - no patching, no SSH keys, no capacity planning. Workers need long-running SQS connections which Lambda's cold starts would break.

### Why SQS Message Queue?

**Decoupling for reliability:** Scheduler and workers can fail, restart, or scale independently. If we get 100 new monitors added suddenly, the queue buffers the load while workers auto-scale.

**Fully managed and cheap:** No servers to maintain, costs $2/month at our scale versus $12/month for Redis. Built-in visibility timeout means messages automatically retry if a worker crashes.

### Why db.t3.micro for RDS?

**Right-sized capacity:** We're doing 1.6 database operations per second with 50 monitors. A t3.micro handles 10,000+ ops/second, so we're using 0.01% of capacity.

**Cost efficiency:** Paying for t3.small would double costs for capacity we won't use until hitting 200+ monitors. Upgrade when database CPU exceeds 50% or queries take more than 50ms.

---

## Reliability & Failure Handling

### Database Failures

**What happens:** RDS becomes unavailable, all services lose database connection.

**How we handle it:** RDS Multi-AZ provides automatic failover in 1-2 minutes. Services have connection retry logic with exponential backoff. Workers continue processing in-flight checks, they just can't update status until database returns.

### Worker Crashes

**What happens:** Worker process crashes while checking a URL.

**How we handle it:** SQS visibility timeout (5 minutes) automatically returns the message to queue. Another worker picks it up within 10 seconds. No data loss because we only delete messages after successful processing.

### Scheduler Crashes

**What happens:** Scheduler task dies, no new messages added to queue.

**How we handle it:** ECS automatically restarts the task in ~30 seconds. Scheduler is stateless so it restarts cleanly. Workers process existing queue then idle until scheduler recovers.

### Queue Overload

**What happens:** 200 monitors added suddenly, queue fills with messages.

**How we handle it:** Auto-scaling kicks in at 10 messages, workers scale from 1 to 5 in 1-2 minutes. Queue has 24-hour retention, plenty of time to process backlog. No checks are lost.

### Network Issues

**What happens:** Worker can't reach target URL due to network partition.

**How we handle it:** Request times out based on configured limit. Threshold logic requires 2 consecutive failures before alerting, so temporary network blips don't trigger false alarms.

---

## Performance & Capacity

**Current Capacity:**
- Single worker: 1,800 checks per hour
- 5 workers maximum: 9,000 checks per hour
- Supports 150 monitors at 60-second intervals
- Realistic capacity: 50-100 monitors with headroom

**Latency:**
- Monitor creation to first check: ~20 seconds average
- Check execution time: 2-20 seconds per check
- Alert delivery: 2-15 seconds from status change to Slack

**Bottlenecks:**
- Current: Worker processing speed (HTTP requests take time)
- Database and SQS have 100x headroom at current scale
- Next bottleneck at 200+ monitors: database connections

---

## Scaling Strategy

### Vertical Scaling (Current to 200 monitors)

Upgrade database from db.t3.micro to t3.small when CPU exceeds 50%. Increase worker CPU allocation from 256 to 512 units if worker CPU stays above 80%. Add second API task if latency exceeds 500ms.

### Horizontal Scaling (200 to 1,000 monitors)

Add database read replicas for API queries to reduce primary database load. Run multiple scheduler instances, each handling a subset of monitors by ID range. Deploy to multiple AWS regions for global coverage.

### Architectural Changes (1,000+ monitors)

Replace scheduler with EventBridge scheduled rules (one per monitor) to eliminate polling. Migrate from PostgreSQL to DynamoDB for unlimited horizontal scaling. Add Redis cache layer to reduce database reads by 80%. Consider Lambda for workers to get massive parallelism.

**Pragmatic approach:** Current setup handles 50-200 monitors easily. Don't over-engineer for problems you don't have yet. Scale when you experience actual pain.

---

## Security Posture

### What We're Doing Right

Database in private subnets with zero internet exposure. Security groups enforce minimum required access (ALB can only talk to ECS on port 8000, ECS can only talk to RDS on port 5432). Separate IAM roles for execution (pulling images, writing logs) and task operations (SQS access limited to specific queue). RDS encryption at rest enabled.

### Known Gaps

No HTTPS on load balancer - traffic visible in transit. Database password in environment variables instead of AWS Secrets Manager. No API authentication - anyone can create monitors. ECS tasks have public IPs and direct internet exposure. No rate limiting to prevent abuse.

### Production Fixes

**Immediate :** Enable HTTPS with ACM certificate, move database password to Secrets Manager, add API key authentication.

**Short-term :** Implement rate limiting at 100 requests/minute, enable CloudTrail for audit logs, configure VPC Flow Logs.

**Long-term :** Deploy NAT Gateway and move ECS to private subnets, add WAF rules for attack protection, implement IP allowlisting.

**Trade-off:** Current setup works for internal tools and demos. Production systems need HTTPS and secrets management immediately. Other security features can wait for product traction.
