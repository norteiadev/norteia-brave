"""Fake client implementations for offline testing.

Each fake implements the corresponding Protocol interface from brave/clients/base.py
using structural typing — no inheritance, no isinstance() checks.

Fakes live here so they can be shared across unit and integration tests.
"""
