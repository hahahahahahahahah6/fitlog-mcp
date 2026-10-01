#!/usr/bin/env python3
"""fitlog-mcp entry point: zero-dependency MCP server over Streamable HTTP."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.transport import main

if __name__ == "__main__":
    main()
