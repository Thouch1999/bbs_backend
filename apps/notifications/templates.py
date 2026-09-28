"""
Bilingual (km default, en fallback) content for every notification template,
per docs/SRS.md §7.16/4.5. Not Django gettext: these are full message bodies
sent to passengers over SMS/email/Telegram, not UI labels, so each language's
copy is written out in full rather than translated string-by-string.

Each entry is {"subject": ..., "body": ...} with `{placeholders}` filled by
`render()`'s context. subject is unused by channels that don't have one
(SMS/Telegram) — callers pass it through anyway for a uniform call shape.
"""

from apps.notifications.models import Notification

_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    Notification.Template.BOOKING_CONFIRMATION: {
        "km": {
            "subject": "កក់សំបុត្រជោគជ័យ — {pnr}",
            "body": (
                "សួស្តី {passenger_name}, ការកក់សំបុត្ររបស់អ្នកបានជោគជ័យ។\n"
                "លេខកក់ (PNR): {pnr}\n"
                "ផ្លូវ: {route_name}\n"
                "ថ្ងៃចេញដំណើរ: {departure_at}\n"
                "តម្លៃសរុប: {total_amount} {currency}\n"
                "សូមរក្សាទុកលេខកក់នេះសម្រាប់ការឡានឡើងឡាន។"
            ),
        },
        "en": {
            "subject": "Booking confirmed — {pnr}",
            "body": (
                "Hi {passenger_name}, your booking is confirmed.\n"
                "PNR: {pnr}\n"
                "Route: {route_name}\n"
                "Departure: {departure_at}\n"
                "Total paid: {total_amount} {currency}\n"
                "Keep this PNR handy for boarding."
            ),
        },
    },
    Notification.Template.PAYMENT_RECEIPT: {
        "km": {
            "subject": "បង្កាន់ដៃទូទាត់ប្រាក់ — {pnr}",
            "body": (
                "សូមអរគុណ {passenger_name}, យើងបានទទួលការទូទាត់របស់អ្នកសម្រាប់ការកក់ {pnr}។\n"
                "ចំនួនទឹកប្រាក់៖ {total_amount} {currency}\n"
                "វិធីទូទាត់៖ {provider}"
            ),
        },
        "en": {
            "subject": "Payment receipt — {pnr}",
            "body": (
                "Thank you {passenger_name}, we've received your payment for booking {pnr}.\n"
                "Amount: {total_amount} {currency}\n"
                "Method: {provider}"
            ),
        },
    },
    Notification.Template.DEPARTURE_REMINDER: {
        "km": {
            "subject": "ការរំលឹកមុនចេញដំណើរ — {pnr}",
            "body": (
                "សួស្តី {passenger_name}, ឡានរបស់អ្នកសម្រាប់ការកក់ {pnr} "
                "នឹងចេញដំណើរនៅ {departure_at} ពីផ្លូវ {route_name}។ សូមមកដល់មុនម៉ោង។"
            ),
        },
        "en": {
            "subject": "Departure reminder — {pnr}",
            "body": (
                "Hi {passenger_name}, your bus for booking {pnr} departs at {departure_at} "
                "on route {route_name}. Please arrive early."
            ),
        },
    },
    Notification.Template.TRIP_DELAYED: {
        "km": {
            "subject": "ការធ្វើដំណើរត្រូវពន្យារពេល — {pnr}",
            "body": (
                "សូមជម្រាបជូន {passenger_name}, ដំណើររបស់អ្នកសម្រាប់ការកក់ {pnr} "
                "ត្រូវពន្យារពេល {delay_minutes} នាទី។ ម៉ោងចេញដំណើរថ្មី៖ {departure_at}។"
            ),
        },
        "en": {
            "subject": "Your trip is delayed — {pnr}",
            "body": (
                "Hi {passenger_name}, your trip for booking {pnr} is delayed by "
                "{delay_minutes} minutes. New departure: {departure_at}."
            ),
        },
    },
    Notification.Template.TRIP_CANCELLED: {
        "km": {
            "subject": "ដំណើរត្រូវបានលុបចោល — {pnr}",
            "body": (
                "សូមជម្រាបជូន {passenger_name}, ដំណើររបស់អ្នកសម្រាប់ការកក់ {pnr} "
                "ត្រូវបានលុបចោលដោយ{cancellation_reason}។ ការសងប្រាក់វិញនឹងដំណើរការ។"
            ),
        },
        "en": {
            "subject": "Your trip was cancelled — {pnr}",
            "body": (
                "Hi {passenger_name}, your trip for booking {pnr} was cancelled: "
                "{cancellation_reason}. A refund will be processed."
            ),
        },
    },
    Notification.Template.REFUND_PROCESSED: {
        "km": {
            "subject": "ការសងប្រាក់វិញត្រូវបានដំណើរការ — {pnr}",
            "body": (
                "សូមជម្រាបជូន {passenger_name}, ការសងប្រាក់វិញចំនួន {amount} {currency} "
                "សម្រាប់ការកក់ {pnr} ត្រូវបានដំណើរការរួចរាល់។"
            ),
        },
        "en": {
            "subject": "Refund processed — {pnr}",
            "body": (
                "Hi {passenger_name}, a refund of {amount} {currency} for booking {pnr} "
                "has been processed."
            ),
        },
    },
}


_TEMPLATES[Notification.Template.SUPPORT_REPLY] = {
    "km": {
        "subject": "ចម្លើយពីផ្នែកជំនួយ BBMS — {subject}",
        "body": "សួស្តី {customer_name}, ផ្នែកជំនួយ BBMS បានឆ្លើយតបសំណើរបស់អ្នក៖\n{reply}",
    },
    "en": {
        "subject": "BBMS support replied — {subject}",
        "body": "Hi {customer_name}, BBMS support replied to your request:\n{reply}",
    },
}


def render(template: str, language: str, context: dict) -> dict:
    """Returns {"subject": ..., "body": ...} rendered in `language`, defaulting
    to Khmer for anything not km/en, per SRS: km default, en fallback."""
    lang = language if language in ("km", "en") else "km"
    try:
        strings = _TEMPLATES[template][lang]
    except KeyError as exc:
        raise ValueError(f"No template '{template}' for language '{lang}'.") from exc
    return {key: value.format(**context) for key, value in strings.items()}
