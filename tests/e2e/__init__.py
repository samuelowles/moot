"""End-to-end tests for the Claude Code plugin layer.

The unit suite covers the Python runtime; this package covers the surfaces an
install actually touches: the manifests, the agent charters, the skills,
commands and scheduled-task templates, the PreToolUse hooks (run as real
subprocesses with a JSON payload on stdin), and the CLI (run as subprocesses
from a working directory that is not the repo root).
"""
