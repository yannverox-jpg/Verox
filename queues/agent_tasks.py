"""Compatibility import; Vercel registers the root module verox_queue_worker."""
from verox_queue_worker import process_agent_task

__all__ = ["process_agent_task"]
