/**
 * Identifies a Browse card by its recipe route, not its model ID.
 * Multiple recipes can intentionally point to the same underlying model.
 */
export function modelCardKey({ url }: { url: string }): string {
  return url;
}
