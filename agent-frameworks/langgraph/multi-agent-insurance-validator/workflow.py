import os
import re
import json
import uuid
import base64
import boto3
import tempfile
import time

from datetime import datetime
from typing import TypedDict, Annotated
import operator

from dotenv import load_dotenv
from snowflake.snowpark import Session
from langgraph.graph import StateGraph, END

load_dotenv()

STAGE = os.getenv("SNOWFLAKE_STAGE")

# -----------------------------
# Snowflake
# -----------------------------
conn = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
    "user": os.getenv("SNOWFLAKE_USER"),
    "password": os.getenv("SNOWFLAKE_PASSWORD"),
    "role": os.getenv("SNOWFLAKE_ROLE"),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    "database": os.getenv("SNOWFLAKE_DATABASE"),
    "schema": os.getenv("SNOWFLAKE_SCHEMA"),
}
session = Session.builder.configs(conn).create()

# -----------------------------
# Bedrock
# -----------------------------
bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))

# -----------------------------
# STATE
# -----------------------------
class State(TypedDict):
    dl_path: str
    claim_path: str
    car_path: str
    car_local: str
    dl: dict
    claim: dict
    car: dict
    steps: Annotated[list, operator.add]
    files: dict
    comparison: dict
    decision: str


# -----------------------------
# UTIL: RETRY
# -----------------------------
def fetch_with_retry(sql, retries=6, delay=2):
    print(f"Executing SQL with retry: {sql}")  # Debug log
    refresh_sql = f"ALTER STAGE {STAGE} REFRESH"
    for _ in range(retries):
        session.sql(refresh_sql).collect()
        rows = session.sql(sql).collect()
        if rows:
            return rows[0]
        time.sleep(delay)
    raise ValueError("File not found after retries")

# -----------------------------
# UPLOAD
# -----------------------------
def upload_to_stage(file, filename):
    print(f"Uploading file: {filename}")  # Debug log
    if file is None:
        raise ValueError("File missing")

    content = file.read()
    if not content:
        raise ValueError("Empty file")

    temp_dir = tempfile.gettempdir()
    local_path = os.path.join(temp_dir, filename.split("/")[-1])

    with open(local_path, "wb") as f:
        f.write(content)

    prefix = filename.split("/")[0]

    # IMPORTANT: upload into folder
    session.file.put(
        local_path,
        f"@{STAGE}/{prefix}",
        auto_compress=False,
        overwrite=True
    )

    return filename, local_path

def upload_all(state: State):
    files = state["files"]

    dl_name, _ = upload_to_stage(files["dl"], f"DL/{uuid.uuid4()}_{files['dl'].name}")
    claim_name, _ = upload_to_stage(files["claim"], f"CLAIMS/{uuid.uuid4()}_{files['claim'].name}")
    car_name, car_local = upload_to_stage(files["car"], f"CAR/{uuid.uuid4()}_{files['car'].name}")

    # Give Snowflake time to index stage
    time.sleep(3)

    return {
        "dl_path": dl_name,
        "claim_path": claim_name,
        "car_path": car_name,
        "car_local": car_local,
        "steps": ["Files Uploaded"]
    }

# -----------------------------
# DL EXTRACTION
# -----------------------------
def extract_dl(state: State):
    print(f"Extracting DL from {state['dl_path']}")  # Debug log
    sql = f"""
    SELECT
        RESULT:response:"full_name"::STRING AS full_name,
        RESULT:response:"dl_number"::STRING AS dl_number,
        RESULT:response:"date_of_birth"::STRING AS date_of_birth,
        RESULT:response:"address"::STRING AS address
    FROM (
        SELECT AI_EXTRACT(
            file => TO_FILE('@{STAGE}', s.relative_path),
            responseFormat => {{
                'full_name': 'Full name on the license',
                'dl_number': 'Driver license number',
                'date_of_birth': 'Date of birth',
                'address': 'Address on the license'
            }}
        ) AS RESULT
        FROM DIRECTORY(@{STAGE}) s
        WHERE s.relative_path = '{state["dl_path"]}')
    """

    row = fetch_with_retry(sql)

    return {
        "dl": {
            "full_name": row["FULL_NAME"],
            "dl_number": row["DL_NUMBER"],
            "date_of_birth": row["DATE_OF_BIRTH"],
            "address": row["ADDRESS"]
        },
        "steps": ["DL Extracted"]
    }

