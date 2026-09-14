# Agentic Insurance Claim Validator

An end-to-end multimodal insurance claim validation solution built using Agentic AI principles with Snowflake, AWS Bedrock, Snowpark, AI_EXTRACT, Streamlit in Snowflake, and LangGraph.

The platform validates vehicle insurance claims by processing:

- Driver’s License
- Insurance Claim Form
- Vehicle Damage Image

The workflow combines structured document extraction, multimodal image analysis, policy verification, and intelligent orchestration to determine whether a claim should be approved or rejected.

# Architecture Overview

![alt text](<Agentic AI Claim Processing Architecture.png>)

The solution is designed around a multi-agent workflow architecture:

- Streamlit in Snowflake provides the frontend experience
- LangGraph orchestrates the workflow and agent execution
- Snowflake AI_EXTRACT performs structured document extraction
- Snowpark executes validation and business logic
- AWS Bedrock Nova Lite performs vehicle image analysis
- Snowflake stores policy and reference data

The workflow mimics how a human insurance validation team processes claims while leveraging AI-native automation.

# Features

- Upload and process:
  - Driver License
  - Claim Document
  - Vehicle Damage Image

- Structured document extraction using Snowflake AI_EXTRACT

- Vehicle image analysis using AWS Bedrock Nova Lite

- Multi-agent orchestration using LangGraph

- Real-time validation against Snowflake policy data

- Intelligent comparison engine for:
  - Name matching
  - DL verification
  - VIN verification
  - Vehicle color validation
  - Policy validity checks

- Interactive Streamlit result dashboard

- Fully serverless cloud-native architecture

# Technology Stack
| Component | Tool / Service |
|---|---|
| Frontend UI | Streamlit in Snowflake |
| Workflow Orchestration | LangGraph |
| Document Extraction | Snowflake AI_EXTRACT |
| Data Processing | Snowpark |
| Image Intelligence | AWS Bedrock Nova Lite |
| Database | Snowflake |
| Storage | Snowflake Internal Stage |
| Programming Language | Python 3.10+ |
| Cloud Platform | AWS + Snowflake |

# Solution Workflow

![alt text](<Agentic AI Claim Processing Architecture - Sequence.png>)

## 1. User Upload
Users upload:

- Driver License
- Insurance Claim Form
- Vehicle Damage Image

through the Streamlit interface.

## 2. File Upload to Snowflake Stage
Uploaded files are stored in Snowflake internal stages:

```text
DL/
CLAIMS/
CAR/
````

## 3. Driver License Extraction

Snowflake AI_EXTRACT extracts:

* Full Name
* Driver License Number
* Date of Birth
* Address

## 4. Claim Form Extraction

Snowflake AI_EXTRACT extracts:

* Customer ID
* Full Name
* Driver License Number
* Incident Date
* VIN
* Vehicle Information
* Incident Description


## 5. Vehicle Image Analysis

Vehicle image is analyzed using AWS Bedrock Nova Lite to identify:

* Vehicle Color
* Damage Description
* Damage Severity


## 6. Policy Validation

Snowpark queries Snowflake policy tables using the extracted customer ID.

Validation includes:

* VIN verification
* Policy expiry validation
* Customer reference checks

## 7. Intelligent Comparison Engine

The workflow compares extracted data across all sources:

| Validation          | Source Comparison                |
| ------------------- | -------------------------------- |
| Name Match          | DL vs Claim                      |
| DL Match            | DL vs Claim                      |
| VIN Match           | Claim vs Policy Database         |
| Vehicle Color Match | Claim vs Car Image               |
| Policy Validity     | Incident Date vs Policy End Date |

## 8. Final Decision

The workflow determines whether the claim is:

* APPROVED
* REJECTED

The final decision and validation breakdown are displayed in the Streamlit UI.

# UI Output Example

## Validation Breakdown

```json
{
  "name": true,
  "dl": true,
  "vin": true,
  "color": true
}
```

## Extracted Driver License Data
```json
{
  "full_name": "James William Carter",
  "dl_number": "12345678",
  "date_of_birth": "14/05/1990",
  "address": "12 Collins Street, Melbourne VIC 3000"
}
```

## Extracted Claim Data

```json
{
  "customer_id": "CUST001",
  "full_name": "James William Carter",
  "dl_number": "12345678",
  "incident_date": "2025-03-10",
  "vin": "KA81JS7515",
  "vehicle": "Toyota Corolla"
}
```

## Car Analysis Output

```json
{
  "color": "White",
  "damage": "Minor collision while parking",
  "severity": "Minor"
}
```

# Repository Structure

```text
.
├── frontend_app.py
├── workflow.py
├── requirements.txt
├── .env
├── README.md
└── assets/
```

# Getting Started

## 1. Clone Repository

```bash
git clone https://github.com/thisisbhanuj/multi-agent-insurance-validator.git
cd multi-agent-insurance-validator
```

## 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure Environment Variables

Create a `.env` file:

```env
SNOWFLAKE_ACCOUNT=your_account
SNOWFLAKE_USER=your_user
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_ROLE=your_role
SNOWFLAKE_WAREHOUSE=your_warehouse
SNOWFLAKE_DATABASE=your_database
SNOWFLAKE_SCHEMA=your_schema
SNOWFLAKE_STAGE=your_stage

AWS_REGION=us-east-1
AWS_MODEL_ID=your_bedrock_model
```

## 5. Run the Application

```bash
streamlit run frontend_app.py
```

# Example Workflow State

```python
class State(TypedDict):
    dl_path: str
    claim_path: str
    car_path: str

    dl: dict
    claim: dict
    car: dict

    comparison: dict
    decision: str
```

# Multi-Agent Workflow
The workflow is orchestrated using LangGraph agents:

| Agent               | Responsibility            |
| ------------------- | ------------------------- |
| Upload Agent        | Upload files to Snowflake |
| DL Extract Agent    | Extract DL fields         |
| Claim Extract Agent | Extract claim fields      |
| Car Analysis Agent  | Analyze damage image      |
| Comparison Agent    | Validate and decide       |

# Security & Governance

* Snowflake-managed storage and governance
* Internal stage-based file handling
* Role-based access control (RBAC)
* Secure Bedrock model invocation
* No external OCR systems required
* Centralized data processing inside Snowflake

# Scalability

The architecture is designed to scale using:

* Snowflake elastic compute
* Serverless AWS Bedrock inference
* Stateless workflow orchestration
* Cloud-native managed services

# Planned Enhancements

* Fraud risk scoring
* PDF report export
* Human review workflow
* Agent-level observability
* Audit trail pipeline
* Real-time event-driven processing
* Model versioning and explainability

# Demo Highlights

This project demonstrates:

* Agentic AI workflow orchestration
* Multimodal AI processing
* Governed enterprise AI architecture
* Cross-platform AI integration
* Real-time intelligent decisioning
* Snowflake + AWS interoperability

# Maintainer

Created and maintained by @thisisbhanuj

For contributions, issues, or feature requests, please open a pull request or GitHub issue.

---
