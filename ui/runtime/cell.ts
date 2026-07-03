export interface CellStyle {
  fg: number;
  bg: number;
  bold: boolean;
  dim: boolean;
  italic: boolean;
  underline: boolean;
  strikethrough: boolean;
  inverse: boolean;
}

export const EMPTY_STYLE: CellStyle = {
  fg: 7,
  bg: 0,
  bold: false,
  dim: false,
  italic: false,
  underline: false,
  strikethrough: false,
  inverse: false,
};

export class Cell {
  char: string;
  style: CellStyle;
  width: number;
  dirty: boolean;

  constructor(char = " ", style: Partial<CellStyle> = {}, width = 1) {
    this.char = char;
    this.style = { ...EMPTY_STYLE, ...style };
    this.width = width;
    this.dirty = true;
  }

  clone(): Cell {
    const c = new Cell(this.char, { ...this.style }, this.width);
    c.dirty = this.dirty;
    return c;
  }

  equals(other: Cell): boolean {
    return (
      this.char === other.char &&
      this.style.fg === other.style.fg &&
      this.style.bg === other.style.bg &&
      this.style.bold === other.style.bold &&
      this.style.dim === other.style.dim &&
      this.style.italic === other.style.italic &&
      this.style.underline === other.style.underline &&
      this.style.strikethrough === other.style.strikethrough &&
      this.style.inverse === other.style.inverse
    );
  }

  clear(): void {
    this.char = " ";
    this.style = { ...EMPTY_STYLE };
    this.dirty = true;
  }

  static space(): Cell {
    return new Cell(" ");
  }
}