# -----------------------------
# CLAIM EXTRACTION
# -----------------------------
def extract_claim(state: State):
    print(f"Extracting Claim from {state['claim_path']}")  # Debug log
    sql = f"""
    SELECT
        RESULT:response:"customer_id"::STRING AS customer_id,
        RESULT:response:"full_name"::STRING AS full_name,
        RESULT:response:"dl_number"::STRING AS dl_number,
        RESULT:response:"incident_date"::STRING AS incident_date,
        RESULT:response:"vin"::STRING AS vin,
        RESULT:response:"vehicle"::STRING AS vehicle,
        RESULT:response:"description"::STRING AS description
    FROM (
        SELECT AI_EXTRACT(
            file => TO_FILE('@{STAGE}', s.relative_path),
            responseFormat => {{
                'customer_id': 'Customer ID',
                'full_name': 'Full name',
                'dl_number': 'Driver license number',
                'incident_date': 'Date of incident',
                'vin': 'Vehicle number or VIN',
                'vehicle': 'Vehicle involved',
                'description': 'Description of the claim'
            }}
        ) AS RESULT
        FROM DIRECTORY(@{STAGE}) s
        WHERE s.relative_path = '{state["claim_path"]}'
    )
    """

    row = fetch_with_retry(sql)

    result = {
        "customer_id": row["CUSTOMER_ID"],
        "full_name": row["FULL_NAME"],
        "dl_number": row["DL_NUMBER"],
        "incident_date": row["INCIDENT_DATE"],
        "vin": row["VIN"],
        "vehicle": row["VEHICLE"],
        "description": row["DESCRIPTION"]
    }

    # Extract color safely
    text = result.get("description") or ""
    match = re.search(r"Color:\s*(.+?)(,|$)", text, re.IGNORECASE)
    if match:
        result["vehicle_color"] = match.group(1).strip()

    return {"claim": result, "steps": ["Claim Extracted"]}

# -----------------------------
# CAR EXTRACTION
# -----------------------------
def extract_car(state: State):
    path = state["car_local"]
    ext = path.split(".")[-1]

    print(f"Extracting Car Data from {path}")  # Debug log

    with open(path, "rb") as f:
        img = base64.b64encode(f.read()).decode()

    body = {
        "schemaVersion": "messages-v1",
        "messages": [{
            "role": "user",
            "content": [
                {"image": {"format": ext, "source": {"bytes": img}}},
                {"text": "Return JSON: {color, damage, severity}"}
            ]
        }]
    }

    try:
        response = bedrock.invoke_model(
            modelId=os.getenv("AWS_MODEL_ID"),
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json"
        )

        text = json.loads(response["body"].read())["output"]["message"]["content"][0]["text"]
        match = re.search(r"\{.*\}", text, re.DOTALL)
        car = json.loads(match.group(0)) if match else {}

    except Exception:
        car = {"color": "unknown"}

    return {"car": car, "steps": ["Car Extracted"]}

# -----------------------------
# COMPARE
# -----------------------------
def compare(state: State):
    dl = state["dl"]
    claim = state["claim"]
    car = state["car"]
    print(f"Comparing data for customer {claim['customer_id']}")  # Debug log

    df = session.sql(f"""
        SELECT VIN, POLICY_END
        FROM CUSTOMER_POLICY
        WHERE CUSTOMER_ID = '{claim["customer_id"]}'
    """).to_pandas()

    if df.empty:
        raise ValueError("Customer not found")

    vin_db = df.iloc[0]["VIN"]
    policy_end = df.iloc[0]["POLICY_END"]

    # Safe date parsing
    try:
        incident_date = datetime.strptime(claim.get("incident_date"), "%Y-%m-%d").date()
    except:
        incident_date = None

    policy_valid = incident_date and incident_date <= policy_end

    comparison = {
        "name": dl["full_name"] == claim["full_name"],
        "dl": dl["dl_number"] == claim["dl_number"],
        "vin": claim["vin"] == vin_db,
        "color": claim.get("vehicle_color", "").lower() in car.get("color", "")
    }

    decision = "ACCEPTED" if all(comparison.values()) and policy_valid else "REJECTED"

    return {
        "comparison": comparison,
        "decision": decision,
        "steps": ["Decision Made"]
    }

# -----------------------------
# WORKFLOW
# -----------------------------
def run_claim_processing_workflow(files):
    builder = StateGraph(State)

    builder.set_entry_point("upload")

    builder.add_node("upload", upload_all)
    builder.add_node("dl", extract_dl)
    builder.add_node("claim", extract_claim)
    builder.add_node("car", extract_car)
    builder.add_node("compare", compare)

    builder.add_edge("upload", "dl")
    builder.add_edge("dl", "claim")
    builder.add_edge("claim", "car")
    builder.add_edge("car", "compare")
    builder.add_edge("compare", END)

    return builder.compile().invoke({"files": files, "steps": []})
