"""
ai/log_sanitizer.py
Log sanitizer to strip secrets from log output.

Applies regex-based redaction to log records so that API keys, tokens,
and other credentials never reach log files or conversation saves that
may be committed to version control.
"""

import re
import logging


# Patterns to redact
SECRET_PATTERNS = [
    # Anthropic API keys
    (re.compile(r'sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}'), 'sk-ant-***REDACTED***'),
    # Generic API keys (long alphanumeric strings after common key field names)
    (re.compile(r'(api[_-]?key|apikey|authorization|bearer|token)(["\s:=]+)([A-Za-z0-9_-]{20,})', re.I),
     r'\1\2***REDACTED***'),
    # Base64 encoded keys (enc: prefix from our settings)
    (re.compile(r'enc:[A-Za-z0-9+/=]{20,}'), 'enc:***REDACTED***'),
    # Ollama URLs with potential auth tokens
    (re.compile(r'(http[s]?://[^@\s]+:)[^@\s]+(@)'), r'\1***@'),
]


class SecretFilter(logging.Filter):
    """Logging filter that redacts secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: sanitize(str(v)) if isinstance(v, str) else v
                               for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(sanitize(str(a)) if isinstance(a, str) else a
                                    for a in record.args)
        return True


def sanitize(text: str) -> str:
    """Remove secrets from a text string."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def compact_log(log_text: str, max_lines: int = 50) -> str:
    """Compact a log by removing duplicates and keeping recent + important lines.

    Inspired by autoresearch's redirect-and-grep pattern -- keeps the context
    window clean by reducing repetitive log output.

    * Removes consecutive duplicate lines.
    * Preserves all lines containing ``ERROR`` or ``WARNING`` regardless of
      position.
    * If the de-duplicated log exceeds *max_lines*, keeps only the last
      *max_lines* lines -- but always re-inserts any ERROR/WARNING lines
      from earlier that would otherwise be lost.
    """
    if not log_text:
        return log_text

    raw_lines = log_text.splitlines()

    # Step 1: Remove consecutive duplicate lines
    deduped: list[str] = []
    for line in raw_lines:
        if deduped and line == deduped[-1]:
            continue
        deduped.append(line)

    # Step 2: Identify important lines (ERROR / WARNING)
    important: list[str] = []
    for line in deduped:
        upper = line.upper()
        if "ERROR" in upper or "WARNING" in upper:
            important.append(line)

    # Step 3: If short enough, return as-is
    if len(deduped) <= max_lines:
        return "\n".join(deduped)

    # Step 4: Keep last max_lines; prepend any important lines that were lost
    tail = deduped[-max_lines:]
    tail_set = set(tail)
    lost_important = [line for line in important if line not in tail_set]

    result_lines = lost_important + tail
    return "\n".join(result_lines)


def add_sanitizer_to_logging():
    """Add the secret filter to all logging handlers."""
    secret_filter = SecretFilter()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(secret_filter)
    # Also add to root logger itself
    root_logger.addFilter(secret_filter)
