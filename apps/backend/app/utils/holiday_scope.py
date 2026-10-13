"""Holiday scoping by state.

A holiday applies to an employee when:
    is_national = true
    OR (country_code = employee_country AND (region IS NULL OR region = employee_state))

`region` is a state code (``states.code``); NULL means country-wide (all states).
The effective employee state is ``employee_master.state_code`` else the state of
their linked office (``office_locations.state_code``).
"""
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def employee_state(db: Session, keycloak_user_id: Optional[str]) -> Optional[str]:
    """Effective state code for an employee (own state, else their office's)."""
    if not keycloak_user_id:
        return None
    row = db.execute(
        text("""
            SELECT COALESCE(em.state_code, ol.state_code)
            FROM employee_master em
            LEFT JOIN office_locations ol ON ol.id = em.location_id
            WHERE em.keycloak_user_id = :k
        """),
        {"k": keycloak_user_id},
    ).fetchone()
    return row[0] if row else None


def holiday_scope_sql(alias: str, state: Optional[str], country: Optional[str]) -> tuple[str, dict]:
    """Return (SQL predicate, params) that a holiday row must satisfy.

    ``alias`` is the table alias (e.g. ``h`` or ``""``). ``state`` may be None
    (then only national/country-wide holidays apply).
    """
    p = f"{alias}." if alias else ""
    params: dict = {"h_state": state}
    parts = [f"{p}is_national = true"]
    if country:
        params["h_country"] = country
        parts.append(f"({p}country_code = :h_country AND ({p}region IS NULL OR {p}region = :h_state))")
    else:
        parts.append(f"({p}region IS NULL OR {p}region = :h_state)")
    return "(" + " OR ".join(parts) + ")", params


def find_holiday(
    db: Session,
    holiday_date,
    state: Optional[str],
    country: Optional[str] = None,
) -> Optional[dict]:
    """Return {id, name} of the holiday applying to this employee on the date, or None."""
    clause, params = holiday_scope_sql("", state, country)
    params["d"] = holiday_date
    row = db.execute(
        text(f"SELECT id, holiday_name FROM holidays WHERE holiday_date = :d AND {clause}"),
        params,
    ).fetchone()
    return {"id": row[0], "name": row[1]} if row else None


def holiday_dates_in_range(
    db: Session,
    from_d,
    to_d,
    state: Optional[str],
    country: Optional[str] = None,
) -> set:
    """Set of holiday dates in range that apply to this employee."""
    clause, params = holiday_scope_sql("", state, country)
    params.update({"f": from_d, "t": to_d})
    rows = db.execute(
        text(f"SELECT holiday_date FROM holidays WHERE holiday_date BETWEEN :f AND :t AND {clause}"),
        params,
    ).fetchall()
    return {r[0] for r in rows}
