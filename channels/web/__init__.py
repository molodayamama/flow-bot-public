"""First-party browser channel for Photozhab generation and billing."""

from channels.web.app import WebAppConfig, WebAppDeps, register_web_app

__all__ = ["WebAppConfig", "WebAppDeps", "register_web_app"]
