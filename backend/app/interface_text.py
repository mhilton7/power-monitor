from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InterfaceTextRevision, InterfaceTextState
from app.problem import ProblemError


@dataclass(frozen=True)
class TextDefinition:
    key: str
    section: str
    default: str
    label: str
    description: str
    field_type: str = "text"
    required: bool = True
    visibility: str = "authenticated"
    max_length: int = 160
    min_length: int = 1
    line_breaks: bool = False
    url_companion: bool = False
    markdown: bool = False
    blank_allowed: bool = False
    preview_location: str = "dashboard"


def _text(
    key: str,
    section: str,
    default: str,
    label: str,
    description: str,
    field_type: str = "text",
    required: bool = True,
    visibility: str = "authenticated",
    max_length: int = 160,
    min_length: int = 1,
    line_breaks: bool = False,
    url_companion: bool = False,
    markdown: bool = False,
    blank_allowed: bool = False,
    preview_location: str = "dashboard",
) -> TextDefinition:
    return TextDefinition(
        key,
        section,
        default,
        label,
        description,
        field_type,
        required,
        visibility,
        max_length,
        min_length,
        line_breaks,
        url_companion,
        markdown,
        blank_allowed,
        preview_location,
    )


TEXT_DEFINITIONS = (
    _text(
        "general.application_name",
        "General",
        "Power Monitor",
        "Application display name",
        "Full product name shown in the dashboard and login screen.",
        visibility="public",
    ),
    _text(
        "general.application_short_name",
        "General",
        "Power Monitor",
        "Application short name",
        "Compact product name for narrow layouts.",
        max_length=40,
    ),
    _text(
        "general.dashboard_welcome_heading",
        "General",
        "Power Dashboard",
        "Dashboard welcome heading",
        "Heading on the Overview page.",
    ),
    _text(
        "general.dashboard_welcome_subtitle",
        "General",
        "Live energy, device health, synchronization, and estimated Southern California "
        "Edison costs across your monitored circuits.",
        "Dashboard welcome subtitle",
        "Subtitle on the Overview page.",
        max_length=300,
    ),
    _text(
        "general.organization_tagline",
        "General",
        "Local energy intelligence",
        "Organization or site tagline",
        "Optional tagline shown beneath the application name.",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=120,
    ),
    _text(
        "general.browser_title_prefix",
        "General",
        "Power Monitor",
        "Browser-title prefix",
        "Prefix used for browser titles.",
        max_length=60,
    ),
    _text(
        "login.heading",
        "Login Screen",
        "Sign in to your dashboard",
        "Login heading",
        "Primary sign-in form heading.",
        visibility="public",
    ),
    _text(
        "login.subtitle",
        "Login Screen",
        "Use your local Power Monitor account to continue.",
        "Login subtitle",
        "Supporting sign-in instructions.",
        visibility="public",
        max_length=240,
    ),
    _text(
        "login.email_label",
        "Login Screen",
        "Email address",
        "Email field label",
        "Label for the account email field.",
        visibility="public",
        max_length=60,
    ),
    _text(
        "login.password_label",
        "Login Screen",
        "Password",
        "Password field label",
        "Label for the password field.",
        visibility="public",
        max_length=60,
    ),
    _text(
        "login.sign_in_button",
        "Login Screen",
        "Sign in",
        "Sign-in button label",
        "Label for the sign-in action.",
        visibility="public",
        max_length=60,
    ),
    _text(
        "login.help_text",
        "Login Screen",
        "Use your local account credentials. Contact your administrator if you need access.",
        "Login help text",
        "Optional non-security login assistance.",
        visibility="public",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=300,
    ),
    _text(
        "login.support_label",
        "Login Screen",
        "Contact support",
        "Support link label",
        "Label for the optional login support link.",
        visibility="public",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=80,
        url_companion=True,
    ),
    _text(
        "login.support_url",
        "Login Screen",
        "",
        "Support link URL",
        "HTTPS or mailto support destination.",
        field_type="url",
        visibility="public",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=500,
    ),
    _text(
        "login.footer",
        "Login Screen",
        "Local account · Secure session · Audited access",
        "Login footer text",
        "Non-security informational text below the form.",
        visibility="public",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=200,
    ),
    _text(
        "navigation.overview",
        "Navigation",
        "Overview",
        "Overview",
        "Overview navigation label.",
        max_length=40,
    ),
    _text(
        "navigation.devices",
        "Navigation",
        "Devices",
        "Devices",
        "Devices navigation label.",
        max_length=40,
    ),
    _text(
        "navigation.topology",
        "Navigation",
        "Topology",
        "Topology",
        "Topology navigation label.",
        max_length=40,
    ),
    _text(
        "navigation.usage", "Navigation", "Usage", "Usage", "Usage navigation label.", max_length=40
    ),
    _text(
        "navigation.history",
        "Navigation",
        "History",
        "History",
        "History navigation label.",
        max_length=40,
    ),
    _text(
        "navigation.costs", "Navigation", "Costs", "Costs", "Costs navigation label.", max_length=40
    ),
    _text(
        "navigation.rates", "Navigation", "Rates", "Rates", "Rates navigation label.", max_length=40
    ),
    _text(
        "navigation.alerts",
        "Navigation",
        "Alerts & Notifications",
        "Alerts & Notifications",
        "Alerts navigation label.",
        max_length=60,
    ),
    _text(
        "navigation.enrollment",
        "Navigation",
        "Enrollment",
        "Enrollment",
        "Enrollment navigation label.",
        max_length=40,
    ),
    _text(
        "navigation.backups",
        "Navigation",
        "Backups",
        "Backups",
        "Backups navigation label.",
        max_length=40,
    ),
    _text(
        "navigation.administration",
        "Navigation",
        "Administration",
        "Administration",
        "Administration navigation label.",
        max_length=60,
    ),
    _text(
        "navigation.users_access",
        "Navigation",
        "Users & Access",
        "Users & Access",
        "Users and access navigation label.",
        max_length=60,
    ),
    _text(
        "navigation.interface_text",
        "Navigation",
        "Dashboard & Login Text",
        "Dashboard & Login Text",
        "Interface-text navigation label.",
        max_length=80,
    ),
    _text(
        "navigation.status_indicators",
        "Navigation",
        "Status Indicators & Layout",
        "Status Indicators & Layout",
        "Status-indicator layout navigation label.",
        max_length=80,
    ),
    _text(
        "pages.overview.title",
        "Page Titles & Subtitles",
        "Power Dashboard",
        "Overview title",
        "Overview page title.",
    ),
    _text(
        "pages.overview.subtitle",
        "Page Titles & Subtitles",
        "Live energy, device health, synchronization, and estimated Southern California "
        "Edison costs across your monitored circuits.",
        "Overview subtitle",
        "Overview page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.devices.title",
        "Page Titles & Subtitles",
        "Device Management",
        "Devices title",
        "Devices page title.",
    ),
    _text(
        "pages.devices.subtitle",
        "Page Titles & Subtitles",
        "Sensor health and general data",
        "Devices subtitle",
        "Devices page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.topology.title",
        "Page Titles & Subtitles",
        "Site & circuit topology",
        "Topology title",
        "Topology page title.",
    ),
    _text(
        "pages.topology.subtitle",
        "Page Titles & Subtitles",
        "Make overlap explicit so parent, service-leg, branch, and submeter readings never "
        "become an accidental total.",
        "Topology subtitle",
        "Topology page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.usage.title",
        "Page Titles & Subtitles",
        "Usage by Time of Day",
        "Usage title",
        "Usage page title.",
    ),
    _text(
        "pages.usage.subtitle",
        "Page Titles & Subtitles",
        "Understand when monitored energy is used.",
        "Usage subtitle",
        "Usage page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.history.title",
        "Page Titles & Subtitles",
        "History & comparison",
        "History title",
        "History page title.",
    ),
    _text(
        "pages.history.subtitle",
        "Page Titles & Subtitles",
        "Raw UTC intervals are rendered in your locale, with gaps and quality limitations "
        "kept visible.",
        "History subtitle",
        "History page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.costs.title", "Page Titles & Subtitles", "Costs", "Costs title", "Costs page title."
    ),
    _text(
        "pages.costs.subtitle",
        "Page Titles & Subtitles",
        "Estimated energy costs for permitted sites.",
        "Costs subtitle",
        "Costs page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.rates.title",
        "Page Titles & Subtitles",
        "Rate plans",
        "Rates title",
        "Rates page title.",
    ),
    _text(
        "pages.rates.subtitle",
        "Page Titles & Subtitles",
        "Effective-dated, source-backed versions preserve historical estimates while new "
        "utility changes remain reviewable.",
        "Rates subtitle",
        "Rates page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.rate_sources.title",
        "Page Titles & Subtitles",
        "SCE rate sources",
        "Rate Sources title",
        "Rate Sources page title.",
    ),
    _text(
        "pages.rate_sources.subtitle",
        "Page Titles & Subtitles",
        "Approved sources are fetched, hashed, archived, parsed, and compared. No candidate "
        "changes an active rate without the configured approval workflow.",
        "Rate Sources subtitle",
        "Rate Sources page subtitle.",
        max_length=360,
    ),
    _text(
        "pages.alerts.title",
        "Page Titles & Subtitles",
        "Alerts & Notifications",
        "Alerts title",
        "Alerts page title.",
    ),
    _text(
        "pages.alerts.subtitle",
        "Page Titles & Subtitles",
        "Review operational alerts and notification delivery.",
        "Alerts subtitle",
        "Alerts page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.enrollment.title",
        "Page Titles & Subtitles",
        "Multi-device enrollment",
        "Sensor Enrollment title",
        "Enrollment page title.",
    ),
    _text(
        "pages.enrollment.subtitle",
        "Page Titles & Subtitles",
        "Prepare a separate short-lived, single-use token for every ESP32 sensor.",
        "Sensor Enrollment subtitle",
        "Enrollment page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.backups.title",
        "Page Titles & Subtitles",
        "Backups",
        "Backups title",
        "Backups page title.",
    ),
    _text(
        "pages.backups.subtitle",
        "Page Titles & Subtitles",
        "Verified logical backups, restores, and redacted log exports.",
        "Backups subtitle",
        "Backups page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.users_access.title",
        "Page Titles & Subtitles",
        "Users & Access",
        "Users & Access title",
        "Users and Access page title.",
    ),
    _text(
        "pages.users_access.subtitle",
        "Page Titles & Subtitles",
        "Manage user roles, permissions, site access, account status, and active sessions.",
        "Users & Access subtitle",
        "Users and Access page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.interface_text.title",
        "Page Titles & Subtitles",
        "Dashboard & Login Text",
        "Dashboard & Login Text title",
        "Interface text page title.",
    ),
    _text(
        "pages.interface_text.subtitle",
        "Page Titles & Subtitles",
        "Customize approved interface labels and messages without changing application routes "
        "or security behavior.",
        "Dashboard & Login Text subtitle",
        "Interface text page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.status_indicators.title",
        "Page Titles & Subtitles",
        "Status Indicators & Layout",
        "Status Indicators & Layout title",
        "Status indicator administration page title.",
    ),
    _text(
        "pages.status_indicators.subtitle",
        "Page Titles & Subtitles",
        "Choose which status indicators are visible, where they appear, and how the dashboard "
        "reorganizes them across screen sizes.",
        "Status Indicators & Layout subtitle",
        "Status indicator administration page subtitle.",
        max_length=300,
    ),
    _text(
        "pages.administration.title",
        "Page Titles & Subtitles",
        "Administration",
        "Administration title",
        "Administration page title.",
    ),
    _text(
        "pages.administration.subtitle",
        "Page Titles & Subtitles",
        "Manage local users, site boundaries, verified backups, security evidence, and server "
        "health.",
        "Administration subtitle",
        "Administration page subtitle.",
        max_length=300,
    ),
    _text(
        "footer.dashboard",
        "Footer & Support",
        "Power Monitor Server",
        "Dashboard footer text",
        "Text shown at the bottom of the authenticated dashboard.",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=160,
        preview_location="footer",
    ),
    _text(
        "footer.support_label",
        "Footer & Support",
        "Support",
        "Dashboard support link label",
        "Label for the dashboard support link.",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=80,
        url_companion=True,
        preview_location="footer",
    ),
    _text(
        "footer.support_url",
        "Footer & Support",
        "",
        "Dashboard support URL",
        "HTTPS or mailto support destination.",
        field_type="url",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=500,
        preview_location="footer",
    ),
    _text(
        "footer.copyright",
        "Footer & Support",
        "",
        "Copyright or organization text",
        "Optional organization attribution.",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=160,
        preview_location="footer",
    ),
    _text(
        "footer.banner",
        "Footer & Support",
        "",
        "Informational banner",
        "Optional non-security informational banner.",
        required=False,
        blank_allowed=True,
        min_length=0,
        max_length=300,
        preview_location="banner",
    ),
)

