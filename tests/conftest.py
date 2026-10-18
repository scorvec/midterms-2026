"""Shared test setup: the tests run from a fresh clone (no data/raw, no data/cache) and never touch the network."""

import socket

import pytest


def _no_network(*args, **kwargs):
    raise OSError("network access is disabled in the tests")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
