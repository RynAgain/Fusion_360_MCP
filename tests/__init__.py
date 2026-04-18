"""Test suite for Artifex360.

Style guide:
- Use pytest-style classes and functions (not unittest.TestCase)
- Use pytest fixtures for setup/teardown
- Use plain 'assert' statements, not self.assert*
- New tests should follow this pattern; legacy unittest.TestCase classes
  will be migrated over time.

NOTE: Several legacy tests have weak assertions (e.g., 'assert isinstance(data, dict)').
When touching these tests, strengthen assertions to verify actual values.
"""
