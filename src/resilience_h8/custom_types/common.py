"""Common types used across the application.

This module defines basic type definitions that are used throughout
the application and aren't specific to any particular domain.
"""

import uuid
from typing import NewType, Union


def get_random_uuid_as_str() -> str:
    """Generate a random UUID and return it as a string.

    Returns:
        str: A UUID4 as a string
    """
    return str(uuid.uuid4())


# Define a type for strings that are UUIDs or actual UUID objects
UUID_STR = Union[uuid.UUID, str]

# We can't use Union with NewType directly, so we'll use str as the base type
# since that's what most functions will expect
UUID4_STR = NewType("UUID4_STR", str)
