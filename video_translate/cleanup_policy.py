"""Compatibility adapter: cleanup decisions are exclusively server-owned."""
def cleanup_policy(payload):
    from .policy_client import request_policy
    return request_policy(payload)
