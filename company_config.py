"""Company-specific configuration layered over the general bookkeeping core."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


VALID_VENDOR_BILL_MODES = {"ask", "create_cost", "support_cost"}


@dataclass
class CompanyProfile:
    name: str = "My Company"
    job_label: str = "Work Order"
    customer_label: str = "Customer"
    vendor_bill_mode: str = "ask"
    approval_threshold: Decimal | None = None
    approver_roles: tuple[str, ...] = ("Owner", "Bookkeeper")
    field_service_system: str = "none"
    accounting_system: str = "none"
    bank_system: str = "none"
    document_system: str = "openai"

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Company name is required")
        if not self.job_label.strip():
            raise ValueError("Job label is required")
        if not self.customer_label.strip():
            raise ValueError("Customer label is required")
        if self.vendor_bill_mode not in VALID_VENDOR_BILL_MODES:
            raise ValueError("Invalid vendor bill mode")
        if self.approval_threshold is not None and self.approval_threshold < 0:
            raise ValueError("Approval threshold cannot be negative")
        if not self.approver_roles:
            raise ValueError("At least one approver role is required")

    def requires_extra_approval(self, amount: Decimal) -> bool:
        return self.approval_threshold is not None and amount >= self.approval_threshold


class CompanyConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.profile = self._load()

    def _load(self) -> CompanyProfile:
        if not self.path.exists():
            return CompanyProfile()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        threshold = data.get("approval_threshold")
        data["approval_threshold"] = Decimal(str(threshold)) if threshold not in (None, "") else None
        data["approver_roles"] = tuple(data.get("approver_roles") or ("Owner", "Bookkeeper"))
        profile = CompanyProfile(**data)
        profile.validate()
        return profile

    def save(self) -> None:
        self.profile.validate()
        data = asdict(self.profile)
        data["approval_threshold"] = (
            str(self.profile.approval_threshold) if self.profile.approval_threshold is not None else None
        )
        data["approver_roles"] = list(self.profile.approver_roles)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def update_from_strings(
        self,
        *,
        name: str,
        job_label: str,
        customer_label: str,
        vendor_bill_mode: str,
        approval_threshold: str,
        approver_roles: str,
        field_service_system: str,
        accounting_system: str,
        bank_system: str,
        document_system: str,
    ) -> CompanyProfile:
        threshold: Decimal | None = None
        raw_threshold = approval_threshold.strip()
        if raw_threshold:
            try:
                threshold = Decimal(raw_threshold)
            except InvalidOperation as exc:
                raise ValueError("Approval threshold must be a valid amount") from exc

        roles = tuple(role.strip() for role in approver_roles.split(",") if role.strip())
        profile = CompanyProfile(
            name=name.strip(),
            job_label=job_label.strip(),
            customer_label=customer_label.strip(),
            vendor_bill_mode=vendor_bill_mode.strip(),
            approval_threshold=threshold,
            approver_roles=roles,
            field_service_system=field_service_system.strip() or "none",
            accounting_system=accounting_system.strip() or "none",
            bank_system=bank_system.strip() or "none",
            document_system=document_system.strip() or "none",
        )
        profile.validate()
        self.profile = profile
        self.save()
        return profile
