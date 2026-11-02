// The analyses of the Graph Engine, and the key each one has under `results`
// (docs/architecture/graph-contract.md).
export const RESULT_KEYS = {
  blast_radius: 'blastRadius',
  attack_paths: 'attackPaths',
  critical_nodes: 'criticalNodes',
} as const;

export const ANALYSES = Object.keys(RESULT_KEYS);
