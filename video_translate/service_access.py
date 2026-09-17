"""Sanitize the Lingzhi service boundary without exposing account metadata."""
import json
import re

SERVICE_ERROR = "SKILL_SERVICE_UNAVAILABLE"
SERVICE_MESSAGE = "Skill授权异常，请联系管理员处理，微信：marlon1102"
_ERROR_FIELDS = {"error", "error_code", "errorcode", "code", "message", "errormessage", "detail", "reason"}
_PATTERNS = (
    r"(?:insufficient|exhausted|depleted)[_ -]*(?:credits?|points?|balance|quota)",
    r"(?:credits?|points?|balance|quota)[_ -]*(?:insufficient|exhausted|depleted|exceeded|not[_ -]*enough)",
    r"(?:not enough|insufficient)\s+(?:account\s+)?(?:credits?|points?|balance|quota)",
    r"(?:积分|余额|额度)[^\n]{0,12}(?:不足|耗尽|用完|超出|超限)",
)
_BILLING_FIELDS = {"credit", "credits", "points", "balance", "quota", "cost", "billing", "usage", "remainingcredits", "consumedcredits", "creditcost", "积分", "余额", "额度"}

def service_blocked(value):
    """Inspect error envelopes only; never infer authorization from media text."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return any(re.search(pattern, value, re.I) for pattern in _PATTERNS)
        return service_blocked(parsed)
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower().replace("_", "")
            if str(key).lower() in _ERROR_FIELDS or name in _ERROR_FIELDS:
                if service_blocked(item):
                    return True
            elif key in {"output", "body", "result", "data", "errors"}:
                if service_blocked(item):
                    return True
    if isinstance(value, list):
        return any(service_blocked(item) for item in value)
    return False

def sanitize_response(value):
    if isinstance(value, dict):
        return {key: sanitize_response(item) for key, item in value.items()
                if not any(word in str(key).lower().replace("_", "").replace("-", "")
                           for word in _BILLING_FIELDS)}
    if isinstance(value, list):
        return [sanitize_response(item) for item in value]
    return value
