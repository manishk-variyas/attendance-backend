from enum import Enum
# =============================================================================
# WORK LOCATION
# =============================================================================


class WorkLocation(str, Enum):
    """Work Location status for attendance and shifts"""
    OFFICE = "OFFICE"
    WFH = "WFH"
    REMOTE_ONSITE = "REMOTE_ONSITE"
    REMOTE_OFFSITE = "REMOTE_OFFSITE"
    OUT_OF_OFFICE = "OUT_OF_OFFICE"
    LEAVE = "LEAVE"





# =============================================================================
# LEAVE MANAGEMENT
# =============================================================================

class LeaveStatus(str, Enum):
    """Leave Status for attendance and shifts"""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PENDING_CANCELLATION = "PENDING_CANCELLATION"
    HALF_DAY = "HALF_DAY"
    


"""
Application-wide constants and enums.

Centralizes magic strings and values to prevent typos and make refactoring easier.
Import from here instead of hardcoding values.
"""

from enum import Enum


# =============================================================================
# WORK LOCATION
# =============================================================================

class WorkLocation(str, Enum):
    """Work location status for attendance and shifts."""
    OFFICE = "OFFICE"
    WFH = "WFH"
    REMOTE_ONSITE = "REMOTE_ONSITE"
    REMOTE_OFFSITE = "REMOTE_OFFSITE"
    OUT_OF_OFFICE = "OUT_OF_OFFICE"
    LEAVE = "LEAVE"


# =============================================================================
# LEAVE MANAGEMENT
# =============================================================================

class LeaveStatus(str, Enum):
    """Leave application approval status."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EMERGENCY = "emergency"
    CANCELLED = "cancelled"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLATION_REJECTED = "cancellation_rejected"


# NOTE: Leave types are DYNAMIC — stored in leave_types table, admin-managed.
# Do NOT create a LeaveType enum. Use LeaveTypeConfig model instead.
# Legacy codes found in leaves.leave_type column: "EL", "UPL", "PL", "CO"
# See: app/models/leave_type.py (LeaveTypeConfig)


# Leave statuses considered "active" for attendance display purposes.
# Used in: attendance/routes.py, attendance/admin_routes.py
ACTIVE_LEAVE_STATUSES = ["approved", "emergency", "cancellation_requested", "cancellation_rejected"]


# =============================================================================
# ATTENDANCE
# =============================================================================

class AttendanceStatus(str, Enum):
    """Attendance record status."""
    PRESENT = "present"
    ABSENT = "absent"
    HALF_DAY = "half_day"


class ShiftAttendanceStatus(str, Enum):
    """Shift attendance punch status."""
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    MISSED = "missed"


# =============================================================================
# SHIFT
# =============================================================================

class ShiftStatus(str, Enum):
    """Shift assignment status."""
    YET_TO_START = "Yet to start"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    MISSED = "Missed"


# =============================================================================
# WFH REQUESTS
# =============================================================================

class WfhStatus(str, Enum):
    """WFH request approval status."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# =============================================================================
# EMPLOYEE
# =============================================================================

class EmployeeStatus(str, Enum):
    """Employee account status."""
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


# =============================================================================
# USER ROLES (Keycloak realm roles)
# =============================================================================


class UserRole(str,Enum):
    """User roles in Keycloak."""
    ADMIN="Admin"
    PM="Project Manager"
    PC="Project Coordinator"
    TECH="Technical Resource"



APPROVER_LEAVES = [UserRole.ADMIN,UserRole.PM,UserRole.PC]



# =============================================================================
# SESSION / AUTH
# =============================================================================

SESSION_COOKIE_NAME = "session_id"

MAX_SESSIONS_PER_USER = 1
MAX_PASSWORD_RESET_PER_DAY = 5
MAX_PASSWORD_RESET_ATTEMPTS = 5


# =============================================================================
# DEFAULT VALUES
# =============================================================================

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_GEOGRAPHY_SRID = 4326
DEFAULT_GEOFENCE_RADIUS_METERS = 300
DEFAULT_GRACE_MINUTES = 15
DEFAULT_AUTO_CHECKOUT_CUTOFF = "22:00"
DEFAULT_FISCAL_YEAR_START_MONTH = 4  # April