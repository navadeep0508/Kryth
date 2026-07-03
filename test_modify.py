def add(a, b):
    """Return the sum of two numbers a and b."""
    return a + b


if __name__ == "__main__":
    # Example usage
    result = add(5, 3)
    print(f"The sum of 5 and 3 is {result}")

    # Test cases
    assert add(0, 0) == 0
    assert add(-1, 1) == 0
    assert add(10, -5) == 5
    print("All tests passed!")