TEXT_CATALOG = {item.key: item for item in TEXT_DEFINITIONS}
PUBLIC_TEXT_KEYS = frozenset(item.key for item in TEXT_DEFINITIONS if item.visibility == "public")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HTML_LIKE = re.compile(r"<\s*/?\s*[a-zA-Z!][^>]*>")


def normalize_text_value(definition: TextDefinition, value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not definition.line_breaks:
        normalized = " ".join(normalized.split())
    if CONTROL_CHARACTERS.search(normalized):
        raise ProblemError(
            422,
            "Invalid interface text",
            f"{definition.label} contains unsupported control characters",
            "interface_text_control_characters",
        )
    if HTML_LIKE.search(normalized) or "{{" in normalized or "{%" in normalized:
        raise ProblemError(
            422,
            "Executable text is not allowed",
            f"{definition.label} must be plain text without HTML or templates",
            "interface_text_markup_forbidden",
        )
    if not normalized and definition.required and not definition.blank_allowed:
        raise ProblemError(
            422,
            "Required text is blank",
            f"{definition.label} cannot be blank",
            "interface_text_required",
        )
    if len(normalized) < definition.min_length or len(normalized) > definition.max_length:
        raise ProblemError(
            422,
            "Interface text length is invalid",
            f"{definition.label} must contain {definition.min_length} to "
            f"{definition.max_length} characters",
            "interface_text_length",
        )
    if definition.field_type == "url" and normalized:
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"https", "mailto"}:
            raise ProblemError(
                422,
                "Unsafe support URL",
                "Support URLs must use https: or mailto:",
                "interface_text_url_scheme",
            )
        if parsed.username or parsed.password:
            raise ProblemError(
                422,
                "Unsafe support URL",
                "Support URLs cannot contain credentials",
                "interface_text_url_credentials",
            )
        if parsed.scheme == "https" and not parsed.hostname:
            raise ProblemError(
                422,
                "Invalid support URL",
                "HTTPS support URLs require a hostname",
                "interface_text_url_invalid",
            )
    return normalized


