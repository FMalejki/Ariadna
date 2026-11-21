/** Outbound links shown in the graph/evidence panels. Links are navigation aids only; they prove nothing. */

/** Returns the URL only when it is a plain http(s) URL (blocks javascript:, data:, etc.), else null. */
export function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export interface ExplorerLink {
  label: string;
  url: string;
}

// Public explorers whose address/tx page URLs were checked to answer over HTTPS.
// Solana Explorer answered 429 (rate limit) to the check, so that template is not verified end-to-end.
const EVM_BLOCKSCOUT: Record<string, { label: string; base: string }> = {
  ethereum: { label: "Ethereum (Blockscout)", base: "https://eth.blockscout.com" },
  base: { label: "Base (Blockscout)", base: "https://base.blockscout.com" },
  arbitrum: { label: "Arbitrum (Blockscout)", base: "https://arbitrum.blockscout.com" },
  polygon: { label: "Polygon (Blockscout)", base: "https://polygon.blockscout.com" },
};

function evmLinks(kind: "address" | "tx", value: string, network: string): ExplorerLink[] {
  // "evm" means the chain is not determined: offer each known chain rather than guessing one.
  const keys = network === "evm" ? Object.keys(EVM_BLOCKSCOUT) : EVM_BLOCKSCOUT[network] ? [network] : [];
  return keys.map((k) => ({ label: EVM_BLOCKSCOUT[k].label, url: `${EVM_BLOCKSCOUT[k].base}/${kind}/${value}` }));
}

/** Explorer links for a wallet address or transaction hash. Unknown networks yield no links (never a guess). */
export function explorerLinks(network: string | null | undefined, kind: "address" | "tx", value: string | null | undefined): ExplorerLink[] {
  if (!network || !value) return [];
  if (network === "bitcoin") return [{ label: "Bitcoin (Blockstream)", url: `https://blockstream.info/${kind}/${value}` }];
  if (network === "solana") return [{ label: "Solana Explorer", url: `https://explorer.solana.com/${kind}/${value}` }];
  return evmLinks(kind, value, network);
}
