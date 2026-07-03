import { Cell } from "./cell";
import { TerminalBuffer } from "./terminal_buffer";

export interface ChangeRegion {
  row: number;
  startCol: number;
  endCol: number;
}

export interface DiffResult {
  changedRows: number[];
  regions: ChangeRegion[];
  full: boolean;
}

export class DiffRenderer {
  private _prev: Cell[][] | null = null;

  diff(buffer: TerminalBuffer): DiffResult {
    const current = buffer.cells;
    const changedRows: number[] = [];
    const regions: ChangeRegion[] = [];

    if (!this._prev) {
      this._prev = current.map((row) => row.map((c) => c.clone()));
      for (let r = 0; r < current.length; r++) {
        changedRows.push(r);
        regions.push({ row: r, startCol: 0, endCol: current[r].length - 1 });
      }
      return { changedRows, regions, full: true };
    }

    const minRows = Math.min(this._prev.length, current.length);
    const maxRows = Math.max(this._prev.length, current.length);

    for (let r = 0; r < maxRows; r++) {
      if (r >= minRows) {
        changedRows.push(r);
        const cols = current[r]?.length ?? 0;
        regions.push({ row: r, startCol: 0, endCol: Math.max(0, cols - 1) });
        continue;
      }

      const prevRow = this._prev[r];
      const currRow = current[r];
      const minCols = Math.min(prevRow.length, currRow.length);
      let rowChanged = false;
      let regionStart = -1;

      for (let c = 0; c < Math.max(prevRow.length, currRow.length); c++) {
        const changed =
          c >= minCols ||
          !prevRow[c].equals(currRow[c]) ||
          currRow[c]?.dirty;

        if (changed && !rowChanged) {
          rowChanged = true;
          changedRows.push(r);
        }

        if (changed && regionStart < 0) {
          regionStart = c;
        } else if (!changed && regionStart >= 0) {
          regions.push({ row: r, startCol: regionStart, endCol: c - 1 });
          regionStart = -1;
        }
      }

      if (regionStart >= 0) {
        regions.push({
          row: r,
          startCol: regionStart,
          endCol: currRow.length - 1,
        });
      }
    }

    this._prev = current.map((row) => row.map((c) => c.clone()));

    return { changedRows, regions, full: false };
  }

  reset(): void {
    this._prev = null;
  }
}
