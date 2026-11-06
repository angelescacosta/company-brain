"""Index the documents in the Company Brain folder by hand (T20).

Usage: uv run python scripts/update_index.py
Normally the update command does this (`scripts/brainctl update`, T20U).
"""

import sys

from company_brain.indexing import index_documents

if __name__ == "__main__":
    result = index_documents()
    print(result)
    sys.exit(0 if result.state == "completed" else 1)
