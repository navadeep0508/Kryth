"""JSON-schema tool specs for streaming file write tools (Rule 16)."""

STREAM_WRITE_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "write_file_begin",
            "description": (
                "Begin a streaming write session for a large file. "
                "Use this instead of write_file when the file will be generated in multiple chunks "
                "(e.g. files > 200 lines). "
                "Creates the file empty, then call write_file_chunk() to append content progressively, "
                "and write_file_finalize() when done. "
                "The dashboard shows a live progress bar. "
                "Parallel streams on different paths are fully supported."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to create or truncate.",
                    },
                    "estimated_lines": {
                        "type": "integer",
                        "description": "Approximate final line count — used for the progress bar. Pass 0 if unknown.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file_chunk",
            "description": (
                "Append a content chunk to an active streaming write session. "
                "Call this after write_file_begin(). Can be called many times. "
                "Each chunk triggers background AST/syntax validation and updates the dashboard progress bar. "
                "Chunks should be 30-80 lines each for best progress granularity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Must match a path passed to write_file_begin.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Raw text to append. Include trailing newlines.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file_finalize",
            "description": (
                "Finalize a streaming write session. "
                "Flushes the file, closes the stream slot, and runs a final background validation. "
                "Must be called after the last write_file_chunk(). "
                "After this call the file is complete and can be read/edited normally."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Must match a path passed to write_file_begin.",
                    },
                },
                "required": ["path"],
            },
        },
    },
]
