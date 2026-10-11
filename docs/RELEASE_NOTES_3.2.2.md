# 🚀 kport v3.2.2 — Release Notes

**Release Date:** June 2026
**Branch:** `refactor`
**Previous Version:** 3.2.1

---

## Overview

kport 3.2.2 is a maintenance and refinement release focusing on CLI robustness, improved error handling, and streamlined release automation. This release enhances the user experience with better error messages and improved timeout handling while maintaining full backward compatibility with 3.2.1.

---

## 🆕 New Features

### 🔍 Enhanced CLI Error Handling

Improved command-line parser with better error messages and warnings for unused subcommands:

- **Custom Error Parser**: Provides more informative error messages when invalid arguments are passed
- **Unused Subcommand Warnings**: Alerts users when they attempt to use unrecognized subcommands
- Better guidance on correct command usage and available options

Example:
```bash
kport invalid-command  # Now shows helpful error message with suggestions
```

---

## 🔧 Improvements

### ⏱️ Graceful Timeout Handling

Enhanced graceful timeout handling in CLI commands for more reliable process termination:

- Improved timeout logic for `kport kill` operations
- More predictable behavior during graceful shutdown attempts
- Better handling of edge cases in timeout scenarios
- Enhanced code readability in timeout-related functions

### 📜 Streamlined Release Automation

Major refactoring of `release.py` for improved maintainability:

- Removed deprecated functions and redundant code
- Simplified version handling logic
- Cleaner build process integration
- Better separation of concerns in release workflows
- Reduced code complexity (454 lines → 190 lines of functional code)

---

## 🧪 Testing

The existing test suite in `tests/test_cli.py` continues to cover:
- CLI command parsing and execution
- Safety Shield functionality
- Port inspection and management
- Error handling scenarios

Run all tests:
```bash
python run_tests.py
# or
pytest tests/ -v
```

---

## 🐛 Bug Fixes & Quality Improvements

- Fixed CLI parser error handling to provide clearer feedback to users
- Improved graceful timeout reliability in port killing operations
- Removed deprecated code paths from release automation
- Enhanced code maintainability in core CLI module

---

## ⬆️ Upgrade Notes

- No breaking changes from v3.2.1
- All existing commands and configuration options remain unchanged
- No new dependencies added

---

## 📄 Full Changelog

- Enhanced CLI error handling with custom parser
- Added warning for unused subcommands
- Improved graceful timeout handling in kill operations
- Refactored `release.py` for better maintainability
- Simplified release script version handling
- Removed deprecated functions from build automation

---

## 🔗 Resources

- **GitHub Repository**: [kport](https://github.com/yourusername/kport)
- **Installation**: See [INSTALL.md](INSTALL.md)
- **Quick Start**: See [QUICKSTART.md](../QUICKSTART.md)
- **Contributing**: See [CONTRIBUTING.md](../CONTRIBUTING.md)
