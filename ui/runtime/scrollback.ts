export interface ScrollbackEntry {
  text: string;
  ts: number;
  tags: Set<string>;
}

export class Scrollback {
  readonly maxSize: number;
  private _lines: ScrollbackEntry[] = [];

  constructor(maxSize = 10000) {
    this.maxSize = maxSize;
  }

  get length(): number {
    return this._lines.length;
  }

  get lines(): readonly ScrollbackEntry[] {
    return this._lines;
  }

  get empty(): boolean {
    return this._lines.length === 0;
  }

  push(text: string, tags?: string[]): void {
    this._lines.push({
      text,
      ts: performance.now(),
      tags: new Set(tags ?? []),
    });
    if (this._lines.length > this.maxSize) {
      this._lines.splice(0, this._lines.length - this.maxSize);
    }
  }

  at(index: number): ScrollbackEntry | undefined {
    if (index < 0) index = this._lines.length + index;
    return this._lines[index];
  }

  last(n = 1): ScrollbackEntry[] {
    return this._lines.slice(-n);
  }

  slice(start: number, end?: number): ScrollbackEntry[] {
    return this._lines.slice(start, end);
  }

  search(
    query: string,
    options: { caseSensitive?: boolean; regex?: boolean } = {},
  ): number[] {
    const { caseSensitive = false, regex = false } = options;
    const results: number[] = [];
    for (let i = 0; i < this._lines.length; i++) {
      let text = this._lines[i].text;
      let searchQuery = query;
      if (!caseSensitive) {
        text = text.toLowerCase();
        searchQuery = searchQuery.toLowerCase();
      }
      try {
        if (regex) {
          const re = new RegExp(searchQuery);
          if (re.test(text)) results.push(i);
        } else {
          if (text.includes(searchQuery)) results.push(i);
        }
      } catch {
        if (text.includes(searchQuery)) results.push(i);
      }
    }
    return results;
  }

  filter(tag: string): ScrollbackEntry[] {
    return this._lines.filter((e) => e.tags.has(tag));
  }

  clear(): void {
    this._lines = [];
  }

  truncate(maxLines: number): void {
    if (this._lines.length > maxLines) {
      this._lines.splice(0, this._lines.length - maxLines);
    }
  }
}
