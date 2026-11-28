import type { MemoryDocument, MemoryImportEntry } from "@/lib/api";

export const RESIDENT_MEMORY_PATHS = [
  "SOUL.md",
  "USER.md",
  "memory/MEMORY.md",
] as const;

export const WIKI_ROOT = "memory/wiki";

export type WikiDirectoryNode = {
  kind: "directory";
  name: string;
  path: string;
  children: WikiTreeNode[];
};

export type WikiTreeNode =
  | WikiDirectoryNode
  | {
      kind: "file";
      name: string;
      path: string;
    };

type MutableDirectory = {
  kind: "directory";
  name: string;
  path: string;
  directories: Map<string, MutableDirectory>;
  files: Array<{ name: string; path: string }>;
};

const pathCollator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: "base",
});

function mutableDirectory(name: string, path: string): MutableDirectory {
  return {
    kind: "directory",
    name,
    path,
    directories: new Map(),
    files: [],
  };
}

function freezeDirectory(directory: MutableDirectory): WikiDirectoryNode {
  const directories = Array.from(directory.directories.values())
    .map(freezeDirectory)
    .sort((left, right) => pathCollator.compare(left.name, right.name));
  const files = directory.files
    .map(({ name, path }): WikiTreeNode => ({ kind: "file", name, path }))
    .sort((left, right) => {
      if (left.name === "index.md") return -1;
      if (right.name === "index.md") return 1;
      return pathCollator.compare(left.name, right.name);
    });
  return {
    kind: "directory",
    name: directory.name,
    path: directory.path,
    children: [...files, ...directories],
  };
}

export function isResidentMemoryPath(path: string): boolean {
  return RESIDENT_MEMORY_PATHS.includes(path as (typeof RESIDENT_MEMORY_PATHS)[number]);
}

export function isWikiMemoryPath(path: string): boolean {
  return path.startsWith(`${WIKI_ROOT}/`) && path.toLowerCase().endsWith(".md");
}

export function normalizeMemoryPath(value: string): string | null {
  const withoutQuery = value.replace(/\\/g, "/").split(/[?#]/, 1)[0] ?? "";
  const parts: string[] = [];
  for (const segment of withoutQuery.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      if (!parts.length) return null;
      parts.pop();
    } else {
      parts.push(segment);
    }
  }
  return parts.length ? parts.join("/") : null;
}

export function wikiAncestorPaths(path: string): string[] {
  if (!path.startsWith(`${WIKI_ROOT}/`)) return [];
  const relative = path.slice(WIKI_ROOT.length + 1);
  const parts = relative.split("/");
  parts.pop();
  const ancestors: string[] = [];
  let current = WIKI_ROOT;
  for (const part of parts) {
    current = `${current}/${part}`;
    ancestors.push(current);
  }
  return ancestors;
}

export function buildWikiTree(documents: MemoryDocument[]): WikiDirectoryNode {
  const root = mutableDirectory("Wiki", WIKI_ROOT);
  for (const document of documents) {
    if (!isWikiMemoryPath(document.path)) continue;
    const relative = document.path.slice(WIKI_ROOT.length + 1);
    const parts = relative.split("/").filter(Boolean);
    if (!parts.length) continue;
    let current = root;
    for (const part of parts.slice(0, -1)) {
      const path = `${current.path}/${part}`;
      let directory = current.directories.get(part);
      if (!directory) {
        directory = mutableDirectory(part, path);
        current.directories.set(part, directory);
      }
      current = directory;
    }
    current.files.push({
      name: parts.at(-1) ?? relative,
      path: document.path,
    });
  }
  return freezeDirectory(root);
}

export function memoryImportsByStatus(imports: MemoryImportEntry[]): {
  pending: MemoryImportEntry[];
  archived: MemoryImportEntry[];
} {
  return {
    pending: imports.filter((entry) => entry.status !== "archived"),
    archived: imports.filter((entry) => entry.status === "archived"),
  };
}

export function formatMemoryBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${new Intl.NumberFormat(undefined, {
    maximumFractionDigits: unit === 0 ? 0 : value < 10 ? 2 : 1,
  }).format(value)} ${units[unit]}`;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

export async function fileContentBase64(file: File): Promise<string> {
  return bytesToBase64(new Uint8Array(await file.arrayBuffer()));
}
