"""Streaming renderer for KRYTH responses.

Architecture
------------

TagParser   — generic state machine over a configurable set of tag specs.
              Splits raw chunk bytes into a sequence of (state, text) events.
              Stateless between calls; all cross-chunk state lives in
              TagParserState (a plain dataclass the caller owns).

BlockRenderer — maps TagParser events to terminal output.
                Owns the visual treatment for each tag (think / plan / …).
                Swappable per tag-spec without touching the parser.

StreamPrinter — the public surface. Orchestrates TagParser + BlockRenderer
                alongside the existing reasoning-spinner path (for models
                that expose a native reasoning field rather than <think>).

Adding a new tag
----------------
1. Register a TagSpec in _TAG_SPECS (name, open_tag, close_tag,
   header, glyph, ansi_color).
2. Done. The parser, state machine, and renderer all handle it
   automatically.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, Iterator

from agent.ui.console import console
from agent.ui.motion import motion_enabled, sleep, DIAMOND_THINKING_FRAMES
from agent.ui.theme import CORE


# ── ANSI palette ─────────────────────────────────────────────────────────────

_ANSI_RESET  = "\033[0m"
_ANSI_BOLD   = "\033[1m"
_ANSI_DIM    = "\033[2m"
_ANSI_GOLD   = "\033[38;2;232;255;58m"   # kryth.core  #E8FF3A
_ANSI_CYAN   = "\033[38;2;100;200;240m"  # thinking    #64C8F0
_ANSI_VIOLET = "\033[38;2;180;140;255m"  # plan        #B48CFF
_ANSI_AMBER  = "\033[38;2;255;180;50m"   # warning     #FFB432
_ANSI_GREEN  = "\033[38;2;74;222;128m"   # success     #4ADE80
_ANSI_RED    = "\033[38;2;255;90;90m"    # error       #FF5A5A
_ANSI_TEAL   = "\033[38;2;64;200;180m"   # exec stream #40C8B4
_ANSI_MUTED  = "\033[38;2;136;136;136m"
_ANSI_GHOST  = "\033[38;2;48;48;48m"     # narration dim — nearly invisible on dark bg

# Lines starting with these patterns are first-person LLM narration.
# We don't suppress them (they may help developers debug) but render
# them as near-invisible ghost text so they don't distract the user.
_NARRATION_RE = re.compile(
    r"^[ \t]*("
    r"I[' ]?(?:ll|'ll|'m|'ve|'d| will| am| need| should| can| see| know| have| noticed| found| realize| want| think| believe| understand)\b|"
    r"(?:Let|Let's|Lets)(?:'s| me)\b|"
    r"(?:Now|Next|First|Then|Finally|After that|After this|Before that)[,.]?\s+(?:I|let me|let's|we)\b|"
    r"(?:Now|Next) (?:I'm|I'll|I need|I should|I can)\b|"
    r"(?:Looking|Checking|Reading|Writing|Running|Building|Testing|Examining|Analyzing|"
    r"Reviewing|Updating|Creating|Editing|Searching|Scanning|Verifying|Installing|"
    r"Deploying|Attempting|Starting|Opening|Loading|Executing|Fetching|Comparing|"
    r"Responding|Proceeding|Providing|Returning|Sending|Using|Calling|Getting|"
    r"Setting|Handling|Processing|Preparing|Generating|Crafting)\b|"
    r"(?:Based on|Looking at|Given that|Since the|Now that|To (?:do|complete|handle|respond|answer|help|address|start|begin|proceed))\b|"
    r"(?:The (?:model|agent|assistant|task|request|project|code|file|result|output|response|answer) (?:is|has|was|should|needs|requires|must))\b"
    r")",
    re.IGNORECASE,
)

# Lines that are model-generated fake UI panels (box-drawing characters).
_BOX_LINE_RE = re.compile(r"^[ \t]*[╭╮╰╯│├┤┬┴┼╔╗╚╝╠╣╦╩╬]")

# Raw tag lines that escaped the streaming parser — ghost-text them so they
# are invisible rather than shown as `</think>` or `<display>...` raw text.
_RAW_TAG_RE = re.compile(
    r"^[ \t]*(?:"
    r"<\|[a-z_]+\|>"                              # <|python_tag|> and similar
    r"|<tool\s+name="                             # <tool name="..." /> XML format
    r"|</?seed:[a-z_]+"                           # <seed:think>, <seed:tool_call>, etc.
    r"|</?(?:think(?:ing)?|reasoning|analysis|reflect(?:ion)?|"
    r"display|status|mission|timeline|spinner|todo|summary|memory|"
    r"experience|health|budget|risk|block|activity|exec_stream|"
    r"build_stream|test_stream|tool_result|file_read|file_write|"
    r"file_edit|checkpoint|agent|team|decision|command)[>\s/]"
    r")",
    re.IGNORECASE,
)

# Inline occurrences of special tokens anywhere in a line — strip before render.
_SPECIAL_TOKEN_RE = re.compile(r"<\|[a-z_]+\|>", re.IGNORECASE)

# Left-border glyph rendered before every thinking/plan line
_BORDER = "│"

_PULSE_STYLES = [
    "\033[38;2;136;136;136m",
    "\033[38;2;180;180;180m",
    "\033[38;2;220;220;220m",
    "\033[38;2;255;255;255m",
    "\033[38;2;220;220;220m",
    "\033[38;2;180;180;180m",
]


# ── Tag specification ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TagSpec:
    """Everything the parser and renderer need for one tag type."""
    name: str           # logical name, e.g. "thinking"
    open_tag: str       # literal open tag, e.g. "<think>"
    close_tag: str      # literal close tag, e.g. "</think>"
    header: str         # header line printed when the block opens
    glyph: str          # icon used in header
    ansi_color: str     # ANSI escape for block text
    show_live: bool = True  # False = suppress silently (tool_call, function, parameter)


# The canonical set of known tags.  Add rows here to support new block types.
_TAG_SPECS: tuple[TagSpec, ...] = (
    # Runtime v2: the UI tag protocol is retired. Models emit plain text and
    # native tool calls; the UI is driven by the event bus. The parser keeps
    # ONLY a leak-stripping safety net — reasoning blocks and stray tool-call
    # XML / special tokens are consumed and hidden so they never reach the
    # terminal. Everything else passes through as plain text.
    #
    # All entries are show_live=False (silent strip). No accumulate-and-render
    # display tags remain.
    TagSpec(name="thinking",      open_tag="<think>",         close_tag="</think>",         header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="thinking_alt",  open_tag="<thinking>",      close_tag="</thinking>",      header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="seed_think",    open_tag="<seed:think>",    close_tag="</seed:think>",    header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="reasoning",     open_tag="<reasoning>",     close_tag="</reasoning>",     header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="analysis",      open_tag="<analysis>",      close_tag="</analysis>",      header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="reflect",       open_tag="<reflect>",       close_tag="</reflect>",       header="", glyph="", ansi_color="", show_live=False),
    # Content boundary marker — consumed silently.
    TagSpec(name="content_boundary", open_tag="<CONTENT>",    close_tag="</CONTENT>",       header="", glyph="", ansi_color="", show_live=False),
    # Stray native-tool-call XML internals — never user-visible.
    TagSpec(name="tool_call",     open_tag="<tool_call>",     close_tag="</tool_call>",     header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="seed_tool_call",open_tag="<seed:tool_call>",close_tag="</seed:tool_call>",header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="function",      open_tag="<function=",      close_tag="</function>",      header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="parameter",     open_tag="<parameter=",     close_tag="</parameter>",     header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="tool_name",     open_tag="<tool_name>",     close_tag="</tool_name>",     header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="tool_use",      open_tag="<tool_use>",      close_tag="</tool_use>",      header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="invoke",        open_tag="<invoke>",        close_tag="</invoke>",        header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="parameters_block", open_tag="<parameters>", close_tag="</parameters>",    header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="arguments",     open_tag="<arguments>",     close_tag="</arguments>",     header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="input_block",   open_tag="<input>",         close_tag="</input>",         header="", glyph="", ansi_color="", show_live=False),
    # Silent internal channels.
    TagSpec(name="kryth_internal",open_tag="<internal>",      close_tag="</internal>",      header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="kryth_debug",   open_tag="<debug>",         close_tag="</debug>",         header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="kryth_context", open_tag="<context>",       close_tag="</context>",       header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="kryth_memraw",  open_tag="<memory_raw>",    close_tag="</memory_raw>",    header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="kryth_sysprompt",open_tag="<prompt>",       close_tag="</prompt>",        header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="kryth_agentmsg",open_tag="<agent_message>", close_tag="</agent_message>", header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="kryth_apireq",  open_tag="<api_request>",   close_tag="</api_request>",   header="", glyph="", ansi_color="", show_live=False),
    TagSpec(name="kryth_apiresp", open_tag="<api_response>",  close_tag="</api_response>",  header="", glyph="", ansi_color="", show_live=False),
)

# Pre-built lookups (lowercase for case-insensitive matching)
_SPEC_BY_OPEN:  dict[str, TagSpec] = {s.open_tag.lower(): s for s in _TAG_SPECS}
_SPEC_BY_CLOSE: dict[str, TagSpec] = {s.close_tag.lower(): s for s in _TAG_SPECS}

# Regex that matches any open OR close tag from the registry
_ALL_TAGS_RE = re.compile(
    "|".join(
        re.escape(t)
        for s in _TAG_SPECS
        for t in (s.open_tag, s.close_tag)
    ),
    re.IGNORECASE,
)

# ── Tag rendering retired (Runtime v2) ────────────────────────────────────────
# The model no longer emits UI tags; the UI is rendered from the event bus.
# The accumulate-and-render tag handlers (and their _tr_* helpers) have been
# removed. The parser keeps only the silent leak-stripping specs above, so any
# stray reasoning / tool-XML is consumed and hidden, and all other text passes
# through as plain output.
_TAG_RENDERERS: Dict[str, Callable[[str], None]] = {}

# Tags whose content is accumulated silently and rendered on close. Empty now —
# no display tags remain.
_ACC_TAGS: frozenset = frozenset()


# ── Parser state ──────────────────────────────────────────────────────────────

class _Mode(Enum):
    NORMAL   = auto()
    IN_BLOCK = auto()   # inside a visible block (think / plan / warning)
    SILENT   = auto()   # inside a silent block (tool_call / function / parameter)


@dataclass
class TagParserState:
    """Mutable cross-chunk parser state. One instance per StreamPrinter."""
    mode: _Mode = _Mode.NORMAL
    active_spec: TagSpec | None = None
    # Partial tag accumulator: when a chunk ends mid-tag we hold the fragment
    partial: str = ""
    # Lines emitted in the current block (for the closing visual)
    block_line_count: int = 0


# ── Parser events ─────────────────────────────────────────────────────────────

class EventKind(Enum):
    NORMAL_TEXT  = auto()   # plain content text → render as normal output
    BLOCK_OPEN   = auto()   # a visible block just opened
    BLOCK_TEXT   = auto()   # text inside a visible block
    BLOCK_CLOSE  = auto()   # a visible block just closed
    # Silent events are fully suppressed — no event emitted to caller


@dataclass
class ParseEvent:
    kind: EventKind
    text: str = ""
    spec: TagSpec | None = None


# ── TagParser ─────────────────────────────────────────────────────────────────

class TagParser:
    """Generic streaming tag parser.

    Call feed(chunk, state) for each incoming content chunk.
    Yields ParseEvent objects that the renderer acts on.
    The parser itself is stateless — all mutable state lives in
    TagParserState so callers can manage lifetime independently.
    """

    def feed(
        self,
        chunk: str,
        state: TagParserState,
    ) -> Iterator[ParseEvent]:
        """Parse one chunk and yield zero or more ParseEvents."""
        # Prepend any partial tag fragment from the previous chunk
        text = state.partial + chunk
        state.partial = ""

        pos = 0
        while pos < len(text):
            # --- Inside a block -------------------------------------------
            if state.mode in (_Mode.IN_BLOCK, _Mode.SILENT):
                close_tag = state.active_spec.close_tag  # type: ignore[union-attr]
                idx = text.lower().find(close_tag.lower(), pos)
                if idx == -1:
                    # Check whether the tail might be a partial close tag
                    fragment = self._partial_match_suffix(text[pos:], close_tag)
                    if fragment:
                        # Yield everything up to the potential partial
                        payload = text[pos:len(text) - len(fragment)]
                        if payload and state.mode == _Mode.IN_BLOCK:
                            yield ParseEvent(EventKind.BLOCK_TEXT, payload, state.active_spec)
                        state.partial = fragment
                        return
                    # No close tag anywhere — yield the rest and stop
                    payload = text[pos:]
                    if payload and state.mode == _Mode.IN_BLOCK:
                        yield ParseEvent(EventKind.BLOCK_TEXT, payload, state.active_spec)
                    return
                # Found the close tag
                payload = text[pos:idx]
                if payload and state.mode == _Mode.IN_BLOCK:
                    yield ParseEvent(EventKind.BLOCK_TEXT, payload, state.active_spec)
                if state.mode == _Mode.IN_BLOCK:
                    yield ParseEvent(EventKind.BLOCK_CLOSE, "", state.active_spec)
                state.mode = _Mode.NORMAL
                state.active_spec = None
                state.block_line_count = 0
                pos = idx + len(close_tag)
                continue

            # --- NORMAL mode ----------------------------------------------
            m = _ALL_TAGS_RE.search(text, pos)
            if m is None:
                # No more tags — check for partial match at end
                fragment = self._partial_match_suffix(text[pos:], None)
                if fragment:
                    payload = text[pos:len(text) - len(fragment)]
                    if payload:
                        yield ParseEvent(EventKind.NORMAL_TEXT, payload)
                    state.partial = fragment
                    return
                payload = text[pos:]
                if payload:
                    yield ParseEvent(EventKind.NORMAL_TEXT, payload)
                return

            # Yield text before this tag
            before = text[pos:m.start()]
            if before:
                yield ParseEvent(EventKind.NORMAL_TEXT, before)

            matched = m.group(0)
            matched_lc = matched.lower()

            # Is it an open tag?
            spec = _SPEC_BY_OPEN.get(matched_lc)
            if spec is None:
                # partial open match (e.g. "<function=write_file>")
                for open_tag, s in _SPEC_BY_OPEN.items():
                    if matched_lc.startswith(open_tag.rstrip(">")):
                        spec = s
                        break

            if spec:
                if spec.show_live:
                    state.mode = _Mode.IN_BLOCK
                    yield ParseEvent(EventKind.BLOCK_OPEN, "", spec)
                else:
                    state.mode = _Mode.SILENT
                state.active_spec = spec
                state.block_line_count = 0
                pos = m.end()
                continue

            # Is it a close tag with no matching open? (orphan) — swallow it
            if matched_lc in _SPEC_BY_CLOSE:
                pos = m.end()
                continue

            # Unknown match — treat as normal text
            yield ParseEvent(EventKind.NORMAL_TEXT, matched)
            pos = m.end()

    @staticmethod
    def _partial_match_suffix(text: str, close_tag: str | None) -> str:
        """Return the longest suffix of text that could be the start of
        any known tag (or specifically close_tag).  Used to hold back
        partial tags that span chunk boundaries."""
        candidates = (
            [close_tag] if close_tag
            else [s.open_tag for s in _TAG_SPECS] + [s.close_tag for s in _TAG_SPECS]
        )
        for length in range(min(len(text), 20), 0, -1):
            suffix = text[-length:]
            for tag in candidates:
                if tag.lower().startswith(suffix.lower()):
                    return suffix
        return ""


# ── Block renderer ────────────────────────────────────────────────────────────

class BlockRenderer:
    """Renders tagged blocks to the terminal.

    Two rendering modes:
    • Streaming (existing tags like <think>, <plan>, <exec_stream>):
      text is written live with a colored left-border as it arrives.
    • Accumulate-and-render (KRYTH protocol tags like <status>, <tool_read>…):
      text is buffered silently; on block close the accumulated content is
      passed to the matching _TAG_RENDERERS entry for a clean one-shot render.
    """

    def __init__(self) -> None:
        self._block_open = False
        self._line_buf = ""      # partial line buffer for border rendering
        self._acc = ""           # accumulator for rich-close tags

    def render_block_open(self, spec: TagSpec) -> None:
        self._block_open = True
        self._line_buf = ""
        self._acc = ""
        if spec.name in _ACC_TAGS:
            return  # silent open — content accumulates, rendered on close
        _write_inplace(
            f"\n{_ANSI_BOLD}{spec.ansi_color}{spec.header}{_ANSI_RESET}\n"
        )

    def render_block_text(self, text: str, spec: TagSpec) -> None:
        if not text:
            return
        if spec.name in _ACC_TAGS:
            self._acc += text
            return
        color = spec.ansi_color
        full = self._line_buf + text
        self._line_buf = ""
        lines = full.split("\n")
        for i, line in enumerate(lines):
            is_last = i == len(lines) - 1
            if is_last and not full.endswith("\n"):
                self._line_buf = line
                break
            if line.strip():
                _write_inplace(
                    f"{_ANSI_DIM}{_ANSI_MUTED}{_BORDER}{_ANSI_RESET} "
                    f"{color}{line}{_ANSI_RESET}\n"
                )
            else:
                _write_inplace("\n")

    def render_block_close(self, spec: TagSpec) -> None:
        if spec.name in _ACC_TAGS:
            renderer = _TAG_RENDERERS.get(spec.name)
            if renderer:
                try:
                    renderer(self._acc.strip())
                except Exception:
                    pass
            self._acc = ""
            self._block_open = False
            return
        if self._line_buf.strip():
            color = spec.ansi_color
            _write_inplace(
                f"{_ANSI_DIM}{_ANSI_MUTED}{_BORDER}{_ANSI_RESET} "
                f"{color}{self._line_buf}{_ANSI_RESET}\n"
            )
        self._line_buf = ""
        self._block_open = False
        _write_inplace("\n")

    def close_gracefully(self, spec: TagSpec) -> None:
        if self._block_open:
            self.render_block_close(spec)

    def reset(self) -> None:
        self._block_open = False
        self._line_buf = ""
        self._acc = ""


# ── StreamPrinter ─────────────────────────────────────────────────────────────

_parallel_mode = False


def set_parallel_mode(enabled: bool) -> None:
    global _parallel_mode
    _parallel_mode = enabled


def _flush_threshold() -> int:
    try:
        cols = console.size.width
    except Exception:
        cols = 80
    return max(40, min(cols, 200))


def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _write_inplace(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass


def _apply_narration_filter(text: str) -> str:
    """Render first-person narration, fake panels, and raw tags as ghost text."""
    def _is_ghost(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        return bool(
            _NARRATION_RE.match(stripped)
            or _BOX_LINE_RE.match(line)
            or _RAW_TAG_RE.match(line)
        )

    if "\n" not in text:
        return f"{_ANSI_GHOST}{text}{_ANSI_RESET}" if _is_ghost(text) else text
    parts = text.split("\n")
    out: list[str] = []
    for i, part in enumerate(parts):
        sep = "" if i == len(parts) - 1 else "\n"
        if _is_ghost(part):
            out.append(f"{_ANSI_GHOST}{part}{_ANSI_RESET}{sep}")
        else:
            out.append(part + sep)
    return "".join(out)


class StreamPrinter:
    """Public streaming surface.

    Coordinates TagParser + BlockRenderer for in-content tagged blocks
    alongside the native reasoning-spinner path (for models that expose
    reasoning via a separate API field rather than <think> tags).
    """

    def __init__(self) -> None:
        self._reasoning_started = False
        self._content_started = False
        self._buf: list[str] = []
        self._buf_len = 0
        self._reasoning_frame = 0
        self._pulse_frame = 0
        # Tag parser + state
        self._parser = TagParser()
        self._parse_state = TagParserState()
        self._block_renderer = BlockRenderer()
        # Ghost-continuation: if a flush was ghost-texted mid-line, carry the
        # ghost style into the next flush until the line ends with \n.
        self._in_ghost_continuation = False

    # ── Existing reasoning-spinner path (native reasoning field) ─────────

    def begin_reasoning(self) -> None:
        if self._reasoning_started:
            return
        self._reasoning_started = True
        if _parallel_mode or not _is_tty():
            self._reasoning_frame = 0
            self._pulse_frame = 0
            return
        self._reasoning_frame = 0
        self._pulse_frame = 0
        glyph = DIAMOND_THINKING_FRAMES[0]
        label_style = _PULSE_STYLES[0]
        _write_inplace(
            f"\n{_ANSI_BOLD}{_ANSI_GOLD}{glyph}{_ANSI_RESET} "
            f"{label_style}Evaluating...{_ANSI_RESET}"
        )

    def reasoning_chunk(self, piece: str, elapsed: float = 0.0) -> None:
        if not self._reasoning_started:
            self.begin_reasoning()
        if not _parallel_mode and _is_tty():
            self._reasoning_frame = (self._reasoning_frame + 1) % len(DIAMOND_THINKING_FRAMES)
            self._pulse_frame = (self._pulse_frame + 1) % len(_PULSE_STYLES)
            glyph = DIAMOND_THINKING_FRAMES[self._reasoning_frame]
            label_style = _PULSE_STYLES[self._pulse_frame]
            _write_inplace(
                f"\r{_ANSI_BOLD}{_ANSI_GOLD}{glyph}{_ANSI_RESET} "
                f"{label_style}Evaluating...{_ANSI_RESET}"
            )
        if motion_enabled():
            sleep(0.06)
        del piece

    def end_reasoning(self) -> None:
        if self._reasoning_started:
            if not _parallel_mode and _is_tty():
                _write_inplace(f"\r{' ' * 20}\r")
            self._reasoning_started = False

    # ── Content stream path (handles <think> and other tags inline) ───────

    def begin_content(self) -> None:
        if self._content_started:
            return
        if self._reasoning_started:
            self.end_reasoning()
        self._content_started = True
        console.print("", end="")  # ensure fresh line

    def content_chunk(self, piece: str) -> None:
        """Feed one chunk from the LLM content stream."""
        if not self._content_started:
            self.begin_content()

        for event in self._parser.feed(piece, self._parse_state):
            if event.kind == EventKind.NORMAL_TEXT:
                self._ingest(event.text)
            elif event.kind == EventKind.BLOCK_OPEN:
                # Flush normal buffer before switching to block rendering
                self._flush()
                self._block_renderer.render_block_open(event.spec)  # type: ignore[arg-type]
            elif event.kind == EventKind.BLOCK_TEXT:
                self._block_renderer.render_block_text(event.text, event.spec)  # type: ignore[arg-type]
            elif event.kind == EventKind.BLOCK_CLOSE:
                self._block_renderer.render_block_close(event.spec)  # type: ignore[arg-type]
            # SILENT events produce no output — intentionally ignored

    def end_content(self, *, render_markdown: bool = True) -> None:
        del render_markdown
        if not self._content_started:
            return
        # Gracefully close any unclosed block (stream ended before </think>)
        if self._parse_state.mode == _Mode.IN_BLOCK and self._parse_state.active_spec:
            self._block_renderer.close_gracefully(self._parse_state.active_spec)
        self._flush()
        console.out("\n", end="", highlight=False)
        self._content_started = False
        self._reset_parser()

    def force_newline(self) -> None:
        if self._reasoning_started:
            self.end_reasoning()
        if self._content_started:
            if self._parse_state.mode == _Mode.IN_BLOCK and self._parse_state.active_spec:
                self._block_renderer.close_gracefully(self._parse_state.active_spec)
            self._flush()
            console.out("\n", end="", highlight=False)
            self._content_started = False
        self._reset_parser()

    # ── Internal helpers ─────────────────────────────────────────────────

    def _ingest(self, piece: str) -> None:
        if not piece:
            return
        # Strip Llama/Meta special tokens inline so they never reach the terminal.
        if "<|" in piece:
            piece = _SPECIAL_TOKEN_RE.sub("", piece)
            if not piece:
                return
        threshold = _flush_threshold()

        # Pre-flush: if the buffer holds non-ghost content and the incoming
        # piece opens a new narration/ghost line, flush the buffer first so
        # the narration doesn't get buried in a mixed chunk and slip through.
        if self._buf_len > 0 and not self._in_ghost_continuation:
            first_part = piece.split("\n")[0] if "\n" in piece else piece
            if first_part and _apply_narration_filter(first_part) is not first_part:
                self._flush()

        if "\n" not in piece and self._buf_len + len(piece) < threshold:
            self._buf.append(piece)
            self._buf_len += len(piece)
            return
        chunks = piece.split("\n")
        for part in chunks[:-1]:
            self._buf.append(part + "\n")
            self._buf_len += len(part) + 1
            self._flush()
        tail = chunks[-1]
        if tail:
            self._buf.append(tail)
            self._buf_len += len(tail)
        if self._buf_len >= threshold:
            self._soft_flush()

    def _flush(self) -> None:
        if self._buf_len == 0:
            return
        text = "".join(self._buf)
        self._buf = []
        self._buf_len = 0
        try:
            if self._in_ghost_continuation:
                _write_inplace(f"{_ANSI_GHOST}{text}{_ANSI_RESET}")
                if "\n" in text:
                    self._in_ghost_continuation = False
            else:
                filtered = _apply_narration_filter(text)
                if filtered is not text:
                    # Ghost-texted — track continuation if line not yet complete
                    self._in_ghost_continuation = "\n" not in text
                    _write_inplace(filtered)
                else:
                    console.out(text, end="", highlight=False)
        except Exception:
            pass
        if motion_enabled() and text:
            if text.endswith((".", ":", "?", "!", "\n")):
                sleep(0.012)
            elif len(text) < 8:
                sleep(0.003)

    def _soft_flush(self) -> None:
        joined = "".join(self._buf)
        cut = joined.rfind(" ")
        if cut <= 0:
            self._flush()
            return
        chunk = joined[:cut + 1]
        try:
            if self._in_ghost_continuation:
                _write_inplace(f"{_ANSI_GHOST}{chunk}{_ANSI_RESET}")
            else:
                filtered = _apply_narration_filter(chunk)
                if filtered is not chunk:
                    self._in_ghost_continuation = True
                    _write_inplace(filtered)
                else:
                    console.out(chunk, end="", highlight=False)
        except Exception:
            pass
        rest = joined[cut + 1:]
        self._buf = [rest] if rest else []
        self._buf_len = len(rest)

    def _reset_parser(self) -> None:
        self._parse_state = TagParserState()
        self._block_renderer.reset()
        self._in_ghost_continuation = False
