"""Mar layer — canonical published records.

Exports:
  promote_to_mar             — create/update MarRecord from a scored RioRecord
  reopen_from_error_report   — reset a published record back to DLQ
"""

from brave.core.mar.service import promote_to_mar, reopen_from_error_report

__all__ = ["promote_to_mar", "reopen_from_error_report"]
