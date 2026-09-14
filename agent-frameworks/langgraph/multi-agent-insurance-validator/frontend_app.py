import streamlit as st
from workflow import run_claim_processing_workflow

st.set_page_config(page_title="Vehicle Insurance Claim Validator", layout="wide")

# -----------------------------
# HEADER
# -----------------------------
st.title("Vehicle Insurance Claim Validator")
st.caption("Automated Document Verification & Fraud Detection")

# -----------------------------
# FILE UPLOAD
# -----------------------------
st.subheader("📂 Upload Documents")

col1, col2, col3 = st.columns(3)

accepted_types = ["jpg", "jpeg", "png", "pdf"]

with col1:
    dl_file = st.file_uploader("Driver's License", type=accepted_types)

with col2:
    claim_file = st.file_uploader("Claim Document", type=accepted_types)

with col3:
    car_file = st.file_uploader("Car Image", type=accepted_types)

def render_card(title, icon, data, fields):
    with st.container(border=True):
        st.markdown(f"### {icon} {title}")

        for label, key in fields.items():
            value = data.get(key, "—")

            col1, col2 = st.columns([1, 2])

            with col1:
                st.caption(label)

            with col2:
                st.markdown(f"**{value}**")

# -----------------------------
# RUN WORKFLOW
# -----------------------------
if st.button("Run Validation", use_container_width=True):

    if not dl_file or not claim_file or not car_file:
        st.warning("⚠️ Please upload all required documents.")
        st.stop()

    with st.spinner("Processing Claim..."):

        try:
            result = run_claim_processing_workflow({
                "dl": dl_file,
                "claim": claim_file,
                "car": car_file
            })

        except Exception as e:
            st.error("❌ Workflow Failed")
            st.exception(e)
            st.stop()

    # -----------------------------
    # DECISION (TOP PRIORITY)
    # -----------------------------
    decision = result.get("decision")

    st.markdown("---")

    if decision == "ACCEPTED":
        st.success("✅ CLAIM APPROVED")
    else:
        st.error("❌ CLAIM REJECTED")

    # -----------------------------
    # COMPARISON (VISUAL GRID)
    # -----------------------------
    st.subheader("🔍 Validation Breakdown")

    comparison = result.get("comparison", {})

    cols = st.columns(len(comparison))

    for idx, (field, value) in enumerate(comparison.items()):
        with cols[idx]:
            st.markdown(f"**{field.upper()}**")

            if isinstance(value, bool):
                if value:
                    st.success("✔ Match")
                else:
                    st.error("✖ Mismatch")
            else:
                st.warning("Invalid format")

    # -----------------------------
    # WORKFLOW STEPS (SECONDARY)
    # -----------------------------
    with st.expander("⚙️ Processing Steps"):
        for step in result.get("steps", []):
            st.markdown(f"- {step}")

    # -----------------------------
    # SIDE-BY-SIDE DATA VIEW
    # -----------------------------
    st.subheader("📊 Extracted Data")

    dl = result.get("dl", {})
    claim = result.get("claim", {})
    car = result.get("car", {})

    col1, col2, col3 = st.columns(3)

    with col1:
        render_card(
            "Driver License",
            "🪪",
            dl,
            {
                "Name": "full_name",
                "DL Number": "dl_number",
                "DOB": "date_of_birth",
                "Address": "address"
            }
        )

    with col2:
        render_card(
            "Claim Document",
            "📄",
            claim,
            {
                "Customer ID": "customer_id",
                "Name": "full_name",
                "DL Number": "dl_number",
                "Incident Date": "incident_date",
                "VIN": "vin",
                "Vehicle": "vehicle"
            }
        )

    with col3:
        render_card(
            "Car Analysis",
            "🚘",
            car,
            {
                "Color": "color",
                "Damage": "damage",
                "Severity": "severity"
            }
        )

    # -----------------------------
    # DEBUG (HIDDEN)
    # -----------------------------
    with st.expander("🛠 Debug Output"):
        st.json(result)