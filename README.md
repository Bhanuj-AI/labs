# BHANUJ Labs & Developer Guides

Practical, reproducible examples for integrating BHANUJ with agent frameworks, AI platforms, cloud services, and enterprise infrastructure.

This repository contains the source code behind BHANUJ Developer Guides.

The goal is simple:

> Start with a working technology stack, add BHANUJ using supported public contracts, and leave with a verifiable governed system.

## What belongs here

Each guide should demonstrate one concrete integration or architecture pattern.

Examples:

- Govern a LangGraph workflow with BHANUJ
- Govern a Google ADK workflow with BHANUJ
- Connect Snowflake workloads to BHANUJ
- Use Amazon S3 for BHANUJ artifact storage
- Configure enterprise identity
- Build production deployment patterns

Guides are intentionally smaller than real enterprise systems.

They optimize for:

- comprehension
- reproducibility
- explicit integration boundaries
- production-valid contracts

They do not attempt to reproduce full enterprise topology.

## Repository structure

```text
labs/
├── agent-frameworks/
│   ├── langgraph/
│   ├── google-adk/
│   ├── crewai/
│   └── ...
│
├── data-platforms/
│   ├── snowflake/
│   ├── databricks/
│   └── ...
│
├── aws/
│   ├── s3/
│   ├── bedrock/
│   ├── sagemaker/
│   └── ...
│
├── identity/
│   ├── keycloak/
│   ├── entra-id/
│   └── ...
│
└── reference-architectures/