from collections import defaultdict

from models import Clinic, Doctor


def list_doctors() -> list[Doctor]:
    return list(
        Doctor.select()
        .where(Doctor.is_active == True)
        .order_by(Doctor.sort_order, Doctor.full_name)
    )


def clinics_by_region() -> dict[str, list[Clinic]]:
    grouped = defaultdict(list)
    clinics = (
        Clinic.select()
        .where(Clinic.is_active == True)
        .order_by(Clinic.region, Clinic.name)
    )
    for c in clinics:
        grouped[c.region].append(c)
    return dict(sorted(grouped.items(), key=lambda x: x[0]))
