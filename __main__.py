#!/usr/bin/env python3
"""
kport - Cross-platform port inspector and killer
This is a wrapper script. The core logic has been moved to src/kport/.
"""
import io
import os
import sys

# Add the src directory to the python path so it can be run directly
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

# Force UTF-8 on Windows to prevent Emoji crash (if not already handled)
if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except AttributeError:
        pass

from kport.cli import main

if __name__ == "__main__":
    sys.exit(main())