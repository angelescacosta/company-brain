"""Company Brain: private RAG assistant (public and fictional demonstration only)."""

import os

if os.environ.get("COMPANY_BRAIN_TEST_ENV") == "1":
    # A child process started by the test suite guards itself too (T1.AC3).
    from company_brain.isolation import install_child_guard

    install_child_guard()
