/**
 * Painel Mapeamento (data-mapper) config layer — 17, UI-PAINEL-1.
 *
 * Pure, React-free helpers for the "Mapeamento da Origem" view. The data-mapper
 * converts each source's raw payload record into the Brave canonical structure.
 * This mapping is inherently LOCAL config (no backend) per product decision, so
 * everything here is in-memory: seed RAW payloads, canonical field options, and
 * the per-source default field→canonical maps, plus pure builders that the
 * component renders (rows + the live canonical preview).
 *
 * Mirrors the design contract logic (Painel-Brave.dc.html lines 545-573 +
 * 871-882): SOURCE_LABELS / RAW / CANON / DEFAULT_MAPS, buildMapRows /
 * buildPreview. No CSS, no React — colors/markup live in the component.
 */

export const SOURCE_KEYS = ["mtur", "tripadvisor", "google_places"] as const;

export type MapSourceKey = (typeof SOURCE_KEYS)[number];

export const SOURCE_LABELS: Record<MapSourceKey, string> = {
  mtur: "mTur",
  tripadvisor: "TripAdvisor",
  google_places: "Google Places",
};

/** Raw payload sample per source (design lines 552-556). */
export const RAW: Record<MapSourceKey, Record<string, string>> = {
  mtur: {
    NO_MUNICIPIO: "Fortaleza",
    SG_UF: "CE",
    NO_REGIAO_TURISTICA: "Costa do Sol",
    CO_MUNICIPIO_IBGE: "2304400",
    NU_LATITUDE: "-3.7319",
    NU_LONGITUDE: "-38.5267",
    DS_CATEGORIA: "destino",
  },
  tripadvisor: {
    name: "Praça do Ferreira",
    "addressObj.city": "Fortaleza",
    "addressObj.state": "CE",
    latitude: "-3.7276",
    longitude: "-38.5270",
    numReviews: "1240",
    rating: "4.5",
    category: "attraction",
    locationId: "g303293-d556",
  },
  google_places: {
    "displayName.text": "Pinacoteca do Estado",
    "addressComponents.city": "São Paulo",
    "addressComponents.uf": "SP",
    "location.latitude": "-23.5346",
    "location.longitude": "-46.6336",
    userRatingCount: "5312",
    rating: "4.6",
    primaryType: "museum",
    id: "ChIJ0Wc...",
  },
};

/** A canonical-field option presented in each row's <select> (design lines 545-549). */
export interface CanonOption {
  key: string;
  label: string;
}

/** Canonical field options, including the "(ignorar)" sentinel `—`. */
export const CANON: CanonOption[] = [
  { key: "name", label: "Nome" },
  { key: "municipality", label: "Município" },
  { key: "uf", label: "UF" },
  { key: "type", label: "Tipo" },
  { key: "lat", label: "Latitude" },
  { key: "lng", label: "Longitude" },
  { key: "review_count", label: "Avaliações" },
  { key: "rating", label: "Nota" },
  { key: "—", label: "(ignorar)" },
];

/** A single source-field → canonical-field mapping entry. */
export interface MapEntry {
  src: string;
  canonical: string;
}

/** Per-source default field→canonical maps (design lines 557-573). */
export const DEFAULT_MAPS: Record<MapSourceKey, MapEntry[]> = {
  mtur: [
    { src: "NO_MUNICIPIO", canonical: "name" },
    { src: "SG_UF", canonical: "uf" },
    { src: "NO_MUNICIPIO", canonical: "municipality" },
    { src: "DS_CATEGORIA", canonical: "type" },
    { src: "NU_LATITUDE", canonical: "lat" },
    { src: "NU_LONGITUDE", canonical: "lng" },
    { src: "CO_MUNICIPIO_IBGE", canonical: "—" },
    { src: "NO_REGIAO_TURISTICA", canonical: "—" },
  ],
  tripadvisor: [
    { src: "name", canonical: "name" },
    { src: "addressObj.city", canonical: "municipality" },
    { src: "addressObj.state", canonical: "uf" },
    { src: "category", canonical: "type" },
    { src: "latitude", canonical: "lat" },
    { src: "longitude", canonical: "lng" },
    { src: "numReviews", canonical: "review_count" },
    { src: "rating", canonical: "rating" },
    { src: "locationId", canonical: "—" },
  ],
  google_places: [
    { src: "displayName.text", canonical: "name" },
    { src: "addressComponents.city", canonical: "municipality" },
    { src: "addressComponents.uf", canonical: "uf" },
    { src: "primaryType", canonical: "type" },
    { src: "location.latitude", canonical: "lat" },
    { src: "location.longitude", canonical: "lng" },
    { src: "userRatingCount", canonical: "review_count" },
    { src: "rating", canonical: "rating" },
    { src: "id", canonical: "—" },
  ],
};

/** A rendered mapping row in the left "Campos da origem → canônico" card. */
export interface MapRow {
  index: number;
  src: string;
  value: string;
  canonical: string;
  dimmed: boolean;
}

/**
 * Build the left-card rows for `source`: one per mapping entry, resolving the
 * raw payload value and flagging entries routed to `—` ("(ignorar)") as dimmed.
 */
export function buildMapRows(
  maps: Record<MapSourceKey, MapEntry[]>,
  source: MapSourceKey,
): MapRow[] {
  const raw = RAW[source];
  const cur = maps[source] ?? DEFAULT_MAPS[source];
  return cur.map((m, i) => ({
    index: i,
    src: m.src,
    value: String(raw[m.src]),
    canonical: m.canonical,
    dimmed: m.canonical === "—",
  }));
}

/** A single line in the right-panel canonical preview. */
export interface PreviewRow {
  key: string;
  value: string;
}

/**
 * Build the live canonical-record preview for `source`: for each canonical
 * option (except `—`), if some mapped row targets it, emit its resolved value;
 * always append a trailing `source` row with the human source label.
 */
export function buildPreview(
  maps: Record<MapSourceKey, MapEntry[]>,
  source: MapSourceKey,
): PreviewRow[] {
  const raw = RAW[source];
  const cur = maps[source] ?? DEFAULT_MAPS[source];
  const rows: PreviewRow[] = [];
  for (const opt of CANON) {
    if (opt.key === "—") continue;
    const m = cur.find((mm) => mm.canonical === opt.key);
    if (m) rows.push({ key: opt.key, value: String(raw[m.src]) });
  }
  rows.push({ key: "source", value: SOURCE_LABELS[source] });
  return rows;
}

/** Deep clone of DEFAULT_MAPS so component state can be mutated freely. */
export function cloneDefaultMaps(): Record<MapSourceKey, MapEntry[]> {
  return {
    mtur: DEFAULT_MAPS.mtur.map((m) => ({ ...m })),
    tripadvisor: DEFAULT_MAPS.tripadvisor.map((m) => ({ ...m })),
    google_places: DEFAULT_MAPS.google_places.map((m) => ({ ...m })),
  };
}
