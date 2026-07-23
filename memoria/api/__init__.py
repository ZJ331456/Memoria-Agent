"""Stable public entry point for the Memoria HTTP API."""

from .app import VERSION, create_app

__all__ = ["VERSION", "create_app"]
