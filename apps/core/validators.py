import os

from django.core.exceptions import ValidationError

ALLOWED_LICENCE_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_LICENCE_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Magic-byte signatures per extension, checked against the file's actual
# content rather than trusting the extension or the client-supplied
# content-type — rejects e.g. an .exe/.html renamed to .pdf.
_LICENCE_DOCUMENT_SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}


def validate_licence_document(file):
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in ALLOWED_LICENCE_DOCUMENT_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '{ext}'. Allowed types: PDF, JPG, PNG."
        )

    if file.size > MAX_LICENCE_DOCUMENT_SIZE_BYTES:
        raise ValidationError("File too large. Maximum size is 10 MB.")

    file.seek(0)
    header = file.read(8)
    file.seek(0)
    signatures = _LICENCE_DOCUMENT_SIGNATURES[ext]
    if not any(header.startswith(sig) for sig in signatures):
        raise ValidationError("File content does not match its extension.")


ALLOWED_BANNER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_BANNER_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

_BANNER_IMAGE_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
}


def validate_banner_image(file):
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in ALLOWED_BANNER_IMAGE_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '{ext}'. Allowed types: JPG, PNG, WEBP."
        )

    if file.size > MAX_BANNER_IMAGE_SIZE_BYTES:
        raise ValidationError("File too large. Maximum size is 5 MB.")

    file.seek(0)
    header = file.read(12)
    file.seek(0)
    signatures = _BANNER_IMAGE_SIGNATURES[ext]
    if not any(header.startswith(sig) for sig in signatures):
        raise ValidationError("File content does not match its extension.")
