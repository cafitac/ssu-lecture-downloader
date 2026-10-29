"""The only module that talks to rusaint (a third-party u-SAINT client, MIT, UniFFI native library).

- rusaint is imported lazily, on first use, so the backend starts (and `/api/health` answers) even
  when the native library is missing; that case reports `available() == False` and every call raises
  `GradesError("grades_unavailable")`.
- Read-only by construction: the only rusaint names used are `USaintSessionBuilder.with_password`,
  `CourseGradesApplicationBuilder.build`, `CourseType.BACHELOR`, `SemesterType`, and the application's
  `semesters / recorded_summary / certificated_summary / classes(include_details=False)`.
  `tests/grades/test_read_only_guard.py` enforces this list.
- Every rusaint exception is converted to `GradesError(code) from None`; its message is never read,
  logged, returned, or chained.
- The service runs these coroutines on a dedicated worker thread (see worker.py), never on the API loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import ModuleType
from typing import Any, Protocol

from ssu_lecture_downloader.core.grades import mapper
from ssu_lecture_downloader.core.grades.models import (
    GRADES_UNAVAILABLE,
    SEMESTER_NAMES,
    ClassList,
    GradesError,
    GradesOverview,
)

logger = logging.getLogger("ssu_lecture_downloader.grades")

class UsaintGateway(Protocol):
    """The port GradesService depends on (a fake implements it in tests)."""

    def available(self) -> bool: ...

    async def login(self, username: str, password: str) -> Any: ...

    async def overview(self, session: Any) -> GradesOverview: ...

    async def classes(self, session: Any, year: int, semester: str) -> ClassList: ...


def _load_rusaint() -> ModuleType:
    import rusaint  # lazy: native library; the only rusaint import in the package

    return rusaint


class _Session:
    """What `login` returns: the rusaint session plus the lazily built grades application."""

    __slots__ = ("session", "app")

    def __init__(self, session: Any) -> None:
        self.session = session
        self.app: Any = None


class RusaintGateway:
    def __init__(self, loader: Callable[[], ModuleType] = _load_rusaint) -> None:
        self._loader = loader
        self._module: ModuleType | None = None
        self._unavailable = False

    def _rusaint(self) -> ModuleType:
        if self._module is not None:
            return self._module
        if self._unavailable:
            raise GradesError(GRADES_UNAVAILABLE)
        try:
            self._module = self._loader()
        except (ImportError, OSError):
            self._unavailable = True
            raise GradesError(GRADES_UNAVAILABLE) from None
        except Exception:  # noqa: BLE001 - a broken native load must not surface its message
            self._unavailable = True
            raise GradesError(GRADES_UNAVAILABLE) from None
        return self._module

    def available(self) -> bool:
        try:
            self._rusaint()
        except GradesError:
            return False
        return True

    async def login(self, username: str, password: str) -> _Session:
        rusaint = self._rusaint()
        try:
            session = await rusaint.USaintSessionBuilder().with_password(username, password)
        except Exception:  # noqa: BLE001
            # Every login failure is "unavailable": rusaint 0.16.3 has one error type (`RusaintError.General`)
            # and no string in the library identifies a wrong-password response (see GR-PY-USAINT record, O7).
            # `grades_login_rejected` stays part of the contract for when a confirmed signal exists.
            raise GradesError(GRADES_UNAVAILABLE) from None
        return _Session(session)

    async def _app(self, handle: _Session) -> Any:
        if handle.app is None:
            rusaint = self._rusaint()
            handle.app = await rusaint.CourseGradesApplicationBuilder().build(handle.session)
        return handle.app

    async def overview(self, session: _Session) -> GradesOverview:
        rusaint = self._rusaint()
        try:
            app = await self._app(session)
            course = rusaint.CourseType.BACHELOR
            semesters = await app.semesters(course)
            recorded = await app.recorded_summary(course)
            certificated = await app.certificated_summary(course)
            return mapper.to_overview(semesters, recorded, certificated)
        except mapper.GradesMappingError as exc:
            logger.warning("grades_mapping field=%s", exc.field)  # a fixed field name, never a value
            raise GradesError(GRADES_UNAVAILABLE) from None
        except GradesError:
            raise
        except Exception:  # noqa: BLE001
            raise GradesError(GRADES_UNAVAILABLE) from None

    async def classes(self, session: _Session, year: int, semester: str) -> ClassList:
        rusaint = self._rusaint()
        if semester not in SEMESTER_NAMES:
            raise GradesError(GRADES_UNAVAILABLE)
        try:
            app = await self._app(session)
            items = await app.classes(rusaint.CourseType.BACHELOR, year, rusaint.SemesterType[semester], False)
            return mapper.to_classes(items, year=year, semester=semester)
        except mapper.GradesMappingError as exc:
            logger.warning("grades_mapping field=%s", exc.field)
            raise GradesError(GRADES_UNAVAILABLE) from None
        except GradesError:
            raise
        except Exception:  # noqa: BLE001
            raise GradesError(GRADES_UNAVAILABLE) from None
