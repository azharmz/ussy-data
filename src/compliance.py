"""Single eligibility rule. Secondary ratings are audit data only."""

POLICY = "sharia_compliance_COMPLIANT_v1"


def is_eligible(record):
    return record.get("sharia_compliance") == "COMPLIANT"
