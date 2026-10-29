"""Invented data and fakes for the grades tests. No real values, usernames, or network anywhere."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from ssu_lecture_downloader.core.grades.models import (
    ClassGradeDto,
    ClassList,
    GradeSummaryDto,
    GradesError,
    GradesOverview,
    RankDto,
    SemesterDto,
)

# Marker values: any of these showing up in a log, error body, trace, file, or access line is a leak.
SENTINEL_USER = "sentinel-user-7731"
SENTINEL_PASSWORD = "sentinel-pass-9917"
SENTINEL_CLASS = "ZZ-SENTINEL-CLASS-NAME"
SENTINEL_PROFESSOR = "ZZ-SENTINEL-PROFESSOR"
SENTINEL_CODE = "ZZ0042"
SENTINEL_GPA = 3.7771
SENTINEL_EXCEPTION = "SENTINEL-EXCEPTION-PAGE-TEXT-5519"
ALL_SENTINELS = (
    SENTINEL_USER,
    SENTINEL_PASSWORD,
    SENTINEL_CLASS,
    SENTINEL_PROFESSOR,
    SENTINEL_CODE,
    str(SENTINEL_GPA),
    SENTINEL_EXCEPTION,
)


class FakeClock:
    def __init__(self) -> None:
        self.moment = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs: float) -> None:
        self.moment += timedelta(**kwargs)


def summary(scale: float = 1.0) -> GradeSummaryDto:
    return GradeSummaryDto(
        attempted_credits=60.0 * scale,
        earned_credits=57.0 * scale,
        pf_earned_credits=3.0,
        grade_points_sum=210.5,
        grade_points_average=SENTINEL_GPA,
        arithmetic_mean=88.25,
    )


def semester(year: int, name: str) -> SemesterDto:
    return SemesterDto(
        year=year,
        semester=name,
        attempted_credits=18.0,
        earned_credits=18.0,
        pf_earned_credits=0.0,
        grade_points_average=SENTINEL_GPA,
        grade_points_sum=70.0,
        arithmetic_mean=87.5,
        semester_rank=RankDto(rank=12, total=140),
        general_rank=RankDto(rank=30, total=400),
        academic_probation=False,
        consult=False,
        flunked=False,
    )


def overview() -> GradesOverview:
    return GradesOverview(
        fetched_at="",
        recorded_summary=summary(),
        certificated_summary=summary(0.5),
        semesters=(semester(2025, "TWO"), semester(2025, "ONE"), semester(2024, "TWO")),
    )


def class_list(year: int, name: str) -> ClassList:
    return ClassList(
        year=year,
        semester=name,
        classes=tuple(
            ClassGradeDto(
                code=f"{SENTINEL_CODE}{index}",
                class_name=f"{SENTINEL_CLASS}-{index}",
                credits=3.0,
                score="88.5" if index % 3 else "P",
                grade="A+",
                professor=SENTINEL_PROFESSOR,
            )
            for index in range(9)
        ),
    )


class FakeGateway:
    """UsaintGateway double. Counts calls; can be slow, fail on demand, or raise a leaky exception."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.logins: list[str] = []  # usernames only; passwords are asserted separately
        self.overview_calls = 0
        self.classes_calls: list[tuple[int, str]] = []
        self.delay = 0.0
        self.login_error: Exception | None = None
        self.overview_errors: list[Exception] = []  # popped one per overview call
        self.classes_errors: list[Exception] = []
        self.sessions: list[Any] = []
        self.overview_sessions: list[Any] = []
        self.blocking = 0.0  # a synchronous sleep inside overview(): a gateway that blocks its thread
        self.threads: set[int] = set()  # thread ids the gateway coroutines ran on

    def available(self) -> bool:
        return self._available

    async def login(self, username: str, password: str) -> Any:
        self.logins.append(username)
        self.threads.add(threading.get_ident())
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.login_error is not None:
            raise self.login_error
        session = SimpleNamespace(id=len(self.logins))
        self.sessions.append(session)
        return session

    async def overview(self, session: Any) -> GradesOverview:
        self.overview_calls += 1
        self.threads.add(threading.get_ident())
        if self.blocking:
            time.sleep(self.blocking)
        self.overview_sessions.append(session)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.overview_errors:
            raise self.overview_errors.pop(0)
        return overview()

    async def classes(self, session: Any, year: int, semester_name: str) -> ClassList:
        self.classes_calls.append((year, semester_name))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.classes_errors:
            raise self.classes_errors.pop(0)
        return class_list(year, semester_name)


def unavailable() -> GradesError:
    return GradesError("grades_unavailable")


# ---- app wiring --------------------------------------------------------------------------------


def make_grades_app(tmp_path, *, gateway=None, pair=("alice", "pw-1"), consented=False, clock=None, **service_kwargs):
    """A full API app whose grades service uses `gateway` (never rusaint) and a consent file under tmp_path."""
    from test_api import FakeCredentialStore, FakeService

    from ssu_lecture_downloader.core.grades.consent import GradesConsentStore
    from ssu_lecture_downloader.core.grades.service import GradesService
    from ssu_lecture_downloader.service.api import create_app
    from ssu_lecture_downloader.service.credentials import CredentialPair

    clock = clock or FakeClock()
    gateway = gateway or FakeGateway()
    store = FakeCredentialStore(CredentialPair(username=pair[0], password=pair[1]) if pair else None)
    consent = GradesConsentStore(tmp_path / "app-data" / "grades-consent.json", now=clock)
    service = GradesService(consent, gateway, lambda: (store.pair.username, store.pair.password) if store.pair else None, now=clock, **service_kwargs)
    if consented:
        consent.grant(1)
    app = create_app(service_factory=lambda: FakeService(), default_output_dir=tmp_path / "downloads", credential_store=store, grades=service)
    return SimpleNamespace(app=app, gateway=gateway, store=store, consent=consent, service=service, clock=clock)
