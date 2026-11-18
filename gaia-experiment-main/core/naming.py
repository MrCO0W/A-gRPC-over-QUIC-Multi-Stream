import re


NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_config_name(name: str) -> str:
    """
    Validates a configuration (topology or scenario) name.
    
    Rules:
    - Non-empty, max 64 characters.
    - Matches ^[A-Za-z0-9._-]+$
    - Cannot be '.' or '..'
    - Cannot start with '.' (hidden files)
    - Cannot contain path separators or traversal sequences
    
    Returns the valid name or raises ValueError.
    """
    if not name or not isinstance(name, str):
        raise ValueError("Configuration name cannot be empty.")
    
    name = name.strip()
    if not name:
        raise ValueError("Configuration name cannot be empty or whitespace.")
    
    if len(name) > 64:
        raise ValueError(f"Configuration name '{name}' exceeds maximum length of 64 characters (got {len(name)}).")
    
    if name in (".", ".."):
        raise ValueError(f"Configuration name cannot be '{name}'.")
    
    if name.startswith("."):
        raise ValueError(f"Configuration name '{name}' cannot start with a dot (hidden file).")
    
    if "/" in name or "\\" in name:
        raise ValueError(f"Configuration name '{name}' cannot contain path separators ('/' or '\\').")
    
    if not NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid configuration name '{name}'. Allowed characters: alphanumeric, dot (.), underscore (_), and hyphen (-)."
        )
    
    return name
