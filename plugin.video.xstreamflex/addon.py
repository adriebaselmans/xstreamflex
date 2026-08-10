"""Entry point for every plugin:// call.

Kodi starts a fresh interpreter per invocation, so this file stays tiny: set up the
import path, build the context, dispatch.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from kodiui.context import build_context  # noqa: E402
from kodiui.router import dispatch  # noqa: E402


def main():
    context = build_context()
    query = sys.argv[2] if len(sys.argv) > 2 else ""
    dispatch(context, query)


if __name__ == "__main__":
    main()
