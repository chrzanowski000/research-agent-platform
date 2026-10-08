# Research Agent Platform — Wiki

Welcome to the engineering wiki for **agents-self-reflect**, a multi-agent AI research platform.

---

## Who This Wiki Is For

- **Engineers** onboarding to the codebase
- **Operators** deploying or maintaining the platform
- **Technical stakeholders** who want to understand what the system does and how it runs

---

## Start Here

New to the project? Read in this order:

1. **[Overview](Overview)** — What the platform does and why it exists
2. **[Services](Services)** — Every service explained (ports, deps, roles)
3. **[Manual: Architecture](Manual-Architecture)** — How requests flow through the system
4. **[Demo Plan Unfinished](Demo-Plan-Unfinished)** — Draft plan for a future end-to-end demo

Then pick a deployment guide based on your environment:

- Local Docker → **[Docker Deployment](Manual-Deployment-Docker)**
- Local Kubernetes (Docker Desktop) → **[Kubernetes Deployment](Manual-Deployment-Kubernetes)**
- Production (EKS) → **[Kubernetes Deployment](Manual-Deployment-Kubernetes)** (EKS section)
- Production (GKE) → **[GKE Deployment](Manual-Deployment-GKE)**

---

## All Pages

| Page | Description |
|------|-------------|
| [Overview](Overview) | Platform purpose, capabilities, and tech stack |
| [Services](Services) | Per-service documentation |
| [Demo Plan Unfinished](Demo-Plan-Unfinished) | Draft demo plan and example prompts |
| [Manual: Architecture](Manual-Architecture) | Request flow, agent pipeline, Mermaid diagrams |
| [Manual: Agent Graphs](Manual-Agent-Graphs) | LangGraph node-by-node docs for all three agents |
| [Manual: Deployment — Docker](Manual-Deployment-Docker) | Docker Compose setup and startup |
| [Manual: Deployment — Kubernetes](Manual-Deployment-Kubernetes) | K8s local, Helm, and EKS deployment |
| [Manual: Deployment — GKE](Manual-Deployment-GKE) | Google Kubernetes Engine + Artifact Registry |
| [Manual: Configuration and Secrets](Manual-Configuration-and-Secrets) | All env vars, secret management, 1Password flow |
| [Manual: Operations and Troubleshooting](Manual-Operations-and-Troubleshooting) | Day-2 ops, logs, restarts, common failures |

---

## Repository Layout (Quick Reference)

```
agents-self-reflect/
├── services/
│   ├── chat-ui/           # Next.js 15 frontend
│   ├── langgraph-api/     # Python LangGraph agents
│   └── persistence-api/   # FastAPI research history API
├── infrastructure/
│   ├── k8s/               # Kustomize manifests (base, dev, prod)
│   ├── helm/              # Helm chart
│   └── docker/            # Dockerfiles
├── scripts/               # Build, deploy, secret injection
├── tests/                 # Python unit tests
├── docker-compose.yml     # Legacy Docker Compose (see Docker page)
└── .env_tpl               # Environment variable template (1Password refs)
```
