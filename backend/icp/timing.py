"""Bounded, credential-free timings; service wall time is not model compute time."""
from contextvars import ContextVar
from urllib.parse import urlsplit

TIMINGS=ContextVar('icp_transport_timings',default=None)


def category(url):
    path=urlsplit(url).path
    if path.endswith('/api/v1/retrieval'):return 'retrieval_service'
    if path.endswith('/chat-messages'):return 'dify_model_service'
    if path.endswith('/workflows/run'):return 'dify_tool_service'
    if path.endswith(('/api/chat','/chat/completions')):return 'local_model_service'
    return 'other_service'
