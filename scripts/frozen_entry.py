"""Entry point for the Windows runtime; stdout belongs exclusively to MCP."""
from multiprocessing import freeze_support
import os
import sys

if __name__ == "__main__":
    freeze_support()
    from naviscoord.mcp_server import main
    # MCP's UTF-8 wrapper may own and close stdout's buffer at shutdown.
    # PyInstaller flushes stdout again after returning from this entry point.
    # Keep an independent descriptor so orderly EOF does not print a traceback.
    final_stdout = os.dup(sys.stdout.fileno())
    try:
        main()
    finally:
        sys.stdout = os.fdopen(final_stdout, "w", encoding="utf-8")
        sys.__stdout__ = sys.stdout
