def get_developer_instructions_section(instructions: str) -> str:
    return f"""# Project Instructions

The following instructions were provided by the project maintainers:

{instructions}

Follow these instructions carefully as they contain important context about this specific project."""


def get_user_instructions_section(instructions: str) -> str:
    return f"""# User Instructions

The user has provided the following custom instructions:

{instructions}"""


def get_memory_section(memory: str) -> str:

    """
    Generate user memory section.
    """
    return f"""# Remembered Context

    The following information has been stored from previous interactions:

    {memory}

    Use this information to personalize your response and maintain consistency with you responses. Use this
    to tweak your responses based on the previous memory you have gotten from that tool.

    """
