"""Platform for Smartschool BE sensor."""

from __future__ import annotations

import logging
import re

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import CONF_USERNAME, PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SmartschoolConfigEntry
from .const import CONF_BASE_URL, DOMAIN
from .coordinator import (
    SmartschoolAgendaCoordinator,
    SmartschoolCoordinator,
    SmartschoolMessagesCoordinator,
)
from .entity import account_device_info

_LOGGER = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^a-z0-9]+")


def _safe_slug(value: str) -> str:
    """Convert a string to a safe slug for unique IDs."""
    return _SAFE_RE.sub("_", (value or "").lower()).strip("_")


def _course_slug(course_name: str) -> str:
    """Convert a course name to a safe slug."""
    return _safe_slug(course_name)


def _student_name(student: dict) -> str:
    """Return best display name for a student."""
    return (
        student.get("fullNameBIN")
        or student.get("fullName")
        or f"{student.get('name', '')} {student.get('surname', '')}".strip()
        or "Student"
    )


def _teacher_name(teacher: dict) -> str:
    """Return best display name for a teacher."""
    return (
        teacher.get("fullNameBIN")
        or teacher.get("fullName")
        or f"{teacher.get('name', '')} {teacher.get('surname', '')}".strip()
        or None
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmartschoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smartschool sensors from a config entry."""
    base_url = config_entry.data[CONF_BASE_URL]
    username = config_entry.data[CONF_USERNAME]
    coordinator = config_entry.runtime_data.results
    device_info = account_device_info(config_entry)

    def _with_device(entities: list[SensorEntity]) -> list[SensorEntity]:
        for entity in entities:
            entity._attr_device_info = device_info
        return entities

    known_courses: set[str] = set()
    known_students: set[int] = set()

    @callback
    def _add_new_course_sensors() -> None:
        """Add a sensor for every course that does not have one yet."""
        data = coordinator.data or {}
        evaluations = data.get("evaluations") or {}
        by_course: dict = evaluations.get("by_course", {})

        new_courses = set(by_course.keys()) - known_courses

        if not new_courses:
            return

        known_courses.update(new_courses)
        _LOGGER.debug("Adding sensors for new courses: %s", new_courses)

        async_add_entities(
            _with_device([
                SmartschoolCourseSensor(
                    coordinator,
                    course,
                    username,
                    config_entry.entry_id,
                )
                for course in sorted(new_courses)
            ])
        )

    @callback
    def _add_new_student_sensors() -> None:
        """Add one info sensor/device for every student."""
        data = coordinator.data or {}
        students = data.get("students") or []

        new_students = [
            student
            for student in students
            if student.get("userID") is not None
            and int(student.get("userID")) not in known_students
        ]

        if not new_students:
            return

        for student in new_students:
            known_students.add(int(student["userID"]))

        _LOGGER.debug("Adding Smartschool student devices: %s", known_students)

        async_add_entities(
            [
                SmartschoolStudentInfoSensor(
                    coordinator,
                    student,
                    base_url,
                    config_entry.entry_id,
                )
                for student in new_students
            ]
        )

    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_course_sensors))
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_student_sensors))

    async_add_entities(
        _with_device([
            SmartschoolLatestResultSensor(
                coordinator,
                username,
                config_entry.entry_id,
            ),
            SmartschoolUnreadMessagesSensor(
                config_entry.runtime_data.messages,
                username,
                config_entry.entry_id,
            ),
            SmartschoolUpcomingTasksSensor(
                config_entry.runtime_data.agenda,
                username,
                config_entry.entry_id,
            ),
        ])
    )

    _add_new_course_sensors()
    _add_new_student_sensors()


class SmartschoolLatestResultSensor(CoordinatorEntity, SensorEntity):
    """Shows the most recently available result across all courses."""

    _attr_icon = "mdi:school"
    # Large list; keep it out of the recorder database.
    _unrecorded_attributes = frozenset({"recente_resultaten"})

    def __init__(self, coordinator: SmartschoolCoordinator, username: str, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_name = f"Smartschool Laatste Resultaat ({username})"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_latest_result"

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        evaluations = data.get("evaluations") or {}

        if not evaluations:
            return "Geen resultaten"

        latest = evaluations.get("latest", {})

        return latest.get("name") or latest.get("score_description") or "Resultaat gevonden"

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        evaluations = data.get("evaluations") or {}

        latest = evaluations.get("latest", {})
        recent = evaluations.get("recent", [])

        attrs = {
            "vak": latest.get("course"),
            "score": latest.get("score_description"),
            "score_percentage": latest.get("score_value"),
            "datum": latest.get("date"),
            "beschikbaar_sinds": latest.get("availability_date"),
            "feedback": latest.get("feedback"),
            "recente_resultaten": recent,
        }

        return attrs


class SmartschoolCourseSensor(CoordinatorEntity, SensorEntity):
    """Shows the latest score for a specific course."""

    _attr_icon = "mdi:book-open-variant"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1
    # Full history can grow large; keep it out of the recorder database.
    _unrecorded_attributes = frozenset({"score_history"})

    def __init__(
        self,
        coordinator: SmartschoolCoordinator,
        course_name: str,
        username: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._course_name = course_name
        self._attr_name = f"Smartschool {course_name} ({username})"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_course_{_course_slug(course_name)}"

    def _get_course_data(self) -> dict:
        data = self.coordinator.data or {}
        evaluations = data.get("evaluations") or {}

        return (evaluations.get("by_course") or {}).get(self._course_name, {})

    @property
    def native_value(self) -> float | None:
        """Return the latest numeric score in %.

        Non-numeric scores (e.g. "goed") stay available via the `score` attribute;
        a measurement sensor must never get a text state.
        """
        ev = self._get_course_data().get("latest", {})

        try:
            return float(ev["score_value"])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self):
        course_data = self._get_course_data()

        ev = course_data.get("latest", {})
        history = course_data.get("history", [])

        score_history = [
            {
                "datum": h.get("availability_date") or h.get("date"),
                "toets": h.get("name"),
                "score": h.get("score_value"),
                "score_omschrijving": h.get("score_description"),
                "feedback": h.get("feedback"),
            }
            for h in history
        ]

        numeric_scores = []

        for h in history:
            try:
                numeric_scores.append(float(h["score_value"]))
            except (TypeError, ValueError, KeyError):
                pass

        gemiddelde = round(sum(numeric_scores) / len(numeric_scores), 1) if numeric_scores else None

        return {
            "toets": ev.get("name"),
            "score": ev.get("score_description"),
            "score_percentage": ev.get("score_value"),
            "datum": ev.get("date"),
            "beschikbaar_sinds": ev.get("availability_date"),
            "feedback": ev.get("feedback"),
            "gemiddelde_score": gemiddelde,
            "aantal_toetsen": len(numeric_scores),
            "score_history": score_history,
        }


class SmartschoolStudentInfoSensor(CoordinatorEntity, SensorEntity):
    """Student info sensor, grouped as a Home Assistant device."""

    _attr_icon = "mdi:account-school"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: SmartschoolCoordinator,
        student: dict,
        base_url: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)

        self._student_id = int(student["userID"])
        self._base_url = base_url.rstrip("/")

        name = _student_name(student)

        self._attr_name = f"{name} Info"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_student_{self._student_id}_info"

    def _get_student(self) -> dict:
        data = self.coordinator.data or {}
        students = data.get("students") or []

        for student in students:
            if int(student.get("userID", 0)) == self._student_id:
                return student

        return {}

    @property
    def native_value(self):
        """Return the student name as state."""
        student = self._get_student()

        return _student_name(student) if student else None

    @property
    def device_info(self) -> DeviceInfo:
        """Group all future student entities under one device."""
        student = self._get_student()
        name = _student_name(student) if student else f"Student {self._student_id}"

        return DeviceInfo(
            identifiers={(DOMAIN, f"student_{self._student_id}")},
            name=name,
            manufacturer="Smartschool",
            model="Student",
            configuration_url=self._base_url,
        )

    @property
    def extra_state_attributes(self):
        """Return student metadata."""
        student = self._get_student()

        titularissen = []

        for teacher in student.get("titu", []) or []:
            name = _teacher_name(teacher)
            if name:
                titularissen.append(name)

        return {
            "user_id": student.get("userID"),
            "account_id": student.get("accountID"),
            "name": student.get("name"),
            "surname": student.get("surname"),
            "full_name": student.get("fullName"),
            "display_name": student.get("fullNameBIN"),
            "class": (student.get("class") or "").strip(),
            "admin_name": student.get("adminName"),
            "titularissen": titularissen,
            "photo_url": student.get("photoUrl"),
            "current_account": student.get("currentAccount"),
            "status": student.get("status"),
            "is_current_user": student.get("isCurrentUser"),
        }


class SmartschoolUnreadMessagesSensor(CoordinatorEntity, SensorEntity):
    """Number of unread messages in the inbox."""

    _attr_icon = "mdi:email-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "berichten"
    _unrecorded_attributes = frozenset({"berichten"})

    def __init__(
        self, coordinator: SmartschoolMessagesCoordinator, username: str, entry_id: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = f"Smartschool Ongelezen Berichten ({username})"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_unread_messages"

    def _inbox(self) -> list[dict]:
        return (self.coordinator.data or {}).get("inbox") or []

    @property
    def native_value(self) -> int:
        return sum(1 for m in self._inbox() if m["unread"])

    @property
    def icon(self) -> str:
        return "mdi:email-alert" if self.native_value else "mdi:email-outline"

    @property
    def extra_state_attributes(self):
        inbox = self._inbox()
        return {
            "totaal_in_inbox": len(inbox),
            "berichten": [
                {
                    "id": m["id"],
                    "van": m["from"],
                    "onderwerp": m["subject"],
                    "datum": m["date"],
                    "ongelezen": m["unread"],
                    "bijlagen": m["attachments"],
                    "vlag": m["flag"],
                }
                for m in inbox[:15]
            ],
        }


class SmartschoolUpcomingTasksSensor(CoordinatorEntity, SensorEntity):
    """Number of upcoming tasks and tests (Smartschool 'Taken en toetsen')."""

    _attr_icon = "mdi:clipboard-text-clock"
    _attr_native_unit_of_measurement = "taken"
    _unrecorded_attributes = frozenset({"taken"})

    def __init__(
        self, coordinator: SmartschoolAgendaCoordinator, username: str, entry_id: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = f"Smartschool Komende Taken ({username})"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_upcoming_tasks"

    def _tasks(self) -> list[dict]:
        today = dt_util.now().date().isoformat()
        tasks = (self.coordinator.data or {}).get("tasks") or []
        return sorted(
            (t for t in tasks if (t.get("date") or "") >= today),
            key=lambda t: t.get("date") or "",
        )

    @property
    def native_value(self) -> int:
        return len(self._tasks())

    @property
    def extra_state_attributes(self):
        tasks = self._tasks()
        tests = [t for t in tasks if "toets" in (t.get("type") or "").lower()]
        return {
            "volgende_datum": tasks[0]["date"] if tasks else None,
            "aantal_toetsen": len(tests),
            "volgende_toets": (
                f"{tests[0]['date']} {tests[0]['course']}: {tests[0]['description']}" if tests else None
            ),
            "taken": [
                {
                    "datum": t.get("date"),
                    "type": t.get("type"),
                    "vak": t.get("course"),
                    "omschrijving": t.get("description"),
                }
                for t in tasks
            ],
        }
