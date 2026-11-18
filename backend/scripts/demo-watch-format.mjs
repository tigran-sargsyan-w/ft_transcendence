/**
 * Presentation-only helpers for the Demo Lab terminal observer.
 * The Graph Engine's raw response stays unchanged; this module only filters
 * the human-readable view to disposable lab services.
 */
export const LAB_PREFIX = 'svc:ft-demo-lab:';

export function shortLabId(id) {
  return typeof id === 'string' && id.startsWith(LAB_PREFIX)
    ? id.slice(LAB_PREFIX.length)
    : String(id);
}

export function formatAnalysisSummary({ seedId, freshness, topologyRevision, results }) {
  const lines = [
    `ANALYSIS seed=${shortLabId(seedId)} freshness=${freshness ?? 'unknown'} revision=${topologyRevision ?? 'unknown'}`,
  ];
  const blast = results?.blastRadius;
  if (!Array.isArray(blast?.affectedNodeIds)) {
    lines.push('  BLAST unavailable');
  } else {
    const labNodes = blast.affectedNodeIds
      .filter((id) => typeof id === 'string' && id.startsWith(LAB_PREFIX))
      .map((id) => ({ id, depth: blast.depthByNodeId?.[id] }))
      .sort((a, b) => {
        const da = Number.isInteger(a.depth) ? a.depth : Infinity;
        const db = Number.isInteger(b.depth) ? b.depth : Infinity;
        return da - db || a.id.localeCompare(b.id);
      });
    const outsideCount = blast.affectedNodeIds.length - labNodes.length;
    const list = labNodes
      .map(({ id, depth }) => `${shortLabId(id)}(depth ${Number.isInteger(depth) ? depth : '?'})`)
      .join(', ');
    lines.push(`  BLAST potential impact: ${list || 'no lab nodes'}${outsideCount > 0 ? `; ${outsideCount} outside lab` : ''}`);
  }

  const critical = results?.criticalNodes?.nodes;
  if (!Array.isArray(critical)) {
    lines.push('  CRITICAL unavailable');
  } else {
    const labCritical = critical
      .filter((entry) => entry && typeof entry.nodeId === 'string' &&
        entry.nodeId.startsWith(LAB_PREFIX) && Number.isFinite(entry.score))
      .sort((a, b) => b.score - a.score || a.nodeId.localeCompare(b.nodeId));
    const list = labCritical
      .map(({ nodeId, score }) => `${shortLabId(nodeId)}=${score.toFixed(2)}`)
      .join(', ');
    lines.push(`  CRITICAL lab scores: ${list || 'none'}`);
  }
  return lines;
}
