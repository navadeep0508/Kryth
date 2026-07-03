import { Cell, EMPTY_STYLE } from "./cell";
import { Cursor } from "./cursor";
import { Scrollback } from "./scrollback";

export interface BufferDimensions {
  rows: number;
  cols: number;
}

export interface BufferSnapshot {
  rows: number;
  cols: number;
  cells: string[][];
  cursor: { row: number; col: number };
  scrollOffset: number;
}

export type InsertMode = "insert" | "replace";

export class TerminalBuffer {
  rows: number;
  cols: number;
  cells: Cell[][];
  cursor: Cursor;
  scrollback: Scrollback;
  mode: InsertMode;
  protected _scrollOffset = 0;

  constructor(rows = 24, cols = 80, scrollbackSize = 10000) {
    this.rows = rows;
    this.cols = cols;
    this.cells = this._allocGrid(rows, cols);
    this.cursor = new Cursor();
    this.scrollback = new Scrollback(scrollbackSize);
    this.mode = "replace";
  }

  protected _allocGrid(rows: number, cols: number): Cell[][] {
    const grid: Cell[][] = [];
    for (let r = 0; r < rows; r++) {
      const row: Cell[] = [];
      for (let c = 0; c < cols; c++) {
        row.push(new Cell(" "));
      }
      grid.push(row);
    }
    return grid;
  }

  resize(rows: number, cols: number): void {
    if (rows === this.rows && cols === this.cols) return;

    const newCells = this._allocGrid(rows, cols);
    const copyRows = Math.min(rows, this.rows);
    const copyCols = Math.min(cols, this.cols);

    for (let r = 0; r < copyRows; r++) {
      for (let c = 0; c < copyCols; c++) {
        if (this.cells[r]?.[c]) {
          newCells[r][c] = this.cells[r][c].clone();
        }
      }
    }

    const oldRows = this.rows;
    this.rows = rows;
    this.cols = cols;
    this.cells = newCells;

    if (this.cursor.col >= cols) this.cursor.col = cols - 1;
    if (this.cursor.row >= rows) this.cursor.row = rows - 1;

    if (rows < oldRows) {
      this._scrollOffset = Math.max(0, this._scrollOffset - (oldRows - rows));
    }
  }

  write(text: string, style = EMPTY_STYLE): void {
    for (const ch of text) {
      if (ch === "\n") {
        this._newline();
        continue;
      }
      if (this.mode === "replace") {
        this._putChar(ch, style);
      } else {
        this._insertChar(ch, style);
      }
      this.cursor.moveRight(1, this.cols);
    }
  }

  writeln(text: string, style = EMPTY_STYLE): void {
    this.write(text, style);
    this._newline();
  }

  protected _putChar(ch: string, style: typeof EMPTY_STYLE): void {
    if (this.cursor.row >= this.rows) return;
    const cell = this.cells[this.cursor.row][this.cursor.col];
    cell.char = ch;
    cell.style = { ...style };
    cell.dirty = true;
  }

  protected _insertChar(ch: string, style: typeof EMPTY_STYLE): void {
    if (this.cursor.row >= this.rows) return;
    const row = this.cells[this.cursor.row];
    for (let c = this.cols - 1; c > this.cursor.col; c--) {
      row[c].char = row[c - 1].char;
      row[c].style = { ...row[c - 1].style };
    }
    row[this.cursor.col].char = ch;
    row[this.cursor.col].style = { ...style };
  }

  protected _newline(): void {
    this.cursor.carriageReturn();
    if (this.cursor.row >= this.rows - 1) {
      this._scrollUp();
    } else {
      this.cursor.lineFeed(this.rows);
    }
  }

  protected _scrollUp(): void {
    const scrolledRow = this.cells[0]
      .map((c) => c.char)
      .join("")
      .trimEnd();
    if (scrolledRow) {
      this.scrollback.push(scrolledRow);
    }
    for (let r = 0; r < this.rows - 1; r++) {
      for (let c = 0; c < this.cols; c++) {
        this.cells[r][c] = this.cells[r + 1][c].clone();
      }
    }
    for (let c = 0; c < this.cols; c++) {
      this.cells[this.rows - 1][c].clear();
    }
  }

  scrollUp(n = 1): void {
    for (let i = 0; i < n; i++) this._scrollUp();
  }

  scrollDown(n = 1): void {
    for (let i = 0; i < n; i++) {
      for (let r = this.rows - 1; r > 0; r--) {
        for (let c = 0; c < this.cols; c++) {
          this.cells[r][c] = this.cells[r - 1][c].clone();
        }
      }
      for (let c = 0; c < this.cols; c++) {
        this.cells[0][c].clear();
      }
    }
  }

  clear(): void {
    for (let r = 0; r < this.rows; r++) {
      for (let c = 0; c < this.cols; c++) {
        this.cells[r][c].clear();
      }
    }
    this.cursor.home();
  }

  clearRow(row: number): void {
    if (row < 0 || row >= this.rows) return;
    for (let c = 0; c < this.cols; c++) {
      this.cells[row][c].clear();
    }
  }

  clearToEnd(row: number, col: number): void {
    if (row < 0 || row >= this.rows) return;
    for (let c = col; c < this.cols; c++) {
      this.cells[row][c].clear();
    }
    for (let r = row + 1; r < this.rows; r++) {
      this.clearRow(r);
    }
  }

  insertLine(row: number): void {
    if (row < 0 || row >= this.rows) return;
    this.scrollback.push(this.getRowText(this.rows - 1));
    for (let r = this.rows - 1; r > row; r--) {
      for (let c = 0; c < this.cols; c++) {
        this.cells[r][c] = this.cells[r - 1][c].clone();
      }
    }
    this.clearRow(row);
  }

  deleteLine(row: number): void {
    if (row < 0 || row >= this.rows) return;
    for (let r = row; r < this.rows - 1; r++) {
      for (let c = 0; c < this.cols; c++) {
        this.cells[r][c] = this.cells[r + 1][c].clone();
      }
    }
    this.clearRow(this.rows - 1);
  }

  getRowText(row: number): string {
    if (row < 0 || row >= this.rows) return "";
    return this.cells[row]
      .map((c) => c.char)
      .join("")
      .replace(/\s+$/, "");
  }

  getVisibleText(): string {
    const lines: string[] = [];
    for (let r = 0; r < this.rows; r++) {
      lines.push(this.getRowText(r));
    }
    return lines.join("\n");
  }

  markAllDirty(): void {
    for (let r = 0; r < this.rows; r++) {
      for (let c = 0; c < this.cols; c++) {
        this.cells[r][c].dirty = true;
      }
    }
  }

  clearDirty(): void {
    for (let r = 0; r < this.rows; r++) {
      for (let c = 0; c < this.cols; c++) {
        this.cells[r][c].dirty = false;
      }
    }
  }

  snapshot(): BufferSnapshot {
    return {
      rows: this.rows,
      cols: this.cols,
      cells: this.cells.map((row) => row.map((c) => c.char)),
      cursor: { row: this.cursor.row, col: this.cursor.col },
      scrollOffset: this._scrollOffset,
    };
  }
}
