"""Fail-closed command guards for local and hosted database operations."""

import argparse

from researchflow.config import get_settings
from researchflow.errors import ResearchFlowError
from researchflow.infrastructure.database.session import (
    assert_safe_hosted_development_target,
    assert_safe_local_test_target,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=("local-test", "hosted-development"))
    arguments = parser.parse_args()

    try:
        settings = get_settings()
        if arguments.target == "local-test":
            assert_safe_local_test_target(settings)
        else:
            assert_safe_hosted_development_target(settings)
    except ResearchFlowError as error:
        print(f"target safety check failed: {error.code.value}")
        return 2

    print(f"target safety check passed: {arguments.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
