import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "plugin.video.xstreamflex", "resources", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


class FakeClient:
    """Stands in for HttpClient: returns queued payloads, records the calls made."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def get_json(self, url, params=None):
        params = params or {}
        action = params.get("action", "")
        self.calls.append((url, action, params))
        if action in self.responses:
            payload = self.responses[action]
        elif "" in self.responses:
            payload = self.responses[""]
        else:
            raise AssertionError("no fixture for action %r" % action)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_text(self, url, params=None):
        self.calls.append((url, "text", params or {}))
        return self.responses.get("text", "")

    def iter_lines(self, url, params=None):
        self.calls.append((url, "lines", params or {}))
        return iter(self.responses.get("lines", []))

    def close(self):
        pass


@pytest.fixture
def fake_client():
    return FakeClient
