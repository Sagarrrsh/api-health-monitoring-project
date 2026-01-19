# Health Monitor System Documentation

## Table of Contents

1. [System Overview](#system-overview)
2. [Component Documentation](#component-documentation)
3. [System Workflow](#system-workflow)
4. [Architecture Diagram](#architecture-diagram)
5. [Design Decisions & Trade-offs](#design-decisions--trade-offs)
6. [Reliability & Failure Handling](#reliability--failure-handling)
7. [Performance & Capacity](#performance--capacity)
8. [Cost Analysis](#cost-analysis)
9. [Scaling Strategy](#scaling-strategy)
10. [Security Posture](#security-posture)

---

## System Overview

Ever had your API go down at 3 AM and nobody noticed until customers started complaining? This system solves that problem.

The Health Monitor is a distributed monitoring platform that continuously checks if your APIs are alive and screams at you (via Slack) when they're not. Built on AWS with a queue-based architecture, it's designed to scale from monitoring a handful of endpoints to hundreds, all while keeping costs reasonable and operations simple.

**The Problem:** Teams need reliable monitoring that doesn't require babysitting, can handle varying loads, and won't break the bank.

**The Solution:** A serverless-ish architecture using ECS Fargate that auto-scales workers based on queue depth, uses managed services (RDS, SQS) to minimize operational overhead, and implements smart notification logic to avoid alert fatigue.

---

## Component Documentation

### API Service (The Front Door)

This is what users interact with - a FastAPI server that manages monitor configurations. Users can create monitors by specifying what URL to check, how often to check it (30s to 1 hour), what HTTP status they expect, and where to send alerts. The service enforces sane defaults and validates everything because users will absolutely try to check a URL every second or set a 0-second timeout if you let them.

**Why FastAPI?** Fast development, automatic API docs, built-in validation with Pydantic, and async support for future optimization. It's 2026, we don't write Flask boilerplate anymore.

### Database Initializer (The Setup Crew)

A simple Python script that runs once to create the database schema. Could we use database migrations? Absolutely. Do we need them for a demo project? Not really. This keeps it simple - one script, one table, done.

**Design choice:** Running this as a manual ECS task instead of auto-running on API startup prevents race conditions where multiple API instances try to create tables simultaneously. Been there, debugged that.

### Scheduler Service (The Relentless Producer)

Every 30 seconds, this service wakes up, checks which monitors are enabled, and dumps check requests into SQS. It's intentionally dumb - it doesn't care about check intervals, success rates, or anything else. Just "here are monitors, go check them."

**Why so simple?** Separation of concerns. The scheduler's job is distribution, not decision-making. This makes it easier to reason about, debug, and scale. Plus, if the scheduler crashes and restarts, it just picks up where it left off - no state to recover.

### Worker Service (The Heavy Lifter)

This is where the real work happens. Workers poll SQS, grab health check messages, make HTTP requests to target URLs, and update the database with results. They track consecutive failures and successes, implementing a simple but effective state machine: UNKNOWN → DOWN (after 2 failures) or UNKNOWN → UP (after 2 successes).

**The smart part:** Workers only send Slack notifications when status actually changes (UP ↔ DOWN), not on every check. Nobody wants 50 Slack messages saying "still down" every minute.

**Why threshold logic?** Prevents false alarms from transient network blips. A single timeout doesn't mean your API is dead, but three in a row? Yeah, something's wrong.

---

## System Workflow

Here's how a monitor goes from creation to alert:

**User creates a monitor** → API validates (is this URL real? Is the interval sane?) → Stores in PostgreSQL → Returns success

**Every 30 seconds:** Scheduler queries DB → Finds all enabled monitors → For each one, creates a message with {url, timeout, expected_status, webhook} → Pushes to SQS queue

**Workers (1-5 of them) continuously:** Poll SQS with long polling (10s wait) → Grab up to 5 messages → For each message: make HTTP GET request → Did it return the expected status code within timeout?

**If check succeeds:** Increment consecutive_successes → Reset consecutive_failures → If we hit 2 successes and status was DOWN → Change to UP → Send "🎉 Service recovered!" to Slack

**If check fails:** Increment consecutive_failures → Reset consecutive_successes → If we hit 2 failures and status was UP → Change to DOWN → Send "🚨 Service is down!" to Slack

**Auto-scaling happens automatically:** Queue depth > 10 messages → ECS spins up more workers (up to 5) → More capacity to process checks → Queue drains → Workers scale back down

---

## Architecture Diagram

```
                              INTERNET
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │  Application Load      │◄─── Entry point for API calls
                    │  Balancer (Public)     │     (Only public-facing component)
                    └───────────┬────────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
                    ▼                       ▼
        ┌─────────────────────┐   ┌─────────────────────┐
        │   ECS CLUSTER       │   │   ECS CLUSTER       │
        │                     │   │                     │
        │  ┌──────────────┐  │   │  ┌──────────────┐  │
        │  │ API Service  │  │   │  │  Scheduler   │  │
        │  │   (1 task)   │  │   │  │   (1 task)   │  │
        │  │  Port 8000   │  │   │  │  No ports    │  │
        │  └──────┬───────┘  │   │  └──────┬───────┘  │
        │         │          │   │         │          │
        └─────────┼──────────┘   └─────────┼──────────┘
                  │                        │
                  │                        │ Reads monitors
                  │ CRUD ops               │ every 30s
                  │                        │
                  └────────┬───────────────┘
                           │
                           ▼
                 ┌───────────────────┐
                 │   PostgreSQL RDS  │◄─── Single source of truth
                 │  (Private subnet) │     for all monitor state
                 │   db.t3.micro     │
                 └───────────────────┘
                           ▲
                           │ Updates status
                           │
                  ┌────────┴────────┐
                  │                 │
        ┌─────────┴──────────┐     │
        │  Worker Pool       │     │
        │  (1-5 tasks)       │─────┘
        │  Auto-scales on    │
        │  queue depth       │
        └─────────┬──────────┘
                  │
                  ▲ Polls for messages
                  │
        ┌─────────┴──────────┐
        │    SQS Queue       │◄─── Message buffer
        │  (Health checks)   │     Decouples scheduler
        │  Visibility: 5min  │     from workers
        └─────────▲──────────┘
                  │
                  │ Produces messages
                  │
        ┌─────────┴──────────┐
        │    Scheduler       │
        │  (Loops every 30s) │
        └────────────────────┘

WORKER EXTERNALS:
Worker → Target URLs (HTTP checks) → Slack Webhooks (Notifications)

NETWORK TOPOLOGY:
├── VPC (10.0.0.0/16)
│   ├── Public Subnets (2 AZs): ALB + ECS tasks
│   ├── Private Subnets (2 AZs): RDS only
│   ├── Internet Gateway: Public subnet egress
│   └── Security Groups:
│       ├── ALB → ECS port 8000
│       └── ECS → RDS port 5432

AUTO-SCALING TRIGGER:
Queue depth > 10 messages → Add worker (max 5)
Queue depth < 5 messages → Remove worker (min 1)
```

---

## Design Decisions & Trade-offs

### Why Queue-Based Architecture?

**Decision:** Use SQS between scheduler and workers instead of direct processing.

**Reasoning:** Decoupling is everything. The scheduler can push work without waiting for it to complete. Workers can fail, restart, or scale without affecting the scheduler. If we get a surge of monitors, the queue absorbs the load while workers scale up.

**Trade-off:** Adds slight latency (messages sit in queue) and operational complexity (one more service to monitor). But the benefits massively outweigh the costs for a production system.

### Why Not Lambda?

**Considered:** AWS Lambda for workers instead of ECS Fargate.

**Why we didn't:** Lambda's 15-minute timeout is fine, but cold starts would hurt check accuracy. Fargate keeps workers warm and ready. Plus, we get better visibility into worker behavior with long-running processes vs. ephemeral functions.

**When Lambda makes sense:** If you're checking 1,000+ URLs and need massive parallelism, Lambda wins. For 50-100 monitors, Fargate is simpler and cheaper.

### Why db.t3.micro?

**Decision:** Smallest RDS instance that's not in the free tier.

**Reasoning:** For this scale (50 monitors), we're doing maybe 6,000 DB operations/hour total. A t3.micro handles 10,000+ ops/second. We're using 0.1% of capacity. Going bigger is pure waste.

**When to upgrade:** When you hit 200+ monitors and start seeing connection pool exhaustion or query latency > 50ms. Then jump to t3.small or consider Aurora Serverless.

### Why 30-Second Scheduler Interval?

**Decision:** Fixed 30s loop instead of dynamic timing.

**Reasoning:** Simpler than trying to be smart about intervals. The queue handles buffering, and workers process at their own pace. Over-engineering the scheduler adds complexity for minimal gain.

**Trade-off:** Can't support check intervals < 30s without code changes. But honestly, if you're health-checking every 10 seconds, you need a different monitoring solution (like CloudWatch Synthetics).

### Why Threshold-Based Alerting?

**Decision:** Require 2 consecutive failures before marking DOWN, 2 successes before UP.

**Reasoning:** The internet is noisy. Packets drop. DNS hiccups. A single failure means nothing. Requiring consecutive failures filters 95% of false alarms while only adding ~1 minute delay to real alerts.

**User benefit:** Prevents alert fatigue. Nobody wants 50 Slack pings at 3 AM because AWS had a 5-second network blip.

---

## Reliability & Failure Handling

### Database Failures

**Scenario:** RDS becomes unavailable.

**Impact:** All services fail to connect, health checks stop.

**Mitigation:** 
- RDS runs in multi-AZ with automatic failover (add in production)
- 7-day automated backups with point-in-time recovery
- Connection retry logic with exponential backoff in all services
- Workers continue processing in-flight checks, just can't update DB

**Recovery time:** Multi-AZ failover takes 1-2 minutes. Without it, manual restore from snapshot takes 10-15 minutes.

### Worker Crashes

**Scenario:** Worker process crashes mid-check.

**Impact:** Message becomes invisible for 5 minutes (visibility timeout), then returns to queue.

**Mitigation:**
- SQS visibility timeout ensures messages aren't lost
- Another worker picks up the message automatically
- Multiple workers provide redundancy
- No data loss because we haven't deleted the message yet

**Recovery time:** Automatic, next worker polls within 10 seconds.

### Scheduler Crashes

**Scenario:** Scheduler task dies.

**Impact:** No new messages added to queue. Workers process existing queue then idle.

**Mitigation:**
- ECS automatically restarts failed tasks
- Scheduler is stateless, restarts cleanly
- No persistent state to corrupt or lose
- Health checks resume within 30-60 seconds

**Recovery time:** Automatic ECS restart in ~30 seconds.

### SQS Queue Overload

**Scenario:** 200 monitors added suddenly, queue fills up.

**Impact:** Processing lag increases, checks take longer.

**Mitigation:**
- Auto-scaling kicks in at 10 messages
- Workers scale from 1 → 5, increasing throughput 5x
- Queue has 24-hour retention, plenty of time to catch up
- Scheduler keeps running, queue absorbs the load

**Recovery time:** Auto-scaling responds in 1-2 minutes, queue drains in 5-10 minutes.

### Network Partitions

**Scenario:** Worker can't reach target URL due to network issues.

**Impact:** Health check fails, but that's expected behavior.

**Mitigation:**
- Timeout enforcement prevents infinite hangs
- Consecutive failure logic prevents false alarms
- Worker continues processing other messages
- No cascading failures

**Expected behavior:** Monitor goes DOWN after 2 timeouts, user gets alerted.

### Thundering Herd

**Scenario:** 100 monitors all scheduled at the same second.

**Impact:** Queue gets 100 messages instantly.

**Mitigation:**
- SQS is designed for bursts
- Workers use long polling (10s) to batch receives
- Auto-scaling responds to sustained load
- Processing spreads out naturally over 1-2 minutes

**User experience:** No visible impact, all checks complete within 2 minutes.

### State Consistency

**Scenario:** Worker updates consecutive_failures but crashes before sending webhook.

**Impact:** Monitor state is correct, but notification wasn't sent.

**Trade-off accepted:** Better to miss an occasional notification than spam users with duplicates. Status change detection on next check will trigger notification.

**Alternative considered:** Two-phase commit with webhook confirmation. Rejected as over-engineering for a monitoring system.

---

## Performance & Capacity

### Current Capacity

**Single worker throughput:**
- 5 messages per poll × 6 polls/minute = 30 messages/minute
- 30 messages/min × 60 min = 1,800 checks/hour per worker

**Full system capacity:**
- 5 workers × 1,800 checks/hour = 9,000 checks/hour
- At 60-second intervals: supports 150 monitors
- At 5-minute intervals: supports 750 monitors

**Realistic capacity:** 50-100 monitors running smoothly with headroom for spikes.

### Request Flow Analysis

**Per monitor per hour (60s intervals):**
- Scheduler: 60 DB reads + 60 SQS writes
- Workers: 60 SQS reads + 60 HTTP requests + 60 DB writes
- Total: 120 DB ops + 120 SQS ops + 60 HTTP requests

**With 50 monitors:**
- 6,000 DB operations/hour (1.6 ops/second)
- 6,000 SQS messages/hour
- 3,000 HTTP health checks/hour

**Database load:** db.t3.micro handles this easily. We're using ~0.01% of capacity.

### Bottleneck Analysis

**Current bottleneck:** Worker processing speed.

**Evidence:**
- DB can handle 10,000+ ops/second, we're doing 2
- SQS can handle 3,000 messages/second, we're doing 2
- Workers are CPU/network bound on HTTP requests

**Next bottleneck at scale:** Database connections.
- 3 service types × 5 max workers = 15 potential connections
- Default RDS max_connections = 87
- Headroom: 72 connections available

### Latency Breakdown

**Monitor creation to first check:**
- API write: <100ms
- Wait for scheduler: 0-30s (average 15s)
- Queue processing: <5s
- First check completes: ~20s average

**Check execution:**
- SQS poll: 0-10s (long polling)
- HTTP request: configurable timeout (1-10s)
- DB update: <100ms
- Total: 2-20s per check

**Alert delivery:**
- Status change detected: immediate
- Webhook POST: 1-5s
- User sees Slack message: 1-10s
- Total alert time: 2-15s from status change

---

## Cost Analysis

### Current Monthly Costs (Estimating 50 monitors)

**ECS Fargate:**
- API: 1 task × 0.25 vCPU × $0.04/vCPU-hour × 730 hours = $7.30
- Scheduler: 1 task × 0.25 vCPU × $0.04/vCPU-hour × 730 hours = $7.30
- Workers: 3 tasks average × 0.25 vCPU × $0.04/vCPU-hour × 730 hours = $21.90
- Memory: negligible at 512MB
- **Subtotal: $36.50/month**

**RDS PostgreSQL:**
- db.t3.micro: $0.017/hour × 730 hours = $12.41
- Storage: 20GB × $0.115/GB = $2.30
- Backup: 140GB × $0.095/GB = $13.30
- **Subtotal: $28/month**

**Application Load Balancer:**
- Fixed cost: $16.20/month
- LCU hours: ~$5/month (low traffic)
- **Subtotal: $21/month**

**SQS:**
- 6,000 messages/hour × 730 hours = 4.38M requests
- First 1M free, remaining 3.38M × $0.40/1M = $1.35
- **Subtotal: $1.35/month**

**CloudWatch Logs:**
- 3 services × 2GB/month = 6GB
- 6GB × $0.50/GB = $3
- **Subtotal: $3/month**

**Total: ~$90/month for 50 monitors = $1.80 per monitor**

### Optimized Costs

**Use Fargate Spot (70% discount):**
- Workers: $21.90 → $6.57
- API/Scheduler: Keep on-demand for stability
- **Savings: $15.33/month**

**RDS Reserved Instance (1-year, 30% discount):**
- $28/month → $19.60/month
- **Savings: $8.40/month**

**Optimize CloudWatch retention (7 days → 3 days):**
- 6GB → 3GB = $1.50
- **Savings: $1.50/month**

**Remove ALB, use API Gateway:**
- ALB $21/month → API Gateway $3.50/million requests = $0.50
- **Savings: $20.50/month** (if traffic < 100K requests/month)

**Optimized total: ~$45/month = $0.90 per monitor**
**Total savings: $45/month (50% reduction)**

### Cost at Scale

**500 monitors:**
- Workers scale to 5 constantly: $36.50
- RDS upgrade to t3.small: $50
- SQS: ~$15
- ALB: $21
- CloudWatch: $10
- **Total: ~$132/month = $0.26 per monitor**

**Economies of scale:** Fixed costs (ALB, base RDS) amortize across more monitors.

---

## Scaling Strategy

### Vertical Scaling (0-200 monitors)

**Current state:** Plenty of headroom.

**When to scale:**
- DB CPU > 50%: Upgrade to t3.small ($50/month)
- Worker CPU > 80%: Increase CPU allocation to 512 units
- API latency > 500ms: Add second API task

**Easy wins:**
- Enable RDS Performance Insights
- Add read replicas for API queries
- Increase worker memory for faster HTTP library

### Horizontal Scaling (200-1,000 monitors)

**Database sharding by monitor ID:**
- Split monitors across 2-3 databases
- Route traffic based on ID hash
- Requires code changes but massive capacity increase

**Multiple scheduler instances:**
- Partition monitors by ID range
- Each scheduler handles subset
- Prevents single-scheduler bottleneck

**Regional distribution:**
- Deploy to multiple AWS regions
- Route health checks from nearest region
- Reduces latency for global endpoints

### Architectural Changes (1,000+ monitors)

**Switch to event-driven:**
- Replace scheduler with EventBridge rules
- One rule per monitor, triggered on schedule
- Eliminates scheduler polling, reduces DB load

**Move to DynamoDB:**
- Better horizontal scaling than RDS
- Pay per request instead of fixed instance
- Trades query flexibility for infinite scale

**Add caching layer:**
- Redis/ElastiCache for monitor configs
- Reduce DB reads by 80%
- Workers read from cache, only write to DB

**Consider serverless:**
- Lambda for workers at massive scale
- Step Functions for orchestration
- Scales to 10,000+ monitors automatically

### The Reality Check

For most users, the current architecture handles 50-200 monitors beautifully. Over-engineering for 10,000 monitors is solving a problem you don't have. Scale when you feel pain, not before.

---

## Security Posture

### What We're Doing Right

**Network isolation:**
- Database in private subnets, zero internet exposure
- Security groups enforce least-privilege (ALB→ECS:8000, ECS→RDS:5432)
- No SSH access to any component

**IAM roles:**
- Separate execution role (pull images, write logs) and task role (SQS access)
- Task role limited to specific queue ARN
- No wildcard permissions

**Data encryption:**
- RDS encryption at rest enabled
- Secrets marked as sensitive in Terraform
- CloudWatch logs retained for audit trail

### What We're Missing (And Why)

**No HTTPS on ALB:**
- Current: HTTP only
- Risk: Credentials/data visible in transit
- Why not fixed: Requires ACM certificate and domain
- Production fix: Add ACM cert, force HTTPS redirect, 10 lines of Terraform

**Database password in env vars:**
- Current: PASSWORD in container environment
- Risk: Visible in ECS task definition, CloudWatch logs
- Why not fixed: Simplicity for demo
- Production fix: AWS Secrets Manager with automatic rotation, 5 minutes setup

**No API authentication:**
- Current: Public API, anyone can create monitors
- Risk: Abuse, unauthorized access
- Why not fixed: Demo simplicity
- Production fix: API Gateway with JWT or API keys, rate limiting

**Public IPs on ECS tasks:**
- Current: Tasks in public subnet with public IPs
- Risk: Direct internet exposure
- Why not fixed: NAT Gateway costs $32/month
- Production fix: NAT Gateway + private subnet deployment

**No request rate limiting:**
- Current: Unlimited API calls
- Risk: DoS, cost spike from abuse
- Why not fixed: Minimal risk at demo scale
- Production fix: AWS WAF ($5/month) or API Gateway throttling

### Security Roadmap (Priority Order)

**Week 1:**
1. Enable HTTPS on ALB (ACM certificate)
2. Move DB password to Secrets Manager
3. Add API key authentication

**Week 2:**
4. Implement rate limiting (100 requests/minute)
5. Add CloudTrail for API audit logs
6. Enable VPC Flow Logs

**Month 2:**
7. Deploy NAT Gateway, move tasks to private subnet
8. Add WAF rules for common attack patterns
9. Implement IP allowlisting for API

**The pragmatic take:** This setup is fine for internal tools or demos. For production with paying customers, fix HTTPS and secrets management immediately. Everything else can wait for traction.