def validate_text_values(values: dict[str, str], *, complete: bool = False) -> dict[str, str]:
    unknown = sorted(set(values) - set(TEXT_CATALOG))
    if unknown:
        raise ProblemError(
            422,
            "Unknown interface text key",
            "Only server-registered text keys may be changed",
            "interface_text_key_unknown",
            extra={"keys": unknown},
        )
    normalized = {
        key: normalize_text_value(TEXT_CATALOG[key], value) for key, value in values.items()
    }
    if complete:
        merged = compiled_defaults()
        merged.update(normalized)
        for key, definition in TEXT_CATALOG.items():
            normalize_text_value(definition, merged[key])
    return normalized


def compiled_defaults() -> dict[str, str]:
    return {item.key: item.default for item in TEXT_DEFINITIONS}


async def current_revision(
    session: AsyncSession,
) -> tuple[int, dict[str, str], InterfaceTextRevision | None]:
    state = await session.get(InterfaceTextState, "current")
    if state is None or state.current_revision_id is None:
        return 0, {}, None
    revision = await session.get(InterfaceTextRevision, state.current_revision_id)
    if revision is None:
        return 0, {}, None
    return revision.revision, dict(revision.values), revision


async def current_text_payload(
    session: AsyncSession, *, public_only: bool = False
) -> dict[str, object]:
    revision, overrides, _row = await current_revision(session)
    values = compiled_defaults()
    values.update(overrides)
    if public_only:
        values = {key: values[key] for key in sorted(PUBLIC_TEXT_KEYS)}
    return {"revision": revision, "values": values}


async def catalog_payload(session: AsyncSession) -> dict[str, object]:
    revision, overrides, _row = await current_revision(session)
    return {
        "revision": revision,
        "definitions": [
            {
                **asdict(item),
                "current_override": overrides.get(item.key),
                "current_value": overrides.get(item.key, item.default),
                "published_revision": revision,
            }
            for item in TEXT_DEFINITIONS
        ],
    }
