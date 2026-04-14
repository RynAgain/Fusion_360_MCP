"""Hierarchical rule loading system for project-specific and mode-specific instructions."""
import os
import logging
import glob
import re

logger = logging.getLogger(__name__)

# Search directories (relative to project root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RULES_DIRS = [
    os.path.join(PROJECT_ROOT, 'config', 'rules'),        # Global rules
    os.path.join(PROJECT_ROOT, '.f360-rules'),              # Project-specific rules
]

MODE_RULES_PATTERN = os.path.join(PROJECT_ROOT, 'config', 'rules-{}')  # Mode-specific


# Security: restrict mode names to safe characters to prevent path traversal
_SAFE_MODE_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


def _validate_mode(mode: str) -> None:
    """Raise ValueError if mode contains unsafe path characters.

    Security: prevents path-traversal attacks where a crafted mode name
    like ``../../etc`` could read files outside the config directory.
    """
    if not _SAFE_MODE_PATTERN.match(mode):
        raise ValueError(
            f"Invalid mode name: {mode!r}. "
            "Must contain only alphanumeric characters, hyphens, and underscores."
        )


def load_rules(mode: str = None) -> str:
    """
    Load rules from hierarchical directories.

    Priority (concatenated in order):
    1. Global rules from config/rules/
    2. Project rules from .f360-rules/
    3. Mode-specific rules from config/rules-{mode}/ (if mode specified)

    All .md and .txt files in each directory are loaded and concatenated.
    """
    parts = []

    # Global rules
    for rules_dir in RULES_DIRS:
        loaded = _load_dir(rules_dir)
        if loaded:
            parts.append(loaded)

    # Mode-specific rules
    if mode and mode != 'full':
        # Security: validate mode to prevent path traversal
        _validate_mode(mode)
        mode_dir = MODE_RULES_PATTERN.format(mode)
        loaded = _load_dir(mode_dir)
        if loaded:
            parts.append(f"## Rules for {mode} mode\n\n{loaded}")

    return "\n\n".join(parts)


def _load_dir(directory: str) -> str:
    """Load all .md and .txt files from a directory."""
    if not os.path.isdir(directory):
        return ""

    files = sorted(
        glob.glob(os.path.join(directory, '*.md')) +
        glob.glob(os.path.join(directory, '*.txt'))
    )

    parts = []
    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if content:
                filename = os.path.basename(filepath)
                parts.append(f"### {filename}\n{content}")
                logger.debug("Loaded rule file: %s", filepath)
        except Exception as e:
            logger.warning("Failed to load rule file %s: %s", filepath, e)

    return "\n\n".join(parts)


def list_rule_files() -> list[dict]:
    """List all discovered rule files with their sources."""
    files = []

    for rules_dir in RULES_DIRS:
        if os.path.isdir(rules_dir):
            source = 'global' if 'config' in rules_dir else 'project'
            for filepath in sorted(glob.glob(os.path.join(rules_dir, '*.*'))):
                if filepath.endswith(('.md', '.txt')):
                    files.append({
                        'path': filepath,
                        'name': os.path.basename(filepath),
                        'source': source,
                        'directory': rules_dir,
                    })

    # Mode-specific directories
    config_dir = os.path.join(PROJECT_ROOT, 'config')
    if os.path.isdir(config_dir):
        for entry in os.listdir(config_dir):
            if entry.startswith('rules-'):
                mode_dir = os.path.join(config_dir, entry)
                if os.path.isdir(mode_dir):
                    mode = entry[6:]  # strip 'rules-' prefix
                    for filepath in sorted(glob.glob(os.path.join(mode_dir, '*.*'))):
                        if filepath.endswith(('.md', '.txt')):
                            files.append({
                                'path': filepath,
                                'name': os.path.basename(filepath),
                                'source': f'mode:{mode}',
                                'directory': mode_dir,
                            })

    return files


def create_example_rules():
    """Create example rule files to demonstrate the system."""
    global_dir = os.path.join(PROJECT_ROOT, 'config', 'rules')
    os.makedirs(global_dir, exist_ok=True)

    # Create example global rule
    example_path = os.path.join(global_dir, 'example.md')
    if not os.path.exists(example_path):
        with open(example_path, 'w', encoding='utf-8') as f:
            f.write("""# Example Global Rule

This is an example rule file. Place .md or .txt files in this directory
to add custom instructions to the AI agent.

Examples of useful rules:
- Unit preferences: "All user dimensions are in inches; convert to cm for the API"
- Design standards: "Use 0.5mm fillet radius on all external edges"
- Material defaults: "Default material is ABS Plastic unless specified"
- Naming conventions: "Prefix all component names with the project code"

Delete this file and replace with your own rules.
""")

    # Create example mode-specific rule
    sketch_dir = os.path.join(PROJECT_ROOT, 'config', 'rules-sketch')
    os.makedirs(sketch_dir, exist_ok=True)

    example_sketch = os.path.join(sketch_dir, 'example.md')
    if not os.path.exists(example_sketch):
        with open(example_sketch, 'w', encoding='utf-8') as f:
            f.write("""# Example Sketch Mode Rule

This rule only applies when in Sketch Mode.

Example sketch-specific rules:
- "Always verify profiles are closed before finishing"
- "Use construction lines for reference geometry"
- "Add dimensions to all sketch curves"
""")
