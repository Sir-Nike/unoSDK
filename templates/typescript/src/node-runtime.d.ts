// Minimal Node.js runtime declarations for the unoSDK TypeScript starter.
//
// The starter deliberately ships with no npm dependencies (Node strips the
// types at load time), so `tsc --noEmit` would otherwise fail because it
// cannot find the Node globals and built-in modules. These are exactly the
// APIs the starter uses; if it starts using more, extend this file.

declare module "node:readline" {
  export interface Interface {
    on(event: "line", listener: (line: string) => void): this;
    close(): void;
  }
  export function createInterface(options: {
    input: unknown;
    crlfDelay?: number;
  }): Interface;
}

declare module "node:fs" {
  export function realpathSync(path: string): string;
}

declare module "node:url" {
  export function fileURLToPath(url: string): string;
}

declare const process: {
  argv: string[];
  stdin: unknown;
  stdout: { write(data: string, callback?: () => void): void };
  stderr: { write(data: string): void };
  exit(code?: number): never;
};

declare class Buffer {
  static byteLength(input: string, encoding?: string): number;
}

declare function setTimeout(handler: () => void, timeout?: number): {
  unref(): void;
};

interface ImportMeta {
  readonly url: string;
}
