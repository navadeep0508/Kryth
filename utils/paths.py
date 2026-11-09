from pathlib import Path
import codecs


def resolve_path(base: str | Path, path: str | Path) -> Path:
    base_path = Path(base).resolve()
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (base_path / candidate).resolve()
    try:
        resolved.relative_to(base_path)
    except ValueError as exc:
        raise ValueError(f"Path is outside the working directory: {path}") from exc
    return resolved

def display_path_relative_to_cwd(path: str, cwd: Path | None) -> str:
    try:
        p = Path(path)
    except Exception:
        return path

    if cwd:
        try:
            return str(p.relative_to(cwd))
        except ValueError:
            pass

    return str(p)


# Text file encodings to try, in order of preference.
_TEXT_ENCODINGS = ["utf-8-sig", "utf-8", "utf-16", "latin-1"]

# Byte Order Marks for common encodings.
_BOM_MARKERS = {
    b"\xef\xbb\xbf": "utf-8-sig",   # UTF-8 BOM
    b"\xff\xfe": "utf-16-le",       # UTF-16 LE BOM
    b"\xfe\xff": "utf-16-be",       # UTF-16 BE BOM
}

# Bytes that strongly indicate binary content in the first 8KB.
_BINARY_BYTES = frozenset({
    0x00,  # NUL
    0x01,  # SOH
    0x02,  # STX
    0x03,  # ETX
    0x04,  # EOT
    0x05,  # ENQ
    0x06,  # ACK
    0x07,  # BEL
    0x0B,  # VT
    0x0E,  # SO
    0x0F,  # SI
    0x10,  # DLE
    0x11,  # DC1
    0x12,  # DC2
    0x13,  # DC3
    0x14,  # DC4
    0x15,  # NAK
    0x16,  # SYN
    0x17,  # ETB
    0x18,  # CAN
    0x19,  # EM
    0x1A,  # SUB (Ctrl-Z)
    0x1C,  # FS
    0x1D,  # GS
    0x1E,  # RS
    0x1F,  # US
})

# Common text file extensions that should never be treated as binary
# solely based on content heuristics.
_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".rst", ".log", ".csv", ".tsv", ".ini", ".cfg", ".conf",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".html", ".htm", ".css",
    ".js", ".ts", ".jsx", ".tsx", ".py", ".sh", ".bash", ".zsh", ".bat",
    ".cmd", ".ps1", ".dockerfile", ".env", ".gitignore", ".gitattributes",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".swift", ".pl", ".r", ".m", ".mm", ".scala",
    ".sql", ".graphql", ".proto", ".gradle", ".properties",
})


def _detect_encoding(data: bytes) -> str | None:
    """Detect text encoding from BOM or byte patterns."""
    # Check BOM markers first.
    for bom, encoding in _BOM_MARKERS.items():
        if data.startswith(bom):
            return encoding

    # Heuristic: try UTF-8 validation.
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # Try UTF-16 (with or without BOM).
    try:
        data.decode("utf-16")
        return "utf-16"
    except UnicodeDecodeError:
        pass

    return None


def is_binary_file(path: str | Path) -> bool:
    """Detect whether a file is binary using content inspection.

    A file is considered binary when:
    - It contains null bytes or control characters (excluding common
      whitespace: tab, newline, carriage return).
    - Its encoding cannot be determined as UTF-8, UTF-16, or latin-1.

    Files with known text extensions are never classified as binary solely
    based on a decoding error — a safe fallback decoder is used instead.
    """
    path = Path(path)

    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
    except (OSError, IOError):
        return False

    if not chunk:
        # Empty file is text.
        return False

    # Check for BOM or detectable encoding.
    encoding = _detect_encoding(chunk)
    if encoding is None:
        # Could not detect any text encoding — likely binary.
        return True

    # For UTF-16, null bytes are expected (every other byte is 0x00 for ASCII).
    # Skip the null-byte check when UTF-16 is detected.
    is_utf16 = encoding.startswith("utf-16")

    # Scan for binary control bytes (excluding common whitespace).
    for byte in chunk:
        if byte in _BINARY_BYTES:
            # Null bytes are expected in UTF-16 encoded text.
            if is_utf16 and byte == 0x00:
                continue
            return True

    # If we get here, the file decodes as text and has no binary markers.
    return False


def read_text_safe(path: str | Path) -> str:
    """Read a file as text, trying multiple encodings with a safe fallback.

    Never raises UnicodeDecodeError — falls back to latin-1 (which can
    decode any byte sequence) if no other encoding works.
    """
    path = Path(path)
    with open(path, "rb") as f:
        data = f.read()

    if not data:
        return ""

    encoding = _detect_encoding(data)
    if encoding:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass

    # Safe fallback: latin-1 decodes any byte sequence without error.
    return data.decode("latin-1", errors="replace")


def ensure_parent_dir(path: str | Path) -> Path:
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    return path