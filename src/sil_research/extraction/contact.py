"""Phone number and general (organisation-level) email extraction.

Per the privacy requirements, this deliberately extracts *general business*
contact details only - it prefers role-style addresses (info@, admin@,
contact@, etc.) over anything that looks like a personal mailbox, and never
attempts to associate a phone/email with a named individual.
"""

from __future__ import annotations

import re

_PHONE_PATTERN = re.compile(
    r"(?:\+?61[\s\-]?)?(?:\(0\d\)|0\d)[\s\-]?\d{4}[\s\-]?\d{4}"  # landline: 0X XXXX XXXX
    r"|(?:\+?61[\s\-]?4|04)\d{2}[\s\-]?\d{3}[\s\-]?\d{3}"  # mobile: 04XX XXX XXX
    r"|1[38]00[\s\-]?\d{3}[\s\-]?\d{3}"  # 1300 / 1800 numbers
)

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_GENERIC_LOCAL_PARTS = (
    "info",
    "admin",
    "contact",
    "enquiries",
    "enquiry",
    "office",
    "hello",
    "support",
    "reception",
    "intake",
    "referrals",
    "referral",
)

_FREE_WEBMAIL_DOMAINS = ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com")


def extract_phone_numbers(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _PHONE_PATTERN.finditer(text):
        normalised = re.sub(r"[\s\-]", "", match.group(0))
        if normalised in seen:
            continue
        seen.add(normalised)
        found.append(match.group(0).strip())
    return found


def extract_emails(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _EMAIL_PATTERN.finditer(text)))


def extract_general_email(text: str, domain: str | None = None) -> str | None:
    """Return the best candidate general/business email address, or None.

    Preference order: a generic-local-part address on the provider's own
    domain > any address on the provider's own domain > a generic-local-part
    address anywhere > None. Free-webmail addresses (gmail.com etc.) are
    treated as likely-personal and only used as a last resort.
    """
    candidates = extract_emails(text)
    if not candidates:
        return None

    def local_part(email: str) -> str:
        return email.split("@", 1)[0].lower()

    def email_domain(email: str) -> str:
        return email.split("@", 1)[1].lower()

    def is_generic(email: str) -> bool:
        return any(local_part(email).startswith(prefix) for prefix in _GENERIC_LOCAL_PARTS)

    def is_own_domain(email: str) -> bool:
        return domain is not None and email_domain(email).endswith(domain.lower())

    def is_free_webmail(email: str) -> bool:
        return email_domain(email) in _FREE_WEBMAIL_DOMAINS

    ranked = sorted(
        candidates,
        key=lambda e: (
            not (is_generic(e) and is_own_domain(e)),
            not is_own_domain(e),
            not is_generic(e),
            is_free_webmail(e),
        ),
    )
    return ranked[0]
