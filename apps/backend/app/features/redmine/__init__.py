"""Redmine integration feature package.

Intentionally does NOT import submodules here: ``app.utils.shift_time`` imports
``app.features.redmine.constants`` at module load, and eagerly importing
``.routes`` (which pulls in shifts/attendance) would create a circular import
when ``shift_time`` is imported first (e.g. the standalone attendance sweeper).
Import ``app.features.redmine.routes`` directly instead.
"""
