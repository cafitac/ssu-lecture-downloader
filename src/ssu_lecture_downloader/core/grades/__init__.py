"""Read-only u-SAINT grades (see .dev/features/20261029-grades-view/design/).

Privacy invariant: grade values live only in `GradesService` memory and in the HTTP response to the
local webview. Nothing here logs, traces, or writes a grade value, a username, or a credential.
"""

from __future__ import annotations
