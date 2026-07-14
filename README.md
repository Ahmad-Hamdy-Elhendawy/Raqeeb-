# 🚀 Raqib - AI-Powered Website Monitoring Platform

> **Serverless AWS website uptime monitoring with intelligent business alerts and AI-powered recovery recommendations.**

![AWS](https://img.shields.io/badge/AWS-Serverless-orange)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![Lambda](https://img.shields.io/badge/AWS-Lambda-yellow)
![DynamoDB](https://img.shields.io/badge/DynamoDB-NoSQL-blue)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 📖 Overview

**Raqib** is a serverless website monitoring platform built on AWS that continuously monitors websites every five minutes, detects outages, measures performance, estimates business impact, and sends intelligent Telegram notifications.

Unlike traditional uptime monitoring tools, Raqib distinguishes between **business owners** and **developers**, sending each audience the information they actually need.

Business owners receive simple outage notifications and AI-generated recovery advice in Egyptian Arabic, while developers receive detailed technical diagnostics including latency, SSL status, DNS timing, redirect information, HTTP status codes, and individual ping results.

---

## ✨ Features

- 🌐 Website uptime monitoring
- ⏱ Automatic monitoring every 5 minutes
- 📊 Average / Min / Max latency measurement
- 🔍 DNS latency measurement
- 🔒 SSL certificate validation
- ↪ Redirect detection
- 📈 24-hour uptime statistics
- 💰 Financial impact estimation based on advertising spend
- 🤖 AI-generated business recovery recommendations
- 📱 Telegram notifications
- 👨‍💻 Separate owner & developer alerts
- ☁ Fully serverless AWS architecture

---

# 🏗 Architecture

![Architecture](https://github.com/Ahmad-Hamdy-Elhendawy/Raqeeb-/blob/main/architecture.png?raw=true)

---

# ⚙ System Workflow

```text
User
 │
 ▼
API Gateway
 │
 ▼
Register Website Lambda
 │
 ▼
DynamoDB (Websites)

──────────────────────────────────────────────

Every 5 Minutes

EventBridge Schedule

 │

 ▼

Ping Lambda

 ├── DNS Check

 ├── SSL Validation

 ├── HTTP Ping ×3

 ├── Performance Analysis

 ├── Financial Impact

 ├── Store Results

 └── Detect Status Change

 │

 ▼

Alert Lambda

 ├── Fetch Website Metadata

 ├── Analyze Last 10 Pings

 ├── Build Notifications

 ├── Gemini AI (Owner Recovery Only)

 └── Telegram Bot API

 │

 ▼

Owner & Developer
```

---

# ☁ AWS Services

| Service | Purpose |
|----------|----------|
| API Gateway | Register websites |
| Lambda | Serverless compute |
| EventBridge | Schedule monitoring |
| DynamoDB | Website metadata & ping history |
| CloudWatch Logs | Logging |
| IAM | Permissions |

---

# 🤖 AI Usage

Gemini AI is **not** responsible for monitoring websites.

AI is only used after a website recovers from downtime.

It generates:

- Business recovery recommendations
- Customer communication advice
- Preventive actions
- Financial damage mitigation tips

Language:

- Egyptian Arabic

Fallback:

If Gemini is unavailable, Raqib automatically sends built-in recovery recommendations.

---

# 📊 Metrics Collected

For every monitoring cycle Raqib stores:

- Website status
- HTTP status code
- Response time
- DNS latency
- SSL validity
- SSL expiration
- Redirect information
- Response size
- Error summary
- Money lost
- Uptime percentage
- Downtime duration

---

# 📱 Notification Types

## 🔴 Website Down → Developer

Contains

- HTTP Status
- SSL Details
- Ping Results
- DNS Timing
- Latency
- Redirects
- Error Summary

---

## 🔴 Website Down → Owner

Contains

- Website URL
- Time
- Estimated advertising loss
- Developer notification confirmation

---

## ✅ Website Recovered → Owner

Contains

- Downtime summary
- Money lost
- Current performance
- SSL health
- AI-generated business advice

---

# 📂 Repository Structure

```text
.
├── src/
│
├── register_lambda/
│
├── ping_lambda/
│
├── alert_lambda/
│
├── architecture/
│
├── docs/
│
├── README.md
├── ARCHITECTURE.md
├── DEPLOYMENT.md
├── API.md
├── CHANGELOG.md
└── LICENSE
```

---

# 🚀 Deployment

See

```
DEPLOYMENT.md
```

---

# 🔮 Future Improvements

- Email notifications
- Slack integration
- SMS alerts
- Public status pages
- Multi-region monitoring
- Browser synthetic monitoring
- Predictive outage detection
- React dashboard
- Historical analytics

---

# 🛡 License

MIT License

---

# 👥 Team

Built during a hackathon using:

- AWS Lambda
- Amazon DynamoDB
- Amazon EventBridge
- Amazon API Gateway
- Telegram Bot API
- Google Gemini
- Python

---

# ⭐ Why Raqib?

Traditional uptime monitors answer:

> "Is my website down?"

Raqib answers:

> "How much money am I losing, who should know, and what should I do next?"

That makes it valuable for both business owners and developers.
