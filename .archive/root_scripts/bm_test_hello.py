"""
BM Test Hello Module

A minimal but complete Python test module demonstrating basic functionality.
"""

import sys
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Main function that prints 'hello' and logs the event."""
    print('hello')
    logger.info("Successfully printed 'hello'")


if __name__ == "__main__":
    main()
