/**
 * Compute a new page's vault-relative path: kind-folder plus kebab-slug of
 * title. Ported from enchiridion-go/internal/place.
 *
 * *Which* kind a page belongs to is judgment (wiki-conventions' placement
 * algorithm) and stays with the ingesting agent. Turning a chosen kind +
 * title into `wiki/<kind-folder>/<slug>.md` is mechanics, and lives here so
 * filenames are consistent regardless of who — or which model — is ingesting.
 * Kind *values* stay singular (`concept`); kind *folders* pluralize
 * (`concepts/`), except `synthesis` — see [KindFolders] (ADR-0008).
 */

/**
 * Maps a kind value to its `wiki/` folder name (ADR-0008: folders pluralize,
 * values stay singular — `synthesis` has no distinct plural, so it's
 * unchanged). The single source of truth for the mapping; no other package
 * may hardcode a kind-folder string.
 */
export const KindFolders: Record<string, string> = {
  source: "sources",
  synthesis: "synthesis",
  entity: "entities",
  concept: "concepts",
};

/**
 * Maps a folder name to its kind value, for readers going the other direction
 * (pagerecord deriving a page's kind from its path).
 */
export const FolderKinds: Record<string, string> = Object.fromEntries(
  Object.entries(KindFolders).map(([kind, folder]) => [folder, kind]),
);

/** The fixed kind-value set (wiki-conventions, "Vault structure"), in the
 * canonical order the CLI presents them. */
export const Kinds: string[] = ["concept", "entity", "source", "synthesis"];

/** Caps generated kebab-slug filenames — readability, plus headroom under
 * the Windows 255-char path limit (#70). */
export const MaxSlugLength = 64;

/** The shortest prefix worth keeping when truncating at a hyphen boundary;
 * below it, a hard cut reads better. */
const minWordCut = 8;

const APOSTROPHE_RE = /['’]/g;
const NON_ALNUM_RE = /[^a-z0-9]+/g;

/**
 * Truncates slug to maxLength at the last hyphen boundary, when that leaves
 * at least minWordCut chars. Otherwise a hard cut.
 */
function truncateSlug(slug: string, maxLength: number): string {
  if (slug.length <= maxLength) return slug;
  const cut = slug.slice(0, maxLength).lastIndexOf("-");
  if (cut >= minWordCut) {
    return slug.slice(0, cut).replace(/-+$/, "");
  }
  return slug.slice(0, maxLength).replace(/-+$/, "");
}

/**
 * Returns title as a lowercase kebab-slug. Apostrophes are dropped rather
 * than hyphenated ("What's" -> "whats", not "what-s"); every other run of
 * non-alphanumerics collapses to one hyphen; ends are stripped. maxLength,
 * when positive, truncates via truncateSlug.
 */
export function slugify(title: string, maxLength: number): string {
  let slug = title.toLowerCase().replace(APOSTROPHE_RE, "");
  slug = slug.replace(NON_ALNUM_RE, "-");
  slug = slug.replace(/^-+/, "").replace(/-+$/, "");
  if (maxLength > 0) {
    slug = truncateSlug(slug, maxLength);
  }
  return slug;
}

/**
 * Derives a kind value from a folder name using the ADR-0008 rule: strip a
 * trailing `s` if present (`decisions` → `decision`); otherwise return the
 * folder name verbatim (`people` → `people`).
 *
 * Intended for custom kind-folders not already in [FolderKinds] — canonical
 * folders should be looked up there directly.
 */
export function folderToKind(folder: string): string {
  return folder.replace(/s$/, "");
}

/**
 * Returns the vault-relative path for a new page of kind titled title.
 *
 * Canonical kinds are resolved from [KindFolders]. Custom (discovered) kinds
 * are resolved from extraKindFolders (a {kind: folder} map). Throws an error
 * when kind is unknown in both.
 */
export function path(
  kind: string,
  title: string,
  extraKindFolders?: Record<string, string>,
): string {
  const folder = KindFolders[kind] ?? extraKindFolders?.[kind];
  if (folder === undefined) {
    throw new Error(
      `unknown kind "${kind}"; must be one of ${Kinds.join(", ")}`,
    );
  }
  return `wiki/${folder}/${slugify(title, MaxSlugLength)}.md`;
}
