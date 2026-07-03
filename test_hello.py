"""
A simple Python module to demonstrate a greeting function.

This module provides a single function `greet` that returns a personalized
hello message for a given name. It is intended as a foundational example
for learning basic Python function definitions and string formatting.
"""

def greet(name: str) -> str:
    """
    Returns a personalized greeting message.
    
    Args:
        name (str): The name of the person to greet.
        
    Returns:
        str: A greeting message in the format 'Hello, {name}!'
        
    Examples:
        >>> greet("Alice")
        'Hello, Alice!'
        >>> greet("Bob")
        'Hello, Bob!'
    """
    return f"Hello, {name}!"


# Example usage (uncomment to test)
# if __name__ == "__main__":
#     print(greet("World"))
