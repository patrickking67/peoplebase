"""Rules and file handling for employee documents.

Nothing here knows about Flask routes or the database. A file is accepted only
when its extension and its first bytes agree on one of three types, it is
stored under a random name that the uploader never influences, and the name
they gave is kept as text for display.
"""

import re
import secrets
from pathlib import Path

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Multipart framing and the other form fields on top of the file itself.
UPLOAD_OVERHEAD_BYTES = 64 * 1024
MAX_DOCUMENTS_PER_EMPLOYEE = 50
MAX_ORIGINAL_NAME_LENGTH = 150

# type -> (the extensions a file of that type may carry, the first bytes of such a file, content type)
TYPES = {
    "pdf": ((".pdf",), (b"%PDF-",), "application/pdf"),
    "png": ((".png",), (b"\x89PNG\r\n\x1a\n",), "image/png"),
    "jpeg": ((".jpg", ".jpeg"), (b"\xff\xd8\xff",), "image/jpeg"),
}
STORED_EXTENSIONS = {"pdf": ".pdf", "png": ".png", "jpeg": ".jpg"}
STORED_NAME = re.compile(r"[0-9a-f]{32}\.(?:pdf|png|jpg)")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def clean_original_name(name):
    """The file name as text for display: no directories, no control characters."""
    name = CONTROL_CHARACTERS.sub("", name or "")
    name = re.split(r"[\\/]", name)[-1].strip().strip(".")
    return name[:MAX_ORIGINAL_NAME_LENGTH]


def sniff(data):
    """The type named by the first bytes of a file, or None."""
    for kind, (_, signatures, _) in TYPES.items():
        if data.startswith(signatures):
            return kind
    return None


def check_upload(name, data):
    """Return (kind, error). The extension and the content must name the same type."""
    if not data:
        return None, "The file is empty."
    if len(data) > MAX_UPLOAD_BYTES:
        return None, "The file is larger than 10 MB."
    suffix = Path(clean_original_name(name)).suffix.lower()
    allowed = [ext for extensions, _, _ in TYPES.values() for ext in extensions]
    if suffix not in allowed:
        return None, "Upload a PDF, PNG or JPEG file."
    kind = sniff(data)
    if kind is None or suffix not in TYPES[kind][0]:
        return None, "The file's contents do not match its extension. Upload a real PDF, PNG or JPEG."
    return kind, None


def content_type(kind):
    return TYPES[kind][2]


def uploads_directory(instance_path):
    """instance/uploads, created private to the app's user. Never under static/."""
    directory = Path(instance_path) / "uploads"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def store(instance_path, data, kind):
    """Write the bytes under a random name and return that name."""
    stored_name = secrets.token_hex(16) + STORED_EXTENSIONS[kind]
    (uploads_directory(instance_path) / stored_name).write_bytes(data)
    return stored_name


def path_for(instance_path, stored_name):
    """The file's path, or None for anything that is not a name this module made."""
    if not STORED_NAME.fullmatch(stored_name or ""):
        return None
    directory = uploads_directory(instance_path).resolve()
    path = (directory / stored_name).resolve()
    if path.parent != directory:
        return None
    return path


def remove(instance_path, stored_name):
    """Delete a stored file. A file that is already gone is fine."""
    path = path_for(instance_path, stored_name)
    if path is not None:
        path.unlink(missing_ok=True)
