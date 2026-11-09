from datetime import datetime
from typing import TYPE_CHECKING
import platform

if TYPE_CHECKING:
    from config.config import Config


def _get_shell_info():

    """
    Get shell information based on platform or operating system used during session.
    """

    import os
    import sys

    if sys.platform == 'darwin':
        return os.environ.get("SHELL", "/bin/zsh")
    elif sys.platform == "win32":
        return "PowerShell/cmd.exe"
    else:
        return os.environ.get("SHELL", "/bin/bash")


def get_environment_section(config: "Config") -> str:

    """
    Generate the environment section
    """

    now = datetime.now()
    os_info = f"{platform.system()} {platform.release()}"

    return f""" # Environment

    - **Current Date** : {now.strftime("%A, %B, %d, %Y")}
    - **Operating System**: {os_info}
    - **Current Working Directory: {config.cwd}
    - **Shell** : {_get_shell_info()}

    The user has granted you access to run tools in service of their request. Use them wisely and when needed.

    """
