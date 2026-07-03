export type CursorStyle = "block" | "underline" | "bar";

export interface CursorState {
  row: number;
  col: number;
  visible: boolean;
  style: CursorStyle;
  blink: boolean;
  shape: string;
}

export class Cursor {
  row: number;
  col: number;
  visible: boolean;
  style: CursorStyle;
  blink: boolean;

  constructor() {
    this.row = 0;
    this.col = 0;
    this.visible = true;
    this.style = "block";
    this.blink = true;
  }

  moveTo(row: number, col: number): void {
    this.row = Math.max(0, row);
    this.col = Math.max(0, col);
  }

  moveUp(n = 1): void {
    this.row = Math.max(0, this.row - n);
  }

  moveDown(n = 1, maxRows = 24): void {
    this.row = Math.min(maxRows - 1, this.row + n);
  }

  moveLeft(n = 1): void {
    this.col = Math.max(0, this.col - n);
  }

  moveRight(n = 1, maxCols = 80): void {
    this.col = Math.min(maxCols - 1, this.col + n);
  }

  home(): void {
    this.col = 0;
  }

  carriageReturn(): void {
    this.col = 0;
  }

  lineFeed(maxRows = 24): void {
    if (this.row >= maxRows - 1) return;
    this.row += 1;
  }

  snapshot(): CursorState {
    return {
      row: this.row,
      col: this.col,
      visible: this.visible,
      style: this.style,
      blink: this.blink,
      shape: `${this.style}${this.blink ? " blink" : ""}`,
    };
  }

  clone(): Cursor {
    const c = new Cursor();
    c.row = this.row;
    c.col = this.col;
    c.visible = this.visible;
    c.style = this.style;
    c.blink = this.blink;
    return c;
  }
}
