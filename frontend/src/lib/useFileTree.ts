import type { FileTreeNode } from './types';

/**
 * Strip infrastructure prefixes from a path to get a clean relative path
 * within the session workspace.
 */
export function stripSessionPrefix(path: string, rootPath: string): string {
  let rel = path.replace(/^\/+/, '');
  const rootNorm = rootPath.replace(/^\/+/, '').replace(/\/$/, '');
  if (rel.startsWith(rootNorm + '/')) {
    rel = rel.slice(rootNorm.length + 1);
  }
  while (/^sessions\/[^/]+\//.test(rel)) {
    rel = rel.replace(/^sessions\/[^/]+\//, '');
  }
  while (/^[0-9a-f]{8}-[0-9a-f]{4}-[^/]*\//.test(rel)) {
    rel = rel.replace(/^[0-9a-f]{8}-[0-9a-f]{4}-[^/]*\//, '');
  }
  return rel;
}

/**
 * Resolve a raw path to its position within the flat session workspace.
 * Sessions no longer have pre-defined stage subfolders — whatever the agent
 * wrote is what the tree shows.
 */
export function ensureStagePath(rawPath: string, _stage: string, rootPath: string): string {
  return stripSessionPrefix(rawPath, rootPath);
}

export function buildTreeFromFlatList(
  files: { path: string; type: string; _stage?: string }[],
  rootPath: string,
): FileTreeNode {
  const root: FileTreeNode = { name: 'workspace', path: rootPath, type: 'directory', children: [] };

  for (const file of files) {
    const rel = ensureStagePath(file.path, file._stage || '', rootPath);
    if (!rel) continue;

    const segments = rel.split('/');
    let current = root;

    for (let i = 0; i < segments.length; i++) {
      const segment = segments[i];
      const isLast = i === segments.length - 1;

      if (isLast && file.type === 'file') {
        if (!current.children!.find((c) => c.name === segment && c.type === 'file')) {
          current.children!.push({ name: segment, path: file.path, type: 'file' });
        }
      } else {
        let child = current.children!.find((c) => c.name === segment && c.type === 'directory');
        if (!child) {
          child = {
            name: segment,
            path: segments.slice(0, i + 1).join('/'),
            type: 'directory',
            children: [],
          };
          current.children!.push(child);
        }
        current = child;
      }
    }
  }

  sortTree(root);
  return unwrapTree(root);
}

export function insertNodeIntoTree(
  tree: FileTreeNode,
  node: FileTreeNode,
  rootPath: string,
  stage: string = '',
): FileTreeNode {
  const cloned = JSON.parse(JSON.stringify(tree)) as FileTreeNode;
  const rel = ensureStagePath(node.path, stage, rootPath);
  if (!rel) return cloned;

  const segments = rel.split('/');
  let current = cloned;

  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i];
    const isLast = i === segments.length - 1;

    if (!current.children) current.children = [];

    if (isLast) {
      if (!current.children.find((c) => c.name === segment && c.type === node.type)) {
        current.children.push({ name: segment, path: node.path, type: node.type });
      }
    } else {
      let child = current.children.find((c) => c.name === segment && c.type === 'directory');
      if (!child) {
        child = {
          name: segment,
          path: segments.slice(0, i + 1).join('/'),
          type: 'directory',
          children: [],
        };
        current.children.push(child);
      }
      current = child;
    }
  }

  sortTree(cloned);
  return cloned;
}

export function sortTree(node: FileTreeNode) {
  if (!node.children) return;
  for (const child of node.children) sortTree(child);
  node.children.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

export function unwrapTree(tree: FileTreeNode): FileTreeNode {
  const isInfraName = (name: string) =>
    name === 'sessions' || /^[0-9a-f]{8}-[0-9a-f]{4}-/.test(name);

  while (
    tree.children &&
    tree.children.length === 1 &&
    tree.children[0].type === 'directory' &&
    isInfraName(tree.children[0].name)
  ) {
    const only = tree.children[0];
    tree = { ...tree, children: only.children || [] };
  }
  return tree;
}

export function countFiles(node: FileTreeNode): number {
  if (node.type === 'file') return 1;
  return (node.children || []).reduce((sum, c) => sum + countFiles(c), 0);
}

export function fileBreadcrumb(filePath: string): string[] {
  return stripSessionPrefix(filePath, '').split('/');
}
