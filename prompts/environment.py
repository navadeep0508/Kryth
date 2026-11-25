from datetime import datetime
from typing import TYPE_CHECKING
import platform
import sys

if TYPE_CHECKING:
    from config.config import Config


def _get_shell_info() -> str:
    """Get shell information based on platform or operating system used during session."""
    import os

    if sys.platform == 'darwin':
        return os.environ.get("SHELL", "/bin/zsh")
    elif sys.platform == "win32":
        return "PowerShell"
    else:
        return os.environ.get("SHELL", "/bin/bash")


def _get_shell_notes() -> str:
    """Return platform-specific shell usage notes for the system prompt."""
    if sys.platform == "win32":
        return """
## Windows Shell Notes (IMPORTANT)

The shell is **PowerShell**. The following POSIX/Unix commands do NOT exist and will
always fail — never use them:

| Forbidden command | PowerShell equivalent |
|---|---|
| `head -N file` | `Get-Content file -TotalCount N` |
| `tail -N file` | `Get-Content file -Tail N` |
| `tail -f file` | (use `Get-Content -Wait file`) |
| `grep PATTERN file` | `Select-String -Pattern PATTERN -Path file` |
| `grep PATTERN` (pipe) | `... | Select-String -Pattern PATTERN` |
| `find . -name X` | `Get-ChildItem -Recurse -Filter X` |
| `ls -la` | `Get-ChildItem` |
| `cat file` | `Get-Content file` |
| `wc -l file` | `(Get-Content file).Count` |
| `echo text > file` | `Set-Content -Path file -Value text` |
| `touch file` | `New-Item -ItemType File file` |
| `export VAR=val` | `$env:VAR = 'val'` |

Do **not** pipe to `head` or `tail` — they are not available. Use `Select-Object`:
- `... | Select-Object -First 50` (equivalent to `| head -50`)
- `... | Select-Object -Last 30`  (equivalent to `| tail -30`)

Use the native `read` / `grep` / `glob` / `list_directories` tools wherever possible
instead of shell commands, as they work reliably without shell compatibility issues."""
    return ""


def get_environment_section(config: "Config") -> str:
    """Generate the environment section."""
    now = datetime.now()
    os_info = f"{platform.system()} {platform.release()}"

    return f"""# Environment

- **Current Date**: {now.strftime("%A, %B %d, %Y")}
- **Operating System**: {os_info}
- **Current Working Directory**: {config.cwd}
- **Shell**: {_get_shell_info()}

The user has granted you access to run tools in service of their request. Use them wisely and when needed.
{_get_shell_notes()}
"""
