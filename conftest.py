# conftest.py
"""Root pytest configuration — adds service source directories to sys.path."""
import sys
import os

_root = os.path.dirname(os.path.abspath(__file__))

# Allow: import agents.research_agent, import config, etc.
sys.path.insert(0, os.path.join(_root, "services", "langgraph-api"))

# Allow: from models import Base, Query, Run, Source
sys.path.insert(0, os.path.join(_root, "services", "persistence-api"))
