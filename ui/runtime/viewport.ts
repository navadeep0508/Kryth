import { TerminalBuffer } from "./terminal_buffer";

export interface ViewportState {
  scrollOffset: number;
  visibleRows: number;
  visibleCols: number;
  totalLines: number;
  atBottom: boolean;
  scrollPercent: number;
}

export class Viewport {
  buffer: TerminalBuffer;
  scrollOffset = 0;
  maxScrollbackLines: number;

  constructor(buffer: TerminalBuffer, maxScrollbackLines = 5000) {
    this.buffer = buffer;
    this.maxScrollbackLines = maxScrollbackLines;
  }

  get visibleRows(): number {
    return this.buffer.rows;
  }

  get visibleCols(): number {
    return this.buffer.cols;
  }

  get totalLines(): number {
    return this.buffer.scrollback.length + this.buffer.rows;
  }

  get atBottom(): boolean {
    return this.scrollOffset <= 0;
  }

  get scrollPercent(): number {
    const max = this.maxScroll();
    if (max <= 0) return 100;
    return Math.round(((max - this.scrollOffset) / max) * 100);
  }

  maxScroll(): number {
    return Math.max(0, this.buffer.scrollback.length);
  }

  scrollTo(offset: number): void {
    this.scrollOffset = Math.max(0, Math.min(offset, this.maxScroll()));
  }

  scrollUp(n = 1): void {
    this.scrollOffset = Math.min(
      this.scrollOffset + n,
      this.maxScroll(),
    );
  }

  scrollDown(n = 1): void {
    this.scrollOffset = Math.max(0, this.scrollOffset - n);
  }

  scrollToBottom(): void {
    this.scrollOffset = 0;
  }

  scrollToTop(): void {
    this.scrollOffset = this.maxScroll();
  }

  scrollPageUp(): void {
    this.scrollUp(this.buffer.rows);
  }

  scrollPageDown(): void {
    this.scrollDown(this.buffer.rows);
  }

  isLineVisible(lineIndex: number): boolean {
    const firstVisible = this.scrollOffset;
    const lastVisible = this.scrollOffset + this.buffer.rows - 1;
    return lineIndex >= firstVisible && lineIndex <= lastVisible;
  }

  getLineAtScreenRow(screenRow: number): number {
    return this.scrollOffset + screenRow;
  }

  getScreenRowForLine(lineIndex: number): number {
    return lineIndex - this.scrollOffset;
  }

  snapshot(): ViewportState {
    return {
      scrollOffset: this.scrollOffset,
      visibleRows: this.visibleRows,
      visibleCols: this.visibleCols,
      totalLines: this.totalLines,
      atBottom: this.atBottom,
      scrollPercent: this.scrollPercent,
    };
  }
}
